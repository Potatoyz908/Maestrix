"""Pure pan/zoom math. screen = (canvas - origin) * zoom; `origin` is the canvas point at the screen's top-left."""
from __future__ import annotations

from .constants import (CANVAS_INITIAL_ORIGIN, CANVAS_MAX_ZOOM, CANVAS_MIN_ZOOM, CANVAS_ZOOM_STEP, CULL_MARGIN)
from .geometry import Point, Rect


class Viewport:
    __slots__ = ("origin", "zoom")

    def __init__(self, origin: Point | None = None, zoom: float = 1.0) -> None:
        self.origin = origin or Point(*CANVAS_INITIAL_ORIGIN)
        self.zoom = zoom

    def to_screen(self, p: Point) -> Point:
        return Point((p.x - self.origin.x) * self.zoom, (p.y - self.origin.y) * self.zoom)

    def to_canvas(self, p: Point) -> Point:
        return Point(p.x / self.zoom + self.origin.x, p.y / self.zoom + self.origin.y)

    def rect_to_screen(self, r: Rect) -> Rect:
        return Rect((r.x - self.origin.x) * self.zoom, (r.y - self.origin.y) * self.zoom, r.w * self.zoom, r.h * self.zoom)

    def pan_by_screen(self, dx: float, dy: float) -> None:
        """Move the *content* by (dx, dy) screen px (drag right => content follows the pointer)."""
        self.origin = Point(self.origin.x - dx / self.zoom, self.origin.y - dy / self.zoom)

    def zoom_at(self, anchor_screen: Point, factor: float) -> None:
        """Multiply zoom by `factor` (clamped), keeping the canvas point under `anchor_screen` fixed."""
        self.set_zoom_at(anchor_screen, self.zoom * factor)

    def set_zoom_at(self, anchor_screen: Point, zoom: float) -> None:
        before = self.to_canvas(anchor_screen)
        self.zoom = min(CANVAS_MAX_ZOOM, max(CANVAS_MIN_ZOOM, zoom))
        self.origin = Point(before.x - anchor_screen.x / self.zoom, before.y - anchor_screen.y / self.zoom)

    def zoom_step(self, anchor_screen: Point, direction: int) -> None:
        """Ctrl+= / Ctrl+-: additive step of 0.25 (the macOS app's canvasZoomStep)."""
        self.set_zoom_at(anchor_screen, self.zoom + direction * CANVAS_ZOOM_STEP)

    def visible_rect(self, width: float, height: float, margin: float = CULL_MARGIN) -> Rect:
        return Rect(self.origin.x - margin, self.origin.y - margin, width / self.zoom + 2 * margin, height / self.zoom + 2 * margin)

    def focus_on(self, r: Rect, width: float, height: float, padding_px: float = 48.0) -> None:
        """`\\` on a selected node: center it and zoom so it fits (never zooms in past 1.0)."""
        fit = min((width - 2 * padding_px) / max(r.w, 1), (height - 2 * padding_px) / max(r.h, 1), 1.0)
        self.zoom = min(CANVAS_MAX_ZOOM, max(CANVAS_MIN_ZOOM, fit))
        c = r.center
        self.origin = Point(c.x - width / (2 * self.zoom), c.y - height / (2 * self.zoom))
