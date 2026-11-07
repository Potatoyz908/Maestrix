import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// `omaestri ask` (contract K4):
///   ask "Name" "prompt"                       type the prompt, wait 150 ms, press Enter separately, wait for the answer
///   ask "Name" --raw "2\n"                    send raw input (\n Enter, \t Tab, \e ESC, \xNN byte), then wait
///   ask --batch '{"A": "p1", "B": "p2"}'      ask several connected agents in parallel -> JSON [{name, output|error}]
///   ask --batch "A:p1" "B:p2"                 same, positional form
/// Waiting: an agent terminal (agentType != generic_shell) answers when its output has been quiet for
/// `agentQuietSeconds` after it started reacting (ceiling 20 min); a shell answers when its prompt is back (ceiling
/// 30 s). The reply is the screen text produced since the prompt was sent (escapes removed).
final class AskHandler {
    static let shared = AskHandler()
    private let logger = Logger.make(category: "AskHandler")
    private init() {}

    static let enterDelay: Duration = .milliseconds(150)
    static let agentQuietSeconds: TimeInterval = 3
    static let agentCeiling: TimeInterval = 20 * 60
    static let shellCeiling: TimeInterval = 30
    static let replyLineLimit = 200

    enum Request: Equatable {
        case single(name: String, text: String, raw: Bool)
        case batch([(name: String, prompt: String)])

        static func == (l: Request, r: Request) -> Bool {
            switch (l, r) {
            case let (.single(a, b, c), .single(x, y, z)): return a == x && b == y && c == z
            case let (.batch(a), .batch(b)): return a.map(\.name) == b.map(\.name) && a.map(\.prompt) == b.map(\.prompt)
            default: return false
            }
        }
    }

