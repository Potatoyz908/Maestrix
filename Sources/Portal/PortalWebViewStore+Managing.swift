import Foundation
import WebKit

extension PortalWebViewStore: PortalManaging {
    func ensureWebView(for portalId: UUID, initialURL: String?) {
        _ = createWebView(for: portalId, initialURL: initialURL)
    }

    func hasWebView(for portalId: UUID) -> Bool {
        webView(for: portalId) != nil
    }

    func portalInfo(for portalId: UUID) -> (title: String?, url: String?)? {
        guard let wv = webView(for: portalId) else { return nil }
        return (wv.title, wv.url?.absoluteString)
    }

    func accessibilityTree(for portalId: UUID) async -> String? {
        guard let wv = webView(for: portalId) else { return nil }
        return await PortalSnapshotService.shared.buildAccessibilityTree(for: wv)
    }
}
