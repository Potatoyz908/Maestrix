"""
Canvas Interaction Handler for Maestrix GTK 4 Native.
Handles hit testing, selection, drag & drop, resize handles, pan, and zoom.
"""

from typing import List, Optional, Tuple
try:
    from .viewport import Viewport
except ImportError:
    from viewport import Viewport
from maestri_core.models.node import CanvasNode
from maestri_core.models.connection import Connection
from maestri_core.signals.bus import bus

SNAP_GRID = 20.0
HANDLE_RADIUS = 10.0
PIN_RADIUS = 12.0


class CanvasInteractionHandler:
    def __init__(self, viewport: Viewport):
        self.viewport = viewport
        self.selected_node_id: Optional[str] = None

        # Drag state
        self.is_dragging_node = False
        self.drag_node_id: Optional[str] = None
        self.drag_offset_x = 0.0
        self.drag_offset_y = 0.0

        # Resize state
        self.is_resizing = False
        self.resize_handle: Optional[str] = None
        self.resize_initial_frame = None

        # Pan state
        self.is_panning = False
        self.last_pan_x = 0.0
        self.last_pan_y = 0.0

        # Connection rope drag state
        self.is_connecting = False
        self.connect_source_node_id: Optional[str] = None
        self.drag_rope_start: Optional[Tuple[float, float]] = None
        self.drag_rope_current: Optional[Tuple[float, float]] = None

    def hit_test_nodes(self, nodes: List[CanvasNode], screen_x: float, screen_y: float) -> Optional[str]:
        """Alias returning node id for test compatibility."""
        node = self.hit_test_node(screen_x, screen_y, nodes)
        return node.id if node else None

    def hit_test_handles(self, node: CanvasNode, screen_x: float, screen_y: float) -> Optional[str]:
        """Alias for hit_test_handle."""
        return self.hit_test_handle(screen_x, screen_y, node)

    def handle_mouse_press(self, screen_x: float, screen_y: float, nodes: List[CanvasNode], button: int = 1) -> bool:
        """Alias for on_mouse_down."""
        return self.on_mouse_down(screen_x, screen_y, button, nodes)

    def handle_mouse_move(self, screen_x: float, screen_y: float, nodes: List[CanvasNode]) -> bool:
        """Alias for on_mouse_move."""
        return self.on_mouse_move(screen_x, screen_y, nodes)

    def handle_mouse_release(self, screen_x: float, screen_y: float, nodes: List[CanvasNode], button: int = 1):
        """Alias for on_mouse_up."""
        return self.on_mouse_up(screen_x, screen_y, button, nodes)

    @property
    def selected_ids(self) -> set:
        return {self.selected_node_id} if self.selected_node_id else set()

    def hit_test_handle(self, screen_x: float, screen_y: float, node: CanvasNode) -> Optional[str]:
        """Check if screen coordinate hits any of the 8 resize handles on the selected node."""
        sx, sy = self.viewport.canvas_to_screen(node.frame.x, node.frame.y)
        sw = node.frame.width * self.viewport.zoom
        sh = node.frame.height * self.viewport.zoom

        handles = {
            "nw": (sx, sy),
            "n": (sx + sw / 2.0, sy),
            "ne": (sx + sw, sy),
            "e": (sx + sw, sy + sh / 2.0),
            "se": (sx + sw, sy + sh),
            "s": (sx + sw / 2.0, sy + sh),
            "sw": (sx, sy + sh),
            "w": (sx, sy + sh / 2.0),
            "top-left": (sx, sy),
            "top": (sx + sw / 2.0, sy),
            "top-right": (sx + sw, sy),
            "right": (sx + sw, sy + sh / 2.0),
            "bottom-right": (sx + sw, sy + sh),
            "bottom": (sx + sw / 2.0, sy + sh),
            "bottom-left": (sx, sy + sh),
            "left": (sx, sy + sh / 2.0),
        }

        # Check compass keys first
        compass_keys = ["se", "nw", "ne", "sw", "e", "w", "s", "n"]
        for handle_name in compass_keys:
            hx, hy = handles[handle_name]
            if abs(screen_x - hx) <= HANDLE_RADIUS and abs(screen_y - hy) <= HANDLE_RADIUS:
                return handle_name
        return None

    def hit_test_pin(self, screen_x: float, screen_y: float, node: CanvasNode) -> Optional[str]:
        """Check if screen coordinate hits connection pins (left or right)."""
        sx, sy = self.viewport.canvas_to_screen(node.frame.x, node.frame.y)
        sw = node.frame.width * self.viewport.zoom
        sh = node.frame.height * self.viewport.zoom

        left_pin = (sx, sy + sh / 2.0)
        right_pin = (sx + sw, sy + sh / 2.0)

        if math_dist(screen_x, screen_y, left_pin[0], left_pin[1]) <= PIN_RADIUS:
            return "left"
        if math_dist(screen_x, screen_y, right_pin[0], right_pin[1]) <= PIN_RADIUS:
            return "right"
        return None

    def hit_test_node(self, screen_x: float, screen_y: float, nodes: List[CanvasNode]) -> Optional[CanvasNode]:
        """Check if screen coordinate is inside any node card (topmost z-index first)."""
        cx, cy = self.viewport.screen_to_canvas(screen_x, screen_y)
        for node in reversed(sorted(nodes, key=lambda n: n.z_index)):
            if node.frame.contains(cx, cy):
                return node
        return None

    def hit_test_close_button(self, screen_x: float, screen_y: float, node: CanvasNode) -> bool:
        """Check if close button in header was clicked."""
        sx, sy = self.viewport.canvas_to_screen(node.frame.x, node.frame.y)
        sw = node.frame.width * self.viewport.zoom
        header_h = 36.0 * self.viewport.zoom
        close_x = sx + sw - (18.0 * self.viewport.zoom)
        close_y = sy + (header_h / 2.0)
        return abs(screen_x - close_x) <= 14.0 and abs(screen_y - close_y) <= 14.0

    def on_mouse_down(self, screen_x: float, screen_y: float, button: int, nodes: List[CanvasNode]) -> bool:
        """Handle mouse button press."""
        # Middle click -> Start Pan
        if button == 2:
            self.is_panning = True
            self.last_pan_x = screen_x
            self.last_pan_y = screen_y
            return True

        if button == 1:
            # 1. Check resize handle on selected node
            if self.selected_node_id:
                sel_node = next((n for n in nodes if n.id == self.selected_node_id), None)
                if sel_node:
                    handle = self.hit_test_handle(screen_x, screen_y, sel_node)
                    if handle:
                        self.is_resizing = True
                        self.resize_handle = handle
                        self.resize_initial_frame = (
                            sel_node.frame.x, sel_node.frame.y, sel_node.frame.width, sel_node.frame.height
                        )
                        return True

            # 2. Check connection pins
            for node in nodes:
                pin = self.hit_test_pin(screen_x, screen_y, node)
                if pin:
                    self.is_connecting = True
                    self.connect_source_node_id = node.id
                    self.drag_rope_start = (screen_x, screen_y)
                    self.drag_rope_current = (screen_x, screen_y)
                    return True

            # 3. Check node hit
            hit_node = self.hit_test_node(screen_x, screen_y, nodes)
            if hit_node:
                # Check close button
                if self.hit_test_close_button(screen_x, screen_y, hit_node):
                    bus.emit("node-removed", hit_node.id)
                    return True

                self.selected_node_id = hit_node.id
                bus.emit("node-selected", hit_node.id)

                # Bring to top z-index
                max_z = max((n.z_index for n in nodes), default=0)
                hit_node.z_index = max_z + 1

                # Start node drag
                self.is_dragging_node = True
                self.drag_node_id = hit_node.id
                cx, cy = self.viewport.screen_to_canvas(screen_x, screen_y)
                self.drag_offset_x = cx - hit_node.frame.x
                self.drag_offset_y = cy - hit_node.frame.y
                return True
            else:
                # Deselect
                self.selected_node_id = None
                bus.emit("node-selected", "")
                return True

        return False

    def on_mouse_move(self, screen_x: float, screen_y: float, nodes: List[CanvasNode]) -> bool:
        """Handle mouse movement / dragging."""
        needs_redraw = False

        if self.is_panning:
            dx = screen_x - self.last_pan_x
            dy = screen_y - self.last_pan_y
            self.viewport.pan(dx, dy)
            self.last_pan_x = screen_x
            self.last_pan_y = screen_y
            bus.emit("viewport-changed", self.viewport.origin_x, self.viewport.origin_y, self.viewport.zoom)
            needs_redraw = True

        elif self.is_connecting:
            self.drag_rope_current = (screen_x, screen_y)
            needs_redraw = True

        elif self.is_dragging_node and self.drag_node_id:
            node = next((n for n in nodes if n.id == self.drag_node_id), None)
            if node:
                cx, cy = self.viewport.screen_to_canvas(screen_x, screen_y)
                raw_x = cx - self.drag_offset_x
                raw_y = cy - self.drag_offset_y

                # Magnetic grid snapping
                snapped_x = round(raw_x / SNAP_GRID) * SNAP_GRID
                snapped_y = round(raw_y / SNAP_GRID) * SNAP_GRID

                node.frame.x = snapped_x
                node.frame.y = snapped_y
                bus.emit("node-moved", node.id, snapped_x, snapped_y)
                needs_redraw = True

        elif self.is_resizing and self.selected_node_id and self.resize_handle and self.resize_initial_frame:
            node = next((n for n in nodes if n.id == self.selected_node_id), None)
            if node:
                cx, cy = self.viewport.screen_to_canvas(screen_x, screen_y)
                ix, iy, iw, ih = self.resize_initial_frame
                h = self.resize_handle

                # Calculate new bounds based on handle
                if "right" in h:
                    node.frame.width = max(200.0, cx - ix)
                if "bottom" in h:
                    node.frame.height = max(150.0, cy - iy)
                if "left" in h:
                    new_w = max(200.0, (ix + iw) - cx)
                    node.frame.x = (ix + iw) - new_w
                    node.frame.width = new_w
                if "top" in h:
                    new_h = max(150.0, (iy + ih) - cy)
                    node.frame.y = (iy + ih) - new_h
                    node.frame.height = new_h

                bus.emit("node-resized", node.id, node.frame.x, node.frame.y, node.frame.width, node.frame.height)
                needs_redraw = True

        return needs_redraw

    def on_mouse_up(self, screen_x: float, screen_y: float, button: int, nodes: List[CanvasNode]) -> Optional[Tuple[str, str]]:
        """Handle mouse release. Returns (node_a_id, node_b_id) if connection was completed."""
        result_connection = None

        if self.is_connecting and self.connect_source_node_id:
            # Check if dropped onto another node
            target_node = self.hit_test_node(screen_x, screen_y, nodes)
            if target_node and target_node.id != self.connect_source_node_id:
                result_connection = (self.connect_source_node_id, target_node.id)

        self.is_panning = False
        self.is_dragging_node = False
        self.drag_node_id = None
        self.is_resizing = False
        self.resize_handle = None
        self.resize_initial_frame = None
        self.is_connecting = False
        self.connect_source_node_id = None
        self.drag_rope_start = None
        self.drag_rope_current = None

        return result_connection

    def on_scroll(self, screen_x: float, screen_y: float, delta_y: float) -> bool:
        """Handle mouse wheel zoom."""
        factor = 1.15 if delta_y < 0 else 0.85
        self.viewport.zoom_at(factor, screen_x, screen_y)
        bus.emit("viewport-changed", self.viewport.origin_x, self.viewport.origin_y, self.viewport.zoom)
        return True


def math_dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