    /// Parse `args` (args[0] == "ask"). Returns an error string for bad usage.
    static func parse(_ args: [String]) -> Result<Request, AskError> {
        let rest = Array(args.dropFirst())
        let usage = "error: usage: omaestri ask \"Agent Name\" \"prompt\" | ask \"Agent Name\" --raw \"text\" | ask --batch '{\"Agent\": \"prompt\"}'"
        if let i = rest.firstIndex(of: "--batch") {
            let items = Array(rest[(i + 1)...])
            guard !items.isEmpty else { return .failure(AskError(message: usage)) }
            if items.count == 1, let data = items[0].data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let pairs = obj.keys.sorted().compactMap { k in (obj[k] as? String).map { (name: k, prompt: $0) } }
                return pairs.isEmpty ? .failure(AskError(message: usage)) : .success(.batch(pairs))
            }
            var pairs: [(name: String, prompt: String)] = []
            for item in items {
                guard let colon = item.firstIndex(of: ":") else {
                    return .failure(AskError(message: "error: --batch expects a JSON object {\"Agent\": \"prompt\"} or \"Agent:prompt\" items"))
                }
                pairs.append((String(item[..<colon]), String(item[item.index(after: colon)...])))
            }
            guard Set(pairs.map(\.name)).count == pairs.count else { return .failure(AskError(message: "error: --batch targets must be distinct")) }
            return .success(.batch(pairs))
        }
        let raw = rest.contains("--raw")
        let positional = rest.filter { $0 != "--raw" }
        guard positional.count >= 2 else { return .failure(AskError(message: usage)) }
        return .success(.single(name: positional[0], text: positional[1], raw: raw))
    }

    @MainActor
    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard let caller = terminalId else { return "error: missing terminal ID" }
        switch Self.parse(args) {
        case .failure(let err):
            return err.message
        case .success(.single(let name, let text, let raw)):
            switch await ask(name, text: text, raw: raw, from: caller) {
            case .success(let out): return out
            case .failure(let err): return "error: \(err.message)"
            }
        case .success(.batch(let pairs)):
            let results = await withTaskGroup(of: (Int, [String: String]).self) { group in
                for (i, p) in pairs.enumerated() {
                    group.addTask { @MainActor in
                        switch await self.ask(p.name, text: p.prompt, raw: false, from: caller) {
                        case .success(let out): return (i, ["name": p.name, "output": out])
                        case .failure(let err): return (i, ["name": p.name, "error": err.message])
                        }
                    }
                }
                var acc: [(Int, [String: String])] = []
                for await r in group { acc.append(r) }
                return acc.sorted { $0.0 < $1.0 }.map(\.1)
            }
            let data = (try? JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])) ?? Data("[]".utf8)
            return String(decoding: data, as: UTF8.self)
        }
    }

    struct AskError: Error { let message: String }

    /// Send `text` to the connected agent `name` and wait for its answer.
    @MainActor
    func ask(_ name: String, text: String, raw: Bool, from caller: UUID) async -> Result<String, AskError> {
        guard let agent = AgentDirectory.resolve(name, from: caller) else {
            return .failure(AskError(message: "agent '\(name)' not found. Use 'omaestri list' to see connected agents."))
        }
        guard let tm = PlatformHooks.terminalsService, let session = tm.terminals[agent.terminalId] else {
            return .failure(AskError(message: "agent '\(agent.name)' is not running"))
        }
        let cm = ConnectionManager.shared
        let me = cm.canonical(caller)
        let connId = cm.connections(for: me).first { Set([$0.nodeIdA, $0.nodeIdB]) == Set([me, agent.terminalId]) }?.id
        if let connId { cm.updateStatus(.communicating, for: connId) }
        defer { if let connId { cm.updateStatus(.idle, for: connId) } }

        let tid = agent.terminalId
        let before = tm.bufferText(for: tid) ?? ""
        let activity = OutputActivity()
        let unsubscribe = tm.observeOutput(for: tid) { _ in activity.touch() }
        defer { unsubscribe?() }

        session.markActiveTask()
        if raw {
            let bytes = TerminalText.decodeRaw(text)
            if let linux = tm as? LinuxTerminalManager {
                linux.writeRaw(to: tid, data: bytes)
            } else {
                tm.write(to: tid, text: String(decoding: bytes, as: UTF8.self))
            }
        } else {
            // TUIs treat text + CR in one write as a paste: type first, press Enter separately
            tm.write(to: tid, text: text.trimmingCharacters(in: .newlines))
            try? await Task.sleep(for: Self.enterDelay)
            activity.reset()
            tm.write(to: tid, text: "\r")
        }
        logger.debug("ask -> \(tid.uuidString.prefix(8)): \(text.prefix(50))")

        let isAgent = session.agentType != "generic_shell"
        if isAgent {
            await waitForQuiet(activity, quiet: Self.agentQuietSeconds, ceiling: Self.agentCeiling)
        } else {
            await waitForPrompt(tm: tm, tid: tid, before: before, activity: activity, ceiling: Self.shellCeiling)
        }
        if isAgent {
            EventBroadcaster.shared.publish(type: "terminalBecameIdle", workspaceId: tm.terminalWorkspaceMap[tid],
                                            payload: ["terminalId": tid.uuidString])
        }
        return .success(Self.reply(before: before, after: tm.bufferText(for: tid) ?? session.recentOutput(lines: 80)))
    }

    /// Screen text produced after `before`: the new lines when the buffer grew in place, otherwise (a TUI redrew the
    /// screen) its last `replyLineLimit` lines.
    static func reply(before: String, after: String) -> String {
        let b = TerminalText.tidy(before).components(separatedBy: "\n")
        let a = TerminalText.tidy(after).components(separatedBy: "\n")
        var common = 0
        while common < min(a.count, b.count) - 1, a[common] == b[common] { common += 1 }
        let fresh = common > 0 ? Array(a[common...]) : a
        return Array(fresh.suffix(replyLineLimit)).joined(separator: "\n")
    }

    // MARK: - Waiting

    /// Thread-safe "last output" clock fed by the PTY output thread.
    final class OutputActivity: @unchecked Sendable {
        private let lock = NSLock()
        private var last: Date?
        func touch() { lock.lock(); last = Date(); lock.unlock() }
        func reset() { lock.lock(); last = nil; lock.unlock() }
        var lastOutput: Date? { lock.lock(); defer { lock.unlock() }; return last }
    }

    /// Agent strategy: done once output started after the prompt and then stayed quiet for `quiet` seconds.
    @MainActor
    private func waitForQuiet(_ activity: OutputActivity, quiet: TimeInterval, ceiling: TimeInterval) async {
        let deadline = Date().addingTimeInterval(ceiling)
        while Date() < deadline {
            try? await Task.sleep(for: .milliseconds(250))
            if let last = activity.lastOutput, Date().timeIntervalSince(last) >= quiet { return }
        }
    }

    /// Shell strategy: done when new output ends with a prompt (`$ % > ❯ #`) and has been quiet briefly.
    @MainActor
    private func waitForPrompt(tm: any TerminalManaging, tid: UUID, before: String, activity: OutputActivity,
                               ceiling: TimeInterval) async {
        let deadline = Date().addingTimeInterval(ceiling)
        let baseline = TerminalText.tidy(before).components(separatedBy: "\n").count
        while Date() < deadline {
            try? await Task.sleep(for: .milliseconds(100))
            guard let last = activity.lastOutput, Date().timeIntervalSince(last) >= 0.3 else { continue }
            let text = TerminalText.tidy(tm.bufferText(for: tid) ?? "")
            let lines = text.components(separatedBy: "\n")
            guard lines.count > baseline || text != TerminalText.tidy(before) else { continue }
            if let tail = lines.last?.trimmingCharacters(in: .whitespaces), Self.looksLikePrompt(tail) { return }
        }
    }

    static func looksLikePrompt(_ line: String) -> Bool {
        guard let last = line.last else { return false }
        return "$%>❯#".contains(last)
    }
}
