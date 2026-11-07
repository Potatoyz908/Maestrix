import Foundation

/// Remote (SSH) workspaces, phase A (docs/linux-ssh-spec.md): host list from ~/.ssh/config, connection test, remote
/// folder listing and remote terminals over `ssh -tt` with a shared ControlMaster connection.
/// Security: every field is validated before any argv is built; the target goes after `--`; remote path and command
/// are single-quoted for the REMOTE shell; nothing runs through a local shell (Process / exec with an argv).

/// Optional `ssh` object of a workspace (payload and manifest entry). Absent for local workspaces, so the Maestri
/// schema is unchanged for them.
struct SSHTarget: Codable, Equatable {
    var host: String
    var user: String?
    var port: Int?
    var identityFile: String?
    var remotePath: String?

    enum Invalid: Error, CustomStringConvertible {
        case field(String, String)
        var description: String {
            if case .field(let name, let why) = self { return "invalid \(name): \(why)" }
            return "invalid"
        }
    }

    private static let allowed = CharacterSet(charactersIn:
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._@:-")

    static func checkName(_ value: String, _ field: String) throws {
        guard !value.isEmpty else { throw Invalid.field(field, "empty") }
        guard !value.hasPrefix("-") else { throw Invalid.field(field, "must not start with '-'") }
        guard value.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
            throw Invalid.field(field, "only letters, digits and . _ @ : - are allowed")
        }
    }

    /// Validates every field (throws before anything is executed). Returns the target with `identityFile` expanded.
    func validated() throws -> SSHTarget {
        var t = self
        try Self.checkName(host, "host")
        if let u = user, !u.isEmpty { try Self.checkName(u, "user") } else { t.user = nil }
        if let p = port { guard (1...65535).contains(p) else { throw Invalid.field("port", "must be 1-65535") } }
        if let f = identityFile, !f.isEmpty {
            guard !f.hasPrefix("-") else { throw Invalid.field("identityFile", "must not start with '-'") }
            let expanded = (f as NSString).expandingTildeInPath
            guard FileManager.default.fileExists(atPath: expanded) else { throw Invalid.field("identityFile", "file not found") }
            t.identityFile = expanded
        } else {
            t.identityFile = nil
        }
        if let r = remotePath, r.contains("\0") { throw Invalid.field("remotePath", "contains NUL") }
        return t
    }

    /// `user@host` or `host`.
    var destination: String { user.map { "\($0)@\(host)" } ?? host }

