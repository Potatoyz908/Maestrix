import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Entry point of the Nexus CLI commands (`partitura`, `routine`, `workspace`, `floor`).
/// `routine`, `workspace` and `floor` call the server's own REST API over loopback, so persistence, events and floor
/// hooks follow exactly the same path as the GUI.
enum NexusCLI {
    static func route(args: [String], terminalId: UUID?) async -> String {
        switch args.first {
        case "partitura": return await PartituraHandler.shared.handleAsync(args: args, terminalId: terminalId)
        case "routine": return await RoutineCLI.handle(Array(args.dropFirst()), terminalId: terminalId)
        case "workspace": return await WorkspaceCLI.handle(Array(args.dropFirst()), terminalId: terminalId)
        case "floor": return await FloorCLI.handle(Array(args.dropFirst()), terminalId: terminalId)
        default: return "error: unknown command '\(args.first ?? "")'"
        }
    }

    /// `--flag value` / `--switch` parser; positional arguments keep their order.
    struct Options {
        var positional: [String] = []
        var values: [String: String] = [:]
        var switches: Set<String> = []

        init(_ args: [String], valueFlags: Set<String>) {
            var i = 0
            while i < args.count {
                let a = args[i]
                if a.hasPrefix("--") {
                    let key = String(a.dropFirst(2))
                    if valueFlags.contains(key), i + 1 < args.count {
                        values[key] = args[i + 1]; i += 2; continue
                    }
                    switches.insert(key)
                } else {
                    positional.append(a)
                }
                i += 1
            }
        }
    }
}

/// Loopback client of this server's REST API (127.0.0.1:<port>, Bearer token).
enum NexusLoopback {
    struct Response {
        let status: Int
        let body: Data
        var text: String { String(decoding: body, as: UTF8.self) }
        var json: Any? { try? JSONSerialization.jsonObject(with: body) }
        var ok: Bool { (200..<300).contains(status) }
        /// "error: ..." line from a failed response.
        var errorText: String {
            let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return t.hasPrefix("error:") ? t : "error: \(t.isEmpty ? "HTTP \(status)" : t)"
        }
    }

    static func request(_ method: String, _ path: String, json: Any? = nil, timeout: TimeInterval = 60) async -> Response {
        let port = await MainActor.run { InterAgentServer.shared.port }
        let token = await MainActor.run { InterAgentServer.shared.authToken }
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return Response(status: 400, body: Data("error: bad path".utf8)) }
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = method
        req.setValue("127.0.0.1:\(port)", forHTTPHeaderField: "Host")
        if !token.isEmpty { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json, let data = try? JSONSerialization.data(withJSONObject: json) {
            req.httpBody = data
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return await withCheckedContinuation { cont in
            URLSession.shared.dataTask(with: req) { data, resp, err in
                if let err {
                    cont.resume(returning: Response(status: 599, body: Data("error: \(err.localizedDescription)".utf8)))
                } else {
                    cont.resume(returning: Response(status: (resp as? HTTPURLResponse)?.statusCode ?? 599, body: data ?? Data()))
                }
            }.resume()
        }
    }

    static func q(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? s }
}

// MARK: - routine

enum RoutineCLI {
    static let usage = """
    usage: omaestri routine list
           omaestri routine show "Name"
           omaestri routine create "Name" --command "..." --every 30m [--terminal "Name"] [--disabled]
           omaestri routine edit "Name" [--name "New"] [--command "..."] [--every 1h] [--terminal "Name"]
           omaestri routine enable|disable|run|delete "Name"
    """
    static let valueFlags: Set<String> = ["command", "every", "interval", "terminal", "target", "name", "daily", "weekly",
                                          "once", "count", "until", "pre-run"]

