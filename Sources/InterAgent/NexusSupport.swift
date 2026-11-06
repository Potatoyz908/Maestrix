import Foundation

/// Shared helpers for the Nexus endpoints/handlers (portal, floors, routines, partituras, terminals).
///
/// Workspaces live on disk (`<dataDir>/workspaces/<id>/workspace.json`); the server keeps no copy in memory, so every
/// lookup reads the files. Node/connection mutations go through `ServerWorkspace` (K2, Codice).
enum NexusWorkspaces {
    /// All workspace ids the server knows: manifest entries plus directories on disk (same rule as GET /api/workspaces).
    static func allWorkspaceIds() -> [UUID] {
        let pm = PersistenceManager.shared
        var ids: [UUID] = ((try? pm.loadManifest())?.workspaces.map(\.id)) ?? []
        var seen = Set(ids)
        let dir = pm.appDataURL.appendingPathComponent("workspaces")
        for name in ((try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []).sorted() {
            if let id = UUID(uuidString: name), !seen.contains(id) {
                seen.insert(id)
                ids.append(id)
            }
        }
        return ids
    }

    /// Ids that refer to the same terminal node: the node id and its `content.terminal.id` (they differ in legacy
    /// workspaces, K1).
    static func aliases(of node: CanvasNode) -> Set<UUID> {
        var s: Set<UUID> = [node.id]
        if case .terminal(let t) = node.content { s.insert(t.id) }
        return s
    }

    /// The workspace + node for a node id or terminal id.
    static func locate(_ id: UUID, preferring wsHint: UUID? = nil) -> (wsId: UUID, doc: WorkspaceDocument, node: CanvasNode)? {
        let pm = PersistenceManager.shared
        var order = allWorkspaceIds()
        if let wsHint, let i = order.firstIndex(of: wsHint) { order.remove(at: i); order.insert(wsHint, at: 0) }
        for wsId in order {
            guard let doc = try? pm.loadWorkspace(id: wsId) else { continue }
            if let node = doc.payload.nodes.first(where: { aliases(of: $0).contains(id) }) {
                return (wsId, doc, node)
            }
        }
        return nil
    }

    /// Workspace of a terminal: the live PTY map first, then workspace.json.
    @MainActor
    static func workspaceOfTerminal(_ tid: UUID) -> UUID? {
        if let ws = LinuxTerminalManager.shared.terminalWorkspaceMap[tid] { return ws }
        return locate(tid)?.wsId
    }

    /// Node ids connected to `callerTid` according to workspace.json (any connection kind), plus the live
    /// ConnectionManager. Both the node id and the terminal id of the caller are matched (K1).
    @MainActor
    static func connectedNodeIds(of callerTid: UUID) -> (wsId: UUID?, ids: [UUID]) {
        var out: [UUID] = ConnectionManager.shared.connectedNodeIds(for: callerTid)
        guard let found = locate(callerTid) else { return (nil, out) }
        let me = aliases(of: found.node)
        let p = found.doc.payload
        func add(_ a: UUID, _ b: UUID) {
            if me.contains(a) { out.append(b) } else if me.contains(b) { out.append(a) }
        }
        p.connections.forEach { add($0.terminalIdA, $0.terminalIdB) }
        p.noteConnections.forEach { add($0.terminalId, $0.noteNodeId) }
        p.portalConnections.forEach { add($0.terminalId, $0.portalNodeId) }
        var seen = Set<UUID>()
        return (found.wsId, out.filter { seen.insert($0).inserted })
    }

    /// Free spot to the right of `anchor` (or at the origin of the canvas when there is no anchor).
    static func placement(rightOf anchor: CanvasNode?, size: CGSize, in doc: WorkspaceDocument) -> CGRect {
        guard let a = anchor else {
            let o = doc.payload.canvasOrigin
            return CGRect(x: o.x + 80, y: o.y + 80, width: size.width, height: size.height)
        }
        var rect = CGRect(x: a.frame.maxX + 48, y: a.frame.minY, width: size.width, height: size.height)
        // Step down until nothing overlaps (bounded: never loops forever on a crowded canvas).
        for _ in 0..<40 where doc.payload.nodes.contains(where: { $0.frame.intersects(rect) }) {
            rect.origin.y += size.height + 24
        }
        return rect
    }
}

/// Human-readable error (Linux Foundation shows NSLocalizedDescriptionKey in `localizedDescription`).
func nexusError(_ message: String, code: Int = 1) -> NSError {
    NSError(domain: "OpenMaestri", code: code, userInfo: [NSLocalizedDescriptionKey: message])
}

/// HTTP response helper with the CORS origin the server already validated.
enum NexusHTTP {
    static func json(_ obj: Any, status: Int = 200, origin: String?) -> Data {
        guard JSONSerialization.isValidJSONObject(obj),
              let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else {
            return InterAgentServer.buildHTTPResponse(status: 500, body: "error: encode failed", allowedOrigin: origin)
        }
        return InterAgentServer.buildHTTPResponse(status: status, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: origin)
    }

    static func text(_ body: String, status: Int, origin: String?) -> Data {
        InterAgentServer.buildHTTPResponse(status: status, body: body, allowedOrigin: origin)
    }

    static func query(_ pathParts: [String]) -> [String: String] {
        guard pathParts.count > 1 else { return [:] }
        var out: [String: String] = [:]
        for pair in pathParts[1].components(separatedBy: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1).map(String.init)
            if kv.count == 2 { out[kv[0]] = kv[1].removingPercentEncoding ?? kv[1] }
        }
        return out
    }
}
