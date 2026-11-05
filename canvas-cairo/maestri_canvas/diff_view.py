"""Colored diff view of a repository's uncommitted changes (spec: docs/reference/maestri-reference-index.md §7, "Diff 视图").

A read-only monospace Gtk.TextView: file headers bold on a tinted band, hunk headers in accent color, added lines green,
removed lines red, context dimmed. Lines are classified by `maestri_core.diffparse.classify` (hunk-aware, so content lines
starting with "+++"/"---" are not mistaken for headers).

Selecting text inside the diff enables "Ask agent": it emits `ask-requested(quote)` with the selection as a fenced block,
for the host to hand to a connected agent (reference: "select a code block -> chat icon -> ask an agent").
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango

from maestri_core.diffparse import classify, stat
from maestri_core.gitops import GitOps, GitResult

# (foreground, background, weight) per line kind; backgrounds are translucent so they read on light and dark themes.
TAG_STYLE = {
    "file": ("#e5e5e5", "rgba(120,120,120,0.35)", Pango.Weight.BOLD),
    "meta": ("#8a8a8a", None, Pango.Weight.NORMAL),
    "hunk": ("#3b8eea", "rgba(59,142,234,0.12)", Pango.Weight.NORMAL),
    "add": ("#23d18b", "rgba(35,209,139,0.14)", Pango.Weight.NORMAL),
    "del": ("#f14c4c", "rgba(241,76,76,0.14)", Pango.Weight.NORMAL),
    "context": (None, None, Pango.Weight.NORMAL),
}


class DiffView(Gtk.Box):
    """`DiffView(root)`; `reload()` re-runs `git diff` asynchronously (`is_loading()` while it runs)."""
    __gtype_name__ = "MaestriDiffView"
    __gsignals__ = {
        "ask-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "close-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, root: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("maestri-diff-view")
        self.git = GitOps(root)
        self._loading = False
        self._pending = False
        self.lines: list[tuple[str, str]] = []

        bar = Gtk.Box(spacing=4, margin_start=6, margin_end=2)
        bar.add_css_class("toolbar")
        title = Gtk.Label(label="Changes", xalign=0)
        title.add_css_class("heading")
        self.summary = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.summary.add_css_class("dim-label")
        self.btn_ask = Gtk.Button(icon_name="chat-message-new-symbolic", tooltip_text="Ask an agent about the selection",
                                  sensitive=False)
        self.btn_ask.add_css_class("flat")
        self.btn_ask.connect("clicked", lambda _b: self._ask())
        btn_reload = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload diff")
        btn_reload.add_css_class("flat")
        btn_reload.connect("clicked", lambda _b: self.reload())
        btn_close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Hide changes")
        btn_close.add_css_class("flat")
        btn_close.connect("clicked", lambda _b: self.emit("close-requested"))
        for w in (title, self.summary, self.btn_ask, btn_reload, btn_close):
            bar.append(w)

        self.buffer = Gtk.TextBuffer()
        for kind, (fg, bg, weight) in TAG_STYLE.items():
            props = {"weight": weight}
            if fg:
                props["foreground"] = fg
            if bg:
                props["paragraph-background"] = bg
            self.buffer.create_tag(kind, **props)
        self.buffer.connect("notify::has-selection", lambda b, _p: self.btn_ask.set_sensitive(b.get_has_selection()))
        self.text = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.NONE, left_margin=6, right_margin=6, top_margin=4, bottom_margin=4)
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.text)

        self.append(bar)
        self.append(Gtk.Separator())
        self.append(scroller)

    # ---- public API -------------------------------------------------------------------------------
    def set_root(self, root: str) -> None:
        self.git = GitOps(root, cache=self.git.cache)
        if self.get_visible():
            self.reload()

    def is_loading(self) -> bool:
        return self._loading

    def reload(self) -> None:
        if self._loading:
            self._pending = True   # coalesce: one more run after the current one
            return
        self._loading = True
        self.summary.set_label("loading…")
        self.git.diff(self._on_diff)

    def set_diff_text(self, diff_text: str) -> None:
        """Render a unified diff (also used directly by tests)."""
        self.lines = classify(diff_text)
        buf = self.buffer
        buf.set_text("", 0)
        it = buf.get_end_iter()
        for i, (kind, line) in enumerate(self.lines):
            buf.insert_with_tags_by_name(it, line + ("\n" if i + 1 < len(self.lines) else ""), kind)
        self.summary.set_label(str(stat(self.lines)))

    def selected_text(self) -> str:
        sel = self.buffer.get_selection_bounds()
        return self.buffer.get_text(sel[0], sel[1], False) if sel else ""

    # ---- internals --------------------------------------------------------------------------------
    def _on_diff(self, r: GitResult) -> None:
        self._loading = False
        if r.ok:
            self.set_diff_text(r.output)
            if not r.output.strip():
                self.buffer.set_text("Working tree clean.", -1)
        else:
            self.lines = []
            self.buffer.set_text(r.output or "git diff failed", -1)
            self.summary.set_label("error")
        if self._pending:
            self._pending = False
            self.reload()
        else:
            self.emit("loaded")

    def _ask(self) -> None:
        text = self.selected_text().strip("\n")
        if text:
            self.emit("ask-requested", f"```diff\n{text}\n```")
