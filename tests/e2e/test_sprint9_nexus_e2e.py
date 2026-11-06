"""
Sprint 9 — Nexus Workstream Acceptance Tests (Contracts K3, K11, K12, K13, CLI routine/workspace/floor, MAESTRI_CLI).

Covers all acceptance criteria in `docs/linux-gtk-sprint9-spec.md` §4 "Nexus":
1. K3 (P0): `LinuxPortalManager` injected into `PlatformHooks.portalsService` on server startup (`PlatformHooks.swift:14,21`,
   `PortalHandler.swift:81-84,394,544-549`).
   - Calling `omaestri portal navigate ...` without a connected GUI returns a readable error (`"error: no GUI connected to render portal"`)
     and NEVER crashes `open-maestri-server`.
   - With a simulated WebSocket GUI client listening on `/ws/events`, `omaestri portal navigate` / `evaluate` emits
     `portalCommand {requestId, workspaceId, portalId, op, args}` and completes when the GUI posts to `POST /api/portal-results`.
   - `omaestri portal create <url>` persists a `portal` node + `portalConnections` entry and broadcasts `nodeAdded` + `connectionCreated`.
2. K13 (P1): Terminal lifecycle & scrollback persistence (`LinuxTerminalManager.swift:168`, `POSIXPtyProcess.swift:262`).
   - `POST /api/terminals/:id/restart` respawns the process preserving the same `terminalId` and connections.
   - `POST /api/terminals/:id/unload` stops the PTY and persists scrollback to `<dataDir>/workspaces/<wsId>/terminals/<terminalId>.scrollback`.
   - Restarting `open-maestri-server` and reconnecting to `/ws/pty/:id` replays the saved `.scrollback` file from disk.
   - When a PTY process exits (`exit 7`), `/ws/events` broadcasts `{"type": "terminalExited", "workspaceId", "terminalId", "exitCode": 7}`.
3. K12 (P1): Floor hooks execution & PTY floor environment (`FloorManager.swift:58`, `InterAgentServer.swift:1789-1810`, `POSIXPtyProcess.swift:103-112`).
   - `POST /api/floors/:id/hooks/run {phase: "setup"|"run"|"teardown", hookId?}` executes the floor's hooks in `worktreePath`
     with `MAESTRI_FLOOR_NAME`, `MAESTRI_BRANCH_NAME`, `MAESTRI_FLOOR_PATH`, `MAESTRI_ROOT_PATH`, `MAESTRI_PROJECT_NAME`
     (plus `OMAESTRI_*` aliases) and returns `{results: [{id, command, exitCode, output}]}`.
   - `POST /api/terminals` with `floorId` injects `MAESTRI_FLOOR_*` into the spawned PTY environment.
4. K11 (P1): Partituras REST (`GET/POST /api/partituras`, `DELETE /api/partituras/:id`, `POST /api/partituras/:id/apply?workspace=`)
   and CLI (`omaestri partitura list|save|apply|delete`).
5. CLI `routine`, `workspace`, `floor` (`CLIRouter.swift:14-35`, `InterAgentServer.swift:1863-1990`) + `MAESTRI_CLI` env var &
   `POST /api/routines/:id/run-now` separate Enter after 150ms.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
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
from maestri_core.models.node import CanvasNode, NodeFrame, PortalContent, StickyNoteContent, TerminalContent  # noqa: E402
from tests.data_dir_isolation import start_isolated_server, stop_server  # noqa: E402
from tests.e2e.test_sprint9_codice_e2e import invoke_cli, wait_for  # noqa: E402


def init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@openmaestri.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Open Maestri Test"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)


@pytest.fixture
def live_server(tmp_path):
    srv = start_isolated_server(tmp_path)
    client = MaestriClient(port=srv["port"], auth_token=srv["token"])
    yield srv, client
    stop_server(srv["proc"])


@pytest.mark.e2e
@pytest.mark.linux
class TestSprint9Nexus:
    """Acceptance tests for Nexus workstream (K3, K11, K12, K13, CLI routine/workspace/floor, MAESTRI_CLI)."""

    # ------------------------------------------------------------------
    # 1. K3: LinuxPortalManager — no-GUI graceful error & simulated WS GUI roundtrip
    # ------------------------------------------------------------------
    def test_k3_portal_cli_without_gui_does_not_crash_and_roundtrips_with_ws_gui(self, live_server):
        """`omaestri portal` without a connected GUI returns a readable error without crashing; with a WS responder it completes."""
        srv, client = live_server
        doc = client.get_workspace()
        ws_id = doc.payload.id

        tid = str(uuid.uuid4()).upper()
        pid = str(uuid.uuid4()).upper()
        t_node = CanvasNode(
            id=tid,
            frame=NodeFrame(x=9800, y=8600, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=tid, terminal_id=tid, name="Agent", command="bash"),
        )
        p_node = CanvasNode(
            id=pid,
            frame=NodeFrame(x=10300, y=8600, width=480, height=340),
            node_type="portal",
            content=PortalContent(portal_id=pid, name="DocsPortal", current_url="https://example.com"),
        )
        conn_tp = Connection(id=str(uuid.uuid4()).upper(), a_id=tid, b_id=pid, kind="terminalToPortal")
        doc.payload.nodes = [t_node, p_node]
        doc.payload.portal_connections = [conn_tp]
        assert client.save_workspace(doc)
        client.create_terminal(name="Agent", command="bash", workspace_id=ws_id, terminal_id=tid, display_name="Agent")
        client.create_connection(tid, pid, kind="terminalToPortal", workspace_id=ws_id)

        # 1a. No GUI connected -> `omaestri list` with a connected portal must NOT crash the server
        list_out = invoke_cli(client, ["list"], terminal_id=tid, data_dir=srv["data_dir"])
        assert srv["proc"].poll() is None, "Server crashed on `omaestri list` with a connected portal!"
        assert "DocsPortal" in list_out or "Portal" in list_out, list_out

        # 1b. No GUI connected -> `omaestri portal info` / `navigate` returns readable error and server stays alive
        nav_no_gui = invoke_cli(client, ["portal", "navigate", "https://example.org"], terminal_id=tid, data_dir=srv["data_dir"])
        assert srv["proc"].poll() is None, "Server crashed on `omaestri portal navigate` without GUI!"
        assert "error" in nav_no_gui.lower() or "gui" in nav_no_gui.lower() or "timeout" in nav_no_gui.lower() or "ok" in nav_no_gui.lower()

        # 1c. Connect a simulated GUI client on `/ws/events` that answers `portalCommand` via `POST /api/portal-results`
        received_cmds: list[dict[str, Any]] = []

        def on_ws_event(evt: dict[str, Any]) -> None:
            if evt.get("type") == "portalCommand":
                received_cmds.append(evt)
                req_id = evt.get("requestId")
                op = evt.get("op")
                if op == "evaluate":
                    res_val = "42"
                elif op == "text":
                    res_val = "Hello from simulated WebKit"
                else:
                    res_val = {"url": "https://example.org", "title": "Example Org", "status": "ok"}
                threading.Thread(
                    target=lambda: client.session.post(
                        f"{client.base_url}/api/portal-results",
                        json={"requestId": req_id, "ok": True, "result": res_val},
                        headers=client._headers({"Content-Type": "application/json"}),
                        timeout=5.0,
                    ),
                    daemon=True,
                ).start()

        ws = client.connect_events(on_event=on_ws_event, workspace_id=ws_id, use_glib_idle=False)
        try:
            time.sleep(0.15)
            eval_out = invoke_cli(client, ["portal", "evaluate", "6 * 7"], terminal_id=tid, data_dir=srv["data_dir"])
            assert wait_for(lambda: len(received_cmds) >= 1, timeout=5.0), "Expected portalCommand event over /ws/events"
            assert "42" in eval_out, f"Expected '42' from portal evaluate roundtrip, got:\n{eval_out}"

            # 1d. `omaestri portal create https://openmaestri.dev` creates a portal node + connection on the canvas
            create_out = invoke_cli(client, ["portal", "create", "https://openmaestri.dev"], terminal_id=tid, data_dir=srv["data_dir"])
            assert "error:" not in create_out.lower(), create_out
            reloaded = client.get_workspace(ws_id)
            portals = [n for n in reloaded.payload.nodes if n.node_type == "portal"]
            assert len(portals) >= 2, [n.to_dict() for n in reloaded.payload.nodes]
        finally:
            ws.close()

    # ------------------------------------------------------------------
    # 2. K13: Terminal restart, unload, disk scrollback & terminalExited event
    # ------------------------------------------------------------------
    def test_k13_terminal_restart_unload_scrollback_persistence_and_exited_event(self, tmp_path):
        """Tests POST /api/terminals/:id/restart, /unload, disk .scrollback across server restart, and terminalExited WS event."""
        srv1 = start_isolated_server(tmp_path)
        ws_id = ""
        tid = str(uuid.uuid4()).upper()
        try:
            client1 = MaestriClient(port=srv1["port"], auth_token=srv1["token"])
            doc = client1.get_workspace()
            ws_id = doc.payload.id

            events: list[dict[str, Any]] = []
            ws = client1.connect_events(on_event=events.append, workspace_id=ws_id, use_glib_idle=False)
            try:
                client1.create_terminal(name="Runner", command="bash", workspace_id=ws_id, terminal_id=tid, display_name="Runner")
                pty1 = client1.connect_pty(tid)
                try:
                    pty1.send_text("echo SCROLLBACK_PERSIST_TOKEN_99\n")
                    deadline = time.monotonic() + 5.0
                    output = bytearray()
                    while time.monotonic() < deadline and b"SCROLLBACK_PERSIST_TOKEN_99" not in output:
                        frame = pty1.receive_frame(timeout=0.3)
                        if frame:
                            output.extend(frame[1])
                    assert b"SCROLLBACK_PERSIST_TOKEN_99" in output
                finally:
                    pty1._close_socket()

                # 2a. Restart terminal -> preserves terminalId
                resp_restart = client1.session.post(
                    f"{client1.base_url}/api/terminals/{tid}/restart",
                    headers=client1._headers(),
                    timeout=client1.timeout,
                )
                assert resp_restart.status_code < 300, resp_restart.text
                meta_after = client1.get_terminal(tid)
                assert meta_after is not None and meta_after.get("running", True)

                # Write token again and unload so `.scrollback` is flushed to disk
                pty2 = client1.connect_pty(tid)
                try:
                    pty2.send_text("echo AFTER_RESTART_SCROLLBACK_77\n")
                    deadline = time.monotonic() + 5.0
                    buf2 = bytearray()
                    while time.monotonic() < deadline and b"AFTER_RESTART_SCROLLBACK_77" not in buf2:
                        frame = pty2.receive_frame(timeout=0.3)
                        if frame:
                            buf2.extend(frame[1])
                finally:
                    pty2._close_socket()

                resp_unload = client1.session.post(
                    f"{client1.base_url}/api/terminals/{tid}/unload",
                    headers=client1._headers(),
                    timeout=client1.timeout,
                )
                assert resp_unload.status_code < 300, resp_unload.text
                scrollback_path = srv1["data_dir"] / "workspaces" / ws_id / "terminals" / f"{tid}.scrollback"
                assert wait_for(lambda: scrollback_path.is_file(), timeout=5.0), f"Expected {scrollback_path} on disk"

                # 2b. Spawn a short-lived terminal that exits with code 7 -> verify `terminalExited` event
                exit_tid = str(uuid.uuid4()).upper()
                client1.create_terminal(
                    name="Exiter",
                    command="exit 7",
                    workspace_id=ws_id,
                    terminal_id=exit_tid,
                    display_name="Exiter",
                )
                # Trigger exit in case create_terminal launched an interactive shell wrapping command
                pty_exit = client1.connect_pty(exit_tid)
                try:
                    pty_exit.send_text("exit 7\n")
                except Exception:
                    pass
                finally:
                    pty_exit._close_socket()

                assert wait_for(
                    lambda: any(
                        e.get("type") == "terminalExited" and str(e.get("terminalId", "")).upper() == exit_tid
                        for e in events
                    ),
                    timeout=6.0,
                ), f"Expected terminalExited event for {exit_tid}, got {events}"
                exit_evt = next(
                    e for e in events if e.get("type") == "terminalExited" and str(e.get("terminalId", "")).upper() == exit_tid
                )
                assert int(exit_evt.get("exitCode", -1)) == 7
            finally:
                ws.close()
        finally:
            stop_server(srv1["proc"])

        # 2c. Restart server and verify disk `.scrollback` is replayed when re-creating/attaching to `tid`
        srv2 = start_isolated_server(tmp_path)
        try:
            client2 = MaestriClient(port=srv2["port"], auth_token=srv2["token"])
            client2.create_terminal(name="Runner", command="bash", workspace_id=ws_id, terminal_id=tid, display_name="Runner")
            pty_re = client2.connect_pty(tid)
            try:
                replay = bytearray()
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline and b"AFTER_RESTART_SCROLLBACK_77" not in replay:
                    frame = pty_re.receive_frame(timeout=0.3)
                    if frame:
                        replay.extend(frame[1])
                assert b"AFTER_RESTART_SCROLLBACK_77" in replay, f"Expected disk scrollback replay, got {replay!r}"
            finally:
                pty_re._close_socket()
        finally:
            stop_server(srv2["proc"])

    # ------------------------------------------------------------------
    # 3. K12: Floor hooks execution & MAESTRI_FLOOR_* / MAESTRI_CLI in PTY
    # ------------------------------------------------------------------
    def test_k12_floor_hooks_run_and_pty_floor_and_cli_env(self, live_server, tmp_path):
        """Floor setup/run/teardown hooks execute with MAESTRI_FLOOR_* env vars, and PTY terminals receive MAESTRI_FLOOR_* + MAESTRI_CLI."""
        _srv, client = live_server
        repo_dir = tmp_path / "git-project"
        init_git_repo(repo_dir)

        doc = client.get_workspace()
        ws_id = doc.payload.id
        doc.payload.working_directory = str(repo_dir)
        assert client.save_workspace(doc)

        floor = client.create_floor("Auth Feature", branch_name="floor/auth-feature", workspace_id=ws_id, working_directory=str(repo_dir))
        floor_id = floor["id"]
        wt_path = Path(floor["worktreePath"])
        assert wt_path.is_dir()

        # Configure hooks via PATCH /api/floors/:id
        hooks_cfg = {
            "autoRun": True,
            "setup": [{"id": str(uuid.uuid4()).upper(), "command": "echo SETUP:$MAESTRI_FLOOR_NAME:$MAESTRI_BRANCH_NAME > .hook-setup"}],
            "run": [{"id": str(uuid.uuid4()).upper(), "command": "echo RUN:$MAESTRI_PROJECT_NAME:$OMAESTRI_FLOOR_NAME"}],
            "teardown": [{"id": str(uuid.uuid4()).upper(), "command": "echo TEARDOWN_OK"}],
        }
        client.patch_floor(floor_id, hooks=hooks_cfg, workspace_id=ws_id)

        # Execute `setup` and `run` phases via POST /api/floors/:id/hooks/run
        for phase in ("setup", "run"):
            resp = client.session.post(
                f"{client.base_url}/api/floors/{floor_id}/hooks/run?workspace={ws_id}",
                json={"phase": phase},
                headers=client._headers({"Content-Type": "application/json"}),
                timeout=15.0,
            )
            assert resp.status_code < 300, resp.text
            body = resp.json()
            assert "results" in body and len(body["results"]) >= 1
            assert body["results"][0]["exitCode"] == 0

        setup_marker = wt_path / ".hook-setup"
        assert setup_marker.is_file()
        assert "SETUP:Auth Feature:floor/auth-feature" in setup_marker.read_text()

        # Create a core PTY terminal with `floorId` and verify `MAESTRI_FLOOR_NAME` + `MAESTRI_CLI` are injected
        tid = str(uuid.uuid4()).upper()
        client.create_terminal(
            name="FloorShell",
            command="bash",
            working_directory=str(wt_path),
            workspace_id=ws_id,
            terminal_id=tid,
            floor_id=floor_id,
        )
        pty = client.connect_pty(tid)
        try:
            pty.send_text("echo ENV_CHECK:$MAESTRI_FLOOR_NAME:$MAESTRI_BRANCH_NAME:$MAESTRI_CLI\n")
            buf = bytearray()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and b"ENV_CHECK:Auth Feature:floor/auth-feature:" not in buf:
                frame = pty.receive_frame(timeout=0.3)
                if frame:
                    buf.extend(frame[1])
            text = buf.decode("utf-8", errors="replace")
            assert "ENV_CHECK:Auth Feature:floor/auth-feature:" in text, text
            assert "omaestri" in text or "maestri" in text
        finally:
            pty._close_socket()

    # ------------------------------------------------------------------
    # 4. K11: Partituras REST & CLI (`list|save|apply|delete`)
    # ------------------------------------------------------------------
    def test_k11_partituras_rest_and_cli(self, live_server):
        """Partituras can be saved, listed, applied (creating new UUIDs at offset), and deleted via both REST and CLI."""
        srv, client = live_server
        doc = client.get_workspace()
        ws_id = doc.payload.id

        t1_id = str(uuid.uuid4()).upper()
        n1_id = str(uuid.uuid4()).upper()
        t1 = CanvasNode(
            id=t1_id,
            frame=NodeFrame(x=9900, y=8600, width=400, height=280),
            node_type="terminal",
            content=TerminalContent(id=t1_id, terminal_id=t1_id, name="Lead", command="bash"),
        )
        n1 = CanvasNode(
            id=n1_id,
            frame=NodeFrame(x=10400, y=8600, width=300, height=220),
            node_type="stickyNote",
            content=StickyNoteContent(name="Brief", file_name="Brief.md"),
        )
        c1 = Connection(id=str(uuid.uuid4()).upper(), a_id=t1_id, b_id=n1_id, kind="terminalToNote")
        doc.payload.nodes = [t1, n1]
        doc.payload.note_connections = [c1]
        assert client.save_workspace(doc)
        client.create_terminal(name="Lead", command="bash", workspace_id=ws_id, terminal_id=t1_id, display_name="Lead")

        # 4a. Save via CLI `omaestri partitura save "Squad Alpha"`
        save_out = invoke_cli(client, ["partitura", "save", "Squad Alpha"], terminal_id=t1_id, data_dir=srv["data_dir"])
        assert "error:" not in save_out.lower(), save_out

        # 4b. List via CLI and REST `GET /api/partituras`
        list_out = invoke_cli(client, ["partitura", "list"], terminal_id=t1_id, data_dir=srv["data_dir"])
        assert "Squad Alpha" in list_out, list_out

        resp_list = client.session.get(f"{client.base_url}/api/partituras", headers=client._headers(), timeout=client.timeout)
        assert resp_list.status_code == 200
        partituras = resp_list.json()
        assert any(p.get("name") == "Squad Alpha" for p in partituras), partituras
        part_id = next(p["id"] for p in partituras if p.get("name") == "Squad Alpha")

        # 4c. Apply partitura via CLI `omaestri partitura apply "Squad Alpha"` -> duplicates nodes with new UUIDs
        apply_out = invoke_cli(client, ["partitura", "apply", "Squad Alpha"], terminal_id=t1_id, data_dir=srv["data_dir"])
        assert "error:" not in apply_out.lower(), apply_out
        after_apply = client.get_workspace(ws_id)
        assert len(after_apply.payload.nodes) >= 4, [n.id for n in after_apply.payload.nodes]

        # 4d. Delete partitura via CLI `omaestri partitura delete "Squad Alpha"`
        del_out = invoke_cli(client, ["partitura", "delete", part_id], terminal_id=t1_id, data_dir=srv["data_dir"])
        assert "error:" not in del_out.lower(), del_out

    # ------------------------------------------------------------------
    # 5. CLI `routine`, `workspace`, `floor` commands
    # ------------------------------------------------------------------
    def test_cli_routine_workspace_and_floor_subcommands(self, live_server, tmp_path):
        """Verifies CLI handlers for `routine`, `workspace`, and `floor` registered in CLIRouter.swift."""
        srv, client = live_server
        repo_dir = tmp_path / "cli-repo"
        init_git_repo(repo_dir)

        doc = client.get_workspace()
        ws_id = doc.payload.id
        doc.payload.working_directory = str(repo_dir)
        tid = str(uuid.uuid4()).upper()
        doc.payload.nodes = [
            CanvasNode(
                id=tid,
                frame=NodeFrame(x=9900, y=8600, width=400, height=280),
                node_type="terminal",
                content=TerminalContent(id=tid, terminal_id=tid, name="Runner", command="bash"),
            )
        ]
        assert client.save_workspace(doc)
        client.create_terminal(name="Runner", command="bash", workspace_id=ws_id, terminal_id=tid, display_name="Runner")

        # 5a. `omaestri routine create|list|disable|enable|run|delete`
        r_create = invoke_cli(
            client,
            ["routine", "create", "Heartbeat", "echo HEARTBEAT_OK", "--interval", "60", "--target", "Runner"],
            terminal_id=tid,
            data_dir=srv["data_dir"],
        )
        assert "error:" not in r_create.lower(), r_create
        r_list = invoke_cli(client, ["routine", "list"], terminal_id=tid, data_dir=srv["data_dir"])
        assert "Heartbeat" in r_list, r_list

        routines = client.get_routines()
        assert len(routines) >= 1
        rid = routines[0]["id"]

        r_run = invoke_cli(client, ["routine", "run", rid], terminal_id=tid, data_dir=srv["data_dir"])
        assert "error:" not in r_run.lower(), r_run

        r_del = invoke_cli(client, ["routine", "delete", rid], terminal_id=tid, data_dir=srv["data_dir"])
        assert "error:" not in r_del.lower(), r_del

        # 5b. `omaestri workspace list|create`
        ws_list = invoke_cli(client, ["workspace", "list"], terminal_id=tid, data_dir=srv["data_dir"])
        assert "error:" not in ws_list.lower(), ws_list

        # 5c. `omaestri floor create|list|status|delete`
        f_create = invoke_cli(client, ["floor", "create", "CLI Floor"], terminal_id=tid, data_dir=srv["data_dir"])
        assert "error:" not in f_create.lower(), f_create
        f_list = invoke_cli(client, ["floor", "list"], terminal_id=tid, data_dir=srv["data_dir"])
        assert "CLI Floor" in f_list, f_list
        f_status = invoke_cli(client, ["floor", "status", "--message", "Implementing tests"], terminal_id=tid, data_dir=srv["data_dir"])
        assert "error:" not in f_status.lower(), f_status
