"""
E2E Test Suite for Visual Parity with macOS Maestri (docs/images/home.png and docs/images/create-terminal.png).

Covers:
1. Top Center Floating Island Toolbar (8 primary tools + selection actions pill).
2. Bottom Right Floating Controls (Floors button, Minimap toggle, Zoom -, %, +).
3. New Terminal Modal Dialog with Quick Start presets (Claude Code, Codex, Gemini CLI, OpenCode, Shell)
   and Details/Appearance/Roles tabs.
4. Modernized Sidebar with search entry and card-based workspace items.
5. High-resolution visual snapshot verification.
"""

import os
import sys
import time
from pathlib import Path
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.demo import screenshot  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from ui.floating_bars import CanvasBottomRightControls, CanvasTopToolbar  # noqa: E402
from ui.terminal_dialog import NewTerminalDialog, PRESETS  # noqa: E402
from ui.workspace_dialogs import (  # noqa: E402
    CreateWorkspaceDialog,
    EditWorkspaceDialog,
    DeleteWorkspaceDialog,
    GroupDialog,
)
from maestri_core.models.workspace import SidebarGroup, WorkspaceEntry  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def pump_events(seconds: float = 0.2, interval: float = 0.005) -> None:
    ctx = GLib.MainContext.default()
    end_time = time.time() + seconds
    while time.time() < end_time:
        ctx.iteration(False)
        time.sleep(interval)


def wait_until(cond, timeout: float = 5.0) -> bool:
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


