import XCTest
@testable import OpenMaestriCore
#if canImport(Glibc)
import Glibc
#endif

/// /ws/events 生命周期事件流与 /api/terminals PTY 元数据测试。
/// 阻塞式 socket 客户端运行在后台线程，避免与 @MainActor 的 HTTP handler 死锁。
final class EventStreamTests: IsolatedDataDirTestCase {

    func testEventEncodingFormat() throws {
        let ws = UUID()
        let text = try XCTUnwrap(EventBroadcaster.encode(
            type: "terminalResized", workspaceId: ws,
            payload: ["terminalId": "ABC", "cols": 100, "rows": 30],
            date: Date(timeIntervalSince1970: 0)
        ))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "terminalResized")
        XCTAssertEqual(obj["ts"] as? String, "1970-01-01T00:00:00Z", "ISO8601 without fractional seconds")
        XCTAssertEqual(obj["workspaceId"] as? String, ws.uuidString)
        XCTAssertEqual(obj["cols"] as? Int, 100)
    }

    func testEventsRequireAuth() async throws {
        let port: UInt16 = try await MainActor.run {
            try InterAgentServer.shared.start()
            return InterAgentServer.shared.port
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let resp = try await Task.detached { try Self.exchange(port: port, request:
            "GET /ws/events HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n") }.value
        XCTAssertTrue(resp.hasPrefix("HTTP/1.1 401"), "unauthenticated /ws/events must be rejected: \(resp.prefix(40))")
    }

    func testTerminalLifecycleEventsAndMetadata() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        let termId = UUID()
        let result: [String] = try await Task.detached {
            let ws = try Self.openEventSocket(port: port, token: token)
            defer { close(ws) }
            let auth = "Authorization: Bearer \(token)\r\n"

            // 创建终端 -> terminalCreated
            let body = "{\"id\":\"\(termId.uuidString)\",\"command\":\"\",\"workingDirectory\":\"/tmp\"}"
            let post = try Self.exchange(port: port, request:
                "POST /api/terminals HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)")
            XCTAssertTrue(post.contains("200 OK"))
            let created = try Self.readEvent(ws, type: "terminalCreated")
            XCTAssertEqual(created["terminalId"] as? String, termId.uuidString)
            XCTAssertEqual(created["running"] as? Bool, true)
            XCTAssertEqual(created["cols"] as? Int, 80)

            // 元数据
            let meta = try Self.exchange(port: port, request:
                "GET /api/terminals/\(termId.uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(meta.contains("\"pid\":"), meta)
            XCTAssertTrue(meta.contains("\"rows\":24"), meta)
            let list = try Self.exchange(port: port, request:
                "GET /api/terminals HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(list.contains(termId.uuidString), list)
            let missing = try Self.exchange(port: port, request:
                "GET /api/terminals/\(UUID().uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(missing.hasPrefix("HTTP/1.1 404"))

            // resize -> terminalResized
            await MainActor.run { LinuxTerminalManager.shared.resize(terminalId: termId, cols: 120, rows: 40) }
            let resized = try Self.readEvent(ws, type: "terminalResized")
            XCTAssertEqual(resized["cols"] as? Int, 120)
            XCTAssertEqual(resized["rows"] as? Int, 40)

            // 删除 -> terminalRemoved
            let del = try Self.exchange(port: port, request:
                "DELETE /api/terminals/\(termId.uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(del.contains("200 OK"))
            let removed = try Self.readEvent(ws, type: "terminalRemoved")
            XCTAssertEqual(removed["terminalId"] as? String, termId.uuidString)
            return ["ok"]
        }.value
        XCTAssertEqual(result, ["ok"])
    }

    func testConnectionCreatedAndRemovedEvents() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        let a = UUID(), b = UUID(), connId = UUID()
        try await Task.detached {
            let auth = "Authorization: Bearer \(token)\r\n"
            // 确保存在活动 workspace
            _ = try Self.exchange(port: port, request: "GET /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            let ws = try Self.openEventSocket(port: port, token: token)
            defer { close(ws) }

            let body = "{\"id\":\"\(connId.uuidString)\",\"a\":\"\(a.uuidString)\",\"b\":\"\(b.uuidString)\",\"kind\":\"terminalToTerminal\"}"
            let post = try Self.exchange(port: port, request:
                "POST /api/connections HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)")
            XCTAssertTrue(post.contains("200 OK"), post)
            let created = try Self.readEvent(ws, type: "connectionCreated")
            XCTAssertEqual(created["connectionId"] as? String, connId.uuidString)
            XCTAssertEqual(created["kind"] as? String, "terminalToTerminal")
            XCTAssertEqual(created["a"] as? String, a.uuidString)
            XCTAssertEqual(created["b"] as? String, b.uuidString)
            XCTAssertEqual((created["ropePoints"] as? [[Double]])?.count, 21)

            let putBody = "{\"ropePoints\":[[10.0,20.0],[30.0,40.0]]}"
            let put = try Self.exchange(port: port, request:
                "PUT /api/connections/\(connId.uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(putBody.utf8.count)\r\nConnection: close\r\n\r\n\(putBody)")
            XCTAssertTrue(put.contains("200 OK"), put)
            let changed = try Self.readEvent(ws, type: "connectionChanged")
            XCTAssertEqual(changed["connectionId"] as? String, connId.uuidString)
            XCTAssertEqual(changed["kind"] as? String, "terminalToTerminal")
            XCTAssertEqual((changed["ropePoints"] as? [[Double]])?.count, 2)

            let del = try Self.exchange(port: port, request:
                "DELETE /api/connections/\(connId.uuidString) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(del.contains("200 OK"))
            let removed = try Self.readEvent(ws, type: "connectionRemoved")
            XCTAssertEqual(removed["connectionId"] as? String, connId.uuidString)
            XCTAssertEqual(removed["a"] as? String, a.uuidString)
            XCTAssertEqual(removed["kind"] as? String, "terminalToTerminal")
            let saved = try Self.readEvent(ws, type: "workspaceSaved")
            XCTAssertNotNil(saved["workspaceId"] as? String)
        }.value
    }

    func testWorkspaceSavedPreservesFloorsAndBroadcastsFloorEvents() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        // Prepare a temporary git repo for floor creation
        let tmpRepo = NSTemporaryDirectory() + "maestri-evt-floor-\(UUID().uuidString)"
        try FileManager.default.createDirectory(atPath: tmpRepo, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(atPath: tmpRepo) }
        try Self.initGitRepo(at: tmpRepo)

        try await Task.detached {
            let auth = "Authorization: Bearer \(token)\r\n"
            let getResp = try Self.exchange(port: port, request: "GET /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            let wsJson = try Self.extractJSONBody(getResp)
            let wsObj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(wsJson.utf8)) as? [String: Any])
            let payloadObj = try XCTUnwrap(wsObj["payload"] as? [String: Any])
            let wsId = try XCTUnwrap(payloadObj["id"] as? String)

            let ws = try Self.openEventSocket(port: port, token: token)
            defer { close(ws) }

            // Create floor with space in name -> slugified branch/worktree + floorCreated event
            let floorBody = "{\"name\":\"Sprint 4 UI\",\"workspaceId\":\"\(wsId)\",\"workingDirectory\":\"\(tmpRepo)\"}"
            let createFloor = try Self.exchange(port: port, request:
                "POST /api/floors HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(floorBody.utf8.count)\r\nConnection: close\r\n\r\n\(floorBody)")
            XCTAssertTrue(createFloor.contains("201 Created"), createFloor)
            let fCreated = try Self.readEvent(ws, type: "floorCreated")
            let floorId = try XCTUnwrap(fCreated["floorId"] as? String)
            XCTAssertEqual(fCreated["branchName"] as? String, "floor/sprint-4-ui")

            // Patch floor name & hooks -> floorChanged event
            let patchBody = "{\"name\":\"Sprint 4 Renamed\",\"hooks\":{\"setup\":[\"echo hi\"],\"autoRunSetup\":true}}"
            let patchFloor = try Self.exchange(port: port, request:
                "PATCH /api/floors/\(floorId)?workspace=\(wsId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(patchBody.utf8.count)\r\nConnection: close\r\n\r\n\(patchBody)")
            XCTAssertTrue(patchFloor.contains("200 OK"), patchFloor)
            let fChanged = try Self.readEvent(ws, type: "floorChanged")
            XCTAssertEqual(fChanged["name"] as? String, "Sprint 4 Renamed")

            // Save workspace snapshot WITHOUT floors (simulating WorkspaceSync R1) -> floors must be preserved!
            var staleWs = wsObj
            var stalePayload = payloadObj
            stalePayload.removeValue(forKey: "floors")
            staleWs["payload"] = stalePayload
            let staleData = try JSONSerialization.data(withJSONObject: staleWs)
            let staleStr = String(decoding: staleData, as: UTF8.self)
            let saveResp = try Self.exchange(port: port, request:
                "POST /api/workspace HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(staleStr.utf8.count)\r\nConnection: close\r\n\r\n\(staleStr)")
            XCTAssertTrue(saveResp.contains("200 OK"), saveResp)
            let wsSaved = try Self.readEvent(ws, type: "workspaceSaved")
            XCTAssertEqual(wsSaved["workspaceId"] as? String, wsId)

            let floorsResp = try Self.exchange(port: port, request:
                "GET /api/floors?workspace=\(wsId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(floorsResp.contains(floorId), "Floor must survive POST /api/workspace without floors: \(floorsResp)")

            // M3: GET /api/floors/:id/status resolves repo via floorRepoDirectory
            let statusResp = try Self.exchange(port: port, request:
                "GET /api/floors/\(floorId)/status?workspace=\(wsId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(statusResp.contains("200 OK"), statusResp)

            // M4: GET /api/floors/:id/land-preview returns merge-tree diff/stat WITHOUT side-effect WS broadcast
            let previewResp = try Self.exchange(port: port, request:
                "GET /api/floors/\(floorId)/land-preview?workspace=\(wsId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(previewResp.contains("200 OK"), previewResp)
            XCTAssertTrue(previewResp.contains("\"diff\":"), previewResp)
            XCTAssertTrue(previewResp.contains("\"treeOid\":"), previewResp)

            // L1: invalid target branch returns 400 Bad Request; unknown floor returns 404 Not Found
            let badTargetResp = try Self.exchange(port: port, request:
                "GET /api/floors/\(floorId)/land-preview?workspace=\(wsId)&target=--bad HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(badTargetResp.hasPrefix("HTTP/1.1 400"), badTargetResp)
            let missingBranchResp = try Self.exchange(port: port, request:
                "GET /api/floors/\(floorId)/land-preview?workspace=\(wsId)&target=no-such-branch HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(missingBranchResp.hasPrefix("HTTP/1.1 400"), missingBranchResp)
            let notFoundFloorResp = try Self.exchange(port: port, request:
                "GET /api/floors/\(UUID().uuidString)/land-preview?workspace=\(wsId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(notFoundFloorResp.hasPrefix("HTTP/1.1 404"), notFoundFloorResp)

            // Delete floor -> floorRemoved event
            let delFloor = try Self.exchange(port: port, request:
                "DELETE /api/floors/\(floorId)?workspace=\(wsId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(delFloor.contains("200 OK"), delFloor)
            let fRemoved = try Self.readEvent(ws, type: "floorRemoved")
            XCTAssertEqual(fRemoved["floorId"] as? String, floorId)
        }.value
    }

    func testRoutinesCrudAndEvents() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }

        let targetTid = UUID()
        try await Task.detached {
            let auth = "Authorization: Bearer \(token)\r\n"
            let ws = try Self.openEventSocket(port: port, token: token)
            defer { close(ws) }

            let createBody = "{\"name\":\"Nightly\",\"prompt\":\"make && make test\\n&&\\ngit status\",\"intervalSeconds\":120,\"targetTerminalId\":\"\(targetTid.uuidString)\"}"
            let createResp = try Self.exchange(port: port, request:
                "POST /api/routines HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(createBody.utf8.count)\r\nConnection: close\r\n\r\n\(createBody)")
            XCTAssertTrue(createResp.contains("201 Created"), createResp)
            let cEvt = try Self.readEvent(ws, type: "routineChanged")
            XCTAssertEqual(cEvt["action"] as? String, "created")
            let routineId = try XCTUnwrap(cEvt["routineId"] as? String)

            // Verify own-line && parsing in GET /api/routines
            let listResp = try Self.exchange(port: port, request:
                "GET /api/routines HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(listResp.contains("\"make && make test\""), listResp)

            // Patch routine
            let patchBody = "{\"isActive\":false,\"name\":\"Nightly v2\",\"intervalSeconds\":180}"
            let patchResp = try Self.exchange(port: port, request:
                "PATCH /api/routines/\(routineId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(patchBody.utf8.count)\r\nConnection: close\r\n\r\n\(patchBody)")
            XCTAssertTrue(patchResp.contains("200 OK"), patchResp)
            let uEvt = try Self.readEvent(ws, type: "routineChanged")
            XCTAssertEqual(uEvt["action"] as? String, "updated")

            // Run-now emits routineStarted -> routineStep -> routineFinished
            let runResp = try Self.exchange(port: port, request:
                "POST /api/routines/\(routineId)/run-now HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: 0\r\nConnection: close\r\n\r\n")
            XCTAssertTrue(runResp.contains("200 OK"), runResp)
            let started = try Self.readEvent(ws, type: "routineStarted")
            XCTAssertEqual(started["routineId"] as? String, routineId)
            let step = try Self.readEvent(ws, type: "routineStep")
            XCTAssertEqual(step["routineId"] as? String, routineId)
            let finished = try Self.readEvent(ws, type: "routineFinished")
            XCTAssertEqual(finished["routineId"] as? String, routineId)

            // Delete routine
            let delResp = try Self.exchange(port: port, request:
                "DELETE /api/routines/\(routineId) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Connection: close\r\n\r\n")
            XCTAssertTrue(delResp.contains("200 OK"), delResp)
            let dEvt = try Self.readEvent(ws, type: "routineChanged")
            XCTAssertEqual(dEvt["action"] as? String, "removed")
        }.value
    }

    /// Sidebar contract: `terminalCount` in /api/workspaces (and create/edit/duplicate responses, save response,
    /// nodeAdded/nodeRemoved/workspaceSaved events) always equals the `.terminal` nodes in workspace.json.
    func testWorkspaceTerminalCountFollowsNodesAndWorkspaceLifecycle() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let wsId = UUID()
        var copyId: String?
        defer {
            for id in [wsId.uuidString, copyId].compactMap({ $0 }) {
                _ = try? Self.exchange(port: port, request:
                    "DELETE /api/workspaces/\(id) HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer \(token)\r\nConnection: close\r\n\r\n")
            }
        }

        copyId = try await Task.detached { () -> String in
            let auth = "Authorization: Bearer \(token)\r\n"
            func send(_ method: String, _ path: String, _ body: String = "") throws -> String {
                try Self.exchange(port: port, request:
                    "\(method) \(path) HTTP/1.1\r\nHost: 127.0.0.1\r\n\(auth)Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)")
            }
            func object(_ resp: String) throws -> [String: Any] {
                try XCTUnwrap(JSONSerialization.jsonObject(with: Data(Self.extractJSONBody(resp).utf8)) as? [String: Any], resp)
            }
            func listed(_ id: String) throws -> Int? {
                let list = try XCTUnwrap(JSONSerialization.jsonObject(
                    with: Data(Self.extractJSONBody(send("GET", "/api/workspaces")).utf8)) as? [[String: Any]])
                guard let e = list.first(where: { $0["id"] as? String == id }) else { return nil }
                return try XCTUnwrap(e["terminalCount"] as? Int, "every entry carries terminalCount")
            }
            let id = wsId.uuidString
            let ws = try Self.openEventSocket(port: port, token: token)
            defer { close(ws) }

            // create -> 0
            let created = try object(send("POST", "/api/workspaces",
                "{\"id\":\"\(id)\",\"name\":\"Count \(id.prefix(4))\",\"workingDirectory\":\"/tmp\"}"))
            XCTAssertEqual(created["terminalCount"] as? Int, 0)
            XCTAssertEqual(try listed(id), 0)

            // add 2 terminals + 1 note -> 2 (nodeAdded carries the count)
            var terminalNodes: [String] = []
            for (i, type) in ["terminal", "stickyNote", "terminal"].enumerated() {
                let node = try object(send("POST", "/api/nodes?workspace=\(id)", "{\"nodeType\":\"\(type)\"}"))
                let added = try Self.readEvent(ws, type: "nodeAdded")
                XCTAssertEqual(added["terminalCount"] as? Int, [1, 1, 2][i])
                if type == "terminal" { terminalNodes.append(try XCTUnwrap(node["id"] as? String)) }
            }
            XCTAssertEqual(try listed(id), 2)

            // remove one terminal -> 1
            XCTAssertTrue(try send("DELETE", "/api/nodes/\(terminalNodes[0])?workspace=\(id)").contains("200 OK"))
            XCTAssertEqual(try Self.readEvent(ws, type: "nodeRemoved")["terminalCount"] as? Int, 1)
            XCTAssertEqual(try listed(id), 1)

            // client save (duplicate a terminal on the canvas + move it) -> 2, in the response and in workspaceSaved
            var doc = try object(send("GET", "/api/workspace?id=\(id)"))
            var payload = try XCTUnwrap(doc["payload"] as? [String: Any])
            var nodes = try XCTUnwrap(payload["nodes"] as? [[String: Any]])
            var dup = try XCTUnwrap(nodes.first(where: { ($0["content"] as? [String: Any])?["terminal"] != nil }))
            dup["id"] = UUID().uuidString
            dup["frame"] = [[10400.0, 8900.0], [500.0, 350.0]]
            nodes.append(dup)
            payload["nodes"] = nodes
            doc["payload"] = payload
            let saved = try object(send("POST", "/api/workspace",
                String(decoding: JSONSerialization.data(withJSONObject: doc), as: UTF8.self)))
            XCTAssertEqual(saved["terminalCount"] as? Int, 2)
            let savedEvent = try Self.readEvent(ws, type: "workspaceSaved")
            XCTAssertEqual(savedEvent["workspaceId"] as? String, id)
            XCTAssertEqual(savedEvent["terminalCount"] as? Int, 2)
            XCTAssertEqual(try listed(id), 2)

            // rename keeps it; duplicate copies it; delete drops the entry
            let renamed = try object(send("PUT", "/api/workspaces/\(id)", "{\"name\":\"Count renamed\"}"))
            XCTAssertEqual(renamed["terminalCount"] as? Int, 2)
            let copy = try object(send("POST", "/api/workspaces/\(id)/duplicate"))
            let cid = try XCTUnwrap(copy["id"] as? String)
            XCTAssertEqual(copy["terminalCount"] as? Int, 2)
            XCTAssertEqual(try listed(cid), 2)
            XCTAssertTrue(try send("DELETE", "/api/workspaces/\(cid)").contains("200 OK"))
            XCTAssertNil(try listed(cid))
            XCTAssertEqual(try listed(id), 2, "deleting the copy leaves the original untouched")
            return cid
        }.value
    }

    /// Rope status: `ask` turns the connection "communicating" while it runs and "idle" afterwards, each change
    /// published as `connectionChanged {workspaceId, connectionId, status}`.
    func testAskPublishesConnectionStatusChanges() async throws {
        let (port, token): (UInt16, String) = try await MainActor.run {
            try InterAgentServer.shared.start()
            return (InterAgentServer.shared.port, InterAgentServer.shared.authToken)
        }
        defer { Task { @MainActor in InterAgentServer.shared.stop() } }
        let ws = UUID(), a = UUID(), b = UUID(), connId = UUID()
        var p = WorkspacePayload(id: ws, name: "Status", workingDirectory: "/tmp")
        for (id, name) in [(a, "Caller"), (b, "Target")] {
            var t = TerminalContent(name: name, command: "bash", workingDirectory: "/tmp")
            t.id = id
            p.nodes.append(CanvasNode(id: id, frame: CGRect(x: 9800, y: 8500, width: 400, height: 280), content: .terminal(t)))
        }
        p.connections = [TerminalConnection(id: connId, terminalIdA: a, terminalIdB: b)]
        let pm = PersistenceManager.shared
        try pm.ensureWorkspaceDirectoryExists(id: ws)
        try pm.saveSync(WorkspaceDocument(payload: p), to: pm.workspaceURL(id: ws))
        await MainActor.run {
            ConnectionManager.shared.reloadWorkspace(ws)
            for (id, name) in [(a, "Caller"), (b, "Target")] {
                _ = PlatformHooks.terminals.createTerminal(id: id, command: "bash --norc --noprofile", workingDirectory: "/tmp",
                                                           workspaceId: ws, roleName: nil, displayName: name, agentType: "generic_shell")
            }
        }
        defer { Task { @MainActor in [a, b].forEach { PlatformHooks.terminals.removeTerminal(id: $0) } } }
        try await Task.sleep(for: .milliseconds(600))

        try await Task.detached {
            let evs = try Self.openEventSocket(port: port, token: token)
            defer { close(evs) }
            let body = "{\"args\":[\"ask\",\"Target\",\"echo STATUS_OK\"]}"
            let resp = try Self.exchange(port: port, request:
                "POST /cli HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer \(token)\r\nX-Terminal-ID: \(a.uuidString)\r\n" +
                "Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)")
            XCTAssertTrue(resp.contains("STATUS_OK"), resp)
            let first = try Self.readEvent(evs, type: "connectionChanged")
            XCTAssertEqual(first["connectionId"] as? String, connId.uuidString)
            XCTAssertEqual(first["status"] as? String, "communicating")
            XCTAssertEqual(first["workspaceId"] as? String, ws.uuidString)
            let second = try Self.readEvent(evs, type: "connectionChanged")
            XCTAssertEqual(second["status"] as? String, "idle")
        }.value
    }

    // MARK: - Helpers (blocking, call off the main actor)

    private static func connect(port: UInt16) throws -> Int32 {
        let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
        guard fd >= 0 else { throw NSError(domain: "test", code: 1) }
        var tv = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let rc = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Glibc.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard rc == 0 else { close(fd); throw NSError(domain: "test", code: 2) }
        return fd
    }

    private static func exchange(port: UInt16, request: String) throws -> String {
        let fd = try connect(port: port)
        defer { close(fd) }
        let data = Data(request.utf8)
        _ = data.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
        var out = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = recv(fd, &buf, buf.count, 0)
            if n <= 0 { break }
            out.append(buf, count: n)
        }
        return String(decoding: out, as: UTF8.self)
    }

    private static var rxBuffers: [Int32: Data] = [:]
    private static let rxLock = NSLock()

    private static func openEventSocket(port: UInt16, token: String) throws -> Int32 {
        let fd = try connect(port: port)
        let req = "GET /ws/events HTTP/1.1\r\nHost: 127.0.0.1:\(port)\r\nAuthorization: Bearer \(token)\r\n" +
                  "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
                  "Sec-WebSocket-Version: 13\r\n\r\n"
        let data = Data(req.utf8)
        _ = data.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
        var buf = [UInt8](repeating: 0, count: 1024)
        let n = recv(fd, &buf, buf.count, 0)
        let raw = Data(buf[..<max(n, 0)])
        guard let sep = raw.range(of: Data("\r\n\r\n".utf8)) else {
            close(fd); throw NSError(domain: "test", code: 3)
        }
        let head = String(decoding: raw[..<sep.lowerBound], as: UTF8.self)
        guard head.hasPrefix("HTTP/1.1 101") else { close(fd); throw NSError(domain: "test", code: 3, userInfo: [NSLocalizedDescriptionKey: head]) }
        rxLock.lock()
        rxBuffers[fd] = Data(raw[sep.upperBound...])
        rxLock.unlock()
        return fd
    }

    /// 读取文本帧直到收到指定 type 的事件（跳过其它事件，保留同一次 recv 中的后续帧）。
    private static func readEvent(_ fd: Int32, type: String) throws -> [String: Any] {
        rxLock.lock()
        var buffer = rxBuffers[fd] ?? Data()
        rxLock.unlock()
        defer {
            rxLock.lock()
            rxBuffers[fd] = buffer
            rxLock.unlock()
        }
        var buf = [UInt8](repeating: 0, count: 4096)
        let deadline = Date().addingTimeInterval(8)
        while Date() < deadline {
            while let frame = WebSocketFraming.decodeFrame(from: &buffer) {
                guard frame.opcode == .text,
                      let obj = try? JSONSerialization.jsonObject(with: frame.payload) as? [String: Any] else { continue }
                if obj["type"] as? String == type { return obj }
            }
            let n = recv(fd, &buf, buf.count, 0)
            if n <= 0 { break }
            buffer.append(buf, count: n)
        }
        throw NSError(domain: "test", code: 4, userInfo: [NSLocalizedDescriptionKey: "timeout waiting for \(type)"])
    }

    private static func extractJSONBody(_ httpResponse: String) throws -> String {
        guard let range = httpResponse.range(of: "\r\n\r\n") else {
            throw NSError(domain: "test", code: 5, userInfo: [NSLocalizedDescriptionKey: "missing HTTP body"])
        }
        return String(httpResponse[range.upperBound...])
    }

    private static func initGitRepo(at path: String) throws {
        let env = ProcessInfo.processInfo.environment.merging([
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
        ]) { _, new in new }
        for args in [["init"], ["commit", "--allow-empty", "-m", "init"]] {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            p.arguments = args
            p.currentDirectoryURL = URL(fileURLWithPath: path)
            p.environment = env
            try p.run()
            p.waitUntilExit()
        }
    }
}
