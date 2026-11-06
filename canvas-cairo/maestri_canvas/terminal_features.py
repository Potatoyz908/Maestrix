"""Terminal conveniences shared by the local VTE terminal and the core-backed `VteTerminalView` (audit part 2.B, Sprint 9 §4 Arquiteto 1).

`TerminalFeatures(term, frame)` is installed by `NodeFrame` for every node whose content has a Vte.Terminal (`terminal_of`):

* keyboard: Ctrl+Shift+C copy, Ctrl+Shift+V paste, Ctrl+Shift+A select all, Ctrl+Shift+F find (VTE-GTK4 binds none of them);
* find bar: overlay on the node (entry + previous / next / close; Enter = next, Shift+Enter = previous, Esc = close);
* clickable links: URLs matched by regex and OSC 8 hyperlinks, opened with Ctrl+click through `open_uri` (module level, mockable);
* bell: an outline bell badge in the header until the terminal gets the keyboard again;
* busy / idle: output marks the terminal busy, 1.5 s of silence idle (header dot + `on_activity(bool)`);
* exited: `set_exited(code)` shows "exited N" in the header (local shell exit or the core's `terminalExited` event).

Pure helpers (`shortcut_action`, `URL_PATTERN`, `search_pattern`) are unit-tested without a display.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

# PCRE2 compile flags (VTE regexes are PCRE2; Vte requires MULTILINE for match/search regexes).
PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400
PCRE2_UTF = 0x00080000

IDLE_AFTER_MS = 1500
# scheme://... up to whitespace/quotes/brackets, not ending in punctuation that usually closes a sentence.
URL_PATTERN = r"(?:https?|ftp|file)://[^\s<>\"'`]*[^\s<>\"'`.,;:!?)\]}]"

_SHORTCUTS = {"c": "copy", "v": "paste", "a": "select-all", "f": "find"}


def shortcut_action(keyval_name: str, ctrl: bool, shift: bool) -> Optional[str]:
    """Ctrl+Shift+<key> -> action name. GDK reports the shifted keyval ('C'), so the name is lower-cased first."""
    if not (ctrl and shift):
        return None
    return _SHORTCUTS.get((keyval_name or "").lower())


def search_pattern(text: str) -> str:
    """Literal, case-insensitive search: the user's text is escaped, never interpreted as a regex."""
    return re.escape(text)


def open_uri(uri: str, parent: Optional[Gtk.Widget] = None) -> None:
    """Open a link from the terminal in the default handler. Replaced in tests."""
    try:
        launcher = Gtk.UriLauncher.new(uri)
        root = parent.get_root() if parent is not None else None
        launcher.launch(root if isinstance(root, Gtk.Window) else None, None, None, None)
    except (AttributeError, TypeError, GLib.Error):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            pass


def _vte():
    from gi.repository import Vte   # the caller already loaded Vte 3.91
    return Vte


class TerminalSearchBar(Gtk.Box):
    """Find bar shown over the terminal (top-right). Uses VTE's own search (scrollback included, wraps around)."""

    def __init__(self, features: "TerminalFeatures") -> None:
        super().__init__(spacing=2, halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=6, margin_end=10,
                         css_classes=["maestri-term-search"])
        self.features = features
        self.entry = Gtk.SearchEntry(placeholder_text="Find", width_chars=18)
        self.entry.connect("search-changed", lambda _e: self.features.find(self.entry.get_text(), backwards=False, restart=True))
        self.entry.connect("activate", lambda _e: self.features.find(self.entry.get_text(), backwards=False))
        self.entry.connect("stop-search", lambda _e: self.features.close_search())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.entry.add_controller(keys)
        self.status = Gtk.Label(css_classes=["dim-label"], margin_start=4, margin_end=4)
        self.prev_btn = Gtk.Button(icon_name="maestri-arrow-up-symbolic", tooltip_text="Previous (Shift+Enter)",
                                   css_classes=["flat"])
        self.next_btn = Gtk.Button(icon_name="maestri-arrow-down-symbolic", tooltip_text="Next (Enter)", css_classes=["flat"])
        self.close_btn = Gtk.Button(icon_name="maestri-close-symbolic", tooltip_text="Close (Esc)", css_classes=["flat"])
        self.prev_btn.connect("clicked", lambda _b: self.features.find(self.entry.get_text(), backwards=True))
        self.next_btn.connect("clicked", lambda _b: self.features.find(self.entry.get_text(), backwards=False))
        self.close_btn.connect("clicked", lambda _b: self.features.close_search())
        for w in (self.entry, self.status, self.prev_btn, self.next_btn, self.close_btn):
            self.append(w)

    def _key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.features.find(self.entry.get_text(), backwards=True)
            return True
        if keyval == Gdk.KEY_Escape:
            self.features.close_search()
            return True
        return False


