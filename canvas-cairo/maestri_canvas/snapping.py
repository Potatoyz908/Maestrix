"""Magnetic tile snapping (port of TileSnapping.swift). Only active while Ctrl is held during a drag (reference spec)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .constants import SNAP_PROXIMITY_RADIUS, SNAP_THRESHOLD
from .geometry import Point, Rect

Axis = Literal["horizontal", "vertical"]


@dataclass(frozen=True, slots=True)
class GuideLine:
    axis: Axis          # "vertical" draws x=position from start..end; "horizontal" draws y=position
    position: float
    start: float
    end: float


def snap(dragging: Rect, others: Iterable[Rect]) -> tuple[Point, list[GuideLine]]:
    x, y = dragging.x, dragging.y
    guides: list[GuideLine] = []
    d_min_x, d_max_x, d_min_y, d_max_y = dragging.min_x, dragging.max_x, dragging.min_y, dragging.max_y
    best_dx = best_dy = SNAP_THRESHOLD + 1
    near = dragging.inset(-SNAP_PROXIMITY_RADIUS, -SNAP_PROXIMITY_RADIUS)

    for o in others:
        if not near.intersects(o):
            continue
        for da, oa in ((d_min_x, o.min_x), (d_min_x, o.max_x), (d_max_x, o.min_x), (d_max_x, o.max_x)):
            dist = abs(da - oa)
            if dist < SNAP_THRESHOLD and dist < best_dx:
                best_dx = dist
                x = d_min_x + (oa - da)
                guides = [g for g in guides if g.axis != "vertical"]
                guides.append(GuideLine("vertical", oa, min(d_min_y, o.min_y) - 20, max(d_max_y, o.max_y) + 20))
        for da, oa in ((d_min_y, o.min_y), (d_min_y, o.max_y), (d_max_y, o.min_y), (d_max_y, o.max_y)):
            dist = abs(da - oa)
            if dist < SNAP_THRESHOLD and dist < best_dy:
                best_dy = dist
                y = d_min_y + (oa - da)
                guides = [g for g in guides if g.axis != "horizontal"]
                guides.append(GuideLine("horizontal", oa, min(d_min_x, o.min_x) - 20, max(d_max_x, o.max_x) + 20))
    return Point(x, y), guides
