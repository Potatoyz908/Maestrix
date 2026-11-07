import XCTest
#if canImport(open_maestri)
@testable import open_maestri
#else
@testable import OpenMaestriCore
#endif

/// OPEN_MAESTRI_DATA_DIR replaces ~/.open-maestri for every data path; without it nothing changes.
final class DataDirOverrideTests: IsolatedDataDirTestCase {

    func testResolveDataDirectory() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        XCTAssertEqual(PersistenceManager.resolveDataDirectory(override: nil).path,
                       home.appendingPathComponent(".open-maestri").path)
        XCTAssertEqual(PersistenceManager.resolveDataDirectory(override: "  ").path,
                       home.appendingPathComponent(".open-maestri").path, "blank = unset")
        XCTAssertEqual(PersistenceManager.resolveDataDirectory(override: "/tmp/x/../om-data/").path, "/tmp/om-data")
        XCTAssertEqual(PersistenceManager.resolveDataDirectory(override: "~/om-data").path,
                       home.appendingPathComponent("om-data").path)
        let cwd = FileManager.default.currentDirectoryPath
        XCTAssertEqual(PersistenceManager.resolveDataDirectory(override: "rel/dir").path,
                       URL(fileURLWithPath: cwd).appendingPathComponent("rel/dir").standardizedFileURL.path)
    }

    func testEveryDataPathFollowsTheOverride() throws {
        let root = URL(fileURLWithPath: TestDataDir.path).standardizedFileURL.path
        let pm = PersistenceManager.shared
        let ws = UUID(), term = UUID()
        for url in [pm.appDataURL, pm.manifestURL, pm.appStateURL, pm.preferencesURL, pm.routinesURL,
                    pm.sidebarLayoutURL, pm.workspaceURL(id: ws), pm.notesDirURL(workspaceId: ws),
                    pm.scrollbackURL(terminalId: term, workspaceId: ws)] {
            XCTAssertTrue(url.path.hasPrefix(root), url.path)
        }
        XCTAssertEqual(InterAgentServer.globalSocketPath, root + "/run/agent.sock")
        XCTAssertTrue(RoleInjector.shared.roleDirPath(roleId: UUID(), workingDirectory: "").hasPrefix(root + "/roles/"))
    }

    func testServerWritesTokenAndSocketUnderTheOverride() async throws {
        let root = URL(fileURLWithPath: TestDataDir.path).standardizedFileURL.path
        let socket: String? = try await MainActor.run {
            try InterAgentServer.shared.start()
            return InterAgentServer.shared.currentSocketPath
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        XCTAssertEqual(socket, root + "/run/agent.sock")
        XCTAssertTrue(FileManager.default.fileExists(atPath: root + "/run/agent.sock"))
        if ProcessInfo.processInfo.environment["MAESTRI_AUTH_TOKEN"]?.isEmpty ?? true {
            XCTAssertTrue(FileManager.default.fileExists(atPath: root + "/run/auth.token"))
        }
    }
}
