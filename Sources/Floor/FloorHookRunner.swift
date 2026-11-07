import Foundation

/// K12 — floor hooks on the server (macOS parity: `HooksManager` + `FloorOverviewView`).
///
/// * Setup runs after `POST /api/floors` when `hooks.autoRunSetup` is on (macOS `runSetupHooks` guard).
/// * Teardown runs on `DELETE /api/floors/:id`, before the worktree is removed (macOS order).
/// * `POST /api/floors/:id/hooks/run {phase, hookId?}` runs one phase (or one command of it) on demand.
/// Each command runs `sh -c` inside the floor worktree with the MAESTRI_* floor variables; its exit code and output
/// are returned and published as `floorHookFinished {floorId, phase, index, command, exitCode, output}`.
enum FloorHookRunner {
    struct Outcome {
        let index: Int
        let command: String
        let exitCode: Int32
        let output: String

        var json: [String: Any] { ["index": index, "command": command, "exitCode": Int(exitCode), "output": output] }
    }

    static let phases = ["setup", "run", "teardown"]
    /// Output kept per command (tail), so a chatty install script cannot flood the event stream.
    static let outputLimit = 16_000

    static func commands(_ hooks: FloorHooks, phase: String) -> [String] {
        switch phase {
        case "setup": return hooks.setup
        case "run": return hooks.run
        case "teardown": return hooks.teardown
        default: return []
        }
    }

    /// Repository root the floor was created from (the worktree lives in `<root>/.open-maestri/floors/<slug>`).
    static func rootDirectory(of entry: FloorEntry, fallback: String) -> String {
        InterAgentServer.floorRepoDirectory(entry: entry, fallback: fallback)
    }

    /// MAESTRI_FLOOR_NAME / BRANCH_NAME / FLOOR_PATH / ROOT_PATH / PROJECT_NAME (+ OMAESTRI_*), as on macOS.
    static func environment(for entry: FloorEntry, rootDirectory: String) -> [String: String] {
        FloorManager.hookEnvironment(for: InterAgentServer.floor(from: entry, workingDirectory: rootDirectory),
                                     workingDirectory: rootDirectory)
    }

    /// Floor variables for a terminal created with `floorId` (K12), or nil when the floor is unknown.
    static func terminalEnvironment(floorId: UUID, workspaceId: UUID?) -> [String: String]? {
        let pm = PersistenceManager.shared
        var order = NexusWorkspaces.allWorkspaceIds()
        if let ws = workspaceId, let i = order.firstIndex(of: ws) { order.remove(at: i); order.insert(ws, at: 0) }
        for wsId in order {
            guard let doc = try? pm.loadWorkspace(id: wsId),
                  let entry = doc.payload.floors.first(where: { $0.id == floorId }) else { continue }
            return environment(for: entry, rootDirectory: rootDirectory(of: entry, fallback: doc.payload.workingDirectory))
        }
        return nil
    }

    /// Runs `phase` (all commands, or only `index`) and publishes one event per command. Stops at the first failure,
    /// like a shell `&&` chain, so a failed `npm install` does not run the next step.
    @discardableResult
    static func run(phase: String, index: Int? = nil, entry: FloorEntry, rootDirectory: String, workspaceId: UUID) -> [Outcome] {
        let all = commands(entry.hooks, phase: phase)
        let picked: [(Int, String)] = index.map { i in all.indices.contains(i) ? [(i, all[i])] : [] }
            ?? Array(all.enumerated()).map { ($0.offset, $0.element) }
        let env = ProcessInfo.processInfo.environment.merging(environment(for: entry, rootDirectory: rootDirectory)) { _, new in new }
        let cwd = FileManager.default.fileExists(atPath: entry.worktreePath) ? entry.worktreePath : rootDirectory
        var outcomes: [Outcome] = []
        for (i, cmd) in picked where !cmd.trimmingCharacters(in: .whitespaces).isEmpty {
            let outcome = execute(cmd, index: i, cwd: cwd, environment: env)
            outcomes.append(outcome)
            var payload = outcome.json
            payload["floorId"] = entry.id.uuidString
            payload["phase"] = phase
            EventBroadcaster.shared.publish(type: "floorHookFinished", workspaceId: workspaceId, payload: payload)
            if outcome.exitCode != 0 { break }
        }
        return outcomes
    }

    static func execute(_ command: String, index: Int, cwd: String, environment: [String: String]) -> Outcome {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", command]
        process.currentDirectoryURL = URL(fileURLWithPath: cwd)
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.standardInput = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return Outcome(index: index, command: command, exitCode: 127, output: "failed to start: \(error.localizedDescription)")
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()   // drains while running: no pipe deadlock
        process.waitUntilExit()
        var text = String(decoding: data, as: UTF8.self)
        if text.count > outputLimit { text = "…" + String(text.suffix(outputLimit)) }
        return Outcome(index: index, command: command, exitCode: process.terminationStatus, output: text)
    }

    /// After POST /api/floors: Setup in the background when Auto-run is on.
    static func runSetupIfAutomatic(entry: FloorEntry, rootDirectory: String, workspaceId: UUID) {
        guard entry.hooks.autoRunSetup, !entry.hooks.setup.isEmpty else { return }
        DispatchQueue.global(qos: .utility).async {
            run(phase: "setup", entry: entry, rootDirectory: rootDirectory, workspaceId: workspaceId)
        }
    }

    /// Before DELETE /api/floors/:id removes the worktree (runs while the worktree still exists).
    static func runTeardown(entry: FloorEntry, rootDirectory: String, workspaceId: UUID) async {
        guard !entry.hooks.teardown.isEmpty else { return }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            DispatchQueue.global(qos: .utility).async {
                run(phase: "teardown", entry: entry, rootDirectory: rootDirectory, workspaceId: workspaceId)
                cont.resume()
            }
        }
    }
}
