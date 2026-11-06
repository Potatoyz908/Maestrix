import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// 连接类型
enum ConnectionType {
    case terminalToTerminal
    case terminalToNote
    case terminalToPortal
    case portalToPortal
    case noteToNote
}

/// 连接状态
enum ConnectionStatus {
    case idle           // 灰色虚线
    case communicating  // 绿色 glow
    case disconnected   // 红色虚线
    case error          // 错误
}

/// 运行时连接记录（内存中，序列化版本在 WorkspacePayload）
struct ActiveConnection {
    let id: UUID
    let nodeIdA: UUID
    let nodeIdB: UUID
    let type: ConnectionType
    var status: ConnectionStatus = .idle
    /// Workspace whose `workspace.json` holds this connection (nil = created in memory only, macOS paths)
    var workspaceId: UUID? = nil
}

/// 连接生命周期管理器（@MainActor，画布状态修改必须在主线程）
@MainActor
public final class ConnectionManager {
    public static let shared = ConnectionManager()
    private let logger = Logger.make(category: "ConnectionManager")

    private(set) var connections: [UUID: ActiveConnection] = [:]

    /// node id -> terminal id for terminals whose two ids differ (legacy Linux documents, contract K1).
    /// Lets `X-Terminal-ID` / connection endpoints given as either id reach the same runtime terminal.
    private(set) var terminalAliases: [UUID: UUID] = [:]

    private init() {}

    /// The terminal id for `id` when it is a known terminal node id, otherwise `id` itself.
    func canonical(_ id: UUID) -> UUID {
        terminalAliases[id] ?? id
    }

    // MARK: - 建立连接

    /// 建立 Terminal↔Terminal 连接，自动注入 Skill
    func connectTerminals(
        idA: UUID, idB: UUID,
        serverPort: UInt16,
        ropePoints: [[Double]] = []
    ) -> TerminalConnection {
        let conn = TerminalConnection(
            id: UUID(),
            terminalIdA: idA,
            terminalIdB: idB,
            ropePoints: ropePoints.isEmpty
                ? buildDefaultRopePoints() : ropePoints
        )
        let active = ActiveConnection(id: conn.id, nodeIdA: idA, nodeIdB: idB, type: .terminalToTerminal)
        connections[conn.id] = active

        // 向双端注入 Skill（FR29）
        let host = "\(Constants.interAgentServerHost):\(serverPort)"
        SkillInjector.shared.inject(to: idA, host: host)
        SkillInjector.shared.inject(to: idB, host: host)

        logger.info("Terminal connection established: \(idA.uuidString.prefix(8)) ↔ \(idB.uuidString.prefix(8))")
        return conn
    }

    /// 建立 Terminal↔Terminal 连接（同时持久化到 workspace）
    func connectTerminals(
        idA: UUID, idB: UUID,
        serverPort: UInt16,
        ropePoints: [[Double]] = [],
        workspace: WorkspaceManager?
    ) -> TerminalConnection {
        let conn = connectTerminals(idA: idA, idB: idB, serverPort: serverPort, ropePoints: ropePoints)
        workspace?.addConnection(conn)
        return conn
    }

    /// 建立 Terminal↔Note 连接（同时持久化到 workspace）
    func connectTerminalToNote(
        terminalId: UUID, noteNodeId: UUID,
        ropePoints: [[Double]] = [],
        workspace: WorkspaceManager?
    ) -> NoteConnection {
        let conn = connectTerminalToNote(terminalId: terminalId, noteNodeId: noteNodeId, ropePoints: ropePoints)
        workspace?.addNoteConnection(conn)
        return conn
    }

    /// 建立 Terminal↔Portal 连接（同时持久化到 workspace）
    func connectTerminalToPortal(
        terminalId: UUID, portalNodeId: UUID,
        ropePoints: [[Double]] = [],
        workspace: WorkspaceManager?
    ) -> PortalConnection {
        let conn = connectTerminalToPortal(terminalId: terminalId, portalNodeId: portalNodeId, ropePoints: ropePoints)
        workspace?.addPortalConnection(conn)
        return conn
    }

    /// 建立 Terminal↔Note 连接
    func connectTerminalToNote(terminalId: UUID, noteNodeId: UUID, ropePoints: [[Double]] = []) -> NoteConnection {
        let conn = NoteConnection(
            id: UUID(), terminalId: terminalId, noteNodeId: noteNodeId,
            ropePoints: ropePoints.isEmpty ? buildDefaultRopePoints() : ropePoints
        )
        let active = ActiveConnection(id: conn.id, nodeIdA: terminalId, nodeIdB: noteNodeId, type: .terminalToNote)
        connections[conn.id] = active
        logger.info("Terminal↔Note connection: \(terminalId.uuidString.prefix(8)) → \(noteNodeId.uuidString.prefix(8))")
        return conn
    }

