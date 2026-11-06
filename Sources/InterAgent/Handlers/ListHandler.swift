import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// `omaestri list`: the caller's connected agents (with role), portals (name + URL) and notes, including the
/// note↔note chain indented under its parent. Everything is read from the caller's workspace.json + runtime
/// connections; it never touches the portal service (absent on Linux without a GUI bridge).
final class ListHandler {
    static let shared = ListHandler()
    private let logger = Logger.make(category: "ListHandler")
    private init() {}

    @MainActor
    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard let caller = terminalId else { return "error: missing terminal ID" }
        let cm = ConnectionManager.shared
        let me = cm.canonical(caller)
        let graph = AgentDirectory.graph(of: me)

        var sections: [String] = []
        let self_ = AgentDirectory.agent(me, graph: graph)
        if self_.name != "unknown" {
            sections.append("You:\n  - name: \"\(self_.name)\"" + (self_.role.map { " - role: \($0)" } ?? ""))
        }

        let agents = AgentDirectory.connectedAgents(of: me).map { a in
            "  - name: \"\(a.name)\"" + (a.role.map { " - role: \($0)" } ?? "")
        }
        var portals: [String] = []
        for c in cm.connections(for: me) where c.type == .terminalToPortal {
            let pid = c.nodeIdA == me ? c.nodeIdB : c.nodeIdA
            if case .portal(let p)? = graph?.node(pid)?.content {
                let name = p.name.isEmpty ? "Portal" : p.name
                portals.append("  - name: \"\(name)\"" + (p.currentURL.isEmpty ? "" : " - url: \(p.currentURL)"))
            } else {
                portals.append("  - name: \"Portal\"")
            }
        }
        let notes = (graph?.notes(reachableFrom: me) ?? []).map { n in
            String(repeating: "  ", count: n.depth + 1) + "- name: \"\(n.name)\"" + (n.node.isLocked ? " - locked: true" : "")
        }

        if agents.isEmpty && notes.isEmpty && portals.isEmpty {
            return sections.isEmpty ? "No connections." : sections.joined(separator: "\n\n") + "\n\nNo connections."
        }
        if !agents.isEmpty { sections.append("Connected agents:\n" + agents.joined(separator: "\n")) }
        if !portals.isEmpty {
            sections.append("Connected portals (use `omaestri portal snapshot`):\n" + portals.joined(separator: "\n"))
        }
        if !notes.isEmpty {
            sections.append("Connected notes (use `omaestri note read/write/edit`):\n" + notes.joined(separator: "\n"))
        }
        return sections.joined(separator: "\n\n")
    }
}
