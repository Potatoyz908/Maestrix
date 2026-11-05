"""FileTree node body: lazy-expanding tree with git badges, backed by FileTreeModel."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from maestri_core.filetree import FileTreeModel, FileEntry
from maestri_core.models.node import FileTreeContent

_BADGE_CSS = {"M": "warning", "A": "success", "D": "error", "R": "accent", "?": "dim-label"}


class FileTreeWidget(Gtk.ScrolledWindow):
    def __init__(self, client, content: FileTreeContent) -> None:
        super().__init__()
        self.model = FileTreeModel(client, content)
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self._box)
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
