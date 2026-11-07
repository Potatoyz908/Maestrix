import XCTest
#if canImport(open_maestri)
@testable import open_maestri
#else
@testable import OpenMaestriCore
#endif

/// Regression: `&&` chains prompts only on its own line (docs/reference/official/11-routines.md).
final class RoutineModelsTests: IsolatedDataDirTestCase {
    func testOwnLineSeparatorChainsPrompts() {
        let r = Routine(name: "chain", prompt: "pull the latest changes\n&&\nrun the test suite\n  &&  \nsummarize",
                        intervalSeconds: 300, targetTerminalId: UUID())
        XCTAssertEqual(r.prompts, ["pull the latest changes", "run the test suite", "summarize"])
    }

    func testInlineAmpersandsStayInOnePrompt() {
        XCTAssertEqual(Routine.parsePrompts("run make && make test"), ["run make && make test"])
        XCTAssertEqual(Routine.parsePrompts("line one\nline two && three"), ["line one\nline two && three"])
    }

    func testEmptySegmentsAreDropped() {
        XCTAssertEqual(Routine.parsePrompts("&&\n\n&&\nonly\n&&"), ["only"])
        XCTAssertEqual(Routine.parsePrompts("   "), [])
    }

    func testContainerWritesPayloadAndReadsBothKeys() throws {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        var c = RoutinesContainer()
        c.routines = [Routine(name: "a", prompt: "x", intervalSeconds: 60, targetTerminalId: UUID())]
        let json = try JSONSerialization.jsonObject(with: try enc.encode(c)) as? [String: Any]
        XCTAssertNotNil(json?["payload"])
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        let legacy = try dec.decode(RoutinesContainer.self, from: try enc.encode(["routines": c.routines]))
        XCTAssertEqual(legacy.routines.map(\.name), ["a"])
    }
}
