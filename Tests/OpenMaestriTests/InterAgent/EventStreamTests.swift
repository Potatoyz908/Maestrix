import XCTest
@testable import OpenMaestriCore
#if canImport(Glibc)
import Glibc
#endif

/// /ws/events 生命周期事件流与 /api/terminals PTY 元数据测试。
/// 阻塞式 socket 客户端运行在后台线程，避免与 @MainActor 的 HTTP handler 死锁。
final class EventStreamTests: XCTestCase {

    func testEventEncodingFormat() throws {
        let ws = UUID()
        let text = try XCTUnwrap(EventBroadcaster.encode(
            type: "terminalResized", workspaceId: ws,
            payload: ["terminalId": "ABC", "cols": 100, "rows": 30],
            date: Date(timeIntervalSince1970: 0)
        ))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "terminalResized")
        XCTAssertEqual(obj["ts"] as? String, "1970-01-01T00:00:00Z", "ISO8601 without fractional seconds")
        XCTAssertEqual(obj["workspaceId"] as? String, ws.uuidString)
        XCTAssertEqual(obj["cols"] as? Int, 100)
    }

    func testEventsRequireAuth() async throws {
        let port: UInt16 = try await MainActor.run {
            try InterAgentServer.shared.start()
            return InterAgentServer.shared.port
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let resp = try await Task.detached { try Self.exchange(port: port, request:
            "GET /ws/events HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n") }.value
        XCTAssertTrue(resp.hasPrefix("HTTP/1.1 401"), "unauthenticated /ws/events must be rejected: \(resp.prefix(40))")
    }

    func testTerminalLifecycleEventsAndMetadata() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        let termId = UUID()
        let result: [String] = try await Task.detached {
            let ws = try Self.openEventSocket(port: port, token: token)
            defer { close(ws) }
            let auth = "Authorization: Bearer \(token)\r\n"

            // 创建终端 -> terminalCreated
            let body = "{\"id\":\"\(termId.uuidString)\",\"command\":\"\",\"workingDirectory\":\"/tmp\"}"
            let post = try Self.exchange(port: port, request:
                "POST /api/terminals HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)")
            XCTAssertTrue(post.contains("200 OK"))
            let created = try Self.readEvent(ws, type: "terminalCreated")
            XCTAssertEqual(created["terminalId"] as? String, termId.uuidString)
            XCTAssertEqual(created["running"] as? Bool, true)
            XCTAssertEqual(created["cols"] as? Int, 80)

            // 元数据
            let meta = try Self.exchange(port: port, request:
                "GET /api/terminals/\(termId.uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(meta.contains("\"pid\":"), meta)
            XCTAssertTrue(meta.contains("\"rows\":24"), meta)
            let list = try Self.exchange(port: port, request:
                "GET /api/terminals HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(list.contains(termId.uuidString), list)
            let missing = try Self.exchange(port: port, request:
                "GET /api/terminals/\(UUID().uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(missing.hasPrefix("HTTP/1.1 404"))

            // resize -> terminalResized
            await MainActor.run { LinuxTerminalManager.shared.resize(terminalId: termId, cols: 120, rows: 40) }
            let resized = try Self.readEvent(ws, type: "terminalResized")
            XCTAssertEqual(resized["cols"] as? Int, 120)
            XCTAssertEqual(resized["rows"] as? Int, 40)

            // 删除 -> terminalRemoved
            let del = try Self.exchange(port: port, request:
                "DELETE /api/terminals/\(termId.uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(del.contains("200 OK"))
            let removed = try Self.readEvent(ws, type: "terminalRemoved")
            XCTAssertEqual(removed["terminalId"] as? String, termId.uuidString)
            return ["ok"]
        }.value
        XCTAssertEqual(result, ["ok"])
    }

    // MARK: - Helpers (blocking, call off the main actor)

    private static func connect(port: UInt16) throws -> Int32 {
        let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
        guard fd >= 0 else { throw NSError(domain: "test", code: 1) }
        var tv = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let rc = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Glibc.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard rc == 0 else { close(fd); throw NSError(domain: "test", code: 2) }
        return fd
    }

    private static func exchange(port: UInt16, request: String) throws -> String {
        let fd = try connect(port: port)
        defer { close(fd) }
        let data = Data(request.utf8)
        _ = data.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
        var out = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = recv(fd, &buf, buf.count, 0)
            if n <= 0 { break }
            out.append(buf, count: n)
        }
        return String(decoding: out, as: UTF8.self)
    }

    private static func openEventSocket(port: UInt16, token: String) throws -> Int32 {
        let fd = try connect(port: port)
        let req = "GET /ws/events HTTP/1.1\r\nHost: 127.0.0.1:\(port)\r\nAuthorization: Bearer \(token)\r\n" +
                  "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
                  "Sec-WebSocket-Version: 13\r\n\r\n"
        let data = Data(req.utf8)
        _ = data.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
        var buf = [UInt8](repeating: 0, count: 1024)
        let n = recv(fd, &buf, buf.count, 0)
        let head = String(decoding: buf[..<max(n, 0)], as: UTF8.self)
        guard head.hasPrefix("HTTP/1.1 101") else { close(fd); throw NSError(domain: "test", code: 3, userInfo: [NSLocalizedDescriptionKey: head]) }
        return fd
    }

    /// 读取文本帧直到收到指定 type 的事件（跳过其它事件）。
    private static func readEvent(_ fd: Int32, type: String) throws -> [String: Any] {
        var buffer = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        let deadline = Date().addingTimeInterval(8)
        while Date() < deadline {
            while let frame = WebSocketFraming.decodeFrame(from: &buffer) {
                guard frame.opcode == .text,
                      let obj = try? JSONSerialization.jsonObject(with: frame.payload) as? [String: Any] else { continue }
                if obj["type"] as? String == type { return obj }
            }
            let n = recv(fd, &buf, buf.count, 0)
            if n <= 0 { break }
            buffer.append(buf, count: n)
        }
        throw NSError(domain: "test", code: 4, userInfo: [NSLocalizedDescriptionKey: "timeout waiting for \(type)"])
    }
}
