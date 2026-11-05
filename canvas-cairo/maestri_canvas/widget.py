"""GTK 4 glue. Everything pointer/keyboard-related is delegated to the pure CanvasController; this file only translates GTK events.

Structure (Gtk.Overlay):
    CairoLayer   - Gtk.Widget whose snapshot is a pycairo ImageSurface uploaded as a Gdk.MemoryTexture
    Gtk.Fixed    - node widgets (NodeFrame), positioned/zoomed with per-child GskTransforms

Why not Gtk.DrawingArea + set_draw_func: PyGObject needs the foreign-struct bridge `gi._gi_cairo` (Debian/Ubuntu python3-gi-cairo)
to hand a cairo.Context to a Python callback; it is missing on this host (ImportError). Rendering into our own ImageSurface needs no
bridge. If it becomes available, CairoLayer can switch to a DrawingArea without touching the renderer (it accepts any cairo.Context).

Input model: ONE capture-phase GestureDrag on the CanvasWidget sees every press, asks `pick()` what is under the pointer and tags it with a
role (header / edge / pin / content / background). Chrome roles are claimed by the engine; content roles are DENIED so the child widget
(terminal, text view, ...) gets the event untouched. Child widgets therefore need no gestures of their own - only a role tag.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
import cairo
from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk

from .constants import CANVAS_GRID_SPACING  # noqa: F401
from .geometry import Point, Rect, round_half_away
from .interaction import (WHEEL_NOTCH_PX, Background, CanvasController, ConnectRequested, DeleteRequested, DrawRequested,
                          DuplicateRequested, Event, Mods, NodeContent, NodeEdge, NodeHeader, NodePin, NodeResized, NodesMoved,
                          SelectionChanged, Target, ToolChanged, ViewportChanged)
from .renderer import CairoRenderer
from .resize import Edge
from .scene import WIDGET_KINDS, ConnItem, NodeItem, Scene, norm
from .viewport import Viewport

HEADER_H = 28
SIDE, SIDE_OUT = 10, 5        # side resize handle: 10px thick, 5 outside the border
CORNER, CORNER_OUT = 16, 6    # corner handle: 16px square, 6 outside
_ROLE = "_canvas_role"   # python attribute set on chrome widgets: ("header"|"edge"|"pin", payload)


class CairoLayer(Gtk.Widget):
    """A Cairo-painted layer. `top=False`: grid, ropes, canvas-drawn nodes (below the node widgets).
    `top=True`: gesture overlays on a transparent surface (above them); it paints nothing when no gesture is active."""
    __gtype_name__ = "MaestriCairoLayer"

    def __init__(self, canvas: "CanvasWidget", top: bool = False) -> None:
        super().__init__()
        self._canvas, self._top = canvas, top
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False)   # pure paint: never steals pointer events

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        w, h, s = self.get_width(), self.get_height(), self.get_scale_factor()
        c = self._canvas
        if w <= 0 or h <= 0 or (self._top and c.controller.overlay.is_empty()):
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * s, h * s)
        surf.set_device_scale(s, s)   # draw in logical px; HiDPI handled by the surface
        cr = cairo.Context(surf)
        if self._top:
            c.renderer.draw_overlay(cr, c.scene, c.viewport, c.controller.overlay)
        else:
            c.renderer.draw(cr, c.scene, c.viewport, w, h, c.controller.ropes, None)
        surf.flush()
        tex = Gdk.MemoryTexture.new(w * s, h * s, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                    GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())
        snapshot.append_texture(tex, Graphene.Rect().init(0, 0, w, h))


class NodeFrame(Gtk.Overlay):
    """Chrome for one node: header (drag + close), the content widget, 8 resize handles and 2 connection pins.
    Handles/pins carry role tags only; the CanvasWidget's gesture does the work."""

    def __init__(self, item: NodeItem, content: Gtk.Widget, on_close) -> None:
        super().__init__()
        self.node_id = item.id
        self.add_css_class("maestri-node")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(spacing=6)
        header.add_css_class("maestri-node-header")
        header.set_size_request(-1, HEADER_H)
        setattr(header, _ROLE, ("header", item.id))
        self.title = Gtk.Label(label=item.title or item.id[:8], xalign=0, hexpand=True, ellipsize=3)
        close = Gtk.Button(label="×")
        close.add_css_class("flat")
        close.connect("clicked", lambda _b: on_close(self.node_id))
        setattr(close, _ROLE, ("content", item.id))   # a real button: it keeps its own click
        header.append(self.title)
        header.append(close)
        content.set_vexpand(True)
        content.set_hexpand(True)
        setattr(content, _ROLE, ("content", item.id))
        box.append(header)
        box.append(content)
        self.set_child(box)
        self.content = content
        for edge in Edge:
            self.add_overlay(self._handle(edge))
        for side in ("left", "right"):
            self.add_overlay(self._pin(side))

    def _handle(self, edge: Edge) -> Gtk.Widget:
        """Resize handle straddling the border (part outside, part inside) so the edge is easy to grab.
        Side handles run the full side minus the corner handles; corners are bigger squares."""
        w = Gtk.Box()
        w.add_css_class("maestri-handle")
        setattr(w, _ROLE, ("edge", (self.node_id, edge)))
        parts = edge.value.split("_")
        corner = len(parts) == 2
        thick, out = (CORNER, CORNER_OUT) if corner else (SIDE, SIDE_OUT)
        horiz = "left" in parts or "right" in parts
        vert = "top" in parts or "bottom" in parts
        w.set_halign(Gtk.Align.START if "left" in parts else Gtk.Align.END if "right" in parts else Gtk.Align.FILL)
        w.set_valign(Gtk.Align.START if "top" in parts else Gtk.Align.END if "bottom" in parts else Gtk.Align.FILL)
        w.set_size_request(thick if horiz else -1, thick if vert else -1)
        # push the outer part past the frame boundary
        if "left" in parts: w.set_margin_start(-out)
        if "right" in parts: w.set_margin_end(-out)
        if "top" in parts: w.set_margin_top(-out)
        if "bottom" in parts: w.set_margin_bottom(-out)
        if not corner:   # leave the corners to the corner handles
            for setter in (("set_margin_top", "set_margin_bottom") if horiz else ("set_margin_start", "set_margin_end")):
                getattr(w, setter)(CORNER - CORNER_OUT)
        return w

    def _pin(self, side: str) -> Gtk.Widget:
        w = Gtk.Box()
        w.add_css_class("maestri-pin")
        setattr(w, _ROLE, ("pin", (self.node_id, side)))
        w.set_size_request(14, 14)
        w.set_halign(Gtk.Align.START if side == "left" else Gtk.Align.END)
        w.set_valign(Gtk.Align.CENTER)
        w.set_margin_start(-7 if side == "left" else 0)
        w.set_margin_end(-7 if side == "right" else 0)
        return w


