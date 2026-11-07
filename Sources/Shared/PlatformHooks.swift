import Foundation

/// Core ↔ UI 解耦钩子。
/// Core 层（Workspace/Connection/…）不得直接引用终端管理器与 Portal WebView 存储等
/// 依赖 AppKit/WebKit 的类型；UI 层在启动时注入实现。
@MainActor
enum PlatformHooks {
    /// 终端节点被删除：UI 层应终止 PTY 并释放视图
    static var onTerminalRemoved: ((UUID) -> Void)?
    /// Portal 节点被删除：UI 层应释放 WebView
    static var onPortalRemoved: ((UUID) -> Void)?

    /// 终端 / Portal 服务（启动时由平台层注入）
    static var terminalsService: (any TerminalManaging)?
    static var portalsService: (any PortalManaging)?
    static var terminals: any TerminalManaging {
        guard let s = terminalsService else { fatalError("PlatformHooks.terminals 未注入（须在 InterAgentServer 启动前设置）") }
        return s
    }
    static var portals: any PortalManaging {
        // K3: never crash the server (all PTYs would die) — without an injected service, use the Linux bridge, which
        // answers "portal view unavailable" when no window is connected.
        if let s = portalsService { return s }
        let bridge = LinuxPortalManager.shared
        portalsService = bridge
        return bridge
    }
    /// InterAgent 服务器当前监听端口（CLIRouter debug、连接注入用）
    nonisolated(unsafe) static var serverPort: () -> UInt16 = { 0 }
}
