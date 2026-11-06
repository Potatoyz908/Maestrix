import Foundation
#if canImport(Glibc)
import Glibc
private let libcSend = Glibc.send
private let libcRecv = Glibc.recv
#elseif canImport(Darwin)
import Darwin
private let libcSend = Darwin.send
private let libcRecv = Darwin.recv
#endif

/// 单个 /ws/events 订阅连接（服务端 → 客户端的文本帧推送）。
final class EventsWebSocketSession: @unchecked Sendable {
    let id = UUID()
    private let fd: Int32
    /// nil = 接收所有 workspace 的事件
    let workspaceFilter: UUID?
    private var isOpen = true
    private let lock = NSLock()
    var onClose: ((EventsWebSocketSession) -> Void)?

    init(fd: Int32, workspaceFilter: UUID?) {
        self.fd = fd
        self.workspaceFilter = workspaceFilter
    }

    /// 发送握手响应并启动读取循环（仅用于处理 ping/close）。
    func start(acceptKey: String) {
        let response = "HTTP/1.1 101 Switching Protocols\r\n" +
                       "Upgrade: websocket\r\n" +
                       "Connection: Upgrade\r\n" +
                       "Sec-WebSocket-Accept: \(acceptKey)\r\n\r\n"
        guard rawSend(Data(response.utf8)) else {
            close()
            return
        }
        DispatchQueue.global(qos: .utility).async { [self] in
            self.readLoop()
        }
    }

    func sendText(_ text: String) -> Bool {
        rawSend(WebSocketFraming.makeFrame(opcode: .text, payload: Data(text.utf8)))
    }

    private func rawSend(_ data: Data) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard isOpen else { return false }
        return data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return false }
            var sent = 0
            while sent < raw.count {
                let n = libcSend(fd, base + sent, raw.count - sent, Int32(MSG_NOSIGNAL))
                if n <= 0 { return false }
                sent += n
            }
            return true
        }
    }

    private func readLoop() {
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 2048)
        while true {
            let n = libcRecv(fd, &chunk, chunk.count, 0)
            guard n > 0 else { break }
            buffer.append(chunk, count: n)
            while let frame = WebSocketFraming.decodeFrame(from: &buffer) {
                switch frame.opcode {
                case .ping:
                    _ = rawSend(WebSocketFraming.makeFrame(opcode: .pong, payload: frame.payload))
                case .close:
                    _ = rawSend(WebSocketFraming.makeFrame(opcode: .close, payload: Data()))
                    close()
                    return
                default:
                    break // 事件通道为单向推送，忽略客户端数据
                }
            }
            if buffer.count > 1 << 20 { break }
        }
        close()
    }

    func close() {
        lock.lock()
        guard isOpen else { lock.unlock(); return }
        isOpen = false
        lock.unlock()
        #if canImport(Glibc)
        _ = Glibc.shutdown(fd, Int32(SHUT_RDWR))
        _ = Glibc.close(fd)
        #elseif canImport(Darwin)
        _ = Darwin.shutdown(fd, SHUT_RDWR)
        _ = Darwin.close(fd)
        #endif
        let cb = onClose
        onClose = nil
        cb?(self)
    }
}

/// 节点生命周期 / PTY 元数据事件总线。线程安全，可从任意线程 publish。
///
/// 事件帧格式（JSON 文本帧）:
/// `{"type":"nodeAdded","ts":"2026-...Z","workspaceId":"UUID?", ...payload}`
/// type ∈ workspaceSaved | nodeAdded | nodeRemoved |
///        terminalCreated | terminalExited | terminalResized | terminalRemoved |
///        connectionCreated | connectionChanged | connectionRemoved |
///        floorCreated | floorChanged | floorRemoved | floorLandPreview | floorLanded |
///        routineChanged | routineStarted | routineStep | routineFinished | routineFailed
/// 连接事件 payload: connectionId, kind(terminalToTerminal|terminalToNote|terminalToPortal|
/// portalToPortal|noteToNote|crossFloor), a, b (与 workspace.json 中 A/B 顺序一致)
final class EventBroadcaster: @unchecked Sendable {
    static let shared = EventBroadcaster()

    private var sessions: [UUID: EventsWebSocketSession] = [:]
    private let lock = NSLock()

    private init() {}

    var subscriberCount: Int {
        lock.lock(); defer { lock.unlock() }
        return sessions.count
    }

    func register(_ session: EventsWebSocketSession) {
        lock.lock()
        sessions[session.id] = session
        lock.unlock()
        session.onClose = { [weak self] s in
            guard let self else { return }
            self.lock.lock()
            self.sessions.removeValue(forKey: s.id)
            self.lock.unlock()
        }
    }

    func closeAll() {
        lock.lock()
        let all = Array(sessions.values)
        sessions.removeAll()
        lock.unlock()
        all.forEach { $0.close() }
    }

    /// 构造事件 JSON 字符串（UUID 保持大写，与 workspace.json 一致）。
    static func encode(type: String, workspaceId: UUID?, payload: [String: Any], date: Date = Date()) -> String? {
        var obj = payload
        obj["type"] = type
        obj["ts"] = ISO8601DateFormatter().string(from: date) // 无小数秒，匹配 Swift .iso8601
        if let workspaceId { obj["workspaceId"] = workspaceId.uuidString }
        guard JSONSerialization.isValidJSONObject(obj),
              let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    func publish(type: String, workspaceId: UUID? = nil, payload: [String: Any] = [:]) {
        guard let text = Self.encode(type: type, workspaceId: workspaceId, payload: payload) else { return }
        lock.lock()
        let targets = sessions.values.filter { s in
            guard let f = s.workspaceFilter, let w = workspaceId else { return true }
            return f == w
        }
        lock.unlock()
        for s in targets where !s.sendText(text) {
            s.close()
        }
    }
}
