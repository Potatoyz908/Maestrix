import AppKit
import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// 终端生命周期管理器
/// - 为每个 Terminal 节点创建/销毁 PTY 会话
/// - **并行启动**：所有终端 PTY 同时 fork，对标 Maestri 快速无卡顿启动
/// - 所有 PTY 写入操作必须通过此类（架构约束）
@MainActor
final class TerminalManager {
    static let shared = TerminalManager()
    private let logger = Logger.make(category: "TerminalManager")

    /// 活跃终端 UUID → 终端状态（跨工作区共存，符合 Maestri 多工作区后台运行设计）
    private(set) var terminals: [UUID: TerminalSession] = [:]
    /// 终端 UUID → 所属工作区 ID（用于按工作区查询）
    private(set) var terminalWorkspaceMap: [UUID: UUID] = [:]
    /// 终端 UUID → SwiftTermProvider（取代旧的 TerminalProviderRegistry）
    private(set) var providers: [UUID: SwiftTermProvider] = [:]
    /// 已完成 shell 初始化的终端 ID 集合
    private(set) var completedProviders: Set<UUID> = []

    private(set) var isShuttingDown = false

    private init() {}

    // MARK: - 终端创建

    func createTerminal(
        id: UUID,
        command: String,
        workingDirectory: String,
        workspaceId: UUID? = nil,
        roleName: String? = nil,
        displayName: String? = nil,
        agentType: String = "generic_shell"
    ) -> TerminalSession {
        let session = TerminalSession(
            id: id,
            command: command,
            workingDirectory: workingDirectory,
            roleName: roleName
        )
        session.agentType = agentType
        session.displayName = displayName
        terminals[id] = session
        if let wsId = workspaceId {
            terminalWorkspaceMap[id] = wsId
            Task.detached(priority: .background) {
                let store = ScrollbackStore()
                let entries = (try? store.load(terminalId: id, workspaceId: wsId)) ?? []
                if !entries.isEmpty {
                    Task { @MainActor in
                        session.bulkLoadHistory(entries.map { $0.text })
                    }
                }
            }
        }

        // 并行启动：直接创建 provider 并启动 PTY，不排队等待
        startProvider(
            id: id,
            command: command,
            workingDirectory: workingDirectory,
            workspaceId: workspaceId,
            roleName: roleName
        )

        logger.debug("Terminal \(id) created and starting, command: \(command)")
        return session
    }

    /// 便捷方法：通过 AgentPreset 创建终端（用于测试和工具栏）
    @discardableResult
    func createTerminal(
        id: UUID,
        workingDirectory: String,
        preset: AgentPreset,
        workspaceId: UUID? = nil,
        roleName: String? = nil
    ) -> TerminalSession {
        createTerminal(
            id: id,
            command: preset.command,
            workingDirectory: workingDirectory,
            workspaceId: workspaceId,
            roleName: roleName,
            agentType: preset.agentType
        )
    }

    /// 补填终端所属工作区映射（用于复用 provider 时 workspaceId 未随 session 传入的情况）
    func registerWorkspace(terminalId: UUID, workspaceId: UUID) {
        terminalWorkspaceMap[terminalId] = workspaceId
    }

    func removeTerminal(id: UUID) {
        providers[id]?.stop()
        providers.removeValue(forKey: id)
        completedProviders.remove(id)
        terminals[id]?.terminate()
        terminals.removeValue(forKey: id)
        terminalWorkspaceMap.removeValue(forKey: id)
        logger.debug("Terminal \(id) removed")
    }

    func shutdown() {
        isShuttingDown = true
        providers.values.forEach { $0.stop() }
        providers.removeAll()
        completedProviders.removeAll()
        terminals.removeAll()
    }

    // MARK: - PTY 写入（所有写入的唯一入口）

    func write(to terminalId: UUID, text: String) {
        guard let session = terminals[terminalId] else {
            logger.warning("Write to unknown terminal \(terminalId)")
            return
        }
        session.write(text)
    }

    func writeLine(to terminalId: UUID, text: String) {
        write(to: terminalId, text: text + "\n")
    }

    // MARK: - 并行启动（每个终端独立启动 PTY，互不等待）

    private func startProvider(
        id: UUID,
        command: String,
        workingDirectory: String,
        workspaceId: UUID?,
        roleName: String?
    ) {
        let provider = SwiftTermProvider(
            terminalId: id,
            command: command,
            workingDirectory: workingDirectory
        )
        provider.serverPort = InterAgentServer.shared.port
        provider.workspaceId = workspaceId
        if let prefs = try? PersistenceManager.shared.loadPreferences() {
            provider.preferredFont = NSFont(name: prefs.terminalFontFamily, size: prefs.terminalFontSize)
                ?? NSFont.monospacedSystemFont(ofSize: prefs.terminalFontSize, weight: .regular)
        }
        providers[id] = provider

        // shellReadyCallback：shell 初始化完成 → 标记 → 发通知（不再阻塞其他终端）
        provider.shellReadyCallback = { [weak self] in
            guard let self else { return }
            self.completedProviders.insert(id)
            if let wsId = workspaceId {
                NotificationCenter.default.post(
                    name: .terminalShellReady,
                    object: nil,
                    userInfo: ["terminalId": id, "workspaceId": wsId]
                )
            }
        }

        // 绑定 PTY 输出 → session.recordOutput，并建立 session 写入通道
        if let session = terminals[id] {
            provider.onDataReceived = { [weak session] text in
                Task { @MainActor in session?.recordOutput(text) }
            }
            // 建立 session → provider 写入路径（PTY 启动后 pendingWrites 需要能发送）
            session.onOutput = { [weak provider] text in provider?.write(text) }
        }

        // 直接启动 PTY（不依赖 TerminalEmbeddedView 是否已 attach）。
        // 对标 Maestri：终端创建时立刻启动 PTY 进程，无 viewport culling 延迟。
        // TerminalEmbeddedView 随后 attach 时走 re-attach 分支（已有 terminalView）。
        provider.start(in: NSRect(x: 0, y: 0, width: 600, height: 400))

        // 通知 TerminalEmbeddedView（如已存在）可以 attach provider 的 terminalView
        NotificationCenter.default.post(
            name: .terminalProviderReady,
            object: nil,
            userInfo: ["terminalId": id]
        )
    }
}
