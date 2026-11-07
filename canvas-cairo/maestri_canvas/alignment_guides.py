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

Resize (`align_resize`): only the edges being dragged move. Each active edge snaps to the nearest edge OR center of a visible node
(an edge may land on a neighbour's center line here: that is how you size a node to reach the middle of another), else it keeps
the 16-unit grid snapping of resize.py. The opposite edge never moves and the minimum size always wins over a snap.

`snapping.snap()` (Ctrl + drag, Maestri's magnetic tiles) is a different, explicit mode and is untouched.
"""
from __future__ import annotations

from typing import Iterable

from .constants import MIN_NODE_HEIGHT, MIN_NODE_WIDTH
from .geometry import Rect
from .resize import Edge, resize_frame
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


def _active(edge: Edge) -> tuple[str | None, str | None]:
    """('left'|'right'|None, 'top'|'bottom'|None) moved by a resize handle."""
    v = edge.value
    return ("left" if "left" in v else "right" if "right" in v else None,
            "top" if "top" in v else "bottom" if "bottom" in v else None)


def _nearest(pos: float, anchors, threshold: float) -> tuple[float, str] | None:
    """Closest anchor within `threshold`; on an exact tie an edge beats a center."""
    in_range = [(abs(a - pos), kind != "edge", a, kind) for a, kind in anchors if abs(a - pos) <= threshold]
    if not in_range:
        return None
    _d, _c, a, kind = min(in_range)
    return a, kind


def _edge_guide(axis: str, pos: float, kind: str, frame: Rect, others: list[Rect], overhang: float) -> GuideLine:
    anchors = _x_anchors if axis == "vertical" else _y_anchors
    hits = [o for o in others if any(abs(a - pos) <= EPS for a, _k in anchors(o))]
    span = (lambda r: (r.min_y, r.max_y)) if axis == "vertical" else (lambda r: (r.min_x, r.max_x))
    mark = (lambda r: (r.min_y, r.y + r.h / 2, r.max_y)) if axis == "vertical" else (lambda r: (r.min_x, r.x + r.w / 2, r.max_x))
    boxes = [frame, *hits]
    marks = sorted({round(v, 6) for r in boxes for v in mark(r)})
    return GuideLine(axis, pos, min(span(r)[0] for r in boxes) - overhang, max(span(r)[1] for r in boxes) + overhang,  # type: ignore[arg-type]
                     kind, tuple(marks))


def align_resize(start: Rect, edge: Edge, dx: float, dy: float, others, zoom: float = 1.0,
                 min_size: tuple[float, float] = (MIN_NODE_WIDTH, MIN_NODE_HEIGHT),
                 snap_px: float = SNAP_PX) -> tuple[Rect, list[GuideLine]]:
    """The resized frame and its guides. Active edges in range of a neighbour's edge/center snap to it; the others keep the grid."""
    others = [o for o in others if o.w > 0 and o.h > 0]
    gridded = resize_frame(start, edge, dx, dy, grid=True, min_size=min_size)
    if not others:
        return gridded, []
    raw = resize_frame(start, edge, dx, dy, grid=False, min_size=min_size)
    z = max(zoom, 1e-3)
    threshold, overhang = snap_px / z, GUIDE_OVERHANG_PX / z
    min_w, min_h = min_size
    l, r, t, b = gridded.min_x, gridded.max_x, gridded.min_y, gridded.max_y
    hx, hy = _active(edge)
    snapped: list[tuple[str, float, str]] = []

    xs = [a for o in others for a in _x_anchors(o)]
    ys = [a for o in others for a in _y_anchors(o)]
    if hx == "left" and (m := _nearest(raw.min_x, xs, threshold)) and m[0] <= start.max_x - min_w:
        l = m[0]; snapped.append(("vertical", m[0], m[1]))
    elif hx == "right" and (m := _nearest(raw.max_x, xs, threshold)) and m[0] >= start.min_x + min_w:
        r = m[0]; snapped.append(("vertical", m[0], m[1]))
    if hy == "top" and (m := _nearest(raw.min_y, ys, threshold)) and m[0] <= start.max_y - min_h:
        t = m[0]; snapped.append(("horizontal", m[0], m[1]))
    elif hy == "bottom" and (m := _nearest(raw.max_y, ys, threshold)) and m[0] >= start.min_y + min_h:
        b = m[0]; snapped.append(("horizontal", m[0], m[1]))
    frame = Rect(l, t, r - l, b - t)
    return frame, [_edge_guide(axis, pos, kind, frame, others, overhang) for axis, pos, kind in snapped]
