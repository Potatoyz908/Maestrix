import XCTest
#if canImport(open_maestri)
@testable import open_maestri
#else
@testable import OpenMaestriCore
#endif

final class FloorManagerTests: XCTestCase {
    private let fm = FloorManager.shared
    private var testDir: String!

    override func setUpWithError() throws {
        // 创建临时 git repo 用于测试
        testDir = NSTemporaryDirectory() + "open-maestri-floor-test-\(UUID().uuidString)"
        try FileManager.default.createDirectory(atPath: testDir, withIntermediateDirectories: true)
        // 初始化 git repo
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["init"]
        process.currentDirectoryURL = URL(fileURLWithPath: testDir)
        try process.run(); process.waitUntilExit()
        // 创建初始提交（git worktree 需要至少一个提交）
        let touch = Process()
        touch.executableURL = URL(fileURLWithPath: "/usr/bin/touch")
        touch.arguments = [testDir + "/README.md"]
        try touch.run(); touch.waitUntilExit()
        let add = Process()
        add.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        add.arguments = ["add", "."]
        add.currentDirectoryURL = URL(fileURLWithPath: testDir)
        try add.run(); add.waitUntilExit()
        let commit = Process()
        commit.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        commit.arguments = ["commit", "-m", "init", "--allow-empty-message"]
        commit.currentDirectoryURL = URL(fileURLWithPath: testDir)
        commit.environment = ProcessInfo.processInfo.environment.merging([
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"
        ]) { _, new in new }
        try commit.run(); commit.waitUntilExit()
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(atPath: testDir)
    }

    // MARK: - Floor 创建

    func testCreateFloorCreatesWorktreeDirectory() throws {
        let floor = try fm.createFloor(name: "test-floor", branchName: "feature/test", workingDirectory: testDir)
        XCTAssertTrue(FileManager.default.fileExists(atPath: floor.worktreePath),
                      "Floor worktree 目录应被创建")
        // 清理
        try? fm.removeFloor(floor, workingDirectory: testDir)
    }

    func testCreateFloorReturnsCorrectBranchName() throws {
        let floor = try fm.createFloor(name: "my-floor", branchName: "my-branch", workingDirectory: testDir)
        XCTAssertEqual(floor.branchName, "my-branch")
        XCTAssertEqual(floor.name, "my-floor")
        try? fm.removeFloor(floor, workingDirectory: testDir)
    }

    func testRemoveFloorDeletesWorktreeDirectory() throws {
        let floor = try fm.createFloor(name: "remove-floor", branchName: "remove-branch", workingDirectory: testDir)
        XCTAssertTrue(FileManager.default.fileExists(atPath: floor.worktreePath))
        try fm.removeFloor(floor, workingDirectory: testDir)
        XCTAssertFalse(FileManager.default.fileExists(atPath: floor.worktreePath),
                       "Floor worktree 目录应在删除后消失")
    }

    // MARK: - FloorHooks

    func testFloorHooksDefaultValues() {
        let hooks = FloorHooks()
        XCTAssertTrue(hooks.setup.isEmpty)
        XCTAssertTrue(hooks.run.isEmpty)
        XCTAssertTrue(hooks.teardown.isEmpty)
        XCTAssertFalse(hooks.autoRunSetup)
    }

    func testHookEnvironmentVariablesContainRequiredKeys() throws {
        let floor = try fm.createFloor(name: "hook-floor", branchName: "hook-branch", workingDirectory: testDir)
        let env = FloorManager.hookEnvironment(for: floor, workingDirectory: testDir)
        XCTAssertEqual(env["MAESTRI_FLOOR_NAME"], "hook-floor")
        XCTAssertEqual(env["OMAESTRI_FLOOR_NAME"], "hook-floor")
        XCTAssertEqual(env["MAESTRI_BRANCH_NAME"], "hook-branch")
        XCTAssertEqual(env["OMAESTRI_BRANCH_NAME"], "hook-branch")
        XCTAssertEqual(env["MAESTRI_FLOOR_PATH"], floor.worktreePath)
        XCTAssertEqual(env["OMAESTRI_FLOOR_PATH"], floor.worktreePath)
        XCTAssertEqual(env["MAESTRI_ROOT_PATH"], testDir)
        XCTAssertEqual(env["OMAESTRI_ROOT_PATH"], testDir)
        XCTAssertEqual(env["MAESTRI_PROJECT_NAME"], URL(fileURLWithPath: testDir).lastPathComponent)
        XCTAssertEqual(env["OMAESTRI_PROJECT_NAME"], URL(fileURLWithPath: testDir).lastPathComponent)
        try? fm.removeFloor(floor, workingDirectory: testDir)
    }

