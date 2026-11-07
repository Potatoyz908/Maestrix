"""Inline editor for the text of canvas-drawn nodes (a text label, or the caption inside a shape).

Drawn nodes have no widget of their own (Cairo paints them), so editing pops a `Gtk.Popover` with a `Gtk.TextView` over the node.
Enter commits (Shift+Enter = new line), Esc cancels, clicking elsewhere commits. `on_commit(text)` runs only when the text changed.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


class DrawnTextEditor:
    def __init__(self, parent: Gtk.Widget) -> None:
        self.parent = parent
        self.node_id: str | None = None
        self._original = ""
        self._on_commit: Callable[[str], None] | None = None
        self._cancelled = False

        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.view.add_css_class("maestri-drawn-editor")
        scroller = Gtk.ScrolledWindow(child=self.view, propagate_natural_height=True, max_content_height=400)
        self.popover = Gtk.Popover(child=scroller, has_arrow=False, autohide=True)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.set_parent(parent)
        self.popover.connect("closed", self._closed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.view.add_controller(keys)

    @property
    def active(self) -> bool:
        return self.node_id is not None

    def open(self, node_id: str, text: str, screen_rect: tuple[float, float, float, float], on_commit: Callable[[str], None]) -> None:
        """`screen_rect` = the node's (x, y, w, h) in parent coordinates; the editor sits over it."""
        if self.active:
            self.popover.popdown()   # commits the previous edit first
        self.node_id, self._original, self._on_commit, self._cancelled = node_id, text, on_commit, False
        buf = self.view.get_buffer()
        buf.set_text(text)
        buf.select_range(buf.get_start_iter(), buf.get_end_iter())
        x, y, w, h = screen_rect
        self.view.set_size_request(max(160, int(w)), max(32, int(h)))
        rect = Gdk.Rectangle()
        # point at the node's top edge: the popover opens below it, covering the label
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), max(1, int(w)), 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()
        self.view.grab_focus()

    def text(self) -> str:
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def commit(self) -> None:
        self.popover.popdown()

    def cancel(self) -> None:
        self._cancelled = True
        self.popover.popdown()

    def _key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self.commit()
            return True
        return False

    def _closed(self, *_a) -> None:
        if self.node_id is None:
            return
        text, cb, changed = self.text(), self._on_commit, not self._cancelled and self.text() != self._original
        self.node_id, self._on_commit = None, None
        if changed and cb is not None:
            cb(text)

    def dispose(self) -> None:
        self.popover.unparent()
