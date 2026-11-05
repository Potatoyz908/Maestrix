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
        proc.onProcessExit = { [weak session] code in
            Task { @MainActor in
                session?.terminate()
                EventBroadcaster.shared.publish(
                    type: "terminalExited",
                    workspaceId: self.terminalWorkspaceMap[id],
                    payload: ["terminalId": id.uuidString, "exitCode": code.map { Int($0) } ?? -1]
                )
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
        EventBroadcaster.shared.publish(
            type: "terminalCreated",
            workspaceId: workspaceId,
            payload: metadata(for: id) ?? ["terminalId": id.uuidString]
        )
        return session
    }

    func removeTerminal(id: UUID) {
        let wsId = terminalWorkspaceMap[id]
        let existed = terminals[id] != nil
        processes[id]?.terminate()
        processes.removeValue(forKey: id)
        terminals[id]?.terminate()
        terminals.removeValue(forKey: id)
        terminalWorkspaceMap.removeValue(forKey: id)
        logger.info("Linux terminal \(id.uuidString.prefix(8)) removed")
        if existed {
            EventBroadcaster.shared.publish(type: "terminalRemoved", workspaceId: wsId, payload: ["terminalId": id.uuidString])
        }
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
        guard let proc = processes[terminalId] else { return }
        let before = (proc.cols, proc.rows)
        proc.resize(cols: cols, rows: rows)
        if (proc.cols, proc.rows) != before {
            EventBroadcaster.shared.publish(
                type: "terminalResized",
                workspaceId: terminalWorkspaceMap[terminalId],
                payload: ["terminalId": terminalId.uuidString, "cols": proc.cols, "rows": proc.rows]
            )
        }
    }

    /// PTY 元数据快照（JSON 可序列化）。无此终端时返回 nil。
    func metadata(for terminalId: UUID) -> [String: Any]? {
        guard let session = terminals[terminalId] else { return nil }
        let proc = processes[terminalId]
        var m: [String: Any] = [
            "terminalId": terminalId.uuidString,
            "command": session.command,
            "workingDirectory": session.workingDirectory,
            "running": proc?.isRunning ?? false,
            "cols": proc?.cols ?? 80,
            "rows": proc?.rows ?? 24,
            "agentType": session.agentType,
        ]
        if let pid = proc?.childPid, pid > 0 { m["pid"] = Int(pid) }
        if let started = proc?.startedAt { m["startedAt"] = ISO8601DateFormatter().string(from: started) }
        if let code = proc?.exitCode { m["exitCode"] = Int(code) }
        if let name = session.displayName { m["displayName"] = name }
        if let role = session.roleName { m["roleName"] = role }
        if let ws = terminalWorkspaceMap[terminalId] { m["workspaceId"] = ws.uuidString }
        return m
    }

    func allMetadata() -> [[String: Any]] {
        terminals.keys.compactMap { metadata(for: $0) }
            .sorted { ($0["terminalId"] as? String ?? "") < ($1["terminalId"] as? String ?? "") }
    }

    func bufferText(for terminalId: UUID) -> String? {
        processes[terminalId]?.bufferText()
    }

    func observeOutput(for terminalId: UUID, _ handler: @escaping (String) -> Void) -> (() -> Void)? {
        guard let proc = processes[terminalId] else { return nil }
        return proc.addOutputListener(handler)
    }
}
