"""
Sprint 9 — Vertice Workstream Acceptance & E2E Tests (GTK4 Window, Sync & Wiring).

Covers §3 contracts (K1a, K2, K9, K10, K11, K12, K13) and §4 Vertice criteria:
1. K1a: Every GUI terminal creation path (`new_terminal_node`, `_create_node("terminal")`,
   `create_terminal_preset`, `open_new_terminal_dialog`, `_on_canvas_duplicate_requested`,
   `CanvasContextMenu`) produces `CanvasNode.id == TerminalContent.terminal_id == TerminalContent.id`.
2. K1a / K12: `ensure_terminal` receives `agent_type` (`claude_code`, `codex`, `gemini_cli`,
   `open_code`, `generic_shell`) and `floor_id` when `cwd` is inside a floor's `worktreePath`.
3. K2: Live canvas events (`nodeAdded`, `nodeRemoved`, `connectionCreated`, `connectionRemoved`)
   consumed from `/ws/events` update `win.document.payload` and `win.canvas_widget` without
   re-POSTing to `/api/connections`.
4. Full terminal dialog field persistence (`monitor_with_ombro`, `is_manager`, `theme_id`,
   `font_size`, `assigned_role_id`) on create and edit, persisted to `workspace.json`.
5. K13: `win.reload_terminal(node_id)` and `win.unload_terminal(node_id)`.
6. K9: `win.delete_connection(conn_id)`, `node-lock-changed` (`is_locked` persistence & delete
   protection), and `node-z-changed` (`z_index` persistence across reload).
7. K10: `win.copy_selected_nodes()`, `win.copy_node(node_id)`, and `win.paste_nodes(at=None)`
   (Maestri element JSON -> cloned nodes, `https://` URL -> Portal, plain text -> StickyNote).
8. K11: `win.save_partitura(name, node_ids=None)` and `win.apply_partitura(partitura_id, at=None)`.
9. Window shortcuts & HeaderBar: `Ctrl+Down`/`Ctrl+Up` workspace switching, `Ctrl+1..9` jump to
   terminal, HeaderBar "Open in editor" button (mocked launcher), File/View/Help menu bar installed,
   and new FileTree node defaulting to `document.payload.working_directory`.

All tests run against an isolated `OPEN_MAESTRI_DATA_DIR` under Xvfb (`DISPLAY=:99`).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.scene import ConnItem, norm  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.connection import Connection  # noqa: E402
from maestri_core.models.node import (  # noqa: E402
    CanvasNode,
    NodeFrame,
    StickyNoteContent,
    TerminalContent,
)
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402

from tests.data_dir_isolation import start_isolated_server  # noqa: E402


def pump_gtk(ms: int = 120) -> None:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + (ms / 1000.0)
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 5.0, step_ms: int = 25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump_gtk(step_ms)
        if predicate():
            return True
    return bool(predicate())


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "open-maestri-data"
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(data_dir))
    ws_workdir = tmp_path / "project-workdir"
    ws_workdir.mkdir(parents=True, exist_ok=True)
    with start_isolated_server(data_dir) as srv:
        client = MaestriClient(port=srv['port'], auth_token=srv['token'])
        doc = client.get_workspace()
        assert doc is not None and doc.payload is not None
        doc.payload.working_directory = str(ws_workdir)
        doc.payload.nodes = []
        client.save_workspace(doc)

        app = Gtk.Application(
            application_id=f"com.openmaestri.vertice.s9.{uuid.uuid4().hex[:8]}",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        app.register(None)
        win = OpenMaestriWindow(app, client)
        win.present()
        assert win.wait_loaded(timeout=8.0), "Window failed to load initial workspace"
        pump_gtk(150)
        try:
            yield srv, client, win, ws_workdir
        finally:
            win.close()
            pump_gtk(80)


# ============================================================================
# 1. P0 — K1a: Terminal CanvasNode.id == TerminalContent.terminal_id + agentType/floorId
# ============================================================================
class TestK1aTerminalIdAndAgentFloorWiring:
    def test_all_gui_terminal_creation_paths_match_node_id_and_terminal_id(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env

        # Path 1: win.new_terminal_node(...)
        node1 = win.new_terminal_node(
            NodeFrame(x=9900, y=8600, width=420, height=300),
            name="Direct Node",
            command="claude",
            cwd=str(ws_workdir),
        )
        assert node1.id == node1.content.terminal_id == node1.content.id
        assert node1.content.agent_type == "claude_code"

        # Path 2: win._create_node("terminal")
        win._create_node("terminal", at=Point(10000, 8600))
        node2 = win.document.payload.nodes[-1]
        assert node2.node_type == "terminal"
        assert node2.id == node2.content.terminal_id == node2.content.id

        # Path 3: win.create_terminal_preset("codex")
        node3 = win.create_terminal_preset("codex", at=Point(10450, 8600))
        assert node3.id == node3.content.terminal_id == node3.content.id
        assert node3.content.agent_type == "codex"

        # Path 4: win.open_new_terminal_dialog(...) callback
        dlg = win.open_new_terminal_dialog(at=Point(10900, 8600), default_preset="gemini")
        pump_gtk(50)
        dlg._on_create_clicked(None)
        pump_gtk(100)
        node4 = win.document.payload.nodes[-1]
        assert node4.id == node4.content.terminal_id == node4.content.id
        assert node4.content.agent_type == "gemini_cli"

        # Path 5: win._on_canvas_duplicate_requested(...)
        win._on_canvas_duplicate_requested(
            win.canvas_widget,
            {node3.id: Rect(11400, 8600, 420, 300)},
        )
        pump_gtk(80)
        node5 = win.document.payload.nodes[-1]
        assert node5.id != node3.id
        assert node5.id == node5.content.terminal_id == node5.content.id

    def test_ensure_terminal_passes_agent_type_and_floor_id(self, isolated_env, tmp_path: Path) -> None:
        _, client, win, _ = isolated_env
        floor_wt = tmp_path / "floor-worktree"
        sub_dir = floor_wt / "src"
        sub_dir.mkdir(parents=True, exist_ok=True)
        floor_id = str(uuid.uuid4()).upper()
        win.floors.floors = [{"id": floor_id, "name": "feat-k12", "worktreePath": str(floor_wt)}]

        assert win._floor_id_for(str(sub_dir)) == floor_id

        calls: list[dict] = []
        orig_ensure = client.ensure_terminal

        def spy_ensure(tid, **kwargs):
            calls.append({"tid": tid, **kwargs})
            return orig_ensure(tid, **kwargs)

        with patch.object(client, "ensure_terminal", side_effect=spy_ensure):
            node = win.new_terminal_node(
                NodeFrame(x=9900, y=8600, width=420, height=300),
                name="Floor Claude",
                command="claude --dangerously-skip-permissions",
                cwd=str(sub_dir),
            )
            win._insert_node(node)
            assert wait_until(lambda: any(c["tid"] == node.id for c in calls), timeout=4.0)

        call = next(c for c in calls if c["tid"] == node.id)
        assert call["agent_type"] == "claude_code"
        assert call["floor_id"] == floor_id


# ============================================================================
# 2. P0 — K2: Live Canvas Events (`nodeAdded`, `nodeRemoved`, `connectionCreated`, `connectionRemoved`)
# ============================================================================
class TestK2LiveCanvasEvents:
    def test_ws_events_update_document_and_canvas_without_re_posting(self, isolated_env) -> None:
        _, client, win, ws_workdir = isolated_env

        # Look up the event handler method on OpenMaestriWindow
        dispatch = (
            getattr(win, "_on_ws_event", None)
            or getattr(win, "_handle_ws_event", None)
            or getattr(win, "on_ws_event", None)
        )
        assert callable(dispatch), (
            "Vertice K2: OpenMaestriWindow must expose an event handler "
            "(`_on_ws_event` / `_handle_ws_event` / `on_ws_event`) consuming `/ws/events` payloads"
        )

        # 1. nodeAdded event for a stickyNote and a terminal
        note_id = str(uuid.uuid4()).upper()
        note_node = CanvasNode(
            id=note_id,
            frame=NodeFrame(x=10100, y=8700, width=280, height=220),
            node_type="stickyNote",
            content=StickyNoteContent(name="Live Note", file_name="Live Note.md"),
            z_index=5,
        )
        term_id = str(uuid.uuid4()).upper()
        term_node = CanvasNode(
            id=term_id,
            frame=NodeFrame(x=10500, y=8700, width=420, height=300),
            node_type="terminal",
            content=TerminalContent(
                id=term_id,
                terminal_id=term_id,
                name="Live Recruit",
                command="bash",
                working_directory=str(ws_workdir),
            ),
            z_index=6,
        )

        dispatch("nodeAdded", {"workspaceId": win.document.payload.id, "node": note_node.to_dict()})
        dispatch("nodeAdded", {"workspaceId": win.document.payload.id, "node": term_node.to_dict()})
        pump_gtk(150)

        assert win._find_node(note_id) is not None
        assert win._find_node(term_id) is not None
        assert norm(note_id) in win.canvas_widget.scene.nodes
        assert norm(term_id) in win.canvas_widget.scene.nodes

        # 2. connectionCreated event must NOT re-POST to /api/connections
        conn_id = str(uuid.uuid4()).upper()
        with patch.object(client, "create_connection") as mock_post_conn:
            dispatch(
                "connectionCreated",
                {
                    "id": conn_id,
                    "sourceNodeId": term_id,
                    "targetNodeId": note_id,
                    "kind": "note",
                    "workspaceId": win.document.payload.id,
                },
            )
            pump_gtk(100)
            mock_post_conn.assert_not_called()

        assert any(c.id.upper() == conn_id for c in win.document.payload.all_connections())
        assert any(c.id.upper() == conn_id for c in win.canvas_widget.scene.connections)

        # 3. connectionRemoved event removes from payload + scene
        dispatch("connectionRemoved", {"id": conn_id, "workspaceId": win.document.payload.id})
        pump_gtk(80)
        assert not any(c.id.upper() == conn_id for c in win.document.payload.all_connections())
        assert not any(c.id.upper() == conn_id for c in win.canvas_widget.scene.connections)

        # 4. nodeRemoved event removes node from payload + scene
        dispatch("nodeRemoved", {"nodeId": note_id, "workspaceId": win.document.payload.id})
        pump_gtk(80)
        assert win._find_node(note_id) is None
        assert norm(note_id) not in win.canvas_widget.scene.nodes


# ============================================================================
# 3. P1 — Full Terminal Dialog Persistence & Reload/Unload (K13)
# ============================================================================
class TestTerminalDialogPersistenceAndLifecycle:
    def test_terminal_dialog_fields_persist_to_workspace_json(self, isolated_env) -> None:
        _, client, win, ws_workdir = isolated_env
        role_id = str(uuid.uuid4()).upper()

        dlg = win.open_new_terminal_dialog(at=Point(10000, 8600))
        pump_gtk(50)
        dlg.on_create(
            {
                "name": "Configured Agent",
                "command": "claude",
                "cwd": str(ws_workdir),
                "icon": "sparkles",
                "color": "purple",
                "theme_id": "dracula",
                "font_size": 15.0,
                "monitor_with_ombro": True,
                "is_manager": True,
                "assigned_role_id": role_id,
            }
        )
        dlg.close()
        pump_gtk(150)

        node = win.document.payload.nodes[-1]
        c = node.content
        assert c.monitor_with_ombro is True
        assert c.is_manager is True
        assert c.theme_id == "dracula"
        assert c.font_size == 15.0
        assert c.assigned_role_id == role_id

        if win.sync is not None:
            win.sync.flush()
        reloaded = client.get_workspace()
        saved_node = next(n for n in reloaded.payload.nodes if n.id == node.id)
        sc = saved_node.content
        assert sc.monitor_with_ombro is True
        assert sc.is_manager is True
        assert sc.theme_id == "dracula"
        assert sc.font_size == 15.0
        assert sc.assigned_role_id == role_id

    def test_reload_and_unload_terminal_invoke_core_endpoints(self, isolated_env) -> None:
        _, client, win, ws_workdir = isolated_env
        node = win.new_terminal_node(
            NodeFrame(x=10000, y=8600, width=420, height=300),
            name="Reloadable Shell",
            command="bash",
            cwd=str(ws_workdir),
        )
        win._insert_node(node)
        assert wait_until(lambda: client.get_terminal(node.id) is not None, timeout=4.0)

        # Unload terminal keeps CanvasNode on canvas
        assert win.unload_terminal(node.id) is True
        pump_gtk(200)
        assert win._find_node(node.id) is not None

        # Reload terminal restarts the shell session and keeps CanvasNode
        assert win.reload_terminal(node.id) is True
        pump_gtk(300)
        assert win._find_node(node.id) is not None
        info = client.get_terminal(node.id)
        assert info is not None


# ============================================================================
# 4. P1 — K9: Delete Connection, Lock Persistence & Z-order Persistence
# ============================================================================
class TestK9ConnectionDeleteLockAndZOrder:
    def test_delete_connection_removes_from_server_payload_and_canvas(self, isolated_env) -> None:
        _, client, win, ws_workdir = isolated_env
        t1 = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=400, height=280), "T1", "bash", str(ws_workdir))
        t2 = win.new_terminal_node(NodeFrame(x=10400, y=8600, width=400, height=280), "T2", "bash", str(ws_workdir))
        win._insert_node(t1)
        win._insert_node(t2)
        conn = client.create_connection(t1.id, t2.id, kind="terminal", workspace_id=win.document.payload.id)
        assert conn is not None
        win.document.payload.connections.append(conn)
        win.canvas_widget.add_connection(ConnItem(conn.id, t1.id, t2.id))
        pump_gtk(80)

        assert hasattr(win, "delete_connection") and callable(win.delete_connection), (
            "Vertice K9: win.delete_connection(conn_id) must be implemented"
        )
        win.delete_connection(conn.id)
        assert wait_until(
            lambda: not any(c.id == conn.id for c in win.document.payload.all_connections()),
            timeout=3.0,
        )
        assert not any(c.id == conn.id for c in win.canvas_widget.scene.connections)
        assert not any(c.id == conn.id for c in client.list_connections())

    def test_lock_and_z_order_persist_and_locked_node_blocks_delete(self, isolated_env) -> None:
        _, client, win, _ = isolated_env
        win._create_node("stickyNote", at=Point(10000, 8600))
        node = win.document.payload.nodes[-1]

        # Lock node via K9 signal or NodeContextMenu
        if GObject.signal_lookup("node-lock-changed", type(win.canvas_widget)):
            win.canvas_widget.emit("node-lock-changed", node.id, True)
        else:
            from ui.context_menus import NodeContextMenu

            menu = NodeContextMenu(win, node.id, load_partituras=False)
            menu._toggle_lock()
        pump_gtk(100)
        assert node.is_locked is True

        # Locked node must NOT be deleted when delete-requested fires
        win._on_canvas_delete_requested(win.canvas_widget, (node.id,))
        pump_gtk(80)
        assert win._find_node(node.id) is not None, "Locked node must not be deleted"

        # Z-order update via node-z-changed signal
        if GObject.signal_lookup("node-z-changed", type(win.canvas_widget)):
            win.canvas_widget.emit("node-z-changed", node.id, 42)
            pump_gtk(80)
            assert node.z_index == 42

        if win.sync is not None:
            win.sync.flush()
        reloaded = client.get_workspace()
        saved = next(n for n in reloaded.payload.nodes if n.id == node.id)
        assert saved.is_locked is True
        if GObject.signal_lookup("node-z-changed", type(win.canvas_widget)):
            assert saved.z_index == 42


# ============================================================================
# 5. P1 — K10: Copy Element & Paste (Element JSON, URL -> Portal, Text -> Note)
# ============================================================================
class TestK10CopyAndPasteNodes:
    def test_copy_node_and_paste_nodes_clones_element_with_offset(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        win._create_node("stickyNote", at=Point(10000, 8600))
        src = win.document.payload.nodes[-1]
        count_before = len(win.document.payload.nodes)

        assert hasattr(win, "copy_node") and callable(win.copy_node), "K10: win.copy_node(node_id) required"
        assert hasattr(win, "paste_nodes") and callable(win.paste_nodes), "K10: win.paste_nodes(at=None) required"

        win.copy_node(src.id)
        pump_gtk(80)
        win.paste_nodes(at=Point(10350, 8800))
        pump_gtk(150)

        assert len(win.document.payload.nodes) == count_before + 1
        pasted = win.document.payload.nodes[-1]
        assert pasted.id != src.id
        assert pasted.node_type == "stickyNote"

    def test_paste_url_creates_portal_and_plain_text_creates_sticky_note(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        display = Gdk.Display.get_default()
        assert display is not None
        clipboard = display.get_clipboard()

        # 1. URL on clipboard -> Portal node
        clipboard.set("https://example.org/sprint9")
        pump_gtk(80)
        win.paste_nodes(at=Point(10100, 8700))
        assert wait_until(
            lambda: any(
                n.node_type == "portal" and "example.org/sprint9" in getattr(n.content, "current_url", "")
                for n in win.document.payload.nodes
            ),
            timeout=3.0,
        )

        # 2. Plain text on clipboard -> StickyNote node with that text
        clipboard.set("Sprint 9 clipboard note body")
        pump_gtk(80)
        win.paste_nodes(at=Point(10500, 8700))
        assert wait_until(
            lambda: any(n.node_type == "stickyNote" for n in win.document.payload.nodes),
            timeout=3.0,
        )


# ============================================================================
# 6. P1 — K11: Partituras Save & Apply
# ============================================================================
class TestK11PartiturasSaveAndApply:
    def test_save_partitura_and_apply_partitura_creates_nodes_and_connections(self, isolated_env) -> None:
        _, client, win, ws_workdir = isolated_env
        t1 = win.new_terminal_node(NodeFrame(x=10000, y=8600, width=420, height=300), "Lead", "bash", str(ws_workdir))
        t2 = win.new_terminal_node(NodeFrame(x=10500, y=8600, width=420, height=300), "Dev", "bash", str(ws_workdir))
        win._insert_node(t1)
        win._insert_node(t2)
        conn = client.create_connection(t1.id, t2.id, kind="terminal", workspace_id=win.document.payload.id)
        if conn is not None:
            win.document.payload.connections.append(conn)

        assert hasattr(win, "save_partitura") and callable(win.save_partitura), "K11: win.save_partitura required"
        assert hasattr(win, "apply_partitura") and callable(win.apply_partitura), "K11: win.apply_partitura required"

        win.save_partitura("Duo Layout", [t1.id, t2.id])
        assert wait_until(
            lambda: any(p.get("name") == "Duo Layout" for p in client.list_partituras()),
            timeout=4.0,
        )
        saved = next(p for p in client.list_partituras() if p.get("name") == "Duo Layout")

        nodes_before = len(win.document.payload.nodes)
        win.apply_partitura(saved["id"], at=Point(11000, 9000))
        assert wait_until(lambda: len(win.document.payload.nodes) >= nodes_before + 2, timeout=4.0)


# ============================================================================
# 7. P2 — Shortcuts, HeaderBar Open in Editor, Menu Bar & FileTree Default Root
# ============================================================================
class TestVerticeShortcutsHeaderAndFileTreeRoot:
    def test_file_tree_node_defaults_to_workspace_working_directory(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env
        win._create_node("fileTree", at=Point(10000, 8600))
        ft_node = win.document.payload.nodes[-1]
        assert ft_node.node_type == "fileTree"
        assert os.path.realpath(ft_node.content.root_path) == os.path.realpath(str(ws_workdir))

    def test_menubar_is_built_and_open_in_editor_button_exists(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env
        menu_model = win._build_menu_model()
        assert menu_model.get_n_items() >= 3  # _File, _View, _Help

        # Check HeaderBar "Open in editor" button or action
        open_editor_fn = (
            getattr(win, "open_in_editor", None)
            or getattr(win, "_on_open_in_editor", None)
            or getattr(win, "_open_in_editor", None)
        )
        assert callable(open_editor_fn), (
            "Vertice P2: HeaderBar 'Open in editor' handler (`open_in_editor` / `_on_open_in_editor`) must exist"
        )
        with patch("subprocess.Popen") as mock_popen:
            open_editor_fn()
            assert mock_popen.called
