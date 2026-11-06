import Foundation
#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif
import SwiftTerm

/// Linux 原生 POSIX PTY 会话进程
/// - 使用 openpty/forkpty (<pty.h>) 启动登录 shell 或自定义命令
/// - DispatchSourceRead 异步监听 master PTY 输出
/// - 内部对接 SwiftTerm.Terminal headless 实例，处理 ANSI 序列与 buffer 快照
final class POSIXPtyProcess: @unchecked Sendable {
    private let logger = Logger.make(category: "POSIXPtyProcess")

    let terminalId: UUID
    let command: String
    let workingDirectory: String
    /// Extra variables for the child (K12 floor variables etc.); applied after the defaults.
    let extraEnvironment: [String: String]

    private(set) var childPid: pid_t = -1
    private(set) var masterFd: Int32 = -1
    private var readSource: DispatchSourceRead?
    private(set) var isRunning: Bool = false

    /// PTY 元数据 (供 /api/terminals 与 /ws/events 使用)
    private(set) var cols: Int = 80
    private(set) var rows: Int = 24
    private(set) var startedAt: Date?
    private(set) var exitCode: Int32?

    /// SwiftTerm 无头终端实例，用于 ANSI 解析与屏幕缓冲区快照
    private var headlessTerminal: Terminal?
    private let terminalDelegate = HeadlessTerminalDelegate()

    /// 输出监听回调 (在输出线程执行)
    var onDataReceived: ((String) -> Void)?
    /// 多监听器管理 (线程安全)
    private var outputListeners: [UUID: (String) -> Void] = [:]
    private let listenerLock = NSLock()

    /// 进程退出回调
    var onProcessExit: ((Int32?) -> Void)?

    init(terminalId: UUID, command: String, workingDirectory: String, extraEnvironment: [String: String] = [:]) {
        self.terminalId = terminalId
        self.command = command
        self.workingDirectory = workingDirectory
        self.extraEnvironment = extraEnvironment

        let term = Terminal(delegate: terminalDelegate)
        term.resize(cols: 80, rows: 24)
        term.changeScrollback(10000)
        self.headlessTerminal = term
    }

    /// 注册输出监听器，返回取消闭包 (支持多端同时监听)
    func addOutputListener(id: UUID = UUID(), _ listener: @escaping (String) -> Void) -> (() -> Void) {
        listenerLock.lock()
        outputListeners[id] = listener
        listenerLock.unlock()
        return { [weak self] in
            guard let self else { return }
            self.listenerLock.lock()
            self.outputListeners.removeValue(forKey: id)
            self.listenerLock.unlock()
        }
    }

    /// 启动 PTY 子进程
    func start() throws {
        guard !isRunning else { return }

        var master: Int32 = -1
        var slave: Int32 = -1
        var ws = winsize(ws_row: 24, ws_col: 80, ws_xpixel: 0, ws_ypixel: 0)

        #if canImport(Glibc)
        let openptyRes = openpty(&master, &slave, nil, nil, &ws)
        #elseif canImport(Darwin)
        let openptyRes = Darwin.openpty(&master, &slave, nil, nil, &ws)
        #endif
        guard openptyRes == 0 else {
            let err = errno
            throw NSError(domain: "POSIXPtyProcess", code: Int(err),
                          userInfo: [NSLocalizedDescriptionKey: "openpty failed: \(String(cString: strerror(err)))"])
        }

        // 确定要执行的 shell / 命令
        let userShell = ProcessInfo.processInfo.environment["SHELL"]
        let defaultShell = (userShell != nil && !userShell!.isEmpty && FileManager.default.isExecutableFile(atPath: userShell!)) ? userShell! : Constants.defaultShellPath
        let execPath: String
        let execArgs: [String]
        let trimmedCmd = command.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmedCmd.isEmpty || trimmedCmd == "bash" || trimmedCmd == "zsh" || trimmedCmd.hasSuffix("/bash") || trimmedCmd.hasSuffix("/zsh") {
            execPath = defaultShell
            execArgs = [defaultShell, "-i", "-l"]
        } else {
            // 对标 Maestri：始终在交互式 login shell 中启动命令，保持持久交互
            execPath = defaultShell
            execArgs = [defaultShell, "-i", "-l", "-c", "\(trimmedCmd); exec \(defaultShell)"]
        }

        // 构建环境变量
        var env = ProcessInfo.processInfo.environment
        env["TERM"] = "xterm-256color"
        env["MAESTRI_TERMINAL_ID"] = terminalId.uuidString
        env["OMAESTRI_TERMINAL_ID"] = terminalId.uuidString
        env["MAESTRI_SERVER_PORT"] = "\(PlatformHooks.serverPort())"
        if !InterAgentServer.shared.authToken.isEmpty {
            env["MAESTRI_AUTH_TOKEN"] = InterAgentServer.shared.authToken
        }
        if let socketPath = InterAgentServer.shared.currentSocketPath {
            env["MAESTRI_SOCKET"] = socketPath
        }

        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var candidatePaths: [String] = []

        // 1. ~/.nvm/versions/node/*/bin (按版本降序排列，新版本优先)
        let nvmNodeDir = "\(home)/.nvm/versions/node"
        if let versions = try? FileManager.default.contentsOfDirectory(atPath: nvmNodeDir) {
            for version in versions.sorted().reversed() {
                candidatePaths.append("\(nvmNodeDir)/\(version)/bin")
            }
        }

        // 2. ~/.cargo/bin
        candidatePaths.append("\(home)/.cargo/bin")

        // 3. ~/.local/bin
        candidatePaths.append("\(home)/.local/bin")

        // 4. 继承已有的 PATH 环境变量
        if let existing = env["PATH"] {
            candidatePaths.append(contentsOf: existing.split(separator: ":").map(String.init))
        }

        // 5. Linux 系统标准二进制目录
        let systemPaths = [
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin"
        ]
        candidatePaths.append(contentsOf: systemPaths)

        // 规范化与去重：只保留存在的目录或系统标准路径，保持优先级顺序
        var resolvedPaths: [String] = []
        var seen = Set<String>()
        for p in candidatePaths {
            guard !p.isEmpty, !seen.contains(p) else { continue }
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: p, isDirectory: &isDir), isDir.boolValue {
                seen.insert(p)
                resolvedPaths.append(p)
            }
        }

