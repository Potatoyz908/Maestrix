import XCTest
@testable import OpenMaestriCore
#if canImport(Glibc)
import Glibc
#endif

/// Sprint 9 Codice acceptance (server side, isolated data dir, real bash PTYs):
/// K6 restore from workspace.json, K1b legacy ids, revocation on delete, K4 ask / --batch / --raw, list names.
final class CollaborationTests: IsolatedDataDirTestCase {

    // MARK: - Fixtures

    private func terminalNode(nodeId: UUID, terminalId: UUID, name: String, x: Double, manager: Bool = false) -> CanvasNode {
        var t = TerminalContent(name: name, agentType: "generic_shell", command: "bash", workingDirectory: "/tmp")
        t.id = terminalId
        t.isManager = manager
        return CanvasNode(id: nodeId, frame: CGRect(x: x, y: 8600, width: 400, height: 280), content: .terminal(t))
    }

    private func noteNode(_ id: UUID, name: String) -> CanvasNode {
        CanvasNode(id: id, frame: CGRect(x: 10500, y: 9000, width: 300, height: 200), content: .stickyNote(StickyNoteContent(name: name)))
    }

    private func save(_ payload: WorkspacePayload) throws {
        let pm = PersistenceManager.shared
        try pm.ensureWorkspaceDirectoryExists(id: payload.id)
        try pm.saveSync(WorkspaceDocument(payload: payload), to: pm.workspaceURL(id: payload.id))
    }

    private func startServer() async throws -> (UInt16, String) {
        try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
    }

    private func spawn(_ id: UUID, name: String, ws: UUID, agentType: String = "generic_shell") async {
        await MainActor.run {
            _ = PlatformHooks.terminals.createTerminal(id: id, command: "bash --norc --noprofile", workingDirectory: "/tmp",
                                                       workspaceId: ws, roleName: nil, displayName: name, agentType: agentType)
        }
        // wait for the first prompt
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline {
            let text = await MainActor.run { PlatformHooks.terminals.bufferText(for: id) ?? "" }
            if text.contains("$") || text.contains("#") { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
    }

    private func kill(_ ids: [UUID]) async {
        await MainActor.run { ids.forEach { PlatformHooks.terminals.removeTerminal(id: $0) } }
    }

    // MARK: - K6 + K1b

    func testRestoredLegacyConnectionsServeListAndAreRevokedOnDelete() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        let ws = UUID()
        let nodeA = UUID(), termA = UUID(), termB = UUID(), note = UUID(), connAB = UUID()
        var p = WorkspacePayload(id: ws, name: "Collab", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: nodeA, terminalId: termA, name: "Alpha", x: 9900),   // legacy: node id != terminal id
                   terminalNode(nodeId: termB, terminalId: termB, name: "Beta", x: 10400),
                   noteNode(note, name: "Architecture")]
        p.connections = [TerminalConnection(id: connAB, terminalIdA: nodeA, terminalIdB: termB)]       // stored with the NODE id
        p.noteConnections = [NoteConnection(terminalId: nodeA, noteNodeId: note)]
        try save(p)

        await MainActor.run { ConnectionManager.shared.restoreAllWorkspaces() }   // what open-maestri-server does at start
        await spawn(termA, name: "Alpha", ws: ws)
        await spawn(termB, name: "Beta", ws: ws)
        defer { Task { await self.kill([termA, termB]) } }

        let out = try await cli(port, token, ["list"], as: termA)
        XCTAssertTrue(out.contains("\"Beta\""), out)
        XCTAssertTrue(out.contains("\"Architecture\""), "real note name, not \"Note\": \(out)")
        let viaNodeId = try await cli(port, token, ["list"], as: nodeA)
        XCTAssertTrue(viaNodeId.contains("\"Beta\""), "X-Terminal-ID may be the legacy node id: \(viaNodeId)")

        let del = try await http(port, token, "DELETE", "/api/connections/\(connAB.uuidString)?workspace=\(ws.uuidString)")
        XCTAssertTrue(del.hasPrefix("HTTP/1.1 200"), del)
        let after = try await cli(port, token, ["list"], as: termA)
        XCTAssertFalse(after.contains("\"Beta\""), "permission revoked: \(after)")
        XCTAssertTrue(after.contains("\"Architecture\""))
        let gone = try await cli(port, token, ["ask", "Beta", "echo hi"], as: termA)
        XCTAssertTrue(gone.hasPrefix("error:"), gone)
    }

