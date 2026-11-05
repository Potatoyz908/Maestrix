"""Terminal content widget for a `terminal` node: Vte.Terminal used purely as a DISPLAY.

The core owns the PTY and the shell (so `omaestri ask/check`, scrollback persistence and agent features keep working); VTE shows
the bytes the core streams (`feed`) and forwards what the user types (`commit`) back to the transport. VTE never spawns anything here.
"""
from __future__ import annotations

import gi

from .gi_env import ensure_typelib_paths

ensure_typelib_paths()
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gtk, Pango, Vte

from .pty_transport import PtyTransport
from .replay import normalize_replay

BG = "#1e1e1e"
FG = "#d4d4d4"
# VS Code Dark+ ANSI palette: the VTE default dark blue is nearly unreadable on #1e1e1e.
PALETTE = ["#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
           "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#e5e5e5"]


class VteTerminalView(Gtk.ScrolledWindow):
    """`canvas.add_node(item, VteTerminalView(transport))`. Takes ownership of `transport` (closed on `dispose`)."""

    def __init__(self, transport: PtyTransport, scrollback_lines: int = 5000, font: str = "Monospace 11") -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.term = Vte.Terminal()
        self.term.set_scrollback_lines(scrollback_lines)
        self.term.set_font(Pango.FontDescription(font))
        self.term.set_scroll_on_output(True)
        self.term.set_scroll_on_keystroke(True)
        self.term.set_mouse_autohide(True)
        fg, bg = Gdk.RGBA(), Gdk.RGBA()
        fg.parse(FG); bg.parse(BG)
        palette = []
        for hex_ in PALETTE:
            c = Gdk.RGBA(); c.parse(hex_); palette.append(c)
        self.term.set_colors(fg, bg, palette)
        self.term.set_bold_is_bright(True)   # "bold blue" (ls directories, prompts) uses the bright blue
        self.set_child(self.term)

        self.transport = transport
        self._size: tuple[int, int] = (0, 0)
        self._first_frame = True   # the first frame of each connection is the core's scrollback snapshot (see replay.py)
        transport.on_data = self._on_data
        transport.on_status = self._on_status
        self.term.connect("commit", lambda _t, text, _n: transport.write(text))   # typed keys + pastes
        self.term.add_tick_callback(self._watch_size)

    def _on_data(self, data: bytes) -> None:
        if self._first_frame:
            self._first_frame = False
            data = normalize_replay(data)
            if not data:
                return   # an empty-screen snapshot is just blank rows
        self.term.feed(data)

    def _on_status(self, status: str, reconnected: bool) -> None:
        if status == "open":
            self._first_frame = True
            # The core replays the saved buffer on EVERY connection: clear first or a reconnect would show the history twice.
            if reconnected:
                self.term.reset(True, True)

    def _watch_size(self, _w, _clock) -> bool:
        """VTE has no notify for its grid size: poll two ints per frame and tell the core (SIGWINCH) only when they change."""
        size = (self.term.get_column_count(), self.term.get_row_count())
        if size != self._size and size[0] > 0 and size[1] > 0:
            self._size = size
            self.transport.resize(*size)
        return True

    def text(self) -> str:
        """Scrollback + visible text (tests, `ask`-style readers). Vte >= 0.72 range API (get_text() is deprecated).
        Row numbers are absolute and keep counting across reset(), while the vadjustment bounds can be stale for a frame, so the range
        is deliberately over-wide and VTE clamps it to the rows that exist."""
        cols = max(self.term.get_column_count(), 1)
        out = self.term.get_text_range_format(Vte.Format.TEXT, 0, 0, 10_000_000, cols - 1)
        return (out[0] if isinstance(out, tuple) else out) or ""

    def dispose(self) -> None:
        self.transport.close()
