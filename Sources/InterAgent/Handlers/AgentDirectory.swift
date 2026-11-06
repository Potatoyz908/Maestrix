import Foundation

/// Names and scope for the CLI handlers (`ask`, `check`, `list`, `connect`…): which agents a terminal may reach
/// (its connections, contract C12) and what they are called. Names come from the canvas (`workspace.json`) first,
/// so they match what the user sees, then from the live PTY session.
@MainActor
enum AgentDirectory {
    struct Agent {
        let terminalId: UUID
        let name: String
        let role: String?
        let session: TerminalSession?
    }

    /// Workspace document of the caller terminal (node id or terminal id).
    static func graph(of terminal: UUID) -> WorkspaceGraph? {
        let tid = ConnectionManager.shared.canonical(terminal)
        let hint = PlatformHooks.terminalsService?.terminalWorkspaceMap[tid]
        return WorkspaceGraph.containing(terminal: tid, hint: hint)
    }

    static func agent(_ terminalId: UUID, graph: WorkspaceGraph?) -> Agent {
        let session = PlatformHooks.terminalsService?.terminals[terminalId]
        let content = graph?.terminalNode(terminalId)?.content.terminalContent
        let canvasName = content?.name.trimmingCharacters(in: .whitespaces)
        let name = [session?.agentName, canvasName, session?.displayName,
                    session.map { $0.command.isEmpty ? "Shell" : $0.command }]
            .compactMap { $0 }.first { !$0.isEmpty } ?? "unknown"
        var role = session?.roleName
        if role == nil, let rid = content?.assignedRoleId {
            role = PersistenceManager.shared.loadPreferencesSync().rolePresets.first { $0.id == rid }?.name
        }
        return Agent(terminalId: terminalId, name: name, role: role, session: session)
    }

    /// Terminals connected to `caller` (terminal↔terminal links), in a stable order.
    static func connectedAgents(of caller: UUID) -> [Agent] {
        let cm = ConnectionManager.shared
        let me = cm.canonical(caller)
        let g = graph(of: me)
        return cm.connections(for: me).filter { $0.type == .terminalToTerminal }
            .map { $0.nodeIdA == me ? $0.nodeIdB : $0.nodeIdA }
            .map { agent($0, graph: g) }
    }

    /// Resolve `query` among the caller's connected agents: exact name, case-insensitive name, id prefix, substring.
    static func resolve(_ query: String, from caller: UUID) -> Agent? {
        let agents = connectedAgents(of: caller)
        let q = query.lowercased()
        return agents.first { $0.name == query }
            ?? agents.first { $0.name.lowercased() == q }
            ?? agents.first { q.count >= 4 && $0.terminalId.uuidString.lowercased().hasPrefix(q) }
            ?? agents.first { $0.name.lowercased().contains(q) }
            ?? agents.first { ($0.session?.command.lowercased().contains(q)) == true }
    }
}

/// Plain-text helpers for terminal output returned to agents.
enum TerminalText {
    /// Remove CSI / OSC / single-character escape sequences and other control characters (keeps \n and \t).
    static func stripAnsi(_ text: String) -> String {
        var out = String.UnicodeScalarView()
        var it = Array(text.unicodeScalars)[...]
        while let c = it.popFirst() {
            if c == "\u{1B}" {
                guard let n = it.popFirst() else { break }
                if n == "[" {
                    while let x = it.popFirst(), !(0x40...0x7E).contains(x.value) {}
                } else if n == "]" {
                    while let x = it.popFirst() {
                        if x == "\u{07}" { break }
                        if x == "\u{1B}" { _ = it.popFirst(); break }
                    }
                }
                continue
            }
            if c == "\r" { continue }
            if c.value < 0x20 && c != "\n" && c != "\t" { continue }
            out.append(c)
        }
        return String(out)
    }

    /// Readable screen text: no escapes, trailing spaces trimmed per line, runs of blank lines collapsed to one,
    /// no blank lines at the ends.
    static func tidy(_ text: String) -> String {
        var lines: [String] = []
        for raw in stripAnsi(text).components(separatedBy: "\n") {
            var line = raw
            while line.last == " " { line.removeLast() }
            if line.isEmpty && (lines.last?.isEmpty ?? true) { continue }
            lines.append(line)
        }
        while lines.last?.isEmpty == true { lines.removeLast() }
        return lines.joined(separator: "\n")
    }

    static func lastLines(_ text: String, _ n: Int) -> String {
        let lines = text.components(separatedBy: "\n")
        return lines.suffix(max(1, n)).joined(separator: "\n")
    }

    /// `--raw` escapes: \n Enter (CR), \r, \t Tab, \e ESC, \\ backslash, \xNN byte.
    static func decodeRaw(_ s: String) -> Data {
        var out = Data()
        var chars = Array(s.unicodeScalars)[...]
        while let c = chars.popFirst() {
            guard c == "\\", let n = chars.first else {
                out.append(contentsOf: Array(String(c).utf8)); continue
            }
            chars.removeFirst()
            switch n {
            case "n", "r": out.append(0x0D)
            case "t": out.append(0x09)
            case "e": out.append(0x1B)
            case "\\": out.append(0x5C)
            case "x":
                let hex = String(String.UnicodeScalarView(chars.prefix(2)))
                if hex.count == 2, let b = UInt8(hex, radix: 16) {
                    out.append(b); chars.removeFirst(2)
                } else {
                    out.append(contentsOf: Array("\\x".utf8))
                }
            default:
                out.append(0x5C); out.append(contentsOf: Array(String(n).utf8))
            }
        }
        return out
    }
}