        // 确保无论如何包含标准基础系统 PATH
        for fallback in systemPaths {
            if !seen.contains(fallback) {
                seen.insert(fallback)
                resolvedPaths.append(fallback)
            }
        }

        // K5/C1: the `omaestri` CLI is reachable as $MAESTRI_CLI and through PATH (its directory first).
        if let cli = Self.omaestriPath() {
            env["MAESTRI_CLI"] = cli
            env["OMAESTRI_CLI"] = cli
            let dir = (cli as NSString).deletingLastPathComponent
            resolvedPaths.removeAll { $0 == dir }
            resolvedPaths.insert(dir, at: 0)
        }

        env["PATH"] = resolvedPaths.joined(separator: ":")
        for (k, v) in extraEnvironment { env[k] = v }

        #if canImport(Glibc)
        let pid = fork()
        #elseif canImport(Darwin)
        let pid = Darwin.fork()
        #endif

        if pid < 0 {
            close(master)
            close(slave)
            let err = errno
            throw NSError(domain: "POSIXPtyProcess", code: Int(err),
                          userInfo: [NSLocalizedDescriptionKey: "fork failed: \(String(cString: strerror(err)))"])
        }

        if pid == 0 {
            // 子进程
            close(master)
            #if canImport(Glibc)
            _ = setsid()
            _ = login_tty(slave)
            #elseif canImport(Darwin)
            _ = Darwin.setsid()
            _ = Darwin.login_tty(slave)
            #endif

            if !workingDirectory.isEmpty && FileManager.default.fileExists(atPath: workingDirectory) {
                _ = chdir(workingDirectory)
            }

            for (k, v) in env {
                #if canImport(Glibc)
                setenv(k, v, 1)
                #elseif canImport(Darwin)
                Darwin.setenv(k, v, 1)
                #endif
            }

            // 转换 argv
            var cArgs = execArgs.map { strdup($0) }
            cArgs.append(nil)

            #if canImport(Glibc)
            execvp(execPath, &cArgs)
            #elseif canImport(Darwin)
            Darwin.execvp(execPath, &cArgs)
            #endif

            _exit(127)
        }

        // 父进程
        close(slave)
        self.childPid = pid
        self.masterFd = master
        self.isRunning = true
        self.startedAt = Date()

        // 设置非阻塞
        let flags = fcntl(master, F_GETFL, 0)
        _ = fcntl(master, F_SETFL, flags | O_NONBLOCK)

        // 监听输出
        let source = DispatchSource.makeReadSource(fileDescriptor: master, queue: .global(qos: .userInitiated))
        source.setEventHandler { [weak self] in
            self?.readFromMaster()
        }
        source.setCancelHandler { [master] in
            close(master)
        }
        source.resume()
        self.readSource = source

