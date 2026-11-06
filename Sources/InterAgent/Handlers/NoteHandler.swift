import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// `omaestri note …` (contract K7). Scope = notes connected to the calling terminal plus their note↔note chain, read
/// from the caller's workspace.json (`X-Terminal-ID`, node id or terminal id). Name lookup: exact, case-insensitive,
/// then substring, always inside that scope. Custom-path notes resolve to their real file.
///   note read "N" [offset] [limit]      -> "[N lines total]" / "[lines a-b of N]" + numbered lines
///   note write "N" "content" | note edit "N" "old" "new"  -> OK (locked notes refuse)
///   note create ["content"] [--name N] [--color C]          -> node + connection to the caller (K2 events)
///   note delete "N"                     -> removes the node (managed .md deleted, custom file kept)
final class NoteHandler {
    static let shared = NoteHandler()
    private let logger = Logger.make(category: "NoteHandler")
    private let nm = NoteFileManager.shared
    private init() {}

    static let usage = "error: usage: omaestri note <read|write|edit|create|delete> ..."

    @MainActor
    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard args.count >= 2 else { return Self.usage }
        guard let caller = terminalId else { return "error: missing terminal ID" }
        switch args[1] {
        case "read":   return read(args, caller)
        case "write":  return write(args, caller)
        case "edit":   return edit(args, caller)
        case "create": return create(args, caller)
        case "delete": return delete(args, caller)
        default: return "error: unknown note subcommand '\(args[1])'. Valid: read|write|edit|create|delete"
        }
    }

    // MARK: - Scope

    private struct NoteError: Error { let message: String }

    private struct Target {
        let graph: WorkspaceGraph
        let note: WorkspaceGraph.ScopedNote
        let path: String
    }

    @MainActor
    private func target(_ name: String, _ caller: UUID) -> Result<Target, NoteError> {
        let me = ConnectionManager.shared.canonical(caller)
        guard let graph = AgentDirectory.graph(of: me) else {
            return .failure(NoteError(message: "error: this terminal is not on a canvas"))
        }
        guard let note = graph.note(named: name, reachableFrom: me), let path = graph.notePath(of: note.node) else {
            return .failure(NoteError(message: "error: note '\(name)' not found in connections. Use 'omaestri list' to see connected notes."))
        }
        return .success(Target(graph: graph, note: note, path: path))
    }

    // MARK: - read / write / edit

    /// Maestri CLI format: header, then "<line number>\t<text>".
    static func numbered(_ content: String, offset: Int?, limit: Int?) -> String {
        let lines = content.components(separatedBy: "\n")
        let total = lines.count
        let start = max(1, offset ?? 1)
        guard start <= total else { return "[lines \(start)-\(start) of \(total)]" }
        let end = limit.map { min(total, start + max(1, $0) - 1) } ?? total
        let header = (offset == nil && limit == nil) ? "[\(total) lines total]" : "[lines \(start)-\(end) of \(total)]"
        let body = (start...end).map { "\($0)\t\(lines[$0 - 1])" }.joined(separator: "\n")
        return header + "\n" + body
    }

    @MainActor
    private func read(_ args: [String], _ caller: UUID) -> String {
        guard args.count >= 3 else { return "error: usage: omaestri note read \"Note Name\" [offset] [limit]" }
        switch target(args[2], caller) {
        case .failure(let e): return e.message
        case .success(let t):
            do {
                let content = FileManager.default.fileExists(atPath: t.path) ? try nm.read(filePath: t.path) : ""
                let offset = args.count >= 4 ? Int(args[3]) : nil
                let limit = args.count >= 5 ? Int(args[4]) : nil
                return Self.numbered(content, offset: offset, limit: limit)
            } catch {
                return "error: \(error.localizedDescription)"
            }
        }
    }

    @MainActor
    private func write(_ args: [String], _ caller: UUID) -> String {
        guard args.count >= 4 else { return "error: usage: omaestri note write \"Note Name\" \"content\"" }
        switch target(args[2], caller) {
        case .failure(let e): return e.message
        case .success(let t):
            if t.note.node.isLocked { return "error: note '\(t.note.name)' is locked" }
            do {
                try nm.write(filePath: t.path, content: args[3])
                return "OK"
            } catch {
                return "error: \(error.localizedDescription)"
            }
        }
    }

    @MainActor
    private func edit(_ args: [String], _ caller: UUID) -> String {
        guard args.count >= 5 else { return "error: usage: omaestri note edit \"Note Name\" \"old text\" \"new text\"" }
        switch target(args[2], caller) {
        case .failure(let e): return e.message
        case .success(let t):
            if t.note.node.isLocked { return "error: note '\(t.note.name)' is locked" }
            do {
                try nm.edit(filePath: t.path, oldText: args[3], newText: args[4])
                return "OK"
            } catch {
                return "error: \(error.localizedDescription)"
            }
        }
    }

    // MARK: - create / delete

    /// Options after the subcommand: positional content, `--name N`, `--color C`, `--stack S` (accepted, ignored).
    static func parseCreate(_ args: [String]) -> (content: String, name: String?, color: String?) {
        var content: String?
        var name: String?
        var color: String?
        var i = 2
        while i < args.count {
            let a = args[i]
            if ["--name", "--color", "--stack"].contains(a) {
                let v = i + 1 < args.count ? args[i + 1] : nil
                if a == "--name" { name = v } else if a == "--color" { color = v }
                i += 2
            } else {
                if content == nil { content = a }
                i += 1
            }
        }
        return (content ?? "", name?.trimmingCharacters(in: .whitespacesAndNewlines), color)
    }

    /// Automatic note name: first non-empty line (markdown markers removed, ≤ 40 chars), else "Note".
    static func autoName(_ content: String) -> String {
        let first = content.components(separatedBy: "\n").lazy
            .map { $0.trimmingCharacters(in: CharacterSet(charactersIn: "#*>-_` \t")) }
            .first { !$0.isEmpty } ?? ""
        let cleaned = first.components(separatedBy: CharacterSet(charactersIn: "/\\:*?\"<>|")).joined(separator: "-")
        return cleaned.isEmpty ? "Note" : String(cleaned.prefix(40)).trimmingCharacters(in: .whitespaces)
    }

    @MainActor
    private func create(_ args: [String], _ caller: UUID) -> String {
        let me = ConnectionManager.shared.canonical(caller)
        guard let graph = AgentDirectory.graph(of: me), let term = graph.terminalNode(me) else {
            return "error: note create must run from a terminal on the canvas"
        }
        let opts = Self.parseCreate(args)
        let taken = Set(graph.payload.nodes.compactMap(WorkspaceGraph.noteName(of:)).map { $0.lowercased() })
        let base = (opts.name?.isEmpty == false ? opts.name! : Self.autoName(opts.content))
        var name = base
        var n = 2
        while taken.contains(name.lowercased()) { name = "\(base) \(n)"; n += 1 }

        var content = StickyNoteContent(name: name)
        content.hasCustomName = opts.name?.isEmpty == false
        if let c = opts.color, !c.isEmpty { content.color = c }
        // to the left of the caller terminal, top-aligned (macOS placement)
        let size = CGSize(width: 300, height: 220)
        let frame = CGRect(x: term.frame.minX - size.width - 40, y: term.frame.minY, width: size.width, height: size.height)
        let node = CanvasNode(frame: frame, content: .stickyNote(content))
        do {
            let path = NoteFileManager.shared.managedPath(workspaceId: graph.workspaceId, noteName: name)
            try nm.write(filePath: path, content: opts.content)
            try ServerWorkspace.addNode(node, to: graph.workspaceId)
            try ServerWorkspace.addConnection(in: graph.workspaceId, a: term.content.terminalContent?.id ?? term.id,
                                              b: node.id, kind: .terminalToNote)
            NoteRegistry.shared.register(name: name, filePath: path, nodeId: node.id)
            logger.info("note create '\(name)' in \(graph.workspaceId.uuidString.prefix(8))")
            return "Created note \"\(name)\""
        } catch {
            return "error: \(error)"
        }
    }

    @MainActor
    private func delete(_ args: [String], _ caller: UUID) -> String {
        guard args.count >= 3 else { return "error: usage: omaestri note delete \"Note Name\"" }
        switch target(args[2], caller) {
        case .failure(let e): return e.message
        case .success(let t):
            do {
                try ServerWorkspace.removeNode(t.note.node.id, from: t.graph.workspaceId)
                if case .stickyNote(let c) = t.note.node.content, c.storageMode == StorageMode.managed {
                    try? nm.delete(filePath: t.path)      // managed: the .md goes with the note; custom: file kept
                }
                return "Deleted note \"\(t.note.name)\""
            } catch {
                return "error: \(error)"
            }
        }
    }
}

