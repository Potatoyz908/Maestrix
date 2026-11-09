"""Floor switch animation: the canvas tilts back into a 3D stack of layers (Ground at the bottom, floors above), the stack
slides to the chosen layer, which then flattens back into the canvas (reference 09-floors.md: "the canvas repositions into a
3D space"). Pure overlay: it paints a live WidgetPaintable of the canvas, so nothing in the canvas itself is touched.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango

from maestri_core.signals.bus import bus

DURATION = 0.9          # seconds
TILT = 30.0             # degrees: a layer in the stack is rotated, then squashed into a parallelogram
SQUASH = 0.55           # vertical squash of a layer in the stack
LAYER_GAP = 0.2        # vertical distance between two layers, as a fraction of the canvas height
STACK_SCALE = 0.55


def _ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def _rgba(spec: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(spec)
    return c


def plan(t: float) -> tuple[float, float, float]:
    """(tilt amount 0..1, travel 0..1 from the old layer to the new one, overlay opacity) at time t in 0..1."""
    tilt = _ease(t / 0.3) if t < 0.3 else (1.0 if t < 0.7 else 1.0 - _ease((t - 0.7) / 0.3))
    travel = _ease((t - 0.25) / 0.5)
    fade = 1.0 if t < 0.92 else max(0.0, 1.0 - (t - 0.92) / 0.08)
    return tilt, travel, fade


class FloorTransition(Gtk.Widget):
    def __init__(self, window, controller) -> None:
        super().__init__(can_target=False, can_focus=False, visible=False, hexpand=True, vexpand=True)
        self.window, self.controller = window, controller
        self._paintable: Optional[Gtk.WidgetPaintable] = None
        self._names: list[str] = []
        self._from = self._to = 0
        self._start = 0.0
        self._tick = 0
        self._last = getattr(controller, "active_floor_id", None)
        self._handler = bus.connect("floor-switched", lambda _b, fid: self.play(fid))

    def dispose_bus(self) -> None:
        if self._handler:
            bus.disconnect(self._handler)
            self._handler = 0

    def _index(self, floor_id: Optional[str]) -> int:
        ids = [None] + [f.get("id") for f in (self.controller.floors or [])]
        return ids.index(floor_id) if floor_id in ids else 0

    def play(self, floor_id: Optional[str]) -> None:
        prev, self._last = self._last, floor_id
        if prev == floor_id or not self.window.canvas_widget.get_mapped():
            return
        if Gtk.Settings.get_default() is not None and not Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            return
        self._names = ["Ground"] + [str(f.get("name") or "Floor") for f in (self.controller.floors or [])]
        self._from, self._to = self._index(prev), self._index(floor_id)
        self._paintable = Gtk.WidgetPaintable.new(self.window.canvas_widget)
        self._start = time.monotonic()
        self.set_visible(True)
        if not self._tick:
            self._tick = self.add_tick_callback(self._on_tick)

    def _on_tick(self, _w, _clock) -> bool:
        if time.monotonic() - self._start >= DURATION:
            self._tick = 0
            self.set_visible(False)
            self._paintable = None
            return GLib.SOURCE_REMOVE
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    # ---- painting ----------------------------------------------------------------------------------------------------
    def do_snapshot(self, snap: Gtk.Snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0 or self._paintable is None:
            return
        t = (time.monotonic() - self._start) / DURATION
        tilt, travel, fade = plan(t)
        snap.push_opacity(fade)
        snap.append_color(_rgba("#0c0f1b"), Graphene.Rect().init(0, 0, w, h))   # backdrop while the layers fly
        pos = self._from + (self._to - self._from) * travel   # fractional layer index at the centre of the view
        order = sorted(range(len(self._names)), key=lambda i: -abs(i - pos))   # farthest first, the centred layer on top
        for i in order:
            self._layer(snap, w, h, i, pos, tilt)
        if tilt > 0.02:   # names on top of every layer
            scale = 1.0 - (1.0 - STACK_SCALE) * tilt
            for i in range(len(self._names)):
                cy = h / 2 + (pos - i) * h * (1.0 - tilt + LAYER_GAP * tilt)
                if -20 < cy < h + 20:
                    self._label(snap, i, tilt, w, cy, scale)
        snap.pop()

    def _layer(self, snap: Gtk.Snapshot, w: int, h: int, i: int, pos: float, tilt: float) -> None:
        current = abs(i - pos) < 0.5
        offset = (pos - i) * h * (1.0 - tilt + LAYER_GAP * tilt)   # Ground below, floors above; full-height apart when flat
        scale = 1.0 - (1.0 - STACK_SCALE) * tilt
        cx, cy = w / 2, h / 2 + offset
        snap.save()
        # 2D "layer" projection (works on every GSK renderer, cairo included): rotate, then squash vertically
        snap.translate(Graphene.Point().init(cx, cy))
        snap.scale(scale, scale * (1.0 - SQUASH * tilt))
        snap.rotate(-TILT * tilt)
        snap.translate(Graphene.Point().init(-w / 2, -h / 2))
        rect = Graphene.Rect().init(0, 0, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, 18 * tilt)
        snap.push_rounded_clip(rounded)
        snap.append_color(_rgba("#1c263c"), rect)
        if i in (self._from, self._to):
            snap.push_opacity(1.0 if current else 0.6)
            self._paintable.snapshot(snap, w, h)
            snap.pop()
        snap.pop()
        if tilt > 0.02:
            border = _rgba("#71dcec" if i == self._to else "#4a5a82")
            border.alpha = tilt
            bw = 3.0 / max(scale, 0.1)
            snap.append_border(rounded, [bw] * 4, [border] * 4)
        snap.restore()

    def _label(self, snap: Gtk.Snapshot, i: int, tilt: float, w: int, cy: float, scale: float) -> None:
        layout = self.create_pango_layout(self._names[i])
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 15"))
        _ink, logical = layout.get_pixel_extents()
        snap.save()
        x = w / 2 + w * scale * 0.5 * math.cos(math.radians(TILT)) + 64   # right of the layer's right corner
        snap.translate(Graphene.Point().init(min(x, w - logical.width - 16), cy - logical.height / 2))
        color = _rgba("#71dcec" if i == self._to else "#edf1ff")
        color.alpha = tilt
        snap.append_layout(layout, color)
        snap.restore()


def install_floor_transition(window, controller) -> FloorTransition:
    overlay = window.canvas_widget.get_parent()
    fx = FloorTransition(window, controller)
    if isinstance(overlay, Gtk.Overlay):
        overlay.add_overlay(fx)
    return fx
