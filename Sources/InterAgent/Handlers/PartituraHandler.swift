import Foundation

/// `omaestri partitura list | save|create "Name" ["Agent" ...] | update "Name" ["Agent" ...] | apply|insert "Name"
///  [--workspace W] [--at X,Y] | delete "Name"` (K11; verbs of the maestri-manager skill plus the Sprint 9 aliases).
final class PartituraHandler {
    static let shared = PartituraHandler()
    private init() {}

    static let usage = """
    usage: omaestri partitura list
           omaestri partitura create "Name" ["Agent" ...]   (alias: save) saves you and your team
           omaestri partitura update "Name" ["Agent" ...]
           omaestri partitura insert "Name" [--workspace W] [--at X,Y]   (alias: apply)
           omaestri partitura delete "Name"
    """

    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard args.count >= 2 else { return Self.usage }
        let sub = args[1]
        let rest = Array(args.dropFirst(2))
        switch sub {
        case "list", "ls":
            let all = PartituraStore.all()
            guard !all.isEmpty else { return "No partituras saved." }
            return all.map { p in
                let s = PartituraStore.summary(p)
                return "\(s["name"] as? String ?? "?")  (\(s["terminalCount"] as? Int ?? 0) agents, \(s["nodeCount"] as? Int ?? 0) elements)  [\(String((s["id"] as? String ?? "").prefix(8)))]"
            }.joined(separator: "\n")
        case "create", "save", "update":
            guard let name = rest.first, !name.isEmpty else { return "error: " + Self.usage }
            return await MainActor.run { save(name: name, agents: Array(rest.dropFirst()), terminalId: terminalId, update: sub == "update") }
        case "insert", "apply":
            guard let name = rest.first, !name.isEmpty else { return "error: " + Self.usage }
            return await MainActor.run { insert(key: name, options: Array(rest.dropFirst()), terminalId: terminalId) }
        case "delete", "rm":
            guard let name = rest.first else { return "error: " + Self.usage }
            return PartituraStore.delete(name) ? "Deleted partitura '\(name)'" : "error: partitura '\(name)' not found"
        case "-h", "--help", "help":
            return Self.usage
        default:
            return "error: unknown partitura subcommand '\(sub)'\n" + Self.usage
        }
    }

    @MainActor
    private func save(name: String, agents: [String], terminalId: UUID?, update: Bool) -> String {
        guard let tid = terminalId, let caller = NexusWorkspaces.locate(tid) else {
            return "error: partitura create must run from a terminal on the canvas"
        }
        let existing = PartituraStore.find(name)
        if update && existing == nil { return "error: partitura '\(name)' not found" }
        var ids: [UUID] = [caller.node.id]
        if agents.isEmpty {
            ids += NexusWorkspaces.connectedNodeIds(of: tid).ids          // you and your team
        } else {
            for agent in agents {
                guard let n = caller.doc.payload.nodes.first(where: { $0.content.terminalContent?.name == agent })
                        ?? caller.doc.payload.nodes.first(where: { $0.content.terminalContent?.name.caseInsensitiveCompare(agent) == .orderedSame }) else {
                    return "error: agent '\(agent)' not found in this workspace"
                }
                ids.append(n.id)
            }
        }
        do {
            var p = try PartituraStore.capture(name: name, nodeIds: ids, from: caller.doc)
            if let existing { p["id"] = existing["id"]; p["createdAt"] = existing["createdAt"] }
            let saved = try PartituraStore.save(p)
            let count = (saved["nodes"] as? [Any])?.count ?? 0
            return "\(existing == nil ? "Saved" : "Updated") partitura '\(name)' (\(count) elements)"
        } catch {
            return "error: \(error.localizedDescription)"
        }
    }

    @MainActor
    private func insert(key: String, options: [String], terminalId: UUID?) -> String {
        guard let p = PartituraStore.find(key) else { return "error: partitura '\(key)' not found" }
        var wsId: UUID? = nil
        var at: CGPoint? = nil
        var i = 0
        while i < options.count {
            if options[i] == "--workspace", i + 1 < options.count {
                wsId = Self.workspaceId(named: options[i + 1]); i += 2
                if wsId == nil { return "error: workspace '\(options[i - 1])' not found" }
            } else if options[i] == "--at", i + 1 < options.count {
                let xy = options[i + 1].split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
                if xy.count == 2 { at = CGPoint(x: xy[0], y: xy[1]) }
                i += 2
            } else { i += 1 }
        }
        let caller = terminalId.flatMap { NexusWorkspaces.locate($0) }
        guard let target = wsId ?? caller?.wsId ?? InterAgentServer.resolveWorkspaceId(pathParts: [], pm: .shared),
              let doc = try? PersistenceManager.shared.loadWorkspace(id: target) else {
            return "error: no workspace to insert into"
        }
        do {
            let inserted = try PartituraStore.insert(p, into: target, at: at ?? PartituraStore.freeSpot(in: doc))
            let agents = inserted.nodes.filter { $0.content.terminalContent != nil }
            // connect the caller to the band (only to its Maestros when it has any), as the skill describes
            if let caller, caller.wsId == target {
                let maestros = agents.filter { $0.content.terminalContent?.isManager == true }
                for a in (maestros.isEmpty ? agents : maestros) {
                    _ = try? ServerWorkspace.addConnection(in: target, a: caller.node.id, b: a.id, kind: .terminalToTerminal)
                }
            }
            let names = agents.compactMap { $0.content.terminalContent?.name }
            let name = p["name"] as? String ?? key
            return names.isEmpty
                ? "Inserted partitura '\(name)' (\(inserted.nodes.count) elements)"
                : "Inserted partitura '\(name)': \(names.joined(separator: ", ")). Ask them with: omaestri ask \"\(names[0])\" \"...\""
        } catch {
            return "error: \(error)"
        }
    }

    static func workspaceId(named key: String) -> UUID? {
        if let id = UUID(uuidString: key) { return id }
        let entries = (try? PersistenceManager.shared.loadManifest())?.workspaces ?? []
        return entries.first { $0.name == key }?.id ?? entries.first { $0.name.caseInsensitiveCompare(key) == .orderedSame }?.id
    }
}
