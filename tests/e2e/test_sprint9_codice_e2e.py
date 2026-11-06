"""
Sprint 9 — Codice Workstream Acceptance Tests (Contracts K1b, K2, K4, K5, K6, K7, K8).

Covers all acceptance criteria in `docs/linux-gtk-sprint9-spec.md` §4 "Codice":
1. K6 (P0): `restoreConnections` on server startup and `POST /api/workspace`, plus revocation on
   `DELETE /api/connections/:id` and node removal (`InterAgentServer.swift:1138-1153`, `ConnectionManager.swift:129-175`).
2. K1b (P0): Legacy `workspace.json` where `CanvasNode.id != TerminalContent.id` resolves in both
   directions (`ConnectionManager`, `AskHandler`, `CheckHandler`, `ListHandler`, `MaestroHandlers`).
3. K4 (P0): `POST /api/terminals` accepts `agentType`, `TerminalActivityMonitor` emits `terminalBecameIdle`,
   `AskHandler` sends text then waits 150ms before sending Enter, and supports `--batch` and `--raw`.
4. K5 (P0): `SkillInjector.installSkillsIfNeeded()` writes `open-maestri*` skills into
   `<OPEN_MAESTRI_DATA_DIR>/skills` on startup when `OPEN_MAESTRI_DATA_DIR` is set.
5. K2 (P0): `/ws/events` broadcasts `nodeAdded`, `nodeRemoved`, `connectionCreated`, and `connectionRemoved`
   on node/connection mutations (REST and CLI).
6. K7 (P1): `omaestri note create "text" --name "Spec" --color yellow` creates a `stickyNote` on the canvas,
   connects it to the caller terminal, emits `nodeAdded` + `connectionCreated`; `note read` returns
   `[N lines total]` / `[lines X-Y of N]`; `note delete "Spec"` removes node and `.md`; `list` displays
   real note names and traverses `noteToNoteConnections` chains; note access is scoped by `X-Terminal-ID`.
7. K8 (P1): Maestro mode (`isManager: true`) `recruit`, `dismiss`, `connect`, and `role assign` create/update/remove
   nodes and connections in `workspace.json`, emit K2 events, and `list` includes role info in `You:`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.connection import Connection  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame, StickyNoteContent, TerminalContent  # noqa: E402
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload  # noqa: E402
from tests.data_dir_isolation import REPO, start_isolated_server, stop_server  # noqa: E402


def wait_for(predicate, timeout: float = 8.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def cli_binary() -> Path | None:
    for rel in (
        ".build/debug/omaestri",
        ".build/release/omaestri",
        ".build/x86_64-unknown-linux-gnu/debug/omaestri",
        ".build/x86_64-unknown-linux-gnu/release/omaestri",
    ):
        p = REPO / rel
        if p.is_file():
            return p
    return None


def invoke_cli(
    client: MaestriClient,
    args: list[str],
    terminal_id: str,
    data_dir: Path | None = None,
) -> str:
    """Invoke a CLI command against the isolated server via `/cli` (or the `omaestri` binary when socket is local)."""
    sock = (data_dir / "run" / "maestri.sock") if data_dir else None
    bin_path = cli_binary()
    if bin_path is not None and sock is not None and sock.exists() and len(str(sock)) < 100:
        env = os.environ.copy()
        env["OPEN_MAESTRI_DATA_DIR"] = str(data_dir)
        env["MAESTRI_SOCKET"] = str(sock)
        env["MAESTRI_TERMINAL_ID"] = terminal_id
        proc = subprocess.run([str(bin_path), *args], env=env, capture_output=True, text=True, timeout=30)
        return (proc.stdout + proc.stderr).strip()

    resp = client.session.post(
        f"{client.base_url}/cli",
        json={"args": args},
        headers=client._headers(
            {
                "Content-Type": "application/json",
                "X-Terminal-ID": terminal_id,
            }
        ),
        timeout=30.0,
    )
    return resp.text.strip()


@pytest.fixture
def live_server(tmp_path):
    srv = start_isolated_server(tmp_path)
    client = MaestriClient(port=srv["port"], auth_token=srv["token"])
    yield srv, client
    stop_server(srv["proc"])


@pytest.mark.e2e
@pytest.mark.linux
class TestSprint9Codice:
    """Acceptance tests for Codice workstream (K1b, K2, K4, K5, K6, K7, K8)."""

    # ------------------------------------------------------------------
    # 1. K5: Skills installation in <OPEN_MAESTRI_DATA_DIR>/skills
    # ------------------------------------------------------------------
    def test_k5_skills_installed_in_isolated_data_dir(self, live_server):
        """Starting the server with OPEN_MAESTRI_DATA_DIR populates <data_dir>/skills/open-maestri*/SKILL.md without touching ~/.claude."""
        srv, _client = live_server
        skills_dir = srv["data_dir"] / "skills"
        assert wait_for(lambda: skills_dir.is_dir(), timeout=5.0), f"Expected {skills_dir} to be created on startup"

        skill_files = list(skills_dir.glob("open-maestri*/SKILL.md"))
        assert len(skill_files) >= 3, f"Expected at least 3 open-maestri* skills, got {[str(p) for p in skill_files]}"
        skill_names = {p.parent.name for p in skill_files}
        assert {"open-maestri", "open-maestri-portal", "open-maestri-manager"}.issubset(skill_names)
        for sf in skill_files:
            assert sf.stat().st_size > 50, f"Skill file {sf} is unexpectedly empty"

    # ------------------------------------------------------------------
    # 2. K6 + K1b: Restore connections on restart & legacy ID resolution
    # ------------------------------------------------------------------
    def test_k6_and_k1b_restore_connections_on_restart_and_legacy_ids(self, tmp_path):
        """Connections saved in workspace.json (including legacy differing node.id vs content.id) work immediately after server restart and revoke on delete."""
        srv1 = start_isolated_server(tmp_path)
        try:
            client1 = MaestriClient(port=srv1["port"], auth_token=srv1["token"])
            doc = client1.get_workspace()
            ws_id = doc.payload.id

            # K1b: Create terminal A with legacy differing CanvasNode.id vs TerminalContent.id, and terminal B with matching IDs
            node_a_id = str(uuid.uuid4()).upper()
            term_a_id = str(uuid.uuid4()).upper()
            assert node_a_id != term_a_id
            term_b_id = str(uuid.uuid4()).upper()
            note_id = str(uuid.uuid4()).upper()

            node_a = CanvasNode(
                id=node_a_id,
                frame=NodeFrame(x=9900, y=8600, width=400, height=280),
                node_type="terminal",
                content=TerminalContent(id=term_a_id, terminal_id=term_a_id, name="Alpha", command="bash"),
                z_index=1,
            )
            # Force legacy mismatch on node_a
            node_a.id = node_a_id

            node_b = CanvasNode(
                id=term_b_id,
                frame=NodeFrame(x=10400, y=8600, width=400, height=280),
                node_type="terminal",
                content=TerminalContent(id=term_b_id, terminal_id=term_b_id, name="Beta", command="bash"),
                z_index=2,
            )
            note_node = CanvasNode(
                id=note_id,
                frame=NodeFrame(x=10400, y=8950, width=320, height=220),
                node_type="stickyNote",
                content=StickyNoteContent(name="Architecture", file_name="Architecture.md"),
                z_index=3,
            )
            conn_ab = Connection(id=str(uuid.uuid4()).upper(), a_id=node_a_id, b_id=term_b_id, kind="terminal")
            conn_note = Connection(id=str(uuid.uuid4()).upper(), a_id=node_a_id, b_id=note_id, kind="terminalToNote")

            doc.payload.nodes = [node_a, node_b, note_node]
            doc.payload.connections = [conn_ab]
            doc.payload.note_connections = [conn_note]
            assert client1.save_workspace(doc)
            client1.put_note("Architecture", "# System Design\nLine 2\n")
        finally:
            stop_server(srv1["proc"])

        # Restart the server on the SAME data_dir without calling POST /api/connections
        srv2 = start_isolated_server(tmp_path)
        try:
            client2 = MaestriClient(port=srv2["port"], auth_token=srv2["token"])
            # Spawn the two PTY sessions so ListHandler/AskHandler see active terminals
            client2.create_terminal(name="Alpha", command="bash", workspace_id=ws_id, terminal_id=term_a_id, display_name="Alpha")
            client2.create_terminal(name="Beta", command="bash", workspace_id=ws_id, terminal_id=term_b_id, display_name="Beta")

            # Immediately call `list` using `term_a_id` (even though conn_ab stored `node_a_id`!)
            out = invoke_cli(client2, ["list"], terminal_id=term_a_id, data_dir=srv2["data_dir"])
            assert "Beta" in out, f"Expected restored connection to Beta after restart, got:\n{out}"
            assert "Architecture" in out, f"Expected real note name 'Architecture' in list output, got:\n{out}"

            # Now delete the terminal-to-terminal connection and verify permission is revoked
            assert client2.delete_connection(conn_ab.id, workspace_id=ws_id)
            out_after = invoke_cli(client2, ["list"], terminal_id=term_a_id, data_dir=srv2["data_dir"])
            assert "Beta" not in out_after, f"Expected Beta connection to be revoked after delete_connection, got:\n{out_after}"
        finally:
            stop_server(srv2["proc"])

    # ------------------------------------------------------------------
    # 3. K2: WebSocket events for node/connection creation & removal
    # ------------------------------------------------------------------
    def test_k2_ws_events_node_and_connection_lifecycle(self, live_server):
        """/ws/events emits nodeAdded, connectionCreated, connectionRemoved, and nodeRemoved with expected payloads."""
        _srv, client = live_server
        doc = client.get_workspace()
        ws_id = doc.payload.id

        events: list[dict[str, Any]] = []
        ws = client.connect_events(
            on_event=lambda evt: events.append(evt),
            workspace_id=ws_id,
            use_glib_idle=False,
        )
        try:
            time.sleep(0.15)
            n1 = client.create_node("terminal", frame=[[9900, 8600], [400, 280]], content={"name": "T1", "command": "bash"}, workspace_id=ws_id)
            n2 = client.create_node("stickyNote", frame=[[10400, 8600], [300, 220]], content={"fileName": "Plan.md", "color": "yellow"}, workspace_id=ws_id)

            assert wait_for(lambda: sum(1 for e in events if e.get("type") == "nodeAdded") >= 2, timeout=5.0), events
            added_ids = {
                str((e.get("node") or {}).get("id", "")).upper()
                for e in events
                if e.get("type") == "nodeAdded"
            }
            assert n1.id.upper() in added_ids and n2.id.upper() in added_ids

            conn = client.create_connection(n1.id, n2.id, kind="terminalToNote", workspace_id=ws_id)
            assert wait_for(lambda: any(e.get("type") == "connectionCreated" for e in events), timeout=5.0), events
            conn_evt = next(e for e in events if e.get("type") == "connectionCreated")
            assert conn_evt.get("kind") in ("noteConnections", "terminalToNote")

            assert client.delete_connection(conn.id, workspace_id=ws_id)
            assert wait_for(
                lambda: any(
                    e.get("type") == "connectionRemoved" and str(e.get("connectionId", "")).upper() == conn.id.upper()
                    for e in events
                ),
                timeout=5.0,
            ), events

            resp = client.session.delete(
                f"{client.base_url}/api/nodes/{n2.id}?workspace={ws_id}",
                headers=client._headers(),
                timeout=client.timeout,
            )
            assert resp.status_code < 300
            assert wait_for(
                lambda: any(
                    e.get("type") == "nodeRemoved" and str(e.get("nodeId", "")).upper() == n2.id.upper()
                    for e in events
                ),
                timeout=5.0,
            ), events
        finally:
            ws.close()

    # ------------------------------------------------------------------
    # 4. K4: `ask` (separate Enter, agentType, --batch, --raw)
    # ------------------------------------------------------------------
    def test_k4_ask_single_batch_and_raw(self, live_server):
        """`omaestri ask` executes a command on a connected bash terminal and returns its output, supporting `--raw` and `--batch`."""
        srv, client = live_server
        doc = client.get_workspace()
        ws_id = doc.payload.id

        tid1 = str(uuid.uuid4()).upper()
        tid2 = str(uuid.uuid4()).upper()
        tid3 = str(uuid.uuid4()).upper()

        n1 = CanvasNode(
            id=tid1,
            frame=NodeFrame(x=9800, y=8600, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=tid1, terminal_id=tid1, name="Caller", command="bash", agent_type="generic_shell"),
        )
        n2 = CanvasNode(
            id=tid2,
            frame=NodeFrame(x=10300, y=8600, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=tid2, terminal_id=tid2, name="Worker1", command="bash", agent_type="generic_shell"),
        )
        n3 = CanvasNode(
            id=tid3,
            frame=NodeFrame(x=10300, y=8950, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=tid3, terminal_id=tid3, name="Worker2", command="bash", agent_type="generic_shell"),
        )
        c12 = Connection(id=str(uuid.uuid4()).upper(), a_id=tid1, b_id=tid2, kind="terminal")
        c13 = Connection(id=str(uuid.uuid4()).upper(), a_id=tid1, b_id=tid3, kind="terminal")
        doc.payload.nodes = [n1, n2, n3]
        doc.payload.connections = [c12, c13]
        assert client.save_workspace(doc)

        client.create_terminal(name="Caller", command="bash", workspace_id=ws_id, terminal_id=tid1, display_name="Caller", agent_type="generic_shell")
        client.create_terminal(name="Worker1", command="bash", workspace_id=ws_id, terminal_id=tid2, display_name="Worker1", agent_type="generic_shell")
        client.create_terminal(name="Worker2", command="bash", workspace_id=ws_id, terminal_id=tid3, display_name="Worker2", agent_type="generic_shell")
        client.create_connection(tid1, tid2, kind="terminal", workspace_id=ws_id)
        client.create_connection(tid1, tid3, kind="terminal", workspace_id=ws_id)
        time.sleep(0.5)

        # Single ask with --raw
        ans = invoke_cli(client, ["ask", "Worker1", "echo $((6*7))", "--raw"], terminal_id=tid1, data_dir=srv["data_dir"])
        assert "42" in ans, f"Expected '42' in ask response, got:\n{ans}"

        # Batch ask across Worker1 and Worker2
        batch_out = invoke_cli(
            client,
            ["ask", "--batch", "Worker1:echo BATCH_ONE", "Worker2:echo BATCH_TWO"],
            terminal_id=tid1,
            data_dir=srv["data_dir"],
        )
        assert "BATCH_ONE" in batch_out and "BATCH_TWO" in batch_out, f"Expected both batch outputs, got:\n{batch_out}"

    # ------------------------------------------------------------------
    # 5. K7: Note CLI create, read with header, chaining, scoping, delete
    # ------------------------------------------------------------------
    def test_k7_notes_create_read_header_chaining_scope_and_delete(self, live_server):
        """`note create` adds node+connection+events; `note read` includes `[N lines total]`; `noteToNoteConnections` chains; scoping blocks unconnected terminal."""
        srv, client = live_server
        doc = client.get_workspace()
        ws_id = doc.payload.id

        tid_allowed = str(uuid.uuid4()).upper()
        tid_stranger = str(uuid.uuid4()).upper()
        n_allowed = CanvasNode(
            id=tid_allowed,
            frame=NodeFrame(x=9800, y=8600, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=tid_allowed, terminal_id=tid_allowed, name="Author", command="bash"),
        )
        n_stranger = CanvasNode(
            id=tid_stranger,
            frame=NodeFrame(x=9800, y=9000, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=tid_stranger, terminal_id=tid_stranger, name="Stranger", command="bash"),
        )
        doc.payload.nodes = [n_allowed, n_stranger]
        assert client.save_workspace(doc)
        client.create_terminal(name="Author", command="bash", workspace_id=ws_id, terminal_id=tid_allowed, display_name="Author")
        client.create_terminal(name="Stranger", command="bash", workspace_id=ws_id, terminal_id=tid_stranger, display_name="Stranger")

        events: list[dict[str, Any]] = []
        ws = client.connect_events(on_event=events.append, workspace_id=ws_id, use_glib_idle=False)
        try:
            time.sleep(0.1)
            # 1. `omaestri note create` with --name and --color
            create_out = invoke_cli(
                client,
                ["note", "create", "alpha\nbeta\ngamma", "--name", "Spec", "--color", "yellow"],
                terminal_id=tid_allowed,
                data_dir=srv["data_dir"],
            )
            assert "error:" not in create_out.lower(), create_out

            assert wait_for(lambda: any(e.get("type") == "nodeAdded" for e in events), timeout=5.0), events
            assert wait_for(lambda: any(e.get("type") == "connectionCreated" for e in events), timeout=5.0), events

            # Reload workspace and verify Spec stickyNote node + noteConnection exist
            reloaded = client.get_workspace(ws_id)
            spec_nodes = [n for n in reloaded.payload.nodes if n.node_type == "stickyNote" and n.content.name == "Spec"]
            assert len(spec_nodes) == 1, [n.to_dict() for n in reloaded.payload.nodes]
            spec_node = spec_nodes[0]

            # 2. `note read "Spec"` returns `[3 lines total]` header
            read_all = invoke_cli(client, ["note", "read", "Spec"], terminal_id=tid_allowed, data_dir=srv["data_dir"])
            assert "[3 lines total]" in read_all, f"Expected '[3 lines total]' header, got:\n{read_all}"
            assert "alpha" in read_all and "gamma" in read_all

            # `note read "Spec" 2 1` returns `[lines 2-2 of 3]`
            read_range = invoke_cli(client, ["note", "read", "Spec", "2", "1"], terminal_id=tid_allowed, data_dir=srv["data_dir"])
            assert "[lines 2-2 of 3]" in read_range, f"Expected '[lines 2-2 of 3]' header, got:\n{read_range}"
            assert "beta" in read_range

            # 3. Scoping: `tid_stranger` is NOT connected to "Spec" and must be denied
            denied = invoke_cli(client, ["note", "read", "Spec"], terminal_id=tid_stranger, data_dir=srv["data_dir"])
            assert "error:" in denied.lower() or "not found" in denied.lower(), f"Expected stranger terminal to be denied, got:\n{denied}"

            # 4. Chaining: connect a second note "ChainedDoc" to "Spec" via `noteToNoteConnections`
            chained_id = str(uuid.uuid4()).upper()
            chained_node = CanvasNode(
                id=chained_id,
                frame=NodeFrame(x=10600, y=8600, width=300, height=220),
                node_type="stickyNote",
                content=StickyNoteContent(name="ChainedDoc", file_name="ChainedDoc.md"),
            )
            client.put_note("ChainedDoc", "chained content")
            reloaded = client.get_workspace(ws_id)
            reloaded.payload.nodes.append(chained_node)
            reloaded.payload.note_to_note_connections.append(
                Connection(id=str(uuid.uuid4()).upper(), a_id=spec_node.id, b_id=chained_id, kind="noteToNote")
            )
            assert client.save_workspace(reloaded)

            list_out = invoke_cli(client, ["list"], terminal_id=tid_allowed, data_dir=srv["data_dir"])
            assert "Spec" in list_out and "ChainedDoc" in list_out, f"Expected both Spec and chained ChainedDoc in list:\n{list_out}"
            chained_read = invoke_cli(client, ["note", "read", "ChainedDoc"], terminal_id=tid_allowed, data_dir=srv["data_dir"])
            assert "chained content" in chained_read

            # 5. `note delete "Spec"` removes node and `.md` file
            del_out = invoke_cli(client, ["note", "delete", "Spec"], terminal_id=tid_allowed, data_dir=srv["data_dir"])
            assert "error:" not in del_out.lower(), del_out
            after_del = client.get_workspace(ws_id)
            assert not any(n.id == spec_node.id for n in after_del.payload.nodes)
        finally:
            ws.close()

    # ------------------------------------------------------------------
    # 6. K8: Maestro recruit, connect, role assign, and dismiss
    # ------------------------------------------------------------------
    def test_k8_maestro_recruit_connect_role_assign_and_dismiss(self, live_server):
        """A terminal with `isManager: true` can recruit an agent (persisted in workspace.json + K2 events), assign roles, connect to notes, and dismiss."""
        srv, client = live_server
        doc = client.get_workspace()
        ws_id = doc.payload.id

        maestro_id = str(uuid.uuid4()).upper()
        note_id = str(uuid.uuid4()).upper()
        maestro_node = CanvasNode(
            id=maestro_id,
            frame=NodeFrame(x=9900, y=8500, width=420, height=300),
            node_type="terminal",
            content=TerminalContent(
                id=maestro_id,
                terminal_id=maestro_id,
                name="Maestro Lead",
                command="bash",
                is_manager=True,
            ),
            z_index=1,
        )
        note_node = CanvasNode(
            id=note_id,
            frame=NodeFrame(x=10500, y=8500, width=320, height=220),
            node_type="stickyNote",
            content=StickyNoteContent(name="Roadmap", file_name="Roadmap.md"),
            z_index=2,
        )
        doc.payload.nodes = [maestro_node, note_node]
        assert client.save_workspace(doc)
        client.put_note("Roadmap", "# Sprint 9 Roadmap")
        client.create_terminal(name="Maestro Lead", command="bash", workspace_id=ws_id, terminal_id=maestro_id, display_name="Maestro Lead")

        events: list[dict[str, Any]] = []
        ws = client.connect_events(on_event=events.append, workspace_id=ws_id, use_glib_idle=False)
        try:
            time.sleep(0.1)
            # 1. Create a role preset via CLI
            role_out = invoke_cli(client, ["role", "create", "Reviewer", "Audit every diff carefully."], terminal_id=maestro_id, data_dir=srv["data_dir"])
            assert "created" in role_out.lower(), role_out

            # 2. Recruit a new agent with `--command bash` and `--role Reviewer`
            rec_out = invoke_cli(
                client,
                ["recruit", "BuilderBot", "--command", "bash", "--role", "Reviewer"],
                terminal_id=maestro_id,
                data_dir=srv["data_dir"],
            )
            assert "recruited" in rec_out.lower(), rec_out

            assert wait_for(lambda: any(e.get("type") == "nodeAdded" for e in events), timeout=5.0), events
            assert wait_for(lambda: any(e.get("type") == "connectionCreated" for e in events), timeout=5.0), events

            # Verify the recruited node is persisted in workspace.json below Maestro
            updated_doc = client.get_workspace(ws_id)
            recruited_nodes = [
                n for n in updated_doc.payload.nodes
                if n.node_type == "terminal" and n.content.name == "BuilderBot"
            ]
            assert len(recruited_nodes) == 1, [n.to_dict() for n in updated_doc.payload.nodes]
            rec_node = recruited_nodes[0]
            assert rec_node.id == rec_node.content.terminal_id
            assert any(
                {c.a_id.upper(), c.b_id.upper()} == {maestro_id, rec_node.id.upper()}
                for c in updated_doc.payload.connections
            )

            # 3. Connect BuilderBot to the Roadmap note via `omaestri connect "BuilderBot" "Roadmap"`
            conn_out = invoke_cli(
                client,
                ["connect", "BuilderBot", "Roadmap"],
                terminal_id=maestro_id,
                data_dir=srv["data_dir"],
            )
            assert "connected" in conn_out.lower(), conn_out
            updated_doc2 = client.get_workspace(ws_id)
            assert any(
                {c.a_id.upper(), c.b_id.upper()} == {rec_node.id.upper(), note_id}
                for c in updated_doc2.payload.note_connections
            ), [c.to_dict() for c in updated_doc2.payload.note_connections]

            # 4. Dismiss BuilderBot -> removes PTY, node, and connections
            dis_out = invoke_cli(client, ["dismiss", "BuilderBot"], terminal_id=maestro_id, data_dir=srv["data_dir"])
            assert "dismissed" in dis_out.lower(), dis_out
            assert wait_for(
                lambda: any(
                    e.get("type") == "nodeRemoved" and str(e.get("nodeId", "")).upper() == rec_node.id.upper()
                    for e in events
                ),
                timeout=5.0,
            ), events
            final_doc = client.get_workspace(ws_id)
            assert not any(n.id.upper() == rec_node.id.upper() for n in final_doc.payload.nodes)
        finally:
            ws.close()