    /// Parse from a JSON object (REST body / query); nil when there is no host.
    init?(json: [String: Any]) {
        guard let host = (json["host"] as? String)?.trimmingCharacters(in: .whitespaces), !host.isEmpty else { return nil }
        self.host = host
        user = (json["user"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        port = (json["port"] as? Int) ?? (json["port"] as? String).flatMap(Int.init)
        identityFile = (json["identityFile"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        remotePath = (json["remotePath"] as? String).flatMap { $0.isEmpty ? nil : $0 }
    }

    init(host: String, user: String? = nil, port: Int? = nil, identityFile: String? = nil, remotePath: String? = nil) {
        self.host = host; self.user = user; self.port = port; self.identityFile = identityFile; self.remotePath = remotePath
    }

    var json: [String: Any] {
        var d: [String: Any] = ["host": host]
        if let user { d["user"] = user }
        if let port { d["port"] = port }
        if let identityFile { d["identityFile"] = identityFile }
        if let remotePath { d["remotePath"] = remotePath }
        return d
    }
}

/// One concrete `Host` block of an ssh config.
struct SSHHostEntry: Equatable {
    var alias: String
    var hostName: String?
    var user: String?
    var port: Int?
    var identityFile: String?

    var json: [String: Any] {
        var d: [String: Any] = ["alias": alias]
        if let hostName { d["hostName"] = hostName }
        if let user { d["user"] = user }
        if let port { d["port"] = port }
        if let identityFile { d["identityFile"] = identityFile }
        return d
    }
}

enum SSHConfigParser {
    /// `$OPEN_MAESTRI_SSH_CONFIG` or ~/.ssh/config.
    static var defaultPath: String {
        if let p = ProcessInfo.processInfo.environment["OPEN_MAESTRI_SSH_CONFIG"], !p.isEmpty { return p }
        return (NSHomeDirectory() as NSString).appendingPathComponent(".ssh/config")
    }

    /// Hosts of `path` (following `Include`, relative to the config's directory), skipping `*`/`?`/`!` patterns.
    static func hosts(path: String = defaultPath) -> [SSHHostEntry] {
        var out: [SSHHostEntry] = []
        var seen = Set<String>()
        parse(path, depth: 0, into: &out, seen: &seen)
        return out
    }

    private static func parse(_ path: String, depth: Int, into out: inout [SSHHostEntry], seen: inout Set<String>) {
        guard depth < 8, let text = try? String(contentsOfFile: path, encoding: .utf8) else { return }
        let baseDir = (path as NSString).deletingLastPathComponent
        var current: [Int] = []          // indexes in `out` of the Host block being read
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#") else { continue }
            let parts = line.split(maxSplits: 1, whereSeparator: { $0 == " " || $0 == "\t" || $0 == "=" })
            guard parts.count == 2 else { continue }
            let key = parts[0].lowercased()
            let value = parts[1].trimmingCharacters(in: CharacterSet(charactersIn: " \t=\""))
            switch key {
            case "include":
                for pattern in value.split(separator: " ") {
                    var p = (String(pattern) as NSString).expandingTildeInPath
                    if !p.hasPrefix("/") { p = (baseDir as NSString).appendingPathComponent(p) }
                    for file in expandGlob(p) { parse(file, depth: depth + 1, into: &out, seen: &seen) }
                }
                current = []
            case "host":
                current = []
                for name in value.split(separator: " ").map(String.init)
                where !name.contains("*") && !name.contains("?") && !name.hasPrefix("!") && !seen.contains(name) {
                    seen.insert(name)
                    out.append(SSHHostEntry(alias: name))
                    current.append(out.count - 1)
                }
            case "match":
                current = []
            case "hostname": for i in current where out[i].hostName == nil { out[i].hostName = value }
            case "user": for i in current where out[i].user == nil { out[i].user = value }
            case "port": for i in current where out[i].port == nil { out[i].port = Int(value) }
            case "identityfile": for i in current where out[i].identityFile == nil { out[i].identityFile = value }
            default: break
            }
        }
    }

    private static func expandGlob(_ pattern: String) -> [String] {
        guard pattern.contains("*") || pattern.contains("?") else { return [pattern] }
        let dir = (pattern as NSString).deletingLastPathComponent
        let filePattern = (pattern as NSString).lastPathComponent
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
        return names.filter { fnmatch(filePattern, $0, 0) == 0 }.sorted().map { (dir as NSString).appendingPathComponent($0) }
    }
}

enum SSHCommandBuilder {
    /// `$OPEN_MAESTRI_SSH_BIN` (tests use a fake) or `ssh`.
    static var binary: String {
        if let p = ProcessInfo.processInfo.environment["OPEN_MAESTRI_SSH_BIN"], !p.isEmpty { return p }
        return "ssh"
    }

    /// POSIX single-quoting for the remote shell.
    static func quote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Remote `cd` target: `~` / `~/x` stay relative to the remote home (quoted after the `~/`).
    static func remoteCd(_ path: String?) -> String {
        guard let p = path?.trimmingCharacters(in: .whitespaces), !p.isEmpty, p != "~" else { return "cd" }
        if p.hasPrefix("~/") { return "cd ~/" + quote(String(p.dropFirst(2))) }
        return "cd -- " + quote(p)
    }

    private static func options(_ t: SSHTarget) -> [String] {
        var a: [String] = []
        if let p = t.port { a += ["-p", String(p)] }
        if let f = t.identityFile { a += ["-i", f] }
        return a
    }

    /// Non-interactive probe: `ssh -o BatchMode=yes -o ConnectTimeout=8 [-p][-i] -- dest true`.
    static func testArgv(_ t: SSHTarget) -> [String] {
        [binary, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"] + options(t) + ["--", t.destination, "true"]
    }

    /// `cd -- '<path>' && pwd && ls -1Ap` on the remote (BatchMode).
    static func listDirArgv(_ t: SSHTarget, path: String?) -> [String] {
        [binary, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"] + options(t) +
            ["--", t.destination, "\(remoteCd(path)) && pwd && ls -1Ap"]
    }

    /// Interactive remote terminal sharing one master connection per host (ControlMaster).
    static func terminalArgv(_ t: SSHTarget, command: String, controlDir: String) -> [String] {
        let cmd = command.trimmingCharacters(in: .whitespacesAndNewlines)
        let isShell = cmd.isEmpty || ["bash", "zsh", "sh", "fish"].contains(cmd)
        let remote = remoteCd(t.remotePath) + " && " + (isShell ? "exec \"${SHELL:-bash}\" -l" : cmd)
        return [binary, "-tt",
                "-o", "ControlMaster=auto", "-o", "ControlPath=\(controlDir)/ssh-%C",
                "-o", "ControlPersist=10m", "-o", "ServerAliveInterval=30"] + options(t) +
            ["--", t.destination, remote]
    }

    /// `<dataDir>/run` (ControlPath sockets live next to agent.sock, 0700).
    static var controlDir: String {
        let dir = PersistenceManager.shared.appDataURL.appendingPathComponent("run").path
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        return dir
    }

    struct Output { let status: Int32; let stdout: String; let stderr: String; let timedOut: Bool }

    /// Run an argv (no shell), stdin closed, with a timeout.
    static func run(_ argv: [String], timeout: TimeInterval = 15) -> Output {
        let p = Process()
        if argv[0].contains("/") {
            p.executableURL = URL(fileURLWithPath: argv[0])
            p.arguments = Array(argv.dropFirst())
        } else {
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = argv          // /usr/bin/env looks argv[0] up in PATH; no shell involved
        }
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        p.standardInput = FileHandle.nullDevice
        do { try p.run() } catch {
            return Output(status: 127, stdout: "", stderr: "cannot run \(argv[0]): \(error.localizedDescription)", timedOut: false)
        }
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global().async { p.waitUntilExit(); done.signal() }
        var timedOut = false
        if done.wait(timeout: .now() + timeout) == .timedOut { timedOut = true; p.terminate(); _ = done.wait(timeout: .now() + 2) }
        let o = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let e = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return Output(status: timedOut ? 124 : p.terminationStatus, stdout: o, stderr: e, timedOut: timedOut)
    }

    /// Whether an ssh failure means "a password / passphrase / key is needed" (the UI then asks for one).
    static func needsPassword(_ stderr: String) -> Bool {
        let s = stderr.lowercased()
        return s.contains("permission denied") || s.contains("password") || s.contains("passphrase")
            || s.contains("no more authentication methods")
    }
}
