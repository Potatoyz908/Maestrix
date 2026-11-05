import Foundation

/// Linux 平台的无头终端管理器
/// 实现 TerminalManaging 协议，完全不依赖 AppKit/SwiftUI
@MainActor
final class LinuxTerminalManager: TerminalManaging {
    static let shared = LinuxTerminalManager()
    private let logger = Logger.make(category: "LinuxTerminalManager")

    private(set) var terminals: [UUID: TerminalSession] = [:]
    private(set) var terminalWorkspaceMap: [UUID: UUID] = [:]
    private(set) var processes: [UUID: POSIXPtyProcess] = [:]

    private init() {}

    @discardableResult
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
        }

        // 创建并启动原生 POSIX PTY 进程
        let proc = POSIXPtyProcess(terminalId: id, command: command, workingDirectory: workingDirectory)
        processes[id] = proc

        // 绑定输出流到 session
        proc.onDataReceived = { [weak session] text in
            Task { @MainActor in
                session?.recordOutput(text)
            }
        }
        proc.onProcessExit = { [weak session] _ in
            Task { @MainActor in
                session?.terminate()
            }
        }

        // session.onOutput -> proc.write
        session.onOutput = { [weak proc] text in
            proc?.write(text)
        }

        do {
            try proc.start()
        } catch {
            logger.error("Failed to start POSIX PTY for \(id.uuidString.prefix(8)): \(error)")
        }

        logger.info("Linux terminal \(id.uuidString.prefix(8)) created and PTY started")
        return session
    }

    func removeTerminal(id: UUID) {
        processes[id]?.terminate()
        processes.removeValue(forKey: id)
        terminals[id]?.terminate()
        terminals.removeValue(forKey: id)
        terminalWorkspaceMap.removeValue(forKey: id)
        logger.info("Linux terminal \(id.uuidString.prefix(8)) removed")
    }

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

    func writeRaw(to terminalId: UUID, data: Data) {
        processes[terminalId]?.writeRaw(data)
    }

    func resize(terminalId: UUID, cols: Int, rows: Int) {
        processes[terminalId]?.resize(cols: cols, rows: rows)
    }

    func bufferText(for terminalId: UUID) -> String? {
        processes[terminalId]?.bufferText()
    }

    func observeOutput(for terminalId: UUID, _ handler: @escaping (String) -> Void) -> (() -> Void)? {
        guard let proc = processes[terminalId] else { return nil }
        return proc.addOutputListener(handler)
    }
}