    /// 建立 Terminal↔Portal 连接
    func connectTerminalToPortal(terminalId: UUID, portalNodeId: UUID, ropePoints: [[Double]] = []) -> PortalConnection {
        let conn = PortalConnection(
            id: UUID(), terminalId: terminalId, portalNodeId: portalNodeId,
            ropePoints: ropePoints.isEmpty ? buildDefaultRopePoints() : ropePoints
        )
        let active = ActiveConnection(id: conn.id, nodeIdA: terminalId, nodeIdB: portalNodeId, type: .terminalToPortal)
        connections[conn.id] = active
        return conn
    }

    /// 建立 Note↔Note 连接（Note Chaining）
    func connectNoteToNote(noteNodeIdA: UUID, noteNodeIdB: UUID, ropePoints: [[Double]] = []) -> NoteToNoteConnection {
        let conn = NoteToNoteConnection(
            noteNodeIdA: noteNodeIdA, noteNodeIdB: noteNodeIdB,
            ropePoints: ropePoints.isEmpty ? buildDefaultRopePoints() : ropePoints
        )
        let active = ActiveConnection(id: conn.id, nodeIdA: noteNodeIdA, nodeIdB: noteNodeIdB, type: .noteToNote)
        connections[conn.id] = active
        logger.info("Note↔Note connection: \(noteNodeIdA.uuidString.prefix(8)) ↔ \(noteNodeIdB.uuidString.prefix(8))")
        return conn
    }

    /// 建立 Portal↔Portal 连接（共享 session）
    func connectPortalToPortal(portalIdA: UUID, portalIdB: UUID, ropePoints: [[Double]] = []) -> PortalToPortalConnection {
        let conn = PortalToPortalConnection(
            portalIdA: portalIdA, portalIdB: portalIdB,
            ropePoints: ropePoints.isEmpty ? buildDefaultRopePoints() : ropePoints
        )
        let active = ActiveConnection(id: conn.id, nodeIdA: portalIdA, nodeIdB: portalIdB, type: .portalToPortal)
        connections[conn.id] = active
        logger.info("Portal↔Portal connection: \(portalIdA.uuidString.prefix(8)) ↔ \(portalIdB.uuidString.prefix(8))")
        return conn
    }

    // MARK: - 断开连接

    /// Removes an active connection by ID and posts `connectionStatusChanged`.
    func disconnect(id: UUID) {
        connections.removeValue(forKey: id)
        logger.debug("Connection \(id.uuidString.prefix(8)) removed")
    }

    /// Removes all connections that involve the given node (called on node deletion).
    func disconnectAll(involvedNode nodeId: UUID) {
        let toRemove = connections.values.filter { $0.nodeIdA == nodeId || $0.nodeIdB == nodeId }
        toRemove.forEach { connections.removeValue(forKey: $0.id) }
    }

    // MARK: - 状态更新

    func updateStatus(_ status: ConnectionStatus, for connectionId: UUID) {
        connections[connectionId]?.status = status
    }

