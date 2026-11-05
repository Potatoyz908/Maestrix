"""FileTree node body: lazy-expanding tree with git badges, backed by FileTreeModel."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from maestri_core.filetree import FileTreeModel, FileEntry
from maestri_core.gitops import GitOps, GitResult
from maestri_core.signals.bus import bus
from maestri_core.models.node import FileTreeContent

_BADGE_CSS = {"M": "warning", "A": "success", "D": "error", "R": "accent", "?": "dim-label"}


class FileTreeWidget(Gtk.Box):
    def __init__(self, client, content: FileTreeContent) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.model = FileTreeModel(client, content)
        self.git = GitOps(content.root_path)
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self._build_toolbar())
        self._status = Gtk.Label(label="", xalign=0, wrap=True, margin_start=8, margin_end=8)
        self._status.add_css_class("dim-label")
        self.append(self._status)
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(self._box)
        self.append(scrolled)
        self._expanded: set = set()
        self.model.refresh_async(lambda: GLib.idle_add(self._rebuild))

    def _rebuild(self) -> bool:
        child = self._box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._box.remove(child)
            child = nxt
        if self.model.error and not self.model.entries(""):
            self._box.append(Gtk.Label(label=f"⚠ {self.model.error}", xalign=0, wrap=True, margin_start=8, margin_top=8))
        self._add_level("", 0)
        return GLib.SOURCE_REMOVE

    def _add_level(self, rel: str, depth: int) -> None:
        for e in self.model.entries(rel):
            self._box.append(self._row(e, depth))
            if e.is_dir and e.path in self._expanded:
                self._add_level(e.path, depth + 1)

    def _row(self, e: FileEntry, depth: int) -> Gtk.Widget:
        btn = Gtk.Button(has_frame=False)
        row = Gtk.Box(spacing=6, margin_start=8 + depth * 14)
        arrow = ("▾ " if e.path in self._expanded else "▸ ") if e.is_dir else "   "
        row.append(Gtk.Label(label=f"{arrow}{'📁' if e.is_dir else '📄'} {e.name}", xalign=0, hexpand=True))
        badge = self.model.badge_for(e)
        if badge:
            lbl = Gtk.Label(label=badge, margin_end=6)
            lbl.add_css_class(_BADGE_CSS.get(badge, "dim-label"))
            row.append(lbl)
        btn.set_child(row)
        if e.is_dir:
            btn.connect("clicked", lambda _b, p=e.path: self._toggle(p))
        return btn

    def _toggle(self, path: str) -> None:
        if path in self._expanded:
            self._expanded.discard(path)
            self._rebuild()
            return
        self._expanded.add(path)
        if path in self.model.children:
            self._rebuild()
        else:
            import threading
            def work():
                self.model.load_dir(path)
                GLib.idle_add(self._rebuild)
            threading.Thread(target=work, daemon=True).start()

    # -- git toolbar ---------------------------------------------------------
    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=4, margin_start=6, margin_end=6, margin_top=4)
        self._git_buttons = []
        for label, tip, handler in (
            ("⬇ Pull", "git pull --ff-only", lambda: self._run_git("pull")),
            ("⬆ Push", "git push", lambda: self._run_git("push")),
            ("⎇ Checkout", "Switch branch", self._choose_branch),
            ("📦 Stash", "git stash push -u", lambda: self._run_git("stash")),
            ("📤 Pop", "git stash pop", lambda: self._run_git("stash-pop")),
        ):
            b = Gtk.Button(label=label, tooltip_text=tip)
            b.connect("clicked", lambda _b, h=handler: h())
            bar.append(b)
            self._git_buttons.append(b)
        return bar

    def _set_busy(self, busy: bool, text: str = "") -> None:
        for b in self._git_buttons:
            b.set_sensitive(not busy)
        self._status.set_text(text)

    def _run_git(self, action: str, arg: str = None) -> None:
        self._set_busy(True, f"git {action}…")
        self.git.run(action, self._on_git_done, arg)

    def _on_git_done(self, r: GitResult) -> None:
        first = r.output.splitlines()[-1] if r.output else ""
        self._set_busy(False, ("✓ " if r.ok else "✗ ") + f"{r.action}: {first}")
        self.model.refresh_async(lambda: GLib.idle_add(self._rebuild))
        bus.emit("status-changed", f"git-{r.action}", r.ok)

    def _choose_branch(self) -> None:
        self._set_busy(True, "Listing branches…")
        self.git.list_branches(self._show_branch_popover)

    def _show_branch_popover(self, branches) -> None:
        self._set_busy(False)
        if not branches:
            self._status.set_text("✗ no branches found")
            return
        pop = Gtk.Popover()
        pop.set_parent(self._git_buttons[2])
        pop.connect("closed", lambda p: p.unparent())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for name in branches:
            b = Gtk.Button(label=name, has_frame=False)
            b.connect("clicked", lambda _b, n=name: (pop.popdown(), self._run_git("checkout", n)))
            box.append(b)
        pop.set_child(box)
        pop.popup()
