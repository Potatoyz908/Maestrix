import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// Floor 配置（git worktree 隔离环境）
struct Floor: Codable, Identifiable {
    var id: UUID
    var name: String
    var branchName: String
    var worktreePath: String    // .open-maestri/floors/{name}
    var hooks: FloorHooks
    var createdAt: Date

    init(id: UUID = UUID(), name: String, branchName: String, workspaceDir: String) {
        self.id = id
        self.name = name
        self.branchName = branchName
        let slug = FloorManager.slugify(name)
        self.worktreePath = "\(workspaceDir)/.open-maestri/floors/\(slug)"
        self.hooks = FloorHooks()
        self.createdAt = Date()
    }
}

struct FloorHooks: Codable {
    var setup: [String] = []
    var run: [String] = []
    var teardown: [String] = []
    var autoRunSetup: Bool = false
}

/// Floor 生命周期管理器（git worktree 操作，FR53-55）
final class FloorManager {
    static let shared = FloorManager()
    private let logger = Logger.make(category: "FloorManager")
    private init() {}

    /// 将 Floor 名称转换为安全的路径/分支 slug（仅含 [a-z0-9-]，无 .. 或 /）。
    static func slugify(_ name: String) -> String {
        var result = ""
        var lastWasDash = false
        for scalar in name.lowercased().unicodeScalars {
            let isAlphaNum = (scalar.value >= 0x61 && scalar.value <= 0x7A) || (scalar.value >= 0x30 && scalar.value <= 0x39)
            if isAlphaNum {
                result.unicodeScalars.append(scalar)
                lastWasDash = false
            } else if !lastWasDash && !result.isEmpty {
                result.append("-")
                lastWasDash = true
            }
        }
        while result.hasSuffix("-") { result.removeLast() }
        return result.isEmpty ? "floor" : result
    }

    /// 构造 Floor Hook 环境变量（同时导出 MAESTRI_* 与 OMAESTRI_* 兼容别名）。
    static func hookEnvironment(for floor: Floor, workingDirectory: String) -> [String: String] {
        let projectName = URL(fileURLWithPath: workingDirectory).lastPathComponent
        return [
            "MAESTRI_FLOOR_NAME":    floor.name,
            "MAESTRI_BRANCH_NAME":   floor.branchName,
            "MAESTRI_FLOOR_PATH":    floor.worktreePath,
            "MAESTRI_ROOT_PATH":     workingDirectory,
            "MAESTRI_PROJECT_NAME":  projectName,
            "OMAESTRI_FLOOR_NAME":   floor.name,
            "OMAESTRI_BRANCH_NAME":  floor.branchName,
            "OMAESTRI_FLOOR_PATH":   floor.worktreePath,
            "OMAESTRI_ROOT_PATH":    workingDirectory,
            "OMAESTRI_PROJECT_NAME": projectName,
        ]
    }

    /// 确保 `<workingDirectory>/.git/info/exclude` 包含 `/.open-maestri/`（幂等）。
    static func ensureGitExclude(in workingDirectory: String) {
        let infoDir = workingDirectory + "/.git/info"
        let excludePath = infoDir + "/exclude"
        let entry = "/.open-maestri/"
        try? FileManager.default.createDirectory(atPath: infoDir, withIntermediateDirectories: true)
        let existing = (try? String(contentsOfFile: excludePath, encoding: .utf8)) ?? ""
        let lines = existing.components(separatedBy: .newlines)
        guard !lines.contains(entry) else { return }
        let prefix = existing.isEmpty || existing.hasSuffix("\n") ? existing : existing + "\n"
        try? (prefix + entry + "\n").write(toFile: excludePath, atomically: true, encoding: .utf8)
    }

    // MARK: - Floor 创建

    /// `existingBranch`: check out a branch that already exists (no `-b`). `baseBranch`: start point of a new branch
    /// (default: Ground's HEAD).
    func createFloor(name: String, branchName: String, workingDirectory: String,
                     existingBranch: Bool = false, baseBranch: String? = nil) throws -> Floor {
        // 验证工作目录是 git repo
        let gitDir = workingDirectory + "/.git"
        guard FileManager.default.fileExists(atPath: gitDir) else {
            let fmt = Bundle.main.localizedString(forKey: "floor.error.not_git_repo", value: nil, table: nil)
            throw MaestriError.floorOperationFailed(String(format: fmt, workingDirectory))
        }

        Self.ensureGitExclude(in: workingDirectory)
        let floor = Floor(name: name, branchName: branchName, workspaceDir: workingDirectory)
        try runGit(Self.worktreeAddArgs(branch: branchName, path: floor.worktreePath,
                                        existingBranch: existingBranch, baseBranch: baseBranch), in: workingDirectory)
        logger.info("Floor '\(name)' created at \(floor.worktreePath)")
        return floor
    }

