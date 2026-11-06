import Foundation
#if canImport(Network) && os(macOS)
import Network
#endif
#if canImport(OSLog)
import OSLog
#endif
#if canImport(Darwin)
import Darwin
private let libcSend = Darwin.send
private let sockStreamType: Int32 = SOCK_STREAM
#elseif canImport(Glibc)
import Glibc
private let libcSend = Glibc.send
private let sockStreamType: Int32 = Int32(SOCK_STREAM.rawValue)
#endif

/// 本地 HTTP & WebSocket IPC 服务器
/// - 绑定 127.0.0.1 动态端口，仅接受本机连接（安全约束）
/// - 支持 Unix Domain Socket (~/.open-maestri/run/agent.sock)
/// - 路由：
///   - POST /cli -> omaestri CLI 命令路由
///   - GET /api/workspace -> 读取工作区 JSON
///   - POST /api/workspace -> 保存工作区 JSON
///   - POST /api/terminals -> 创建新终端会话
///   - DELETE /api/terminals/:id -> 删除终端会话
///   - GET /api/terminals[/:id] -> PTY 元数据 (pid/cols/rows/running/exitCode/...)
///   - 连接事件: connectionCreated / connectionRemoved (POST/DELETE /api/connections, DELETE /api/nodes)
///   - DELETE /api/nodes/:id -> 删除节点及其连接并广播 nodeRemoved
///   - WS /ws/events[?workspace=UUID] -> 节点/终端生命周期事件 (JSON 文本帧)
///   - WS /ws/pty/:id -> RFC 6455 WebSocket 双向流式 PTY 与尺寸控制
public final class InterAgentServer {
    public static let shared = InterAgentServer()
    private let logger = Logger.make(category: "InterAgentServer")

    public private(set) var port: UInt16 = 0

    // MARK: - 安全认证 Token
    public private(set) var authToken: String = ""

    private func setupAuthToken() {
        if let envToken = ProcessInfo.processInfo.environment["MAESTRI_AUTH_TOKEN"], !envToken.isEmpty {
            self.authToken = envToken
        } else {
            let runDir = PersistenceManager.shared.appDataURL
                .appendingPathComponent("run").path
            let tokenPath = "\(runDir)/auth.token"
            try? FileManager.default.createDirectory(atPath: runDir, withIntermediateDirectories: true)
            if let existing = try? String(contentsOfFile: tokenPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
               !existing.isEmpty {
                self.authToken = existing
            } else {
                let token = UUID().uuidString.replacingOccurrences(of: "-", with: "") + UUID().uuidString.replacingOccurrences(of: "-", with: "")
                self.authToken = token
                try? token.write(toFile: tokenPath, atomically: true, encoding: .utf8)
                #if canImport(Glibc)
                _ = Glibc.chmod(tokenPath, 0o600)
                #elseif canImport(Darwin)
                _ = Darwin.chmod(tokenPath, 0o600)
                #endif
            }
        }
    }

    /// 常量时间字符串比较（防时序攻击）
    static func constantTimeCompare(_ a: String, _ b: String) -> Bool {
        let aBytes = Array(a.utf8)
        let bBytes = Array(b.utf8)
        guard aBytes.count == bBytes.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<aBytes.count {
            diff |= aBytes[i] ^ bBytes[i]
        }
        return diff == 0
    }

    func validateAuth(headerMap: [String: String], path: String) -> Bool {
        guard !self.authToken.isEmpty else { return true }

        if let authHeader = headerMap["authorization"] {
            let parts = authHeader.components(separatedBy: " ")
            if parts.count == 2, parts[0].lowercased() == "bearer" {
                if Self.constantTimeCompare(parts[1], self.authToken) {
                    return true
                }
            }
        }

        if let qIdx = path.firstIndex(of: "?") {
            let query = String(path[path.index(after: qIdx)...])
            for pair in query.components(separatedBy: "&") {
                let kv = pair.components(separatedBy: "=")
                if kv.count == 2 && kv[0] == "token" {
                    let decodedToken = kv[1].removingPercentEncoding ?? kv[1]
                    if Self.constantTimeCompare(decodedToken, self.authToken) {
                        return true
                    }
                }
            }
        }

        return false
    }

    static func isAllowedHost(_ hostHeader: String?) -> Bool {
        guard let hostHeader = hostHeader, !hostHeader.isEmpty else { return true }
        let hostOnly = hostHeader.components(separatedBy: ":").first?.lowercased() ?? hostHeader.lowercased()
        return hostOnly == "127.0.0.1" || hostOnly == "localhost"
    }

    static func isAllowedOrigin(_ originHeader: String?) -> Bool {
        guard let originHeader = originHeader, !originHeader.isEmpty else { return true }
        guard let url = URL(string: originHeader),
              let host = url.host?.lowercased() else {
            return false
        }
        return host == "127.0.0.1" || host == "localhost"
    }

    // MARK: - Unix Socket 支持
    private var unixSocketFd: Int32 = -1
    private var unixSocketSource: DispatchSourceRead?

    /// 当前活跃的 Unix socket 路径（供 SwiftTermProvider / PTY 注入 MAESTRI_SOCKET）
    public private(set) var currentSocketPath: String?

    /// 全局固定 socket 路径（不再绑定 workspace UUID，避免切换工作区后旧终端 CLI 断联）
    public static var globalSocketPath: String {
        let runDir = PersistenceManager.shared.appDataURL
            .appendingPathComponent("run").path
        return "\(runDir)/agent.sock"
    }

    private init() {}

    // MARK: - 启动与停止

#if os(Linux)
    private var tcpSocketFd: Int32 = -1
    private var tcpSocketSource: DispatchSourceRead?
    private var ptySessions: [PTYWebSocketSession] = []
    private let ptySessionsLock = NSLock()

    func registerPTYSession(_ session: PTYWebSocketSession) {
        ptySessionsLock.lock()
        ptySessions.append(session)
        ptySessionsLock.unlock()

        session.onClose = { [weak self, weak session] _ in
            guard let self, let session else { return }
            self.ptySessionsLock.lock()
            self.ptySessions.removeAll { $0 === session }
            self.ptySessionsLock.unlock()
        }
    }

    /// Starts the TCP HTTP server on a dynamic port on Linux via POSIX sockets.
    public func start() throws {
        setupAuthToken()
        MainActor.assumeIsolated {
            if PlatformHooks.terminalsService == nil {
                PlatformHooks.terminalsService = LinuxTerminalManager.shared
            }
        }
        PlatformHooks.serverPort = { [weak self] in self?.port ?? 0 }

        try startTCPServer()
        startUnixSocketIfNeeded()
        logger.debug("InterAgentServer started on \(Constants.interAgentServerHost):\(self.port)")
    }

    private func startTCPServer() throws {
        let fd = socket(AF_INET, sockStreamType, 0)
        guard fd >= 0 else {
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "socket(AF_INET) failed: \(String(cString: strerror(errno)))"])
        }
        var opt: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(0) // dynamic port assigned by kernel
        addr.sin_addr.s_addr = inet_addr(Constants.interAgentServerHost)

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "bind(AF_INET) failed: \(String(cString: strerror(errno)))"])
        }

        var actualAddr = sockaddr_in()
        var actualLen = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &actualAddr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(fd, $0, &actualLen)
            }
        }
        guard nameResult == 0 else {
            close(fd)
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "getsockname failed"])
        }
        self.port = UInt16(bigEndian: actualAddr.sin_port)

        guard listen(fd, 128) == 0 else {
            close(fd)
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "listen(AF_INET) failed"])
        }

        let flags = fcntl(fd, F_GETFL, 0)
        _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)

        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: .global(qos: .userInitiated))
        source.setEventHandler { [weak self] in
            self?.acceptTCPConnection(serverFd: fd)
        }
        source.resume()
        tcpSocketSource = source
        tcpSocketFd = fd
        logger.info("InterAgentServer POSIX TCP ready on \(Constants.interAgentServerHost):\(self.port)")
    }

    private func acceptTCPConnection(serverFd: Int32) {
        while true {
            var clientAddr = sockaddr_in()
            var clientLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            let clientFd = withUnsafeMutablePointer(to: &clientAddr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    accept(serverFd, $0, &clientLen)
                }
            }
            guard clientFd >= 0 else { break }
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.handleClient(fd: clientFd, isUnix: false)
            }
        }
    }
#else
    private var listener: NWListener?

    /// Starts the TCP HTTP server on a dynamic port via Network.framework (macOS).
    public func start() throws {
        setupAuthToken()
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredLocalEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host(Constants.interAgentServerHost),
            port: .any
        )

        listener = try NWListener(using: params)

        let semaphore = DispatchSemaphore(value: 0)
        var startError: Error? = nil

        listener?.stateUpdateHandler = { [weak self] state in
            self?.handleStateChange(state)
            switch state {
            case .ready, .failed:
                semaphore.signal()
            default:
                break
            }
        }
        listener?.newConnectionHandler = { [weak self] connection in
            self?.handleConnection(connection)
        }
        listener?.start(queue: .global(qos: .userInitiated))

        let result = semaphore.wait(timeout: .now() + 2.0)
        if result == .timedOut {
            logger.warning("InterAgentServer: timed out waiting for port assignment")
        }
        _ = startError
        startUnixSocketIfNeeded()
        logger.debug("InterAgentServer starting on \(Constants.interAgentServerHost):\(self.port)")
    }

    private func handleStateChange(_ state: NWListener.State) {
        switch state {
        case .ready:
            port = listener?.port?.rawValue ?? 0
            restartCount = 0
            logger.info("InterAgentServer ready on port \(self.port)")
        case .failed(let error):
            logger.error("InterAgentServer failed: \(error)")
            scheduleRestart()
        default:
            break
        }
    }

    private var restartCount = 0
    private let maxRestarts = 5

    private func scheduleRestart() {
        guard restartCount < maxRestarts else {
            logger.error("InterAgentServer: max restart attempts (\(self.maxRestarts)) reached, giving up")
            return
        }
        let delay = Constants.serverRestartDelay * pow(2.0, Double(restartCount))
        restartCount += 1
        logger.warning("InterAgentServer restarting in \(Int(delay))s (attempt \(self.restartCount)/\(self.maxRestarts))")
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
            try? self?.start()
        }
    }

    private func handleConnection(_ connection: NWConnection) {
        connection.start(queue: .global(qos: .userInitiated))
        receiveRequest(on: connection)
    }

    private func receiveRequest(on connection: NWConnection) {
        receiveData(on: connection, accumulated: Data())
    }

    private func receiveData(on connection: NWConnection, accumulated: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 262144) { [weak self] data, _, isComplete, error in
            guard error == nil else { connection.cancel(); return }
            let total = accumulated + (data ?? Data())
            if let headerEnd = total.range(of: Data("\r\n\r\n".utf8)) {
                let headerData = total[..<headerEnd.upperBound]
                let headerStr = String(decoding: headerData, as: UTF8.self)
                let bodyStart = headerEnd.upperBound
                let contentLength = Self.parseContentLength(from: headerStr)
                let bodyReceived = total.count - bodyStart
                if contentLength <= 0 || bodyReceived >= contentLength {
                    Task { [weak self] in
                        guard let self else { connection.cancel(); return }
                        let bodyData = total.subdata(in: bodyStart..<total.count)
                        let headerLines = headerStr.components(separatedBy: "\r\n")
                        var headerMap: [String: String] = [:]
                        for line in headerLines.dropFirst() {
                            if let cIdx = line.firstIndex(of: ":") {
                                let k = line[..<cIdx].trimmingCharacters(in: .whitespaces).lowercased()
                                let v = line[line.index(after: cIdx)...].trimmingCharacters(in: .whitespaces)
                                headerMap[k] = v
                            }
                        }
                        let reqLine = headerLines.first ?? ""
                        let parts = reqLine.components(separatedBy: " ")
                        let method = parts.count > 0 ? parts[0].uppercased() : "GET"
                        let path = parts.count > 1 ? parts[1] : "/"

                        let responseData = await self.routeHTTPRequest(method: method, path: path, headerLines: headerLines, headerMap: headerMap, body: bodyData)
                        connection.send(content: responseData, completion: .idempotent)
                        connection.cancel()
                    }
                    return
                }
            }
            if !isComplete {
                self?.receiveData(on: connection, accumulated: total)
            } else {
                Task { [weak self] in
                    guard let self else { connection.cancel(); return }
                    let responseData = Self.buildHTTPResponse(status: 400, body: "error: truncated request")
                    connection.send(content: responseData, completion: .idempotent)
                    connection.cancel()
                }
            }
        }
    }
