"""8-direction resize (port of ResizeEdge.swift + CanvasResizeHandler.applyResizeOnCanvas, in canvas space)."""
from __future__ import annotations

from enum import Enum

from .constants import CANVAS_GRID_SPACING, MIN_NODE_HEIGHT, MIN_NODE_WIDTH
from .geometry import Rect, round_half_away


class Edge(str, Enum):
    RIGHT = "right"; LEFT = "left"; TOP = "top"; BOTTOM = "bottom"
    TOP_LEFT = "top_left"; TOP_RIGHT = "top_right"; BOTTOM_LEFT = "bottom_left"; BOTTOM_RIGHT = "bottom_right"

    @property
    def cursor(self) -> str:
        """CSS/GDK cursor name."""
        return {
            Edge.LEFT: "ew-resize", Edge.RIGHT: "ew-resize", Edge.TOP: "ns-resize", Edge.BOTTOM: "ns-resize",
            Edge.TOP_LEFT: "nwse-resize", Edge.BOTTOM_RIGHT: "nwse-resize",
            Edge.TOP_RIGHT: "nesw-resize", Edge.BOTTOM_LEFT: "nesw-resize",
        }[self]


def _grid(v: float) -> float:
    return round_half_away(v / CANVAS_GRID_SPACING) * CANVAS_GRID_SPACING


def resize_frame(start: Rect, edge: Edge, dx: float, dy: float, grid: bool = True,
                 min_size: tuple[float, float] = (MIN_NODE_WIDTH, MIN_NODE_HEIGHT)) -> Rect:
    """`dx`/`dy` are canvas units (screen delta / zoom). The active edge snaps to the 16-unit grid; the opposite edge stays fixed.
    `min_size`: widget nodes keep 160x80; canvas-drawn kinds (a text label, a short stroke) pass a smaller one."""
    left = edge in (Edge.LEFT, Edge.TOP_LEFT, Edge.BOTTOM_LEFT)
    right = edge in (Edge.RIGHT, Edge.TOP_RIGHT, Edge.BOTTOM_RIGHT)
    top = edge in (Edge.TOP, Edge.TOP_LEFT, Edge.TOP_RIGHT)
    bottom = edge in (Edge.BOTTOM, Edge.BOTTOM_LEFT, Edge.BOTTOM_RIGHT)
    s = _grid if grid else (lambda v: v)

    l, r, t, b = start.min_x, start.max_x, start.min_y, start.max_y
    min_w, min_h = min_size
    if left:   l = min(s(start.x + dx), r - min_w)
    if right:  r = max(s(r + dx), l + min_w)
    if top:    t = min(s(start.y + dy), b - min_h)
    if bottom: b = max(s(b + dy), t + min_h)
    return Rect(l, t, r - l, b - t)
