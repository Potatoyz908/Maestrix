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
from gi.repository import Gtk, Pango, Vte

from .file_dnd import install_terminal_drop
from .pty_transport import PtyTransport
from .replay import normalize_replay
from .terminal_themes import THEMES, apply_terminal_style

BG = THEMES["dark"].bg
FG = THEMES["dark"].fg
PALETTE = list(THEMES["dark"].palette)


class VteTerminalView(Gtk.ScrolledWindow):
    """`canvas.add_node(item, VteTerminalView(transport))`. Takes ownership of `transport` (closed on `dispose`)."""

    def __init__(self, transport: PtyTransport, scrollback_lines: int = 5000, font: str = "Monospace 11",
                 theme: str = "dark") -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.term = Vte.Terminal()
        self.term.set_scrollback_lines(scrollback_lines)
        self.term.set_scroll_on_output(True)
        self.term.set_scroll_on_keystroke(True)
        self.term.set_mouse_autohide(True)
        self.term.set_size_request(80, 40)   # keep the node shrinkable (VTE's natural 80x24 must not become a minimum)
        fd = Pango.FontDescription.from_string(font)
        apply_terminal_style(self.term, fd.get_family(), (fd.get_size() / Pango.SCALE) or None, theme)
        self.set_child(self.term)
        self.disposed = False

        self.transport = transport
        self._size: tuple[int, int] = (0, 0)
        self._first_frame = True   # the first frame of each connection is the core's scrollback snapshot (see replay.py)
        transport.on_data = self._on_data
        transport.on_status = self._on_status
        self.term.connect("commit", lambda _t, text, _n: transport.write(text))   # typed keys + pastes
        self.term.add_tick_callback(self._watch_size)
        # Files dragged from a FileTreeView (or a file manager) are pasted into the shell as quoted paths, without Enter.
        # paste_text -> (bracketed paste if the shell asked for it) -> "commit" -> transport.write.
        self.drop_target = install_terminal_drop(self, self.term.paste_text, focus=self.term)

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

    def set_style(self, family: str | None, size: float | None, theme: str | None) -> None:
        apply_terminal_style(self.term, family, size, theme)

    def dispose(self) -> None:
        self.disposed = True
        self.transport.close()
