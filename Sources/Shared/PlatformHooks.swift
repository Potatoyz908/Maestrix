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
}
