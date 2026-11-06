import Foundation
#if canImport(OSLog)
import OSLog
#endif

final class MaestroHandlers {
    static let shared = MaestroHandlers()
    private let logger = Logger.make(category: "MaestroHandlers")
    private init() {}

    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard let command = args.first else { return "error: empty command" }
        switch command {
        case "recruit": return await handleRecruitAsync(args: args, fromTerminalId: terminalId)
        case "dismiss": return await handleDismissAsync(args: args, fromTerminalId: terminalId)
        case "connect": return await handleConnectAsync(args: args, fromTerminalId: terminalId)
        case "role":    return await handleRoleAsync(args: args, fromTerminalId: terminalId)
        case "preset":  return await handlePresetAsync(args: args)
        default: return "error: unknown maestro command '\(command)'"
        }
    }

    // MARK: - recruit (K8)

    struct RecruitOptions {
        var name = ""
        var preset: String?
        var command: String?
        var role: String?
        var dir: String?
        var icon: String?
        var color: String?
        var floor: String?
        var replace = false
    }

    static func parseRecruit(_ args: [String]) -> RecruitOptions? {
        var o = RecruitOptions()
        var positional: [String] = []
        var i = 1
        while i < args.count {
            let a = args[i]
            let v = i + 1 < args.count ? args[i + 1] : nil
            switch a {
            case "--replace": o.replace = true; i += 1; continue
            case "--preset": o.preset = v
            case "--command": o.command = v
            case "--role": o.role = v
            case "--dir": o.dir = v
            case "--icon": o.icon = v
            case "--color": o.color = v
            case "--floor": o.floor = v
            default:
                if a.hasPrefix("--") { i += 1 } else { positional.append(a); i += 1 }
                continue
            }
            i += 2
        }
        guard let name = positional.first?.trimmingCharacters(in: .whitespaces), !name.isEmpty else { return nil }
        o.name = name
        return o
    }

    /// Grid slot below the Maestro: x = frame.x + i·(w+24), y = frame.y + h + 48 (spec K8).
    static func recruitFrame(below maestro: CGRect, index i: Int) -> CGRect {
        CGRect(x: maestro.minX + CGFloat(i) * (maestro.width + 24), y: maestro.minY + maestro.height + 48,
               width: maestro.width, height: maestro.height)
    }

    /// Terminal of the caller's workspace named `name` (canvas name, then the PTY's agent/display name).
    @MainActor
    static func terminal(named name: String, in graph: WorkspaceGraph) -> CanvasNode? {
        let q = name.lowercased()
        let terms = graph.payload.nodes.filter { $0.content.terminalContent != nil }
        func names(_ n: CanvasNode) -> [String] {
            let tid = n.content.terminalContent!.id
            let s = PlatformHooks.terminalsService?.terminals[tid]
            return [n.content.terminalContent!.name, s?.agentName, s?.displayName].compactMap { $0?.lowercased() }
        }
        return terms.first { names($0).contains(q) } ?? terms.first { names($0).contains { $0.contains(q) } }
    }

    @MainActor
    private func handleRecruitAsync(args: [String], fromTerminalId: UUID?) async -> String {
        guard let o = Self.parseRecruit(args) else {
            return "error: usage: omaestri recruit \"Name\" [--preset P] [--command C] [--role R] [--dir D] [--icon I] [--color C] [--floor F] [--replace]"
        }
        guard let caller = fromTerminalId else { return "error: recruit can only be called from a Maestro terminal" }
        let me = ConnectionManager.shared.canonical(caller)
        // fail-closed: the caller's node in ITS workspace.json must have isManager
        guard let graph = AgentDirectory.graph(of: me), let maestro = graph.terminalNode(me),
              let maestroTc = maestro.content.terminalContent, maestroTc.isManager else {
            return "error: this terminal is not in Maestro mode. Enable Maestro mode when creating the terminal."
        }
        let wsId = graph.workspaceId
        let prefs = PersistenceManager.shared.loadPreferencesSync()

        if let existing = Self.terminal(named: o.name, in: graph) {
            guard o.replace else { return "error: an agent named '\(o.name)' already exists. Use --replace to replace it." }
            let tid = existing.content.terminalContent!.id
            do { try ServerWorkspace.removeNode(existing.id, from: wsId) } catch { return "error: \(error)" }
            PlatformHooks.terminals.removeTerminal(id: tid)
        }

        let wanted = o.preset ?? (o.command == nil ? "generic_shell" : nil)
        let preset = wanted.flatMap { p in
            prefs.agentPresets.first { $0.agentType == p || $0.name.lowercased() == p.lowercased() || $0.command == p }
        }
        let command = o.command ?? preset?.command ?? (wanted == "generic_shell" ? "" : wanted ?? "")
        let agentType = preset?.agentType ?? InterAgentServer.agentType(forCommand: command)
        let role: RolePreset? = o.role.flatMap { rn in prefs.rolePresets.first { $0.name.lowercased() == rn.lowercased() } }
        if let rn = o.role, role == nil { return "error: role '\(rn)' not found. Create it with: omaestri role create \"\(rn)\" \"instructions\"" }

        var baseDir = maestroTc.workingDirectory.isEmpty ? graph.payload.workingDirectory : maestroTc.workingDirectory
        if let f = o.floor {
            guard let floor = graph.payload.floors.first(where: { $0.name.lowercased() == f.lowercased() }) else {
                return "error: floor '\(f)' not found"
            }
            baseDir = floor.worktreePath
        }
        if let d = o.dir, !d.isEmpty { baseDir = (d as NSString).expandingTildeInPath }

        let recruitId = UUID()          // K1: node id == terminal id
        var tc = TerminalContent(name: o.name, agentType: agentType, command: command, workingDirectory: baseDir)
        tc.id = recruitId
        if let preset { tc.icon = preset.icon; tc.color = preset.color }
        if let role { tc.assignedRoleId = role.id; tc.icon = role.icon; tc.color = role.color }
        if let i = o.icon, !i.isEmpty { tc.icon = i }
        if let c = o.color, !c.isEmpty { tc.color = c }

        let slot = ConnectionManager.shared.connections(for: me).filter { $0.type == .terminalToTerminal }.count
        let node = CanvasNode(id: recruitId, frame: Self.recruitFrame(below: maestro.frame, index: slot), content: .terminal(tc))
        var startDir = baseDir
        if let role {
            RoleInjector.shared.prepareRoleDirectory(roleId: role.id, rolePreset: role, workingDirectory: baseDir)
            startDir = RoleInjector.shared.roleDirPath(roleId: role.id, workingDirectory: baseDir)
        }
        do {
            try ServerWorkspace.addNode(node, to: wsId)
            try ServerWorkspace.addConnection(in: wsId, a: maestroTc.id, b: recruitId, kind: .terminalToTerminal)
        } catch {
            return "error: \(error)"
        }
        let session = PlatformHooks.terminals.createTerminal(
            id: recruitId, command: command, workingDirectory: startDir, workspaceId: wsId,
            roleName: role?.name, displayName: o.name, agentType: agentType)
        session.agentName = o.name
        logger.info("Recruited '\(o.name)' (\(agentType)) in \(wsId.uuidString.prefix(8))")
        var msg = "Recruited '\(o.name)'" + (preset.map { " with preset '\($0.name)'" } ?? "")
        if let role { msg += " and role '\(role.name)'" }
        return msg
    }

    // MARK: - dismiss (K8)

    @MainActor
    private func handleDismissAsync(args: [String], fromTerminalId: UUID?) async -> String {
        guard args.count >= 2 else { return "error: usage: omaestri dismiss \"Name\"" }
        guard let caller = fromTerminalId else { return "error: missing terminal ID" }
        guard let agent = AgentDirectory.resolve(args[1], from: caller) else {
            return "error: agent '\(args[1])' not found among connected agents"
        }
        guard let graph = AgentDirectory.graph(of: agent.terminalId) else { return "error: agent '\(agent.name)' is not on a canvas" }
        do { try ServerWorkspace.removeNode(agent.terminalId, from: graph.workspaceId) } catch { return "error: \(error)" }
        PlatformHooks.terminals.removeTerminal(id: agent.terminalId)
        return "Dismissed '\(agent.name)'"
    }

    // MARK: - connect (K8: terminals and notes of the caller's workspace)

    @MainActor
    private func handleConnectAsync(args: [String], fromTerminalId: UUID?) async -> String {
        guard args.count >= 3 else { return "error: usage: omaestri connect \"From\" \"To\"" }
        guard let caller = fromTerminalId,
              let graph = AgentDirectory.graph(of: ConnectionManager.shared.canonical(caller)) else {
            return "error: connect must run from a terminal on the canvas"
        }
        func endpoint(_ name: String) -> CanvasNode? {
            if let t = Self.terminal(named: name, in: graph) { return t }
            let q = name.lowercased()
            let notes = graph.payload.nodes.filter { WorkspaceGraph.noteName(of: $0) != nil }
            return notes.first { WorkspaceGraph.noteName(of: $0)!.lowercased() == q }
                ?? notes.first { WorkspaceGraph.noteName(of: $0)!.lowercased().contains(q) }
        }
        guard let a = endpoint(args[1]) else { return "error: '\(args[1])' not found" }
        guard let b = endpoint(args[2]) else { return "error: '\(args[2])' not found" }
        guard a.id != b.id else { return "error: cannot connect '\(args[1])' to itself" }
        let aEnd = a.content.terminalContent?.id ?? a.id
        let bEnd = b.content.terminalContent?.id ?? b.id
        do {
            try ServerWorkspace.addConnection(in: graph.workspaceId, a: aEnd, b: bEnd)
        } catch {
            return "error: \(error)"
        }
        return "Connected '\(args[1])' ↔ '\(args[2])'"
    }

    // MARK: - role

    @MainActor
    private func handleRoleAsync(args: [String], fromTerminalId: UUID?) async -> String {
        guard args.count >= 2 else {
            return "error: usage: omaestri role <list|create|show|edit|write|assign> [args...]"
        }
        switch args[1] {
        case "list":
            let prefs = PersistenceManager.shared.loadPreferencesSync()
            if prefs.rolePresets.isEmpty {
                return "No roles defined. Create one: omaestri role create \"RoleName\" \"Role instructions\""
            }
            return prefs.rolePresets.map { "  - \($0.name): \($0.prompt.prefix(60))..." }.joined(separator: "\n")

        case "create":
            guard args.count >= 4 else {
                return "error: usage: omaestri role create \"RoleName\" \"Role instructions\""
            }
            let (roleName, rolePrompt) = (args[2], args[3])
            var prefs = PersistenceManager.shared.loadPreferencesSync()
            let newRole = RolePreset(id: UUID(), name: roleName, prompt: rolePrompt, color: "#007AFF", icon: "person.fill")
            prefs.rolePresets.append(newRole)
            try? PersistenceManager.shared.savePreferences(prefs)
            return "Role '\(roleName)' created"

        case "show":
            guard args.count >= 3 else {
                return "error: usage: omaestri role show \"RoleName\""
            }
            let roleName = args[2]
            let prefs = PersistenceManager.shared.loadPreferencesSync()
            guard let role = prefs.rolePresets.first(where: { $0.name.lowercased() == roleName.lowercased() }) else {
                return "error: role '\(roleName)' not found"
            }
            return "Name: \(role.name)\nPrompt: \(role.prompt)"

        case "edit", "write":
            guard args.count >= 4 else {
                return "error: usage: omaestri role \(args[1]) \"RoleName\" --prompt \"...\""
            }
            let roleName = args[2]
            var prefs = PersistenceManager.shared.loadPreferencesSync()
            guard let idx = prefs.rolePresets.firstIndex(where: { $0.name.lowercased() == roleName.lowercased() }) else {
                return "error: role '\(roleName)' not found"
            }
            var i = 3
            while i < args.count {
                if args[i] == "--prompt" && i + 1 < args.count {
                    prefs.rolePresets[idx].prompt = args[i+1]
                    i += 2
                } else { i += 1 }
            }
            try? PersistenceManager.shared.savePreferences(prefs)
            return "Role '\(roleName)' updated"

        case "assign":
            guard args.count >= 4 else {
                return "error: usage: omaestri role assign \"AgentName\" \"RoleName\" (use --none to clear)"
            }
            return assignRole(agentName: args[2], roleName: args[3], caller: fromTerminalId)

        default:
            return "error: unknown role subcommand '\(args[1])'. Valid: list|create|show|edit|write|assign"
        }
    }

    /// K8 role assign: write the role files, record it on the node (id, icon, color) and restart the agent in the
    /// role directory with the same terminal id: position, name and connections stay (they live in workspace.json).
    @MainActor
    private func assignRole(agentName: String, roleName: String, caller: UUID?) -> String {
        guard let caller, let graph = AgentDirectory.graph(of: ConnectionManager.shared.canonical(caller)) else {
            return "error: role assign must run from a terminal on the canvas"
        }
        guard let node = Self.terminal(named: agentName, in: graph), let tc = node.content.terminalContent else {
            return "error: agent '\(agentName)' not found"
        }
        let prefs = PersistenceManager.shared.loadPreferencesSync()
        let role: RolePreset?
        if roleName == "--none" {
            role = nil
        } else {
            guard let r = prefs.rolePresets.first(where: { $0.name.lowercased() == roleName.lowercased() }) else {
                return "error: role '\(roleName)' not found"
            }
            role = r
        }
        let baseDir = tc.workingDirectory.isEmpty ? graph.payload.workingDirectory : tc.workingDirectory
        var startDir = baseDir
        if let role {
            RoleInjector.shared.prepareRoleDirectory(roleId: role.id, rolePreset: role, workingDirectory: baseDir)
            startDir = RoleInjector.shared.roleDirPath(roleId: role.id, workingDirectory: baseDir)
        }
        do {
            try ServerWorkspace.updateNode(node.id, in: graph.workspaceId) { n in
                guard case .terminal(var t) = n.content else { return }
                t.assignedRoleId = role?.id
                if let role { t.icon = role.icon; t.color = role.color }
                n.content = .terminal(t)
            }
        } catch {
            return "error: \(error)"
        }
        let tm = PlatformHooks.terminals
        let old = tm.terminals[tc.id]
        let agentNameValue = old?.agentName
        if let linux = tm as? LinuxTerminalManager { linux.saveScrollback(tc.id) }
        tm.removeTerminal(id: tc.id)
        let session = tm.createTerminal(id: tc.id, command: old?.command ?? tc.command, workingDirectory: startDir,
                                        workspaceId: graph.workspaceId, roleName: role?.name,
                                        displayName: tc.name, agentType: old?.agentType ?? tc.agentType)
        session.agentName = agentNameValue
        if let role { return "Role '\(role.name)' assigned to '\(tc.name)' (agent restarted)" }
        return "Role cleared for '\(tc.name)' (agent restarted)"
    }

    // MARK: - preset

    @MainActor
    private func handlePresetAsync(args: [String]) async -> String {
        guard args.count >= 2 else {
            return "error: usage: omaestri preset <list>"
        }
        switch args[1] {
        case "list":
            let prefs = PersistenceManager.shared.loadPreferencesSync()
            let active = prefs.agentPresets.filter { $0.isActive }
            return active.map { "  - \($0.agentType): \($0.name) (\($0.command))" }.joined(separator: "\n")
        default:
            return "error: unknown preset subcommand '\(args[1])'. Valid: list"
        }
    }
}
