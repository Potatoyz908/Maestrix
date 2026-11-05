"""Content widget for a `fileTree` node: a lazily-expanding directory outline (spec: docs/reference/maestri-reference-index.md §7, List mode).

Model stack, all native GTK 4 list models (nothing is read until a folder is expanded; enumeration is async and monitored, so files
created/deleted on disk appear/disappear live):

    Gtk.DirectoryList (per folder) -> Gtk.FilterListModel (hidden files) -> Gtk.SortListModel (folders first, then name)
      -> Gtk.TreeListModel (create_func builds the child model of a folder on first expand) -> Gtk.SingleSelection -> Gtk.ListView

One filter and one sorter instance are shared by every folder model, so toggling "show hidden" refilters the whole tree at once.
Rows show the file's themed GIcon (`standard::icon`, resolved by the GTK 4 icon theme) inside a Gtk.TreeExpander (disclosure triangle,
indentation, keyboard expand/collapse with +/-/←/→).
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk, Pango

ATTRS = ",".join((
    "standard::name", "standard::display-name", "standard::type", "standard::icon", "standard::symbolic-icon",
    "standard::is-hidden", "standard::is-backup", "standard::size", "standard::content-type",
))
ICON_PX = 16


def _info_file(info: Gio.FileInfo) -> Gio.File:
    return info.get_attribute_object("standard::file")   # set by Gtk.DirectoryList on every item


def _is_dir(info: Gio.FileInfo) -> bool:
    return info.get_file_type() == Gio.FileType.DIRECTORY


class FileTreeView(Gtk.Box):
    """`canvas.add_node(item, FileTreeView("/path"))`. Toolbar: back / forward / up, current root, show-hidden, refresh.

    Activating a folder row (double-click, Enter) toggles it; activating a file emits `file-activated(path)`.
    `set_root(path)` re-roots the tree (history kept for back/forward) and emits `root-changed(path)`."""
    __gtype_name__ = "MaestriFileTreeView"
    __gsignals__ = {
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "root-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, root: str | os.PathLike | None = None, show_hidden: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("maestri-file-tree")
        self._show_hidden = show_hidden
        self._back: list[str] = []
        self._forward: list[str] = []
        self._root = ""

        # shared by every folder model
        self._filter = Gtk.CustomFilter.new(self._keep)
        self._sorter = Gtk.CustomSorter.new(self._compare)

        # ---- toolbar ----
        bar = Gtk.Box(spacing=2)
        bar.add_css_class("toolbar")
        self.btn_back = self._tool_button("go-previous-symbolic", "Back", self.go_back)
        self.btn_forward = self._tool_button("go-next-symbolic", "Forward", self.go_forward)
        self.btn_up = self._tool_button("go-up-symbolic", "Parent folder", self.go_up)
        self.path_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
        self.path_label.add_css_class("dim-label")
        self.btn_hidden = Gtk.ToggleButton(icon_name="view-reveal-symbolic", tooltip_text="Show hidden files", active=show_hidden)
        self.btn_hidden.add_css_class("flat")
        self.btn_hidden.connect("toggled", lambda b: self.set_show_hidden(b.get_active()))
        btn_refresh = self._tool_button("view-refresh-symbolic", "Refresh", self.refresh)
        for w in (self.btn_back, self.btn_forward, self.btn_up, self.path_label, self.btn_hidden, btn_refresh):
            bar.append(w)

        # ---- list ----
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=False)
        self.list_view.add_css_class("maestri-file-tree-list")
        self.list_view.connect("activate", self._on_activate)
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.list_view)

        self.append(bar)
        self.append(Gtk.Separator())
        self.append(scroller)
        self.set_root(root or Path.home(), _record=False)

    # ---- public API -------------------------------------------------------------------------------
    @property
    def root(self) -> str:
        return self._root

    @property
    def tree(self) -> Gtk.TreeListModel:
        return self.selection.get_model()

    def set_root(self, path: str | os.PathLike, _record: bool = True) -> None:
        path = str(Path(path).expanduser().resolve())
        if path == self._root:
            return
        if _record and self._root:
            self._back.append(self._root)
            self._forward.clear()
        self._root = path
        self._load_root()
        self.emit("root-changed", path)

    def go_back(self) -> None:
        if self._back:
            self._forward.append(self._root)
            self._root = self._back.pop()
            self._load_root()
            self.emit("root-changed", self._root)

    def go_forward(self) -> None:
        if self._forward:
            self._back.append(self._root)
            self._root = self._forward.pop()
            self._load_root()
            self.emit("root-changed", self._root)

    def go_up(self) -> None:
        parent = str(Path(self._root).parent)
        if parent != self._root:
            self.set_root(parent)

    def set_show_hidden(self, show: bool) -> None:
        if show == self._show_hidden:
            return
        self._show_hidden = show
        if self.btn_hidden.get_active() != show:
            self.btn_hidden.set_active(show)
        self._filter.changed(Gtk.FilterChange.LESS_STRICT if show else Gtk.FilterChange.MORE_STRICT)

    def refresh(self) -> None:
        """Rebuild from disk (folders are also monitored, so this is only needed for unmonitorable mounts). Expansion is reset."""
        self._load_root()

    def is_loading(self) -> bool:
        """True while any expanded folder is still being enumerated."""
        tree = self.tree
        if tree is None:
            return False
        if self._root_list.is_loading():
            return True
        for i in range(tree.get_n_items()):
            row = tree.get_item(i)
            child = row.get_children()
            if child is not None and row.get_expanded() and child.get_model().get_model().is_loading():
                return True
        return False

    def rows(self) -> list[tuple[int, str]]:
        """Visible rows as (depth, name), top to bottom: what the outline currently shows."""
        tree = self.tree
        return [(tree.get_item(i).get_depth(), tree.get_item(i).get_item().get_name()) for i in range(tree.get_n_items())]

    def row_for(self, rel_path: str) -> Gtk.TreeListRow | None:
        """The visible row of `root/rel_path`, or None (not loaded / inside a collapsed folder)."""
        target = os.path.join(self._root, rel_path)
        tree = self.tree
        for i in range(tree.get_n_items()):
            row = tree.get_item(i)
            if _info_file(row.get_item()).get_path() == target:
                return row
        return None

    # ---- models -----------------------------------------------------------------------------------
    def _dir_model(self, gfile: Gio.File) -> Gtk.SortListModel:
        dl = Gtk.DirectoryList.new(ATTRS, gfile)
        dl.set_monitored(True)
        return Gtk.SortListModel.new(Gtk.FilterListModel.new(dl, self._filter), self._sorter)

    def _create_child(self, info: Gio.FileInfo) -> Gio.ListModel | None:
        return self._dir_model(_info_file(info)) if _is_dir(info) else None

    def _load_root(self) -> None:
        root_model = self._dir_model(Gio.File.new_for_path(self._root))
        self._root_list = root_model.get_model().get_model()
        tree = Gtk.TreeListModel.new(root_model, False, False, self._create_child)
        self.selection.set_model(tree)
        self.path_label.set_label(self._display_path(self._root))
        self.path_label.set_tooltip_text(self._root)
        self.btn_back.set_sensitive(bool(self._back))
        self.btn_forward.set_sensitive(bool(self._forward))
        self.btn_up.set_sensitive(Path(self._root).parent != Path(self._root))

    def _keep(self, info: Gio.FileInfo, *_) -> bool:
        return self._show_hidden or not (info.get_is_hidden() or info.get_is_backup())

    @staticmethod
    def _compare(a: Gio.FileInfo, b: Gio.FileInfo, *_) -> int:
        ka = (not _is_dir(a), a.get_display_name().casefold(), a.get_name())
        kb = (not _is_dir(b), b.get_display_name().casefold(), b.get_name())
        return Gtk.Ordering.SMALLER if ka < kb else Gtk.Ordering.LARGER if ka > kb else Gtk.Ordering.EQUAL

    @staticmethod
    def _display_path(p: str) -> str:
        home = str(Path.home())
        return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p

    # ---- rows -------------------------------------------------------------------------------------
    @staticmethod
    def _setup_row(_f, list_item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander()
        box = Gtk.Box(spacing=6)
        icon = Gtk.Image(pixel_size=ICON_PX)
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.append(icon)
        box.append(label)
        expander.set_child(box)
        list_item.set_child(expander)

    @staticmethod
    def _bind_row(_f, list_item: Gtk.ListItem) -> None:
        row: Gtk.TreeListRow = list_item.get_item()
        info: Gio.FileInfo = row.get_item()
        expander: Gtk.TreeExpander = list_item.get_child()
        expander.set_list_row(row)
        box = expander.get_child()
        icon, label = box.get_first_child(), box.get_last_child()
        gicon = info.get_icon() or info.get_symbolic_icon()
        if gicon is not None:
            icon.set_from_gicon(gicon)
        else:
            icon.set_from_icon_name("folder" if _is_dir(info) else "text-x-generic")
        label.set_label(info.get_display_name())
        path = _info_file(info).get_path()
        expander.set_tooltip_text(path)
        if info.get_is_hidden():
            label.add_css_class("dim-label")
        else:
            label.remove_css_class("dim-label")

    @staticmethod
    def _unbind_row(_f, list_item: Gtk.ListItem) -> None:
        list_item.get_child().set_list_row(None)

    def _on_activate(self, _lv: Gtk.ListView, position: int) -> None:
        row: Gtk.TreeListRow = self.tree.get_item(position)
        if row is None:
            return
        info = row.get_item()
        if _is_dir(info):
            row.set_expanded(not row.get_expanded())
        else:
            self.emit("file-activated", _info_file(info).get_path())

    def _tool_button(self, icon: str, tip: str, cb) -> Gtk.Button:
        b = Gtk.Button(icon_name=icon, tooltip_text=tip)
        b.add_css_class("flat")
        b.connect("clicked", lambda _b: cb())
        return b
