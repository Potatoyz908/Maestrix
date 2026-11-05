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

    func createFloor(name: String, branchName: String, workingDirectory: String) throws -> Floor {
        // 验证工作目录是 git repo
        let gitDir = workingDirectory + "/.git"
        guard FileManager.default.fileExists(atPath: gitDir) else {
            let fmt = Bundle.main.localizedString(forKey: "floor.error.not_git_repo", value: nil, table: nil)
            throw MaestriError.terminalConnectionFailed(String(format: fmt, workingDirectory))
        }

        Self.ensureGitExclude(in: workingDirectory)
        let floor = Floor(name: name, branchName: branchName, workspaceDir: workingDirectory)
        try runGit(["worktree", "add", "-b", branchName, floor.worktreePath], in: workingDirectory)
        logger.info("Floor '\(name)' created at \(floor.worktreePath)")
        return floor
    }

    // MARK: - Floor 删除

    func removeFloor(_ floor: Floor, workingDirectory: String) throws {
        try runGit(["worktree", "remove", "--force", floor.worktreePath], in: workingDirectory)
        logger.info("Floor '\(floor.name)' removed")
    }

    // MARK: - Landing（合并提交到目标分支）

    func land(floor: Floor, targetBranch: String, workingDirectory: String) throws {
        // 获取 Floor 分支提交到主仓库
        try runGit([
            "fetch",
            floor.worktreePath,
            "\(floor.branchName):\(floor.branchName)"
        ], in: workingDirectory)
        try runGit(["merge", floor.branchName, "--no-ff", "-m", "Landing: \(floor.name)"], in: workingDirectory)
        logger.info("Floor '\(floor.name)' landed onto '\(targetBranch)'")
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

    @discardableResult
    private func runGit(_ args: [String], in directory: String) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = args
        process.currentDirectoryURL = URL(fileURLWithPath: directory)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        process.waitUntilExit()
        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }
}
