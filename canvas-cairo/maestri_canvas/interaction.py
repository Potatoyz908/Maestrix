"""Pointer/keyboard state machine. Pure: no GTK, no cairo, no clocks. The widget feeds it widget-space coordinates and mirrors CanvasInteraction.swift.

Reference spec (docs/reference/maestri-reference-index.md), with the macOS command key mapped to Ctrl:
  pan: two-finger scroll / middle-drag / Space+drag      zoom: Ctrl+scroll / pinch / Ctrl+= / Ctrl+-
  move: drag a node header (Ctrl = magnetic tile snapping)   resize: drag edge/corner (snaps to the 16-unit grid)
  duplicate: Alt+drag     delete: Ctrl+W / Delete     focus node: \\     select: click, Shift+click, marquee on empty space
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntFlag
from typing import Callable, Union

from .constants import CANVAS_GRID_SPACING, DRAG_THRESHOLD_PX, MIN_NODE_HEIGHT, MIN_NODE_WIDTH
from .geometry import Point, Rect, round_half_away
from .resize import Edge, resize_frame
from .rope import RopeSimulation
from .scene import ConnItem, PinSide, Scene, norm, pin_point
from .snapping import GuideLine, snap
from .viewport import Viewport

WHEEL_NOTCH_PX = 40.0         # the widget converts one discrete wheel notch to this many px
SCROLL_ZOOM_PER_PX = 0.0025   # zoom = exp(-px * 0.0025): one notch (40px) ~ 10%
NUDGE_SHIFT_MULT = 10


class Mods(IntFlag):
    NONE = 0
    SHIFT = 1
    CTRL = 2
    ALT = 4


# ---- what the pointer is over (resolved by the widget through Gtk pick) ---------------------------
@dataclass(frozen=True, slots=True)
class Background: pass
@dataclass(frozen=True, slots=True)
class NodeHeader: id: str            # draggable chrome
@dataclass(frozen=True, slots=True)
class NodeEdge: id: str; edge: Edge  # resize handle
@dataclass(frozen=True, slots=True)
class NodePin: id: str; side: PinSide
@dataclass(frozen=True, slots=True)
class NodeContent: id: str           # terminal/note/... body: the child widget handles the pointer
Target = Union[Background, NodeHeader, NodeEdge, NodePin, NodeContent]


# ---- events emitted to the application -----------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NodesMoved: frames: dict[str, Rect]; final: bool
@dataclass(frozen=True, slots=True)
class NodeResized: id: str; frame: Rect; final: bool
@dataclass(frozen=True, slots=True)
class SelectionChanged: ids: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class ViewportChanged: x: float; y: float; zoom: float
@dataclass(frozen=True, slots=True)
class ConnectRequested: a: str; side_a: PinSide; b: str; side_b: PinSide
@dataclass(frozen=True, slots=True)
class DeleteRequested: ids: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class DuplicateRequested: frames: dict[str, Rect]   # source id -> frame for the copy
@dataclass(frozen=True, slots=True)
class DrawRequested: kind: str; frame: Rect
@dataclass(frozen=True, slots=True)
class ToolChanged: tool: str
Event = Union[NodesMoved, NodeResized, SelectionChanged, ViewportChanged, ConnectRequested, DeleteRequested, DuplicateRequested, DrawRequested, ToolChanged]


# ---- gesture states (CanvasInteraction) -----------------------------------------------------------
@dataclass(slots=True)
class _Panning: last: Point
@dataclass(slots=True)
class _Dragging:
    start: Point; primary: str; starts: dict[str, Rect]; duplicate: bool; active: bool = False
@dataclass(slots=True)
class _Resizing: id: str; edge: Edge; start_frame: Rect; start: Point
@dataclass(slots=True)
class _Marquee: start: Point; base: frozenset[str]; moved: bool = False
@dataclass(slots=True)
class _Connecting: src: str; side: PinSide
@dataclass(slots=True)
class _Drawing: kind: str; start: Point
_State = Union[None, _Panning, _Dragging, _Resizing, _Marquee, _Connecting, _Drawing]


@dataclass(slots=True)
class Overlay:
    """Transient visuals owned by the gesture in progress; the renderer draws them, the widget never mutates them."""
    guides: list[GuideLine] = field(default_factory=list)
    marquee: Rect | None = None
    connecting: tuple[Point, Point, tuple[str, PinSide] | None] | None = None  # (from, to, hovered target pin)
    draw_rect: Rect | None = None
    ghosts: list[Rect] = field(default_factory=list)  # Alt-drag duplicate preview

    def is_empty(self) -> bool:
        return not (self.guides or self.marquee or self.connecting or self.draw_rect or self.ghosts)


class CanvasController:
    def __init__(self, scene: Scene, viewport: Viewport, ropes: RopeSimulation | None = None) -> None:
        self.scene, self.vp = scene, viewport
        self.ropes = ropes if ropes is not None else RopeSimulation()
        self.overlay = Overlay()
        self.tool = "select"
        self.cursor: str | None = None
        self.size = (1.0, 1.0)            # widget size in px, set by the widget (for focus_on / zoom-at-center)
        self.space_held = False
        self.dirty = True                 # the widget redraws when set, then clears it
        self.on_event: Callable[[Event], None] | None = None
        self._state: _State = None

    # ---- helpers ------------------------------------------------------------------------------
    def _emit(self, e: Event) -> None:
        self.dirty = True
        if self.on_event:
            self.on_event(e)

    @property
    def busy(self) -> bool:
        return self._state is not None

    def _viewport_changed(self) -> None:
        self._emit(ViewportChanged(self.vp.origin.x, self.vp.origin.y, self.vp.zoom))

    def _set_selection(self, ids: set[str]) -> None:
        if ids != self.scene.selection:
            self.scene.selection = ids
            self._emit(SelectionChanged(tuple(sorted(ids))))

    def _reanchor(self, node_ids: set[str]) -> None:
        for c in self.scene.connections_of(node_ids):
            a = self.scene.anchors(c)
            if a:
                self.ropes.update_anchors(c.id, *a)

    def set_tool(self, tool: str) -> None:
        if tool != self.tool:
            self.tool = tool
            self._emit(ToolChanged(tool))

    # ---- pointer -------------------------------------------------------------------------------
    def press(self, pt: Point, button: int, mods: Mods, target: Target) -> bool:
        """Returns True when the engine claims the gesture (the widget then denies it to children)."""
        self.dirty = True
        canvas = self.vp.to_canvas(pt)
        if isinstance(target, NodeContent):
            self._select_on_press(target.id, mods)
            return False  # the child widget (terminal, text view, ...) keeps the pointer
        if button == 2 or (button == 1 and self.space_held and isinstance(target, Background)):
            self._state = _Panning(pt)
            self.cursor = "grabbing"
            return True
        if button != 1:
            return False

        if isinstance(target, NodeHeader):
            self._select_on_press(target.id, mods)
            node = self.scene.get(target.id)
            if node is None or node.locked:
                return True
            moving = [n for n in self.scene.selected_nodes() if not n.locked]
            self._state = _Dragging(pt, node.id, {n.id: n.frame for n in moving}, duplicate=bool(mods & Mods.ALT))
            return True
        if isinstance(target, NodeEdge):
            self._select_on_press(target.id, mods)
            node = self.scene.get(target.id)
            if node is not None and not node.locked:
                self._state = _Resizing(node.id, target.edge, node.frame, pt)
                self.cursor = target.edge.cursor
            return True
        if isinstance(target, NodePin):
            src = self.scene.get(target.id)
            if src is not None:
                self._state = _Connecting(src.id, target.side)
                self.overlay.connecting = (pin_point(src.frame, target.side), canvas, None)
                self.cursor = "crosshair"
            return True

        # Background
        if self.tool != "select":
            self._state = _Drawing(self.tool, canvas)
            self.cursor = "crosshair"
        else:
            self._state = _Marquee(canvas, frozenset(self.scene.selection) if mods & Mods.SHIFT else frozenset())
        return True

    def _select_on_press(self, node_id: str, mods: Mods) -> None:
        k = norm(node_id)
        sel = set(self.scene.selection)
        if mods & Mods.SHIFT:
            sel ^= {k}
        elif k not in sel:
            sel = {k}   # pressing an already-selected node keeps the selection, so groups can be dragged
        self._set_selection(sel)
        if self.scene.raise_to_front(k):
            self.dirty = True

    def move(self, pt: Point, mods: Mods) -> None:
        st = self._state
        canvas = self.vp.to_canvas(pt)
        if isinstance(st, _Panning):
            self.vp.pan_by_screen(pt.x - st.last.x, pt.y - st.last.y)
            st.last = pt
            self._viewport_changed()
        elif isinstance(st, _Dragging):
            if not st.active:
                if math.hypot(pt.x - st.start.x, pt.y - st.start.y) < DRAG_THRESHOLD_PX:
                    return
                st.active = True
            self._drag(st, pt, mods)
        elif isinstance(st, _Resizing):
            dx, dy = (pt.x - st.start.x) / self.vp.zoom, (pt.y - st.start.y) / self.vp.zoom
            frame = resize_frame(st.start_frame, st.edge, dx, dy)
            self.scene.set_frame(st.id, frame)
            self._reanchor({st.id})
            self._emit(NodeResized(st.id, frame, False))
        elif isinstance(st, _Marquee):
            if not st.moved and math.hypot(canvas.x - st.start.x, canvas.y - st.start.y) * self.vp.zoom >= DRAG_THRESHOLD_PX:
                st.moved = True
            if st.moved:
                r = Rect.from_points(st.start, canvas)
                self.overlay.marquee = r
                self._set_selection(set(st.base) | {n.id for n in self.scene.nodes_in_rect(r)})
                self.dirty = True
        elif isinstance(st, _Connecting):
            src = self.scene.get(st.src)
            if src is not None:
                hit = self.scene.hit_pin(canvas, self.vp.zoom, exclude=st.src)
                target = (hit[0].id, hit[1]) if hit and self.scene.can_connect(st.src, hit[0].id) else None
                self.overlay.connecting = (pin_point(src.frame, st.side), canvas, target)
                self.dirty = True
        elif isinstance(st, _Drawing):
            self.overlay.draw_rect = Rect.from_points(st.start, canvas)
            self.dirty = True

    def _drag(self, st: _Dragging, pt: Point, mods: Mods) -> None:
        dx, dy = (pt.x - st.start.x) / self.vp.zoom, (pt.y - st.start.y) / self.vp.zoom
        self.overlay.guides = []
        if mods & Mods.CTRL and st.primary in st.starts:   # magnetic tile snapping: Ctrl held, relative to the primary node
            others = [n.frame for n in self.scene.nodes.values() if n.id not in st.starts]
            moved = st.starts[st.primary].offset(dx, dy)
            snapped, guides = snap(moved, others)
            dx, dy = snapped.x - st.starts[st.primary].x, snapped.y - st.starts[st.primary].y
            self.overlay.guides = guides
        frames = {i: f.offset(dx, dy) for i, f in st.starts.items()}
        if st.duplicate:
            self.overlay.ghosts = list(frames.values())   # originals stay put; the copy is created on release
        else:
            for i, f in frames.items():
                self.scene.set_frame(i, f)
            self._reanchor(set(frames))
            self._emit(NodesMoved(frames, False))
        self.dirty = True

    def release(self, pt: Point, mods: Mods) -> None:
        st, self._state = self._state, None
        canvas = self.vp.to_canvas(pt)
        if isinstance(st, _Dragging) and st.active:
            if st.duplicate:
                frames = {i: r for i, r in zip(st.starts, self.overlay.ghosts)}
                self._emit(DuplicateRequested(frames))
            else:
                self._emit(NodesMoved({i: self.scene.nodes[i].frame for i in st.starts}, True))
        elif isinstance(st, _Resizing):
            self._emit(NodeResized(st.id, self.scene.nodes[st.id].frame, True))
        elif isinstance(st, _Marquee) and not st.moved and not st.base:
            self._set_selection(set())   # a plain click on empty space deselects
        elif isinstance(st, _Connecting):
            hit = self.scene.hit_pin(canvas, self.vp.zoom, exclude=st.src)
            if hit and self.scene.can_connect(st.src, hit[0].id):
                self._emit(ConnectRequested(st.src, st.side, hit[0].id, hit[1]))
        elif isinstance(st, _Drawing):
            r = Rect.from_points(st.start, canvas)
            if r.w >= MIN_NODE_WIDTH / 2 and r.h >= MIN_NODE_HEIGHT / 2:
                g = CANVAS_GRID_SPACING
                x0, y0 = round_half_away(r.x / g) * g, round_half_away(r.y / g) * g
                frame = Rect(x0, y0, max(MIN_NODE_WIDTH, round_half_away(r.w / g) * g), max(MIN_NODE_HEIGHT, round_half_away(r.h / g) * g))
                self._emit(DrawRequested(st.kind, frame))
            self.set_tool("select")
        self._clear_overlay()
        self.cursor = None

    def cancel(self) -> bool:
        """Esc: abort the gesture in progress (restoring frames), else drop the tool, else deselect."""
        st, self._state = self._state, None
        if isinstance(st, _Dragging):
            for i, f in st.starts.items():
                self.scene.set_frame(i, f)
            self._reanchor(set(st.starts))
            if st.active and not st.duplicate:
                self._emit(NodesMoved(dict(st.starts), True))
        elif isinstance(st, _Resizing):
            self.scene.set_frame(st.id, st.start_frame)
            self._reanchor({st.id})
            self._emit(NodeResized(st.id, st.start_frame, True))
        if st is not None:
            self._clear_overlay(); self.cursor = None
            return True
        if self.tool != "select":
            self.set_tool("select")
            return True
        if self.scene.selection:
            self._set_selection(set())
            return True
        return False

    def _clear_overlay(self) -> None:
        self.overlay = Overlay()
        self.dirty = True

    # ---- wheel / touchpad ------------------------------------------------------------------------
    def scroll(self, pt: Point, dx: float, dy: float, mods: Mods) -> bool:
        """dx/dy in px (the widget converts wheel notches via WHEEL_NOTCH_PX). Ctrl = zoom, else pan (Shift swaps the axis)."""
        if mods & Mods.CTRL:
            self.vp.zoom_at(pt, math.exp(-dy * SCROLL_ZOOM_PER_PX))
        else:
            if mods & Mods.SHIFT:
                dx, dy = dy, dx
            self.vp.pan_by_screen(-dx, -dy)
        self._viewport_changed()
        return True

    def pinch(self, center: Point, factor: float) -> None:
        self.vp.zoom_at(center, factor)
        self._viewport_changed()

    # ---- keyboard ---------------------------------------------------------------------------------
    def key_release(self, name: str) -> None:
        if name == "space":
            self.space_held = False

    def key(self, name: str, mods: Mods, canvas_focused: bool) -> bool:
        """`name` is the GDK keyval name lower-cased ('equal', 'minus', 'escape', 'delete', 'left', ...). Returns True if handled.
        When focus is inside a node (terminal, note editor...) only Ctrl chords are taken; plain keys belong to that widget."""
        center = Point(self.size[0] / 2, self.size[1] / 2)
        if mods & Mods.CTRL:
            if name in ("equal", "plus"):
                self.vp.zoom_step(center, +1); self._viewport_changed(); return True
            if name in ("minus", "underscore"):
                self.vp.zoom_step(center, -1); self._viewport_changed(); return True
            if name == "0":
                self.vp.set_zoom_at(center, 1.0); self._viewport_changed(); return True
            if name == "w" and self.scene.selection:
                self._emit(DeleteRequested(tuple(sorted(self.scene.selection)))); return True
            if name == "a" and canvas_focused:
                self._set_selection(set(self.scene.nodes)); return True
            return False
        if not canvas_focused:
            return False
        if name == "escape":
            return self.cancel()
        if name == "space":
            self.space_held = True
            return True
        if name in ("delete", "backspace") and self.scene.selection:
            self._emit(DeleteRequested(tuple(sorted(self.scene.selection)))); return True
        if name == "backslash" and self.scene.selection:
            sel = self.scene.selected_nodes()
            r = sel[0].frame
            for n in sel[1:]:
                r = r.union(n.frame)
            self.vp.focus_on(r, *self.size); self._viewport_changed(); return True
        if name == "p":
            self.set_tool("portal"); return True
        if name in ("left", "right", "up", "down") and self.scene.selection:
            step = CANVAS_GRID_SPACING * (NUDGE_SHIFT_MULT if mods & Mods.SHIFT else 1)
            dx = {"left": -step, "right": step}.get(name, 0.0)
            dy = {"up": -step, "down": step}.get(name, 0.0)
            frames = {}
            for n in self.scene.selected_nodes():
                if not n.locked:
                    n.frame = n.frame.offset(dx, dy)
                    frames[n.id] = n.frame
            self._reanchor(set(frames))
            if frames:
                self._emit(NodesMoved(frames, True))
            return True
        return False

    # ---- connections confirmed by the core ------------------------------------------------------
    def add_connection(self, c: ConnItem) -> None:
        self.scene.add_connection(c)
        a = self.scene.anchors(c)
        if a:
            self.ropes.add(c.id, *a)
        self.dirty = True
