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

            // 1. GET /api/workspace
            let getWsReq = "GET /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
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
            let postTermReq = "POST /api/terminals HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: \(createTermBody.utf8.count)\r\nConnection: close\r\n\r\n\(createTermBody)"
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
            let deleteTermReq = "DELETE /api/terminals/\(termId) HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            let deleteTermResp = try self.tcpExchange(port: port, request: deleteTermReq)
            XCTAssertTrue(deleteTermResp.hasPrefix("HTTP/1.1 200 OK"), "DELETE /api/terminals should return 200 OK")
            XCTAssertTrue(deleteTermResp.contains("\"status\":\"ok\""))

            // 4. PUT /api/notes/:name & GET /api/notes/:name
            let noteContent = "# Hello Note\nTesting note storage."
            let putNoteReq = "PUT /api/notes/my-note HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: \(noteContent.utf8.count)\r\nConnection: close\r\n\r\n\(noteContent)"
            let putNoteResp = try self.tcpExchange(port: port, request: putNoteReq)
            XCTAssertTrue(putNoteResp.hasPrefix("HTTP/1.1 200 OK"), "PUT /api/notes/my-note should return 200 OK")

            let getNoteReq = "GET /api/notes/my-note HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            let getNoteResp = try self.tcpExchange(port: port, request: getNoteReq)
            XCTAssertTrue(getNoteResp.hasPrefix("HTTP/1.1 200 OK"), "GET /api/notes/my-note should return 200 OK")
            XCTAssertTrue(getNoteResp.contains(noteContent))

            // 5. Path traversal protection
            let badReq = "GET /api/notes/../../etc/passwd HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            let badResp = try self.tcpExchange(port: port, request: badReq)
            XCTAssertTrue(badResp.hasPrefix("HTTP/1.1 400 Bad Request") || badResp.hasPrefix("HTTP/1.1 404"),
                          "Path traversal should be rejected")
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