// MARK: - NoteRegistry（运行时 Note 路径缓存；macOS 画布使用）

/// Note 节点路径注册表，由画布在创建/连接 Note 时更新
final class NoteRegistry {
    static let shared = NoteRegistry()
    private var registry: [String: String] = [:]        // name → filePath
    private var nodeIdRegistry: [UUID: String] = [:]    // nodeId → name
    private let lock = NSLock()
    private init() {}

    func register(name: String, filePath: String, nodeId: UUID? = nil) {
        lock.lock(); defer { lock.unlock() }
        registry[name] = filePath
        if let nodeId { nodeIdRegistry[nodeId] = name }
    }

    func unregister(name: String, nodeId: UUID? = nil) {
        lock.lock(); defer { lock.unlock() }
        registry.removeValue(forKey: name)
        if let nodeId { nodeIdRegistry.removeValue(forKey: nodeId) }
    }

    func unregisterByNodeId(_ nodeId: UUID) {
        lock.lock(); defer { lock.unlock() }
        if let name = nodeIdRegistry.removeValue(forKey: nodeId) {
            registry.removeValue(forKey: name)
        }
    }

    func path(forName name: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        return registry[name] ?? registry.first { $0.key.lowercased() == name.lowercased() }?.value
    }

    func name(forNodeId nodeId: UUID) -> String? {
        lock.lock(); defer { lock.unlock() }
        return nodeIdRegistry[nodeId]
    }
}
