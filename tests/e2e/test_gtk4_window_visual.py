"""
End-to-End Visual & Interaction Test Suite for Open Maestri GTK 4 under Xvfb.

Tests:
1. Window Visual Hierarchy & Layout (HeaderBar, MenuBar, Sidebar, CanvasWidget).
2. Node Creation & Visual Placement for all 4 kinds (Terminal, Sticky Note, Portal, FileTree).
3. Visual Drag & Resize Gestures on Canvas.
4. Window Action Dispatch & Shortcuts (Sidebar toggle, Reset zoom, Preferences, Save).
5. Visual Node Deletion via Close Button.
6. Visual Snapshot & Cairo Rendering Verification.
"""

import os
import sys
import time
from pathlib import Path
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gio  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import Mods, NodeHeader, NodeEdge  # noqa: E402
from maestri_canvas.resize import Edge  # noqa: E402
from maestri_canvas.demo import screenshot  # noqa: E402
from maestri_canvas.sticky_note import StickyNoteView  # noqa: E402
from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def pump_events(seconds: float = 0.2, interval: float = 0.005) -> None:
    """Pump GLib MainContext events for the specified duration."""
    ctx = GLib.MainContext.default()
    end_time = time.time() + seconds
    while time.time() < end_time:
        ctx.iteration(False)
        time.sleep(interval)


@pytest.fixture
def artifacts_dir() -> Path:
    """Ensure artifacts directory exists for visual regression screenshots."""
    out_dir = ROOT_DIR / "artifacts" / "e2e-screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


