"""
GTK 4 CanvasWidget for Open Maestri Native.
Wraps Gtk.DrawingArea and CairoCanvasRenderer with full mouse and gesture controllers.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib

import cairo
import sys
from pathlib import Path

# Add canvas-cairo and maestri_core to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from viewport import Viewport
from renderer import CairoCanvasRenderer
from interaction import CanvasInteractionHandler
from maestri_core.models.workspace import WorkspacePayload
from maestri_core.models.node import CanvasNode, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent
from maestri_core.models.connection import Connection
from maestri_core.signals.bus import bus


class CanvasWidget(Gtk.DrawingArea):
    def __init__(self, workspace: WorkspacePayload):
        super().__init__()
        self.workspace = workspace

        # Initialize Viewport from workspace origin & zoom
        origin_x = workspace.canvas_origin[0] if len(workspace.canvas_origin) > 0 else 9800.0
        origin_y = workspace.canvas_origin[1] if len(workspace.canvas_origin) > 1 else 8500.0
        self.viewport = Viewport(origin_x=origin_x, origin_y=origin_y, zoom=workspace.canvas_zoom)

        self.renderer = CairoCanvasRenderer(self.viewport)
        self.interaction = CanvasInteractionHandler(self.viewport)

        # Configure DrawingArea
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._on_draw)

        # Setup GTK 4 event controllers
        self._setup_events()

        # Connect bus signals
        bus.connect("node-added", self._on_bus_node_added)
        bus.connect("node-removed", self._on_bus_node_removed)
        bus.connect("workspace-loaded", self._on_bus_workspace_loaded)

    def _setup_events(self):
        # 1. Primary Click Gesture (Selection, pin click, close button)
        click_gesture = Gtk.GestureClick.new()
        click_gesture.set_button(0)  # Listen to all buttons
        click_gesture.connect("pressed", self._on_click_pressed)
        click_gesture.connect("released", self._on_click_released)
        self.add_controller(click_gesture)

        # 2. Drag Gesture (Node movement, pan, resize, rope connection)
        drag_gesture = Gtk.GestureDrag.new()
        drag_gesture.connect("drag-begin", self._on_drag_begin)
        drag_gesture.connect("drag-update", self._on_drag_update)
        drag_gesture.connect("drag-end", self._on_drag_end)
        self.add_controller(drag_gesture)

        # 3. Scroll Controller (Mouse wheel zoom)
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.SMOOTH
        )
        scroll_controller.connect("scroll", self._on_scroll)
        self.add_controller(scroll_controller)

    def _on_draw(self, area, cr: cairo.Context, width: int, height: int):
        """GTK 4 DrawingArea render callback."""
        connections = self.workspace.all_connections()
        self.renderer.render(
            cr=cr,
            width=width,
            height=height,
            nodes=self.workspace.nodes,
            connections=connections,
            selected_node_id=self.interaction.selected_node_id,
            drag_rope_start=self.interaction.drag_rope_start,
            drag_rope_current=self.interaction.drag_rope_current,
        )

    def _on_click_pressed(self, gesture, n_press, x, y):
        self.grab_focus()
        button = gesture.get_current_button()
        if self.interaction.on_mouse_down(x, y, button, self.workspace.nodes):
            self.queue_draw()

    def _on_click_released(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        completed_conn = self.interaction.on_mouse_up(x, y, button, self.workspace.nodes)
        if completed_conn:
            src_id, tgt_id = completed_conn
            new_conn = Connection(a_id=src_id, b_id=tgt_id)
            self.workspace.connections.append(new_conn)
            bus.emit("connection-created", new_conn)
        self.queue_draw()

    def _on_drag_begin(self, gesture, start_x, start_y):
        button = gesture.get_current_button()
        self.interaction.on_mouse_down(start_x, start_y, button, self.workspace.nodes)
        self.queue_draw()

    def _on_drag_update(self, gesture, offset_x, offset_y):
        start_x, start_y = gesture.get_start_point()
        curr_x = start_x + offset_x
        curr_y = start_y + offset_y
        if self.interaction.on_mouse_move(curr_x, curr_y, self.workspace.nodes):
            self.queue_draw()

    def _on_drag_end(self, gesture, offset_x, offset_y):
        start_x, start_y = gesture.get_start_point()
        curr_x = start_x + offset_x
        curr_y = start_y + offset_y
        button = gesture.get_current_button()
        completed_conn = self.interaction.on_mouse_up(curr_x, curr_y, button, self.workspace.nodes)
        if completed_conn:
            src_id, tgt_id = completed_conn
            new_conn = Connection(a_id=src_id, b_id=tgt_id)
            self.workspace.connections.append(new_conn)
            bus.emit("connection-created", new_conn)
        self.queue_draw()

    def _on_scroll(self, controller, dx, dy):
        # Anchor zoom at center of widget
        w = self.get_width() or 1200
        h = self.get_height() or 800
        self.interaction.on_scroll(w / 2.0, h / 2.0, dy)
        self.queue_draw()
        return True

    def reset_zoom(self):
        w = self.get_width() or 1200
        h = self.get_height() or 800
        self.viewport.reset_zoom(w / 2.0, h / 2.0)
        self.queue_draw()

    # Bus signal handlers
    def _on_bus_node_added(self, bus_obj, node):
        if node not in self.workspace.nodes:
            self.workspace.nodes.append(node)
        self.queue_draw()

    def _on_bus_node_removed(self, bus_obj, node_id):
        self.workspace.nodes = [n for n in self.workspace.nodes if n.id != node_id]
        if self.interaction.selected_node_id == node_id:
            self.interaction.selected_node_id = None
        self.queue_draw()

    def _on_bus_workspace_loaded(self, bus_obj, new_workspace):
        self.workspace = new_workspace
        if len(new_workspace.canvas_origin) >= 2:
            self.viewport.origin_x = new_workspace.canvas_origin[0]
            self.viewport.origin_y = new_workspace.canvas_origin[1]
        self.viewport.zoom = new_workspace.canvas_zoom
        self.queue_draw()
