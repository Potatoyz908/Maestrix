"""Pure pycairo renderer: draws onto ANY cairo.Context (an ImageSurface owned by the widget, or a test surface). No GTK imports.

Layers, bottom to top: background + dot grid, ropes, canvas-drawn nodes (text/shape/freehand via drawn.py; stroke is still a
placeholder), then gesture overlays (snap guides, marquee, rope being dragged, draw-to-create rect or freehand stroke, Alt-drag ghosts). Widget nodes (terminal/note/portal/fileTree)
are real GTK widgets stacked above this layer, so they are NOT drawn here unless `placeholders=True` (headless tests/screenshots).
"""
from __future__ import annotations

import math

import cairo

from .drawn import DRAWN_KINDS, draw_freehand_preview, draw_node
from .geometry import Point, Rect
from .interaction import Overlay
from .rope import RopeSimulation, static_catenary
from .scene import WIDGET_KINDS, ConnItem, Scene, pin_point
from .viewport import Viewport

BG = (1.0, 1.0, 1.0)              # CanvasBackground "dotGrid": white under the line grid
SOLID_BG = (0.98, 0.98, 0.98)     # CanvasBackground "solid": NSColor(white: 0.98)
GRID = (0.90, 0.90, 0.90)         # Constants.canvasGridLineColor (#E6E6E6)
GRID_LINE_W = 0.5                 # Constants.canvasGridLineWidth
ROPE = (0.54, 0.54, 0.56)
ACCENT = (0.231, 0.510, 0.965)
GUIDE = (0.937, 0.302, 0.459)   # alignment guides: rose, distinct from the blue selection / marquee
GUIDE_ALPHA = 0.75
GUIDE_MARK_PX = 2.5
KIND_FILL = {"terminal": (0.12, 0.12, 0.12), "stickyNote": (1.0, 0.953, 0.639), "portal": (1.0, 1.0, 1.0), "fileTree": (0.98, 0.98, 0.99)}
GRID_STEP = 16.0                  # Constants.canvasGridSpacing
BACKGROUNDS = ("dotGrid", "solid", "transparent")   # preferences.json `canvasBackground`
MIN_GRID_PX = 4.0                 # below 4 px on screen macOS skips the grid


def _polyline_smooth(cr: cairo.Context, pts: list[Point]) -> None:
    """Quadratic curve through segment midpoints: removes the visible facets of a 21-point polyline."""
    cr.move_to(pts[0].x, pts[0].y)
    for i in range(1, len(pts) - 1):
        mx, my = (pts[i].x + pts[i + 1].x) / 2, (pts[i].y + pts[i + 1].y) / 2
        # Cairo has only cubic curves: lift the quadratic (cur -> ctrl pts[i] -> mid) to a cubic.
        cx, cy = cr.get_current_point()
        cr.curve_to(cx + 2 / 3 * (pts[i].x - cx), cy + 2 / 3 * (pts[i].y - cy), mx + 2 / 3 * (pts[i].x - mx), my + 2 / 3 * (pts[i].y - my), mx, my)
    cr.line_to(pts[-1].x, pts[-1].y)