    // MARK: - Floor 删除

    /// `git worktree add` argv (pure, unit-tested). `--` keeps a hostile path from being read as an option.
    static func worktreeAddArgs(branch: String, path: String, existingBranch: Bool, baseBranch: String?) -> [String] {
        if existingBranch {
            return ["worktree", "add", "--", path, branch]
        }
        var args = ["worktree", "add", "-b", branch, "--", path]
        if let base = baseBranch, !base.isEmpty { args.append(base) }
        return args
    }

    /// Removes the worktree (always) and, when `deleteBranch`, the floor's branch. The `floors/` directory goes away with
    /// the last floor.
    func removeFloor(_ floor: Floor, workingDirectory: String, deleteBranch: Bool = false) throws {
        if FileManager.default.fileExists(atPath: floor.worktreePath) {
            try runGit(["worktree", "remove", "--force", "--", floor.worktreePath], in: workingDirectory)
        } else {
            _ = try? runGit(["worktree", "prune"], in: workingDirectory)
        }
        if deleteBranch {
            try runGit(["branch", "-D", "--", floor.branchName], in: workingDirectory)
        }
        let floorsDir = URL(fileURLWithPath: floor.worktreePath).deletingLastPathComponent().path
        if let rest = try? FileManager.default.contentsOfDirectory(atPath: floorsDir), rest.isEmpty {
            try? FileManager.default.removeItem(atPath: floorsDir)
        }
        logger.info("Floor '\(floor.name)' removed (deleteBranch=\(deleteBranch))")
    }

    // MARK: - Landing preview / landing (git plumbing; Ground's working tree is only touched by a --ff-only update)

    struct LandPreview {
        var floorBranch: String
        var target: String
        var branches: [String]
        var dirty: Bool
        var dirtyFiles: [String]
        var sameBranch: Bool
        var ahead: Int
        var fastForward: Bool
        var stat: String
        var diff: String = ""
        var treeOid: String = ""
        var conflicts: [String]

        var canLand: Bool { !dirty && conflicts.isEmpty && !sameBranch && ahead > 0 }

        var json: [String: Any] {
            ["floorBranch": floorBranch, "target": target, "branches": branches, "dirty": dirty, "dirtyFiles": dirtyFiles,
             "sameBranch": sameBranch, "ahead": ahead, "fastForward": fastForward, "stat": stat, "diff": diff,
             "treeOid": treeOid, "conflicts": conflicts, "canLand": canLand]
        }
    }

    /// Ground's checked-out branch (nil when detached).
    func groundBranch(workingDirectory: String) -> String? {
        guard let out = try? runGit(["symbolic-ref", "--quiet", "--short", "HEAD"], in: workingDirectory) else { return nil }
        let b = out.trimmingCharacters(in: .whitespacesAndNewlines)
        return b.isEmpty ? nil : b
    }

