import XCTest
#if canImport(open_maestri)
@testable import open_maestri
#else
@testable import OpenMaestriCore
#endif

/// Throwaway data directory for the whole test process, published through `OPEN_MAESTRI_DATA_DIR` so that
/// PersistenceManager, InterAgentServer (run/agent.sock, run/auth.token), notes, roles… never touch the real
/// ~/.open-maestri. Removed at process exit (set OPEN_MAESTRI_KEEP_TEST_DATA=1 to keep it for debugging).
enum TestDataDir {
    static let path: String = {
        let dir = (NSTemporaryDirectory() as NSString)
            .appendingPathComponent("open-maestri-tests-\(getpid())-\(UUID().uuidString.prefix(8))")
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        setenv(PersistenceManager.dataDirEnvironmentKey, dir, 1)
        if getenv("OPEN_MAESTRI_KEEP_TEST_DATA") == nil {
            atexit { try? FileManager.default.removeItem(atPath: TestDataDir.path) }
        }
        return dir
    }()

    /// Idempotent. Aborts the run if the redirection did not take effect: a test must never reach the real dir.
    static func activate() {
        let expected = URL(fileURLWithPath: path).standardizedFileURL.path
        let actual = PersistenceManager.shared.appDataURL.path
        precondition(actual == expected, "test data dir not isolated: \(actual) != \(expected)")
        let real = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(Constants.appDataDirectoryName).standardizedFileURL.path
        precondition(actual != real, "tests must not use the real \(real)")
    }
}

/// Base class for every test case of the core target: isolates the data dir before any test of the class runs.
class IsolatedDataDirTestCase: XCTestCase {
    override class func setUp() {
        TestDataDir.activate()
        super.setUp()
    }
}