@pytest.fixture(scope="session")
def maestri_app():
    """Create and register a shared session-scoped Gtk.Application instance."""
    app = Gtk.Application(
        application_id="org.openmaestri.e2etest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    yield app


@pytest.fixture
def window(maestri_app):
    """Instantiate and present an OpenMaestriWindow under test."""
    client = MaestriClient(port=0)  # offline standalone mode
    win = OpenMaestriWindow(maestri_app, client)
    win.present()
    pump_events(0.3)
    yield win
    win.close()
    pump_events(0.05)


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestGTK4WindowVisualE2E:
    """Comprehensive visual and interaction E2E tests for the GTK 4 window."""

    def test_window_visual_hierarchy_and_layout(self, window, artifacts_dir):
        """Validates that the top-level window initializes with correct hierarchy and renders visually."""
        # 1. Geometry verification
        assert window.get_width() >= 1200
        assert window.get_height() >= 700
        assert "Open Maestri" in window.get_title()

        # 2. Layout structure verification
        paned = window.paned
        assert isinstance(paned, Gtk.Paned)
        assert paned.get_orientation() == Gtk.Orientation.HORIZONTAL

        sidebar = window.sidebar
        assert sidebar is not None
        assert sidebar.get_visible() is True

        canvas_widget = window.canvas_widget
        assert canvas_widget is not None
        assert canvas_widget.get_hexpand() is True
        assert canvas_widget.get_vexpand() is True

        # 3. Initial scene nodes (default workspace provides 2 demo terminals)
        assert len(window.document.payload.nodes) >= 2
        assert len(canvas_widget.scene.nodes) >= 2

        # 4. Capture visual snapshot of the canvas widget
        shot_path = str(artifacts_dir / "01_window_layout_initial.png")
        screenshot(canvas_widget, shot_path)
        assert os.path.isfile(shot_path), "Screenshot file must exist"
        assert os.path.getsize(shot_path) > 1000, "Screenshot should not be empty"

        # Verify Cairo can decode the snapshot and verify dimensions
        import cairo
        surf = cairo.ImageSurface.create_from_png(shot_path)
        assert surf.get_width() > 800
        assert surf.get_height() > 500

    def test_node_creation_lifecycle_visual(self, window, artifacts_dir):
        """Verifies creating nodes for all 4 types (terminal, note, portal, fileTree) updates scene and visual state."""
        cw = window.canvas_widget
        initial_doc_nodes = len(window.document.payload.nodes)
        initial_scene_nodes = len(cw.scene.nodes)

        # 1. Add Terminal
        window._create_node("terminal")
        pump_events(0.15)
        assert len(window.document.payload.nodes) == initial_doc_nodes + 1
        assert len(cw.scene.nodes) == initial_scene_nodes + 1
        term_node = window.document.payload.nodes[-1]
        assert term_node.node_type == "terminal"
        assert term_node.id in cw.frames

        # 2. Add Sticky Note
        window._create_node("stickyNote")
        pump_events(0.15)
        assert len(window.document.payload.nodes) == initial_doc_nodes + 2
        note_node = window.document.payload.nodes[-1]
        assert note_node.node_type == "stickyNote"
        assert note_node.id in cw.frames
        assert isinstance(cw.frames[note_node.id].content, StickyNoteView)

        # 3. Add Portal
        window._create_node("portal")
        pump_events(0.15)
        assert len(window.document.payload.nodes) == initial_doc_nodes + 3
        portal_node = window.document.payload.nodes[-1]
        assert portal_node.node_type == "portal"
        assert portal_node.id in cw.frames

        # 4. Add FileTree
        window._create_node("fileTree")
        pump_events(0.15)
        assert len(window.document.payload.nodes) == initial_doc_nodes + 4
        tree_node = window.document.payload.nodes[-1]
        assert tree_node.node_type == "fileTree"
        assert tree_node.id in cw.frames
        assert isinstance(cw.frames[tree_node.id].content, FileTreeView)

        # 5. Capture visual snapshot with all 4 newly created nodes
        shot_path = str(artifacts_dir / "02_canvas_with_all_node_types.png")
        screenshot(cw, shot_path)
        assert os.path.isfile(shot_path)
        assert os.path.getsize(shot_path) > 1000

    def test_node_drag_interaction(self, window):
        """Simulates dragging a node via its header and verifies coordinate updates on scene and document."""
        cw = window.canvas_widget
        first_id = list(cw.scene.nodes.keys())[0]
        orig_frame = cw.scene.nodes[first_id].frame

        # Compute screen bounds of the node
        ok, bounds = cw.frames[first_id].compute_bounds(cw)
        assert ok, "Frame bounds must compute successfully"

        # Click inside the header bar (middle of 28px header height)
        start_pt = Point(bounds.get_x() + 50, bounds.get_y() + 14)
        target = cw.target_at(start_pt.x, start_pt.y)
        assert isinstance(target, NodeHeader), f"Expected NodeHeader target, got {target}"
        assert target.id == first_id

        # Drag gesture
        dx, dy = 120.0, 60.0
        cw.controller.press(start_pt, 1, Mods.NONE, target)
        cw.controller.move(Point(start_pt.x + dx, start_pt.y + dy), Mods.NONE)
        cw.controller.release(Point(start_pt.x + dx, start_pt.y + dy), Mods.NONE)
        pump_events(0.2)

        # Verify world coordinates changed
        new_frame = cw.scene.nodes[first_id].frame
        assert new_frame.x != orig_frame.x or new_frame.y != orig_frame.y

        # Verify underlying document updated
        doc_node = window._find_node(first_id)
        assert doc_node is not None
        assert doc_node.frame.x == new_frame.x
        assert doc_node.frame.y == new_frame.y

    def test_node_resize_interaction(self, window):
        """Simulates dragging the bottom-right resize handle and verifies dimensions change."""
        cw = window.canvas_widget
        first_id = list(cw.scene.nodes.keys())[0]
        orig_frame = cw.scene.nodes[first_id].frame

        ok, bounds = cw.frames[first_id].compute_bounds(cw)
        assert ok

        # Target corner handle
        start_pt = Point(bounds.get_x() + bounds.get_width() - 2, bounds.get_y() + bounds.get_height() - 2)
        target = cw.target_at(start_pt.x, start_pt.y)
        assert isinstance(target, NodeEdge), f"Expected NodeEdge target, got {target}"
        assert target.edge == Edge.BOTTOM_RIGHT

        # Drag resize handle
        cw.controller.press(start_pt, 1, Mods.NONE, target)
        cw.controller.move(Point(start_pt.x + 80, start_pt.y + 50), Mods.NONE)
        cw.controller.release(Point(start_pt.x + 80, start_pt.y + 50), Mods.NONE)
        pump_events(0.2)

        new_frame = cw.scene.nodes[first_id].frame
        assert new_frame.w > orig_frame.w, f"Width should have increased from {orig_frame.w}"
        assert new_frame.h > orig_frame.h, f"Height should have increased from {orig_frame.h}"

    def test_node_close_and_deletion(self, window):
        """Tests that clicking a node's close button cleanly removes it from scene and document without error."""
        cw = window.canvas_widget
        first_id = list(cw.scene.nodes.keys())[0]

        frame = cw.frames[first_id]
        header = frame.get_first_child().get_first_child()  # FrameBox -> HeaderBox
        close_btn = header.get_last_child()
        assert isinstance(close_btn, Gtk.Button)

        # Trigger deletion via close button
        close_btn.emit("clicked")
        pump_events(0.2)

        # Verify removal
        assert first_id not in cw.scene.nodes
        assert first_id not in cw.frames
        assert window._find_node(first_id) is None

    def test_menu_actions_and_shortcuts(self, window):
        """Tests application actions: sidebar toggle, zoom reset, preferences dialog, and sync save."""
        # 1. Sidebar Toggle
        assert window.sidebar.get_visible() is True
        window._on_toggle_sidebar(None)
        pump_events(0.05)
        assert window.sidebar.get_visible() is False
        window._on_toggle_sidebar(None)
        pump_events(0.05)
        assert window.sidebar.get_visible() is True

        # 2. Zoom Reset
        window.canvas_widget.viewport.zoom = 2.0
        assert window.canvas_widget.viewport.zoom == 2.0
        window.reset_zoom()
        pump_events(0.05)
        assert window.canvas_widget.viewport.zoom == 1.0

        # 3. Preferences Window
        window.show_preferences()
        pump_events(0.15)
        assert window.settings_window is not None
        assert window.settings_window.get_visible() is True
        window.settings_window.close()
        pump_events(0.05)
        assert window.settings_window is None

        # 4. Save Flush
        window._save_to_backend()
        pump_events(0.05)

    def test_window_resizing_visual_stability(self, window, artifacts_dir):
        """Tests that resizing the top-level window preserves canvas layout and produces valid visual snapshots."""
        cw = window.canvas_widget

        # Resize to 1600x900
        window.set_default_size(1600, 900)
        pump_events(0.25)
        assert cw.get_width() > 1000

        # Snapshot at 1600x900
        shot_path = str(artifacts_dir / "03_canvas_resized_1600x900.png")
        screenshot(cw, shot_path)
        assert os.path.isfile(shot_path)
        assert os.path.getsize(shot_path) > 1000

        # Resize back to 1280x800
        window.set_default_size(1280, 800)
        pump_events(0.25)

    def test_sticky_note_real_widget_rendering_and_interaction(self, window, artifacts_dir):
        """Validates real StickyNoteView widget rendering: Markdown editor, Raw/Formatted modes, live color styling, and Cairo snapshot."""
        cw = window.canvas_widget

        # 1. Create a real Sticky Note
        window._create_node("stickyNote")
        pump_events(0.25)
        note_node = [n for n in window.document.payload.nodes if n.node_type == "stickyNote"][-1]
        note_frame = cw.frames[note_node.id]
        note_view = note_frame.content

        # 2. Verify instance and structure
        assert isinstance(note_view, StickyNoteView)
        assert note_frame.has_css_class("maestri-note")
        assert note_view.raw_btn is not None
        assert note_view.fmt_btn is not None
        assert note_view.title_label is not None
        assert note_view.menu_btn is not None

        # 3. Test Markdown editing
        test_markdown = "# Sprint 3 Real Widget E2E\n\n- [x] StickyNoteView native\n- [x] FileTreeView native\n- [ ] Portal next #e2e #linux"
        note_view._set_text(test_markdown)
        pump_events(0.2)
        assert "Sprint 3 Real Widget E2E" in note_view.text()

        # 4. Test Mode Toggling (Raw <-> Formatted Preview)
        assert note_view.raw_btn.get_active() is True
        assert note_view.stack.get_visible_child_name() == "raw"

        note_view.set_previewing(True)
        pump_events(0.15)
        assert note_view.fmt_btn.get_active() is True
        assert note_view.stack.get_visible_child_name() == "formatted"

        note_view.set_previewing(False)
        pump_events(0.15)
        assert note_view.raw_btn.get_active() is True
        assert note_view.stack.get_visible_child_name() == "raw"

        # 5. Test Live Color Switching
        note_view.set_color("teal")
        pump_events(0.15)
        assert note_view.meta.color == "teal"

        # 6. Test Font Size Adjustment
        note_view.set_font_size(16)
        pump_events(0.1)
        assert note_view.meta.font_size == 16

        # 7. Visual Snapshot of the real Sticky Note on Canvas
        shot_path = str(artifacts_dir / "04_sticky_note_real_widget.png")
        screenshot(cw, shot_path)
        assert os.path.isfile(shot_path)
        assert os.path.getsize(shot_path) > 1000

        import cairo
        surf = cairo.ImageSurface.create_from_png(shot_path)
        assert surf.get_width() > 800
        assert surf.get_height() > 500

    def test_file_tree_real_widget_rendering_and_interaction(self, window, artifacts_dir):
        """Validates real FileTreeView widget rendering: Navigation toolbar, lazy DirectoryList, Gtk.ListView, and Cairo snapshot."""
        cw = window.canvas_widget

        # 1. Create a real FileTree node
        window._create_node("fileTree")
        pump_events(0.25)
        tree_node = [n for n in window.document.payload.nodes if n.node_type == "fileTree"][-1]
        tree_frame = cw.frames[tree_node.id]
        tree_view = tree_frame.content

        # 2. Verify instance and toolbar hierarchy
        assert isinstance(tree_view, FileTreeView)
        assert tree_view.has_css_class("maestri-file-tree")
        assert tree_view.btn_back is not None
        assert tree_view.btn_forward is not None
        assert tree_view.btn_up is not None
        assert tree_view.path_label is not None
        assert tree_view.btn_hidden is not None
        assert tree_view.list_view is not None
        assert isinstance(tree_view.list_view, Gtk.ListView)
        assert isinstance(tree_view.selection, Gtk.SingleSelection)

        # 3. Test Re-rooting to repo root directory
        tree_view.set_root(str(ROOT_DIR))
        pump_events(0.3)
        assert tree_view.root == str(ROOT_DIR)

        # 4. Test Toggling Show Hidden Files
        assert tree_view.btn_hidden.get_active() is False
        tree_view.set_show_hidden(True)
        pump_events(0.15)
        assert tree_view.btn_hidden.get_active() is True
        tree_view.set_show_hidden(False)
        pump_events(0.15)
        assert tree_view.btn_hidden.get_active() is False

        # 5. Test Navigation Up and Back
        tree_view.go_up()
        pump_events(0.2)
        assert tree_view.root != str(ROOT_DIR)

        tree_view.go_back()
        pump_events(0.2)
        assert tree_view.root == str(ROOT_DIR)

        # 6. Visual Snapshot of the real FileTree on Canvas
        shot_path = str(artifacts_dir / "05_file_tree_real_widget.png")
        screenshot(cw, shot_path)
        assert os.path.isfile(shot_path)
        assert os.path.getsize(shot_path) > 1000

        import cairo
        surf = cairo.ImageSurface.create_from_png(shot_path)
        assert surf.get_width() > 800
        assert surf.get_height() > 500

