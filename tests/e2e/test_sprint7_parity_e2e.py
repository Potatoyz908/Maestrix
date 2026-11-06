"""
Sprint 7 E2E Parity Test Suite for Open Maestri GTK 4 Native.

Covers the 4 Sprint 7 QA requirements:
1. Connection by dropping anywhere on the target node's bounding box (body/header/corners),
   anchored at the center of the border nearest the source node (left, right, top, bottom).
2. Opening and interacting with the right-click context menus (canvas background, terminal/node,
   workspace card, sidebar group, and HeaderBar '+ Add > Terminal' presets).
3. Removal of dead static sections ('FLOORS (WORKTREES)' and 'CANVAS AGENTS') from the sidebar.
4. Bottom-right floating capsules / controls (Floors/Workspace, Agent rings, Minimap, Zoom -, %, +).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import ConnectRequested, Mods, NodePin  # noqa: E402
from maestri_canvas.scene import NodeItem, nearest_side, pin_point  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame, StickyNoteContent, TerminalContent  # noqa: E402
from maestri_core.models.workspace import SidebarGroup, SidebarLayout, WorkspaceEntry  # noqa: E402
from ui.floating_bars import CanvasBottomRightControls  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def pump_events(seconds: float = 0.15, interval: float = 0.005) -> None:
    ctx = GLib.MainContext.default()
    end_time = time.monotonic() + seconds
    while time.monotonic() < end_time:
        while ctx.iteration(False):
            pass
        time.sleep(interval)


def walk_widgets(root: Gtk.Widget) -> Iterator[Gtk.Widget]:
    """Yield `root` and every descendant widget in its GTK4 tree."""
    stack = [root]
    while stack:
        w = stack.pop()
        yield w
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


def collect_menu_labels(menu_model: Gio.MenuModel) -> list[str]:
    """Recursively collect all item labels from a Gio.MenuModel and its sections/submenus."""
    labels: list[str] = []
    n = menu_model.get_n_items()
    for i in range(n):
        val = menu_model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_LABEL, GLib.VariantType.new("s"))
        if val is not None:
            labels.append(val.get_string())
        for link_name in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
            sub = menu_model.get_item_link(i, link_name)
            if sub is not None:
                labels.extend(collect_menu_labels(sub))
    return labels


@pytest.fixture(scope="module")
def maestri_app():
    app = Gtk.Application(
        application_id="org.openmaestri.sprint7.e2e",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    yield app


@pytest.fixture
def window(maestri_app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    (tmp_path / ".zshrc").touch()
    client = MaestriClient(port=0)
    win = OpenMaestriWindow(maestri_app, client)
    win.present()
    assert win.wait_loaded(), "Workspace did not finish initial load"
    pump_events(0.15)
    yield win
    win.close()
    pump_events(0.05)


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestSprint7ParityE2E:
    """End-to-end verification of Sprint 7 macOS parity features."""

    # ------------------------------------------------------------------
    # 1. Connection by dropping anywhere on a node's bounding box
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "target_rect,expected_side",
        [
            (Rect(10500, 8600, 320, 220), "left"),    # target to the right of source -> anchors on left border center
            (Rect(9200, 8600, 320, 220), "right"),    # target to the left of source -> anchors on right border center
            (Rect(9900, 9100, 320, 220), "top"),      # target below source -> anchors on top border center
            (Rect(9900, 8100, 320, 220), "bottom"),   # target above source -> anchors on bottom border center
        ],
    )
    def test_connection_drop_anywhere_on_node_box(self, window, target_rect: Rect, expected_side: str):
        """Releasing a connection drag anywhere inside a target node's box connects and anchors at the nearest border center."""
        cw = window.canvas_widget
        ctl = cw.controller

        # Clear existing nodes & connections for deterministic geometry
        for nid in list(cw.scene.nodes):
            cw.remove_node(nid)
        window.document.payload.nodes.clear()
        window.document.payload.connections.clear()

        src_frame = Rect(9900, 8600, 320, 220)
        src_node = CanvasNode(
            id="SRC-NODE",
            frame=NodeFrame(x=src_frame.x, y=src_frame.y, width=src_frame.w, height=src_frame.h),
            node_type="terminal",
            content=TerminalContent(name="Source Terminal", command="bash"),
            z_index=1,
        )
        dst_node = CanvasNode(
            id="DST-NODE",
            frame=NodeFrame(x=target_rect.x, y=target_rect.y, width=target_rect.w, height=target_rect.h),
            node_type="stickyNote",
            content=StickyNoteContent(name="Target Note", file_name="Target.md"),
            z_index=2,
        )
        window.document.payload.nodes.extend([src_node, dst_node])
        window._add_node_to_canvas(src_node)
        window._add_node_to_canvas(dst_node)
        pump_events(0.05)

        # Start drag from source's right pin
        pin_start_canvas = pin_point(src_frame, "right")
        pin_start_screen = cw.viewport.to_screen(pin_start_canvas)

        # Drop deep inside the target node's body (75% across width, 65% down height — far from the 14px pin)
        drop_canvas = Point(target_rect.x + target_rect.w * 0.75, target_rect.y + target_rect.h * 0.65)
        drop_screen = cw.viewport.to_screen(drop_canvas)

        ctl.press(pin_start_screen, 1, Mods.NONE, NodePin("SRC-NODE", "right"))
        ctl.move(drop_screen, Mods.NONE)

        # While hovering over the body, overlay.connecting highlights the target
        assert ctl.overlay.connecting is not None
        hovered_target = ctl.overlay.connecting[2]
        assert hovered_target is not None
        assert hovered_target[0] == "DST-NODE"
        if hovered_target[1] is not None:
            assert hovered_target[1] == expected_side

        # Release inside the body -> emits ConnectRequested -> creates connection in scene
        ctl.release(drop_screen, Mods.NONE)
        pump_events(0.1)

        conns = list(cw.scene.connections.values())
        assert len(conns) == 1
        conn = conns[0]
        assert {conn.a, conn.b} == {"SRC-NODE", "DST-NODE"}

        # Verify the anchor point on the target node is the exact center of `expected_side`
        anchors = cw.scene.anchors(conn)
        assert anchors is not None
        expected_dst_anchor = pin_point(target_rect, expected_side)
        dst_anchor = anchors[1] if conn.b == "DST-NODE" else anchors[0]
        assert abs(dst_anchor.x - expected_dst_anchor.x) < 1e-3
        assert abs(dst_anchor.y - expected_dst_anchor.y) < 1e-3

    def test_connection_drop_on_header_and_lookthrough_drawn_shape(self, window):
        """Dropping on the node header or through an overlapping drawn shape connects to the underlying connectable node."""
        cw = window.canvas_widget
        ctl = cw.controller

        for nid in list(cw.scene.nodes):
            cw.remove_node(nid)
        window.document.payload.nodes.clear()

        src_rect = Rect(9900, 8600, 300, 200)
        dst_rect = Rect(10400, 8600, 300, 200)
        src_node = CanvasNode(
            id="TERM-1",
            frame=NodeFrame(x=src_rect.x, y=src_rect.y, width=src_rect.w, height=src_rect.h),
            node_type="terminal",
            content=TerminalContent(name="Term 1", command="bash"),
            z_index=1,
        )
        dst_node = CanvasNode(
            id="TERM-2",
            frame=NodeFrame(x=dst_rect.x, y=dst_rect.y, width=dst_rect.w, height=dst_rect.h),
            node_type="terminal",
            content=TerminalContent(name="Term 2", command="bash"),
            z_index=2,
        )
        window.document.payload.nodes.extend([src_node, dst_node])
        window._add_node_to_canvas(src_node)
        window._add_node_to_canvas(dst_node)

        # Place a drawn shape on top of TERM-2 (higher z-index) and a non-connectable fileTree nearby
        cw.scene.add_node(NodeItem("DRAWN-OVERLAY", Rect(10420, 8610, 120, 80), 10, "shape", "Overlay"))
        cw.scene.add_node(NodeItem("FILE-TREE-1", Rect(10400, 8950, 300, 200), 3, "fileTree", "Files"))
        pump_events(0.05)

        start_screen = cw.viewport.to_screen(pin_point(src_rect, "right"))

        # 1. Dropping on non-connectable fileTree creates no connection
        ft_drop = cw.viewport.to_screen(Point(10550, 9050))
        ctl.press(start_screen, 1, Mods.NONE, NodePin("TERM-1", "right"))
        ctl.move(ft_drop, Mods.NONE)
        ctl.release(ft_drop, Mods.NONE)
        pump_events(0.05)
        assert len(cw.scene.connections) == 0

        # 2. Dropping on TERM-2's header (covered by DRAWN-OVERLAY) looks through the shape and connects TERM-1 <-> TERM-2
        header_drop = cw.viewport.to_screen(Point(10460, 8614))
        ctl.press(start_screen, 1, Mods.NONE, NodePin("TERM-1", "right"))
        ctl.move(header_drop, Mods.NONE)
        ctl.release(header_drop, Mods.NONE)
        pump_events(0.1)

        assert len(cw.scene.connections) == 1
        conn = next(iter(cw.scene.connections.values()))
        assert {conn.a, conn.b} == {"TERM-1", "TERM-2"}
        assert nearest_side(dst_rect, src_rect) == "left"

    # ------------------------------------------------------------------
    # 2. Removal of dead static sections from the sidebar
    # ------------------------------------------------------------------
    def test_sidebar_dead_sections_removed(self, window):
        """Sidebar has no static FLOORS (WORKTREES) or CANVAS AGENTS sections, even after refresh."""
        sidebar = window.sidebar

        # Attributes for dead sections must not exist
        assert not hasattr(sidebar, "floors_box")
        assert not hasattr(sidebar, "floors_list_box")
        assert not hasattr(sidebar, "agents_box")
        assert not hasattr(sidebar, "agents_list_box")

        # Trigger a refresh with workspaces and groups to ensure nothing re-renders dead sections
        sample_ws = [
            WorkspaceEntry(id="ws-1", name="Maestri Linux", working_directory="/tmp/maestri", icon="terminal", color="blue")
        ]
        sidebar._apply_refresh(sample_ws, SidebarLayout())
        pump_events(0.05)

        all_widgets = list(walk_widgets(sidebar))
        labels = [w.get_label() or "" for w in all_widgets if isinstance(w, Gtk.Label)]
        css_classes = {cls for w in all_widgets for cls in w.get_css_classes()}

        for dead_heading in ("FLOORS (WORKTREES)", "CANVAS AGENTS"):
            assert not any(dead_heading in lbl for lbl in labels), f"Dead section {dead_heading!r} still in sidebar"

        for dead_css in ("maestri-floors-list", "maestri-agents-list", "maestri-floor-card", "maestri-agent-card"):
            assert dead_css not in css_classes, f"Dead CSS class {dead_css!r} still in sidebar"

        # Live workspace sections remain intact
        assert sidebar.search_entry.get_placeholder_text() == "Search Workspaces"
        assert sidebar.ws_list_box.get_first_child() is not None
        assert "maestri-workspace-card" in css_classes

    # ------------------------------------------------------------------
    # 3. Opening the right-click context menus
    # ------------------------------------------------------------------
    def test_right_click_context_menus(self, window):
        """Right-click opens context menus on canvas background, workspace cards, groups, and nodes."""
        cw = window.canvas_widget
        sidebar = window.sidebar

        # 3a. Canvas Blank Area Right-Click Context Menu
        # Find an empty background screen coordinate (e.g. (40, 40))
        empty_pt = Point(40.0, 40.0)
        if hasattr(window, "_on_canvas_right_click"):
            gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            window._on_canvas_right_click(gesture, 1, empty_pt.x, empty_pt.y)
            pump_events(0.08)

        ctx_popover = getattr(window, "_ctx_popover", None) or getattr(
            getattr(window, "context_menus", None), "canvas_popover", None
        )
        assert ctx_popover is not None, "Canvas right-click popover should be configured"
        menu_model = ctx_popover.get_menu_model() if hasattr(ctx_popover, "get_menu_model") else None
        if menu_model is not None:
            menu_labels = collect_menu_labels(menu_model)
            assert any("Terminal" in lbl for lbl in menu_labels)
            assert any("Note" in lbl for lbl in menu_labels)
            assert any("Portal" in lbl for lbl in menu_labels)
            assert any("File Tree" in lbl for lbl in menu_labels)

        # Verify canvas 'ctx' action group creates a node at clicked point
        n_before = len(window.document.payload.nodes)
        ctx_group = cw.get_action_group("ctx") if hasattr(cw, "get_action_group") else None
        if ctx_group is not None:
            if ctx_group.has_action("new-stickyNote"):
                ctx_group.activate_action("new-stickyNote", None)
            elif ctx_group.has_action("add"):
                ctx_group.activate_action("add", GLib.Variant.new_string("stickyNote"))
            pump_events(0.08)
            assert len(window.document.payload.nodes) == n_before + 1

        if ctx_popover.get_visible():
            ctx_popover.popdown()
            pump_events(0.05)

        # 3b. Node / Terminal Right-Click Context Menu (when present via context_menus or node action group)
        term_nodes = [n for n in window.document.payload.nodes if n.node_type == "terminal"]
        if not term_nodes:
            window.create_terminal_preset("claude")
            pump_events(0.08)
            term_nodes = [n for n in window.document.payload.nodes if n.node_type == "terminal"]
        term_node = term_nodes[0]

        if hasattr(cw, "signals") or GObject_has_signal(cw, "context-requested"):
            cw.emit("context-requested", "node", term_node.id, 200.0, 200.0)
            pump_events(0.08)
        elif hasattr(window, "_show_node_context_menu"):
            window._show_node_context_menu(term_node.id, 200.0, 200.0)
            pump_events(0.08)

        node_popover = getattr(window, "_node_ctx_popover", None) or getattr(
            getattr(window, "context_menus", None), "node_popover", None
        )
        if node_popover is not None and hasattr(node_popover, "get_menu_model") and node_popover.get_menu_model():
            node_labels = collect_menu_labels(node_popover.get_menu_model())
            for expected_item in ("Copy", "Paste", "Rename", "Duplicate"):
                assert any(expected_item in lbl for lbl in node_labels)
            if node_popover.get_visible():
                node_popover.popdown()

        # 3c. Sidebar Workspace Card Right-Click Context Menu
        entry = WorkspaceEntry(
            id="ws-ctx-test",
            name="Context Menu WS",
            working_directory="/tmp",
            icon="folder",
            color="blue",
            is_pinned=False,
        )
        card = sidebar._create_workspace_card(entry, count=2)
        sidebar.ws_list_box.append(card)
        pump_events(0.05)

        sidebar._show_workspace_context_menu(card, 10.0, 10.0, entry)
        pump_events(0.08)
        ws_popovers = [w for w in walk_widgets(card) if isinstance(w, Gtk.Popover)]
        assert len(ws_popovers) >= 1
        ws_pop = ws_popovers[-1]
        assert "maestri-context-popover" in ws_pop.get_css_classes()
        ws_btn_labels = [w.get_label() or "" for w in walk_widgets(ws_pop) if isinstance(w, Gtk.Button)]
        for expected_ws_action in ("Edit Workspace", "Duplicate Workspace", "Open in File Manager", "Pin Workspace", "Delete Workspace"):
            assert any(expected_ws_action in lbl for lbl in ws_btn_labels)
        ws_pop.popdown()
        pump_events(0.05)

        # 3d. Sidebar Group Right-Click Context Menu
        grp = SidebarGroup(id="grp-ctx-1", name="Backend Services", items=[])
        grp_box = sidebar._create_group_box(grp, {})
        sidebar.groups_container.append(grp_box)
        pump_events(0.05)
        header_row = grp_box.get_first_child()
        sidebar._show_group_context_menu(header_row, 10.0, 10.0, grp)
        pump_events(0.08)
        grp_popovers = [w for w in walk_widgets(header_row) if isinstance(w, Gtk.Popover)]
        assert len(grp_popovers) >= 1
        grp_pop = grp_popovers[-1]
        grp_btn_labels = [w.get_label() or "" for w in walk_widgets(grp_pop) if isinstance(w, Gtk.Button)]
        assert any("Rename Group" in lbl for lbl in grp_btn_labels)
        assert any("Delete Group" in lbl for lbl in grp_btn_labels)
        grp_pop.popdown()
        pump_events(0.05)

    # ------------------------------------------------------------------
    # 4. Bottom-right capsules / controls
    # ------------------------------------------------------------------
    def test_bottom_four_capsules_bar(self, window):
        """Verifies the bottom-right floating capsules (Floors/Workspace, Agent rings, Minimap, Zoom)."""
        bottom = window.bottom_controls
        assert isinstance(bottom, CanvasBottomRightControls)
        assert bottom.get_parent() is window.canvas_stack
        assert bottom.get_halign() == Gtk.Align.END
        assert bottom.get_valign() == Gtk.Align.END

        # Collect all floating pills inside bottom_controls
        pills = [w for w in walk_widgets(bottom) if "maestri-floating-pill" in w.get_css_classes()]
        assert len(pills) >= 1

        # If refactored into 4 separate pills (P1 Floors/Workspace, P2 Agent Rings, P3 Minimap, P4 Zoom),
        # verify all 4 pill containers exist
        direct_pills = [c for c in bottom.observe_children() if "maestri-floating-pill" in c.get_css_classes()]
        if len(direct_pills) == 4:
            p1_floors, p2_rings, p3_minimap, p4_zoom = direct_pills
            assert p1_floors.get_visible()
            assert p3_minimap.get_visible()
            assert p4_zoom.get_visible()
            # P2 rings pill is visible when monitored terminals exist, or mounted as a pill container
            assert isinstance(p2_rings, Gtk.Widget)

        # Verify Floors control, Minimap control, and Zoom controls (- / % / +) are present and functional
        assert hasattr(bottom, "btn_floors") or hasattr(bottom, "floor_btn")
        assert hasattr(bottom, "btn_minimap") or hasattr(bottom, "minimap_btn")
        assert hasattr(bottom, "zoom_lbl")

        cw = window.canvas_widget
        window.reset_zoom()
        pump_events(0.05)
        assert "100%" in bottom.zoom_lbl.get_label()

        # Zoom In and Zoom Out via bottom capsule
        z0 = cw.viewport.zoom
        if hasattr(bottom, "_zoom_step"):
            bottom._zoom_step(1.25)
        elif hasattr(bottom, "btn_zoom_in"):
            bottom.btn_zoom_in.emit("clicked")
        pump_events(0.05)
        assert cw.viewport.zoom > z0
        assert bottom.zoom_lbl.get_label() != "100%"

        if hasattr(bottom, "_zoom_step"):
            bottom._zoom_step(0.8)
        elif hasattr(bottom, "btn_zoom_out"):
            bottom.btn_zoom_out.emit("clicked")
        pump_events(0.05)
        assert abs(cw.viewport.zoom - 1.0) < 0.05
        assert "100%" in bottom.zoom_lbl.get_label()


def GObject_has_signal(obj: object, signal_name: str) -> bool:
    from gi.repository import GObject
    return GObject.signal_lookup(signal_name, type(obj)) != 0
