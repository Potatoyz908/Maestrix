import Foundation
#if canImport(OSLog)
import OSLog
#endif

/// 单个终端会话状态
@MainActor
final class TerminalSession {
    let id: UUID
    let command: String
    let workingDirectory: String
    let roleName: String?
    /// 节点的 agent 类型（来自 TerminalContent.agentType），供 AskHandler 选择等待策略
    var agentType: String = "generic_shell"
    /// 用户在 UI 中为此节点设置的显示名称（来自 TerminalContent.name）
    var displayName: String?
    /// Agent 实际分配的名称（来自 OMAESTRI_AGENT_NAME 环境变量，由 MaestroHandlers.recruit 注入）
    var agentName: String?
    /// 终端当前工作目录（由 PTY OSC 7 回调实时更新）
    private(set) var currentDirectory: String?
    private(set) var isRunning: Bool = false
    private(set) var isIdle: Bool = true
    /// 是否有通过 IPC（omaestri ask 等）触发的任务正在执行中
    /// 只有此标志为 true 时，任务完成后才会触发红点通知
    private(set) var hasActiveTask: Bool = false

    /// PTY 写入回调（TerminalEmbeddedView.makeNSView 后设置）
    var onOutput: ((String) -> Void)? {
        didSet {
            // onOutput 一旦设置，立即 flush 待写队列（解决时序竞态）
            if onOutput != nil && !pendingWrites.isEmpty {
                let pending = pendingWrites
                pendingWrites.removeAll()
                pending.forEach { onOutput?($0) }
            }
        }
    }

    /// onOutput 未就绪时暂存待写文本
    private var pendingWrites: [String] = []

    /// 近期输出环形缓冲区（最多 500 行，供 omaestri check 使用）
    /// 使用环形索引避免 removeFirst 的 O(n) 拷贝开销
    private var outputRing: [String] = []
    private var outputRingStart: Int = 0  // 逻辑起始位置
    private var outputRingCount: Int = 0  // 当前有效行数
    private let bufferMaxLines = 500

    private let activityMonitor = TerminalActivityMonitor()

    init(id: UUID, command: String, workingDirectory: String, roleName: String?) {
        self.id = id
        self.command = command
        self.workingDirectory = workingDirectory
        self.roleName = roleName

        activityMonitor.onStatusChanged = { [weak self] isActive in
            guard let self else { return }
            if !isActive {
                self.markIdle()
            }
        }
        activityMonitor.start()
    }

    func write(_ text: String) {
        if let cb = onOutput {
            cb(text)
        } else {
            // PTY 尚未初始化，暂存到队列（最多缓存 100 条）
            if pendingWrites.count < 100 { pendingWrites.append(text) }
        }
    }

    /// 记录 PTY 输出到缓存（由 SwiftTermProvider 在收到输出时调用）
    func recordOutput(_ text: String) {
        let newLines = text.components(separatedBy: "\n")
        appendToRing(newLines)
        isIdle = false
        activityMonitor.recordOutput()
    }

    /// 批量加载历史记录（仅写入 buffer，不触发 activityMonitor 或 Notification）
    /// 用于 scrollback 恢复场景，避免逐行触发大量副作用
    func bulkLoadHistory(_ lines: [String]) {
        appendToRing(lines)
    }

    /// 获取最近 N 行输出
    func recentOutput(lines: Int = 20) -> String {
        let count = min(lines, outputRingCount)
        guard count > 0 else { return "" }
        var result: [String] = []
        result.reserveCapacity(count)
        // 从环形缓冲区尾部取 count 行
        let startIdx = (outputRingStart + outputRingCount - count) % outputRing.count
        for i in 0..<count {
            result.append(outputRing[(startIdx + i) % outputRing.count])
        }
        return result.joined(separator: "\n")
    }

    // MARK: - Ring Buffer 内部实现

    /// 将新行追加到环形缓冲区（O(1) 均摊，无数组元素移动）
    private func appendToRing(_ newLines: [String]) {
        // 初始化环形缓冲区（首次写入时分配固定容量）
        if outputRing.isEmpty {
            outputRing = Array(repeating: "", count: bufferMaxLines)
        }
        for line in newLines {
            let writeIdx = (outputRingStart + outputRingCount) % bufferMaxLines
            outputRing[writeIdx] = line
            if outputRingCount < bufferMaxLines {
                outputRingCount += 1
            } else {
                // 缓冲区已满，覆盖最旧元素，移动起始指针
                outputRingStart = (outputRingStart + 1) % bufferMaxLines
            }
        }
    }

    /// 标记存在通过 IPC 触发的活跃任务（由 AskHandler 在注入 prompt 前调用）
    func markActiveTask() {
        hasActiveTask = true
    }

    /// 标记空闲（由 activityMonitor 回调触发）
    /// 仅当从非空闲切换到空闲，且有活跃 IPC 任务时才发出通知（避免普通 shell 输出误触发红点）
    func markIdle() {
        guard !isIdle else { return }
        isIdle = true
        guard hasActiveTask else { return }
        hasActiveTask = false
        NotificationCenter.default.post(
            name: .terminalBecameIdle,
            object: nil,
            userInfo: ["terminalId": id]
        )
    }

    /// 更新当前工作目录（由 SwiftTermProvider.hostCurrentDirectoryUpdate 调用）
    func updateCurrentDirectory(_ directory: String?) {
        guard let dir = directory, !dir.isEmpty, dir != currentDirectory else { return }
        currentDirectory = dir
        NotificationCenter.default.post(
            name: .terminalDirectoryChanged,
            object: nil,
            userInfo: ["terminalId": id, "directory": dir]
        )
    }

    func terminate() {
        isRunning = false
        activityMonitor.stop()
    }
}
