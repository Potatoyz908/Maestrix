import Foundation
#if canImport(Glibc)
import Glibc
private let libcSend = Glibc.send
#elseif canImport(Darwin)
import Darwin
private let libcSend = Darwin.send
#endif

enum WebSocketOpcode: UInt8 {
    case continuation = 0x00
    case text = 0x01
    case binary = 0x02
    case close = 0x08
    case ping = 0x09
    case pong = 0x0A
}

enum WebSocketHandshake {
    /// RFC 3174 SHA-1 实现
    static func sha1(_ message: [UInt8]) -> [UInt8] {
        var h0: UInt32 = 0x67452301
        var h1: UInt32 = 0xEFCDAB89
        var h2: UInt32 = 0x98BADCFE
        var h3: UInt32 = 0x10325476
        var h4: UInt32 = 0xC3D2E1F0

        let ml = UInt64(message.count) * 8
        var msg = message
        msg.append(0x80)
        while (msg.count % 64) != 56 {
            msg.append(0)
        }
        for i in (0..<8).reversed() {
            msg.append(UInt8((ml >> (i * 8)) & 0xff))
        }

        for chunkStart in stride(from: 0, to: msg.count, by: 64) {
            var w = [UInt32](repeating: 0, count: 80)
            for i in 0..<16 {
                let offset = chunkStart + i * 4
                w[i] = (UInt32(msg[offset]) << 24) |
                       (UInt32(msg[offset + 1]) << 16) |
                       (UInt32(msg[offset + 2]) << 8) |
                       (UInt32(msg[offset + 3]))
            }
            for i in 16..<80 {
                let val = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]
                w[i] = (val << 1) | (val >> 31)
            }

            var a = h0
            var b = h1
            var c = h2
            var d = h3
            var e = h4

            for i in 0..<80 {
                var f: UInt32 = 0
                var k: UInt32 = 0
                if i < 20 {
                    f = (b & c) | ((~b) & d)
                    k = 0x5A827999
                } else if i < 40 {
                    f = b ^ c ^ d
                    k = 0x6ED9EBA1
                } else if i < 60 {
                    f = (b & c) | (b & d) | (c & d)
                    k = 0x8F1BBCDC
                } else {
                    f = b ^ c ^ d
                    k = 0xCA62C1D6
                }

                let temp = ((a << 5) | (a >> 27)) &+ f &+ e &+ k &+ w[i]
                e = d
                d = c
                c = (b << 30) | (b >> 2)
                b = a
                a = temp
            }

            h0 = h0 &+ a
            h1 = h1 &+ b
            h2 = h2 &+ c
            h3 = h3 &+ d
            h4 = h4 &+ e
        }

        var result = [UInt8]()
        result.reserveCapacity(20)
        for h in [h0, h1, h2, h3, h4] {
            result.append(UInt8((h >> 24) & 0xff))
            result.append(UInt8((h >> 16) & 0xff))
            result.append(UInt8((h >> 8) & 0xff))
            result.append(UInt8(h & 0xff))
        }
        return result
    }

    /// 根据 clientKey 计算 RFC 6455 Sec-WebSocket-Accept 响应值
    static func acceptKey(for clientKey: String) -> String {
        let concatenated = clientKey + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        let hash = sha1(Array(concatenated.utf8))
        return Data(hash).base64EncodedString()
    }
}

enum WebSocketFraming {
    /// 打包服务器发送给客户端的帧（无屏蔽掩码）
    static func makeFrame(opcode: WebSocketOpcode, payload: Data) -> Data {
        var frame = Data()
        frame.append(0x80 | opcode.rawValue) // FIN=1
        let len = payload.count
        if len <= 125 {
            frame.append(UInt8(len))
        } else if len <= 65535 {
            frame.append(126)
            frame.append(UInt8((len >> 8) & 0xff))
            frame.append(UInt8(len & 0xff))
        } else {
            frame.append(127)
            for i in (0..<8).reversed() {
                frame.append(UInt8((UInt64(len) >> (i * 8)) & 0xff))
            }
        }
        frame.append(payload)
        return frame
    }

    struct DecodedFrame {
        let opcode: WebSocketOpcode
        let payload: Data
    }

    /// 从缓冲区解码客户端发来的帧（有屏蔽掩码）
    static func decodeFrame(from buffer: inout Data) -> DecodedFrame? {
        guard buffer.count >= 2 else { return nil }
        let b0 = buffer[buffer.startIndex]
        let b1 = buffer[buffer.startIndex + 1]
        guard let opcode = WebSocketOpcode(rawValue: b0 & 0x0F) else {
            // 无效操作码，消费 1 字节丢弃
            buffer.removeFirst()
            return nil
        }
        let isMasked = (b1 & 0x80) != 0
        var payloadLen = Int(b1 & 0x7F)
        var headerLen = 2
        if payloadLen == 126 {
            guard buffer.count >= 4 else { return nil }
            payloadLen = (Int(buffer[buffer.startIndex + 2]) << 8) | Int(buffer[buffer.startIndex + 3])
            headerLen = 4
        } else if payloadLen == 127 {
            guard buffer.count >= 10 else { return nil }
            var l: UInt64 = 0
            for i in 0..<8 {
                l = (l << 8) | UInt64(buffer[buffer.startIndex + 2 + i])
            }
            payloadLen = Int(l)
            headerLen = 10
        }

        let maskOffset = headerLen
        if isMasked {
            headerLen += 4
        }

        guard buffer.count >= headerLen + payloadLen else { return nil }

        let payloadStartIndex = buffer.startIndex + headerLen
        var unmasked = Data(count: payloadLen)
        if isMasked {
            let maskStartIndex = buffer.startIndex + maskOffset
            let maskKey = [
                buffer[maskStartIndex],
                buffer[maskStartIndex + 1],
                buffer[maskStartIndex + 2],
                buffer[maskStartIndex + 3]
            ]
            var bytes = [UInt8](repeating: 0, count: payloadLen)
            for i in 0..<payloadLen {
                bytes[i] = buffer[payloadStartIndex + i] ^ maskKey[i % 4]
            }
            unmasked = Data(bytes)
        } else {
            unmasked = buffer.subdata(in: payloadStartIndex..<(payloadStartIndex + payloadLen))
        }

        buffer.removeSubrange(buffer.startIndex..<(buffer.startIndex + headerLen + payloadLen))
        return DecodedFrame(opcode: opcode, payload: unmasked)
    }
}

