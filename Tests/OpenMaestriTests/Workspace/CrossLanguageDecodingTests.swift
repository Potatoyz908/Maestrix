import XCTest
import Foundation
@testable import OpenMaestriCore

final class CrossLanguageDecodingTests: XCTestCase {

    func testDecodePythonGeneratedWorkspace() throws {
        let fixtureURL = URL(fileURLWithPath: "Tests/OpenMaestriTests/Fixtures/PythonGeneratedWorkspace.json")
        guard FileManager.default.fileExists(atPath: fixtureURL.path) else {
            XCTFail("PythonGeneratedWorkspace.json fixture not found at \(fixtureURL.path)")
            return
        }

        let data = try Data(contentsOf: fixtureURL)
        let pm = PersistenceManager.shared

        // Decode using Swift OpenMaestriCore PersistenceManager decoder
        let doc: WorkspaceDocument
        do {
            doc = try pm.decoder.decode(WorkspaceDocument.self, from: data)
        } catch {
            XCTFail("Failed to decode PythonGeneratedWorkspace.json: \(error)")
            return
        }

        // 1. Verify Document Schema
        XCTAssertEqual(doc.schemaVersion, 2)
        XCTAssertEqual(doc.type, "workspace")
        XCTAssertEqual(doc.payload.name, "Cross-Language Parity Workspace")
        XCTAssertEqual(doc.payload.canvasOrigin.x, 9800.0)
        XCTAssertEqual(doc.payload.canvasOrigin.y, 8500.0)
        XCTAssertEqual(doc.payload.canvasZoom, 1.0)

        // 2. Verify Nodes
        XCTAssertEqual(doc.payload.nodes.count, 5)

        // Terminal Node
        guard let termNode = doc.payload.nodes.first(where: {
            if case .terminal = $0.content { return true }
            return false
        }) else {
            XCTFail("Missing terminal node")
            return
        }
        if case .terminal(let term) = termNode.content {
            XCTAssertEqual(term.name, "Main Terminal")
            XCTAssertEqual(term.scrollbackLineCount, 42)
            XCTAssertEqual(term.status, "idle")
            XCTAssertEqual(term.command, "bash")
        }

        // Sticky Note Node
        guard let noteNode = doc.payload.nodes.first(where: {
            if case .stickyNote = $0.content { return true }
            return false
        }) else {
            XCTFail("Missing stickyNote node")
            return
        }
        if case .stickyNote(let note) = noteNode.content {
            XCTAssertEqual(note.fileName, "QuickNote.md")
            XCTAssertEqual(note.fontSize, 14)
        }

        // Portal Node
        guard let portalNode = doc.payload.nodes.first(where: {
            if case .portal = $0.content { return true }
            return false
        }) else {
            XCTFail("Missing portal node")
            return
        }
        if case .portal(let portal) = portalNode.content {
            XCTAssertEqual(portal.currentURL, "https://github.com")
        }

        // FileTree Node
        guard let fileTreeNode = doc.payload.nodes.first(where: {
            if case .fileTree = $0.content { return true }
            return false
        }) else {
            XCTFail("Missing fileTree node")
            return
        }
        if case .fileTree(let ft) = fileTreeNode.content {
            XCTAssertEqual(ft.name, "Project Files")
            XCTAssertEqual(ft.rootPath, "/tmp")
        }

        // 3. Verify Connection Arrays
        XCTAssertEqual(doc.payload.connections.count, 1)
        XCTAssertEqual(doc.payload.noteConnections.count, 1)
        XCTAssertEqual(doc.payload.portalConnections.count, 1)
        XCTAssertEqual(doc.payload.portalToPortalConnections.count, 1)
        XCTAssertEqual(doc.payload.noteToNoteConnections.count, 1)

        // 4. Test re-encode and re-decode lossless round-trip
        let reencoded = try pm.encoder.encode(doc)
        let redoc = try pm.decoder.decode(WorkspaceDocument.self, from: reencoded)
        XCTAssertEqual(redoc.payload.id, doc.payload.id)
        XCTAssertEqual(redoc.payload.nodes.count, doc.payload.nodes.count)
        XCTAssertEqual(redoc.payload.connections.count, doc.payload.connections.count)
    }
}
