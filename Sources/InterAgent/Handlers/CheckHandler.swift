import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// `omaestri check "Agent" [lines]`: the agent's current screen as plain text (default 20 lines). Reads the rendered
/// terminal screen, so a TUI's cursor movement and redraws come out as readable lines instead of escape noise
/// (audit C4); falls back to the raw output ring with escapes removed.
final class CheckHandler {
    static let shared = CheckHandler()
    private let logger = Logger.make(category: "CheckHandler")
    private init() {}

    @MainActor
    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard args.count >= 2 else {
            return "error: usage: omaestri check \"TargetAgent\" [lines]"
        }
        let economy = AgentEconomy.isOn()
        let lineCount = args.count >= 3 ? max(1, Int(args[2]) ?? 20) : (economy ? AgentEconomy.checkLineLimit : 20)
        guard let caller = terminalId else { return "error: missing terminal ID" }
        guard let agent = AgentDirectory.resolve(args[1], from: caller) else {
            return "error: " + AgentDirectory.notFound(args[1], from: caller)
        }
        guard let tm = PlatformHooks.terminalsService, let session = tm.terminals[agent.terminalId] else {
            return "error: agent '\(agent.name)' is not running"
        }
        let screen = tm.bufferText(for: agent.terminalId) ?? session.recentOutput(lines: lineCount * 4)
        let tidy = TerminalText.tidy(screen)
        let text = economy ? AgentEconomy.compact(tidy, limit: lineCount) : TerminalText.lastLines(tidy, lineCount)
        return text.isEmpty ? "(no recent output)" : text
    }
}
