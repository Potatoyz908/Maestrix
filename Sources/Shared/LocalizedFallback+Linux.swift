import Foundation

#if os(Linux)
/// Linux 上 LocalizationManager（AppKit/Bundle .lproj）不可用：
/// 暂以 key 本身作为回退文案。后续由 Web Shell 的 i18n 资源替换。
extension String {
    var localized: String { self }
}
#endif
