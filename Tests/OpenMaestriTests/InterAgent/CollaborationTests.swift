import XCTest
@testable import OpenMaestriCore
#if canImport(Glibc)
import Glibc
#endif

/// Sprint 9 Codice acceptance (server side, isolated data dir, real bash PTYs):
/// K6 restore from workspace.json, K1b legacy ids, revocation on delete, K4 ask / --batch / --raw, list names.
final class CollaborationTests: IsolatedDataDirTestCase {

    // MARK: - Fixtures

    private func terminalNode(nodeId: UUID, terminalId: UUID, name: String, x: Double, manager: Bool = false) -> CanvasNode {
        var t = TerminalContent(name: name, agentType: "generic_shell", command: "bash", workingDirectory: "/tmp")
        t.id = terminalId
        t.isManager = manager
        return CanvasNode(id: nodeId, frame: CGRect(x: x, y: 8600, width: 400, height: 280), content: .terminal(t))
    }

    private func noteNode(_ id: UUID, name: String) -> CanvasNode {
        CanvasNode(id: id, frame: CGRect(x: 10500, y: 9000, width: 300, height: 200), content: .stickyNote(StickyNoteContent(name: name)))
    }

    private func save(_ payload: WorkspacePayload) throws {
        let pm = PersistenceManager.shared
        try pm.ensureWorkspaceDirectoryExists(id: payload.id)
        try pm.saveSync(WorkspaceDocument(payload: payload), to: pm.workspaceURL(id: payload.id))
    }

    private func startServer() async throws -> (UInt16, String) {
        try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
    }

    private func spawn(_ id: UUID, name: String, ws: UUID, agentType: String = "generic_shell") async {
        await MainActor.run {
            _ = PlatformHooks.terminals.createTerminal(id: id, command: "bash --norc --noprofile", workingDirectory: "/tmp",
                                                       workspaceId: ws, roleName: nil, displayName: name, agentType: agentType)
        }
        // wait for the first prompt
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline {
            let text = await MainActor.run { PlatformHooks.terminals.bufferText(for: id) ?? "" }
            if text.contains("$") || text.contains("#") { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
    }

    private func kill(_ ids: [UUID]) async {
        await MainActor.run { ids.forEach { PlatformHooks.terminals.removeTerminal(id: $0) } }
    }

    // MARK: - K6 + K1b

    func testRestoredLegacyConnectionsServeListAndAreRevokedOnDelete() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        let ws = UUID()
        let nodeA = UUID(), termA = UUID(), termB = UUID(), note = UUID(), connAB = UUID()
        var p = WorkspacePayload(id: ws, name: "Collab", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: nodeA, terminalId: termA, name: "Alpha", x: 9900),   // legacy: node id != terminal id
                   terminalNode(nodeId: termB, terminalId: termB, name: "Beta", x: 10400),
                   noteNode(note, name: "Architecture")]
        p.connections = [TerminalConnection(id: connAB, terminalIdA: nodeA, terminalIdB: termB)]       // stored with the NODE id
        p.noteConnections = [NoteConnection(terminalId: nodeA, noteNodeId: note)]
        try save(p)

        await MainActor.run { ConnectionManager.shared.restoreAllWorkspaces() }   // what open-maestri-server does at start
        await spawn(termA, name: "Alpha", ws: ws)
        await spawn(termB, name: "Beta", ws: ws)
        defer { Task { await self.kill([termA, termB]) } }

        let out = try await cli(port, token, ["list"], as: termA)
        XCTAssertTrue(out.contains("\"Beta\""), out)
        XCTAssertTrue(out.contains("\"Architecture\""), "real note name, not \"Note\": \(out)")
        let viaNodeId = try await cli(port, token, ["list"], as: nodeA)
        XCTAssertTrue(viaNodeId.contains("\"Beta\""), "X-Terminal-ID may be the legacy node id: \(viaNodeId)")

        let del = try await http(port, token, "DELETE", "/api/connections/\(connAB.uuidString)?workspace=\(ws.uuidString)")
        XCTAssertTrue(del.hasPrefix("HTTP/1.1 200"), del)
        let after = try await cli(port, token, ["list"], as: termA)
        XCTAssertFalse(after.contains("\"Beta\""), "permission revoked: \(after)")
        XCTAssertTrue(after.contains("\"Architecture\""))
        let gone = try await cli(port, token, ["ask", "Beta", "echo hi"], as: termA)
        XCTAssertTrue(gone.hasPrefix("error:"), gone)
    }

