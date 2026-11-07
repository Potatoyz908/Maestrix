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
        // K12: POST /api/terminals with `floorId` (+ the `id` the GUI always sends, K1): queue the floor variables for
        // that PTY, then let the regular handler create it (returns nil).
        if method == "POST" && cleanPath == "/api/terminals",
           let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
           let floorId = (json["floorId"] as? String).flatMap(UUID.init),
           let tid = (json["id"] as? String).flatMap(UUID.init) {
            let wsId = (json["workspaceId"] as? String).flatMap(UUID.init)
            if let env = FloorHookRunner.terminalEnvironment(floorId: floorId, workspaceId: wsId) {
                await MainActor.run { LinuxTerminalManager.shared.pendingEnvironment[tid] = env }
            }
            return nil
        }

        // K12: POST /api/floors/:id/hooks/run {phase: setup|run|teardown, hookId?}
        if method == "POST" && cleanPath.hasPrefix("/api/floors/") && cleanPath.hasSuffix("/hooks/run") {
            let raw = String(cleanPath.dropFirst("/api/floors/".count).dropLast("/hooks/run".count))
            guard let floorId = UUID(uuidString: raw) else { return NexusHTTP.text("error: invalid floor ID", status: 400, origin: origin) }
            let json = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any]) ?? [:]
            let phase = (json["phase"] as? String) ?? NexusHTTP.query(pathParts)["phase"] ?? "run"
            guard FloorHookRunner.phases.contains(phase) else {
                return NexusHTTP.text("error: phase must be setup, run or teardown", status: 400, origin: origin)
            }
            let hookIndex: Int? = (json["hookId"] as? Int) ?? (json["hookId"] as? String).flatMap(Int.init)
            let pm = PersistenceManager.shared
            var order = NexusWorkspaces.allWorkspaceIds()
            if let ws = InterAgentServer.resolveWorkspaceId(pathParts: pathParts, pm: pm), let i = order.firstIndex(of: ws) {
                order.remove(at: i); order.insert(ws, at: 0)
            }
            for wsId in order {
                guard let doc = try? pm.loadWorkspace(id: wsId), let entry = doc.payload.floors.first(where: { $0.id == floorId }) else { continue }
                // a hookId naming the command itself also works (hooks are plain command strings)
                let index = hookIndex ?? (json["hookId"] as? String).flatMap { FloorHookRunner.commands(entry.hooks, phase: phase).firstIndex(of: $0) }
                if json["hookId"] != nil && index == nil {
                    return NexusHTTP.text("error: hook not found", status: 404, origin: origin)
                }
                let root = FloorHookRunner.rootDirectory(of: entry, fallback: doc.payload.workingDirectory)
                let outcomes = await withCheckedContinuation { (cont: CheckedContinuation<[FloorHookRunner.Outcome], Never>) in
                    DispatchQueue.global(qos: .utility).async {
                        cont.resume(returning: FloorHookRunner.run(phase: phase, index: index, entry: entry, rootDirectory: root, workspaceId: wsId))
                    }
                }
                let ok = outcomes.allSatisfy { $0.exitCode == 0 }
                return NexusHTTP.json(["status": ok ? "ok" : "failed", "floorId": floorId.uuidString, "phase": phase,
                                       "results": outcomes.map(\.json)], origin: origin)
            }
            return NexusHTTP.text("error: floor not found", status: 404, origin: origin)
        }

        // R13: POST /api/routines/:id/run-now — separate Enter, waits between chained prompts (RoutineRunNow)
        if method == "POST" && cleanPath.hasPrefix("/api/routines/") && cleanPath.hasSuffix("/run-now") {
            let raw = String(cleanPath.dropFirst("/api/routines/".count).dropLast("/run-now".count))
            guard let rid = UUID(uuidString: raw) else { return NexusHTTP.text("error: invalid routine ID", status: 400, origin: origin) }
            let pm = PersistenceManager.shared
            let container = (try? pm.load(RoutinesContainer.self, from: pm.routinesURL)) ?? RoutinesContainer()
            guard let routine = container.routines.first(where: { $0.id == rid }) else {
                return NexusHTTP.text("error: routine not found", status: 404, origin: origin)
            }
            let started = await MainActor.run { RoutineRunNow.start(routine) }
            return started
                ? NexusHTTP.json(["status": "ok", "id": rid.uuidString], origin: origin)
                : NexusHTTP.text("error: the routine's target terminal is not running", status: 404, origin: origin)
        }

        // K11: partituras
        if cleanPath == "/api/partituras" || cleanPath.hasPrefix("/api/partituras/") {
            return await partituras(method: method, cleanPath: cleanPath, pathParts: pathParts, body: body, origin: origin)
        }

        // K13: POST /api/terminals/:id/restart | /unload
        if method == "POST" && cleanPath.hasPrefix("/api/terminals/")
            && (cleanPath.hasSuffix("/restart") || cleanPath.hasSuffix("/unload")) {
            let parts = cleanPath.dropFirst("/api/terminals/".count).split(separator: "/").map(String.init)
            guard parts.count == 2, let tid = UUID(uuidString: parts[0]) else {
                return NexusHTTP.text("error: invalid terminal ID", status: 400, origin: origin)
            }
            let ok: Bool = await MainActor.run {
                parts[1] == "restart" ? LinuxTerminalManager.shared.restart(id: tid) : LinuxTerminalManager.shared.unload(id: tid)
            }
            guard ok else { return NexusHTTP.text("error: terminal not found", status: 404, origin: origin) }
            return NexusHTTP.json(["status": "ok", "id": tid.uuidString, "action": parts[1]], origin: origin)
        }
        return nil
    }

    private static func partituras(method: String, cleanPath: String, pathParts: [String], body: Data, origin: String?) async -> Data {
        let tail = cleanPath == "/api/partituras" ? "" : String(cleanPath.dropFirst("/api/partituras/".count))
        let parts = tail.split(separator: "/").map { String($0).removingPercentEncoding ?? String($0) }
        let json = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any]) ?? [:]
        switch (method, parts.count) {
        case ("GET", 0):
            return NexusHTTP.json(PartituraStore.all().map(PartituraStore.summary), origin: origin)
        case ("GET", 1):
            guard let p = PartituraStore.find(parts[0]) else { return NexusHTTP.text("error: partitura not found", status: 404, origin: origin) }
            return NexusHTTP.json(PartituraStore.summary(p), origin: origin)
        case ("POST", 0):
            guard let name = (json["name"] as? String)?.trimmingCharacters(in: .whitespaces), !name.isEmpty else {
                return NexusHTTP.text("error: missing name", status: 400, origin: origin)
            }
            do {
                var p: [String: Any]
                if let nodes = json["nodes"] as? [[String: Any]] {            // layout already captured by the GUI
                    p = ["name": name, "nodes": nodes, "connections": json["connections"] ?? []]
                    if let roles = json["roles"] { p["roles"] = roles }
                } else {                                                       // capture from a workspace on disk
                    guard let wsId = (json["workspaceId"] as? String).flatMap(UUID.init)
                            ?? InterAgentServer.resolveWorkspaceId(pathParts: pathParts, pm: .shared),
                          let doc = try? PersistenceManager.shared.loadWorkspace(id: wsId) else {
                        return NexusHTTP.text("error: workspace not found", status: 404, origin: origin)
                    }
                    let ids = (json["nodeIds"] as? [String])?.compactMap(UUID.init) ?? doc.payload.nodes.map(\.id)
                    p = try PartituraStore.capture(name: name, nodeIds: ids, from: doc)
                }
                if let existing = PartituraStore.find(name) { p["id"] = existing["id"]; p["createdAt"] = existing["createdAt"] }
                return NexusHTTP.json(PartituraStore.summary(try PartituraStore.save(p)), status: 201, origin: origin)
            } catch {
                return NexusHTTP.text("error: \(error.localizedDescription)", status: 400, origin: origin)
            }
        case ("DELETE", 1):
            return PartituraStore.delete(parts[0])
                ? NexusHTTP.json(["status": "ok"], origin: origin)
                : NexusHTTP.text("error: partitura not found", status: 404, origin: origin)
        case ("POST", 2) where parts[1] == "apply" || parts[1] == "insert":
            guard let p = PartituraStore.find(parts[0]) else { return NexusHTTP.text("error: partitura not found", status: 404, origin: origin) }
            guard let wsId = InterAgentServer.resolveWorkspaceId(pathParts: pathParts, pm: .shared)
                    ?? (json["workspaceId"] as? String).flatMap(UUID.init),
                  let doc = try? PersistenceManager.shared.loadWorkspace(id: wsId) else {
                return NexusHTTP.text("error: workspace not found", status: 404, origin: origin)
            }
            let at: CGPoint = (json["at"] as? [Double]).flatMap { $0.count == 2 ? CGPoint(x: $0[0], y: $0[1]) : nil }
                ?? PartituraStore.freeSpot(in: doc)
            do {
                let inserted = try await MainActor.run { try PartituraStore.insert(p, into: wsId, at: at) }
                return NexusHTTP.json(["status": "ok", "workspaceId": wsId.uuidString,
                                       "nodes": inserted.nodes.compactMap { NexusWorkspaces.json($0) },
                                       "connectionIds": inserted.connections.map(\.uuidString)], origin: origin)
            } catch {
                return NexusHTTP.text("error: \(error)", status: 500, origin: origin)
            }
        default:
            return NexusHTTP.text("error: unsupported partitura request", status: 400, origin: origin)
        }
    }
}