#endif

    /// Gracefully shuts down listeners and sockets.
    public func stop() {
#if os(Linux)
        tcpSocketSource?.cancel()
        tcpSocketSource = nil
        if tcpSocketFd >= 0 {
            close(tcpSocketFd)
            tcpSocketFd = -1
        }
        ptySessionsLock.lock()
        let sessionsToClose = ptySessions
        ptySessions.removeAll()
        ptySessionsLock.unlock()
        for session in sessionsToClose {
            session.close()
        }
        EventBroadcaster.shared.closeAll()
#else
        listener?.cancel()
        listener = nil
#endif
        stopUnixSocket()
        port = 0
        logger.debug("InterAgentServer stopped")
    }

    // MARK: - Unix Socket 生命周期

    /// 启动全局 Unix socket（应用生命周期内只调用一次）
    func startUnixSocketIfNeeded() {
        guard unixSocketFd < 0 else { return }
        do {
            try startUnixSocket()
        } catch {
            logger.error("InterAgentServer: Unix socket start failed: \(error)")
        }
    }

    private func startUnixSocket() throws {
        let path = Self.globalSocketPath
        currentSocketPath = path

        let runDir = (path as NSString).deletingLastPathComponent
        try FileManager.default.createDirectory(
            atPath: runDir,
            withIntermediateDirectories: true,
            attributes: nil
        )

        unlink(path)

        let fd = socket(AF_UNIX, sockStreamType, 0)
        guard fd >= 0 else {
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "socket() failed: \(String(cString: strerror(errno)))"])
        }
        unixSocketFd = fd

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = path.utf8.prefix(103)
        withUnsafeMutableBytes(of: &addr.sun_path) { ptr in
            pathBytes.enumerated().forEach { ptr[$0.offset] = $0.element }
        }
        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "bind() failed: \(String(cString: strerror(errno)))"])
        }

        guard listen(fd, 10) == 0 else {
            close(fd)
            throw NSError(domain: "InterAgentServer", code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "listen() failed"])
        }

        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: .global(qos: .userInitiated))
        source.setEventHandler { [weak self] in
            self?.acceptUnixConnection(serverFd: fd)
        }
        source.resume()
        unixSocketSource = source
        logger.info("InterAgentServer Unix socket ready at \(path)")
    }

    private func stopUnixSocket() {
        unixSocketSource?.cancel()
        unixSocketSource = nil
        if unixSocketFd >= 0 {
            close(unixSocketFd)
            unixSocketFd = -1
        }
        if let path = currentSocketPath {
            unlink(path)
        }
        currentSocketPath = nil
    }

    private func acceptUnixConnection(serverFd: Int32) {
        while true {
            let clientFd = accept(serverFd, nil, nil)
            guard clientFd >= 0 else { break }
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.handleClient(fd: clientFd, isUnix: true)
            }
        }
    }

    // MARK: - 客户端请求分发 (HTTP REST, CLI & WebSocket Upgrade)

    private func handleClient(fd: Int32, isUnix: Bool = false) {
        var accumulated = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)

        while true {
            let n = recv(fd, &buffer, buffer.count, 0)
            guard n > 0 else { break }
            accumulated.append(contentsOf: buffer[..<n])

            if let headerEnd = accumulated.range(of: Data("\r\n\r\n".utf8)) {
                let headerData = accumulated[..<headerEnd.upperBound]
                let headerStr = String(decoding: headerData, as: UTF8.self)
                let headerLines = headerStr.components(separatedBy: "\r\n")

                var headerMap: [String: String] = [:]
                for line in headerLines.dropFirst() {
                    if let cIdx = line.firstIndex(of: ":") {
                        let k = line[..<cIdx].trimmingCharacters(in: .whitespaces).lowercased()
                        let v = line[line.index(after: cIdx)...].trimmingCharacters(in: .whitespaces)
                        headerMap[k] = v
                    }
                }

                let reqLine = headerLines.first ?? ""
                let parts = reqLine.components(separatedBy: " ")
                let method = parts.count > 0 ? parts[0].uppercased() : "GET"
                let path = parts.count > 1 ? parts[1] : "/"

                let origin = headerMap["origin"]
                let allowedOrigin: String? = {
                    if let o = origin, Self.isAllowedOrigin(o) {
                        return o
                    }
                    return nil
                }()

                // 安全校验 (仅在 TCP 连接上执行，Unix socket 0600 本地信任)
                if !isUnix {
                    if let host = headerMap["host"], !Self.isAllowedHost(host) {
                        let resp = Self.buildHTTPResponse(status: 403, body: "error: forbidden host\n", allowedOrigin: allowedOrigin)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }

                    if let origin, !Self.isAllowedOrigin(origin) {
                        let resp = Self.buildHTTPResponse(status: 403, body: "error: forbidden origin\n", allowedOrigin: nil)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }

                    if method == "OPTIONS" {
                        let resp = Self.buildHTTPResponse(status: 204, body: "", allowedOrigin: allowedOrigin)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }

                    guard self.validateAuth(headerMap: headerMap, path: path) else {
                        let resp = Self.buildHTTPResponse(status: 401, body: "error: unauthorized\n", allowedOrigin: allowedOrigin)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }
                }

                // 检测 WebSocket Upgrade (/ws/pty/:id)
                let isUpgrade = (headerMap["upgrade"]?.lowercased() == "websocket") ||
                                (headerMap["connection"]?.lowercased().contains("upgrade") == true)

                if isUpgrade && (path == "/ws/events" || path.hasPrefix("/ws/events?")) {
                    guard let clientKey = headerMap["sec-websocket-key"] else {
                        let resp = Self.buildHTTPResponse(status: 400, body: "error: missing Sec-WebSocket-Key", allowedOrigin: allowedOrigin)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }
                    var filter: UUID? = nil
                    if let q = path.components(separatedBy: "?").dropFirst().first {
                        for item in q.components(separatedBy: "&") {
                            let kv = item.components(separatedBy: "=")
                            if kv.count == 2, kv[0] == "workspace" { filter = UUID(uuidString: kv[1]) }
                        }
                    }
                    let session = EventsWebSocketSession(fd: fd, workspaceFilter: filter)
                    EventBroadcaster.shared.register(session)
                    session.start(acceptKey: WebSocketHandshake.acceptKey(for: clientKey))
                    return
                }

                if isUpgrade && path.hasPrefix("/ws/pty/") {
                    let cleanPtyPath = path.components(separatedBy: "?").first ?? path
                    let rawId = String(cleanPtyPath.dropFirst("/ws/pty/".count))
                    guard let terminalId = UUID(uuidString: rawId) else {
                        let resp = Self.buildHTTPResponse(status: 400, body: "error: invalid terminal ID", allowedOrigin: allowedOrigin)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }
                    guard let clientKey = headerMap["sec-websocket-key"] else {
                        let resp = Self.buildHTTPResponse(status: 400, body: "error: missing Sec-WebSocket-Key", allowedOrigin: allowedOrigin)
                        _ = resp.withUnsafeBytes { libcSend(fd, $0.baseAddress, $0.count, 0) }
                        close(fd)
                        return
                    }

                    let acceptKey = WebSocketHandshake.acceptKey(for: clientKey)
                    let session = PTYWebSocketSession(fd: fd, terminalId: terminalId)
                    self.registerPTYSession(session)
                    session.start(acceptKey: acceptKey)
                    // session 接管 fd 的生命周期，不在此处 close
                    return
                }

                // 普通 HTTP 路由（Content-Length 判断完整性）
                let contentLength = Self.parseContentLength(from: headerStr)
                let bodyReceived = accumulated.count - headerEnd.upperBound
                if contentLength <= 0 || bodyReceived >= contentLength {
                    Task { [weak self, fd] in
                        guard let self else { close(fd); return }
                        let bodyData = accumulated.subdata(in: headerEnd.upperBound..<accumulated.count)
                        let responseData = await self.routeHTTPRequest(
                            method: method,
                            path: path,
                            headerLines: headerLines,
                            headerMap: headerMap,
                            body: bodyData,
                            allowedOrigin: allowedOrigin
                        )
                        responseData.withUnsafeBytes { raw in
                            guard let base = raw.baseAddress else { return }
                            _ = libcSend(fd, base, raw.count, 0)
                        }
                        close(fd)
                    }
                    return
                }
            }
        }

        close(fd)
    }

    private func routeHTTPRequest(
        method: String,
        path: String,
        headerLines: [String],
        headerMap: [String: String],
        body: Data,
        allowedOrigin: String? = nil
    ) async -> Data {
        let pathParts = path.components(separatedBy: "?")
        let cleanPath = pathParts.first ?? path

        // 0. OPTIONS (CORS Preflight)
        if method == "OPTIONS" {
            return Self.buildHTTPResponse(status: 204, body: "")
        }

        // 1. POST /cli (omaestri CLI)
        if method == "POST" && cleanPath == "/cli" {
            var terminalId: UUID?
            var args: [String] = []
            var httpVersion = "1.1"

            if let firstLine = headerLines.first, firstLine.contains("HTTP/1.0") {
                httpVersion = "1.0"
            }

            if let tidStr = headerMap["x-terminal-id"] {
                terminalId = UUID(uuidString: tidStr)
            }

            if let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
               let rawArgs = json["args"] as? [String] {
                args = rawArgs
            }

            guard !args.isEmpty else {
                return Self.buildHTTPResponse(status: 400, body: "error: missing args", httpVersion: httpVersion)
            }
            let responseBody = await CLIRouter.shared.routeAsync(args: args, terminalId: terminalId)
            return Self.buildHTTPResponse(status: 200, body: responseBody, httpVersion: httpVersion)
        }

        // 2. GET /api/workspace
        if method == "GET" && cleanPath == "/api/workspace" {
            var targetId: UUID? = nil
            if let qIdx = path.firstIndex(of: "?") {
                let query = String(path[path.index(after: qIdx)...])
                for item in query.components(separatedBy: "&") {
                    let kv = item.components(separatedBy: "=")
                    if kv.count == 2, kv[0] == "id" {
                        targetId = UUID(uuidString: kv[1])
                    }
                }
            }
            let pm = PersistenceManager.shared
            let wsId = targetId ?? (try? pm.loadAppState())?.activeWorkspaceId ?? (try? pm.loadManifest())?.workspaces.first?.id
            if let wsId, let doc = try? pm.loadWorkspace(id: wsId) {
                if let jsonData = try? pm.encoder.encode(doc) {
                    return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: jsonData)
                }
            } else {
                let defaultPayload = WorkspacePayload(name: "Default", workingDirectory: FileManager.default.homeDirectoryForCurrentUser.path)
                let defaultDoc = WorkspaceDocument(payload: defaultPayload)
                try? pm.ensureWorkspaceDirectoryExists(id: defaultPayload.id)
                try? pm.saveSync(defaultDoc, to: pm.workspaceURL(id: defaultPayload.id))
                var appState = (try? pm.loadAppState()) ?? AppStateData()
                appState.activeWorkspaceId = defaultPayload.id
                try? pm.saveAppState(appState)
                if let jsonData = try? pm.encoder.encode(defaultDoc) {
                    return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: jsonData)
                }
            }
            return Self.buildHTTPResponse(status: 404, body: "error: workspace not found")
        }

        // 3. POST /api/workspace (保存工作区)
        if method == "POST" && cleanPath == "/api/workspace" {
            let pm = PersistenceManager.shared
            var decodeData = body
            var hasExplicitFloors = false
            var hasExplicitCrossFloor = false
            if var rawJson = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
               var rawPayload = rawJson["payload"] as? [String: Any] {
                hasExplicitFloors = rawPayload["floors"] != nil
                hasExplicitCrossFloor = rawPayload["crossFloorConnections"] != nil
                if !hasExplicitFloors { rawPayload["floors"] = [] }
                if !hasExplicitCrossFloor { rawPayload["crossFloorConnections"] = [] }
                rawJson["payload"] = rawPayload
                if let patched = try? JSONSerialization.data(withJSONObject: rawJson) {
                    decodeData = patched
                }
            }
            guard var doc = try? pm.decoder.decode(WorkspaceDocument.self, from: decodeData) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid WorkspaceDocument JSON", allowedOrigin: allowedOrigin)
            }
            guard doc.schemaVersion == Constants.schemaVersion else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid schemaVersion \(doc.schemaVersion), expected \(Constants.schemaVersion)", allowedOrigin: allowedOrigin)
            }
            // R1 保护：若客户端快照未携带 floors 或为空，保留服务端通过 /api/floors 维护的 floors/crossFloorConnections
            if let existing = try? pm.loadWorkspace(id: doc.payload.id) {
                if !hasExplicitFloors || (doc.payload.floors.isEmpty && !existing.payload.floors.isEmpty) {
                    doc.payload.floors = existing.payload.floors
                }
                if !hasExplicitCrossFloor || (doc.payload.crossFloorConnections.isEmpty && !existing.payload.crossFloorConnections.isEmpty) {
                    doc.payload.crossFloorConnections = existing.payload.crossFloorConnections
                }
            }
            do {
                try pm.ensureWorkspaceDirectoryExists(id: doc.payload.id)
                try pm.saveSync(doc, to: pm.workspaceURL(id: doc.payload.id))
                EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: doc.payload.id, payload: [:])
                let respJson = "{\"status\":\"ok\",\"id\":\"\(doc.payload.id.uuidString)\"}"
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: respJson, allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save workspace: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // 4. POST /api/terminals (创建终端)
        if method == "POST" && cleanPath == "/api/terminals" {
            var command = ""
            var workingDirectory = ""
            var workspaceId: UUID? = nil
            var displayName: String? = nil
            var roleName: String? = nil
            var requestedId: UUID? = nil

            if let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                command = json["command"] as? String ?? ""
                workingDirectory = json["workingDirectory"] as? String ?? ""
                if let wsStr = json["workspaceId"] as? String {
                    workspaceId = UUID(uuidString: wsStr)
                }
                if let idStr = json["id"] as? String {
                    requestedId = UUID(uuidString: idStr)
                }
                displayName = json["displayName"] as? String
                roleName = json["roleName"] as? String
            }

            let newId = requestedId ?? UUID()
            Task { @MainActor in
                let tm = PlatformHooks.terminals
                tm.createTerminal(
                    id: newId,
                    command: command,
                    workingDirectory: workingDirectory,
                    workspaceId: workspaceId,
                    roleName: roleName,
                    displayName: displayName,
                    agentType: "generic_shell"
                )
            }
            let resp = "{\"id\":\"\(newId.uuidString)\"}"
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: resp)
        }

        // 4b. GET /api/terminals (PTY 元数据列表) 与 GET /api/terminals/:id
        if method == "GET" && (cleanPath == "/api/terminals" || cleanPath.hasPrefix("/api/terminals/")) {
            let rawId = cleanPath == "/api/terminals" ? "" : String(cleanPath.dropFirst("/api/terminals/".count))
            if rawId.isEmpty {
                let list: [[String: Any]] = await MainActor.run { LinuxTerminalManager.shared.allMetadata() }
                if let data = try? JSONSerialization.data(withJSONObject: list, options: [.sortedKeys]) {
                    return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
                }
                return Self.buildHTTPResponse(status: 500, body: "error: encode failed", allowedOrigin: allowedOrigin)
            }
            guard let tid = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid terminal ID", allowedOrigin: allowedOrigin)
            }
            let meta: [String: Any]? = await MainActor.run { LinuxTerminalManager.shared.metadata(for: tid) }
            guard let meta, let data = try? JSONSerialization.data(withJSONObject: meta, options: [.sortedKeys]) else {
                return Self.buildHTTPResponse(status: 404, body: "error: terminal not found", allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
        }

        // 5. DELETE /api/terminals/:id
        if method == "DELETE" && cleanPath.hasPrefix("/api/terminals/") {
            let rawId = String(cleanPath.dropFirst("/api/terminals/".count))
            guard let tid = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid terminal ID")
            }
            Task { @MainActor in
                let tm = PlatformHooks.terminals
                tm.removeTerminal(id: tid)
            }
            let resp = "{\"status\":\"ok\"}"
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: resp)
        }

        // 6. GET /api/notes/:name
        if method == "GET" && cleanPath.hasPrefix("/api/notes/") {
            let rawName = String(cleanPath.dropFirst("/api/notes/".count))
            var noteName = rawName.removingPercentEncoding ?? rawName
            if noteName.lowercased().hasSuffix(".md") {
                noteName = String(noteName.dropLast(3))
            }
            guard !noteName.isEmpty,
                  !noteName.contains("/"),
                  !noteName.contains(".."),
                  !noteName.contains("\\") else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid note name or path traversal detected", allowedOrigin: allowedOrigin)
            }

            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }

            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }

            guard let wsId = targetWorkspaceId else {
                return Self.buildHTTPResponse(status: 400, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            let notePath = NoteFileManager.shared.managedPath(workspaceId: wsId, noteName: noteName)
            do {
                let content = try NoteFileManager.shared.read(filePath: notePath)
                return Self.buildHTTPResponse(status: 200, contentType: "text/markdown; charset=utf-8", body: content, allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 404, body: "error: note not found: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // 7. PUT /api/notes/:name
        if method == "PUT" && cleanPath.hasPrefix("/api/notes/") {
            let rawName = String(cleanPath.dropFirst("/api/notes/".count))
            var noteName = rawName.removingPercentEncoding ?? rawName
            if noteName.lowercased().hasSuffix(".md") {
                noteName = String(noteName.dropLast(3))
            }
            guard !noteName.isEmpty,
                  !noteName.contains("/"),
                  !noteName.contains(".."),
                  !noteName.contains("\\") else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid note name or path traversal detected", allowedOrigin: allowedOrigin)
            }

            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }

            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }

            guard let wsId = targetWorkspaceId else {
                return Self.buildHTTPResponse(status: 400, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            let notePath = NoteFileManager.shared.managedPath(workspaceId: wsId, noteName: noteName)
            guard let content = String(data: body, encoding: .utf8) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid UTF-8 body", allowedOrigin: allowedOrigin)
            }

            do {
                try NoteFileManager.shared.write(filePath: notePath, content: content)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to write note: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // 8. DELETE /api/notes/:name
        if method == "DELETE" && cleanPath.hasPrefix("/api/notes/") {
            let rawName = String(cleanPath.dropFirst("/api/notes/".count))
            var noteName = rawName.removingPercentEncoding ?? rawName
            if noteName.lowercased().hasSuffix(".md") {
                noteName = String(noteName.dropLast(3))
            }
            guard !noteName.isEmpty,
                  !noteName.contains("/"),
                  !noteName.contains(".."),
                  !noteName.contains("\\") else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid note name or path traversal detected", allowedOrigin: allowedOrigin)
            }

            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }

            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }

            guard let wsId = targetWorkspaceId else {
                return Self.buildHTTPResponse(status: 400, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            let notePath = NoteFileManager.shared.managedPath(workspaceId: wsId, noteName: noteName)
            do {
                try NoteFileManager.shared.delete(filePath: notePath)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to delete note: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // 9. GET /api/connections
        if method == "GET" && cleanPath == "/api/connections" {
            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }

            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }

            guard let wsId = targetWorkspaceId else {
                return Self.buildHTTPResponse(status: 400, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            guard let doc = try? pm.loadWorkspace(id: wsId) else {
                return Self.buildHTTPResponse(status: 404, body: "error: workspace not found", allowedOrigin: allowedOrigin)
            }

            var connList: [[String: Any]] = []
            for c in doc.payload.connections {
                connList.append([
                    "id": c.id.uuidString,
                    "a": c.terminalIdA.uuidString,
                    "b": c.terminalIdB.uuidString,
                    "type": "terminalToTerminal",
                    "ropePoints": c.ropePoints
                ])
            }
            for c in doc.payload.noteConnections {
                connList.append([
                    "id": c.id.uuidString,
                    "a": c.terminalId.uuidString,
                    "b": c.noteNodeId.uuidString,
                    "type": "terminalToNote",
                    "ropePoints": c.ropePoints
                ])
            }
            for c in doc.payload.portalConnections {
                connList.append([
                    "id": c.id.uuidString,
                    "a": c.terminalId.uuidString,
                    "b": c.portalNodeId.uuidString,
                    "type": "terminalToPortal",
                    "ropePoints": c.ropePoints
                ])
            }
            for c in doc.payload.portalToPortalConnections {
                connList.append([
                    "id": c.id.uuidString,
                    "a": c.portalIdA.uuidString,
                    "b": c.portalIdB.uuidString,
                    "type": "portalToPortal",
                    "ropePoints": c.ropePoints
                ])
            }
            for c in doc.payload.noteToNoteConnections {
                connList.append([
                    "id": c.id.uuidString,
                    "a": c.noteNodeIdA.uuidString,
                    "b": c.noteNodeIdB.uuidString,
                    "type": "noteToNote",
                    "ropePoints": c.ropePoints
                ])
            }

            guard let jsonData = try? JSONSerialization.data(withJSONObject: connList, options: [.prettyPrinted]) else {
                return Self.buildHTTPResponse(status: 500, body: "error: serialization failed", allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: jsonData, allowedOrigin: allowedOrigin)
        }

        // 10. POST /api/connections
        if method == "POST" && cleanPath == "/api/connections" {
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid JSON body", allowedOrigin: allowedOrigin)
            }

            guard let aStr = (json["a"] as? String) ?? (json["terminalIdA"] as? String),
                  let idA = UUID(uuidString: aStr),
                  let bStr = (json["b"] as? String) ?? (json["terminalIdB"] as? String),
                  let idB = UUID(uuidString: bStr) else {
                return Self.buildHTTPResponse(status: 400, body: "error: missing or invalid node IDs 'a' and 'b'", allowedOrigin: allowedOrigin)
            }

            let rawPoints = (json["ropePoints"] as? [[Double]]) ?? []
            let ropePoints: [[Double]] = {
                if rawPoints.count == Constants.ropeControlPointCount { return rawPoints }
                if rawPoints.count == 2 {
                    let p0 = rawPoints[0]
                    let p1 = rawPoints[1]
                    if p0.count == 2 && p1.count == 2 {
                        return (0..<Constants.ropeControlPointCount).map { i in
                            let t = Double(i) / Double(Constants.ropeControlPointCount - 1)
                            return [p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t]
                        }
                    }
                }
                return Array(repeating: [0.0, 0.0], count: Constants.ropeControlPointCount)
            }()

            let connId = (json["id"] as? String).flatMap(UUID.init) ?? UUID()
            let rawKind = (json["kind"] as? String) ?? (json["type"] as? String)

            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }

            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }

            guard let wsId = targetWorkspaceId else {
                return Self.buildHTTPResponse(status: 400, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            let fileURL = pm.workspaceURL(id: wsId)
            guard var doc = try? pm.loadWorkspace(id: wsId) else {
                return Self.buildHTTPResponse(status: 404, body: "error: workspace not found", allowedOrigin: allowedOrigin)
            }

            // 检查节点类型
            let nodeA = doc.payload.nodes.first { $0.id == idA }
            let nodeB = doc.payload.nodes.first { $0.id == idB }

            let isNoteA: Bool
            let isNoteB: Bool
            let isPortalA: Bool
            let isPortalB: Bool

            if let nA = nodeA {
                switch nA.content {
                case .stickyNote: isNoteA = true; isPortalA = false
                case .portal:     isPortalA = true; isNoteA = false
                default:          isNoteA = false; isPortalA = false
                }
            } else {
                isNoteA = false; isPortalA = false
            }

            if let nB = nodeB {
                switch nB.content {
                case .stickyNote: isNoteB = true; isPortalB = false
                case .portal:     isPortalB = true; isNoteB = false
                default:          isNoteB = false; isPortalB = false
                }
            } else {
                isNoteB = false; isPortalB = false
            }

            let resolvedKind: String
            if let rawKind, !rawKind.isEmpty {
                resolvedKind = rawKind
            } else if isNoteA && isNoteB {
                resolvedKind = "noteToNote"
            } else if isNoteA || isNoteB {
                resolvedKind = "terminalToNote"
            } else if isPortalA && isPortalB {
                resolvedKind = "portalToPortal"
            } else if isPortalA || isPortalB {
                resolvedKind = "terminalToPortal"
            } else {
                resolvedKind = "terminalToTerminal"
            }

            Task { @MainActor in
                let cm = ConnectionManager.shared
                switch resolvedKind {
                case "noteToNote":
                    _ = cm.connectNoteToNote(noteNodeIdA: idA, noteNodeIdB: idB, ropePoints: ropePoints)
                case "terminalToNote":
                    let termId = (isNoteA && !isNoteB) ? idB : idA
                    let noteId = (isNoteA && !isNoteB) ? idA : idB
                    _ = cm.connectTerminalToNote(terminalId: termId, noteNodeId: noteId, ropePoints: ropePoints)
                case "terminalToPortal":
                    let termId = (isPortalA && !isPortalB) ? idB : idA
                    let portalId = (isPortalA && !isPortalB) ? idA : idB
                    _ = cm.connectTerminalToPortal(terminalId: termId, portalNodeId: portalId, ropePoints: ropePoints)
                case "portalToPortal":
                    _ = cm.connectPortalToPortal(portalIdA: idA, portalIdB: idB, ropePoints: ropePoints)
                default:
                    _ = cm.connectTerminals(idA: idA, idB: idB, serverPort: PlatformHooks.serverPort(), ropePoints: ropePoints)
                }
            }

            switch resolvedKind {
            case "noteToNote":
                let conn = NoteToNoteConnection(id: connId, noteNodeIdA: idA, noteNodeIdB: idB, ropePoints: ropePoints)
                doc.payload.noteToNoteConnections.append(conn)
            case "terminalToNote":
                let termId = (isNoteA && !isNoteB) ? idB : idA
                let noteId = (isNoteA && !isNoteB) ? idA : idB
                let conn = NoteConnection(id: connId, terminalId: termId, noteNodeId: noteId, ropePoints: ropePoints)
                doc.payload.noteConnections.append(conn)
            case "terminalToPortal":
                let termId = (isPortalA && !isPortalB) ? idB : idA
                let portalId = (isPortalA && !isPortalB) ? idA : idB
                let conn = PortalConnection(id: connId, terminalId: termId, portalNodeId: portalId, ropePoints: ropePoints)
                doc.payload.portalConnections.append(conn)
            case "portalToPortal":
                let conn = PortalToPortalConnection(id: connId, portalIdA: idA, portalIdB: idB, ropePoints: ropePoints)
                doc.payload.portalToPortalConnections.append(conn)
            default:
                let conn = TerminalConnection(id: connId, terminalIdA: idA, terminalIdB: idB, ropePoints: ropePoints)
                doc.payload.connections.append(conn)
            }

            doc.payload.lastModifiedAt = Date()
            do {
                try pm.saveSync(doc, to: fileURL)
                EventBroadcaster.shared.publish(
                    type: "connectionCreated",
                    workspaceId: wsId,
                    payload: [
                        "connectionId": connId.uuidString,
                        "kind": resolvedKind,
                        "a": idA.uuidString,
                        "b": idB.uuidString,
                        "ropePoints": ropePoints,
                    ]
                )
                EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: wsId, payload: [:])
                let resp = "{\"id\":\"\(connId.uuidString)\",\"status\":\"ok\"}"
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: resp, allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save connection to workspace: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // 11. DELETE /api/connections/:id
        if method == "DELETE" && cleanPath.hasPrefix("/api/connections/") {
            let rawId = String(cleanPath.dropFirst("/api/connections/".count))
            guard let connId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid connection ID", allowedOrigin: allowedOrigin)
            }

            Task { @MainActor in
                ConnectionManager.shared.disconnect(id: connId)
            }

            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }

            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }

            if let wsId = targetWorkspaceId {
                let fileURL = pm.workspaceURL(id: wsId)
                if var doc = try? pm.loadWorkspace(id: wsId) {
                    // 删除前记录连接种类与端点，用于广播 connectionRemoved
                    var removedInfo: [String: Any]? = nil
                    if let c = doc.payload.connections.first(where: { $0.id == connId }) {
                        removedInfo = ["kind": "terminalToTerminal", "a": c.terminalIdA.uuidString, "b": c.terminalIdB.uuidString]
                    } else if let c = doc.payload.noteConnections.first(where: { $0.id == connId }) {
                        removedInfo = ["kind": "terminalToNote", "a": c.terminalId.uuidString, "b": c.noteNodeId.uuidString]
                    } else if let c = doc.payload.portalConnections.first(where: { $0.id == connId }) {
                        removedInfo = ["kind": "terminalToPortal", "a": c.terminalId.uuidString, "b": c.portalNodeId.uuidString]
                    } else if let c = doc.payload.portalToPortalConnections.first(where: { $0.id == connId }) {
                        removedInfo = ["kind": "portalToPortal", "a": c.portalIdA.uuidString, "b": c.portalIdB.uuidString]
                    } else if let c = doc.payload.noteToNoteConnections.first(where: { $0.id == connId }) {
                        removedInfo = ["kind": "noteToNote", "a": c.noteNodeIdA.uuidString, "b": c.noteNodeIdB.uuidString]
                    }
                    doc.payload.connections.removeAll { $0.id == connId }
                    doc.payload.noteConnections.removeAll { $0.id == connId }
                    doc.payload.portalConnections.removeAll { $0.id == connId }
                    doc.payload.portalToPortalConnections.removeAll { $0.id == connId }
                    doc.payload.noteToNoteConnections.removeAll { $0.id == connId }
                    doc.payload.lastModifiedAt = Date()
                    try? pm.saveSync(doc, to: fileURL)
                    if var info = removedInfo {
                        info["connectionId"] = connId.uuidString
                        EventBroadcaster.shared.publish(type: "connectionRemoved", workspaceId: wsId, payload: info)
                    }
                    EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: wsId, payload: [:])
                }
            }

            let resp = "{\"status\":\"ok\"}"
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: resp, allowedOrigin: allowedOrigin)
        }

        // PUT /api/connections/:id  body: {"ropePoints": [[x,y],...]} -> 广播 connectionChanged
        if method == "PUT" && cleanPath.hasPrefix("/api/connections/") {
            let rawId = String(cleanPath.dropFirst("/api/connections/".count))
            guard let connId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid connection ID", allowedOrigin: allowedOrigin)
            }
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
                  let points = json["ropePoints"] as? [[Double]] else {
                return Self.buildHTTPResponse(status: 400, body: "error: missing ropePoints", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            guard let wsId = Self.resolveWorkspaceId(pathParts: pathParts, pm: pm),
                  var doc = try? pm.loadWorkspace(id: wsId) else {
                return Self.buildHTTPResponse(status: 404, body: "error: workspace not found", allowedOrigin: allowedOrigin)
            }
            var kind: String? = nil
            if let i = doc.payload.connections.firstIndex(where: { $0.id == connId }) {
                doc.payload.connections[i].ropePoints = points; kind = "terminalToTerminal"
            } else if let i = doc.payload.noteConnections.firstIndex(where: { $0.id == connId }) {
                doc.payload.noteConnections[i].ropePoints = points; kind = "terminalToNote"
            } else if let i = doc.payload.portalConnections.firstIndex(where: { $0.id == connId }) {
                doc.payload.portalConnections[i].ropePoints = points; kind = "terminalToPortal"
            } else if let i = doc.payload.portalToPortalConnections.firstIndex(where: { $0.id == connId }) {
                doc.payload.portalToPortalConnections[i].ropePoints = points; kind = "portalToPortal"
            } else if let i = doc.payload.noteToNoteConnections.firstIndex(where: { $0.id == connId }) {
                doc.payload.noteToNoteConnections[i].ropePoints = points; kind = "noteToNote"
            }
            guard let resolvedKind = kind else {
                return Self.buildHTTPResponse(status: 404, body: "error: connection not found", allowedOrigin: allowedOrigin)
            }
            doc.payload.lastModifiedAt = Date()
            do {
                try pm.saveSync(doc, to: pm.workspaceURL(id: wsId))
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save connection: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
            EventBroadcaster.shared.publish(
                type: "connectionChanged", workspaceId: wsId,
                payload: ["connectionId": connId.uuidString, "kind": resolvedKind, "ropePoints": points]
            )
            EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: wsId, payload: [:])
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
        }

        // 12. GET /api/sidebar-layout
        if method == "GET" && cleanPath == "/api/sidebar-layout" {
            let pm = PersistenceManager.shared
            let layout = (try? pm.loadSidebarLayout()) ?? SidebarLayout()
            if let data = try? pm.encoder.encode(layout) {
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"schemaVersion\":2,\"topLevelItems\":[],\"groups\":[]}", allowedOrigin: allowedOrigin)
        }

        // 13. PUT /api/sidebar-layout
        if method == "PUT" && cleanPath == "/api/sidebar-layout" {
            let pm = PersistenceManager.shared
            guard let layout = try? pm.decoder.decode(SidebarLayout.self, from: body) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid sidebar-layout json", allowedOrigin: allowedOrigin)
            }
            guard layout.schemaVersion == 2 else {
                return Self.buildHTTPResponse(status: 400, body: "error: unsupported schemaVersion, must be 2", allowedOrigin: allowedOrigin)
            }

            let manifest = (try? pm.loadManifest()) ?? WorkspaceManifest()
            var knownIds = Set(manifest.workspaces.map(\.id))
            let workspacesDir = pm.appDataURL.appendingPathComponent("workspaces")
            if let dirs = try? FileManager.default.contentsOfDirectory(atPath: workspacesDir.path) {
                for d in dirs {
                    if let u = UUID(uuidString: d) {
                        knownIds.insert(u)
                    }
                }
            }

            for id in layout.topLevelItems {
                if !knownIds.contains(id) {
                    return Self.buildHTTPResponse(status: 400, body: "error: unknown workspace id \(id) in topLevelItems", allowedOrigin: allowedOrigin)
                }
            }
            for group in layout.groups {
                for id in group.items {
                    if !knownIds.contains(id) {
                        return Self.buildHTTPResponse(status: 400, body: "error: unknown workspace id \(id) in group \(group.name)", allowedOrigin: allowedOrigin)
                    }
                }
            }

            do {
                try pm.saveSidebarLayout(layout)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save sidebar layout - \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // 14. GET /api/workspaces
        if method == "GET" && cleanPath == "/api/workspaces" {
            let pm = PersistenceManager.shared
            var manifest = (try? pm.loadManifest()) ?? WorkspaceManifest()
            let isoFormatter = ISO8601DateFormatter()
            isoFormatter.formatOptions = [.withInternetDateTime]

            var entriesById: [UUID: WorkspaceEntry] = [:]
            for w in manifest.workspaces {
                entriesById[w.id] = w
            }

            // Scan ~/.open-maestri/workspaces/ for any workspaces on disk not yet in manifest
            let workspacesDir = pm.appDataURL.appendingPathComponent("workspaces")
            if let dirs = try? FileManager.default.contentsOfDirectory(atPath: workspacesDir.path) {
                for dirName in dirs {
                    if let wsId = UUID(uuidString: dirName), entriesById[wsId] == nil {
                        if let doc = try? pm.loadWorkspace(id: wsId) {
                            let entry = WorkspaceEntry(
                                id: wsId,
                                name: doc.payload.name,
                                workingDirectory: doc.payload.workingDirectory,
                                icon: "folder",
                                color: "blue"
                            )
                            entriesById[wsId] = entry
                            manifest.workspaces.append(entry)
                        }
                    }
                }
            }

            var list: [[String: Any]] = []
            for w in manifest.workspaces {
                list.append([
                    "id": w.id.uuidString,
                    "name": w.name,
                    "workingDirectory": w.workingDirectory,
                    "icon": w.icon,
                    "color": w.color,
                    "isPinned": w.isPinned,
                    "locationType": w.locationType,
                    "createdAt": isoFormatter.string(from: Date())
                ])
            }
            if let data = try? JSONSerialization.data(withJSONObject: list, options: [.prettyPrinted]) {
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "[]", allowedOrigin: allowedOrigin)
        }

        // 15. POST /api/workspaces
        if method == "POST" && cleanPath == "/api/workspaces" {
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
                  let name = json["name"] as? String, !name.isEmpty else {
                return Self.buildHTTPResponse(status: 400, body: "error: missing workspace name", allowedOrigin: allowedOrigin)
            }
            let workingDirectory = (json["workingDirectory"] as? String) ?? FileManager.default.currentDirectoryPath
            let id = (json["id"] as? String).flatMap(UUID.init) ?? UUID()
            let color = (json["color"] as? String) ?? "blue"
            let icon = (json["icon"] as? String) ?? "folder"
            let isPinned = (json["isPinned"] as? Bool) ?? false
            let locationType = (json["locationType"] as? String) ?? "local"
            var entry = WorkspaceEntry(id: id, name: name, workingDirectory: workingDirectory, icon: icon, color: color)
            entry.isPinned = isPinned
            entry.locationType = locationType

            let pm = PersistenceManager.shared
            try? pm.ensureWorkspaceDirectoryExists(id: id)

            // Create initial workspace document via WorkspaceManager
            let wsManager = WorkspaceManager(entry: entry)
            try? wsManager.saveSync()

            var manifest = (try? pm.loadManifest()) ?? WorkspaceManifest()
            manifest.workspaces.removeAll { $0.id == id }
            manifest.workspaces.append(entry)
            try? pm.saveManifest(manifest)

            var appState = (try? pm.loadAppState()) ?? AppStateData()
            appState.activeWorkspaceId = id
            try? pm.saveAppState(appState)

            var sidebar = (try? pm.loadSidebarLayout()) ?? SidebarLayout()
            if !sidebar.topLevelItems.contains(id) && !sidebar.groups.contains(where: { $0.items.contains(id) }) {
                sidebar.topLevelItems.append(id)
                try? pm.saveSidebarLayout(sidebar)
            }

            let isoFormatter = ISO8601DateFormatter()
            isoFormatter.formatOptions = [.withInternetDateTime]
            let respObj: [String: Any] = [
                "id": id.uuidString,
                "name": name,
                "workingDirectory": workingDirectory,
                "icon": icon,
                "color": color,
                "isPinned": isPinned,
                "locationType": locationType,
                "createdAt": isoFormatter.string(from: Date())
            ]
            if let data = try? JSONSerialization.data(withJSONObject: respObj) {
                return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", body: "{\"id\":\"\(id.uuidString)\"}", allowedOrigin: allowedOrigin)
        }

        // 16. GET /api/floors
        if method == "GET" && cleanPath == "/api/floors" {
            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }
            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }
            if targetWorkspaceId == nil {
                let manifest = (try? pm.loadManifest()) ?? WorkspaceManifest()
                targetWorkspaceId = manifest.workspaces.first?.id
            }

            var floorsList: [[String: Any]] = []
            if let wsId = targetWorkspaceId, let doc = try? pm.loadWorkspace(id: wsId) {
                for f in doc.payload.floors {
                    floorsList.append(Self.floorJSON(f))
                }
            }
            if let data = try? JSONSerialization.data(withJSONObject: floorsList) {
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "[]", allowedOrigin: allowedOrigin)
        }

        // POST /api/floors
        if method == "POST" && cleanPath == "/api/floors" {
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
                  let name = json["name"] as? String, !name.isEmpty else {
                return Self.buildHTTPResponse(status: 400, body: "error: missing floor name", allowedOrigin: allowedOrigin)
            }

            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = (json["workspaceId"] as? String).flatMap(UUID.init)
            if targetWorkspaceId == nil && pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }
            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId
            }
            if targetWorkspaceId == nil {
                let manifest = (try? pm.loadManifest()) ?? WorkspaceManifest()
                targetWorkspaceId = manifest.workspaces.first?.id
            }

            guard let wsId = targetWorkspaceId,
                  var doc = try? pm.loadWorkspace(id: wsId) else {
                return Self.buildHTTPResponse(status: 404, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            let workingDirectory = (json["workingDirectory"] as? String) ?? doc.payload.workingDirectory
            let branchName = (json["branchName"] as? String) ?? "floor/\(FloorManager.slugify(name))"

            do {
                let floor = try FloorManager.shared.createFloor(
                    name: name, branchName: branchName, workingDirectory: workingDirectory,
                    existingBranch: (json["existingBranch"] as? Bool) ?? false,
                    baseBranch: json["baseBranch"] as? String)
                var hooks = floor.hooks
                if let hooksDict = json["hooks"] as? [String: Any] {
                    hooks = Self.decodeFloorHooks(hooksDict, fallback: hooks)
                }
                let floorEntry = FloorEntry(
                    id: floor.id,
                    name: floor.name,
                    branchName: floor.branchName,
                    worktreePath: floor.worktreePath,
                    hooks: hooks,
                    createdAt: floor.createdAt
                )
                doc.payload.floors.append(floorEntry)
                doc.payload.lastModifiedAt = Date()
                try pm.saveSync(doc, to: pm.workspaceURL(id: wsId))
                EventBroadcaster.shared.publish(
                    type: "floorCreated", workspaceId: wsId,
                    payload: ["floorId": floor.id.uuidString, "name": floor.name,
                              "branchName": floor.branchName, "worktreePath": floor.worktreePath]
                )
                EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: wsId, payload: [:])

                let respObj = Self.floorJSON(floorEntry)
                if let data = try? JSONSerialization.data(withJSONObject: respObj) {
                    return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
                }
                return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", body: "{\"id\":\"\(floor.id.uuidString)\"}", allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 400, body: "error: failed to create floor - \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // GET /api/floors/:id/land-preview?target=<branch>  -> {floorBranch, target, branches, dirty, dirtyFiles, sameBranch,
        //                                                     ahead, fastForward, stat, conflicts, canLand}
        // POST /api/floors/:id/land  body: {target}          -> {status, commit, target}
        if (method == "GET" && cleanPath.hasPrefix("/api/floors/") && cleanPath.hasSuffix("/land-preview"))
            || (method == "POST" && cleanPath.hasPrefix("/api/floors/") && cleanPath.hasSuffix("/land")) {
            let isPreview = method == "GET"
            let rawId = String(cleanPath.dropFirst("/api/floors/".count).dropLast(isPreview ? "/land-preview".count : "/land".count))
            guard let floorId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid floor ID", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            guard let wsId = Self.resolveWorkspaceId(pathParts: pathParts, pm: pm),
                  let doc = try? pm.loadWorkspace(id: wsId),
                  let entry = doc.payload.floors.first(where: { $0.id == floorId }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: floor not found", allowedOrigin: allowedOrigin)
            }
            let wd = Self.floorRepoDirectory(entry: entry, fallback: doc.payload.workingDirectory)
            let floor = Self.floor(from: entry, workingDirectory: wd)
            do {
                if isPreview {
                    let preview = try FloorManager.shared.landPreview(floor: floor, target: Self.queryValue(pathParts, "target"), workingDirectory: wd)
                    var respObj = preview.json
                    respObj["floorId"] = entry.id.uuidString
                    let data = (try? JSONSerialization.data(withJSONObject: respObj)) ?? Data("{}".utf8)
                    return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
                }
                let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
                guard let target = json?["target"] as? String, !target.isEmpty else {
                    return Self.buildHTTPResponse(status: 400, body: "error: missing target branch", allowedOrigin: allowedOrigin)
                }
                let commit = try FloorManager.shared.land(floor: floor, targetBranch: target, workingDirectory: wd)
                EventBroadcaster.shared.publish(
                    type: "floorLanded", workspaceId: wsId,
                    payload: ["floorId": entry.id.uuidString, "target": target, "commit": commit]
                )
                let data = (try? JSONSerialization.data(withJSONObject: ["status": "ok", "commit": commit, "target": target])) ?? Data("{}".utf8)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            } catch MaestriError.invalidTargetBranch(let msg) {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid target branch - \(msg)", allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 409, body: "error: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // GET /api/floors/:id/status -> {base, dirty, files:[{path,status}], ahead, behind, insertions, deletions}
        if method == "GET" && cleanPath.hasPrefix("/api/floors/") && cleanPath.hasSuffix("/status") {
            let rawId = String(cleanPath.dropFirst("/api/floors/".count).dropLast("/status".count))
            guard let floorId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid floor ID", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            guard let wsId = Self.resolveWorkspaceId(pathParts: pathParts, pm: pm),
                  let doc = try? pm.loadWorkspace(id: wsId),
                  let entry = doc.payload.floors.first(where: { $0.id == floorId }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: floor not found", allowedOrigin: allowedOrigin)
            }
            let wd = Self.floorRepoDirectory(entry: entry, fallback: doc.payload.workingDirectory)
            do {
                let st = try FloorManager.shared.status(floor: Self.floor(from: entry, workingDirectory: wd), workingDirectory: wd)
                let data = (try? JSONSerialization.data(withJSONObject: st.json)) ?? Data("{}".utf8)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            } catch {
                return Self.buildHTTPResponse(status: 409, body: "error: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
        }

        // PATCH /api/floors/:id  body: {name?, hooks?}
        if method == "PATCH" && cleanPath.hasPrefix("/api/floors/") {
            let rawId = String(cleanPath.dropFirst("/api/floors/".count))
            guard let floorId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid floor ID", allowedOrigin: allowedOrigin)
            }
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid JSON body", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            guard let wsId = Self.resolveWorkspaceId(pathParts: pathParts, pm: pm),
                  var doc = try? pm.loadWorkspace(id: wsId),
                  let idx = doc.payload.floors.firstIndex(where: { $0.id == floorId }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: floor not found", allowedOrigin: allowedOrigin)
            }
            if let name = json["name"] as? String, !name.isEmpty {
                doc.payload.floors[idx].name = name
            }
            if let hooksDict = json["hooks"] as? [String: Any] {
                doc.payload.floors[idx].hooks = Self.decodeFloorHooks(hooksDict, fallback: doc.payload.floors[idx].hooks)
            }
            doc.payload.lastModifiedAt = Date()
            do {
                try pm.saveSync(doc, to: pm.workspaceURL(id: wsId))
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save workspace: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
            let updated = doc.payload.floors[idx]
            EventBroadcaster.shared.publish(
                type: "floorChanged", workspaceId: wsId,
                payload: ["floorId": updated.id.uuidString, "name": updated.name, "branchName": updated.branchName]
            )
            EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: wsId, payload: [:])
            let data = (try? JSONSerialization.data(withJSONObject: Self.floorJSON(updated))) ?? Data("{}".utf8)
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
        }

        // DELETE /api/floors/:id  (query: removeWorktree=0 保留 worktree，仅移除记录)
        if method == "DELETE" && cleanPath.hasPrefix("/api/floors/") {
            let rawId = String(cleanPath.dropFirst("/api/floors/".count))
            guard let floorId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid floor ID", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            guard let wsId = Self.resolveWorkspaceId(pathParts: pathParts, pm: pm),
                  var doc = try? pm.loadWorkspace(id: wsId) else {
                return Self.buildHTTPResponse(status: 404, body: "error: workspace not found", allowedOrigin: allowedOrigin)
            }
            guard let entry = doc.payload.floors.first(where: { $0.id == floorId }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: floor not found", allowedOrigin: allowedOrigin)
            }
            let keepWorktree = Self.queryValue(pathParts, "removeWorktree") == "0"
            let deleteBranch = Self.queryValue(pathParts, "deleteBranch") == "1"
            if !keepWorktree {
                let wd = Self.floorRepoDirectory(entry: entry, fallback: doc.payload.workingDirectory)
                let floor = Self.floor(from: entry, workingDirectory: wd)
                do {
                    try FloorManager.shared.removeFloor(floor, workingDirectory: wd, deleteBranch: deleteBranch)
                } catch {
                    return Self.buildHTTPResponse(status: 400, body: "error: failed to remove worktree - \(error.localizedDescription)", allowedOrigin: allowedOrigin)
                }
            }
            doc.payload.floors.removeAll { $0.id == floorId }
            doc.payload.lastModifiedAt = Date()
            do {
                try pm.saveSync(doc, to: pm.workspaceURL(id: wsId))
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save workspace: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
            EventBroadcaster.shared.publish(type: "floorRemoved", workspaceId: wsId, payload: ["floorId": floorId.uuidString])
            EventBroadcaster.shared.publish(type: "workspaceSaved", workspaceId: wsId, payload: [:])
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
        }

        // GET /api/routines
        if method == "GET" && cleanPath == "/api/routines" {
            let pm = PersistenceManager.shared
            let container = (try? pm.load(RoutinesContainer.self, from: pm.routinesURL)) ?? RoutinesContainer()
            let list = container.routines.map { Self.routineJSON($0) }
            let data = (try? JSONSerialization.data(withJSONObject: list)) ?? Data("[]".utf8)
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
        }

        // POST /api/routines  body: {name, prompt, intervalSeconds, targetTerminalId}
        if method == "POST" && cleanPath == "/api/routines" {
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
                  let name = json["name"] as? String, !name.isEmpty,
                  let prompt = json["prompt"] as? String, !prompt.isEmpty,
                  let interval = (json["intervalSeconds"] as? NSNumber)?.doubleValue, interval > 0,
                  let tidStr = json["targetTerminalId"] as? String, let tid = UUID(uuidString: tidStr) else {
                return Self.buildHTTPResponse(status: 400, body: "error: require name, prompt, intervalSeconds>0, targetTerminalId", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            var container = (try? pm.load(RoutinesContainer.self, from: pm.routinesURL)) ?? RoutinesContainer()
            let routine = Routine(name: name, prompt: prompt, intervalSeconds: interval, targetTerminalId: tid)
            container.routines.append(routine)
            do { try pm.saveSync(container, to: pm.routinesURL) } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save routines: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
            EventBroadcaster.shared.publish(type: "routineChanged", payload: ["action": "created", "routineId": routine.id.uuidString])
            let data = (try? JSONSerialization.data(withJSONObject: Self.routineJSON(routine))) ?? Data("{}".utf8)
            return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
        }

        // PATCH /api/routines/:id  body: {isActive?, name?, prompt?, prompts?, intervalSeconds?, targetTerminalId?}
        if method == "PATCH" && cleanPath.hasPrefix("/api/routines/") {
            let rawId = String(cleanPath.dropFirst("/api/routines/".count))
            guard let rid = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid routine ID", allowedOrigin: allowedOrigin)
            }
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid JSON body", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            var container = (try? pm.load(RoutinesContainer.self, from: pm.routinesURL)) ?? RoutinesContainer()
            guard let idx = container.routines.firstIndex(where: { $0.id == rid }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: routine not found", allowedOrigin: allowedOrigin)
            }
            if let active = json["isActive"] as? Bool { container.routines[idx].isActive = active }
            if let name = json["name"] as? String, !name.isEmpty { container.routines[idx].name = name }
            if let prompts = json["prompts"] as? [String], !prompts.isEmpty {
                container.routines[idx].prompts = prompts
            } else if let prompt = json["prompt"] as? String {
                let parsed = Routine.parsePrompts(prompt)
                if !parsed.isEmpty { container.routines[idx].prompts = parsed }
            }
            if let interval = (json["intervalSeconds"] as? NSNumber)?.doubleValue, interval > 0 {
                container.routines[idx].intervalSeconds = interval
            }
            if let tidStr = json["targetTerminalId"] as? String, let tid = UUID(uuidString: tidStr) {
                container.routines[idx].targetTerminalId = tid
            }
            do { try pm.saveSync(container, to: pm.routinesURL) } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save routines: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
            EventBroadcaster.shared.publish(type: "routineChanged", payload: ["action": "updated", "routineId": rid.uuidString])
            let data = (try? JSONSerialization.data(withJSONObject: Self.routineJSON(container.routines[idx]))) ?? Data("{}".utf8)
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
        }

        // POST /api/routines/:id/{pause|resume|run-now}
        if method == "POST" && cleanPath.hasPrefix("/api/routines/") {
            let tail = String(cleanPath.dropFirst("/api/routines/".count))
            let parts = tail.components(separatedBy: "/")
            guard parts.count == 2, let rid = UUID(uuidString: parts[0]) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid routine action path", allowedOrigin: allowedOrigin)
            }
            let action = parts[1]
            let pm = PersistenceManager.shared
            var container = (try? pm.load(RoutinesContainer.self, from: pm.routinesURL)) ?? RoutinesContainer()
            guard let idx = container.routines.firstIndex(where: { $0.id == rid }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: routine not found", allowedOrigin: allowedOrigin)
            }
            switch action {
            case "pause":
                container.routines[idx].isActive = false
                try? pm.saveSync(container, to: pm.routinesURL)
                EventBroadcaster.shared.publish(type: "routineChanged", payload: ["action": "paused", "routineId": rid.uuidString])
                let data = (try? JSONSerialization.data(withJSONObject: Self.routineJSON(container.routines[idx]))) ?? Data("{}".utf8)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            case "resume":
                container.routines[idx].isActive = true
                try? pm.saveSync(container, to: pm.routinesURL)
                EventBroadcaster.shared.publish(type: "routineChanged", payload: ["action": "resumed", "routineId": rid.uuidString])
                let data = (try? JSONSerialization.data(withJSONObject: Self.routineJSON(container.routines[idx]))) ?? Data("{}".utf8)
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            case "run-now":
                let routine = container.routines[idx]
                EventBroadcaster.shared.publish(type: "routineStarted", payload: ["routineId": rid.uuidString, "n": routine.prompts.count])
                for (i, prompt) in routine.prompts.enumerated() {
                    await MainActor.run {
                        #if os(Linux)
                        LinuxTerminalManager.shared.writeLine(to: routine.targetTerminalId, text: prompt)
                        #endif
                    }
                    EventBroadcaster.shared.publish(type: "routineStep", payload: ["routineId": rid.uuidString, "i": i + 1, "n": routine.prompts.count])
                }
                EventBroadcaster.shared.publish(type: "routineFinished", payload: ["routineId": rid.uuidString])
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\",\"id\":\"\(rid.uuidString)\"}", allowedOrigin: allowedOrigin)
            default:
                return Self.buildHTTPResponse(status: 400, body: "error: unknown routine action '\(action)'", allowedOrigin: allowedOrigin)
            }
        }

        // DELETE /api/routines/:id
        if method == "DELETE" && cleanPath.hasPrefix("/api/routines/") {
            let rawId = String(cleanPath.dropFirst("/api/routines/".count))
            guard let rid = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid routine ID", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            var container = (try? pm.load(RoutinesContainer.self, from: pm.routinesURL)) ?? RoutinesContainer()
            guard container.routines.contains(where: { $0.id == rid }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: routine not found", allowedOrigin: allowedOrigin)
            }
            container.routines.removeAll { $0.id == rid }
            do { try pm.saveSync(container, to: pm.routinesURL) } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save routines: \(error.localizedDescription)", allowedOrigin: allowedOrigin)
            }
            EventBroadcaster.shared.publish(type: "routineChanged", payload: ["action": "removed", "routineId": rid.uuidString])
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
        }

        // 17. GET /api/agents
        if method == "GET" && cleanPath == "/api/agents" {
            let agentList: [[String: Any]] = await MainActor.run {
                #if os(Linux)
                let tm = LinuxTerminalManager.shared
                return tm.terminals.values.map { session in
                    [
                        "id": session.id.uuidString,
                        "name": session.displayName ?? session.roleName ?? "Terminal",
                        "kind": session.agentType
                    ]
                }
                #else
                return []
                #endif
            }
            if let data = try? JSONSerialization.data(withJSONObject: agentList) {
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "[]", allowedOrigin: allowedOrigin)
        }

        // 18. GET /api/files
        if method == "GET" && cleanPath == "/api/files" {
            var root = ""
            var relPath = ""
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 {
                        let key = kv[0]
                        let val = kv[1].removingPercentEncoding ?? kv[1]
                        if key == "root" { root = val }
                        else if key == "path" { relPath = val }
                    }
                }
            }
            if root.isEmpty && relPath.hasPrefix("/") {
                root = relPath
                relPath = ""
            } else if !root.isEmpty && relPath.hasPrefix("/") {
                relPath = String(relPath.dropFirst())
            }
            if root.isEmpty { root = FileManager.default.currentDirectoryPath }
            guard !relPath.contains(".."), !relPath.contains("\\"), !relPath.contains("\0") else {
                return Self.buildHTTPResponse(status: 400, body: "error: path traversal detected", allowedOrigin: allowedOrigin)
            }
            let targetDir = relPath.isEmpty ? root : "\(root)/\(relPath)"
            let fm = FileManager.default
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: targetDir, isDirectory: &isDir), isDir.boolValue else {
                return Self.buildHTTPResponse(status: 404, body: "error: directory not found", allowedOrigin: allowedOrigin)
            }
            guard let contents = try? fm.contentsOfDirectory(atPath: targetDir) else {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to read directory", allowedOrigin: allowedOrigin)
            }

            let gitProvider = GitStatusProvider(workingDirectory: root)
            let gitChanges = (try? gitProvider.status()) ?? []
            var gitMap: [String: String] = [:]
            for change in gitChanges {
                let badge: String
                switch change.status {
                case .modified: badge = "M"
                case .added:    badge = "A"
                case .deleted:  badge = "D"
                case .renamed:  badge = "R"
                case .untracked: badge = "?"
                default: continue
                }
                gitMap[change.path] = badge
            }

            var items: [[String: Any]] = []
            for item in contents {
                if item.hasPrefix(".") { continue }
                let fullPath = "\(targetDir)/\(item)"
                var subDir: ObjCBool = false
                _ = fm.fileExists(atPath: fullPath, isDirectory: &subDir)
                let itemRelPath = relPath.isEmpty ? item : "\(relPath)/\(item)"
                var itemObj: [String: Any] = [
                    "name": item,
                    "path": itemRelPath,
                    "isDir": subDir.boolValue
                ]
                if let badge = gitMap[itemRelPath] {
                    itemObj["gitStatus"] = badge
                }
                items.append(itemObj)
            }

            items.sort { a, b in
                let aIsDir = (a["isDir"] as? Bool) ?? false
                let bIsDir = (b["isDir"] as? Bool) ?? false
                if aIsDir != bIsDir {
                    return aIsDir && !bIsDir
                }
                let aName = (a["name"] as? String) ?? ""
                let bName = (b["name"] as? String) ?? ""
                return aName.localizedStandardCompare(bName) == .orderedAscending
            }

            if let data = try? JSONSerialization.data(withJSONObject: items) {
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "[]", allowedOrigin: allowedOrigin)
        }

        // 19. GET /api/git/status
        if method == "GET" && cleanPath == "/api/git/status" {
            var root = ""
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "root" {
                        root = kv[1].removingPercentEncoding ?? kv[1]
                    }
                }
            }
            if root.isEmpty { root = FileManager.default.currentDirectoryPath }
            let provider = GitStatusProvider(workingDirectory: root)
            var changes: [[String: Any]] = []
            if let entries = try? provider.status() {
                for entry in entries {
                    let badge: String
                    switch entry.status {
                    case .modified: badge = "M"
                    case .added:    badge = "A"
                    case .deleted:  badge = "D"
                    case .renamed:  badge = "R"
                    case .untracked: badge = "?"
                    default: continue
                    }
                    changes.append(["path": entry.path, "status": badge])
                }
            }
            if let data = try? JSONSerialization.data(withJSONObject: changes) {
                return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "[]", allowedOrigin: allowedOrigin)
        }

        // 20. POST /api/nodes
        if method == "POST" && cleanPath == "/api/nodes" {
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
                  let nodeType = json["nodeType"] as? String else {
                return Self.buildHTTPResponse(status: 400, body: "error: missing nodeType", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            var targetWorkspaceId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" {
                        targetWorkspaceId = UUID(uuidString: kv[1])
                    }
                }
            }
            if targetWorkspaceId == nil {
                let appState = try? pm.loadAppState()
                targetWorkspaceId = appState?.activeWorkspaceId ?? (try? pm.loadManifest())?.workspaces.first?.id
            }

            var wsDoc: WorkspaceDocument? = nil
            var resolvedWsId: UUID? = targetWorkspaceId
            if let wsId = targetWorkspaceId, let loaded = try? pm.loadWorkspace(id: wsId) {
                wsDoc = loaded
            } else {
                let defaultPayload = WorkspacePayload(name: "Default", workingDirectory: FileManager.default.homeDirectoryForCurrentUser.path)
                let defaultDoc = WorkspaceDocument(payload: defaultPayload)
                try? pm.ensureWorkspaceDirectoryExists(id: defaultPayload.id)
                try? pm.saveSync(defaultDoc, to: pm.workspaceURL(id: defaultPayload.id))
                var appState = (try? pm.loadAppState()) ?? AppStateData()
                appState.activeWorkspaceId = defaultPayload.id
                try? pm.saveAppState(appState)
                wsDoc = defaultDoc
                resolvedWsId = defaultPayload.id
            }

            guard let wsId = resolvedWsId, var doc = wsDoc else {
                return Self.buildHTTPResponse(status: 404, body: "error: active workspace not found", allowedOrigin: allowedOrigin)
            }

            let nodeId = UUID()
            let frameArray = (json["frame"] as? [[Double]]) ?? [[100, 100], [500, 350]]
            let frameX = frameArray.count > 0 && frameArray[0].count > 0 ? frameArray[0][0] : 100
            let frameY = frameArray.count > 0 && frameArray[0].count > 1 ? frameArray[0][1] : 100
            let frameW = frameArray.count > 1 && frameArray[1].count > 0 ? frameArray[1][0] : 500
            let frameH = frameArray.count > 1 && frameArray[1].count > 1 ? frameArray[1][1] : 350
            let frame = CGRect(x: frameX, y: frameY, width: frameW, height: frameH)

            var content: NodeContent? = nil
            if let contentObj = json["content"],
               let contentData = try? JSONSerialization.data(withJSONObject: contentObj) {
                content = try? pm.decoder.decode(NodeContent.self, from: contentData)
            }

            if content == nil {
                switch nodeType {
                case "terminal":
                    content = .terminal(TerminalContent(
                        name: "Terminal",
                        agentType: "generic_shell",
                        command: "bash",
                        workingDirectory: doc.payload.workingDirectory
                    ))
                case "stickyNote":
                    let noteName = (json["name"] as? String) ?? "Note-\(nodeId.uuidString.prefix(6))"
                    content = .stickyNote(StickyNoteContent(name: noteName))
                case "portal":
                    let portalName = (json["name"] as? String) ?? "Portal"
                    let portalUrl = (json["url"] as? String) ?? (json["currentURL"] as? String) ?? ""
                    content = .portal(PortalContent(name: portalName, url: portalUrl))
                case "fileTree":
                    let ftName = (json["name"] as? String) ?? "Files"
                    let rootPath = (json["rootPath"] as? String) ?? doc.payload.workingDirectory
                    content = .fileTree(FileTreeContent(name: ftName, rootPath: rootPath))
                default:
                    content = .terminal(TerminalContent(
                        name: "Terminal",
                        agentType: "generic_shell",
                        command: "bash",
                        workingDirectory: doc.payload.workingDirectory
                    ))
                }
            }

            let maxZ = doc.payload.nodes.map(\.zIndex).max() ?? 0
            let node = CanvasNode(id: nodeId, frame: frame, content: content!, zIndex: maxZ + 1)
            doc.payload.nodes.append(node)
            doc.payload.lastModifiedAt = Date()
            let fileURL = pm.workspaceURL(id: wsId)
            try? pm.saveSync(doc, to: fileURL)

            var nodePayload: [String: Any] = ["nodeId": nodeId.uuidString, "nodeType": nodeType]
            if let enc = try? pm.encoder.encode(node),
               let obj = try? JSONSerialization.jsonObject(with: enc) {
                nodePayload["node"] = obj
            }
            EventBroadcaster.shared.publish(type: "nodeAdded", workspaceId: wsId, payload: nodePayload)

            if let encoded = try? pm.encoder.encode(node) {
                return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", data: encoded, allowedOrigin: allowedOrigin)
            }
            return Self.buildHTTPResponse(status: 201, contentType: "application/json; charset=utf-8", body: "{\"id\":\"\(nodeId.uuidString)\"}", allowedOrigin: allowedOrigin)
        }

        // 21. DELETE /api/nodes/:id  (?workspace=UUID 可选)
        if method == "DELETE" && cleanPath.hasPrefix("/api/nodes/") {
            let rawId = String(cleanPath.dropFirst("/api/nodes/".count))
            guard let nodeId = UUID(uuidString: rawId) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid node ID", allowedOrigin: allowedOrigin)
            }
            let pm = PersistenceManager.shared
            var wsId: UUID? = nil
            if pathParts.count > 1 {
                for pair in pathParts[1].components(separatedBy: "&") {
                    let kv = pair.components(separatedBy: "=")
                    if kv.count == 2 && kv[0] == "workspace" { wsId = UUID(uuidString: kv[1]) }
                }
            }
            if wsId == nil {
                wsId = (try? pm.loadAppState())?.activeWorkspaceId ?? (try? pm.loadManifest())?.workspaces.first?.id
            }
            guard let wsId, var doc = try? pm.loadWorkspace(id: wsId),
                  let idx = doc.payload.nodes.firstIndex(where: { $0.id == nodeId }) else {
                return Self.buildHTTPResponse(status: 404, body: "error: node not found", allowedOrigin: allowedOrigin)
            }
            let removed = doc.payload.nodes.remove(at: idx)
            var ids: Set<UUID> = [nodeId]
            if case .terminal(let t) = removed.content { ids.insert(t.id) }
            var prunedEvents: [[String: Any]] = []
            func pruned(_ cid: UUID, _ kind: String, _ a: UUID, _ b: UUID) {
                prunedEvents.append(["connectionId": cid.uuidString, "kind": kind, "a": a.uuidString, "b": b.uuidString])
            }
            doc.payload.connections.forEach { if ids.contains($0.terminalIdA) || ids.contains($0.terminalIdB) { pruned($0.id, "terminalToTerminal", $0.terminalIdA, $0.terminalIdB) } }
            doc.payload.noteConnections.forEach { if ids.contains($0.terminalId) || ids.contains($0.noteNodeId) { pruned($0.id, "terminalToNote", $0.terminalId, $0.noteNodeId) } }
            doc.payload.portalConnections.forEach { if ids.contains($0.terminalId) || ids.contains($0.portalNodeId) { pruned($0.id, "terminalToPortal", $0.terminalId, $0.portalNodeId) } }
            doc.payload.portalToPortalConnections.forEach { if ids.contains($0.portalIdA) || ids.contains($0.portalIdB) { pruned($0.id, "portalToPortal", $0.portalIdA, $0.portalIdB) } }
            doc.payload.noteToNoteConnections.forEach { if ids.contains($0.noteNodeIdA) || ids.contains($0.noteNodeIdB) { pruned($0.id, "noteToNote", $0.noteNodeIdA, $0.noteNodeIdB) } }
            doc.payload.crossFloorConnections.forEach { if ids.contains($0.nodeIdA) || ids.contains($0.nodeIdB) { pruned($0.id, "crossFloor", $0.nodeIdA, $0.nodeIdB) } }
            doc.payload.connections.removeAll { ids.contains($0.terminalIdA) || ids.contains($0.terminalIdB) }
            doc.payload.noteConnections.removeAll { ids.contains($0.terminalId) || ids.contains($0.noteNodeId) }
            doc.payload.portalConnections.removeAll { ids.contains($0.terminalId) || ids.contains($0.portalNodeId) }
            doc.payload.portalToPortalConnections.removeAll { ids.contains($0.portalIdA) || ids.contains($0.portalIdB) }
            doc.payload.noteToNoteConnections.removeAll { ids.contains($0.noteNodeIdA) || ids.contains($0.noteNodeIdB) }
            doc.payload.crossFloorConnections.removeAll { ids.contains($0.nodeIdA) || ids.contains($0.nodeIdB) }
            doc.payload.lastModifiedAt = Date()
            do {
                try pm.saveSync(doc, to: pm.workspaceURL(id: wsId))
            } catch {
                return Self.buildHTTPResponse(status: 500, body: "error: failed to save workspace", allowedOrigin: allowedOrigin)
            }
            for ev in prunedEvents {
                EventBroadcaster.shared.publish(type: "connectionRemoved", workspaceId: wsId, payload: ev)
            }
            EventBroadcaster.shared.publish(type: "nodeRemoved", workspaceId: wsId, payload: ["nodeId": nodeId.uuidString])
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: "{\"status\":\"ok\"}", allowedOrigin: allowedOrigin)
        }

        return Self.buildHTTPResponse(status: 404, body: "error: not found", allowedOrigin: allowedOrigin)
    }

    // MARK: - HTTP 解析与响应工具

    private static func parseContentLength(from headers: String) -> Int {
        for line in headers.components(separatedBy: "\r\n") {
            let lower = line.lowercased()
            if lower.hasPrefix("content-length:") {
                let val = line.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)
                return Int(val) ?? 0
            }
        }
        return 0
    }

    /// 解析目标 workspace：?workspace=UUID > activeWorkspaceId > manifest 首项。
    static func resolveWorkspaceId(pathParts: [String], pm: PersistenceManager) -> UUID? {
        if pathParts.count > 1 {
            for pair in pathParts[1].components(separatedBy: "&") {
                let kv = pair.components(separatedBy: "=")
                if kv.count == 2 && kv[0] == "workspace", let id = UUID(uuidString: kv[1]) { return id }
            }
        }
        if let id = (try? pm.loadAppState())?.activeWorkspaceId { return id }
        return ((try? pm.loadManifest()) ?? WorkspaceManifest()).workspaces.first?.id
    }

    /// Percent-decoded value of `key` in the request query (pathParts[1]).
    static func queryValue(_ pathParts: [String], _ key: String) -> String? {
        guard pathParts.count > 1 else { return nil }
        for pair in pathParts[1].components(separatedBy: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1).map(String.init)
            if kv.count == 2 && kv[0] == key {
                return kv[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? kv[1]
            }
        }
        return nil
    }

    static func floorRepoDirectory(entry: FloorEntry, fallback: String) -> String {
        if let range = entry.worktreePath.range(of: "/.open-maestri/floors/") {
            let candidate = String(entry.worktreePath[..<range.lowerBound])
            if !candidate.isEmpty { return candidate }
        }
        return fallback
    }

    static func floor(from entry: FloorEntry, workingDirectory: String) -> Floor {
        var floor = Floor(id: entry.id, name: entry.name, branchName: entry.branchName, workspaceDir: workingDirectory)
        floor.worktreePath = entry.worktreePath
        floor.hooks = entry.hooks
        floor.createdAt = entry.createdAt
        return floor
    }

    static func floorJSON(_ f: FloorEntry) -> [String: Any] {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        return [
            "id": f.id.uuidString,
            "name": f.name,
            "branchName": f.branchName,
            "worktreePath": f.worktreePath,
            "hooks": [
                "setup": f.hooks.setup,
                "run": f.hooks.run,
                "teardown": f.hooks.teardown,
                "autoRunSetup": f.hooks.autoRunSetup,
            ],
            "createdAt": iso.string(from: f.createdAt),
        ]
    }

    static func decodeFloorHooks(_ dict: [String: Any], fallback: FloorHooks) -> FloorHooks {
        var hooks = fallback
        if let s = dict["setup"] as? [String] { hooks.setup = s }
        if let r = dict["run"] as? [String] { hooks.run = r }
        if let t = dict["teardown"] as? [String] { hooks.teardown = t }
        if let a = dict["autoRunSetup"] as? Bool { hooks.autoRunSetup = a }
        return hooks
    }

    static func routineJSON(_ r: Routine) -> [String: Any] {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        return [
            "id": r.id.uuidString,
            "name": r.name,
            "prompts": r.prompts,
            "intervalSeconds": r.intervalSeconds,
            "targetTerminalId": r.targetTerminalId.uuidString,
            "isActive": r.isActive,
            "createdAt": iso.string(from: r.createdAt),
        ]
    }

    static func buildHTTPResponse(
        status: Int = 200,
        contentType: String = "text/plain; charset=utf-8",
        body: String,
        httpVersion: String = "1.1",
        allowedOrigin: String? = nil
    ) -> Data {
        let bodyData = body.data(using: .utf8) ?? Data()
        return buildHTTPResponse(status: status, contentType: contentType, data: bodyData, httpVersion: httpVersion, allowedOrigin: allowedOrigin)
    }

    static func buildHTTPResponse(
        status: Int = 200,
        contentType: String = "text/plain; charset=utf-8",
        data: Data,
        httpVersion: String = "1.1",
        allowedOrigin: String? = nil
    ) -> Data {
        let statusText: String
        switch status {
        case 200: statusText = "OK"
        case 201: statusText = "Created"
        case 204: statusText = "No Content"
        case 400: statusText = "Bad Request"
        case 401: statusText = "Unauthorized"
        case 403: statusText = "Forbidden"
        case 404: statusText = "Not Found"
        default:  statusText = "Internal Server Error"
        }

        var header = "HTTP/\(httpVersion) \(status) \(statusText)\r\n" +
                     "Content-Type: \(contentType)\r\n" +
                     "Content-Length: \(data.count)\r\n"
        if let origin = allowedOrigin, !origin.isEmpty {
            header += "Access-Control-Allow-Origin: \(origin)\r\n"
            header += "Vary: Origin\r\n"
        }
        header += "Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS\r\n"
        header += "Access-Control-Allow-Headers: Content-Type, Authorization, X-Terminal-ID\r\n"
        header += "Connection: close\r\n\r\n"
        return (header.data(using: .utf8) ?? Data()) + data
    }
}
