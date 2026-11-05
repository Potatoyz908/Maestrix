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

/// 本地 HTTP IPC 服务器
/// - 绑定 127.0.0.1 动态端口，仅接受本机连接（安全约束）
/// - 支持 Unix Domain Socket (~/.open-maestri/run/agent.sock)
/// - 单一路由：POST /cli
/// - 所有 omaestri CLI 命令通过此服务路由
final class InterAgentServer {
    static let shared = InterAgentServer()
    private let logger = Logger.make(category: "InterAgentServer")

    private(set) var port: UInt16 = 0

    // MARK: - Unix Socket 支持
    private var unixSocketFd: Int32 = -1
    private var unixSocketSource: DispatchSourceRead?

    /// 当前活跃的 Unix socket 路径（供 SwiftTermProvider / PTY 注入 MAESTRI_SOCKET）
    private(set) var currentSocketPath: String?

    /// 全局固定 socket 路径（不再绑定 workspace UUID，避免切换工作区后旧终端 CLI 断联）
    static var globalSocketPath: String {
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
    func start() throws {
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
                self?.handleClient(fd: clientFd)
            }
        }
    }
#else
    private var listener: NWListener?

    /// Starts the TCP HTTP server on a dynamic port via Network.framework (macOS).
    func start() throws {
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
                        let parsed = await self.parseHTTPRequest(total)
                        let responseData = self.buildHTTPResponse(body: parsed.responseBody, httpVersion: parsed.httpVersion)
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
                    let parsed = await self.parseHTTPRequest(total)
                    let responseData = self.buildHTTPResponse(body: parsed.responseBody, httpVersion: parsed.httpVersion)
                    connection.send(content: responseData, completion: .idempotent)
                    connection.cancel()
                }
            }
        }
    }
#endif

    /// Gracefully shuts down listeners and sockets.
    func stop() {
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
                self?.handleClient(fd: clientFd)
            }
        }
    }

    private func handleClient(fd: Int32) {
        var accumulated = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            let n = recv(fd, &buffer, buffer.count, 0)
            guard n > 0 else { break }
            accumulated.append(contentsOf: buffer[..<n])
            if let headerEnd = accumulated.range(of: Data("\r\n\r\n".utf8)) {
                let headerStr = String(decoding: accumulated[..<headerEnd.upperBound], as: UTF8.self)
                let contentLength = Self.parseContentLength(from: headerStr)
                let bodyReceived = accumulated.count - headerEnd.upperBound
                if contentLength <= 0 || bodyReceived >= contentLength { break }
            }
        }
        guard !accumulated.isEmpty else {
            close(fd)
            return
        }
        Task { [weak self, fd] in
            if let self {
                let parsed = await self.parseHTTPRequest(accumulated)
                let responseData = self.buildHTTPResponse(body: parsed.responseBody, httpVersion: parsed.httpVersion)
                responseData.withUnsafeBytes { rawBuffer in
                    guard let base = rawBuffer.baseAddress else { return }
                    _ = libcSend(fd, base, rawBuffer.count, 0)
                }
            }
            close(fd)
        }
    }

    // MARK: - HTTP 解析与响应

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

    private struct ParsedRequest {
        let responseBody: String
        let httpVersion: String
    }

    private func parseHTTPRequest(_ data: Data) async -> ParsedRequest {
        let raw = String(decoding: data, as: UTF8.self)
        var terminalId: UUID?
        var args: [String] = []
        var httpVersion = "1.1"

        let parts = raw.components(separatedBy: "\r\n\r\n")
        let headers = parts[0]
        let body = parts.count > 1 ? parts[1] : ""

        let headerLines = headers.components(separatedBy: "\r\n")
        if let requestLine = headerLines.first {
            if requestLine.contains("HTTP/1.0") {
                httpVersion = "1.0"
            } else if requestLine.contains("HTTP/1.1") {
                httpVersion = "1.1"
            }
        }

        for line in headerLines {
            let lower = line.lowercased()
            if lower.hasPrefix("x-terminal-id:") {
                let idStr = line.dropFirst("x-terminal-id:".count).trimmingCharacters(in: .whitespaces)
                terminalId = UUID(uuidString: idStr)
            }
        }

        if let bodyData = body.data(using: .utf8),
           let json = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any],
           let rawArgs = json["args"] as? [String] {
            args = rawArgs
        }

        guard !args.isEmpty else {
            return ParsedRequest(responseBody: "error: missing args", httpVersion: httpVersion)
        }
        let responseBody = await CLIRouter.shared.routeAsync(args: args, terminalId: terminalId)
        return ParsedRequest(responseBody: responseBody, httpVersion: httpVersion)
    }

    private func buildHTTPResponse(body: String, httpVersion: String = "1.1") -> Data {
        let bodyData = body.data(using: .utf8) ?? Data()
        let header = "HTTP/\(httpVersion) 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: \(bodyData.count)\r\nConnection: close\r\n\r\n"
        return (header.data(using: .utf8) ?? Data()) + bodyData
    }
}
