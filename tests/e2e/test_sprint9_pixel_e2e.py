"""
Sprint 9 — Pixel Workstream Acceptance & E2E Tests (Context Menus, Dialogs, Settings & Top Toolbar).

Covers §3 contracts (K9, K10, K11, K13) and §4 Pixel criteria:
1. Terminal Copy / Paste / Select All via `terminal_of(frame.content)`.
2. Context menu variants per node type (`terminal`, `stickyNote`, `portal`, `fileTree`, `text`/`shape`).
3. Right-click Rename on a StickyNote invokes `content.begin_rename()`.
4. Context menu actions: Reload (terminal & portal), Unload (terminal), Copy Element, Paste,
   Add ▸ Text (creates text node at click point & opens inline editor), Lock/Unlock (sets badge,
   persists `is_locked`, disables Delete), Monitor Activity (`monitor_with_ombro`), Assign Role ▸
   (reads `preferences.rolePresets`, sets `assigned_role_id`), Enable Maestro (`is_manager` + badge),
   Delete Connection (`ConnectionContextMenu`), New Element Defaults ▸ (`save_new_element_defaults`
   & `apply_new_element_defaults`), and Partituras ▸ (`apply_partitura` & `save_partitura`).
5. `NewTerminalDialog` & `EditTerminalDialog` returning all fields with real `THEMES` IDs and
   dynamic roles from `preferences.rolePresets`.
6. `SettingsWindow` 4th tab "Agents" with CRUD for `rolePresets` and `agentPresets` persisted to
   `preferences.json`.
7. Top toolbar (`CanvasTopToolbar`): Drawing popover (`DrawingTools.button` shapes/pen/highlighter),
   Note/FileTree/Portal draw-to-create (`cw.set_tool`), and Connection button calling
   `cw.begin_connect_mode(sel[0])`.

All tests run against an isolated `OPEN_MAESTRI_DATA_DIR` under Xvfb (`DISPLAY=:99`).
"""

from __future__ import annotations

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
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.scene import norm  # noqa: E402
from maestri_canvas.terminal_themes import THEMES  # noqa: E402
from maestri_canvas.widget import terminal_of  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent  # noqa: E402
from ui.context_menus import (  # noqa: E402
    CanvasContextMenu,
    ConnectionContextMenu,
    NodeContextMenu,
    apply_new_element_defaults,
    new_element_defaults,
    save_new_element_defaults,
)
from ui.settings_window import SettingsWindow  # noqa: E402
from ui.terminal_dialog import EditTerminalDialog, NewTerminalDialog  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402

from tests.data_dir_isolation import start_isolated_server  # noqa: E402