/// 管理单个终端的 WebSocket 会话 (/ws/pty/:id)
final class PTYWebSocketSession: @unchecked Sendable {
    private let fd: Int32
    let terminalId: UUID
    private(set) var isOpen: Bool = true
    private var cancelObserver: (() -> Void)?
    private let lock = NSLock()
    var onClose: ((PTYWebSocketSession) -> Void)?

    init(fd: Int32, terminalId: UUID) {
        self.fd = fd
        self.terminalId = terminalId
    }

    func send(data: Data) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard isOpen else { return false }
        return data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return false }
            var sent = 0
            let total = raw.count
            while sent < total {
                let n = libcSend(fd, base + sent, total - sent, 0)
                if n <= 0 { return false }
                sent += n
            }
            return true
        }
    }

    func start(acceptKey: String) {
        // 1. 发送 HTTP 101 Handshake 响应
        let response = "HTTP/1.1 101 Switching Protocols\r\n" +
                       "Upgrade: websocket\r\n" +
                       "Connection: Upgrade\r\n" +
                       "Sec-WebSocket-Accept: \(acceptKey)\r\n\r\n"
        guard let responseData = response.data(using: .utf8),
              send(data: responseData) else {
            close()
            return
        }

        // 2. 在 MainActor 调度实时监听与历史 buffer 回放
        Task { @MainActor [weak self] in
            guard let self, self.isOpen else { return }
            if let initialText = LinuxTerminalManager.shared.bufferText(for: self.terminalId),
               !initialText.isEmpty {
                let frame = WebSocketFraming.makeFrame(opcode: .binary, payload: Data(initialText.utf8))
                _ = self.send(data: frame)
            }

            self.cancelObserver = LinuxTerminalManager.shared.observeOutput(for: self.terminalId) { [weak self] text in
                guard let self, self.isOpen else { return }
                let frame = WebSocketFraming.makeFrame(opcode: .binary, payload: Data(text.utf8))
                _ = self.send(data: frame)
            }
        }

        // 3. 启动客户端读取事件循环 (接收按键与 resize 控制帧，强引用 self 保证连接期间存活)
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            self.readLoop()
        }
    }

    private func readLoop() {
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 4096)

        while isOpen {
            let n = recv(fd, &chunk, chunk.count, 0)
            guard n > 0 else {
                break
            }
            buffer.append(contentsOf: chunk[..<n])

            while let frame = WebSocketFraming.decodeFrame(from: &buffer) {
                switch frame.opcode {
                case .binary:
                    // 原始输入直接写入 PTY masterFd
                    let data = frame.payload
                    DispatchQueue.main.async {
                        LinuxTerminalManager.shared.writeRaw(to: self.terminalId, data: data)
                    }
                case .text:
                    // 尝试解析控制指令 (如 resize)
                    if let json = try? JSONSerialization.jsonObject(with: frame.payload) as? [String: Any],
                       let type = json["type"] as? String {
                        if type == "resize" {
                            let cols = json["cols"] as? Int ?? 80
                            let rows = json["rows"] as? Int ?? 24
                            DispatchQueue.main.async {
                                LinuxTerminalManager.shared.resize(terminalId: self.terminalId, cols: cols, rows: rows)
                            }
                        }
                    } else if let text = String(data: frame.payload, encoding: .utf8) {
                        // 文本格式按字符串写给终端
                        DispatchQueue.main.async {
                            LinuxTerminalManager.shared.write(to: self.terminalId, text: text)
                        }
                    }
                case .ping:
                    let pong = WebSocketFraming.makeFrame(opcode: .pong, payload: frame.payload)
                    _ = send(data: pong)
                case .close:
                    let closeFrame = WebSocketFraming.makeFrame(opcode: .close, payload: Data())
                    _ = send(data: closeFrame)
                    close()
                    return
                default:
                    break
                }
            }
        }

        close()
    }

    func close() {
        lock.lock()
        guard isOpen else {
            lock.unlock()
            return
        }
        isOpen = false
        lock.unlock()

        let cancel = self.cancelObserver
        self.cancelObserver = nil
        if let cancel {
            Task { @MainActor in
                cancel()
            }
        }
        #if canImport(Glibc)
        _ = Glibc.close(fd)
        #elseif canImport(Darwin)
        _ = Darwin.close(fd)
        #endif

        let notifyClose = self.onClose
        self.onClose = nil
        notifyClose?(self)
    }
}
