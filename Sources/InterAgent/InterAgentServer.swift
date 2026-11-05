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
            guard let doc = try? pm.decoder.decode(WorkspaceDocument.self, from: body) else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid WorkspaceDocument JSON", allowedOrigin: allowedOrigin)
            }
            guard doc.schemaVersion == Constants.schemaVersion else {
                return Self.buildHTTPResponse(status: 400, body: "error: invalid schemaVersion \(doc.schemaVersion), expected \(Constants.schemaVersion)", allowedOrigin: allowedOrigin)
            }
            do {
                try pm.ensureWorkspaceDirectoryExists(id: doc.payload.id)
                try pm.saveSync(doc, to: pm.workspaceURL(id: doc.payload.id))
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
                    doc.payload.connections.removeAll { $0.id == connId }
                    doc.payload.noteConnections.removeAll { $0.id == connId }
                    doc.payload.portalConnections.removeAll { $0.id == connId }
                    doc.payload.portalToPortalConnections.removeAll { $0.id == connId }
                    doc.payload.noteToNoteConnections.removeAll { $0.id == connId }
                    doc.payload.lastModifiedAt = Date()
                    try? pm.saveSync(doc, to: fileURL)
                }
            }

            let resp = "{\"status\":\"ok\"}"
            return Self.buildHTTPResponse(status: 200, contentType: "application/json; charset=utf-8", body: resp, allowedOrigin: allowedOrigin)
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
        header += "Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS\r\n"
        header += "Access-Control-Allow-Headers: Content-Type, Authorization, X-Terminal-ID\r\n"
        header += "Connection: close\r\n\r\n"
        return (header.data(using: .utf8) ?? Data()) + data
    }
}
