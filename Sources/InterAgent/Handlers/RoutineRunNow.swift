import Foundation

/// `POST /api/routines/:id/run-now` (audit R13): each prompt is typed, then Enter is sent SEPARATELY 150 ms later
/// (a single write of text + CR is taken as a paste by Claude/Codex TUIs and never submitted). The next prompt of a
/// `&&` chain only goes after the terminal settles: agents wait for 3 s of output silence (cap 20 min), shells for the
/// prompt to come back (cap 30 s). Runs in the background; progress is published as routineStarted/Step/Finished/Failed.
enum RoutineRunNow {
    static let enterDelay: UInt64 = 150_000_000
    static let agentQuiet: TimeInterval = 3
    static let agentCap: TimeInterval = 20 * 60
    static let shellCap: TimeInterval = 30

    /// false when the target terminal has no live PTY (the caller answers 404 and nothing is typed).
    @MainActor
    static func start(_ routine: Routine) -> Bool {
        let tm = LinuxTerminalManager.shared
        guard let session = tm.terminals[routine.targetTerminalId] else {
            EventBroadcaster.shared.publish(type: "routineFailed", payload: ["routineId": routine.id.uuidString,
                                                                             "reason": "target terminal is not running"])
            return false
        }
        let isAgent = session.agentType != "generic_shell"
        let tid = routine.targetTerminalId
        let rid = routine.id.uuidString
        let n = routine.prompts.count
        Task { @MainActor in
            EventBroadcaster.shared.publish(type: "routineStarted", payload: ["routineId": rid, "n": n])
            for (i, prompt) in routine.prompts.enumerated() {
                guard tm.terminals[tid] != nil else {
                    EventBroadcaster.shared.publish(type: "routineFailed", payload: ["routineId": rid, "reason": "target terminal exited"])
                    return
                }
                var last = Date()
                let stop = tm.observeOutput(for: tid) { _ in DispatchQueue.main.async { last = Date() } }
                tm.write(to: tid, text: prompt)
                try? await Task.sleep(nanoseconds: enterDelay)
                tm.write(to: tid, text: "\r")
                last = Date()
                EventBroadcaster.shared.publish(type: "routineStep", payload: ["routineId": rid, "i": i + 1, "n": n])
                if i < n - 1 {
                    let started = Date()
                    let cap = isAgent ? agentCap : shellCap
                    while Date().timeIntervalSince(started) < cap {
                        try? await Task.sleep(nanoseconds: 250_000_000)
                        let quiet = Date().timeIntervalSince(last)
                        if isAgent ? quiet >= agentQuiet : (quiet >= 0.5 && promptReturned(tm.bufferText(for: tid))) { break }
                    }
                }
                stop?()
            }
            EventBroadcaster.shared.publish(type: "routineFinished", payload: ["routineId": rid])
        }
        return true
    }

    /// Last non-empty line ends like a shell prompt.
    static func promptReturned(_ text: String?) -> Bool {
        guard let line = text?.split(separator: "\n").last(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) else { return false }
        let t = line.trimmingCharacters(in: .whitespaces)
        return ["$", "%", ">", "#", "❯"].contains { t.hasSuffix($0) }
    }
}
