import Foundation

/// K3 — portal bridge for the headless Linux server.
///
/// The WebKitGTK views live in the GTK process, so every operation is a round trip:
///   server → `/ws/events`  `portalCommand {requestId, workspaceId, portalId, op, args}`
///   GUI    → `POST /api/portal-results {requestId, ok, result | error}`
/// With no GUI subscribed, or no answer within `timeout`, the call fails with a readable error
/// ("portal view unavailable: ...") — never a crash. `screenshot` answers base64 PNG.
@MainActor
final class LinuxPortalManager: PortalManaging {
    static let shared = LinuxPortalManager()

    nonisolated static let operations: Set<String> = [
        "create", "close", "navigate", "back", "forward", "reload", "info", "snapshot", "text", "html", "screenshot",
        "evaluate", "click", "fill", "type", "key", "hover", "scroll", "drag", "wait", "select", "check", "focus",
        "logs-start", "logs",
    ]

    /// Seconds to wait for the GUI answer (tests shorten it).
    nonisolated(unsafe) static var timeout: TimeInterval = 30

    private let pending = PortalPendingRequests()

    private init() {}

    // MARK: - Generic command

    /// Sends `op` to the GUI that shows `portalId` and returns its result as text.
    nonisolated func command(_ op: String, portalId: UUID, args: [String: Any] = [:]) async throws -> String {
        guard Self.operations.contains(op) else { throw nexusError("unknown portal operation '\(op)'") }
        guard EventBroadcaster.shared.subscriberCount > 0 else {
            throw nexusError("portal view unavailable: no Open Maestri window is connected to this server")
        }
        let wsId = NexusWorkspaces.locate(portalId)?.wsId
        let requestId = UUID()
        var payload: [String: Any] = ["requestId": requestId.uuidString, "portalId": portalId.uuidString, "op": op, "args": args]
        if !JSONSerialization.isValidJSONObject(payload) { payload["args"] = [:] }
        return try await pending.wait(requestId: requestId, timeout: Self.timeout) {
            EventBroadcaster.shared.publish(type: "portalCommand", workspaceId: wsId, payload: payload)
        }
    }

    /// `POST /api/portal-results`. Returns false when nobody waits for `requestId` (late or unknown answer).
    nonisolated func complete(requestId: UUID, ok: Bool, result: Any?, error: String?) -> Bool {
        let outcome: Result<String, Error>
        if ok {
            outcome = .success(Self.text(result))
        } else {
            outcome = .failure(nexusError(error?.isEmpty == false ? error! : "portal operation failed"))
        }
        return pending.resolve(requestId: requestId, outcome)
    }

    nonisolated static func text(_ value: Any?) -> String {
        switch value {
        case nil, is NSNull: return ""
        case let s as String: return s
        case let n as NSNumber: return n.stringValue
        default:
            if let v = value, JSONSerialization.isValidJSONObject(v),
               let data = try? JSONSerialization.data(withJSONObject: v, options: [.sortedKeys]) {
                return String(decoding: data, as: UTF8.self)
            }
            return String(describing: value!)
        }
    }

    // MARK: - PortalManaging

    func ensureWebView(for portalId: UUID, initialURL: String?) {
        // The GUI creates the WebView when the portal node reaches the canvas (nodeAdded, K2).
    }

    func hasWebView(for portalId: UUID) -> Bool {
        guard let found = NexusWorkspaces.locate(portalId) else { return false }
        if case .portal = found.node.content { return true }
        return false
    }

    func portalInfo(for portalId: UUID) -> (title: String?, url: String?)? {
        guard let found = NexusWorkspaces.locate(portalId), case .portal(let p) = found.node.content else { return nil }
        return (p.name, p.currentURL)
    }

    func accessibilityTree(for portalId: UUID) async -> String? {
        try? await command("snapshot", portalId: portalId)
    }

    func goBack(portalId: UUID) async throws { _ = try await command("back", portalId: portalId) }
    func goForward(portalId: UUID) async throws { _ = try await command("forward", portalId: portalId) }
    func reload(portalId: UUID) async throws { _ = try await command("reload", portalId: portalId) }

    func navigate(portalId: UUID, to urlString: String) async throws {
        _ = try await command("navigate", portalId: portalId, args: ["url": urlString])
    }

    func screenshot(portalId: UUID) async throws -> String {
        try await command("screenshot", portalId: portalId)
    }

    func evaluate(portalId: UUID, javascript: String) async throws -> String {
        try await command("evaluate", portalId: portalId, args: ["js": javascript])
    }
}

/// requestId → continuation, resolved by the HTTP thread or by the timeout, whichever comes first (exactly once).
final class PortalPendingRequests: @unchecked Sendable {
    private var waiting: [UUID: CheckedContinuation<String, Error>] = [:]
    private let lock = NSLock()

    func wait(requestId: UUID, timeout: TimeInterval, send: () -> Void) async throws -> String {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<String, Error>) in
            lock.lock()
            waiting[requestId] = cont
            lock.unlock()
            send()
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
                _ = self?.resolve(requestId: requestId, .failure(nexusError(
                    "portal view unavailable: the window did not answer within \(Int(timeout)) s")))
            }
        }
    }

    @discardableResult
    func resolve(requestId: UUID, _ outcome: Result<String, Error>) -> Bool {
        lock.lock()
        let cont = waiting.removeValue(forKey: requestId)
        lock.unlock()
        guard let cont else { return false }
        cont.resume(with: outcome)
        return true
    }
}