class CairoRenderer:
    def __init__(self, placeholders: bool = False, background: str = "dotGrid") -> None:
        self.placeholders = placeholders
        self.background = background if background in BACKGROUNDS else "dotGrid"

    def draw(self, cr: cairo.Context, scene: Scene, vp: Viewport, width: float, height: float,
             ropes: RopeSimulation | None = None, overlay: Overlay | None = None) -> None:
        """Base layer (+ the overlay when given, for headless use). The widget passes overlay=None and draws it on a top layer."""
        if self.background != "transparent":   # transparent: the window background shows through
            cr.set_source_rgb(*(SOLID_BG if self.background == "solid" else BG))
            cr.paint()
        if self.background == "dotGrid":
            self._grid(cr, vp, width, height)
        cr.save()
        # Everything below is in canvas units: one transform instead of converting every coordinate.
        cr.scale(vp.zoom, vp.zoom)
        cr.translate(-vp.origin.x, -vp.origin.y)
        self._ropes(cr, scene, vp, ropes)
        self._nodes(cr, scene, vp.visible_rect(width, height), vp)
        if overlay is not None:
            self._overlay(cr, scene, vp, overlay)
        cr.restore()

    def draw_overlay(self, cr: cairo.Context, scene: Scene, vp: Viewport, overlay: Overlay) -> None:
        """Transient gesture visuals on a TRANSPARENT surface, meant to sit above the node widgets (a dragged rope must
        stay visible even where it crosses a node)."""
        cr.save()
        cr.scale(vp.zoom, vp.zoom)
        cr.translate(-vp.origin.x, -vp.origin.y)
        self._overlay(cr, scene, vp, overlay)
        cr.restore()

    # ---- layers --------------------------------------------------------------------------------
    def _grid(self, cr: cairo.Context, vp: Viewport, w: float, h: float) -> None:
        """macOS line grid (CanvasBackground.makeGridPattern): a 0.5 px #E6E6E6 line every 16 canvas units, both axes; skipped
        when the spacing is under 4 screen px. Lines sit on pixel centres so they stay crisp at 1x."""
        step = GRID_STEP * vp.zoom
        if step < MIN_GRID_PX:
            return
        cr.save()
        cr.set_source_rgb(*GRID)
        cr.set_line_width(GRID_LINE_W)
        x = -((vp.origin.x * vp.zoom) % step)
        while x <= w:
            px = math.floor(x) + 0.5
            cr.move_to(px, 0); cr.line_to(px, h)
            x += step
        y = -((vp.origin.y * vp.zoom) % step)
        while y <= h:
            py = math.floor(y) + 0.5
            cr.move_to(0, py); cr.line_to(w, py)
            y += step
        cr.stroke()
        cr.restore()

    def _ropes(self, cr: cairo.Context, scene: Scene, vp: Viewport, ropes: RopeSimulation | None) -> None:
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for c in scene.connections.values():
            pts = (ropes.points(c.id) if ropes else None)
            if pts is None:
                a = scene.anchors(c)
                if a is None:
                    continue
                pts = static_catenary(*a)
            if len(pts) < 2:
                continue
            hot = c.a in scene.selection or c.b in scene.selection
            cr.set_source_rgb(*(ACCENT if hot else ROPE))
            cr.set_line_width(2.5 / vp.zoom)   # constant on screen
            cr.set_dash([6 / vp.zoom, 6 / vp.zoom] if c.pending else [])
            _polyline_smooth(cr, pts)
            cr.stroke()
        cr.set_dash([])

    def _nodes(self, cr: cairo.Context, scene: Scene, view: Rect, vp: Viewport) -> None:
        for n in scene.visible_nodes(view):   # culled to view + margin, ascending z
            if n.kind in WIDGET_KINDS and not self.placeholders:
                continue
            f = n.frame
            if n.kind in DRAWN_KINDS and draw_node(cr, n.kind, n.content, f):
                if n.id in scene.selection:   # drawn nodes have no NodeFrame: the selection outline is painted here
                    self._dashed_rect(cr, f.inset(-3, -3), vp)
                elif n.kind == "text" and not (n.content or {}).get("text"):
                    self._empty_text_hint(cr, f, vp)
                continue
            fill = KIND_FILL.get(n.kind, (0.23, 0.23, 0.23))
            cr.rectangle(f.x, f.y, f.w, f.h)
            cr.set_source_rgb(*fill)
            cr.fill_preserve()
            cr.set_source_rgb(0.33, 0.33, 0.33)
            cr.set_line_width(1 / vp.zoom)
            cr.stroke()
            if self.placeholders and n.title:
                lum = 0.2126 * fill[0] + 0.7152 * fill[1] + 0.0722 * fill[2]
                cr.set_source_rgb(*((0.1, 0.1, 0.1) if lum > 0.5 else (0.92, 0.92, 0.92)))  # readable on any fill
                cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(13)
                cr.move_to(f.x + 10, f.y + 20)
                cr.show_text(n.title)
            if n.id in scene.selection and self.placeholders:
                self._dashed_rect(cr, f.inset(-3, -3), vp)

    @staticmethod
    def _empty_text_hint(cr: cairo.Context, f: Rect, vp: Viewport) -> None:
        """An empty text label would be invisible (and unfindable): draw a faint box until it gets text."""
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
        cr.set_line_width(1 / vp.zoom)
        cr.set_dash([3 / vp.zoom, 3 / vp.zoom])
        cr.rectangle(f.x, f.y, f.w, f.h)
        cr.stroke()
        cr.set_dash([])

    @staticmethod
    def _dashed_rect(cr: cairo.Context, r: Rect, vp: Viewport) -> None:
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(1.5 / vp.zoom)
        cr.set_dash([5 / vp.zoom, 4 / vp.zoom])
        cr.rectangle(r.x, r.y, r.w, r.h)
        cr.stroke()
        cr.set_dash([])

    @staticmethod
    def _guides(cr: cairo.Context, guides, vp: Viewport) -> None:
        """Alignment guides: a hairline (1 screen px) in a muted rose, solid for edges, dashed for centers, with a small cross
        on each aligned anchor. Kept translucent so they read as a hint, not as content."""
        if not guides:
            return
        z = vp.zoom
        cr.save()
        cr.set_line_width(1 / z)
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        arm = GUIDE_MARK_PX / z
        for g in guides:
            cr.set_source_rgba(*GUIDE, GUIDE_ALPHA)
            cr.set_dash([4 / z, 3 / z] if g.kind == "center" else [])
            if g.axis == "vertical":
                cr.move_to(g.position, g.start); cr.line_to(g.position, g.end)
            else:
                cr.move_to(g.start, g.position); cr.line_to(g.end, g.position)
            cr.stroke()
            cr.set_dash([])
            for m in g.marks:
                x, y = (g.position, m) if g.axis == "vertical" else (m, g.position)
                cr.move_to(x - arm, y - arm); cr.line_to(x + arm, y + arm)
                cr.move_to(x - arm, y + arm); cr.line_to(x + arm, y - arm)
            cr.stroke()
        cr.restore()

    def _overlay(self, cr: cairo.Context, scene: Scene, vp: Viewport, o: Overlay) -> None:
        self._guides(cr, o.guides, vp)
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(1 / vp.zoom)
        for r in o.ghosts:
            self._dashed_rect(cr, r, vp)
        if o.marquee is not None:
            m = o.marquee
            cr.rectangle(m.x, m.y, m.w, m.h)
            cr.set_source_rgba(*ACCENT, 0.12); cr.fill_preserve()
            cr.set_source_rgba(*ACCENT, 0.8); cr.set_line_width(1 / vp.zoom); cr.stroke()
        if o.draw_rect is not None:
            self._dashed_rect(cr, o.draw_rect, vp)
        if o.draw_path:
            draw_freehand_preview(cr, o.draw_path, o.draw_tool)
        if o.connecting is not None:
            a, b, target = o.connecting
            pts = static_catenary(a, b)
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(2.5 / vp.zoom)
            cr.set_dash([8 / vp.zoom, 6 / vp.zoom])
            _polyline_smooth(cr, pts)
            cr.stroke()
            cr.set_dash([])
            end = b
            if target is not None:   # snap the free end onto the hovered pin and ring it
                node = scene.get(target[0])
                if node is not None:
                    end = pin_point(node.frame, target[1])  # type: ignore[arg-type]
                    cr.set_line_width(2 / vp.zoom)
                    cr.arc(end.x, end.y, 11 / vp.zoom, 0, 2 * math.pi)
                    cr.stroke()
            cr.arc(end.x, end.y, 5 / vp.zoom, 0, 2 * math.pi)
            cr.fill()
