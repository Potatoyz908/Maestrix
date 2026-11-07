import Foundation

/// REST routes for remote workspaces (docs/linux-ssh-spec.md, phase A). Invalid input answers 400 without running ssh.
enum SSHRoutes {
    private static func json(_ obj: Any, status: Int = 200, origin: String?) -> Data {
        let data = (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])) ?? Data("{}".utf8)
        return InterAgentServer.buildHTTPResponse(status: status, contentType: "application/json; charset=utf-8", data: data, allowedOrigin: origin)
    }

    private static func bad(_ message: String, origin: String?) -> Data {
        InterAgentServer.buildHTTPResponse(status: 400, body: "error: \(message)", allowedOrigin: origin)
    }

    private static func query(_ pathParts: [String]) -> [String: Any] {
        guard pathParts.count > 1 else { return [:] }
        var out: [String: Any] = [:]
        for pair in pathParts[1].components(separatedBy: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1).map(String.init)
            if kv.count == 2 { out[kv[0]] = kv[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? kv[1] }
        }
        return out
    }

    /// Runs the blocking ssh call off the cooperative pool.
    private static func runSSH(_ argv: [String], timeout: TimeInterval) async -> SSHCommandBuilder.Output {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async { cont.resume(returning: SSHCommandBuilder.run(argv, timeout: timeout)) }
        }
    }

    static func handle(method: String, cleanPath: String, pathParts: [String], body: Data, origin: String?) async -> Data? {
        guard cleanPath.hasPrefix("/api/ssh/") else { return nil }

        if method == "GET" && cleanPath == "/api/ssh/hosts" {
            return json(SSHConfigParser.hosts().map(\.json), origin: origin)
        }

        if method == "POST" && cleanPath == "/api/ssh/test" {
            let body = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any]) ?? [:]
            guard let raw = SSHTarget(json: body) else { return bad("missing host", origin: origin) }
            let target: SSHTarget
            do { target = try raw.validated() } catch { return bad("\(error)", origin: origin) }
            let out = await runSSH(SSHCommandBuilder.testArgv(target), timeout: 12)
            let ok = out.status == 0
            let message = ok ? "Connected to \(target.destination)"
                : (out.timedOut ? "Connection timed out" : (out.stderr.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    ? "ssh exited with status \(out.status)" : out.stderr.trimmingCharacters(in: .whitespacesAndNewlines)))
            return json(["ok": ok, "message": message, "needsPassword": !ok && SSHCommandBuilder.needsPassword(out.stderr)], origin: origin)
        }

        if method == "GET" && cleanPath == "/api/ssh/list-dir" {
            let q = query(pathParts)
            guard let raw = SSHTarget(json: q) else { return bad("missing host", origin: origin) }
            let target: SSHTarget
            do { target = try raw.validated() } catch { return bad("\(error)", origin: origin) }
            let path = q["path"] as? String
            if let p = path, p.contains("\0") || p.contains("\n") { return bad("invalid path", origin: origin) }
            let out = await runSSH(SSHCommandBuilder.listDirArgv(target, path: path), timeout: 15)
            guard out.status == 0 else {
                if SSHCommandBuilder.needsPassword(out.stderr) { return json(["needsPassword": true], origin: origin) }
                return json(["error": out.stderr.trimmingCharacters(in: .whitespacesAndNewlines)], status: 502, origin: origin)
            }
            var lines = out.stdout.components(separatedBy: "\n").filter { !$0.isEmpty }
            let resolved = lines.isEmpty ? (path ?? "~") : lines.removeFirst()
            let entries: [[String: Any]] = lines.compactMap { l in
                guard l != "./", l != "../" else { return nil }
                let isDir = l.hasSuffix("/")
                return ["name": isDir ? String(l.dropLast()) : l, "isDir": isDir]
            }
            return json(["path": resolved, "entries": entries], origin: origin)
        }
        return nil
    }

    /// argv of the remote terminal for a workspace that has an `ssh` target (nil = local workspace).
    /// Throws when the stored target is invalid (nothing is executed then).
    static func terminalArgv(workspaceId: UUID?, command: String) throws -> [String]? {
        guard let wsId = workspaceId, let doc = try? PersistenceManager.shared.loadWorkspace(id: wsId),
              let target = doc.payload.ssh else { return nil }
        var t = try target.validated()
        if t.remotePath == nil { t.remotePath = doc.payload.workingDirectory }
        return SSHCommandBuilder.terminalArgv(t, command: command, controlDir: SSHCommandBuilder.controlDir)
    }
}
