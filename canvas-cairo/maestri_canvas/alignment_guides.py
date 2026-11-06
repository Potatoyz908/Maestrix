"""Smart alignment guides (pure, no GTK): while nodes are dragged, their bounding box snaps to the edges and centers of the other
nodes on screen, and thin guide lines show what it aligned to.

Rules
  * x axis: the moving box's left / center / right against every other node's left / center / right, edges with edges (left<->left,
    left<->right, right<->right...: this includes "touching" side by side) and centers with centers; the y axis likewise with
    top / middle / bottom.
  * The threshold is in SCREEN pixels (`SNAP_PX / zoom` canvas units), so the pull feels the same at every zoom level.
  * Per axis the smallest correction wins; every anchor pair that is exactly aligned after that correction contributes a guide
    (several nodes sharing one line merge into a single guide spanning all of them).
  * Each guide carries `marks`: the points along it where an anchor sits, drawn as small crosses.

`snapping.snap()` (Ctrl + drag, Maestri's magnetic tiles) is a different, explicit mode and is untouched.
"""
from __future__ import annotations

from typing import Iterable

from .geometry import Rect
from .snapping import GuideLine

SNAP_PX = 6.0          # pull distance, screen pixels
GUIDE_OVERHANG_PX = 12.0   # guides run a little past the outermost aligned box
EPS = 1e-6


def _x_anchors(r: Rect) -> tuple[tuple[float, str], ...]:
    return ((r.min_x, "edge"), (r.x + r.w / 2, "center"), (r.max_x, "edge"))


def _y_anchors(r: Rect) -> tuple[tuple[float, str], ...]:
    return ((r.min_y, "edge"), (r.y + r.h / 2, "center"), (r.max_y, "edge"))


def _best_delta(moving, others_anchors, threshold: float) -> float | None:
    best: float | None = None
    for m, mk in moving:
        for o, ok in others_anchors:
            if mk != ok:
                continue
            d = o - m
            if abs(d) <= threshold and (best is None or abs(d) < abs(best) - EPS):
                best = d
    return best


def _guides(axis: str, moving: Rect, others: list[Rect], overhang: float) -> list[GuideLine]:
    """Guides for every anchor pair that coincides exactly in the (already snapped) `moving` box."""
    anchors = _x_anchors if axis == "vertical" else _y_anchors
    span = (lambda r: (r.min_y, r.max_y)) if axis == "vertical" else (lambda r: (r.min_x, r.max_x))
    mark = (lambda r: (r.min_y, r.y + r.h / 2, r.max_y)) if axis == "vertical" else (lambda r: (r.min_x, r.x + r.w / 2, r.max_x))
    lines: dict[tuple[float, str], list[Rect]] = {}
    for m, mk in anchors(moving):
        for o in others:
            if any(abs(m - a) <= EPS and ak == mk for a, ak in anchors(o)):
                key = (round(m, 6), mk)
                if o not in lines.setdefault(key, []):
                    lines[key].append(o)
    out: list[GuideLine] = []
    for (pos, kind), rects in sorted(lines.items()):
        boxes = [moving, *rects]
        lo = min(span(r)[0] for r in boxes) - overhang
        hi = max(span(r)[1] for r in boxes) + overhang
        marks = tuple(sorted({round(v, 6) for r in boxes for v in mark(r)}))
        out.append(GuideLine(axis, pos, lo, hi, kind, marks))   # type: ignore[arg-type]
    return out


def align(moving: Rect, others: Iterable[Rect], zoom: float = 1.0, snap_px: float = SNAP_PX) -> tuple[float, float, list[GuideLine]]:
    """Correction (dx, dy) to apply to `moving` (canvas units) and the guides to draw. (0, 0, []) when nothing is in range."""
    others = [o for o in others if o.w > 0 and o.h > 0]
    if not others:
        return 0.0, 0.0, []
    z = max(zoom, 1e-3)
    threshold = snap_px / z
    dx = _best_delta(_x_anchors(moving), [a for o in others for a in _x_anchors(o)], threshold) or 0.0
    dy = _best_delta(_y_anchors(moving), [a for o in others for a in _y_anchors(o)], threshold) or 0.0
    snapped = moving.offset(dx, dy)
    overhang = GUIDE_OVERHANG_PX / z
    guides = _guides("vertical", snapped, others, overhang) + _guides("horizontal", snapped, others, overhang)
    return dx, dy, guides


def candidates(frames: Iterable[Rect], view: Rect, moving: Rect) -> list[Rect]:
    """Only nodes visible on screen (or overlapping the moving box) take part: off-screen alignment would be invisible magic."""
    area = view.union(moving)
    return [f for f in frames if area.intersects(f)]
