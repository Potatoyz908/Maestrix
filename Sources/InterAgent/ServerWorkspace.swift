import Foundation

/// Server-side mutations of a workspace document (contract K2). The single path through which REST routes and CLI
/// handlers add or remove nodes and connections, so that every change is:
///   1. persisted atomically in `workspace.json` (load → modify → `saveSync`, serialized on the main actor);
///   2. published on `/ws/events` (`nodeAdded` / `nodeRemoved` / `connectionCreated` / `connectionRemoved`, then
///      `workspaceSaved`) for the GUI to mirror without re-posting;
///   3. reflected in the runtime `ConnectionManager` (rebuilt from the saved document: permissions follow the file).
@MainActor
enum ServerWorkspace {
    enum Failure: Error, CustomStringConvertible {
        case workspaceNotFound(UUID)
        case nodeNotFound(UUID)
        case connectionNotFound(UUID)
        case saveFailed(String)

        var description: String {
            switch self {
            case .workspaceNotFound(let id): return "workspace \(id.uuidString) not found"
            case .nodeNotFound(let id): return "node \(id.uuidString) not found"
            case .connectionNotFound(let id): return "connection \(id.uuidString) not found"
            case .saveFailed(let why): return "failed to save workspace: \(why)"
            }
        }
    }

    private static var pm: PersistenceManager { .shared }

    static func load(_ wsId: UUID) throws -> WorkspaceDocument {
        guard let doc = try? pm.loadWorkspace(id: wsId) else { throw Failure.workspaceNotFound(wsId) }
        return doc
    }

    static func save(_ doc: WorkspaceDocument) throws {
        var doc = doc
        doc.payload.lastModifiedAt = Date()
        do {
            try pm.ensureWorkspaceDirectoryExists(id: doc.payload.id)
            try pm.saveSync(doc, to: pm.workspaceURL(id: doc.payload.id))
        } catch {
            throw Failure.saveFailed(error.localizedDescription)
        }
    }