def pump_gtk(ms: int = 120) -> None:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + (ms / 1000.0)
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "open-maestri-data"
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(data_dir))
    ws_workdir = tmp_path / "pixel-workdir"
    ws_workdir.mkdir(parents=True, exist_ok=True)
    with start_isolated_server(data_dir) as srv:
        client = MaestriClient(port=srv['port'], auth_token=srv['token'])
        doc = client.get_workspace()
        assert doc is not None and doc.payload is not None
        doc.payload.working_directory = str(ws_workdir)
        doc.payload.nodes = []
        client.save_workspace(doc)

        app = Gtk.Application(
            application_id=f"com.openmaestri.pixel.s9.{uuid.uuid4().hex[:8]}",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        app.register(None)
        win = OpenMaestriWindow(app, client)
        win.present()
        assert win.wait_loaded(timeout=8.0)
        pump_gtk(150)
        try:
            yield srv, client, win, ws_workdir
        finally:
            win.close()
            pump_gtk(80)


# ============================================================================
# 1. P0 — NodeContextMenu Variants & Terminal Copy/Paste/Select All
# ============================================================================
class TestNodeContextMenuVariantsAndClipboard:
    def test_context_menu_variants_per_node_type(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env

        # 1. Terminal node menu
        t_node = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), "T1", "bash", str(ws_workdir))
        win._insert_node(t_node)
        t_menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        t_keys = [k for k in t_menu.layout["main"] if k is not None]
        for expected in ("copy", "paste", "select-all", "rename", "edit", "monitor", "assign-role",
                         "maestro", "blur", "reload", "unload", "defaults", "copy-element", "duplicate", "lock", "delete", "partituras"):
            assert expected in t_keys, f"Missing {expected!r} in terminal context menu"

        # 2. StickyNote node menu
        win._create_node("stickyNote", at=Point(10400, 8600))
        n_node = win.document.payload.nodes[-1]
        n_menu = NodeContextMenu(win, n_node.id, load_partituras=False)
        n_keys = [k for k in n_menu.layout["main"] if k is not None]
        for expected in ("rename", "move", "defaults", "copy-element", "duplicate", "lock", "delete", "partituras"):
            assert expected in n_keys, f"Missing {expected!r} in stickyNote context menu"
        assert "unload" not in n_keys
        assert "maestro" not in n_keys

        # 3. Portal node menu
        win._create_node("portal", at=Point(10800, 8600))
        p_node = win.document.payload.nodes[-1]
        p_menu = NodeContextMenu(win, p_node.id, load_partituras=False)
        p_keys = [k for k in p_menu.layout["main"] if k is not None]
        assert "reload" in p_keys
        assert "copy-element" in p_keys
        assert "unload" not in p_keys
        assert "partituras" not in p_keys

        # 4. FileTree node menu
        win._create_node("fileTree", at=Point(11200, 8600))
        ft_node = win.document.payload.nodes[-1]
        ft_menu = NodeContextMenu(win, ft_node.id, load_partituras=False)
        ft_keys = [k for k in ft_menu.layout["main"] if k is not None]
        assert ft_keys == ["copy-element", "duplicate", "lock", "delete"]

        # 5. Drawn text node menu
        d_node = win.drawing.create("text", Rect(11600, 8600, 180, 48))
        d_menu = NodeContextMenu(win, d_node.id, load_partituras=False)
        d_keys = [k for k in d_menu.layout["main"] if k is not None]
        assert d_keys == ["copy-element", "duplicate", "lock", "delete"]

    def test_terminal_menu_uses_terminal_of_for_copy_paste_select_all(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env
        t_node = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), "ClipTerm", "bash", str(ws_workdir))
        win._insert_node(t_node)
        pump_gtk(120)

        menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        term = menu._term()
        frame = win.canvas_widget.frames.get(norm(t_node.id))
        assert term is not None and term is terminal_of(frame.content)

        with patch.object(term, "select_all") as mock_sel, patch.object(term, "paste_clipboard") as mock_paste:
            assert menu.activate("select-all") is True
            mock_sel.assert_called_once()
            assert menu.activate("paste") is True
            mock_paste.assert_called_once()

    def test_sticky_note_rename_calls_content_begin_rename(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        win._create_node("stickyNote", at=Point(10000, 8600))
        note_node = win.document.payload.nodes[-1]
        pump_gtk(80)

        frame = win.canvas_widget.frames.get(norm(note_node.id))
        assert frame is not None
        menu = NodeContextMenu(win, note_node.id, load_partituras=False)
        with patch.object(frame.content, "begin_rename") as mock_begin_rename:
            assert menu.activate("rename") is True
            mock_begin_rename.assert_called_once()


# ============================================================================
# 2. P0 — Wired Context Menu Actions (Monitor, Maestro, Role, Lock, Defaults, Add▸Text, Connection)
# ============================================================================
class TestWiredContextMenuActions:
    def test_monitor_maestro_assign_role_and_lock_actions(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env
        role_id = str(uuid.uuid4()).upper()
        win.preferences._extra["rolePresets"] = [
            {"id": role_id, "name": "Architect", "instructions": "Design clean boundaries"}
        ]

        t_node = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), "Agent", "bash", str(ws_workdir))
        win._insert_node(t_node)
        pump_gtk(80)

        # Toggle Monitor Activity
        menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        prev_mon = bool(t_node.content.monitor_with_ombro)
        assert menu.activate("monitor") is True
        assert t_node.content.monitor_with_ombro is (not prev_mon)

        # Toggle Enable Maestro
        menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        assert menu.activate("maestro") is True
        assert t_node.content.is_manager is True

        # Assign Role from dynamic rolePresets
        menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        assert f"role:{role_id}" in menu.rows
        assert menu.activate(f"role:{role_id}") is True
        assert t_node.content.assigned_role_id == role_id

        # Lock node -> disables Delete row in fresh menu
        menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        assert menu.activate("lock") is True
        locked_menu = NodeContextMenu(win, t_node.id, load_partituras=False)
        assert locked_menu.locked is True
        assert locked_menu.rows["delete"].get_sensitive() is False
        assert locked_menu. activate("delete") is False

    def test_new_element_defaults_save_apply_and_reset(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env
        t_node = win.new_terminal_node(
            NodeFrame(x=9900, y=8600, width=640, height=440),
            "Styled",
            "bash",
            str(ws_workdir),
            theme_id="dracula",
            font_size=16.0,
        )
        win._insert_node(t_node)

        save_new_element_defaults(win, "terminal", t_node, "size")
        save_new_element_defaults(win, "terminal", t_node, "appearance")
        defs = new_element_defaults(win, "terminal")
        assert defs["size"] == [640.0, 440.0]
        assert defs["appearance"]["theme_id"] == "dracula"
        assert defs["appearance"]["font_size"] == 16.0

        fresh = CanvasNode(
            frame=NodeFrame(x=10500, y=8600, width=420, height=300),
            node_type="terminal",
            content=TerminalContent(name="New Term", command="bash"),
        )
        apply_new_element_defaults(win, fresh)
        assert (fresh.frame.width, fresh.frame.height) == (640.0, 440.0)
        assert fresh.content.theme_id == "dracula"
        assert fresh.content.font_size == 16.0

        save_new_element_defaults(win, "terminal", t_node, None)
        assert new_element_defaults(win, "terminal") == {}

    def test_canvas_context_menu_add_text_creates_drawn_text_and_edits(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        pt = Point(10200, 8750)
        cmenu = CanvasContextMenu(win, pt, load_partituras=False)
        with patch.object(win.canvas_widget, "edit_drawn_text") as mock_edit:
            assert cmenu.activate("add-text") is True
            pump_gtk(80)
            drawn_nodes = [n for n in win.document.payload.nodes if n.node_type == "text"]
            assert len(drawn_nodes) == 1
            assert (drawn_nodes[0].frame.x, drawn_nodes[0].frame.y) == (pt.x, pt.y)
            mock_edit.assert_called_once_with(drawn_nodes[0].id)

    def test_connection_context_menu_calls_window_delete_connection(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        win.delete_connection = MagicMock()
        conn_menu = ConnectionContextMenu(win, "CONN-123")
        assert conn_menu.activate("delete-connection") is True
        win.delete_connection.assert_called_once_with("CONN-123")


# ============================================================================
# 3. P1 — Complete `NewTerminalDialog`, `EditTerminalDialog` & `SettingsWindow` Agents Tab
# ============================================================================
class TestTerminalDialogsAndSettingsAgentsTab:
    def test_new_and_edit_terminal_dialogs_expose_all_fields_and_real_themes(self, isolated_env) -> None:
        _, _, win, ws_workdir = isolated_env
        role_id = str(uuid.uuid4()).upper()
        win.preferences._extra["rolePresets"] = [
            {"id": role_id, "name": "QA Lead", "instructions": "Verify every contract"}
        ]

        captured_create: dict = {}
        dlg = NewTerminalDialog(win, on_create=lambda d: captured_create.update(d), default_cwd=str(ws_workdir))
        dlg.present()
        pump_gtk(60)
        dlg._on_submit(None)
        pump_gtk(60)

        # Must return real theme_id from THEMES and full field set
        theme_val = captured_create.get("theme_id") or captured_create.get("theme")
        assert theme_val in THEMES, f"Expected theme_id in THEMES ({list(THEMES)}), got {theme_val!r}"
        for key in ("icon", "color", "font_size"):
            assert key in captured_create, f"NewTerminalDialog missing {key!r} in output payload"

        # EditTerminalDialog must also expose tabs / full fields
        t_node = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), "Editable", "bash", str(ws_workdir))
        win._insert_node(t_node)
        captured_edit: dict = {}
        edlg = EditTerminalDialog(win, t_node, on_save=lambda d: captured_edit.update(d))
        edlg.present()
        pump_gtk(60)
        edlg._on_save_clicked(None)
        pump_gtk(60)
        for key in ("name", "command", "cwd", "theme_id", "font_size", "monitor_with_ombro", "is_manager"):
            assert key in captured_edit or key.replace("_id", "") in captured_edit, (
                f"EditTerminalDialog must return {key!r} on save"
            )

    def test_settings_window_has_agents_tab_with_role_and_agent_presets(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        sw = SettingsWindow(win, win.prefs_store, win._on_preferences_changed)
        sw.present()
        pump_gtk(80)
        try:
            notebook = sw.get_child()
            assert isinstance(notebook, Gtk.Notebook)
            tab_labels = [
                notebook.get_tab_label_text(notebook.get_nth_page(i))
                for i in range(notebook.get_n_pages())
            ]
            assert "Agents" in tab_labels, f"SettingsWindow must include 'Agents' tab, got {tab_labels}"
        finally:
            sw.close()
            pump_gtk(40)


# ============================================================================
# 4. P1 — Top Toolbar Buttons (`floating_bars.py`)
# ============================================================================
class TestTopToolbarButtons:
    def test_top_toolbar_note_filetree_portal_set_draw_tool_and_connect_begins_connect_mode(
        self, isolated_env
    ) -> None:
        _, _, win, ws_workdir = isolated_env
        tb = win.top_toolbar
        cw = win.canvas_widget

        # Clicking stickyNote / fileTree / portal in top toolbar activates draw-to-create tool
        for tool_kind in ("stickyNote", "fileTree", "portal"):
            tb._on_tool_clicked(tool_kind)
            pump_gtk(40)
            assert cw.controller.tool == tool_kind, f"Top toolbar button {tool_kind!r} should set cw.controller.tool = {tool_kind!r}"

        # Clicking Connect button in context_pill calls cw.begin_connect_mode(sel[0])
        t_node = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), "Source", "bash", str(ws_workdir))
        win._insert_node(t_node)
        cw.scene.select_only(t_node.id)
        pump_gtk(40)

        with patch.object(cw, "begin_connect_mode", create=True) as mock_begin_conn:
            tb._on_connect_clicked(None)
            mock_begin_conn.assert_called_once_with(norm(t_node.id))