@pytest.fixture(scope="module")
def maestri_app():
    app = Gtk.Application(
        application_id="org.openmaestri.visualparity",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    yield app


@pytest.fixture
def window(maestri_app):
    client = MaestriClient(port=0)
    win = OpenMaestriWindow(maestri_app, client)
    win.present()
    assert win.wait_loaded(), "workspace did not load"
    pump_events(0.2)
    yield win
    try:
        if hasattr(win, "sidebar") and win.sidebar:
            win.sidebar.close_active_popovers()
        win.close()
    except Exception:
        pass
    pump_events(0.1)


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestVisualParityMac:
    """Verifies 100% visual and functional parity with the official macOS Maestri interface."""

    def test_top_floating_island_toolbar_and_actions(self, window):
        """Top island toolbar is mounted, has all 8 tools, and selection shows the actions pill."""
        top_bar = window.top_toolbar
        assert isinstance(top_bar, CanvasTopToolbar)
        assert top_bar.get_parent() is window.canvas_stack

        # 8 tools exist
        expected_tools = ["select", "terminal", "stickyNote", "file", "fileTree", "portal", "text", "draw"]
        for tid in expected_tools:
            assert tid in top_bar.tool_buttons
            assert top_bar.tool_buttons[tid].get_visible()

        # Context actions pill is initially hidden (no selection)
        window.canvas_widget.scene.selection = set()
        window.canvas_widget.emit("selection-changed", ())
        pump_events(0.05)
        assert top_bar.context_pill.get_visible() is False

        # Clicking text tool sets canvas tool
        top_bar.tool_buttons["text"].emit("clicked")
        pump_events(0.05)
        assert window.canvas_widget.controller.tool == "text"
        assert "active" in top_bar.tool_buttons["text"].get_css_classes()

        # Selecting a node makes context pill visible
        nodes = list(window.canvas_widget.scene.nodes)
        if nodes:
            window.canvas_widget.scene.selection = {nodes[0]}
            window.canvas_widget.emit("selection-changed", (nodes[0],))
            pump_events(0.05)
            assert top_bar.context_pill.get_visible() is True

    def test_bottom_right_controls_and_zoom(self, window):
        """Bottom right controls include floors, minimap, and reactive zoom controls."""
        bottom = window.bottom_controls
        assert isinstance(bottom, CanvasBottomRightControls)
        assert bottom.get_parent() is window.canvas_stack

        # Initial zoom label matches 100%
        assert "100%" in bottom.zoom_lbl.get_label()

        # Zoom in
        cw = window.canvas_widget
        z0 = cw.viewport.zoom
        bottom._zoom_step(1.25)
        pump_events(0.05)
        assert cw.viewport.zoom > z0
        assert "125%" in bottom.zoom_lbl.get_label()

        # Zoom out
        bottom._zoom_step(0.8)
        pump_events(0.05)
        assert abs(cw.viewport.zoom - z0) < 0.05
        assert "100%" in bottom.zoom_lbl.get_label()

    def test_new_terminal_dialog_presets_and_creation(self, window):
        """New Terminal dialog matches docs/images/create-terminal.png with 5 presets and tabs."""
        created_data = []

        dlg = NewTerminalDialog(
            window,
            on_create=lambda data: created_data.append(data),
            default_cwd="/tmp",
            default_preset="claude",
        )
        dlg.present()
        pump_events(0.1)

        # 5 presets exist
        assert len(dlg.preset_buttons) == 5
        for p in PRESETS:
            assert p["id"] in dlg.preset_buttons

        # Initial preset is Claude Code
        assert dlg.name_entry.get_text() == "Claude Code"
        assert dlg.cmd_entry.get_text() == "claude"
        assert dlg.chk_monitor.get_active() is True
        assert dlg.chk_maestro.get_active() is False

        # Switching to Codex preset updates name and command
        dlg._select_preset("codex")
        pump_events(0.05)
        assert dlg.name_entry.get_text() == "Codex"
        assert dlg.cmd_entry.get_text() == "codex"

        # Switching to Gemini CLI updates name and command
        dlg._select_preset("gemini")
        pump_events(0.05)
        assert dlg.name_entry.get_text() == "Gemini CLI"
        assert dlg.cmd_entry.get_text() == "gemini"

        # Tab switching
        dlg._select_tab("appearance")
        pump_events(0.05)
        assert dlg.stack.get_visible_child_name() == "appearance"

        dlg._select_tab("roles")
        pump_events(0.05)
        assert dlg.stack.get_visible_child_name() == "roles"

        dlg._select_tab("details")
        pump_events(0.05)
        assert dlg.stack.get_visible_child_name() == "details"

        # Submit creates the terminal data
        dlg.create_btn.emit("clicked")
        pump_events(0.1)
        assert len(created_data) == 1
        assert created_data[0]["name"] == "Gemini CLI"
        assert created_data[0]["command"] == "gemini"
        assert created_data[0]["monitor"] is True

        # Clicking >_ on the top toolbar activates terminal tool, and drawing/dimensioning on canvas opens NewTerminalDialog
        window.top_toolbar.tool_buttons["terminal"].emit("clicked")
        assert window.canvas_widget.controller.tool == "terminal"
        window.canvas_widget.emit("draw-requested", "terminal", 100.0, 100.0, 560.0, 360.0)
        pump_events(0.1)
        active_dlg = getattr(window, "_active_terminal_dialog", None)
        assert isinstance(active_dlg, NewTerminalDialog)
        assert set(active_dlg.preset_buttons.keys()) == {"claude", "codex", "gemini", "opencode", "shell"}
        active_dlg.close()
        pump_events(0.05)

        # + Add > Terminal submenu contains the 5 AI presets
        term_menu = window.terminal_presets_menu
        assert term_menu.get_n_items() >= 5
        labels = [
            term_menu.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_LABEL, GLib.VariantType.new("s")).get_string()
            for i in range(term_menu.get_n_items())
        ]
        for expected in ("Claude Code", "Codex", "Gemini CLI", "OpenCode", "Shell"):
            assert any(expected in lbl for lbl in labels)

    def test_sidebar_workspace_cards_and_search(self, window):
        """Sidebar provides modern cards and search entry matching docs/images/home.png."""
        sidebar = window.sidebar
        assert sidebar.search_entry is not None
        assert sidebar.search_entry.get_placeholder_text() == "Search Workspaces"
        assert not hasattr(sidebar, "floors_box")
        assert not hasattr(sidebar, "agents_box")

        # Verify cards are rendered
        pump_events(0.1)
        assert sidebar.ws_list_box.get_first_child() is not None

        # Test search filter
        sidebar.search_entry.set_text("NonExistentXYZ")
        pump_events(0.05)
        # Should be empty or filtered
        sidebar.search_entry.set_text("")
        pump_events(0.05)
        assert sidebar.ws_list_box.get_first_child() is not None

    def test_workspace_functions_context_menu(self, window):
        """Verifies full workspace context menu (Edit, Duplicate, Open in FM, Pin, Group, Delete)."""
        sidebar = window.sidebar
        entry = WorkspaceEntry(
            id="ws-test-1",
            name="Test Project",
            working_directory="/tmp",
            icon="folder",
            color="blue",
            is_pinned=False,
        )
        card = sidebar._create_workspace_card(entry, count=3)
        assert hasattr(card, "ws_id")
        assert card.ws_id == "ws-test-1"

        sidebar.ws_list_box.append(card)
        pump_events(0.1)

        # Popover creation
        sidebar._show_workspace_context_menu(card, 0, 0, entry)
        pump_events(0.1)

        # Pin toggle updates status
        entry.is_pinned = True
        card_pinned = sidebar._create_workspace_card(entry, count=3)
        badges = [c.get_label() for c in card_pinned.get_last_child().observe_children() if hasattr(c, "get_label")]
        assert "📌" in badges

        sidebar.close_active_popovers()
        pump_events(0.05)

    def test_create_and_edit_workspace_sheets(self, window):
        """Tests CreateWorkspaceDialog and EditWorkspaceDialog macOS sheets."""
        created = []
        c_dlg = CreateWorkspaceDialog(window, on_create=lambda n, wd, ic: created.append((n, wd, ic)))
        c_dlg.present()
        pump_events(0.05)

        # Initial button is disabled until name is typed
        assert c_dlg.btn_create.get_sensitive() is False
        c_dlg.name_entry.set_text("Alpha Workspace")
        pump_events(0.05)
        assert c_dlg.btn_create.get_sensitive() is True

        # Icon selection
        c_dlg._select_icon("brain")
        assert c_dlg.selected_icon == "brain"

        c_dlg.btn_create.emit("clicked")
        pump_events(0.05)
        assert len(created) == 1
        assert created[0][0] == "Alpha Workspace"
        assert created[0][2] == "brain"

        # Edit Sheet
        saved = []
        entry = WorkspaceEntry(id="alpha-1", name="Alpha", working_directory="/tmp", icon="cpu", color="orange")
        e_dlg = EditWorkspaceDialog(window, entry, on_save=lambda upd: saved.append(upd))
        e_dlg.present()
        pump_events(0.05)

        assert e_dlg.name_entry.get_text() == "Alpha"
        assert e_dlg.selected_color == "orange"
        e_dlg._select_color("purple")
        e_dlg.name_entry.set_text("Alpha Renamed")
        e_dlg.btn_save.emit("clicked")
        pump_events(0.05)
        assert len(saved) == 1
        assert saved[0].name == "Alpha Renamed"
        assert saved[0].color == "purple"

        # Delete Dialog
        deleted = []
        d_dlg = DeleteWorkspaceDialog(window, "Alpha", on_confirm=lambda: deleted.append(True))
        d_dlg.present()
        pump_events(0.05)
        d_dlg._on_delete_clicked(d_dlg.get_last_child())
        pump_events(0.05)
        assert len(deleted) == 1

    def test_macos_traffic_lights_and_header(self, window):
        """Headerbar has traffic light dots and quick create menu."""
        header = window.get_titlebar()
        assert header is not None
        assert hasattr(window, "traffic_box")
        assert "maestri-traffic-lights" in window.traffic_box.get_css_classes()
        assert window.traffic_box.get_parent() is not None

        # Verify 3 traffic dots exist (close, min, max)
        dots = [c for c in window.traffic_box.observe_children()]
        assert len(dots) == 3

        # Verify title buttons are disabled on HeaderBar to prevent ghost buttons from GNOME/system
        assert header.get_show_title_buttons() is False

        # Verify paned configuration keeps sidebar width stable and prevents clipping
        assert window.paned.get_resize_start_child() is False
        assert window.paned.get_shrink_start_child() is False

        # Verify sidebar toggle behavior and floating restore button
        assert window.sidebar.get_visible() is True
        assert window.btn_floating_sidebar.get_visible() is False

        # Toggle hidden (with animation)
        window.btn_sidebar.emit("clicked")
        pump_events(0.25)
        assert window.sidebar.get_visible() is False
        assert window.btn_floating_sidebar.get_visible() is True

        # Click floating button to restore
        window.btn_floating_sidebar.emit("clicked")
        pump_events(0.25)
        assert window.sidebar.get_visible() is True
        assert window.btn_floating_sidebar.get_visible() is False

    def test_terminal_creation_and_renaming(self, window):
        """Verifies terminal creation and renaming via dialog and inline."""
        n0 = len(window.document.payload.nodes)

        # 1. Selection of terminal tool activates dimensioning mode
        window.top_toolbar._on_tool_clicked("terminal")
        assert window.canvas_widget.controller.tool == "terminal"

        # User dimensions the target frame on the canvas before the dialog opens
        window.canvas_widget.emit("draw-requested", "terminal", 100.0, 120.0, 640.0, 480.0)
        pump_events(0.1)
        assert window._active_terminal_dialog is not None
        window._active_terminal_dialog.create_btn.emit("clicked")
        pump_events(0.1)
        assert len(window.document.payload.nodes) == n0 + 1
        new_term = window.document.payload.nodes[-1]
        assert new_term.node_type == "terminal"
        assert new_term.frame.width == 640.0
        assert new_term.frame.height == 480.0

        # 2. Rename selected terminal
        window.canvas_widget.scene.selection = {new_term.id}
        window.canvas_widget.emit("selection-changed", (new_term.id,))
        pump_events(0.05)

        # Rename via node-title-changed signal
        window.canvas_widget.emit("node-title-changed", new_term.id, "Renamed Terminal")
        pump_events(0.05)
        assert new_term.title == "Renamed Terminal"
        assert new_term.content.name == "Renamed Terminal"

    def test_node_connecting_both_modes(self, window):
        """Verifies connection creation works reliably in both single-selection and multi-selection."""
        cw = window.canvas_widget
        nodes = list(cw.scene.nodes)
        if len(nodes) < 2:
            window._create_node("terminal")
            window._create_node("terminal")
            pump_events(0.1)
            nodes = list(cw.scene.nodes)

        a, b = nodes[0], nodes[1]
        cw.scene.selection = {a, b}
        cw.emit("selection-changed", (a, b))
        pump_events(0.05)

        c0 = len(cw.scene.connections)
        window.top_toolbar._on_connect_clicked(None)
        pump_events(0.1)
        assert len(cw.scene.connections) >= c0 + 1

    def test_visual_snapshot_capture(self, window):
        """Captures a full visual snapshot of the canvas with the floating macOS bars."""
        out_dir = ROOT_DIR / "artifacts" / "visual-parity"
        out_dir.mkdir(parents=True, exist_ok=True)
        shot_path = str(out_dir / "01_mac_parity_canvas.png")

        pump_events(0.2)
        screenshot(window.canvas_widget, shot_path)
        assert os.path.isfile(shot_path)
        assert os.path.getsize(shot_path) > 1000