    func testFloorSlugSanitizesUnsafeCharactersAndUpdatesGitExclude() throws {
        XCTAssertEqual(FloorManager.slugify("My Feature ../Floor 1!"), "my-feature-floor-1")
        XCTAssertEqual(FloorManager.slugify("../../../"), "floor")
        let floor = try fm.createFloor(name: "My Feature Floor", branchName: "floor/my-feature-floor", workingDirectory: testDir)
        XCTAssertTrue(floor.worktreePath.hasSuffix("/.open-maestri/floors/my-feature-floor"))
        let exclude = try String(contentsOfFile: testDir + "/.git/info/exclude", encoding: .utf8)
        XCTAssertTrue(exclude.contains("/.open-maestri/"))
        try? fm.removeFloor(floor, workingDirectory: testDir)
    }

    // MARK: - argv / landing / delete branch

    /// Runs git in `dir` with a fixed identity; returns trimmed stdout, fails the test on a non-zero exit.
    @discardableResult
    private func git(_ args: [String], in dir: String? = nil, file: StaticString = #filePath, line: UInt = #line) throws -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: dir ?? testDir)
        p.environment = ProcessInfo.processInfo.environment.merging(Self.identity) { _, new in new }
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        try p.run()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        XCTAssertEqual(p.terminationStatus, 0, "git \(args.joined(separator: " ")) failed", file: file, line: line)
        return (String(data: data, encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static let identity = ["GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                                   "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"]

    private func commitFile(_ name: String, _ text: String, in dir: String) throws {
        try text.write(toFile: dir + "/" + name, atomically: true, encoding: .utf8)
        try git(["add", name], in: dir)
        try git(["commit", "-q", "-m", "edit \(name)"], in: dir)
    }

    func testWorktreeAddArgs() {
        XCTAssertEqual(FloorManager.worktreeAddArgs(branch: "floor/x", path: "/p", existingBranch: false, baseBranch: nil),
                       ["worktree", "add", "-b", "floor/x", "--", "/p"])
        XCTAssertEqual(FloorManager.worktreeAddArgs(branch: "floor/x", path: "/p", existingBranch: false, baseBranch: "dev"),
                       ["worktree", "add", "-b", "floor/x", "--", "/p", "dev"])
        XCTAssertEqual(FloorManager.worktreeAddArgs(branch: "feature", path: "/p", existingBranch: true, baseBranch: "dev"),
                       ["worktree", "add", "--", "/p", "feature"])
    }

    func testCreateFloorFailsLoudlyOnDuplicateBranch() throws {
        let floor = try fm.createFloor(name: "dup", branchName: "floor/dup", workingDirectory: testDir)
        XCTAssertThrowsError(try fm.createFloor(name: "dup 2", branchName: "floor/dup", workingDirectory: testDir))
        try fm.removeFloor(floor, workingDirectory: testDir, deleteBranch: true)
    }

    func testLandRefusesGroundCheckedOutBranchAndFastForwardsAnother() throws {
        for (k, v) in Self.identity { setenv(k, v, 1) }
        let ground = try git(["symbolic-ref", "--short", "HEAD"])
        try git(["branch", "release"])
        let floor = try fm.createFloor(name: "ff", branchName: "floor/ff", workingDirectory: testDir)
        try commitFile("a.txt", "hello\n", in: floor.worktreePath)
        let groundHead = try git(["rev-parse", "HEAD"])

        let p = try fm.landPreview(floor: floor, target: nil, workingDirectory: testDir)
        XCTAssertEqual(p.target, ground, "default target = Ground's branch")
        XCTAssertTrue(p.branches.contains(ground) && p.branches.contains("floor/ff"))
        XCTAssertEqual(p.ahead, 1)
        XCTAssertTrue(p.targetCheckedOut && !p.canLand, "Ground's checked-out branch cannot be landed into")
        XCTAssertThrowsError(try fm.land(floor: floor, targetBranch: ground, workingDirectory: testDir))
        XCTAssertEqual(try git(["rev-parse", "HEAD"]), groundHead)

        let r = try fm.landPreview(floor: floor, target: "release", workingDirectory: testDir)
        XCTAssertTrue(r.fastForward && r.conflicts.isEmpty && !r.dirty && !r.targetCheckedOut && r.canLand)
        XCTAssertTrue(r.stat.contains("1 file changed"), r.stat)
        XCTAssertEqual(r.insertions, 1)
        XCTAssertEqual(r.deletions, 0)
        try fm.land(floor: floor, targetBranch: "release", workingDirectory: testDir)
        XCTAssertEqual(try git(["rev-parse", "release"]), try git(["rev-parse", "floor/ff"]), "fast-forward")
        XCTAssertEqual(try git(["rev-parse", "HEAD"]), groundHead, "Ground's HEAD did not move")
        XCTAssertFalse(FileManager.default.fileExists(atPath: testDir + "/a.txt"), "Ground's working tree untouched")

        try fm.removeFloor(floor, workingDirectory: testDir, deleteBranch: true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: floor.worktreePath))
        XCTAssertFalse(FileManager.default.fileExists(atPath: testDir + "/.open-maestri/floors"), "last floor removes floors/")
        XCTAssertEqual(try git(["branch", "--list", "floor/ff"]), "", "deleteBranch removed the branch")
    }

    func testStatusAndParsers() throws {
        for (k, v) in Self.identity { setenv(k, v, 1) }
        XCTAssertTrue(FloorManager.parseShortstat(" 2 files changed, 5 insertions(+), 1 deletion(-)") == (5, 1))
        XCTAssertTrue(FloorManager.parseShortstat(" 1 file changed, 1 deletion(-)") == (0, 1))
        let parsed = FloorManager.parsePorcelain(" M a.txt\nR  old.txt -> new.txt\n?? b.txt\n")
        XCTAssertEqual(parsed.map(\.path), ["a.txt", "new.txt", "b.txt"])
        XCTAssertEqual(parsed.map(\.status), ["M", "R", "??"])

        let floor = try fm.createFloor(name: "st", branchName: "floor/st", workingDirectory: testDir)
        try commitFile("s.txt", "one\ntwo\n", in: floor.worktreePath)
        try "x\n".write(toFile: floor.worktreePath + "/u.txt", atomically: true, encoding: .utf8)
        let st = try fm.status(floor: floor, workingDirectory: testDir)
        XCTAssertTrue(st.dirty)
        XCTAssertEqual(st.files.map(\.path), ["u.txt"])
        XCTAssertEqual(st.ahead, 1)
        XCTAssertEqual(st.behind, 0)
        XCTAssertEqual(st.insertions, 2)
        try fm.removeFloor(floor, workingDirectory: testDir, deleteBranch: true)
    }

    func testLandArgvNeverTouchesWorkingTree() throws {
        for (k, v) in Self.identity { setenv(k, v, 1) }
        try git(["branch", "release"])
        let floor = try fm.createFloor(name: "merge", branchName: "floor/merge", workingDirectory: testDir)
        try commitFile("f.txt", "floor\n", in: floor.worktreePath)
        // release moves on independently, so the landing is a real merge
        let rel = testDir + "/.open-maestri/rel"
        try git(["worktree", "add", "--", rel, "release"])
        try commitFile("r.txt", "release\n", in: rel)
        try git(["worktree", "remove", "--force", "--", rel])
        let groundHead = try git(["rev-parse", "HEAD"])

        let p = try fm.landPreview(floor: floor, target: "release", workingDirectory: testDir)
        XCTAssertFalse(p.fastForward)
        XCTAssertTrue(p.conflicts.isEmpty && p.canLand)
        try fm.land(floor: floor, targetBranch: "release", workingDirectory: testDir)

        XCTAssertEqual(try git(["rev-list", "--count", "--merges", "-1", "release"]), "1", "release tip is a merge commit")
        XCTAssertEqual(try git(["show", "release:f.txt"]), "floor")
        XCTAssertEqual(try git(["show", "release:r.txt"]), "release")
        XCTAssertEqual(try git(["rev-parse", "HEAD"]), groundHead, "Ground's HEAD did not move")
        XCTAssertFalse(FileManager.default.fileExists(atPath: testDir + "/f.txt"), "Ground's working tree untouched")
        try fm.removeFloor(floor, workingDirectory: testDir, deleteBranch: true)
    }

    func testLandRefusesConflictsAndDirtyFloor() throws {
        for (k, v) in Self.identity { setenv(k, v, 1) }
        let ground = try git(["symbolic-ref", "--short", "HEAD"])
        let floor = try fm.createFloor(name: "conflict", branchName: "floor/conflict", workingDirectory: testDir)
        try commitFile("README.md", "floor side\n", in: floor.worktreePath)
        try commitFile("README.md", "ground side\n", in: testDir)

        let p = try fm.landPreview(floor: floor, target: ground, workingDirectory: testDir)
        XCTAssertEqual(p.conflicts, ["README.md"])
        XCTAssertFalse(p.canLand)
        XCTAssertThrowsError(try fm.land(floor: floor, targetBranch: ground, workingDirectory: testDir))

        try "uncommitted\n".write(toFile: floor.worktreePath + "/new.txt", atomically: true, encoding: .utf8)
        let dirty = try fm.landPreview(floor: floor, target: ground, workingDirectory: testDir)
        XCTAssertTrue(dirty.dirty)
        XCTAssertEqual(dirty.dirtyFiles, ["new.txt"])
        XCTAssertEqual(try fm.landPreview(floor: floor, target: "floor/conflict", workingDirectory: testDir).sameBranch, true)
        try fm.removeFloor(floor, workingDirectory: testDir, deleteBranch: true)
    }
}
