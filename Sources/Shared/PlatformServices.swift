import Foundation

/// 终端管理抽象：InterAgent Handler 只依赖此协议，不依赖 PTY/AppKit/SwiftTerm。
/// macOS：TerminalManager（SwiftTerm）；Linux：由 Sentinela 的 PTY 后端实现。
@MainActor
protocol TerminalManaging: AnyObject {
    var terminals: [UUID: TerminalSession] { get }
    var terminalWorkspaceMap: [UUID: UUID] { get }

    @discardableResult
    func createTerminal(
        id: UUID,
        command: String,
        workingDirectory: String,
        workspaceId: UUID?,
        roleName: String?,
        displayName: String?,
        agentType: String
    ) -> TerminalSession
    func removeTerminal(id: UUID)
    func write(to terminalId: UUID, text: String)
    func writeLine(to terminalId: UUID, text: String)

    /// 终端当前完整 buffer 的纯文本；无 PTY/视图时返回 nil（调用方回退到 session.recentOutput）
    func bufferText(for terminalId: UUID) -> String?
    /// 订阅 PTY 输出；返回取消订阅闭包，无 PTY 时返回 nil。回调在 PTY 输出线程触发。
    func observeOutput(for terminalId: UUID, _ handler: @escaping (String) -> Void) -> (() -> Void)?
}

extension TerminalManaging {
    @discardableResult
    func createTerminal(
        id: UUID,
        command: String,
        workingDirectory: String,
        workspaceId: UUID? = nil,
        roleName: String? = nil,
        agentType: String = "generic_shell"
    ) -> TerminalSession {
        createTerminal(id: id, command: command, workingDirectory: workingDirectory,
                       workspaceId: workspaceId, roleName: roleName,
                       displayName: nil, agentType: agentType)
    }
}

/// Portal（浏览器）抽象：macOS = WKWebView；Linux = Web Shell 内的 iframe/webview 通过桥接实现。
@MainActor
protocol PortalManaging: AnyObject {
    func ensureWebView(for portalId: UUID, initialURL: String?)
    func hasWebView(for portalId: UUID) -> Bool
    /// 标题与当前 URL；WebView 不存在时返回 nil
    func portalInfo(for portalId: UUID) -> (title: String?, url: String?)?
    /// 可访问性树文本（omaestri portal snapshot）
    func accessibilityTree(for portalId: UUID) async -> String?

    func goBack(portalId: UUID) async throws
    func goForward(portalId: UUID) async throws
    func reload(portalId: UUID) async throws
    func navigate(portalId: UUID, to urlString: String) async throws
    func screenshot(portalId: UUID) async throws -> String
    func evaluate(portalId: UUID, javascript: String) async throws -> String
}
