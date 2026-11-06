import Foundation

/// Read-only view of one workspace's `workspace.json`, used by the inter-agent server (Linux has no in-process canvas,
/// so the persisted document is the source of truth for nodes and connections).
///
/// Id model (contract K1): on macOS `CanvasNode.id == TerminalContent.id`. Older Linux documents have two different
/// uuids for a terminal (node id vs. terminal/PTY id) and their connections may reference either. Every terminal
/// endpoint is therefore resolved to the **terminal id** (`TerminalContent.id`, the id of the PTY and of
/// `X-Terminal-ID`); note and portal endpoints stay node ids.
struct WorkspaceGraph {
    let workspaceId: UUID
    let payload: WorkspacePayload

    init(workspaceId: UUID, payload: WorkspacePayload) {
        self.workspaceId = workspaceId
        self.payload = payload
    }

    /// The persisted document, or nil when it does not exist / cannot be decoded.
    static func load(_ workspaceId: UUID, pm: PersistenceManager = .shared) -> WorkspaceGraph? {
        guard let doc = try? pm.loadWorkspace(id: workspaceId) else { return nil }
        return WorkspaceGraph(workspaceId: workspaceId, payload: doc.payload)
    }

    /// Ids of every workspace directory on disk that holds a `workspace.json` (manifest entries and orphans alike).
    static func allWorkspaceIds(pm: PersistenceManager = .shared) -> [UUID] {
        let dir = pm.appDataURL.appendingPathComponent("workspaces")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return names.compactMap(UUID.init(uuidString:)).filter {
            FileManager.default.fileExists(atPath: pm.workspaceURL(id: $0).path)
        }.sorted { $0.uuidString < $1.uuidString }
    }

    /// The workspace whose canvas holds the terminal `id` (node id or terminal id). `hint` is checked first.
    static func containing(terminal id: UUID, hint: UUID? = nil, pm: PersistenceManager = .shared) -> WorkspaceGraph? {
        var order = allWorkspaceIds(pm: pm)
        if let hint, let i = order.firstIndex(of: hint) { order.swapAt(0, i) }
        for wsId in order {
            if let g = load(wsId, pm: pm), g.terminalNode(id) != nil { return g }
        }
        return nil
    }

    // MARK: - Nodes

    func node(_ id: UUID) -> CanvasNode? {
        payload.nodes.first { $0.id == id }
    }

    /// The terminal node for a node id **or** a terminal id.
    func terminalNode(_ id: UUID) -> CanvasNode? {
        payload.nodes.first { n in
            guard let t = n.content.terminalContent else { return false }
            return n.id == id || t.id == id
        }
    }

    /// Terminal id for a terminal endpoint given by node id or terminal id; nil when `id` is not a terminal.
    func terminalId(_ id: UUID) -> UUID? {
        terminalNode(id)?.content.terminalContent?.id
    }

    /// Connection endpoint as the runtime sees it: terminals by terminal id, everything else unchanged.
    func canonicalEndpoint(_ id: UUID) -> UUID {
        terminalId(id) ?? id
    }

    /// node id -> terminal id for terminals whose two ids differ (legacy documents).
    var terminalAliases: [UUID: UUID] {
        var map: [UUID: UUID] = [:]
        for n in payload.nodes {
            if let t = n.content.terminalContent, t.id != n.id { map[n.id] = t.id }
        }
        return map
    }

    var terminalIds: Set<UUID> {
        Set(payload.nodes.compactMap { $0.content.terminalContent?.id })
    }

    // MARK: - Notes

    /// Display name of a note node: its file name without extension (managed notes are always `<name>.md`).
    static func noteName(of node: CanvasNode) -> String? {
        guard case .stickyNote(let c) = node.content else { return nil }
        let file = c.fileName ?? "Note.md"
        let ext = (file as NSString).pathExtension
        let base = ext.isEmpty ? file : (file as NSString).deletingPathExtension
        return base.isEmpty ? "Note" : base
    }

    /// Absolute path of a note's markdown file: the custom path when the note was moved out, otherwise the managed
    /// `<dataDir>/workspaces/<ws>/notes/<name>.md`.
    func notePath(of node: CanvasNode, pm: PersistenceManager = .shared) -> String? {
        guard case .stickyNote(let c) = node.content, let name = Self.noteName(of: node) else { return nil }
        if case .custom(let path) = c.storageMode, !path.isEmpty { return path }
        // same mapping as GET/PUT /api/notes/:name, which the GUI uses for managed notes
        return NoteFileManager.shared.managedPath(workspaceId: workspaceId, noteName: name)
    }