CSS = b"""
.maestri-node { background: @window_bg_color; border: 1px solid alpha(currentColor, .25); border-radius: 8px; }
.maestri-node.selected { border: 2px dashed #3b82f6; }
.maestri-node-header { background: alpha(currentColor, .06); padding: 0 8px; border-radius: 7px 7px 0 0; }
.maestri-pin { background: #3b82f6; border: 2px solid white; border-radius: 50%; opacity: 0; transition: opacity 120ms; }
.maestri-node:hover .maestri-pin, .maestri-connecting .maestri-pin { opacity: 1; }
"""


class CanvasWidget(Gtk.Overlay):
    """Infinite canvas. Public API: add_node / remove_node / update_node_frame / add_connection / signals below."""
    __gtype_name__ = "MaestriCanvasWidget"
    __gsignals__ = {
        "node-moved": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float, bool)),   # id, x, y, w, h, final
        "node-resized": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float, bool)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),                          # tuple[str]
        "connect-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str, str)),               # a, side_a, b, side_b
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "duplicate-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),                        # {id: Rect}
        "draw-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float)),     # kind, frame
        "tool-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "viewport-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float, float)),
    }

    def __init__(self, scene: Scene | None = None, viewport: Viewport | None = None) -> None:
        super().__init__()
        self.scene = scene or Scene()
        self.viewport = viewport or Viewport()
        self.controller = CanvasController(self.scene, self.viewport)
        self.controller.on_event = self._on_event
        self.renderer = CairoRenderer()
        self.frames: dict[str, NodeFrame] = {}
        self._pointer = Point(0, 0)
        self._last_applied: dict[str, tuple] = {}
        self._pinch_prev = 1.0
        self._stack: list[NodeFrame] = []
        self._install_css()
        self.layer = CairoLayer(self)
        self.set_child(self.layer)
        self.fixed = Gtk.Fixed()
        self.add_overlay(self.fixed)
        self.top_layer = CairoLayer(self, top=True)
        self.add_overlay(self.top_layer)
        self.set_focusable(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)

        drag = Gtk.GestureDrag(button=0)                        # any button
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        self._drag_gesture = drag

        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self._motion)
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        self._scroll_ctl = scroll

        zoom = Gtk.GestureZoom()
        zoom.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        zoom.connect("begin", lambda *_: setattr(self, "_pinch_prev", 1.0))
        zoom.connect("scale-changed", self._pinch)
        self.add_controller(zoom)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        keys.connect("key-released", lambda _c, kv, *_: self.controller.key_release((Gdk.keyval_name(kv) or "").lower()))
        self.add_controller(keys)

        self.add_tick_callback(self._tick)

    # ---- public API ---------------------------------------------------------------------------
    def add_node(self, item: NodeItem, content: Gtk.Widget | None = None) -> None:
        self.scene.add_node(item)
        self.controller.dirty = True
        if item.kind in WIDGET_KINDS and content is not None:
            frame = NodeFrame(item, content, on_close=lambda i: self.emit("delete-requested", (i,)))
            self.frames[item.id] = frame
            self.fixed.put(frame, 0, 0)

    def remove_node(self, node_id: str) -> None:
        k = norm(node_id)
        frame = self.frames.pop(k, None)
        if frame is not None:
            self.fixed.remove(frame)
        for c in self.scene.connections_of({k}):
            self.controller.ropes.remove(c.id)
        self.scene.remove_node(k)
        self._last_applied.pop(k, None)
        self.controller.dirty = True

    def update_node_frame(self, node_id: str, frame: Rect) -> None:
        self.scene.set_frame(node_id, frame)
        self.controller._reanchor({norm(node_id)})
        self.controller.dirty = True

    def add_connection(self, conn: ConnItem) -> None:
        self.controller.add_connection(conn)

    def set_tool(self, tool: str) -> None:
        self.controller.set_tool(tool)

    # ---- engine -> GTK ---------------------------------------------------------------------------
    def _on_event(self, e: Event) -> None:
        if isinstance(e, NodesMoved):
            for i, r in e.frames.items():
                self.emit("node-moved", i, r.x, r.y, r.w, r.h, e.final)
        elif isinstance(e, NodeResized):
            self.emit("node-resized", e.id, e.frame.x, e.frame.y, e.frame.w, e.frame.h, e.final)
        elif isinstance(e, SelectionChanged):
            self._sync_selection_css()
            self.emit("selection-changed", e.ids)
        elif isinstance(e, ConnectRequested):
            self.emit("connect-requested", e.a, e.side_a, e.b, e.side_b)
        elif isinstance(e, DeleteRequested):
            self.emit("delete-requested", e.ids)
        elif isinstance(e, DuplicateRequested):
            self.emit("duplicate-requested", e.frames)
        elif isinstance(e, DrawRequested):
            self.emit("draw-requested", e.kind, e.frame.x, e.frame.y, e.frame.w, e.frame.h)
        elif isinstance(e, ToolChanged):
            self.emit("tool-changed", e.tool)
        elif isinstance(e, ViewportChanged):
            self.emit("viewport-changed", e.x, e.y, e.zoom)

    def _sync_selection_css(self) -> None:
        for i, f in self.frames.items():
            (f.add_css_class if i in self.scene.selection else f.remove_css_class)("selected")

    def _tick(self, _w: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        w, h = self.get_width(), self.get_height()
        self.controller.size = (float(w), float(h))
        moving = self.controller.ropes.tick()
        self._place_nodes(w, h)
        connecting = self.controller.overlay.connecting is not None
        if connecting != self.has_css_class("maestri-connecting"):   # reveal every pin while a rope is being dragged
            (self.add_css_class if connecting else self.remove_css_class)("maestri-connecting")
        if self.controller.dirty or moving:
            self.controller.dirty = False
            self.layer.queue_draw()
            self.top_layer.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _place_nodes(self, w: int, h: int) -> None:
        """Position/scale node widgets. Skips GTK calls when nothing changed; hides nodes outside view+margin (saves terminal rendering)."""
        vp = self.viewport
        view = vp.visible_rect(w, h)
        for i, frame in self.frames.items():
            item = self.scene.nodes[i]
            visible = view.intersects(item.frame)
            if frame.get_visible() != visible:
                frame.set_visible(visible)
            if not visible:
                continue
            key = (item.frame, vp.origin, vp.zoom, item.z)
            if self._last_applied.get(i) == key:
                continue
            self._last_applied[i] = key
            f = item.frame
            frame.set_size_request(round_half_away(f.w), round_half_away(f.h))
            t = Gsk.Transform.new().translate(Graphene.Point().init((f.x - vp.origin.x) * vp.zoom, (f.y - vp.origin.y) * vp.zoom)).scale(vp.zoom, vp.zoom)
            self.fixed.set_child_transform(frame, t)
        # stacking follows z: reorder only when the order actually differs
        order = [self.frames[n.id] for n in self.scene.ascending() if n.id in self.frames]
        if order != self._stack:
            prev = None
            for fr in order:
                fr.insert_after(self.fixed, prev)
                prev = fr
            self._stack = order

    # ---- GTK -> engine ---------------------------------------------------------------------------
    @staticmethod
    def _mods(state: Gdk.ModifierType) -> Mods:
        m = Mods.NONE
        if state & Gdk.ModifierType.SHIFT_MASK: m |= Mods.SHIFT
        if state & Gdk.ModifierType.CONTROL_MASK: m |= Mods.CTRL
        if state & Gdk.ModifierType.ALT_MASK: m |= Mods.ALT
        return m

    def target_at(self, x: float, y: float) -> Target:
        """Resolve what is under (x, y) via GTK picking (transform-aware). Chrome widgets carry role tags; anything else inside a node is content."""
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self:
            role = getattr(w, _ROLE, None)
            if role:
                kind, payload = role
                if kind == "header": return NodeHeader(payload)
                if kind == "edge": return NodeEdge(*payload)
                if kind == "pin": return NodePin(*payload)
                if kind == "content": return NodeContent(payload)
            if isinstance(w, NodeFrame):
                return NodeContent(w.node_id)   # padding/border of a frame that is not tagged: leave it to GTK
            w = w.get_parent()
        return Background()

    def _drag_begin(self, g: Gtk.GestureDrag, x: float, y: float) -> None:
        self.grab_focus() if not self._focus_in_node() else None
        ev = g.get_current_event()
        mods = self._mods(ev.get_modifier_state() if ev else 0)
        claimed = self.controller.press(Point(x, y), g.get_current_button(), mods, self.target_at(x, y))
        g.set_state(Gtk.EventSequenceState.CLAIMED if claimed else Gtk.EventSequenceState.DENIED)
        self._apply_cursor()

    def _drag_update(self, g: Gtk.GestureDrag, ox: float, oy: float) -> None:
        ok, sx, sy = g.get_start_point()
        ev = g.get_current_event()
        self.controller.move(Point(sx + ox, sy + oy), self._mods(ev.get_modifier_state() if ev else 0))
        self._apply_cursor()

    def _drag_end(self, g: Gtk.GestureDrag, ox: float, oy: float) -> None:
        ok, sx, sy = g.get_start_point()
        ev = g.get_current_event()
        self.controller.release(Point(sx + ox, sy + oy), self._mods(ev.get_modifier_state() if ev else 0))
        self._apply_cursor()

    def _motion(self, _c: Gtk.EventControllerMotion, x: float, y: float) -> None:
        self._pointer = Point(x, y)
        if not self.controller.busy:
            t = self.target_at(x, y)
            self.set_cursor_from_name({NodeHeader: "grab", NodePin: "crosshair"}.get(type(t)) or (t.edge.cursor if isinstance(t, NodeEdge) else
                                      ("crosshair" if self.controller.tool != "select" and isinstance(t, Background) else
                                       ("grab" if self.controller.space_held else None))))

    def _apply_cursor(self) -> None:
        if self.controller.cursor:
            self.set_cursor_from_name(self.controller.cursor)

    def _scroll(self, c: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        ev = c.get_current_event()
        mods = self._mods(ev.get_modifier_state() if ev else 0)
        # Over a node's content a plain scroll belongs to that widget (terminal scrollback, text view): only Ctrl zooms there.
        if not mods & Mods.CTRL and isinstance(self.target_at(self._pointer.x, self._pointer.y), (NodeContent,)):
            return False
        if c.get_unit() == Gdk.ScrollUnit.WHEEL:
            dx, dy = dx * WHEEL_NOTCH_PX, dy * WHEEL_NOTCH_PX
        return self.controller.scroll(self._pointer, dx, dy, mods)

    def _pinch(self, _g: Gtk.GestureZoom, scale: float) -> None:
        self.controller.pinch(self._pointer, scale / self._pinch_prev)   # scale is cumulative since 'begin'
        self._pinch_prev = scale

    def _focus_in_node(self) -> bool:
        root = self.get_root()
        f = root.get_focus() if root else None
        return f is not None and f is not self and f.is_ancestor(self)

    def _key_pressed(self, _c: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        name = (Gdk.keyval_name(keyval) or "").lower()
        return self.controller.key(name, self._mods(state), canvas_focused=not self._focus_in_node())

    def _install_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
