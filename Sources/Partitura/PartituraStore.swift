import Foundation

/// K11 — partituras: saved canvas layouts (agents with roles/commands, notes, portals and their ropes).
///
/// Stored as `<dataDir>/partituras/<id>.json`:
/// `{"id", "name", "createdAt", "nodes": [<CanvasNode JSON as in workspace.json>], "connections": [{"kind","a","b"}],
///   "roles": [...]}` with frames relative to the top-left of the layout's bounding box. Inserting regenerates every
/// UUID (terminal node id == terminal id, K1) and creates nodes/connections through `ServerWorkspace` (K2 events).
enum PartituraStore {
    static var directory: URL { PersistenceManager.shared.appDataURL.appendingPathComponent("partituras") }

    static func url(_ id: String) -> URL { directory.appendingPathComponent("\(id).json") }

    static func all() -> [[String: Any]] {
        let files = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return files.filter { $0.pathExtension == "json" }
            .compactMap { (try? Data(contentsOf: $0)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } }
            .sorted { ($0["name"] as? String ?? "").localizedCaseInsensitiveCompare($1["name"] as? String ?? "") == .orderedAscending }
    }

    /// By id, then exact name, then case-insensitive name.
    static func find(_ key: String) -> [String: Any]? {
        let list = all()
        return list.first { ($0["id"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
            ?? list.first { ($0["name"] as? String) == key }
            ?? list.first { ($0["name"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
    }

    static func summary(_ p: [String: Any]) -> [String: Any] {
        var s: [String: Any] = ["id": p["id"] ?? "", "name": p["name"] ?? "", "createdAt": p["createdAt"] ?? ""]
        let nodes = p["nodes"] as? [[String: Any]] ?? []
        s["nodeCount"] = nodes.count
        s["terminalCount"] = nodes.filter { (($0["content"] as? [String: Any])?["terminal"]) != nil }.count
        s["nodes"] = nodes
        s["connections"] = p["connections"] ?? []
        if let roles = p["roles"] { s["roles"] = roles }
        return s
    }

    @discardableResult
    static func save(_ p: [String: Any]) throws -> [String: Any] {
        var p = p
        let id = (p["id"] as? String).flatMap { UUID(uuidString: $0)?.uuidString } ?? UUID().uuidString
        p["id"] = id
        if p["createdAt"] == nil { p["createdAt"] = ISO8601DateFormatter().string(from: Date()) }
        guard JSONSerialization.isValidJSONObject(p) else { throw nexusError("invalid partitura") }
        let data = try JSONSerialization.data(withJSONObject: p, options: [.prettyPrinted, .sortedKeys])
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try data.write(to: url(id), options: .atomic)
        return p
    }

    static func delete(_ key: String) -> Bool {
        guard let p = find(key), let id = p["id"] as? String else { return false }
        return (try? FileManager.default.removeItem(at: url(id))) != nil
    }

    // MARK: - Capture from a workspace

    /// Partitura of `nodeIds` (node or terminal ids) in `doc`: frames relative to the bounding box, connections whose
    /// both ends are inside the selection.
    static func capture(name: String, nodeIds: [UUID], from doc: WorkspaceDocument) throws -> [String: Any] {
        let wanted = Set(nodeIds)
        let nodes = doc.payload.nodes.filter { !NexusWorkspaces.aliases(of: $0).isDisjoint(with: wanted) }
        guard !nodes.isEmpty else { throw nexusError("no nodes to save") }
        let minX = nodes.map(\.frame.minX).min() ?? 0
        let minY = nodes.map(\.frame.minY).min() ?? 0
        var alias: [UUID: UUID] = [:]   // any id of a node -> its node id
        for n in nodes { for a in NexusWorkspaces.aliases(of: n) { alias[a] = n.id } }
        let nodeJSON: [Any] = nodes.compactMap { n in
            var m = n
            m.frame = CGRect(x: n.frame.minX - minX, y: n.frame.minY - minY, width: n.frame.width, height: n.frame.height)
            return NexusWorkspaces.json(m)
        }
        var conns: [[String: String]] = []
        func add(_ kind: String, _ a: UUID, _ b: UUID) {
            guard let na = alias[a], let nb = alias[b] else { return }
            conns.append(["kind": kind, "a": na.uuidString, "b": nb.uuidString])
        }
        let p = doc.payload
        p.connections.forEach { add("terminalToTerminal", $0.terminalIdA, $0.terminalIdB) }
        p.noteConnections.forEach { add("terminalToNote", $0.terminalId, $0.noteNodeId) }
        p.portalConnections.forEach { add("terminalToPortal", $0.terminalId, $0.portalNodeId) }
        p.portalToPortalConnections.forEach { add("portalToPortal", $0.portalIdA, $0.portalIdB) }
        p.noteToNoteConnections.forEach { add("noteToNote", $0.noteNodeIdA, $0.noteNodeIdB) }
        return ["name": name, "nodes": nodeJSON, "connections": conns]
    }

    // MARK: - Insert

    struct Inserted {
        var nodes: [CanvasNode] = []
        var connections: [UUID] = []
    }

    /// Creates the partitura's nodes (new UUIDs) at `origin` in workspace `wsId` and its connections.
    @MainActor
    static func insert(_ p: [String: Any], into wsId: UUID, at origin: CGPoint) throws -> Inserted {
        let pm = PersistenceManager.shared
        var remap: [UUID: UUID] = [:]
        var out = Inserted()
        for raw in p["nodes"] as? [[String: Any]] ?? [] {
            guard let data = try? JSONSerialization.data(withJSONObject: raw),
                  var node = try? pm.decoder.decode(CanvasNode.self, from: data) else { continue }
            let newId = UUID()
            for a in NexusWorkspaces.aliases(of: node) { remap[a] = newId }
            node.id = newId
            node.frame.origin = CGPoint(x: origin.x + node.frame.minX, y: origin.y + node.frame.minY)
            switch node.content {
            case .terminal(var t): t.id = newId; node.content = .terminal(t)          // K1: node id == terminal id
            case .portal(var pc): pc.id = newId; node.content = .portal(pc)
            default: break
            }
            out.nodes.append(try ServerWorkspace.addNode(node, to: wsId))
        }
        for c in p["connections"] as? [[String: Any]] ?? [] {
            guard let a = (c["a"] as? String).flatMap(UUID.init).flatMap({ remap[$0] }),
                  let b = (c["b"] as? String).flatMap(UUID.init).flatMap({ remap[$0] }) else { continue }
            let kind = (c["kind"] as? String).flatMap(ConnectionType.init(wireName:))
            out.connections.append(try ServerWorkspace.addConnection(in: wsId, a: a, b: b, kind: kind).id)
        }
        return out
    }

    /// Top-left for an insert: right of everything already on the canvas (a free spot), at the top of the content.
    static func freeSpot(in doc: WorkspaceDocument) -> CGPoint {
        let nodes = doc.payload.nodes
        guard let maxX = nodes.map(\.frame.maxX).max(), let minY = nodes.map(\.frame.minY).min() else {
            return CGPoint(x: doc.payload.canvasOrigin.x + 80, y: doc.payload.canvasOrigin.y + 80)
        }
        return CGPoint(x: maxX + 96, y: minY)
    }
}