    struct ScopedNote {
        let node: CanvasNode
        let name: String
        /// 0 = linked to the terminal directly; n = n hops along note↔note links
        let depth: Int
        let parent: UUID?
    }

    /// Notes reachable from terminal `tid`: those linked to it, then their note↔note chain (breadth first, no cycles).
    func notes(reachableFrom tid: UUID) -> [ScopedNote] {
        guard let term = terminalNode(tid), let termId = term.content.terminalContent?.id else { return [] }
        let terminalEnds: Set<UUID> = [term.id, termId]
        var result: [ScopedNote] = []
        var seen: Set<UUID> = []
        var frontier: [(UUID, Int, UUID?)] = []
        for c in payload.noteConnections where terminalEnds.contains(c.terminalId) {
            frontier.append((c.noteNodeId, 0, nil))
        }
        while !frontier.isEmpty {
            let (id, depth, parent) = frontier.removeFirst()
            guard !seen.contains(id), let n = node(id), let name = Self.noteName(of: n) else { continue }
            seen.insert(id)
            result.append(ScopedNote(node: n, name: name, depth: depth, parent: parent))
            for c in payload.noteToNoteConnections {
                if c.noteNodeIdA == id { frontier.append((c.noteNodeIdB, depth + 1, id)) }
                if c.noteNodeIdB == id { frontier.append((c.noteNodeIdA, depth + 1, id)) }
            }
        }
        return result
    }

    /// Resolve a note by name inside the caller's scope: exact name, then case-insensitive, then substring.
    func note(named query: String, reachableFrom tid: UUID) -> ScopedNote? {
        let scope = notes(reachableFrom: tid)
        let q = query.hasSuffix(".md") ? String(query.dropLast(3)) : query
        return scope.first { $0.name == q }
            ?? scope.first { $0.name.lowercased() == q.lowercased() }
            ?? scope.first { $0.name.lowercased().contains(q.lowercased()) }
    }

    // MARK: - Connections (runtime view)

    struct Edge: Equatable {
        let id: UUID
        let a: UUID
        let b: UUID
        let type: ConnectionType
    }

    /// Every persisted connection with terminal endpoints resolved to terminal ids. Connections whose terminal
    /// endpoint no longer exists on the canvas are skipped.
    var edges: [Edge] {
        var out: [Edge] = []
        func term(_ id: UUID) -> UUID? { terminalId(id) }
        for c in payload.connections {
            if let a = term(c.terminalIdA), let b = term(c.terminalIdB) {
                out.append(Edge(id: c.id, a: a, b: b, type: .terminalToTerminal))
            }
        }
        for c in payload.noteConnections {
            if let t = term(c.terminalId) { out.append(Edge(id: c.id, a: t, b: c.noteNodeId, type: .terminalToNote)) }
        }
        for c in payload.portalConnections {
            if let t = term(c.terminalId) { out.append(Edge(id: c.id, a: t, b: c.portalNodeId, type: .terminalToPortal)) }
        }
        for c in payload.noteToNoteConnections {
            out.append(Edge(id: c.id, a: c.noteNodeIdA, b: c.noteNodeIdB, type: .noteToNote))
        }
        for c in payload.portalToPortalConnections {
            out.append(Edge(id: c.id, a: c.portalIdA, b: c.portalIdB, type: .portalToPortal))
        }
        return out
    }
}

extension ConnectionType {
    /// Wire name used by the REST API and `/ws/events` (`kind`).
    var wireName: String {
        switch self {
        case .terminalToTerminal: return "terminalToTerminal"
        case .terminalToNote: return "terminalToNote"
        case .terminalToPortal: return "terminalToPortal"
        case .portalToPortal: return "portalToPortal"
        case .noteToNote: return "noteToNote"
        }
    }

    init?(wireName: String) {
        switch wireName {
        case "terminalToTerminal", "terminal": self = .terminalToTerminal
        case "terminalToNote", "note": self = .terminalToNote
        case "terminalToPortal", "portal": self = .terminalToPortal
        case "portalToPortal": self = .portalToPortal
        case "noteToNote": self = .noteToNote
        default: return nil
        }
    }
}