    /// `45s`, `30m`, `2h`, `1h30m`; a bare number means minutes (skill contract).
    static func parseEvery(_ s: String) -> TimeInterval? {
        let t = s.trimmingCharacters(in: .whitespaces).lowercased()
        if let n = Double(t) { return n * 60 }
        var total = 0.0, num = ""
        for ch in t {
            if ch.isNumber || ch == "." { num.append(ch); continue }
            guard let v = Double(num) else { return nil }
            switch ch { case "s": total += v; case "m": total += v * 60; case "h": total += v * 3600; case "d": total += v * 86400; default: return nil }
            num = ""
        }
        return num.isEmpty && total > 0 ? total : nil
    }

    static func fmt(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s % 3600 == 0 { return "\(s / 3600)h" }
        if s % 60 == 0 { return "\(s / 60)m" }
        return "\(s)s"
    }

    static func routines() async -> [[String: Any]] {
        let r = await NexusLoopback.request("GET", "/api/routines")
        if let list = r.json as? [[String: Any]] { return list }
        return ((r.json as? [String: Any])?["routines"] as? [[String: Any]]) ?? []
    }

    static func find(_ key: String) async -> [String: Any]? {
        let all = await routines()
        return all.first { ($0["id"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
            ?? all.first { ($0["name"] as? String) == key }
            ?? all.first { ($0["name"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
    }

    /// Terminal named `name` in the caller's workspace (node name), else the caller itself.
    @MainActor
    static func target(_ name: String?, caller: UUID?) -> UUID? {
        guard let name else {
            guard let caller, let found = NexusWorkspaces.locate(caller) else { return caller }
            return found.node.content.terminalContent?.id ?? caller
        }
        if let id = UUID(uuidString: name) { return id }
        var docs: [WorkspaceDocument] = []
        if let caller, let found = NexusWorkspaces.locate(caller) { docs.append(found.doc) }
        docs += NexusWorkspaces.allWorkspaceIds().compactMap { try? PersistenceManager.shared.loadWorkspace(id: $0) }
        for doc in docs {
            if let t = doc.payload.nodes.compactMap({ $0.content.terminalContent }).first(where: { $0.name == name || $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
                return t.id
            }
        }
        return nil
    }

    static func describe(_ r: [String: Any]) -> String {
        let name = r["name"] as? String ?? "?"
        let every = fmt((r["intervalSeconds"] as? NSNumber)?.doubleValue ?? 0)
        let state = (r["isActive"] as? Bool) == false ? "paused" : "active"
        let prompts = (r["prompts"] as? [String]) ?? [(r["prompt"] as? String) ?? ""]
        return "\(name) — every \(every), \(state) [\(String((r["id"] as? String ?? "").prefix(8)))]: \(prompts.joined(separator: " && "))"
    }

    static func handle(_ args: [String], terminalId: UUID?) async -> String {
        guard let sub = args.first else { return usage }
        let o = NexusCLI.Options(Array(args.dropFirst()), valueFlags: valueFlags)
        for unsupported in ["daily", "weekly", "once", "count", "until", "pre-run"] where o.values[unsupported] != nil {
            return "error: --\(unsupported) is not supported on Linux yet (only --every intervals)"
        }
        if o.switches.contains("reminder") { return "error: --reminder is not supported on Linux yet" }
        switch sub {
        case "list", "ls":
            let all = await routines()
            return all.isEmpty ? "No routines." : all.map(describe).joined(separator: "\n")
        case "show":
            guard let key = o.positional.first, let r = await find(key) else { return "error: routine not found" }
            return describe(r)
        case "create":
            guard let name = o.positional.first else { return "error: " + usage }
            guard let command = o.values["command"] ?? o.positional.dropFirst().first else { return "error: missing --command" }
            let every: TimeInterval?
            if let e = o.values["every"] { every = parseEvery(e) }
            else if let i = o.values["interval"] { every = Double(i) }          // seconds
            else { every = nil }
            guard let seconds = every else { return "error: missing or invalid --every (e.g. 30m)" }
            guard seconds >= 60 else { return "error: the minimum interval is 1m" }
            let terminalName = o.values["terminal"] ?? o.values["target"]
            guard let tid = await target(terminalName, caller: terminalId) else {
                return "error: terminal '\(terminalName ?? "")' not found"
            }
            let r = await NexusLoopback.request("POST", "/api/routines", json: [
                "name": name, "prompt": command, "intervalSeconds": seconds, "targetTerminalId": tid.uuidString])
            guard r.ok, let created = r.json as? [String: Any], let id = created["id"] as? String else { return r.errorText }
            if o.switches.contains("disabled") { _ = await NexusLoopback.request("POST", "/api/routines/\(id)/pause") }
            return "Created routine '\(name)' (every \(fmt(seconds)))"
        case "edit":
            guard let key = o.positional.first, let r = await find(key), let id = r["id"] as? String else { return "error: routine not found" }
            var patch: [String: Any] = [:]
            if let n = o.values["name"] { patch["name"] = n }
            if let c = o.values["command"] { patch["prompt"] = c }
            if let e = o.values["every"] {
                guard let s = parseEvery(e), s >= 60 else { return "error: invalid --every (minimum 1m)" }
                patch["intervalSeconds"] = s
            }
            if let t = o.values["terminal"] ?? o.values["target"] {
                guard let tid = await target(t, caller: terminalId) else { return "error: terminal '\(t)' not found" }
                patch["targetTerminalId"] = tid.uuidString
            }
            guard !patch.isEmpty else { return "error: nothing to change" }
            let resp = await NexusLoopback.request("PATCH", "/api/routines/\(id)", json: patch)
            return resp.ok ? "Updated routine '\(r["name"] as? String ?? key)'" : resp.errorText
        case "enable", "disable", "run", "delete":
            guard let key = o.positional.first, let r = await find(key), let id = r["id"] as? String else { return "error: routine not found" }
            let name = r["name"] as? String ?? key
            let resp: NexusLoopback.Response
            switch sub {
            case "enable": resp = await NexusLoopback.request("POST", "/api/routines/\(id)/resume")
            case "disable": resp = await NexusLoopback.request("POST", "/api/routines/\(id)/pause")
            case "run": resp = await NexusLoopback.request("POST", "/api/routines/\(id)/run-now")
            default: resp = await NexusLoopback.request("DELETE", "/api/routines/\(id)")
            }
            guard resp.ok else { return resp.errorText }
            let verb = ["enable": "Enabled", "disable": "Disabled", "run": "Running", "delete": "Deleted"][sub] ?? sub
            return "\(verb) routine '\(name)'"
        default:
            return "error: unknown routine subcommand '\(sub)'\n" + usage
        }
    }
}

// MARK: - workspace

enum WorkspaceCLI {
    static let usage = """
    usage: omaestri workspace list
           omaestri workspace create "Name" --dir PATH
           omaestri workspace rename "Current Name" "New Name"
           omaestri workspace move "Name" --group "Group" | --root
           omaestri workspace unload "Name"
    """

    static func entries() async -> [[String: Any]] {
        (await NexusLoopback.request("GET", "/api/workspaces")).json as? [[String: Any]] ?? []
    }

    static func find(_ key: String) async -> [String: Any]? {
        let all = await entries()
        return all.first { ($0["id"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
            ?? all.first { ($0["name"] as? String) == key }
            ?? all.first { ($0["name"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
    }

    static func handle(_ args: [String], terminalId: UUID?) async -> String {
        guard let sub = args.first else { return usage }
        let o = NexusCLI.Options(Array(args.dropFirst()), valueFlags: ["dir", "from", "group", "folder"])
        switch sub {
        case "list", "ls":
            let all = await entries()
            if all.isEmpty { return "No workspaces." }
            let mine = await MainActor.run { terminalId.flatMap { NexusWorkspaces.workspaceOfTerminal($0) } }
            return all.map { w in
                let id = w["id"] as? String ?? ""
                let marker = UUID(uuidString: id) == mine ? " (you)" : ""
                return "\(w["name"] as? String ?? "?")\(marker) — \(w["workingDirectory"] as? String ?? "")"
            }.joined(separator: "\n")
        case "create":
            guard let name = o.positional.first, !name.isEmpty else { return "error: " + usage }
            guard let dir = o.values["dir"] else { return "error: missing --dir PATH" }
            if o.values["from"] != nil { return "error: --from is not supported on Linux yet" }
            let path = (dir as NSString).expandingTildeInPath
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), isDir.boolValue else {
                return "error: directory '\(dir)' does not exist"
            }
            let r = await NexusLoopback.request("POST", "/api/workspaces", json: ["name": name, "workingDirectory": path])
            guard r.ok else { return r.errorText }
            if let group = o.values["group"] ?? o.values["folder"],
               let id = (r.json as? [String: Any])?["id"] as? String {
                _ = await move(id: id, group: group)
            }
            return "Created workspace '\(name)'"
        case "rename":
            guard o.positional.count >= 2, let w = await find(o.positional[0]), let id = w["id"] as? String else {
                return "error: workspace not found"
            }
            let r = await NexusLoopback.request("PUT", "/api/workspaces/\(id)", json: ["name": o.positional[1]])
            return r.ok ? "Renamed workspace to '\(o.positional[1])'" : r.errorText
        case "move":
            guard let key = o.positional.first, let w = await find(key), let id = w["id"] as? String else { return "error: workspace not found" }
            if o.switches.contains("root") { return await move(id: id, group: nil) }
            guard let group = o.values["group"] ?? o.values["folder"] else { return "error: --group \"Group\" or --root" }
            return await move(id: id, group: group)
        case "unload":
            guard let key = o.positional.first, let w = await find(key), let id = (w["id"] as? String).flatMap(UUID.init) else {
                return "error: workspace not found"
            }
            let n: Int = await MainActor.run {
                let tm = LinuxTerminalManager.shared
                let ids = tm.terminalWorkspaceMap.filter { $0.value == id }.map(\.key)
                ids.forEach { tm.unload(id: $0) }
                return ids.count
            }
            return "Unloaded workspace '\(w["name"] as? String ?? key)' (\(n) terminals stopped; scrollback saved)"
        default:
            return "error: unknown workspace subcommand '\(sub)'\n" + usage
        }
    }

    /// Sidebar layout: remove `id` from wherever it is, then add it to `group` (created if missing) or the root.
    static func move(id: String, group: String?) async -> String {
        let r = await NexusLoopback.request("GET", "/api/sidebar-layout")
        guard var layout = r.json as? [String: Any] else { return r.errorText }
        var top = (layout["topLevelItems"] as? [String]) ?? []
        var groups = (layout["groups"] as? [[String: Any]]) ?? []
        top.removeAll { $0.caseInsensitiveCompare(id) == .orderedSame }
        for i in groups.indices {
            groups[i]["items"] = ((groups[i]["items"] as? [String]) ?? []).filter { $0.caseInsensitiveCompare(id) != .orderedSame }
        }
        if let group {
            if let i = groups.firstIndex(where: { ($0["name"] as? String) == group }) {
                groups[i]["items"] = ((groups[i]["items"] as? [String]) ?? []) + [id]
            } else {
                groups.append(["id": UUID().uuidString, "name": group, "items": [id], "isExpanded": true])
            }
        } else {
            top.append(id)
        }
        layout["topLevelItems"] = top
        layout["groups"] = groups
        let w = await NexusLoopback.request("PUT", "/api/sidebar-layout", json: layout)
        return w.ok ? (group.map { "Moved workspace to '\($0)'" } ?? "Moved workspace to the root") : w.errorText
    }
}

// MARK: - floor

enum FloorCLI {
    static let usage = """
    usage: omaestri floor list
           omaestri floor create "Name" [--from PATH] [--branch NAME] [--existing-branch] [--partitura P]
           omaestri floor land "Name" [--into BRANCH] [--delete-branch] [--keep-floor]
           omaestri floor delete "Name" [--keep-branch] [--keep-folder]
           omaestri floor status ["Text"] [--state working|blocked|review|done] [--progress 2/5] [--floor "Name"] [--clear]
    """

    /// Floor status lines posted by agents (in memory; also published as `floorStatus` events for the GUI).
    @MainActor static var statuses: [UUID: [String: Any]] = [:]

    static func workspace(of terminalId: UUID?) async -> UUID? {
        await MainActor.run {
            terminalId.flatMap { NexusWorkspaces.workspaceOfTerminal($0) }
                ?? InterAgentServer.resolveWorkspaceId(pathParts: [], pm: .shared)
        }
    }

    static func floors(_ ws: UUID) async -> [[String: Any]] {
        (await NexusLoopback.request("GET", "/api/floors?workspace=\(ws.uuidString)")).json as? [[String: Any]] ?? []
    }

    static func find(_ key: String, ws: UUID) async -> [String: Any]? {
        let all = await floors(ws)
        return all.first { ($0["id"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
            ?? all.first { ($0["name"] as? String) == key }
            ?? all.first { ($0["name"] as? String)?.caseInsensitiveCompare(key) == .orderedSame }
    }

    /// The floor the caller runs on: its working directory inside a floor's worktree.
    @MainActor
    static func callerFloor(_ terminalId: UUID?, in list: [[String: Any]]) -> [String: Any]? {
        guard let tid = terminalId else { return nil }
        let cwd = LinuxTerminalManager.shared.terminals[tid]?.workingDirectory
            ?? NexusWorkspaces.locate(tid)?.node.content.terminalContent?.workingDirectory ?? ""
        return list.first { f in
            guard let wt = f["worktreePath"] as? String, !wt.isEmpty else { return false }
            return cwd == wt || cwd.hasPrefix(wt + "/")
        }
    }

    static func handle(_ args: [String], terminalId: UUID?) async -> String {
        guard let sub = args.first else { return usage }
        let o = NexusCLI.Options(Array(args.dropFirst()), valueFlags: ["from", "branch", "into", "partitura", "state",
                                                                         "progress", "floor", "dir", "pull-request", "message"])
        guard let ws = await workspace(of: terminalId) else { return "error: no workspace" }
        switch sub {
        case "list", "ls":
            let all = await floors(ws)
            if all.isEmpty { return "No floors (only Ground)." }
            let current: [UUID: [String: Any]] = await MainActor.run { FloorCLI.statuses }
            return all.map { f in
                var line = "\(f["name"] as? String ?? "?") — \(f["branchName"] as? String ?? "")"
                if let id = (f["id"] as? String).flatMap(UUID.init), let st = current[id], let text = st["text"] as? String {
                    line += "  [\(st["state"] as? String ?? "working")] \(text)"
                }
                return line
            }.joined(separator: "\n")
        case "create":
            for unsupported in ["copy", "no-git", "copy-ground"] where o.switches.contains(unsupported) {
                return "error: --\(unsupported) is not supported on Linux yet"
            }
            if o.values["pull-request"] != nil { return "error: --pull-request is not supported on Linux yet" }
            guard let name = o.positional.first, !name.isEmpty else { return "error: " + usage }
            var body: [String: Any] = ["name": name, "workspaceId": ws.uuidString]
            if let from = o.values["from"] { body["workingDirectory"] = (from as NSString).expandingTildeInPath }
            if let branch = o.values["branch"] { body["branchName"] = branch }
            if o.switches.contains("existing-branch") { body["existingBranch"] = true }
            let r = await NexusLoopback.request("POST", "/api/floors?workspace=\(ws.uuidString)", json: body, timeout: 300)
            guard r.ok, let f = r.json as? [String: Any] else { return r.errorText }
            var out = "Created floor '\(name)' on branch \(f["branchName"] as? String ?? "") at \(f["worktreePath"] as? String ?? "")"
            if let p = o.values["partitura"] {
                let a = await NexusLoopback.request("POST", "/api/partituras/\(NexusLoopback.q(p))/apply?workspace=\(ws.uuidString)", json: [:])
                out += a.ok ? "\nPlaced partitura '\(p)'" : "\n\(a.errorText)"
            }
            return out
        case "land":
            guard let key = o.positional.first, let f = await find(key, ws: ws), let id = f["id"] as? String else { return "error: floor not found" }
            let into = o.values["into"] ?? (f["branchName"] as? String ?? "")
            let r = await NexusLoopback.request("POST", "/api/floors/\(id)/land?workspace=\(ws.uuidString)", json: ["target": into], timeout: 300)
            guard r.ok else { return r.errorText }
            var out = "Landed floor '\(f["name"] as? String ?? key)' into \(into)"
            if !o.switches.contains("keep-floor") {   // skill: the floor is removed after landing unless --keep-floor
                let del = o.switches.contains("delete-branch") ? "&deleteBranch=1" : ""
                let d = await NexusLoopback.request("DELETE", "/api/floors/\(id)?workspace=\(ws.uuidString)\(del)", timeout: 300)
                out += d.ok ? " and removed it" : "\n\(d.errorText)"
            }
            return out
        case "delete", "rm":
            guard let key = o.positional.first, let f = await find(key, ws: ws), let id = f["id"] as? String else { return "error: floor not found" }
            var q = "workspace=\(ws.uuidString)"
            if !o.switches.contains("keep-branch") { q += "&deleteBranch=1" }
            if o.switches.contains("keep-folder") { q += "&removeWorktree=0" }
            let r = await NexusLoopback.request("DELETE", "/api/floors/\(id)?\(q)", timeout: 300)
            return r.ok ? "Deleted floor '\(f["name"] as? String ?? key)'" : r.errorText
        case "set-dir":
            return "error: floor set-dir is not supported on Linux yet (the server cannot move a floor's directory)"
        case "status":
            let all = await floors(ws)
            let floor: [String: Any]?
            if let name = o.values["floor"] { floor = await find(name, ws: ws) } else { floor = await MainActor.run { callerFloor(terminalId, in: all) } }
            // Ground (no floor): the status is kept under the workspace id
            let key = (floor?["id"] as? String).flatMap(UUID.init) ?? ws
            let label = floor?["name"] as? String ?? "Ground"
            if o.switches.contains("clear") {
                await MainActor.run { _ = statuses.removeValue(forKey: key) }
                EventBroadcaster.shared.publish(type: "floorStatus", workspaceId: ws, payload: ["floorId": key.uuidString, "cleared": true])
                return "Cleared status of \(label)"
            }
            guard let text = o.positional.first ?? o.values["message"] ?? o.values["state"].map({ _ in "" }) else {
                let st = await MainActor.run { statuses[key] }
                return st.map { "\(label): [\($0["state"] as? String ?? "working")] \($0["text"] as? String ?? "")" } ?? "\(label): no status"
            }
            var st: [String: Any] = ["text": text, "state": o.values["state"] ?? "working", "floorId": key.uuidString]
            if let p = o.values["progress"] { st["progress"] = p }
            if let state = st["state"] as? String, !["working", "blocked", "review", "done"].contains(state) {
                return "error: --state must be working, blocked, review or done"
            }
            let posted = st
            await MainActor.run { statuses[key] = posted }
            EventBroadcaster.shared.publish(type: "floorStatus", workspaceId: ws, payload: posted)
            return "Status of \(label): [\(posted["state"] as? String ?? "working")] \(text)"
        default:
            return "error: unknown floor subcommand '\(sub)'\n" + usage
        }
    }
}