    // MARK: - K4

    func testAskSingleBatchAndRawOnShells() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let ws = UUID(), caller = UUID(), w1 = UUID(), w2 = UUID()
        var p = WorkspacePayload(id: ws, name: "Ask", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: caller, terminalId: caller, name: "Caller", x: 9800),
                   terminalNode(nodeId: w1, terminalId: w1, name: "Worker1", x: 10300),
                   terminalNode(nodeId: w2, terminalId: w2, name: "Worker2", x: 10800)]
        try save(p)
        for (a, b) in [(caller, w1), (caller, w2)] {
            let body = "{\"a\":\"\(a.uuidString)\",\"b\":\"\(b.uuidString)\",\"kind\":\"terminal\"}"
            let r = try await http(port, token, "POST", "/api/connections?workspace=\(ws.uuidString)", body: body)
            XCTAssertTrue(r.hasPrefix("HTTP/1.1 200"), r)
        }
        await spawn(caller, name: "Caller", ws: ws)
        await spawn(w1, name: "Worker1", ws: ws)
        await spawn(w2, name: "Worker2", ws: ws)
        defer { Task { await self.kill([caller, w1, w2]) } }

        let one = try await cli(port, token, ["ask", "Worker1", "echo $((6*7))"], as: caller)
        XCTAssertTrue(one.contains("42"), one)

        let batch = try await cli(port, token, ["ask", "--batch", "{\"Worker1\": \"echo BATCH_ONE\", \"Worker2\": \"echo BATCH_TWO\"}"], as: caller)
        let arr = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(batch.utf8)) as? [[String: String]], batch)
        XCTAssertEqual(Set(arr.compactMap { $0["name"] }), ["Worker1", "Worker2"])
        XCTAssertTrue(arr.first { $0["name"] == "Worker1" }?["output"]?.contains("BATCH_ONE") == true, batch)
        XCTAssertTrue(arr.first { $0["name"] == "Worker2" }?["output"]?.contains("BATCH_TWO") == true, batch)

        let raw = try await cli(port, token, ["ask", "Worker2", "--raw", "echo RAW_$((1+1))\\n"], as: caller)
        XCTAssertTrue(raw.contains("RAW_2"), raw)

        let check = try await cli(port, token, ["check", "Worker2", "5"], as: caller)
        XCTAssertTrue(check.contains("RAW_2"), check)
        XCTAssertFalse(check.contains("\u{1B}"), "no escape sequences")
    }

    func testAskParsing() {
        XCTAssertEqual(try? AskHandler.parse(["ask", "A", "hi"]).get(), .single(name: "A", text: "hi", raw: false))
        XCTAssertEqual(try? AskHandler.parse(["ask", "A", "--raw", "2\\n"]).get(), .single(name: "A", text: "2\\n", raw: true))
        XCTAssertEqual(try? AskHandler.parse(["ask", "--batch", "{\"B\":\"y\",\"A\":\"x\"}"]).get(),
                       .batch([(name: "A", prompt: "x"), (name: "B", prompt: "y")]))
        XCTAssertEqual(try? AskHandler.parse(["ask", "--batch", "A:x", "B:y"]).get(),
                       .batch([(name: "A", prompt: "x"), (name: "B", prompt: "y")]))
        XCTAssertNil(try? AskHandler.parse(["ask", "A"]).get())
        XCTAssertEqual(TerminalText.decodeRaw("a\\n\\t\\e[A\\x03"), Data([0x61, 0x0D, 0x09, 0x1B, 0x5B, 0x41, 0x03]))
        XCTAssertEqual(TerminalText.tidy("\u{1B}[31mred\u{1B}[0m  \r\n\n\n\nx\u{1B}]0;title\u{07}"), "red\n\nx")
    }

    // MARK: - K7

    func testNoteCreateReadScopeChainAndDelete() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let ws = UUID(), author = UUID(), stranger = UUID()
        var p = WorkspacePayload(id: ws, name: "Notes", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: author, terminalId: author, name: "Author", x: 9800),
                   terminalNode(nodeId: stranger, terminalId: stranger, name: "Stranger", x: 9800)]
        try save(p)
        await MainActor.run { ConnectionManager.shared.reloadWorkspace(ws) }

        let created = try await cli(port, token, ["note", "create", "alpha\nbeta\ngamma", "--name", "Spec", "--color", "yellow"], as: author)
        XCTAssertEqual(created, "Created note \"Spec\"")
        let doc = try PersistenceManager.shared.loadWorkspace(id: ws)
        let spec = try XCTUnwrap(doc.payload.nodes.first { WorkspaceGraph.noteName(of: $0) == "Spec" })
        XCTAssertTrue(doc.payload.noteConnections.contains { $0.terminalId == author && $0.noteNodeId == spec.id })
        XCTAssertLessThan(spec.frame.maxX, 9800, "placed left of the caller")

        let all = try await cli(port, token, ["note", "read", "Spec"], as: author)
        XCTAssertEqual(all, "[3 lines total]\n1    alpha\n2    beta\n3    gamma")
        let range = try await cli(port, token, ["note", "read", "Spec", "2", "5"], as: author)
        XCTAssertEqual(range, "[lines 2-3 of 3]\n2    beta\n3    gamma")
        let denied = try await cli(port, token, ["note", "read", "Spec"], as: stranger)
        XCTAssertTrue(denied.hasPrefix("error:"), "not connected -> no access: \(denied)")

        // chain: Spec <-> ChainedDoc
        let chained = UUID()
        var p2 = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        p2.nodes.append(noteNode(chained, name: "ChainedDoc"))
        p2.noteToNoteConnections.append(NoteToNoteConnection(noteNodeIdA: spec.id, noteNodeIdB: chained))
        try save(p2)
        try NoteFileManager.shared.write(filePath: NoteFileManager.shared.managedPath(workspaceId: ws, noteName: "ChainedDoc"),
                                         content: "chained content")
        let list = try await cli(port, token, ["list"], as: author)
        XCTAssertTrue(list.contains("  - name: \"Spec\"\n    - name: \"ChainedDoc\""), list)
        let r1 = try await cli(port, token, ["note", "write", "ChainedDoc", "v2"], as: author)
        XCTAssertEqual(r1, "OK")
        let r2 = try await cli(port, token, ["note", "edit", "chained", "v2", "v3"], as: author)
        XCTAssertEqual(r2, "OK")
        let r3 = try await cli(port, token, ["note", "read", "ChainedDoc"], as: author)
        XCTAssertEqual(r3, "[1 lines total]\n1    v3")

        let specPath = NoteFileManager.shared.managedPath(workspaceId: ws, noteName: "Spec")
        let r4 = try await cli(port, token, ["note", "delete", "Spec"], as: author)
        XCTAssertEqual(r4, "Deleted note \"Spec\"")
        let after = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        XCTAssertFalse(after.nodes.contains { $0.id == spec.id })
        XCTAssertFalse(after.noteConnections.contains { $0.noteNodeId == spec.id })
        XCTAssertFalse(FileManager.default.fileExists(atPath: specPath), "managed note file removed")
    }

    // MARK: - K8

    func testMaestroRecruitConnectRoleAssignAndDismiss() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let ws = UUID(), maestro = UUID(), plain = UUID(), roadmap = UUID()
        var p = WorkspacePayload(id: ws, name: "Maestro", workingDirectory: "/tmp")
        p.nodes = [terminalNode(nodeId: maestro, terminalId: maestro, name: "Maestro Lead", x: 9900, manager: true),
                   terminalNode(nodeId: plain, terminalId: plain, name: "Plain", x: 11000),
                   noteNode(roadmap, name: "Roadmap")]
        try save(p)
        await MainActor.run { ConnectionManager.shared.reloadWorkspace(ws) }
        await spawn(maestro, name: "Maestro Lead", ws: ws)
        defer { Task { await self.kill([maestro, plain]) } }

        let denied = try await cli(port, token, ["recruit", "X", "--command", "bash"], as: plain)
        XCTAssertTrue(denied.contains("not in Maestro mode"), denied)

        let role = try await cli(port, token, ["role", "create", "Reviewer", "Audit every diff."], as: maestro)
        XCTAssertTrue(role.lowercased().contains("created"), role)
        let rec = try await cli(port, token, ["recruit", "BuilderBot", "--command", "bash --norc", "--role", "Reviewer",
                                              "--color", "#FF9500"], as: maestro)
        XCTAssertTrue(rec.hasPrefix("Recruited 'BuilderBot'"), rec)

        var doc = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        let bot = try XCTUnwrap(doc.nodes.first { $0.content.terminalContent?.name == "BuilderBot" })
        let botTc = try XCTUnwrap(bot.content.terminalContent)
        XCTAssertEqual(bot.id, botTc.id, "K1: node id == terminal id")
        XCTAssertEqual(bot.frame.minX, 9900)
        XCTAssertEqual(bot.frame.minY, 8600 + 280 + 48, "first slot below the Maestro")
        XCTAssertEqual(botTc.color, "#FF9500")
        XCTAssertNotNil(botTc.assignedRoleId)
        XCTAssertTrue(doc.connections.contains { Set([$0.terminalIdA, $0.terminalIdB]) == Set([maestro, bot.id]) })
        let runningDir = await MainActor.run { PlatformHooks.terminals.terminals[bot.id]?.workingDirectory }
        XCTAssertTrue(runningDir?.contains("/.open-maestri/roles/") == true, "starts in the role directory: \(String(describing: runningDir))")
        let list = try await cli(port, token, ["list"], as: maestro)
        XCTAssertTrue(list.contains("\"BuilderBot\" - role: Reviewer"), list)

        let dup = try await cli(port, token, ["recruit", "BuilderBot", "--command", "bash"], as: maestro)
        XCTAssertTrue(dup.contains("--replace"), dup)

        let con = try await cli(port, token, ["connect", "BuilderBot", "Roadmap"], as: maestro)
        XCTAssertTrue(con.hasPrefix("Connected"), con)
        doc = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        XCTAssertTrue(doc.noteConnections.contains { $0.terminalId == bot.id && $0.noteNodeId == roadmap })

        let cleared = try await cli(port, token, ["role", "assign", "BuilderBot", "--none"], as: maestro)
        XCTAssertTrue(cleared.contains("restarted"), cleared)
        doc = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        let after = try XCTUnwrap(doc.nodes.first { $0.id == bot.id })
        XCTAssertEqual(after.frame, bot.frame, "position kept")
        XCTAssertNil(after.content.terminalContent?.assignedRoleId)
        XCTAssertTrue(doc.connections.contains { Set([$0.terminalIdA, $0.terminalIdB]) == Set([maestro, bot.id]) }, "connections kept")
        let restartedDir = await MainActor.run { PlatformHooks.terminals.terminals[bot.id]?.workingDirectory }
        XCTAssertEqual(restartedDir, "/tmp", "back to the original directory")

        let dis = try await cli(port, token, ["dismiss", "BuilderBot"], as: maestro)
        XCTAssertEqual(dis, "Dismissed 'BuilderBot'")
        doc = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        XCTAssertFalse(doc.nodes.contains { $0.id == bot.id })
        XCTAssertFalse(doc.connections.contains { $0.terminalIdA == bot.id || $0.terminalIdB == bot.id })
        XCTAssertFalse(doc.noteConnections.contains { $0.terminalId == bot.id })
        let gone = await MainActor.run { PlatformHooks.terminals.terminals[bot.id] == nil }
        XCTAssertTrue(gone)
    }

    func testRecruitGridSlots() {
        let m = CGRect(x: 100, y: 200, width: 400, height: 300)
        XCTAssertEqual(MaestroHandlers.recruitFrame(below: m, index: 0), CGRect(x: 100, y: 548, width: 400, height: 300))
        XCTAssertEqual(MaestroHandlers.recruitFrame(below: m, index: 2), CGRect(x: 948, y: 548, width: 400, height: 300))
        let o = MaestroHandlers.parseRecruit(["recruit", "Bot", "--dir", "~/x", "--replace", "--floor", "F", "--icon", "star"])
        XCTAssertEqual(o?.name, "Bot"); XCTAssertEqual(o?.dir, "~/x"); XCTAssertEqual(o?.floor, "F")
        XCTAssertEqual(o?.icon, "star"); XCTAssertEqual(o?.replace, true)
        XCTAssertNil(MaestroHandlers.parseRecruit(["recruit", "--replace"]))
    }

    // MARK: - K12 (real git worktree, real hooks, real PTY environment)

    private func git(_ args: [String], in dir: String) throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: dir)
        p.environment = ProcessInfo.processInfo.environment.merging([
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"]) { $1 }
        try p.run(); p.waitUntilExit()
        XCTAssertEqual(p.terminationStatus, 0, "git \(args)")
    }

    private func waitForFile(_ path: String, _ timeout: TimeInterval = 10) async -> String? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let s = try? String(contentsOfFile: path, encoding: .utf8), !s.isEmpty { return s }
            try? await Task.sleep(for: .milliseconds(100))
        }
        return nil
    }

    func testFloorHooksAndTerminalEnvironmentAgainstRealWorktree() async throws {
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let base = NSTemporaryDirectory() + "om-k12-\(UUID().uuidString.prefix(8))"
        let repo = base + "/repo", marks = base + "/marks"
        try FileManager.default.createDirectory(atPath: repo, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(atPath: marks, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: base) }
        try git(["init", "-q"], in: repo)
        try git(["commit", "-q", "--allow-empty", "-m", "init"], in: repo)

        let ws = UUID()
        try save(WorkspacePayload(id: ws, name: "K12", workingDirectory: repo))
        let q = "?workspace=\(ws.uuidString)"

        // create: Setup runs automatically (hooks given at creation, Auto-run on)
        let setup = "printf %s \\\"$MAESTRI_FLOOR_NAME|$MAESTRI_BRANCH_NAME\\\" > \(marks)/setup.txt"
        let createBody = "{\"name\":\"Hooks QA\",\"workspaceId\":\"\(ws.uuidString)\",\"workingDirectory\":\"\(repo)\"," +
                         "\"hooks\":{\"setup\":[\"\(setup)\"],\"autoRunSetup\":true}}"
        let created = try await http(port, token, "POST", "/api/floors\(q)", body: createBody)
        XCTAssertTrue(created.hasPrefix("HTTP/1.1 201"), created)
        let doc = try PersistenceManager.shared.loadWorkspace(id: ws).payload
        let floor = try XCTUnwrap(doc.floors.first { $0.name == "Hooks QA" })
        XCTAssertTrue(FileManager.default.fileExists(atPath: floor.worktreePath + "/.git"), "real worktree at \(floor.worktreePath)")
        let setupOut = await waitForFile(marks + "/setup.txt")
        XCTAssertEqual(setupOut, "Hooks QA|\(floor.branchName)", "Setup ran on create")

        // PATCH hooks: run + teardown
        let patch = "{\"hooks\":{\"setup\":[\"\(setup)\"],\"run\":[\"pwd > \(marks)/run.txt\"]," +
                    "\"teardown\":[\"printf %s \\\"$MAESTRI_FLOOR_PATH\\\" > \(marks)/teardown.txt\"],\"autoRunSetup\":true}}"
        let patched = try await http(port, token, "PATCH", "/api/floors/\(floor.id.uuidString)\(q)", body: patch)
        XCTAssertTrue(patched.hasPrefix("HTTP/1.1 200"), patched)

        let ran = try await http(port, token, "POST", "/api/floors/\(floor.id.uuidString)/hooks/run\(q)", body: "{\"phase\":\"run\"}")
        XCTAssertTrue(ran.hasPrefix("HTTP/1.1 200") && ran.contains("\"status\":\"ok\""), ran)
        let runOut = await waitForFile(marks + "/run.txt", 3)
        XCTAssertEqual(runOut?.trimmingCharacters(in: .whitespacesAndNewlines),
                       URL(fileURLWithPath: floor.worktreePath).resolvingSymlinksInPath().path, "run hook runs in the floor")

        // terminal created with floorId gets the floor variables in its environment
        let tid = UUID()
        let termBody = "{\"id\":\"\(tid.uuidString)\",\"command\":\"bash --norc --noprofile\",\"workingDirectory\":\"/tmp\"," +
                       "\"workspaceId\":\"\(ws.uuidString)\",\"floorId\":\"\(floor.id.uuidString)\"}"
        let term = try await http(port, token, "POST", "/api/terminals", body: termBody)
        XCTAssertTrue(term.hasPrefix("HTTP/1.1 200"), term)
        defer { Task { await self.kill([tid]) } }
        try? await Task.sleep(for: .milliseconds(500))
        await MainActor.run { PlatformHooks.terminals.write(to: tid, text: "env | grep ^MAESTRI_ > \(marks)/env.txt\r") }
        let env = await waitForFile(marks + "/env.txt") ?? ""
        XCTAssertTrue(env.contains("MAESTRI_FLOOR_NAME=Hooks QA"), env)
        XCTAssertTrue(env.contains("MAESTRI_BRANCH_NAME=\(floor.branchName)"), env)
        XCTAssertTrue(env.contains("MAESTRI_FLOOR_PATH=\(floor.worktreePath)"), env)

        // delete: Teardown runs while the worktree still exists
        let del = try await http(port, token, "DELETE", "/api/floors/\(floor.id.uuidString)\(q)")
        XCTAssertTrue(del.hasPrefix("HTTP/1.1 200"), del)
        let teardownOut = await waitForFile(marks + "/teardown.txt", 3)
        XCTAssertEqual(teardownOut, floor.worktreePath, "Teardown ran on delete")
        XCTAssertFalse(FileManager.default.fileExists(atPath: floor.worktreePath), "worktree removed")
    }

    // MARK: - SSH (phase A) with a fake ssh (OPEN_MAESTRI_SSH_BIN)

    func testSSHValidationQuotingAndArgv() throws {
        for bad in ["-oProxyCommand=x", "a b", ";rm", "x$(id)", "", "-"] {
            XCTAssertThrowsError(try SSHTarget(host: bad).validated(), "host \(bad)")
        }
        XCTAssertThrowsError(try SSHTarget(host: "ok", user: "-l").validated())
        XCTAssertThrowsError(try SSHTarget(host: "ok", port: 0).validated())
        XCTAssertThrowsError(try SSHTarget(host: "ok", identityFile: "-i/x").validated())
        XCTAssertThrowsError(try SSHTarget(host: "ok", identityFile: "/no/such/key").validated())
        XCTAssertEqual(SSHCommandBuilder.quote("it's"), "'it'\\''s'")
        let t = try SSHTarget(host: "srv.example", user: "me", port: 2222, remotePath: "/srv/my proj").validated()
        let argv = SSHCommandBuilder.terminalArgv(t, command: "", controlDir: "/d/run")
        XCTAssertEqual(argv.prefix(2), ["ssh", "-tt"].prefix(2).map { $0 == "ssh" ? SSHCommandBuilder.binary : $0 }[...])
        XCTAssertTrue(argv.contains("ControlMaster=auto") && argv.contains("ControlPath=/d/run/ssh-%C") && argv.contains("ControlPersist=10m"))
        let dd = try XCTUnwrap(argv.firstIndex(of: "--"))
        XCTAssertEqual(Array(argv[(dd + 1)...]), ["me@srv.example", "cd -- '/srv/my proj' && exec \"${SHELL:-bash}\" -l"])
        XCTAssertEqual(Array(argv[argv.firstIndex(of: "-p")!...].prefix(2)), ["-p", "2222"])
        XCTAssertEqual(SSHCommandBuilder.terminalArgv(t, command: "claude", controlDir: "/d").last, "cd -- '/srv/my proj' && claude")
        XCTAssertEqual(SSHCommandBuilder.remoteCd("~/a b"), "cd ~/'a b'")
    }

    func testSSHConfigHostsFollowIncludeAndSkipPatterns() throws {
        let dir = NSTemporaryDirectory() + "om-sshcfg-\(UUID().uuidString.prefix(6))"
        try FileManager.default.createDirectory(atPath: dir + "/conf.d", withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: dir) }
        try "Host *\n  User nobody\nInclude conf.d/*.conf\nHost web prod-?\n  HostName 10.0.0.5\n  User deploy\n  Port 2200\n".write(toFile: dir + "/config", atomically: true, encoding: .utf8)
        try "Host db\n  HostName db.internal\n  IdentityFile ~/.ssh/db_key\n".write(toFile: dir + "/conf.d/db.conf", atomically: true, encoding: .utf8)
        let hosts = SSHConfigParser.hosts(path: dir + "/config")
        XCTAssertEqual(hosts.map(\.alias), ["db", "web"])
        XCTAssertEqual(hosts[1], SSHHostEntry(alias: "web", hostName: "10.0.0.5", user: "deploy", port: 2200, identityFile: nil))
        XCTAssertEqual(hosts[0].identityFile, "~/.ssh/db_key")
    }

    func testSSHRoutesAndRemoteTerminalWithFakeSSH() async throws {
        let base = NSTemporaryDirectory() + "om-fakessh-\(UUID().uuidString.prefix(6))"
        try FileManager.default.createDirectory(atPath: base, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: base); unsetenv("OPEN_MAESTRI_SSH_BIN"); unsetenv("OPEN_MAESTRI_SSH_CONFIG") }
        let log = base + "/argv.log", fake = base + "/ssh"
        // fake ssh: records its argv (one per line, then ---); answers list-dir; host "locked" needs a password
        let script = """
        #!/bin/sh
        { for a in "$@"; do printf '%s\\n' "$a"; done; echo ---; } >> '\(log)'
        for a in "$@"; do case "$a" in *locked*) echo 'Permission denied (publickey).' >&2; exit 255;; esac; done
        last=""; for a in "$@"; do last="$a"; done
        case "$last" in *'ls -1Ap'*) printf '/home/me/proj\\nsrc/\\nREADME.md\\n';; esac
        exit 0
        """
        try script.write(toFile: fake, atomically: true, encoding: .utf8)
        chmod(fake, 0o755)
        try "Host box\n  HostName 192.0.2.7\n  User me\n".write(toFile: base + "/config", atomically: true, encoding: .utf8)
        setenv("OPEN_MAESTRI_SSH_BIN", fake, 1)
        setenv("OPEN_MAESTRI_SSH_CONFIG", base + "/config", 1)
        let (port, token) = try await startServer()
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        func body(_ r: String) -> String { r.range(of: "\r\n\r\n").map { String(r[$0.upperBound...]) } ?? r }

        let hosts = try await http(port, token, "GET", "/api/ssh/hosts")
        XCTAssertTrue(body(hosts).contains("\"alias\":\"box\"") && body(hosts).contains("192.0.2.7"), hosts)

        // malicious input: 400 and ssh never runs
        for bad in ["-oProxyCommand=touch /tmp/pwn", "a b", ";rm -rf ~"] {
            let b = String(decoding: try JSONSerialization.data(withJSONObject: ["host": bad]), as: UTF8.self)
            let r = try await http(port, token, "POST", "/api/ssh/test", body: b)
            XCTAssertTrue(r.hasPrefix("HTTP/1.1 400"), r)
            let w = String(decoding: try JSONSerialization.data(withJSONObject: ["name": "X", "ssh": ["host": bad]]), as: UTF8.self)
            let rw = try await http(port, token, "POST", "/api/workspaces", body: w)
            XCTAssertTrue(rw.hasPrefix("HTTP/1.1 400"), rw)
        }
        let listBad = try await http(port, token, "GET", "/api/ssh/list-dir?host=-oProxyCommand%3Dx")
        XCTAssertTrue(listBad.hasPrefix("HTTP/1.1 400"), listBad)
        XCTAssertFalse(FileManager.default.fileExists(atPath: log), "no ssh call for invalid input")

        let ok = try await http(port, token, "POST", "/api/ssh/test", body: "{\"host\":\"box\",\"port\":2222}")
        XCTAssertTrue(body(ok).contains("\"ok\":true"), ok)
        let locked = try await http(port, token, "POST", "/api/ssh/test", body: "{\"host\":\"locked\"}")
        XCTAssertTrue(body(locked).contains("\"needsPassword\":true") && body(locked).contains("\"ok\":false"), locked)
        let ls = try await http(port, token, "GET", "/api/ssh/list-dir?host=box&path=%2Fhome%2Fme%2Fproj")
        XCTAssertTrue(body(ls).contains("\"path\":\"\\/home\\/me\\/proj\"") || body(ls).contains("\"path\":\"/home/me/proj\""), ls)
        XCTAssertTrue(body(ls).contains("{\"isDir\":true,\"name\":\"src\"}") && body(ls).contains("{\"isDir\":false,\"name\":\"README.md\"}"), ls)

        // SSH workspace persisted (manifest + workspace.json) and its terminal runs `ssh -tt …` on the remote
        let wsBody = "{\"name\":\"Remote\",\"ssh\":{\"host\":\"box\",\"user\":\"me\",\"remotePath\":\"/srv/it's here\"}}"
        let created = try await http(port, token, "POST", "/api/workspaces", body: wsBody)
        XCTAssertTrue(created.hasPrefix("HTTP/1.1 201"), created)
        let wsId = try XCTUnwrap((try JSONSerialization.jsonObject(with: Data(body(created).utf8)) as? [String: Any])?["id"] as? String)
        let doc = try PersistenceManager.shared.loadWorkspace(id: UUID(uuidString: wsId)!)
        XCTAssertEqual(doc.payload.locationType, "ssh")
        XCTAssertEqual(doc.payload.ssh?.host, "box")
        XCTAssertEqual(doc.payload.workingDirectory, "/srv/it's here")
        let listed = try await http(port, token, "GET", "/api/workspaces")
        XCTAssertTrue(body(listed).contains("\"host\" : \"box\"") || body(listed).contains("\"host\":\"box\""), listed)

        try? FileManager.default.removeItem(atPath: log)
        let tid = UUID()
        let term = try await http(port, token, "POST", "/api/terminals",
                                  body: "{\"id\":\"\(tid.uuidString)\",\"command\":\"\",\"workspaceId\":\"\(wsId)\"}")
        XCTAssertTrue(term.hasPrefix("HTTP/1.1 200"), term)
        defer { Task { await self.kill([tid]) } }
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline, !((try? String(contentsOfFile: log, encoding: .utf8))?.contains("---") ?? false) {
            try? await Task.sleep(for: .milliseconds(100))
        }
        let argv = (try? String(contentsOfFile: log, encoding: .utf8))?.components(separatedBy: "\n") ?? []
        XCTAssertEqual(argv.first, "-tt", argv.joined(separator: " | "))
        XCTAssertTrue(argv.contains("ControlMaster=auto") && argv.contains { $0.hasPrefix("ControlPath=") && $0.hasSuffix("/run/ssh-%C") })
        let dd = try XCTUnwrap(argv.firstIndex(of: "--"))
        XCTAssertEqual(argv[dd + 1], "me@box")
        XCTAssertEqual(argv[dd + 2], "cd -- '/srv/it'\\''s here' && exec \"${SHELL:-bash}\" -l")
    }

    // MARK: - HTTP helpers (blocking sockets, off the main actor)

    private func cli(_ port: UInt16, _ token: String, _ args: [String], as terminal: UUID) async throws -> String {
        let body = String(decoding: try JSONSerialization.data(withJSONObject: ["args": args]), as: UTF8.self)
        let resp = try await http(port, token, "POST", "/cli", body: body, extra: "X-Terminal-ID: \(terminal.uuidString)\r\n")
        guard let r = resp.range(of: "\r\n\r\n") else { return resp }
        return String(resp[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func http(_ port: UInt16, _ token: String, _ method: String, _ path: String, body: String = "",
                      extra: String = "") async throws -> String {
        try await Task.detached {
            let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
            guard fd >= 0 else { throw NSError(domain: "test", code: 1) }
            defer { close(fd) }
            var tv = timeval(tv_sec: 60, tv_usec: 0)
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
            var addr = sockaddr_in()
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = port.bigEndian
            addr.sin_addr.s_addr = inet_addr("127.0.0.1")
            let rc = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Glibc.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
            guard rc == 0 else { throw NSError(domain: "test", code: 2) }
            let req = "\(method) \(path) HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer \(token)\r\n\(extra)" +
                      "Content-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
            let data = Data(req.utf8)
            _ = data.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
            var out = Data()
            var buf = [UInt8](repeating: 0, count: 4096)
            while true {
                let n = recv(fd, &buf, buf.count, 0)
                if n <= 0 { break }
                out.append(buf, count: n)
            }
            return String(decoding: out, as: UTF8.self)
        }.value
    }
}
