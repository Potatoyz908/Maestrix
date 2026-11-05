"""Scene graph used by the engine. Ids are normalized to UPPERCASE (the core emits uppercase UUIDs; comparisons are case-insensitive)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator, Literal

from .geometry import Point, Rect
from .rope import static_catenary  # noqa: F401  (re-exported for callers)

PinSide = Literal["left", "right"]
PIN_RADIUS_PX = 9.0

# Kinds rendered as real GTK widgets (chrome + content). Everything else (shape, stroke, freehand, text) is drawn by Cairo.
WIDGET_KINDS = frozenset({"terminal", "stickyNote", "portal", "fileTree"})
CONNECTABLE_KINDS = frozenset({"terminal", "stickyNote", "portal"})


def norm(i: str) -> str:
    return i.upper()


@dataclass(slots=True)
class NodeItem:
    id: str
    frame: Rect
    z: int = 0
    kind: str = "other"
    title: str = ""
    locked: bool = False
    content: dict | None = None   # schema-v2 `_0` payload of a canvas-drawn kind (text/shape/freehand); None for widget kinds

    def __post_init__(self) -> None:
        self.id = norm(self.id)


@dataclass(slots=True)
class ConnItem:
    id: str
    a: str
    b: str
    side_a: PinSide | None = None   # None => the facing pair, recomputed as nodes move
    side_b: PinSide | None = None
    pending: bool = False           # created locally, not yet confirmed by the core

    def __post_init__(self) -> None:
        self.a, self.b = norm(self.a), norm(self.b)


def pin_point(frame: Rect, side: PinSide) -> Point:
    return Point(frame.min_x if side == "left" else frame.max_x, frame.y + frame.h / 2)


def facing_sides(a: Rect, b: Rect) -> tuple[PinSide, PinSide]:
    return ("right", "left") if a.center.x <= b.center.x else ("left", "right")


class Scene:
    def __init__(self) -> None:
        self.nodes: dict[str, NodeItem] = {}
        self.connections: dict[str, ConnItem] = {}
        self.selection: set[str] = set()
        self._asc: list[NodeItem] | None = None   # render order (z ascending)
        self._desc: list[NodeItem] | None = None  # hit-test order (z descending): both cached, rebuilt only when z/membership changes

    # ---- nodes ------------------------------------------------------------------------------
    def add_node(self, n: NodeItem) -> NodeItem:
        self.nodes[n.id] = n
        self._invalidate()
        return n

    def remove_node(self, node_id: str) -> None:
        k = norm(node_id)
        self.nodes.pop(k, None)
        self.selection.discard(k)
        for c in [c for c in self.connections.values() if k in (c.a, c.b)]:
            del self.connections[c.id]
        self._invalidate()

    def get(self, node_id: str) -> NodeItem | None:
        return self.nodes.get(norm(node_id))

    def set_frame(self, node_id: str, frame: Rect) -> None:
        n = self.nodes.get(norm(node_id))
        if n is not None:
            n.frame = frame  # frame changes never reorder z: no cache invalidation (drag hot path)

    def raise_to_front(self, node_id: str) -> bool:
        n = self.nodes.get(norm(node_id))
        if n is None:
            return False
        top = max((m.z for m in self.nodes.values()), default=0)
        if n.z == top and sum(1 for m in self.nodes.values() if m.z == top) == 1:
            return False
        n.z = top + 1
        self._invalidate()
        return True

    def _invalidate(self) -> None:
        self._asc = self._desc = None

    def ascending(self) -> list[NodeItem]:
        if self._asc is None:
            self._asc = sorted(self.nodes.values(), key=lambda n: n.z)
        return self._asc

    def descending(self) -> list[NodeItem]:
        if self._desc is None:
            self._desc = self.ascending()[::-1]
        return self._desc

    def invalidate_z(self) -> None:
        """Call after mutating NodeItem.z directly."""
        self._invalidate()

    # ---- queries ----------------------------------------------------------------------------
    def hit_node(self, p: Point) -> NodeItem | None:
        for n in self.descending():
            if n.frame.contains(p):
                return n
        return None

    def visible_nodes(self, view: Rect) -> Iterator[NodeItem]:
        for n in self.ascending():
            if view.intersects(n.frame):
                yield n

    def nodes_in_rect(self, r: Rect) -> list[NodeItem]:
        return [n for n in self.ascending() if r.intersects(n.frame)]

    def hit_pin(self, p: Point, zoom: float, exclude: str | None = None) -> tuple[NodeItem, PinSide] | None:
        """Pin under canvas point `p` (radius is constant in screen px, so it shrinks in canvas units as you zoom in)."""
        r = PIN_RADIUS_PX / zoom
        ex = norm(exclude) if exclude else None
        for n in self.descending():
            if n.id == ex or n.kind not in CONNECTABLE_KINDS:
                continue   # fileTree and canvas-drawn kinds (shape/stroke/text) have no pins
            for side in ("left", "right"):
                q = pin_point(n.frame, side)  # type: ignore[arg-type]
                if math.hypot(q.x - p.x, q.y - p.y) <= r:
                    return n, side  # type: ignore[return-value]
        return None

    # ---- connections ------------------------------------------------------------------------
    def can_connect(self, a: str, b: str) -> bool:
        a, b = norm(a), norm(b)
        if a == b or a not in self.nodes or b not in self.nodes:
            return False
        if self.nodes[a].kind not in CONNECTABLE_KINDS or self.nodes[b].kind not in CONNECTABLE_KINDS:
            return False
        return not any({c.a, c.b} == {a, b} for c in self.connections.values())

    def add_connection(self, c: ConnItem) -> ConnItem:
        self.connections[c.id] = c
        return c

    def anchors(self, c: ConnItem) -> tuple[Point, Point] | None:
        na, nb = self.nodes.get(c.a), self.nodes.get(c.b)
        if na is None or nb is None:
            return None
        fa, fb = facing_sides(na.frame, nb.frame)
        return pin_point(na.frame, c.side_a or fa), pin_point(nb.frame, c.side_b or fb)

    def connections_of(self, node_ids: set[str]) -> list[ConnItem]:
        return [c for c in self.connections.values() if c.a in node_ids or c.b in node_ids]

    # ---- selection --------------------------------------------------------------------------
    def selected_nodes(self) -> list[NodeItem]:
        return [n for n in self.ascending() if n.id in self.selection]