        logger.debug("POSIX PTY started for \(self.terminalId.uuidString.prefix(8)), pid=\(pid)")
    }

    private func readFromMaster() {
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            #if canImport(Glibc)
            let n = read(masterFd, &buffer, buffer.count)
            #elseif canImport(Darwin)
            let n = Darwin.read(masterFd, &buffer, buffer.count)
            #endif
            if n > 0 {
                let slice = buffer[..<n]
                headlessTerminal?.feed(byteArray: Array(slice))
                let str = String(decoding: slice, as: UTF8.self)
                if !str.isEmpty {
                    onDataReceived?(str)
                    listenerLock.lock()
                    let listeners = Array(outputListeners.values)
                    listenerLock.unlock()
                    for listener in listeners {
                        listener(str)
                    }
                }
            } else if n == 0 {
                handleChildTermination()
                break
            } else {
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    break
                }
                handleChildTermination()
                break
            }
        }
    }

    private func handleChildTermination() {
        guard isRunning else { return }
        isRunning = false
        readSource?.cancel()
        readSource = nil

        var status: Int32 = 0
        var exitCode: Int32? = nil
        if childPid > 0 {
            #if canImport(Glibc)
            if waitpid(childPid, &status, WNOHANG) > 0 {
                if (status & 0x7F) == 0 {
                    exitCode = (status >> 8) & 0xFF
                }
            }
            #elseif canImport(Darwin)
            if Darwin.waitpid(childPid, &status, WNOHANG) > 0 {
                if (status & 0x7F) == 0 {
                    exitCode = (status >> 8) & 0xFF
                }
            }
            #endif
        }
        self.exitCode = exitCode
        logger.info("POSIX PTY terminated for \(self.terminalId.uuidString.prefix(8)), exitCode=\(exitCode ?? -1)")
        onProcessExit?(exitCode)
    }

    func write(_ text: String) {
        guard isRunning, masterFd >= 0 else { return }
        text.utf8CString.withUnsafeBufferPointer { ptr in
            guard let base = ptr.baseAddress else { return }
            let len = ptr.count - 1
            guard len > 0 else { return }
            var written = 0
            while written < len {
                #if canImport(Glibc)
                let n = Glibc.write(masterFd, base + written, len - written)
                #elseif canImport(Darwin)
                let n = Darwin.write(masterFd, base + written, len - written)
                #endif
                if n <= 0 { break }
                written += n
            }
        }
    }

    func writeRaw(_ data: Data) {
        guard isRunning, masterFd >= 0 else { return }
        data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return }
            var written = 0
            let len = raw.count
            while written < len {
                #if canImport(Glibc)
                let n = Glibc.write(masterFd, base + written, len - written)
                #elseif canImport(Darwin)
                let n = Darwin.write(masterFd, base + written, len - written)
                #endif
                if n <= 0 { break }
                written += n
            }
        }
    }

    func resize(cols: Int, rows: Int) {
        guard masterFd >= 0, cols > 0, rows > 0, cols <= 1000, rows <= 1000 else { return }
        self.cols = cols
        self.rows = rows
        var ws = winsize(ws_row: UInt16(rows), ws_col: UInt16(cols), ws_xpixel: 0, ws_ypixel: 0)
        #if canImport(Glibc)
        _ = ioctl(masterFd, UInt(TIOCSWINSZ), &ws)
        #elseif canImport(Darwin)
        _ = Darwin.ioctl(masterFd, UInt(TIOCSWINSZ), &ws)
        #endif
        headlessTerminal?.resize(cols: cols, rows: rows)
    }

    /// Restored scrollback (K13): fed to the headless buffer before the shell starts, so the replay to a reconnecting
    /// window shows the previous session first.
    func preload(_ text: String) {
        guard !text.isEmpty else { return }
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\n", with: "\r\n")
        headlessTerminal?.feed(text: normalized + "\r\n")
    }

    /// `omaestri` to expose to the shell: $OMAESTRI_PATH, next to this server binary, then ~/.local/bin.
    static func omaestriPath() -> String? {
        let fm = FileManager.default
        var candidates: [String] = []
        if let env = ProcessInfo.processInfo.environment["OMAESTRI_PATH"], !env.isEmpty { candidates.append(env) }
        let exe = URL(fileURLWithPath: CommandLine.arguments.first ?? "").resolvingSymlinksInPath()
        candidates.append(exe.deletingLastPathComponent().appendingPathComponent("omaestri").path)
        candidates.append(fm.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/omaestri").path)
        return candidates.first { fm.isExecutableFile(atPath: $0) }
    }

    /// 获取 headlessTerminal 当前缓冲区的完整纯文本快照
    func bufferText() -> String? {
        guard let data = headlessTerminal?.getBufferAsData(), !data.isEmpty else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    func terminate() {
        guard isRunning else { return }
        isRunning = false
        readSource?.cancel()
        readSource = nil
        if childPid > 0 {
            #if canImport(Glibc)
            kill(childPid, SIGTERM)
            #elseif canImport(Darwin)
            Darwin.kill(childPid, SIGTERM)
            #endif
        }
        if masterFd >= 0 {
            close(masterFd)
            masterFd = -1
        }
    }

    deinit {
        terminate()
    }
}

/// SwiftTerm TerminalDelegate 简单空实现
private final class HeadlessTerminalDelegate: TerminalDelegate, @unchecked Sendable {
    func send(source: Terminal, data: ArraySlice<UInt8>) {}
}
