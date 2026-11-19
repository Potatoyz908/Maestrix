"""Maestrix mascot: a small animated conductor that appears while the main window is minimized and tells you when an agent
finished its task (Settings > General > "Mascot when minimized").

The frames are transparent PNGs in ui/assets/mascot (idle_*.png, alert_*.png; scripts/gen_mascot_frames.py draws the default
ones). Drag the mascot to move it, click it to bring Maestrix back, close it with the x (it comes back on the next minimize).
Minimized = Gdk.ToplevelState.MINIMIZED, or SUSPENDED on Wayland compositors (xdg-shell has no "minimized" state).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from gi.repository import Gdk, GLib, Gtk

ASSETS = Path(__file__).resolve().parent / "assets" / "mascot"
FRAME_MS = 90
BUBBLE_MS = 9000
SIZE = 112

CSS = """
window.maestri-mascot, window.maestri-mascot > contents { background: transparent; box-shadow: none; border: none; }
.maestri-mascot-bubble { background: #182239; color: #edf1ff; border: 1px solid #71dcec; border-radius: 12px;
  padding: 6px 10px; font-size: 12px; }
.maestri-mascot-close { min-width: 20px; min-height: 20px; padding: 0; border-radius: 10px; background: rgba(12, 15, 27, 0.75);
  color: #edf1ff; opacity: 0; transition: opacity 120ms; }
.maestri-mascot-stage:hover .maestri-mascot-close { opacity: 1; }
"""


def _load_frames(kind: str) -> list[Gdk.Texture]:
    out = []
    for p in sorted(ASSETS.glob(f"{kind}_*.png")):
        try:
            out.append(Gdk.Texture.new_from_filename(str(p)))
        except GLib.Error:
            continue
    return out


def is_minimized(state: Gdk.ToplevelState) -> bool:
    hidden = Gdk.ToplevelState.MINIMIZED
    suspended = getattr(Gdk.ToplevelState, "SUSPENDED", None)   # GTK >= 4.12
    if suspended is not None:
        hidden |= suspended
    return bool(state & hidden)


def finished_message(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return f"{names[0]} terminou a tarefa"
    return f"{', '.join(names[:-1])} e {names[-1]} terminaram"


class MascotWindow(Gtk.Window):
    def __init__(self, main_window: Gtk.Window) -> None:
        super().__init__(decorated=False, resizable=False, title="Maestrix", css_classes=["maestri-mascot"])
        self.main_window = main_window
        self.idle, self.alert = _load_frames("idle"), _load_frames("alert")
        self._frame = 0
        self._alerting = False
        self._tick = 0
        self._bubble_timer = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.bubble = Gtk.Label(wrap=True, max_width_chars=22, justify=Gtk.Justification.CENTER, visible=False,
                                css_classes=["maestri-mascot-bubble"])
        box.append(self.bubble)

        stage = Gtk.Overlay(css_classes=["maestri-mascot-stage"])
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, width_request=SIZE, height_request=SIZE)
        self.picture.set_tooltip_text("Arraste para mover · clique para abrir o Maestrix")
        stage.set_child(self.picture)
        close = Gtk.Button(icon_name="window-close-symbolic", halign=Gtk.Align.END, valign=Gtk.Align.START,
                           css_classes=["maestri-mascot-close"], tooltip_text="Fechar o mascote")
        close.connect("clicked", lambda _b: self.dismiss())
        stage.add_overlay(close)
        box.append(stage)
        self.set_child(box)

        drag = Gtk.GestureDrag()   # drag anywhere on the mascot: the compositor moves the window (works on Wayland too)
        drag.connect("drag-begin", lambda _g, x, y: self._drag_begin(x, y))
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.picture.add_controller(drag)
        self._press: Optional[tuple[float, float]] = None
        self._moved = False

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._show_frame()

    # ---- animation -----------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self._tick:
            self._tick = GLib.timeout_add(FRAME_MS, self._next_frame)

    def stop(self) -> None:
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _frames(self) -> list[Gdk.Texture]:
        return (self.alert if self._alerting and self.alert else self.idle) or self.alert

    def _next_frame(self) -> bool:
        self._frame += 1
        self._show_frame()
        return GLib.SOURCE_CONTINUE

    def _show_frame(self) -> None:
        frames = self._frames()
        if frames:
            self.picture.set_paintable(frames[self._frame % len(frames)])

    # ---- behaviour -----------------------------------------------------------------------------------------------
    def say(self, text: str) -> None:
        """Speech bubble + the excited animation for a while."""
        self.bubble.set_label(text)
        self.bubble.set_visible(True)
        self._alerting = True
        if self._bubble_timer:
            GLib.source_remove(self._bubble_timer)
        self._bubble_timer = GLib.timeout_add(BUBBLE_MS, self._calm_down)

    def _calm_down(self) -> bool:
        self._bubble_timer = 0
        self._alerting = False
        self.bubble.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _drag_begin(self, x: float, y: float) -> None:
        self._press, self._moved = (x, y), False

    def _drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        """Past a few pixels the press becomes a window move (the compositor moves it, Wayland included)."""
        if self._moved or self._press is None or dx * dx + dy * dy < 16:
            return
        surface, device = self.get_surface(), gesture.get_device()
        if isinstance(surface, Gdk.Toplevel) and device is not None:
            self._moved = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            ok, x, y = self.picture.translate_coordinates(self, *self._press)
            if not ok:
                x, y = self._press
            surface.begin_move(device, gesture.get_current_button() or 1, x, y, gesture.get_current_event_time())

    def _drag_end(self, _gesture: Gtk.GestureDrag, _dx: float, _dy: float) -> None:
        if not self._moved:   # a plain click brings Maestrix back
            self.restore_main()
        self._press = None

    def restore_main(self) -> None:
        self.main_window.present()

    def dismiss(self) -> None:
        self.stop()
        self.set_visible(False)


class MascotController:
    """Shows the mascot while the main window is minimized (if enabled) and forwards "agent finished" notices to it."""

    def __init__(self, window: Gtk.Window, enabled: bool = False) -> None:
        self.window = window
        self.enabled = enabled
        self.mascot: Optional[MascotWindow] = None
        self.minimized = False
        self._surface_handler = 0
        if window.get_realized():
            self._watch()
        else:
            window.connect("realize", lambda *_: self._watch())

    def _watch(self) -> None:
        surface = self.window.get_surface()
        if isinstance(surface, Gdk.Toplevel) and not self._surface_handler:
            self._surface_handler = surface.connect("notify::state", lambda s, _p: self._state_changed(s.get_state()))

    def _state_changed(self, state: Gdk.ToplevelState) -> None:
        minimized = is_minimized(state)
        if minimized == self.minimized:
            return
        self.minimized = minimized
        if minimized:
            self.show()
        else:
            self.hide()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.hide()
        elif self.minimized:
            self.show()

    def show(self) -> None:
        if not self.enabled:
            return
        if self.mascot is None:
            self.mascot = MascotWindow(self.window)
            self.mascot.set_application(self.window.get_application())
        self.mascot.start()
        self.mascot.present()

    def hide(self) -> None:
        if self.mascot is not None:
            self.mascot.dismiss()

    def agents_finished(self, names: Iterable[str]) -> None:
        names = [n for n in names if n]
        if not names or not self.enabled or self.mascot is None or not self.mascot.get_visible():
            return
        self.mascot.say(finished_message(names))

    def destroy(self) -> None:
        if self.mascot is not None:
            self.mascot.stop()
            self.mascot.destroy()
            self.mascot = None