class TerminalFeatures:
    def __init__(self, term, frame, on_activity: Optional[Callable[[bool], None]] = None,
                 open_link: Optional[Callable[[str], None]] = None) -> None:
        self.term, self.frame = term, frame
        self.on_activity = on_activity
        self.open_link = open_link
        self.option_as_meta = False
        self.busy = False
        self.exited: Optional[int] = None
        self._idle_timer = 0
        self._search_bar: Optional[TerminalSearchBar] = None
        self._search_text = ""
        self._handlers: list[int] = []

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)   # before VTE turns the chord into bytes for the shell
        keys.connect("key-pressed", self._key_pressed)
        term.add_controller(keys)

        self._install_links()
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._clicked)
        term.add_controller(click)

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _c: self.clear_bell())
        term.add_controller(focus)

        self._handlers.append(term.connect("bell", lambda _t: self.ring_bell()))
        self._handlers.append(term.connect("contents-changed", lambda _t: self._output()))

    # ---- clipboard ---------------------------------------------------------------------------------------------
    def copy(self) -> None:
        Vte = _vte()
        if self.term.get_has_selection():
            self.term.copy_clipboard_format(Vte.Format.TEXT)

    def paste(self) -> None:
        self.term.paste_clipboard()   # bracketed paste when the program asked for it; goes out through "commit"

    def select_all(self) -> None:
        self.term.select_all()

    def _key_pressed(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        action = shortcut_action(Gdk.keyval_name(keyval) or "", bool(state & Gdk.ModifierType.CONTROL_MASK),
                                 bool(state & Gdk.ModifierType.SHIFT_MASK))
        if action is None:
            if self.option_as_meta and state & Gdk.ModifierType.ALT_MASK and not state & Gdk.ModifierType.CONTROL_MASK:
                codepoint = Gdk.keyval_to_unicode(keyval)
                if codepoint >= 32:
                    text = "\x1b" + chr(codepoint)
                    transport = getattr(self.frame.content, "transport", None)
                    if transport is not None:
                        transport.write(text)
                    else:
                        self.term.feed_child(text.encode("utf-8"))
                    return True
            return False
        {"copy": self.copy, "paste": self.paste, "select-all": self.select_all, "find": self.open_search}[action]()
        return True

    # ---- links -------------------------------------------------------------------------------------------------
    def _install_links(self) -> None:
        Vte = _vte()
        try:
            self.term.set_allow_hyperlink(True)   # OSC 8
        except AttributeError:
            pass
        try:
            regex = Vte.Regex.new_for_match(URL_PATTERN, -1, PCRE2_MULTILINE | PCRE2_UTF | PCRE2_CASELESS)
            tag = self.term.match_add_regex(regex, 0)
            self.term.match_set_cursor_name(tag, "pointer")
        except (GLib.Error, TypeError, AttributeError):
            pass

    def link_at(self, x: float, y: float) -> Optional[str]:
        """OSC 8 hyperlink under (x, y) first, else a regex-matched URL."""
        try:
            uri = self.term.check_hyperlink_at(x, y)
        except (AttributeError, TypeError):
            uri = None
        if uri:
            return uri
        try:
            res = self.term.check_match_at(x, y)
            match = res[0] if isinstance(res, tuple) else res
        except (AttributeError, TypeError):
            match = None
        return match or None

    def _clicked(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        state = gesture.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return
        uri = self.link_at(x, y)
        if uri:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            (self.open_link or (lambda u: open_uri(u, self.term)))(uri)

    # ---- find --------------------------------------------------------------------------------------------------
    def open_search(self) -> TerminalSearchBar:
        if self._search_bar is None:
            self._search_bar = TerminalSearchBar(self)
            self.frame.add_overlay(self._search_bar)
            role = getattr(self.frame, "tag", None)
            if role is not None:
                for w in (self._search_bar, self._search_bar.entry, self._search_bar.prev_btn, self._search_bar.next_btn,
                          self._search_bar.close_btn):
                    role(w, "content")   # real controls: the canvas gesture leaves their clicks alone
        self._search_bar.set_visible(True)
        sel = self.term.get_text_selected(_vte().Format.TEXT) if self.term.get_has_selection() else None
        if sel and "\n" not in sel:
            self._search_bar.entry.set_text(sel)
        self._search_bar.entry.grab_focus()
        return self._search_bar

    def close_search(self) -> None:
        if self._search_bar is not None:
            self._search_bar.set_visible(False)
        try:
            self.term.search_set_regex(None, 0)
        except (TypeError, GLib.Error):
            pass
        self._search_text = ""
        self.term.grab_focus()

    def find(self, text: str, backwards: bool = False, restart: bool = False) -> bool:
        """Highlight the next (or previous) occurrence of `text` (literal, case-insensitive). Returns whether one was found."""
        Vte = _vte()
        if not text:
            self._set_status("")
            return False
        if text != self._search_text or restart:
            try:
                regex = Vte.Regex.new_for_search(search_pattern(text), -1, PCRE2_MULTILINE | PCRE2_UTF | PCRE2_CASELESS)
            except GLib.Error:
                return False
            self.term.search_set_regex(regex, 0)
            self.term.search_set_wrap_around(True)
            self._search_text = text
            if restart:
                self.term.unselect_all()
        found = self.term.search_find_previous() if backwards else self.term.search_find_next()
        self._set_status("" if found else "Not found")
        return bool(found)

    def _set_status(self, text: str) -> None:
        if self._search_bar is not None:
            self._search_bar.status.set_label(text)

    # ---- bell / activity / exit --------------------------------------------------------------------------------
    def ring_bell(self) -> None:
        if self.term.has_focus():
            return
        self.frame.set_badge("bell", "", tooltip="Bell: this terminal needs attention", css=("maestri-badge-attention",),
                             icon="maestri-bell-symbolic")

    def clear_bell(self) -> None:
        if self.frame.badge("bell") is not None:
            self.frame.set_badge("bell", None)

    def _output(self) -> None:
        if self.exited is not None:
            return
        if not self.busy:
            self._set_busy(True)
        if self._idle_timer:
            GLib.source_remove(self._idle_timer)
        self._idle_timer = GLib.timeout_add(IDLE_AFTER_MS, self._went_idle)

    def _went_idle(self) -> bool:
        self._idle_timer = 0
        self._set_busy(False)
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.frame.set_badge("status", "", tooltip="Busy" if busy else "Idle",
                             css=("maestri-term-status", "busy" if busy else "idle"), icon="maestri-circle-symbolic")
        if self.on_activity is not None:
            self.on_activity(busy)

    def set_exited(self, code: Optional[int]) -> None:
        """Show (code int) or clear (None) the "process exited" indicator."""
        self.exited = code
        if code is None:
            self.frame.set_badge("exited", None)
            return
        if self._idle_timer:
            GLib.source_remove(self._idle_timer)
            self._idle_timer = 0
        if self.busy:
            self._set_busy(False)
        self.frame.set_badge("exited", f"exited {code}", tooltip=f"The process exited with status {code}",
                             css=("maestri-badge-exited",))

    def cleanup(self) -> None:
        if self._idle_timer:
            GLib.source_remove(self._idle_timer)
            self._idle_timer = 0
        for hid in self._handlers:
            try:
                if self.term.handler_is_connected(hid):
                    self.term.disconnect(hid)
            except TypeError:
                pass
        self._handlers.clear()