    func landPreview(floor: Floor, target requested: String?, workingDirectory: String) throws -> LandPreview {
        let branches = (try runGit(["for-each-ref", "--format=%(refname:short)", "refs/heads/"], in: workingDirectory))
            .split(separator: "\n").map(String.init).filter { !$0.isEmpty }
        let target = (requested?.isEmpty == false ? requested : nil)
            ?? groundBranch(workingDirectory: workingDirectory) ?? branches.first ?? "main"
        let porcelain = FileManager.default.fileExists(atPath: floor.worktreePath)
            ? try runGit(["status", "--porcelain"], in: floor.worktreePath) : ""
        let dirtyFiles = porcelain.split(separator: "\n").map { String($0.dropFirst(3)) }.filter { !$0.isEmpty }
        var p = LandPreview(floorBranch: floor.branchName, target: target, branches: branches, dirty: !dirtyFiles.isEmpty,
                            dirtyFiles: dirtyFiles, sameBranch: target == floor.branchName, ahead: 0, fastForward: false,
                            stat: "", conflicts: [])
        // A worktree shares refs with Ground: landing into the floor's own branch has nothing to transfer.
        guard !p.sameBranch else { return p }
        try runGit(["rev-parse", "--verify", "--quiet", "refs/heads/\(target)"], in: workingDirectory)
        p.ahead = Int((try runGit(["rev-list", "--count", "\(target)..\(floor.branchName)"], in: workingDirectory))
            .trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
        p.fastForward = (try? runGit(["merge-base", "--is-ancestor", target, floor.branchName], in: workingDirectory)) != nil
        p.stat = (try runGit(["diff", "--shortstat", "\(target)...\(floor.branchName)"], in: workingDirectory))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        p.diff = (try? runGit(["diff", "\(target)...\(floor.branchName)"], in: workingDirectory)) ?? ""
        let r = runGitStatus(["merge-tree", "--write-tree", "--name-only", "--no-messages", target, floor.branchName],
                             in: workingDirectory)
        let lines = r.output.split(separator: "\n").map(String.init)
        p.treeOid = lines.first ?? ""
        if !p.fastForward {
            if r.status == 1 {   // conflicts: line 1 = tree oid, then the conflicted paths
                var seen = Set<String>()
                p.conflicts = lines.dropFirst().filter { !$0.isEmpty && seen.insert($0).inserted }
            } else if r.status != 0 {
                throw MaestriError.floorOperationFailed("git merge-tree failed: \(r.output)")
            }
        }
        return p
    }

    /// Lands `floor` into `target`. Refuses a dirty floor and conflicts. Fast-forward when possible, otherwise a merge
    /// commit built with merge-tree + commit-tree. The target ref moves with update-ref (compare-and-swap); when the target
    /// is the branch checked out in Ground, Ground is advanced with `merge --ff-only` instead, which refuses to clobber local
    /// changes. Returns the new target commit.
    @discardableResult
    func land(floor: Floor, targetBranch: String, workingDirectory: String) throws -> String {
        let p = try landPreview(floor: floor, target: targetBranch, workingDirectory: workingDirectory)
        if p.dirty { throw MaestriError.floorOperationFailed("floor has uncommitted changes") }
        if p.sameBranch { throw MaestriError.floorOperationFailed("target is the floor's own branch: nothing to land") }
        if !p.conflicts.isEmpty { throw MaestriError.floorOperationFailed("merge conflicts: \(p.conflicts.joined(separator: ", "))") }
        if p.ahead == 0 { throw MaestriError.floorOperationFailed("nothing to land: \(floor.branchName) has no new commits") }

        let oldTarget = try runGit(["rev-parse", "refs/heads/\(targetBranch)"], in: workingDirectory)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let floorTip = try runGit(["rev-parse", "refs/heads/\(floor.branchName)"], in: workingDirectory)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        var newCommit = floorTip
        if !p.fastForward {
            let tree = try runGit(["merge-tree", "--write-tree", targetBranch, floor.branchName], in: workingDirectory)
                .split(separator: "\n").first.map(String.init) ?? ""
            newCommit = try runGit(["commit-tree", tree, "-p", oldTarget, "-p", floorTip,
                                    "-m", "Land floor '\(floor.name)' (\(floor.branchName)) into \(targetBranch)"],
                                   in: workingDirectory).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if groundBranch(workingDirectory: workingDirectory) == targetBranch {
            try runGit(["merge", "--ff-only", "--quiet", newCommit], in: workingDirectory)
        } else {
            try runGit(["update-ref", "-m", "land floor \(floor.name)", "refs/heads/\(targetBranch)", newCommit, oldTarget],
                       in: workingDirectory)
        }
        logger.info("Floor '\(floor.name)' landed onto '\(targetBranch)' at \(newCommit.prefix(8))")
        return newCommit
    }

    // MARK: - Hooks 执行

    func runHooks(_ hooks: [String], floor: Floor, workingDirectory: String) async throws {
        for hook in hooks {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: Constants.defaultShellPath)
            process.arguments = ["-c", hook]
            process.currentDirectoryURL = URL(fileURLWithPath: floor.worktreePath)
            process.environment = ProcessInfo.processInfo.environment.merging(
                Self.hookEnvironment(for: floor, workingDirectory: workingDirectory)
            ) { _, new in new }
            try process.run()
            process.waitUntilExit()
        }
    }

    // MARK: - 内部 git 调用

    /// Runs git and throws on a non-zero exit (git's own message in the error), so failures reach the API as 400s.
    @discardableResult
    private func runGit(_ args: [String], in directory: String) throws -> String {
        let r = runGitStatus(args, in: directory)
        guard r.status == 0 else {
            throw MaestriError.floorOperationFailed("git \(args.first ?? "") failed: \(r.output.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        return r.output
    }

    private func runGitStatus(_ args: [String], in directory: String) -> (status: Int32, output: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = args
        process.currentDirectoryURL = URL(fileURLWithPath: directory)
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        do { try process.run() } catch { return (-1, "\(error)") }
        // Drain both pipes concurrently before waiting: a full pipe would otherwise block git forever.
        var e = Data()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global(qos: .utility).async {
            e = err.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }
        let o = out.fileHandleForReading.readDataToEndOfFile()
        group.wait()
        process.waitUntilExit()
        let text = String(data: o, encoding: .utf8) ?? ""
        return (process.terminationStatus, process.terminationStatus == 0 ? text : text + (String(data: e, encoding: .utf8) ?? ""))
    }
}