    /// JSON object of an encodable model exactly as `workspace.json` stores it.
    static func jsonObject<T: Encodable>(_ value: T) -> Any? {
        guard let data = try? pm.encoder.encode(value) else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    static func nodeType(of node: CanvasNode) -> String {
        switch node.content {
        case .terminal: return "terminal"
        case .stickyNote: return "stickyNote"
        case .portal: return "portal"
        case .fileTree: return "fileTree"
        case .text: return "text"
        case .shape: return "shape"
        case .stroke: return "stroke"
        case .freehand: return "freehand"
        }
    }

    // MARK: - Nodes

    /// Append `node` (z on top) and publish `nodeAdded {node, nodeId, nodeType, terminalCount}`.
    @discardableResult
    static func addNode(_ node: CanvasNode, to wsId: UUID) throws -> CanvasNode {
        var doc = try load(wsId)
        var node = node
        node.zIndex = max(node.zIndex, (doc.payload.nodes.map(\.zIndex).max() ?? 0) + 1)
        doc.payload.nodes.append(node)
        try save(doc)
        var payload: [String: Any] = [
            "nodeId": node.id.uuidString,
            "nodeType": nodeType(of: node),
            "terminalCount": doc.payload.terminalCount,
        ]
        if let obj = jsonObject(node) { payload["node"] = obj }
        EventBroadcaster.shared.publish(type: "nodeAdded", workspaceId: wsId, payload: payload)
        InterAgentServer.publishWorkspaceSaved(wsId)
        ConnectionManager.shared.reloadWorkspace(wsId)
        return node
    }

    /// Remove a node (by node id, or terminal id for a terminal) and every connection touching it; publishes one
    /// `connectionRemoved` per pruned connection, then `nodeRemoved {nodeId, terminalCount}`.
    @discardableResult
    static func removeNode(_ id: UUID, from wsId: UUID) throws -> CanvasNode {
        var doc = try load(wsId)
        let graph = WorkspaceGraph(workspaceId: wsId, payload: doc.payload)
        guard let node = graph.node(id) ?? graph.terminalNode(id),
              let idx = doc.payload.nodes.firstIndex(where: { $0.id == node.id }) else {
            throw Failure.nodeNotFound(id)
        }
        doc.payload.nodes.remove(at: idx)
        var ids: Set<UUID> = [node.id]
        if let t = node.content.terminalContent { ids.insert(t.id) }
        let pruned = removeConnections(touching: ids, from: &doc.payload)
        try save(doc)
        for ev in pruned {
            EventBroadcaster.shared.publish(type: "connectionRemoved", workspaceId: wsId, payload: ev)
        }
        EventBroadcaster.shared.publish(type: "nodeRemoved", workspaceId: wsId,
                                        payload: ["nodeId": node.id.uuidString, "terminalCount": doc.payload.terminalCount])
        InterAgentServer.publishWorkspaceSaved(wsId)
        ConnectionManager.shared.reloadWorkspace(wsId)
        NoteRegistry.shared.unregisterByNodeId(node.id)
        return node
    }

    // MARK: - Connections

    struct CreatedConnection {
        let id: UUID
        let kind: ConnectionType
        let a: UUID
        let b: UUID
        let ropePoints: [[Double]]
    }

    /// Connection kind from the node types of its endpoints (node ids or terminal ids).
    static func inferKind(_ a: UUID, _ b: UUID, in graph: WorkspaceGraph) -> ConnectionType {
        func kind(_ id: UUID) -> String {
            if graph.terminalNode(id) != nil { return "terminal" }
            switch graph.node(id)?.content {
            case .stickyNote?: return "note"
            case .portal?: return "portal"
            default: return "terminal"
            }
        }
        switch (kind(a), kind(b)) {
        case ("note", "note"): return .noteToNote
        case ("note", _), (_, "note"): return .terminalToNote
        case ("portal", "portal"): return .portalToPortal
        case ("portal", _), (_, "portal"): return .terminalToPortal
        default: return .terminalToTerminal
        }
    }

    /// 21 rope control points: given as-is, interpolated from 2 endpoints, else zeros (the GUI re-simulates them).
    static func normalizeRopePoints(_ raw: [[Double]]) -> [[Double]] {
        let n = Constants.ropeControlPointCount
        if raw.count == n, raw.allSatisfy({ $0.count == 2 }) { return raw }
        if raw.count == 2, raw[0].count == 2, raw[1].count == 2 {
            let (p0, p1) = (raw[0], raw[1])
            return (0..<n).map { i in
                let t = Double(i) / Double(n - 1)
                return [p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t]
            }
        }
        return Array(repeating: [0.0, 0.0], count: n)
    }

    /// Persist a connection between `a` and `b` (as the caller named them: node ids or terminal ids; the runtime
    /// resolves terminals to terminal ids) and publish `connectionCreated {connectionId, kind, a, b, ropePoints,
    /// connection}`. An existing connection between the same endpoints is returned instead of duplicated.
    @discardableResult
    static func addConnection(in wsId: UUID, a: UUID, b: UUID, kind: ConnectionType? = nil, id: UUID? = nil,
                              ropePoints: [[Double]] = []) throws -> CreatedConnection {
        var doc = try load(wsId)
        let graph = WorkspaceGraph(workspaceId: wsId, payload: doc.payload)
        let kind = kind ?? inferKind(a, b, in: graph)
        let ends = Set([graph.canonicalEndpoint(a), graph.canonicalEndpoint(b)])
        if id == nil, let dup = graph.edges.first(where: { $0.type == kind && Set([$0.a, $0.b]) == ends }) {
            return CreatedConnection(id: dup.id, kind: kind, a: a, b: b, ropePoints: ropePoints)
        }
        let connId = id ?? UUID()
        let points = normalizeRopePoints(ropePoints)
        // terminal↔X connections keep the terminal first (Maestri field order)
        func isTerminal(_ x: UUID) -> Bool { graph.terminalNode(x) != nil }
        let (t, o) = isTerminal(a) || !isTerminal(b) ? (a, b) : (b, a)
        let json: Any?
        switch kind {
        case .terminalToTerminal:
            let c = TerminalConnection(id: connId, terminalIdA: a, terminalIdB: b, ropePoints: points)
            doc.payload.connections.append(c); json = jsonObject(c)
        case .terminalToNote:
            let c = NoteConnection(id: connId, terminalId: t, noteNodeId: o, ropePoints: points)
            doc.payload.noteConnections.append(c); json = jsonObject(c)
        case .terminalToPortal:
            let c = PortalConnection(id: connId, terminalId: t, portalNodeId: o, ropePoints: points)
            doc.payload.portalConnections.append(c); json = jsonObject(c)
        case .portalToPortal:
            let c = PortalToPortalConnection(id: connId, portalIdA: a, portalIdB: b, ropePoints: points)
            doc.payload.portalToPortalConnections.append(c); json = jsonObject(c)
        case .noteToNote:
            let c = NoteToNoteConnection(id: connId, noteNodeIdA: a, noteNodeIdB: b, ropePoints: points)
            doc.payload.noteToNoteConnections.append(c); json = jsonObject(c)
        }
        try save(doc)
        let (ea, eb) = kind == .terminalToNote || kind == .terminalToPortal ? (t, o) : (a, b)
        var payload: [String: Any] = [
            "connectionId": connId.uuidString, "kind": kind.wireName,
            "a": ea.uuidString, "b": eb.uuidString, "ropePoints": points,
        ]
        if let json { payload["connection"] = json }
        EventBroadcaster.shared.publish(type: "connectionCreated", workspaceId: wsId, payload: payload)
        InterAgentServer.publishWorkspaceSaved(wsId)
        ConnectionManager.shared.reloadWorkspace(wsId)
        return CreatedConnection(id: connId, kind: kind, a: ea, b: eb, ropePoints: points)
    }

    /// Remove one connection; publishes `connectionRemoved {connectionId, kind, a, b}` and revokes it at runtime.
    static func removeConnection(_ connId: UUID, from wsId: UUID) throws {
        var doc = try load(wsId)
        guard let info = connectionInfo(connId, in: doc.payload) else { throw Failure.connectionNotFound(connId) }
        doc.payload.connections.removeAll { $0.id == connId }
        doc.payload.noteConnections.removeAll { $0.id == connId }
        doc.payload.portalConnections.removeAll { $0.id == connId }
        doc.payload.portalToPortalConnections.removeAll { $0.id == connId }
        doc.payload.noteToNoteConnections.removeAll { $0.id == connId }
        doc.payload.crossFloorConnections.removeAll { $0.id == connId }
        try save(doc)
        var ev = info
        ev["connectionId"] = connId.uuidString
        EventBroadcaster.shared.publish(type: "connectionRemoved", workspaceId: wsId, payload: ev)
        InterAgentServer.publishWorkspaceSaved(wsId)
        ConnectionManager.shared.reloadWorkspace(wsId)
    }

    /// Workspace (on disk) that holds connection `connId`, `hint` first.
    static func workspace(holdingConnection connId: UUID, hint: UUID?) -> UUID? {
        var order = WorkspaceGraph.allWorkspaceIds()
        if let hint, let i = order.firstIndex(of: hint) { order.swapAt(0, i) }
        return order.first { id in
            (try? pm.loadWorkspace(id: id)).map { connectionInfo(connId, in: $0.payload) != nil } ?? false
        }
    }

    static func connectionInfo(_ connId: UUID, in p: WorkspacePayload) -> [String: Any]? {
        if let c = p.connections.first(where: { $0.id == connId }) {
            return ["kind": "terminalToTerminal", "a": c.terminalIdA.uuidString, "b": c.terminalIdB.uuidString]
        } else if let c = p.noteConnections.first(where: { $0.id == connId }) {
            return ["kind": "terminalToNote", "a": c.terminalId.uuidString, "b": c.noteNodeId.uuidString]
        } else if let c = p.portalConnections.first(where: { $0.id == connId }) {
            return ["kind": "terminalToPortal", "a": c.terminalId.uuidString, "b": c.portalNodeId.uuidString]
        } else if let c = p.portalToPortalConnections.first(where: { $0.id == connId }) {
            return ["kind": "portalToPortal", "a": c.portalIdA.uuidString, "b": c.portalIdB.uuidString]
        } else if let c = p.noteToNoteConnections.first(where: { $0.id == connId }) {
            return ["kind": "noteToNote", "a": c.noteNodeIdA.uuidString, "b": c.noteNodeIdB.uuidString]
        } else if let c = p.crossFloorConnections.first(where: { $0.id == connId }) {
            return ["kind": "crossFloor", "a": c.nodeIdA.uuidString, "b": c.nodeIdB.uuidString]
        }
        return nil
    }

    /// Drop every connection touching `ids`; returns the `connectionRemoved` payloads (without workspaceId).
    static func removeConnections(touching ids: Set<UUID>, from p: inout WorkspacePayload) -> [[String: Any]] {
        var events: [[String: Any]] = []
        func pruned(_ cid: UUID, _ kind: String, _ a: UUID, _ b: UUID) {
            events.append(["connectionId": cid.uuidString, "kind": kind, "a": a.uuidString, "b": b.uuidString])
        }
        p.connections.forEach { if ids.contains($0.terminalIdA) || ids.contains($0.terminalIdB) { pruned($0.id, "terminalToTerminal", $0.terminalIdA, $0.terminalIdB) } }
        p.noteConnections.forEach { if ids.contains($0.terminalId) || ids.contains($0.noteNodeId) { pruned($0.id, "terminalToNote", $0.terminalId, $0.noteNodeId) } }
        p.portalConnections.forEach { if ids.contains($0.terminalId) || ids.contains($0.portalNodeId) { pruned($0.id, "terminalToPortal", $0.terminalId, $0.portalNodeId) } }
        p.portalToPortalConnections.forEach { if ids.contains($0.portalIdA) || ids.contains($0.portalIdB) { pruned($0.id, "portalToPortal", $0.portalIdA, $0.portalIdB) } }
        p.noteToNoteConnections.forEach { if ids.contains($0.noteNodeIdA) || ids.contains($0.noteNodeIdB) { pruned($0.id, "noteToNote", $0.noteNodeIdA, $0.noteNodeIdB) } }
        p.crossFloorConnections.forEach { if ids.contains($0.nodeIdA) || ids.contains($0.nodeIdB) { pruned($0.id, "crossFloor", $0.nodeIdA, $0.nodeIdB) } }
        p.connections.removeAll { ids.contains($0.terminalIdA) || ids.contains($0.terminalIdB) }
        p.noteConnections.removeAll { ids.contains($0.terminalId) || ids.contains($0.noteNodeId) }
        p.portalConnections.removeAll { ids.contains($0.terminalId) || ids.contains($0.portalNodeId) }
        p.portalToPortalConnections.removeAll { ids.contains($0.portalIdA) || ids.contains($0.portalIdB) }
        p.noteToNoteConnections.removeAll { ids.contains($0.noteNodeIdA) || ids.contains($0.noteNodeIdB) }
        p.crossFloorConnections.removeAll { ids.contains($0.nodeIdA) || ids.contains($0.nodeIdB) }
        return events
    }
}
