"""Pure pycairo renderer: draws onto ANY cairo.Context (an ImageSurface owned by the widget, or a test surface). No GTK imports.

Layers, bottom to top: background + dot grid, ropes, canvas-drawn nodes (shape/stroke/freehand/text), then gesture overlays
(snap guides, marquee, rope being dragged, draw-to-create rect, Alt-drag ghosts). Widget nodes (terminal/note/portal/fileTree)
are real GTK widgets stacked above this layer, so they are NOT drawn here unless `placeholders=True` (headless tests/screenshots).
"""
from __future__ import annotations

import math

import cairo

from .geometry import Point, Rect
from .interaction import Overlay
from .rope import RopeSimulation, static_catenary
from .scene import WIDGET_KINDS, ConnItem, Scene, pin_point
from .viewport import Viewport

BG = (0.949, 0.949, 0.949)
GRID = (0.80, 0.80, 0.80)
ROPE = (0.54, 0.54, 0.56)
ACCENT = (0.231, 0.510, 0.965)
KIND_FILL = {"terminal": (0.12, 0.12, 0.12), "stickyNote": (1.0, 0.953, 0.639), "portal": (1.0, 1.0, 1.0), "fileTree": (0.98, 0.98, 0.99)}
GRID_STEP = 40.0
MIN_GRID_PX = 12.0


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
    def __init__(self, placeholders: bool = False) -> None:
        self.placeholders = placeholders

    def draw(self, cr: cairo.Context, scene: Scene, vp: Viewport, width: float, height: float,
             ropes: RopeSimulation | None = None, overlay: Overlay | None = None) -> None:
        """Base layer (+ the overlay when given, for headless use). The widget passes overlay=None and draws it on a top layer."""
        cr.set_source_rgb(*BG)
        cr.paint()
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
        step = GRID_STEP
        while step * vp.zoom < MIN_GRID_PX:
            step *= 2  # keep the dot spacing readable (and the dot count bounded) when zoomed far out
        x0 = math.floor(vp.origin.x / step) * step
        y0 = math.floor(vp.origin.y / step) * step
        cr.set_source_rgb(*GRID)
        r = 1.0
        x = x0
        while (x - vp.origin.x) * vp.zoom <= w:
            sx = (x - vp.origin.x) * vp.zoom
            y = y0
            while (y - vp.origin.y) * vp.zoom <= h:
                cr.rectangle(sx - r / 2, (y - vp.origin.y) * vp.zoom - r / 2, r * 1.5, r * 1.5)
                y += step
            x += step
        cr.fill()

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
    def _dashed_rect(cr: cairo.Context, r: Rect, vp: Viewport) -> None:
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(1.5 / vp.zoom)
        cr.set_dash([5 / vp.zoom, 4 / vp.zoom])
        cr.rectangle(r.x, r.y, r.w, r.h)
        cr.stroke()
        cr.set_dash([])

    def _overlay(self, cr: cairo.Context, scene: Scene, vp: Viewport, o: Overlay) -> None:
        cr.set_source_rgb(*ACCENT)
        cr.set_line_width(1 / vp.zoom)
        for g in o.guides:
            if g.axis == "vertical":
                cr.move_to(g.position, g.start); cr.line_to(g.position, g.end)
            else:
                cr.move_to(g.start, g.position); cr.line_to(g.end, g.position)
            cr.stroke()
        for r in o.ghosts:
            self._dashed_rect(cr, r, vp)
        if o.marquee is not None:
            m = o.marquee
            cr.rectangle(m.x, m.y, m.w, m.h)
            cr.set_source_rgba(*ACCENT, 0.12); cr.fill_preserve()
            cr.set_source_rgba(*ACCENT, 0.8); cr.set_line_width(1 / vp.zoom); cr.stroke()
        if o.draw_rect is not None:
            self._dashed_rect(cr, o.draw_rect, vp)
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