    func markCommunicating(_ connectionId: UUID) {
        updateStatus(.communicating, for: connectionId)
        // 150ms 后恢复 idle（FR: 通信结束后渐变回灰色）
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(150))
            if connections[connectionId]?.status == .communicating {
                updateStatus(.idle, for: connectionId)
            }
        }
    }

    // MARK: - 查询

    func connections(for nodeId: UUID) -> [ActiveConnection] {
        let id = canonical(nodeId)
        return connections.values.filter { $0.nodeIdA == id || $0.nodeIdB == id }
            .sorted { $0.id.uuidString < $1.id.uuidString }
    }

    func connectedNodeIds(for nodeId: UUID) -> [UUID] {
        let id = canonical(nodeId)
        return connections(for: id).map { $0.nodeIdA == id ? $0.nodeIdB : $0.nodeIdA }
    }

    // MARK: - Linux: runtime state derived from workspace.json (contracts K1/K6)

    /// Rebuild the runtime connections of one workspace from its persisted document: drops every connection of that
    /// workspace (and any in-memory one touching its nodes), then registers each persisted connection with its
    /// persisted id and terminal endpoints resolved to terminal ids. Idempotent; a removed connection or node is
    /// revoked simply by not being in the document any more.
    func restoreConnections(from graph: WorkspaceGraph) {
        let ws = graph.workspaceId
        var touched = Set(graph.payload.nodes.map(\.id))
        touched.formUnion(graph.terminalIds)
        connections = connections.filter { _, c in
            c.workspaceId != ws && !(c.workspaceId == nil && (touched.contains(c.nodeIdA) || touched.contains(c.nodeIdB)))
        }
        terminalAliases = terminalAliases.filter { !touched.contains($0.key) }
        for (nodeId, tid) in graph.terminalAliases { terminalAliases[nodeId] = tid }
        for e in graph.edges {
            connections[e.id] = ActiveConnection(id: e.id, nodeIdA: e.a, nodeIdB: e.b, type: e.type, workspaceId: ws)
        }
        logger.info("Restored \(graph.edges.count) connection(s) of workspace \(ws.uuidString.prefix(8))")
    }

    /// Forget a workspace entirely (deleted workspace).
    func forgetWorkspace(_ workspaceId: UUID) {
        connections = connections.filter { $0.value.workspaceId != workspaceId }
    }

    /// Re-read one workspace from disk (no-op when the document is gone: its connections are dropped).
    func reloadWorkspace(_ workspaceId: UUID) {
        if let g = WorkspaceGraph.load(workspaceId) {
            restoreConnections(from: g)
        } else {
            forgetWorkspace(workspaceId)
        }
    }

    /// Server start: restore the connections of every workspace on disk.
    public func restoreAllWorkspaces() {
        for id in WorkspaceGraph.allWorkspaceIds() { reloadWorkspace(id) }
    }

    // MARK: - 工作区恢复

    /// 从持久化的工作区数据重建所有运行时连接（应用启动 / 工作区切换时调用）
    /// 不重新生成连接 ID，直接复用持久化的 UUID，保证 CLI 查询结果稳定
    func restoreConnections(from workspace: WorkspaceManager, serverPort: UInt16) {
        let host = "\(Constants.interAgentServerHost):\(serverPort)"

        // 删除与此工作区节点相关的旧连接（避免多工作区污染）
        let wsNodeIds = Set(workspace.nodes.map { $0.id })
        let toRemove = connections.values.filter {
            wsNodeIds.contains($0.nodeIdA) || wsNodeIds.contains($0.nodeIdB)
        }
        toRemove.forEach { connections.removeValue(forKey: $0.id) }

        for conn in workspace.connections {
            guard connections[conn.id] == nil else { continue }
            let active = ActiveConnection(
                id: conn.id,
                nodeIdA: conn.terminalIdA,
                nodeIdB: conn.terminalIdB,
                type: .terminalToTerminal
            )
            connections[conn.id] = active
            SkillInjector.shared.inject(to: conn.terminalIdA, host: host)
            SkillInjector.shared.inject(to: conn.terminalIdB, host: host)
            logger.info("Restored T↔T: \(conn.terminalIdA.uuidString.prefix(8)) ↔ \(conn.terminalIdB.uuidString.prefix(8))")
        }

        for conn in workspace.noteConnections {
            guard connections[conn.id] == nil else { continue }
            let active = ActiveConnection(
                id: conn.id,
                nodeIdA: conn.terminalId,
                nodeIdB: conn.noteNodeId,
                type: .terminalToNote
            )
            connections[conn.id] = active
            SkillInjector.shared.inject(to: conn.terminalId, host: host)
            logger.info("Restored T↔Note: \(conn.terminalId.uuidString.prefix(8)) → \(conn.noteNodeId.uuidString.prefix(8))")
        }

        for conn in workspace.portalConnections {
            guard connections[conn.id] == nil else { continue }
            let active = ActiveConnection(
                id: conn.id,
                nodeIdA: conn.terminalId,
                nodeIdB: conn.portalNodeId,
                type: .terminalToPortal
            )
            connections[conn.id] = active
            SkillInjector.shared.inject(to: conn.terminalId, host: host)
            logger.info("Restored T↔Portal: \(conn.terminalId.uuidString.prefix(8)) → \(conn.portalNodeId.uuidString.prefix(8))")
        }

        for conn in workspace.noteToNoteConnections {
            guard connections[conn.id] == nil else { continue }
            let active = ActiveConnection(
                id: conn.id,
                nodeIdA: conn.noteNodeIdA,
                nodeIdB: conn.noteNodeIdB,
                type: .noteToNote
            )
            connections[conn.id] = active
        }

        for conn in workspace.portalToPortalConnections {
            guard connections[conn.id] == nil else { continue }
            let active = ActiveConnection(
                id: conn.id,
                nodeIdA: conn.portalIdA,
                nodeIdB: conn.portalIdB,
                type: .portalToPortal
            )
            connections[conn.id] = active
        }
    }

    // MARK: - 工具

    private func buildDefaultRopePoints() -> [[Double]] {
        Array(repeating: [0.0, 0.0], count: Constants.ropeControlPointCount)
    }
}
