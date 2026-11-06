import XCTest
@testable import OpenMaestriCore
#if canImport(CoreGraphics)
import CoreGraphics
#endif

/// `WorkspacePayload.terminalCount`: derived from the `.terminal` nodes, never stored (schema unchanged).
final class WorkspaceTerminalCountTests: XCTestCase {

    private func node(_ content: NodeContent) -> CanvasNode {
        CanvasNode(frame: CGRect(x: 9800, y: 8500, width: 500, height: 350), content: content)
    }

    func testCountsOnlyTerminalNodes() {
        var p = WorkspacePayload(name: "W", workingDirectory: "/tmp")
        XCTAssertEqual(p.terminalCount, 0)
        p.nodes = [
            node(.terminal(TerminalContent(name: "A"))),
            node(.stickyNote(StickyNoteContent(name: "N"))),
            node(.terminal(TerminalContent(name: "B", agentType: "claude_code"))),
            node(.portal(PortalContent(name: "P"))),
        ]
        XCTAssertEqual(p.terminalCount, 2)
        p.nodes.removeAll { $0.content.terminalContent?.name == "A" }
        XCTAssertEqual(p.terminalCount, 1)

        let wm = WorkspaceManager(name: "W", workingDirectory: "/tmp")
        wm.nodes = p.nodes
        XCTAssertEqual(wm.terminalCount, 1, "sidebar badge and API share one derivation")
    }

    func testCountIsNotPersisted() throws {
        var p = WorkspacePayload(name: "W", workingDirectory: "/tmp")
        p.nodes = [node(.terminal(TerminalContent(name: "A")))]
        let doc = WorkspaceDocument(payload: p)
        let data = try PersistenceManager.shared.encoder.encode(doc)
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertFalse(json.contains("terminalCount"), "workspace.json schemaVersion 2 must not gain a field")
        XCTAssertTrue(json.contains("\"terminal\""))
        XCTAssertTrue(json.contains("\"_0\""), "NodeContent keeps the {\"terminal\":{\"_0\":...}} wrapper")
        let back = try PersistenceManager.shared.decoder.decode(WorkspaceDocument.self, from: data)
        XCTAssertEqual(back.payload.terminalCount, 1)
        XCTAssertEqual(back.schemaVersion, Constants.schemaVersion)
    }
}
