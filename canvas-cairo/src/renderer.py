"""
Cairo Canvas Renderer for Maestrix GTK 4 Native.
Renders dot grid, catenary ropes, and node cards with selection and resize handles.
Designed for 60 FPS performance (<16ms frame budget).
"""

import math
from typing import Any, List, Optional, Tuple
import cairo

try:
    from .viewport import Viewport
except ImportError:
    from viewport import Viewport
from maestri_core.models.node import CanvasNode
from maestri_core.models.connection import Connection


# Visual constants
COLOR_BG = (0.11, 0.11, 0.12)             # #1c1c1e canvas background
COLOR_GRID_DOT = (0.24, 0.24, 0.26)       # Subtle grid dots
COLOR_NODE_BG = (0.13, 0.13, 0.14)        # Card body
COLOR_HEADER_BG = (0.17, 0.17, 0.18)      # Header bar
COLOR_BORDER = (0.28, 0.28, 0.30)         # Normal border
COLOR_SELECTED = (0.35, 0.55, 0.95)       # Selection blue border / glow
COLOR_ROPE = (0.75, 0.75, 0.78)           # Connection rope color
COLOR_TEXT_PRIMARY = (0.92, 0.92, 0.94)   # Header text
COLOR_TEXT_MUTED = (0.55, 0.55, 0.58)     # Secondary text
COLOR_PIN = (0.45, 0.65, 1.0)             # Connection pin

GRID_SPACING = 20.0
HANDLE_SIZE = 8.0
CORNER_RADIUS = 10.0


