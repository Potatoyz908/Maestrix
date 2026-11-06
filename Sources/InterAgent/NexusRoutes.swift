import Foundation

/// REST routes owned by Nexus (portal bridge, terminals, floor hooks, partituras, routines). `InterAgentServer`
/// calls `handle` first (one dispatch line); nil = not a Nexus route, the server keeps routing.
enum NexusRoutes {
    static func handle(method: String, cleanPath: String, pathParts: [String], headerMap: [String: String],
                       body: Data, origin: String?) async -> Data? {
        // K3: POST /api/portal-results {requestId, ok, result | error}
        if method == "POST" && cleanPath == "/api/portal-results" {
            guard let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
                  let rid = (json["requestId"] as? String).flatMap(UUID.init) else {
                return NexusHTTP.text("error: missing requestId", status: 400, origin: origin)
            }
            let ok = (json["ok"] as? Bool) ?? false
            let delivered = LinuxPortalManager.shared.complete(requestId: rid, ok: ok, result: json["result"],
                                                               error: json["error"] as? String)
            return delivered
                ? NexusHTTP.json(["status": "ok"], origin: origin)
                : NexusHTTP.text("error: no pending portal request \(rid.uuidString)", status: 404, origin: origin)
        }
        return nil
    }
}