    // MARK: - K4

    func testAskSingleBatchAndRawOnShells() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let ws = UUID(), caller = UUID(), w1 = UUID(), w2 = UUID()
        var p = WorkspacePayload(id: ws, name: "Ask", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: caller, terminalId: caller, name: "Caller", x: 9800),
                   terminalNode(nodeId: w1, terminalId: w1, name: "Worker1", x: 10300),
                   terminalNode(nodeId: w2, terminalId: w2, name: "Worker2", x: 10800)]
        try save(p)
        for (a, b) in [(caller, w1), (caller, w2)] {
            let body = "{\"a\":\"\(a.uuidString)\",\"b\":\"\(b.uuidString)\",\"kind\":\"terminal\"}"
            let r = try await http(port, token, "POST", "/api/connections?workspace=\(ws.uuidString)", body: body)
            XCTAssertTrue(r.hasPrefix("HTTP/1.1 200"), r)
        }
        await spawn(caller, name: "Caller", ws: ws)
        await spawn(w1, name: "Worker1", ws: ws)
        await spawn(w2, name: "Worker2", ws: ws)
        defer { Task { await self.kill([caller, w1, w2]) } }

        let one = try await cli(port, token, ["ask", "Worker1", "echo $((6*7))"], as: caller)
        XCTAssertTrue(one.contains("42"), one)

        let batch = try await cli(port, token, ["ask", "--batch", "{\"Worker1\": \"echo BATCH_ONE\", \"Worker2\": \"echo BATCH_TWO\"}"], as: caller)
        let arr = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(batch.utf8)) as? [[String: String]], batch)
        XCTAssertEqual(Set(arr.compactMap { $0["name"] }), ["Worker1", "Worker2"])
        XCTAssertTrue(arr.first { $0["name"] == "Worker1" }?["output"]?.contains("BATCH_ONE") == true, batch)
        XCTAssertTrue(arr.first { $0["name"] == "Worker2" }?["output"]?.contains("BATCH_TWO") == true, batch)

        let raw = try await cli(port, token, ["ask", "Worker2", "--raw", "echo RAW_$((1+1))\\n"], as: caller)
        XCTAssertTrue(raw.contains("RAW_2"), raw)

        let check = try await cli(port, token, ["check", "Worker2", "5"], as: caller)
        XCTAssertTrue(check.contains("RAW_2"), check)
        XCTAssertFalse(check.contains("\u{1B}"), "no escape sequences")
    }

    func testAskParsing() {
        XCTAssertEqual(try? AskHandler.parse(["ask", "A", "hi"]).get(), .single(name: "A", text: "hi", raw: false))
        XCTAssertEqual(try? AskHandler.parse(["ask", "A", "--raw", "2\\n"]).get(), .single(name: "A", text: "2\\n", raw: true))
        XCTAssertEqual(try? AskHandler.parse(["ask", "--batch", "{\"B\":\"y\",\"A\":\"x\"}"]).get(),
                       .batch([(name: "A", prompt: "x"), (name: "B", prompt: "y")]))
        XCTAssertEqual(try? AskHandler.parse(["ask", "--batch", "A:x", "B:y"]).get(),
                       .batch([(name: "A", prompt: "x"), (name: "B", prompt: "y")]))
        XCTAssertNil(try? AskHandler.parse(["ask", "A"]).get())
        XCTAssertEqual(TerminalText.decodeRaw("a\\n\\t\\e[A\\x03"), Data([0x61, 0x0D, 0x09, 0x1B, 0x5B, 0x41, 0x03]))
        XCTAssertEqual(TerminalText.tidy("\u{1B}[31mred\u{1B}[0m  \r\n\n\n\nx\u{1B}]0;title\u{07}"), "red\n\nx")
    }

    // MARK: - K7

    func testNoteCreateReadScopeChainAndDelete() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let ws = UUID(), author = UUID(), stranger = UUID()
        var p = WorkspacePayload(id: ws, name: "Notes", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: author, terminalId: author, name: "Author", x: 9800),
                   terminalNode(nodeId: stranger, terminalId: stranger, name: "Stranger", x: 9800)]
        try save(p)
        await MainActor.run { ConnectionManager.shared.reloadWorkspace(ws) }

        let created = try await cli(port, token, ["note", "create", "alpha\nbeta\ngamma", "--name", "Spec", "--color", "yellow"], as: author)
        XCTAssertEqual(created, "Created note \"Spec\"")
        let doc = try PersistenceManager.shared.loadWorkspace(id: ws)
        let spec = try XCTUnwrap(doc.payload.nodes.first { WorkspaceGraph.noteName(of: $0) == "Spec" })
        XCTAssertTrue(doc.payload.noteConnections.contains { $0.terminalId == author && $0.noteNodeId == spec.id })
        XCTAssertLessThan(spec.frame.maxX, 9800, "placed left of the caller")

        let all = try await cli(port, token, ["note", "read", "Spec"], as: author)
        XCTAssertEqual(all, "[3 lines total]\n1\talpha\n2\tbeta\n3\tgamma")
        let range = try await cli(port, token, ["note", "read", "Spec", "2", "5"], as: author)
        XCTAssertEqual(range, "[lines 2-3 of 3]\n2\tbeta\n3\tgamma")
        let denied = try await cli(port, token, ["note", "read", "Spec"], as: stranger)
        XCTAssertTrue(denied.hasPrefix("error:"), "not connected -> no access: \(denied)")

        // chain: Spec <-> ChainedDoc
        let chained = UUID()
        var p2 = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        p2.nodes.append(noteNode(chained, name: "ChainedDoc"))
        p2.noteToNoteConnections.append(NoteToNoteConnection(noteNodeIdA: spec.id, noteNodeIdB: chained))
        try save(p2)
        try NoteFileManager.shared.write(filePath: NoteFileManager.shared.managedPath(workspaceId: ws, noteName: "ChainedDoc"),
                                         content: "chained content")
        let list = try await cli(port, token, ["list"], as: author)
        XCTAssertTrue(list.contains("  - name: \"Spec\"\n    - name: \"ChainedDoc\""), list)
        let r1 = try await cli(port, token, ["note", "write", "ChainedDoc", "v2"], as: author)
        XCTAssertEqual(r1, "OK")
        let r2 = try await cli(port, token, ["note", "edit", "chained", "v2", "v3"], as: author)
        XCTAssertEqual(r2, "OK")
        let r3 = try await cli(port, token, ["note", "read", "ChainedDoc"], as: author)
        XCTAssertEqual(r3, "[1 lines total]\n1\tv3")

        let specPath = NoteFileManager.shared.managedPath(workspaceId: ws, noteName: "Spec")
        let r4 = try await cli(port, token, ["note", "delete", "Spec"], as: author)
        XCTAssertEqual(r4, "Deleted note \"Spec\"")
        let after = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        XCTAssertFalse(after.nodes.contains { $0.id == spec.id })
        XCTAssertFalse(after.noteConnections.contains { $0.noteNodeId == spec.id })
        XCTAssertFalse(FileManager.default.fileExists(atPath: specPath), "managed note file removed")
    }

    // MARK: - HTTP helpers (blocking sockets, off the main actor)

    private func cli(_ port: UInt16, _ token: String, _ args: [String], as terminal: UUID) async throws -> String {
        let body = String(decoding: try JSONSerialization.data(withJSONObject: ["args": args]), as: UTF8.self)
        let resp = try await http(port, token, "POST", "/cli", body: body, extra: "X-Terminal-ID: \(terminal.uuidString)\r\n")
        guard let r = resp.range(of: "\r\n\r\n") else { return resp }
        return String(resp[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func http(_ port: UInt16, _ token: String, _ method: String, _ path: String, body: String = "",
                      extra: String = "") async throws -> String {
        try await Task.detached {
            let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
            guard fd >= 0 else { throw NSError(domain: "test", code: 1) }
            defer { close(fd) }
            var tv = timeval(tv_sec: 60, tv_usec: 0)
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
            var addr = sockaddr_in()
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = port.bigEndian
            addr.sin_addr.s_addr = inet_addr("127.0.0.1")
            let rc = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Glibc.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
            guard rc == 0 else { throw NSError(domain: "test", code: 2) }
            let req = "\(method) \(path) HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer \(token)\r\n\(extra)" +
                      "Content-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
            let data = Data(req.utf8)
            _ = data.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
            var out = Data()
            var buf = [UInt8](repeating: 0, count: 4096)
            while true {
                let n = recv(fd, &buf, buf.count, 0)
                if n <= 0 { break }
                out.append(buf, count: n)
            }
            return String(decoding: out, as: UTF8.self)
        }.value
    }
}
