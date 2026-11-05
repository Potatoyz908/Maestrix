import XCTest
@testable import OpenMaestriCore
#if canImport(Glibc)
import Glibc
#endif

final class LinuxPtyAndServerTests: XCTestCase {

    func testInterAgentServerDynamicPortAndUnixSocket() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            XCTAssertGreaterThan(server.port, 0, "InterAgentServer should be assigned a positive dynamic TCP port")
            XCTAssertTrue(FileManager.default.fileExists(atPath: InterAgentServer.globalSocketPath),
                          "Unix socket file should exist at \(InterAgentServer.globalSocketPath)")
        }
    }

    func testPOSIXPtyProcessLifecycleAndBuffer() async throws {
        let tid = UUID()
        let proc = POSIXPtyProcess(terminalId: tid, command: "echo SENTINELA_PTY_READY", workingDirectory: "/tmp")

        let exp = expectation(description: "PTY data received")
        var received = ""

        proc.onDataReceived = { text in
            received += text
            if received.contains("SENTINELA_PTY_READY") {
                exp.fulfill()
            }
        }

        try proc.start()
        defer { proc.terminate() }

        await fulfillment(of: [exp], timeout: 5.0)

        let buffer = proc.bufferText()
        XCTAssertNotNil(buffer, "bufferText should not be nil")
        XCTAssertTrue(buffer?.contains("SENTINELA_PTY_READY") == true, "Buffer should contain echo output")
    }

    func testWebSocketKeyAcceptGeneration() {
        // RFC 6455 Section 1.3 test vector
        let clientKey = "dGhlIHNhbXBsZSBub25jZQ=="
        let accept = WebSocketHandshake.acceptKey(for: clientKey)
        XCTAssertEqual(accept, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", "Sec-WebSocket-Accept must match RFC 6455 standard vector")
    }

    func testWebSocketFramingEncodingAndDecoding() {
        let text = "Hello WebSocket"
        let data = Data(text.utf8)
        let frame = WebSocketFraming.makeFrame(opcode: .text, payload: data)

        var buffer = frame
        let decoded = WebSocketFraming.decodeFrame(from: &buffer)
        XCTAssertNotNil(decoded)
        XCTAssertEqual(decoded?.opcode, .text)
        XCTAssertEqual(String(data: decoded!.payload, encoding: .utf8), text)
    }

    func testRESTEndpoints() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            let port = server.port
            XCTAssertGreaterThan(port, 0)
            let auth = "Authorization: Bearer \(server.authToken)\r\n"

            // 1. GET /api/workspace
            let getWsReq = "GET /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getWsResp = try self.tcpExchange(port: port, request: getWsReq)
            XCTAssertTrue(getWsResp.hasPrefix("HTTP/1.1 200 OK"), "GET /api/workspace should return 200 OK")
            XCTAssertTrue(getWsResp.contains("\"schemaVersion\""), "Workspace payload should contain schemaVersion")
            if let bodyRange = getWsResp.range(of: "\r\n\r\n") {
                let jsonBody = String(getWsResp[bodyRange.upperBound...])
                let obj = try? JSONSerialization.jsonObject(with: Data(jsonBody.utf8)) as? [String: Any]
                XCTAssertEqual(obj?["schemaVersion"] as? Int, 2, "schemaVersion must be 2")
            } else {
                XCTFail("Missing HTTP headers delimiter")
            }

            // 2. POST /api/terminals
            let createTermBody = "{\"command\":\"bash\",\"workingDirectory\":\"/tmp\"}"
            let postTermReq = "POST /api/terminals HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(createTermBody.utf8.count)\r\nConnection: close\r\n\r\n\(createTermBody)"
            let postTermResp = try self.tcpExchange(port: port, request: postTermReq)
            XCTAssertTrue(postTermResp.hasPrefix("HTTP/1.1 200 OK"), "POST /api/terminals should return 200 OK")
            XCTAssertTrue(postTermResp.contains("\"id\":"), "POST /api/terminals should return an id")

            // Extract terminal ID
            guard let idRange = postTermResp.range(of: "\"id\":\"") else {
                XCTFail("Could not find id in response: \(postTermResp)")
                return
            }
            let sub = postTermResp[idRange.upperBound...]
            guard let endQuote = sub.firstIndex(of: "\"") else {
                XCTFail("Invalid JSON id format")
                return
            }
            let termId = String(sub[..<endQuote])

            // 3. DELETE /api/terminals/:id
            let deleteTermReq = "DELETE /api/terminals/\(termId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let deleteTermResp = try self.tcpExchange(port: port, request: deleteTermReq)
            XCTAssertTrue(deleteTermResp.hasPrefix("HTTP/1.1 200 OK"), "DELETE /api/terminals should return 200 OK")
            XCTAssertTrue(deleteTermResp.contains("\"status\":\"ok\""))

            // 4. PUT /api/notes/:name & GET /api/notes/:name
            let noteContent = "# Hello Note\nTesting note storage."
            let putNoteReq = "PUT /api/notes/my-note HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(noteContent.utf8.count)\r\nConnection: close\r\n\r\n\(noteContent)"
            let putNoteResp = try self.tcpExchange(port: port, request: putNoteReq)
            XCTAssertTrue(putNoteResp.hasPrefix("HTTP/1.1 200 OK"), "PUT /api/notes/my-note should return 200 OK")

            let getNoteReq = "GET /api/notes/my-note HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getNoteResp = try self.tcpExchange(port: port, request: getNoteReq)
            XCTAssertTrue(getNoteResp.hasPrefix("HTTP/1.1 200 OK"), "GET /api/notes/my-note should return 200 OK")
            XCTAssertTrue(getNoteResp.contains(noteContent))

            // 5. Path traversal protection
            let badReq = "GET /api/notes/../../etc/passwd HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let badResp = try self.tcpExchange(port: port, request: badReq)
            XCTAssertTrue(badResp.hasPrefix("HTTP/1.1 400 Bad Request") || badResp.hasPrefix("HTTP/1.1 404"),
                          "Path traversal should be rejected")

            // 6. DELETE /api/notes/:name
            let deleteNoteReq = "DELETE /api/notes/my-note HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let deleteNoteResp = try self.tcpExchange(port: port, request: deleteNoteReq)
            XCTAssertTrue(deleteNoteResp.hasPrefix("HTTP/1.1 200 OK"), "DELETE /api/notes/my-note should return 200 OK")

            let verifyDeletedNoteResp = try self.tcpExchange(port: port, request: getNoteReq)
            XCTAssertTrue(verifyDeletedNoteResp.hasPrefix("HTTP/1.1 404"), "Deleted note should return 404 Not Found")

            // 7. GET /api/connections, POST /api/connections, DELETE /api/connections/:id
            let getConnReq = "GET /api/connections HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getConnResp = try self.tcpExchange(port: port, request: getConnReq)
            XCTAssertTrue(getConnResp.hasPrefix("HTTP/1.1 200 OK"), "GET /api/connections should return 200 OK")

            let nodeA = UUID()
            let nodeB = UUID()
            let postConnBody = "{\"a\":\"\(nodeA.uuidString)\",\"b\":\"\(nodeB.uuidString)\"}"
            let postConnReq = "POST /api/connections HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(postConnBody.utf8.count)\r\nConnection: close\r\n\r\n\(postConnBody)"
            let postConnResp = try self.tcpExchange(port: port, request: postConnReq)
            XCTAssertTrue(postConnResp.hasPrefix("HTTP/1.1 200 OK"), "POST /api/connections should return 200 OK")
            XCTAssertTrue(postConnResp.contains("\"id\":"), "POST /api/connections should return id")

            guard let connIdRange = postConnResp.range(of: "\"id\":\"") else {
                XCTFail("Could not find connection id in: \(postConnResp)")
                return
            }
            let connSub = postConnResp[connIdRange.upperBound...]
            guard let connEndQuote = connSub.firstIndex(of: "\"") else {
                XCTFail("Invalid JSON id format")
                return
            }
            let connId = String(connSub[..<connEndQuote])

            // Verify connection in GET
            let verifyConnResp = try self.tcpExchange(port: port, request: getConnReq)
            XCTAssertTrue(verifyConnResp.contains(connId), "GET /api/connections should contain created connection")

            // Delete connection
            let delConnReq = "DELETE /api/connections/\(connId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let delConnResp = try self.tcpExchange(port: port, request: delConnReq)
            XCTAssertTrue(delConnResp.hasPrefix("HTTP/1.1 200 OK"), "DELETE /api/connections/:id should return 200 OK")

            // Verify connection removed
            let verifyDelConnResp = try self.tcpExchange(port: port, request: getConnReq)
            XCTAssertFalse(verifyDelConnResp.contains(connId), "Deleted connection should not appear in GET /api/connections")
        }
    }

    func testISO8601WithMillisecondsDecodable() throws {
        struct DateTest: Decodable {
            let createdAt: Date
        }
        let jsonWithoutMs = "{\"createdAt\":\"2026-11-05T15:27:28Z\"}"
        let docWithoutMs = try PersistenceManager.shared.decoder.decode(DateTest.self, from: Data(jsonWithoutMs.utf8))
        XCTAssertNotNil(docWithoutMs)

        let jsonWithMs = "{\"createdAt\":\"2026-11-05T15:27:28.123Z\"}"
        let docWithMs = try PersistenceManager.shared.decoder.decode(DateTest.self, from: Data(jsonWithMs.utf8))
        XCTAssertNotNil(docWithMs)
    }

    func testWebSocketUpgradeHandshake() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            let port = server.port

            // First create a terminal
            let termId = UUID()
            let tm = PlatformHooks.terminals
            tm.createTerminal(
                id: termId,
                command: "echo HELLO_WS",
                workingDirectory: "/tmp",
                workspaceId: nil,
                roleName: nil,
                displayName: "test",
                agentType: "generic_shell"
            )

            // Connect TCP client and perform WebSocket handshake
            let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
            XCTAssertGreaterThanOrEqual(fd, 0)
            defer { close(fd) }

            var addr = sockaddr_in()
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = port.bigEndian
            addr.sin_addr.s_addr = inet_addr("127.0.0.1")

            let connRes = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            XCTAssertEqual(connRes, 0)

            let clientKey = "dGhlIHNhbXBsZSBub25jZQ=="
            let wsReq = "GET /ws/pty/\(termId.uuidString) HTTP/1.1\r\n" +
                        "Host: 127.0.0.1:\(port)\r\n" +
                        "Authorization: Bearer \(server.authToken)\r\n" +
                        "Upgrade: websocket\r\n" +
                        "Connection: Upgrade\r\n" +
                        "Sec-WebSocket-Key: \(clientKey)\r\n" +
                        "Sec-WebSocket-Version: 13\r\n\r\n"

            let reqData = Data(wsReq.utf8)
            _ = reqData.withUnsafeBytes { raw in
                send(fd, raw.baseAddress, raw.count, 0)
            }

            var respData = Data()
            var buf = [UInt8](repeating: 0, count: 1024)
            let n = recv(fd, &buf, buf.count, 0)
            XCTAssertGreaterThan(n, 0)
            respData.append(buf, count: n)

            let respString = String(decoding: respData, as: UTF8.self)
            XCTAssertTrue(respString.hasPrefix("HTTP/1.1 101 Switching Protocols"), "Should return 101 Switching Protocols")
            XCTAssertTrue(respString.contains("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo="),
                          "Handshake response must include correct Sec-WebSocket-Accept")
            XCTAssertTrue(respString.contains("Upgrade: websocket"))
        }
    }

    func testSecurityRejectionWithoutAuthToken() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            // Request without token should be 401 Unauthorized
            let unauthReq = "GET /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            let unauthResp = try self.tcpExchange(port: server.port, request: unauthReq)
            XCTAssertTrue(unauthResp.hasPrefix("HTTP/1.1 401"), "TCP request without token must return 401 Unauthorized")

            // Request with invalid Origin should be 403 Forbidden
            let evilOriginReq = "GET /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: http://evil.com\r\nAuthorization: Bearer \(server.authToken)\r\nConnection: close\r\n\r\n"
            let evilOriginResp = try self.tcpExchange(port: server.port, request: evilOriginReq)
            XCTAssertTrue(evilOriginResp.hasPrefix("HTTP/1.1 403"), "Request with unauthorized origin must return 403 Forbidden")

            // OPTIONS preflight with allowed origin should return 204
            let optionsReq = "OPTIONS /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: http://127.0.0.1:5173\r\nConnection: close\r\n\r\n"
            let optionsResp = try self.tcpExchange(port: server.port, request: optionsReq)
            XCTAssertTrue(optionsResp.hasPrefix("HTTP/1.1 204"), "OPTIONS preflight must return 204")
            XCTAssertTrue(optionsResp.contains("Access-Control-Allow-Origin: http://127.0.0.1:5173"))
        }
    }

    func testNotePercentDecodingAndExtensionStripping() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            let auth = "Authorization: Bearer \(server.authToken)\r\n"
            let text = "Unicode & space note content"

            // PUT /api/notes/minha%20nota.md
            let putReq = "PUT /api/notes/minha%20nota.md HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(text.utf8.count)\r\nConnection: close\r\n\r\n\(text)"
            let putResp = try self.tcpExchange(port: server.port, request: putReq)
            XCTAssertTrue(putResp.hasPrefix("HTTP/1.1 200 OK"))

            // GET without .md and with percent decoding
            let getReq = "GET /api/notes/minha%20nota HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getResp = try self.tcpExchange(port: server.port, request: getReq)
            XCTAssertTrue(getResp.hasPrefix("HTTP/1.1 200 OK"))
            XCTAssertTrue(getResp.contains(text))

            // Cleanup
            let delReq = "DELETE /api/notes/minha%20nota HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            _ = try self.tcpExchange(port: server.port, request: delReq)
        }
    }

    func testConnectionKindAnd21PointsInterpolation() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            let auth = "Authorization: Bearer \(server.authToken)\r\n"
            let idA = UUID()
            let idB = UUID()

            // Pass 2 endpoints and kind="terminalToNote"
            let body = "{\"a\":\"\(idA.uuidString)\",\"b\":\"\(idB.uuidString)\",\"kind\":\"terminalToNote\",\"ropePoints\":[[100,200],[300,400]]}"
            let postReq = "POST /api/connections HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
            let postResp = try self.tcpExchange(port: server.port, request: postReq)
            XCTAssertTrue(postResp.hasPrefix("HTTP/1.1 200 OK"))

            let getReq = "GET /api/connections HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getResp = try self.tcpExchange(port: server.port, request: getReq)
            XCTAssertTrue(getResp.contains("\"type\" : \"terminalToNote\"") || getResp.contains("\"type\":\"terminalToNote\""))
        }
    }

    func testPhase4Endpoints() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            let auth = "Authorization: Bearer \(server.authToken)\r\n"
            let port = server.port

            // 1. GET /api/sidebar-layout
            let getLayoutReq = "GET /api/sidebar-layout HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getLayoutResp = try self.tcpExchange(port: port, request: getLayoutReq)
            XCTAssertTrue(getLayoutResp.hasPrefix("HTTP/1.1 200 OK"))
            XCTAssertTrue(getLayoutResp.contains("\"schemaVersion\":2") || getLayoutResp.contains("\"schemaVersion\" : 2"))

            // 2. PUT /api/sidebar-layout
            let putLayoutBody = "{\"schemaVersion\":2,\"topLevelItems\":[],\"groups\":[{\"id\":\"\(UUID().uuidString)\",\"name\":\"TestGroup\",\"isCollapsed\":false,\"items\":[]}]}"
            let putLayoutReq = "PUT /api/sidebar-layout HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(putLayoutBody.utf8.count)\r\nConnection: close\r\n\r\n\(putLayoutBody)"
            let putLayoutResp = try self.tcpExchange(port: port, request: putLayoutReq)
            XCTAssertTrue(putLayoutResp.hasPrefix("HTTP/1.1 200 OK"))

            // 3. GET /api/workspaces
            let getWorkspacesReq = "GET /api/workspaces HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getWorkspacesResp = try self.tcpExchange(port: port, request: getWorkspacesReq)
            XCTAssertTrue(getWorkspacesResp.hasPrefix("HTTP/1.1 200 OK"))

            // 4. POST /api/workspaces
            let postWorkspaceBody = "{\"name\":\"Linux Parity Test Workspace\",\"workingDirectory\":\"/tmp\"}"
            let postWorkspaceReq = "POST /api/workspaces HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(postWorkspaceBody.utf8.count)\r\nConnection: close\r\n\r\n\(postWorkspaceBody)"
            let postWorkspaceResp = try self.tcpExchange(port: port, request: postWorkspaceReq)
            XCTAssertTrue(postWorkspaceResp.hasPrefix("HTTP/1.1 201 Created"))
            XCTAssertTrue(postWorkspaceResp.contains("Linux Parity Test Workspace"))

            // 5. GET /api/files
            let getFilesReq = "GET /api/files?root=\(FileManager.default.currentDirectoryPath)&path=Sources HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getFilesResp = try self.tcpExchange(port: port, request: getFilesReq)
            XCTAssertTrue(getFilesResp.hasPrefix("HTTP/1.1 200 OK"))
            XCTAssertTrue(getFilesResp.contains("InterAgent"))

            // 6. GET /api/git/status
            let getGitReq = "GET /api/git/status?root=\(FileManager.default.currentDirectoryPath) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n"
            let getGitResp = try self.tcpExchange(port: port, request: getGitReq)
            XCTAssertTrue(getGitResp.hasPrefix("HTTP/1.1 200 OK"))

            // 7. POST /api/nodes (terminal, portal, stickyNote, fileTree)
            for nodeType in ["terminal", "stickyNote", "portal", "fileTree"] {
                let postNodeBody = "{\"nodeType\":\"\(nodeType)\",\"frame\":[[50,50],[400,300]]}"
                let postNodeReq = "POST /api/nodes HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(postNodeBody.utf8.count)\r\nConnection: close\r\n\r\n\(postNodeBody)"
                let postNodeResp = try self.tcpExchange(port: port, request: postNodeReq)
                XCTAssertTrue(postNodeResp.hasPrefix("HTTP/1.1 201 Created"), "POST /api/nodes for \(nodeType) should return 201 Created")
                XCTAssertTrue(postNodeResp.contains("\"\(nodeType)\"") || postNodeResp.contains("\"id\""), "POST /api/nodes for \(nodeType) should serialize node")
            }
        }
    }

    // MARK: - Helpers

    private func tcpExchange(port: UInt16, request: String) throws -> String {
        let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
        guard fd >= 0 else { throw NSError(domain: "test", code: 1) }
        defer { close(fd) }

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let connectRes = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard connectRes == 0 else { throw NSError(domain: "test", code: 2) }

        let reqData = Data(request.utf8)
        _ = reqData.withUnsafeBytes { raw in
            send(fd, raw.baseAddress, raw.count, 0)
        }

        var responseData = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = recv(fd, &buf, buf.count, 0)
            if n > 0 {
                responseData.append(buf, count: n)
            } else {
                break
            }
        }
        return String(decoding: responseData, as: UTF8.self)
    }
}