def draw_rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float):
    """Path helper for rounded rectangle."""
    r = min(r, w / 2.0, h / 2.0)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class CairoCanvasRenderer:
    def __init__(self, viewport: Viewport):
        self.viewport = viewport

    def render_background(self, cr: cairo.Context, width: float, height: float):
        """Draw background and dot grid."""
        cr.set_source_rgb(*COLOR_BG)
        cr.paint()
        self._draw_grid(cr, int(width), int(height))

    def render(
        self,
        cr: cairo.Context,
        width: int,
        height: int,
        nodes: List[CanvasNode],
        connections: List[Connection],
        selected_node_id: Any = None,
        hover_pin: Optional[Tuple[str, str]] = None,
        drag_rope_start: Optional[Tuple[float, float]] = None,
        drag_rope_current: Optional[Tuple[float, float]] = None,
    ):
        """Draw complete canvas frame."""
        # 1. Background
        self.render_background(cr, width, height)

        # Normalize selected IDs
        sel_set = set()
        if isinstance(selected_node_id, (set, list, tuple)):
            sel_set = set(selected_node_id)
        elif selected_node_id:
            sel_set = {str(selected_node_id)}

        # 3. Connection Ropes
        node_map = {n.id: n for n in nodes}
        for conn in connections:
            self._draw_connection(cr, conn, node_map)

        # 3b. Interactive Drag Rope (if user is currently pulling a rope)
        if drag_rope_start and drag_rope_current:
            self._draw_live_rope(cr, drag_rope_start, drag_rope_current)

        # 4. Nodes (sorted by z_index)
        sorted_nodes = sorted(nodes, key=lambda n: n.z_index)
        for node in sorted_nodes:
            is_selected = (node.id in sel_set)
            self._draw_node(cr, node, is_selected)

    def _draw_grid(self, cr: cairo.Context, width: int, height: int):
        """Draw subtle dot grid scaled with viewport."""
        zoom = self.viewport.zoom
        spacing = GRID_SPACING * zoom
        if spacing < 8.0:
            return  # Skip grid when zoomed too far out for performance

        start_x = (-self.viewport.origin_x * zoom) % spacing
        start_y = (-self.viewport.origin_y * zoom) % spacing

        cr.set_source_rgb(*COLOR_GRID_DOT)
        x = start_x
        while x < width:
            y = start_y
            while y < height:
                cr.rectangle(x - 0.75, y - 0.75, 1.5, 1.5)
                y += spacing
            x += spacing
        cr.fill()

    def _draw_connection(self, cr: cairo.Context, conn: Connection, node_map: dict):
        """Draw catenary rope connection between two nodes."""
        node_a = node_map.get(conn.a_id)
        node_b = node_map.get(conn.b_id)
        if not node_a or not node_b:
            return

        # Start and end centers in canvas space
        ax = node_a.frame.x + node_a.frame.width
        ay = node_a.frame.y + (node_a.frame.height / 2.0)
        bx = node_b.frame.x
        by = node_b.frame.y + (node_b.frame.height / 2.0)

        # Convert to screen coordinates
        sax, say = self.viewport.canvas_to_screen(ax, ay)
        sbx, sby = self.viewport.canvas_to_screen(bx, by)

        # Draw smooth Bezier catenary sag
        cr.set_source_rgba(COLOR_ROPE[0], COLOR_ROPE[1], COLOR_ROPE[2], 0.85)
        cr.set_line_width(2.5 * max(0.5, self.viewport.zoom))

        mid_x = (sax + sbx) / 2.0
        sag = max(30.0, abs(sbx - sax) * 0.15)
        mid_y = max(say, sby) + sag

        cr.move_to(sax, say)
        cr.curve_to(mid_x, say + (sag * 0.5), mid_x, sby + (sag * 0.5), sbx, sby)
        cr.stroke()

        # Draw end terminal dots
        cr.arc(sax, say, 4.0, 0, 2 * math.pi)
        cr.arc(sbx, sby, 4.0, 0, 2 * math.pi)
        cr.fill()

    def _draw_live_rope(self, cr: cairo.Context, start_screen: Tuple[float, float], end_screen: Tuple[float, float]):
        """Draw interactive rope being dragged by user."""
        sax, say = start_screen
        sbx, sby = end_screen
        cr.set_source_rgba(COLOR_SELECTED[0], COLOR_SELECTED[1], COLOR_SELECTED[2], 0.9)
        cr.set_line_width(2.5)

        mid_x = (sax + sbx) / 2.0
        sag = max(20.0, abs(sbx - sax) * 0.12)
        cr.move_to(sax, say)
        cr.curve_to(mid_x, say + sag, mid_x, sby + sag, sbx, sby)
        cr.stroke()

    def _draw_node(self, cr: cairo.Context, node: CanvasNode, is_selected: bool):
        """Draw a single canvas node card."""
        sx, sy = self.viewport.canvas_to_screen(node.frame.x, node.frame.y)
        sw = node.frame.width * self.viewport.zoom
        sh = node.frame.height * self.viewport.zoom

        # 1. Card Body Fill & Shadow
        draw_rounded_rect(cr, sx, sy, sw, sh, CORNER_RADIUS)
        cr.set_source_rgb(*COLOR_NODE_BG)
        cr.fill()

        # 2. Header Bar
        header_height = 36.0 * self.viewport.zoom
        cr.save()
        draw_rounded_rect(cr, sx, sy, sw, sh, CORNER_RADIUS)
        cr.clip()

        cr.rectangle(sx, sy, sw, header_height)
        cr.set_source_rgb(*COLOR_HEADER_BG)
        cr.fill()

        # Header bottom border line
        cr.set_source_rgb(*COLOR_BORDER)
        cr.set_line_width(1.0)
        cr.move_to(sx, sy + header_height)
        cr.line_to(sx + sw, sy + header_height)
        cr.stroke()

        # Node type icon / status badge
        dot_r = 4.0 * self.viewport.zoom
        dot_x = sx + (14.0 * self.viewport.zoom)
        dot_y = sy + (header_height / 2.0)

        badge_color = (0.2, 0.8, 0.4) if node.node_type == "terminal" else (
            (1.0, 0.8, 0.2) if node.node_type == "stickyNote" else (0.3, 0.6, 1.0)
        )
        cr.set_source_rgb(*badge_color)
        cr.arc(dot_x, dot_y, dot_r, 0, 2 * math.pi)
        cr.fill()

        # Title text
        font_size = max(9.0, 13.0 * self.viewport.zoom)
        cr.set_font_size(font_size)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_source_rgb(*COLOR_TEXT_PRIMARY)
        text_x = dot_x + (12.0 * self.viewport.zoom)
        text_y = sy + (header_height / 2.0) + (font_size * 0.35)
        cr.move_to(text_x, text_y)
        cr.show_text(node.title)

        # Close button (×)
        close_x = sx + sw - (18.0 * self.viewport.zoom)
        close_y = sy + (header_height / 2.0) + (font_size * 0.35)
        cr.set_source_rgb(*COLOR_TEXT_MUTED)
        cr.move_to(close_x, close_y)
        cr.show_text("×")
        cr.restore()

        # 3. Card Content Area Preview
        self._draw_node_preview(cr, node, sx, sy + header_height, sw, sh - header_height)

        # 4. Card Outer Border
        draw_rounded_rect(cr, sx, sy, sw, sh, CORNER_RADIUS)
        if is_selected:
            cr.set_source_rgb(*COLOR_SELECTED)
            cr.set_line_width(2.5)
        else:
            cr.set_source_rgb(*COLOR_BORDER)
            cr.set_line_width(1.0)
        cr.stroke()

        # 5. Connection Pins (left & right centers)
        pin_radius = 5.0
        cr.set_source_rgb(*COLOR_PIN)
        cr.arc(sx, sy + (sh / 2.0), pin_radius, 0, 2 * math.pi)
        cr.fill()
        cr.arc(sx + sw, sy + (sh / 2.0), pin_radius, 0, 2 * math.pi)
        cr.fill()

        # 6. Selection Resize Handles (8 handles)
        if is_selected:
            self._draw_handles(cr, sx, sy, sw, sh)

    def _draw_node_preview(self, cr: cairo.Context, node: CanvasNode, x: float, y: float, w: float, h: float):
        """Draw specific preview content inside node card."""
        cr.save()
        cr.rectangle(x + 10, y + 10, max(0, w - 20), max(0, h - 20))
        cr.clip()

        font_size = max(8.0, 11.0 * self.viewport.zoom)
        cr.set_font_size(font_size)
        cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_source_rgb(*COLOR_TEXT_MUTED)

        if node.node_type == "terminal":
            lines = [
                f"$ {getattr(node.content, 'command', 'bash')}",
                "Maestrix Terminal Session",
                f"Directory: {getattr(node.content, 'working_directory', '~')}",
                "● Process active (PTY streaming)",
            ]
        elif node.node_type == "stickyNote":
            lines = [
                f"# {node.title}",
                "Markdown Sticky Note",
                getattr(node.content, 'file_name', 'Note.md'),
            ]
        elif node.node_type == "portal":
            lines = [
                f"URL: {getattr(node.content, 'current_url', 'https://google.com')}",
                f"Status: {getattr(node.content, 'status', 'idle')}",
                "Sandboxed Web View",
            ]
        elif node.node_type == "fileTree":
            lines = [
                f"Folder: {getattr(node.content, 'root_path', '/')}",
                "📁 Sources/",
                "  📄 main.swift [M]",
                "  📄 App.swift",
            ]
        else:
            lines = [f"{node.node_type.capitalize()} Node"]

        curr_y = y + 20
        for line in lines:
            if curr_y > y + h - 5:
                break
            cr.move_to(x + 15, curr_y)
            cr.show_text(line)
            curr_y += font_size * 1.6

        cr.restore()

    def _draw_handles(self, cr: cairo.Context, sx: float, sy: float, sw: float, sh: float):
        """Draw 8 resize handles around selected node."""
        hs = HANDLE_SIZE
        half = hs / 2.0
        cr.set_source_rgb(*COLOR_SELECTED)

        positions = [
            (sx - half, sy - half),               # Top-left
            (sx + (sw / 2.0) - half, sy - half),  # Top
            (sx + sw - half, sy - half),          # Top-right
            (sx + sw - half, sy + (sh / 2.0) - half),  # Right
            (sx + sw - half, sy + sh - half),     # Bottom-right
            (sx + (sw / 2.0) - half, sy + sh - half),  # Bottom
            (sx - half, sy + sh - half),          # Bottom-left
            (sx - half, sy + (sh / 2.0) - half),  # Left
        ]

        for hx, hy in positions:
            cr.rectangle(hx, hy, hs, hs)
            cr.fill()
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.set_line_width(1.0)
            cr.rectangle(hx, hy, hs, hs)
            cr.stroke()
            cr.set_source_rgb(*COLOR_SELECTED)
