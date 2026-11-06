"""Content widget for a `fileTree` node (spec: docs/reference/maestri-reference-index.md §7).

Toolbar: back / forward / up, current root, git branch menu (git_menu.GitBranchButton), [List | Grid], show-hidden, refresh.
"Show Changes" in the branch menu reveals a DiffView (diff_view.py) in a vertical pane under the list / grid.

List mode, all native GTK 4 list models (nothing is read until a folder is expanded; enumeration is async and monitored, so files
created/deleted on disk appear/disappear live):

    Gtk.DirectoryList (per folder) -> Gtk.FilterListModel (hidden files) -> Gtk.SortListModel (folders first, then name)
      -> Gtk.TreeListModel (create_func builds the child model of a folder on first expand) -> Gtk.SingleSelection -> Gtk.ListView

Grid mode: the root folder's model (same filter/sorter, plus thumbnail attributes) -> Gtk.SingleSelection -> Gtk.GridView; cells show
a thumbnail (see thumbnails.py) or the themed icon. Activating a folder enters it, a file emits `file-activated`.

One filter and one sorter instance are shared by every folder model, so toggling "show hidden" refilters the whole tree at once.

Also on every row / cell:
  * git badge (M/A/D/R/U/?), folders showing their strongest changed descendant (git_badges.py, refreshed async)
  * DragSource: drop on a terminal to type its quoted path, on the empty canvas to create a preview node, on a note / external app
  * right-click menu: Open, Quick Look, New File…, New Folder…, Rename…, Move to…, Copy Path, Delete… (Gtk.AlertDialog -> Trash)
Space toggles Quick Look (quick_look.py) on the selected file.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from . import file_ops
from .file_dnd import file_content_provider
from .file_ops import FileOpError
from .git_badges import CSS_CLASS, TOOLTIP, GitBadges, GitStatusRunner
from .thumbnails import THUMB_ATTRS, Thumbnailer

ATTRS = ",".join((
    "standard::name", "standard::display-name", "standard::type", "standard::icon", "standard::symbolic-icon",
    "standard::is-hidden", "standard::is-backup", "standard::size", "standard::content-type",
))
GRID_ATTRS = ATTRS + "," + THUMB_ATTRS
ICON_PX = 16
THUMB_PX = 72
GIT_DEBOUNCE_MS = 400
VIEW_MODES = ("list", "grid")


def _info_file(info: Gio.FileInfo) -> Gio.File:
    return info.get_attribute_object("standard::file")   # set by Gtk.DirectoryList on every item


def _is_dir(info: Gio.FileInfo) -> bool:
    return info.get_file_type() == Gio.FileType.DIRECTORY


def _as_info(item) -> Gio.FileInfo | None:
    """List items are Gtk.TreeListRow (list mode) or Gio.FileInfo (grid mode)."""
    if isinstance(item, Gtk.TreeListRow):
        return item.get_item()
    return item


class FileTreeView(Gtk.Box):
    """`canvas.add_node(item, FileTreeView("/path"))`.

    Activating a folder row (double-click, Enter) toggles it (list) or enters it (grid); activating a file emits `file-activated(path)`.
    `set_root(path)` re-roots the tree (history kept for back/forward) and emits `root-changed(path)`; `set_view_mode` emits
    `view-mode-changed(mode)`. `status-changed(text, ok)` reports the outcome of file operations."""
    __gtype_name__ = "MaestriFileTreeView"
    __gsignals__ = {
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "root-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "view-mode-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "git-badges-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        "ask-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),   # DiffView selection quoted for a connected agent
    }

    def __init__(self, root: str | os.PathLike | None = None, show_hidden: bool = False, view_mode: str = "list",
                 git: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("maestri-file-tree")
        self._show_hidden = show_hidden
        self._view_mode = view_mode if view_mode in VIEW_MODES else "list"
        self._back: list[str] = []
        self._forward: list[str] = []
        self._root = ""
        self._initial_root = str(root or Path.home())
        self._grid_root = ""
        self.badges = GitBadges()
        self._git = GitStatusRunner() if git else None
        self.git_button = None
        self.diff_view = None
        self._git_timer = 0
        self._bound: dict[Gtk.Widget, tuple[str, bool]] = {}   # live row/cell widget -> (path, is_dir): retargeted on badge refresh
        self.thumbnailer = Thumbnailer(THUMB_PX)
        self.quick_look_window = None
        self._menu_path: str | None = None
        self._prompt: Gtk.Popover | None = None
        self.last_error: str | None = None

        # shared by every folder model
        self._filter = Gtk.CustomFilter.new(self._keep)
        self._sorter = Gtk.CustomSorter.new(self._compare)

        self.append(self._build_toolbar())
        if self.git_button is not None:
            self.append(self.git_button.feedback)   # result of the last git action, auto-hides
        self.append(Gtk.Separator())

        # ---- list ----
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=False)
        self.list_view.add_css_class("maestri-file-tree-list")
        self.list_view.connect("activate", self._on_activate)

        # ---- grid ----
        gfactory = Gtk.SignalListItemFactory()
        gfactory.connect("setup", self._setup_cell)
        gfactory.connect("bind", self._bind_cell)
        gfactory.connect("unbind", self._unbind_cell)
        self.grid_selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.grid_view = Gtk.GridView(model=self.grid_selection, factory=gfactory, max_columns=12, min_columns=2,
                                      single_click_activate=False)
        self.grid_view.add_css_class("maestri-file-grid")
        self.grid_view.connect("activate", self._on_grid_activate)

        self.stack = Gtk.Stack(vexpand=True, hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        for name, view in (("list", self.list_view), ("grid", self.grid_view)):
            sc = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            sc.set_child(view)
            self._add_background_menu(sc)
            self.stack.add_named(sc, name)
        if git:
            from .diff_view import DiffView
            self.diff_view = DiffView(str(root or Path.home()))
            self.diff_view.set_visible(False)
            self.diff_view.connect("close-requested", lambda _d: self.diff_view.set_visible(False))
            self.diff_view.connect("ask-requested", lambda _d, quote: self.emit("ask-requested", quote))
            self.git_button.connect("diff-requested", lambda _b: self.show_diff())
            self.git_button.connect("action-finished", lambda _b, _r: self._after_git_action())
            self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, shrink_end_child=False, resize_end_child=True)
            self.paned.set_start_child(self.stack)
            self.paned.set_end_child(self.diff_view)
            self.append(self.paned)
        else:
            self.append(self.stack)

        self.status_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False, margin_start=8, margin_end=8,
                                      margin_top=2, margin_bottom=2)
        self.status_label.add_css_class("caption")
        self.status_icon = Gtk.Image(icon_name="maestri-warning-symbolic", visible=False, margin_start=8)   # errors: outline icon, no "⚠"
        self.status_icon.add_css_class("error")
        status_row = Gtk.Box()
        status_row.append(self.status_icon)
        status_row.append(self.status_label)
        self.append(status_row)

        self._install_actions()
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

        self.set_root(root or Path.home(), _record=False)
        self.set_view_mode(self._view_mode, _force=True)

    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=2)
        bar.add_css_class("toolbar")
        self.btn_back = self._tool_button("go-previous-symbolic", "Back", self.go_back)
        self.btn_forward = self._tool_button("go-next-symbolic", "Forward", self.go_forward)
        self.btn_up = self._tool_button("go-up-symbolic", "Parent folder", self.go_up)
        self.path_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
        self.path_label.add_css_class("dim-label")
        if self._git is not None:
            from .git_menu import GitBranchButton
            self.git_button = GitBranchButton(str(Path(self._initial_root).expanduser().resolve()))
        self.btn_list = Gtk.ToggleButton(icon_name="view-list-symbolic", tooltip_text="List")
        self.btn_grid = Gtk.ToggleButton(icon_name="view-grid-symbolic", tooltip_text="Grid", group=self.btn_list)
        self.btn_list.connect("toggled", lambda b: b.get_active() and self.set_view_mode("list"))
        self.btn_grid.connect("toggled", lambda b: b.get_active() and self.set_view_mode("grid"))
        modes = Gtk.Box()
        modes.add_css_class("linked")
        modes.append(self.btn_list)
        modes.append(self.btn_grid)
        self.btn_hidden = Gtk.ToggleButton(icon_name="view-reveal-symbolic", tooltip_text="Show hidden files", active=self._show_hidden)
        self.btn_hidden.add_css_class("flat")
        self.btn_hidden.connect("toggled", lambda b: self.set_show_hidden(b.get_active()))
        btn_refresh = self._tool_button("view-refresh-symbolic", "Refresh", self.refresh)
        self.btn_collapse = self._tool_button("maestri-collapse-all-symbolic", "Collapse All", self.collapse_all)
        for w in (self.btn_back, self.btn_forward, self.btn_up, self.path_label, self.git_button, modes, self.btn_hidden,
                  self.btn_collapse, btn_refresh):
            if w is not None:
                bar.append(w)
        return bar

    # ---- public API -------------------------------------------------------------------------------
    def collapse_all(self) -> None:
        """Collapse every expanded folder (list mode). Bottom-up, so a parent collapse never skips its children's rows."""
        tree = self.tree
        if not isinstance(tree, Gtk.TreeListModel):
            return
        for i in range(tree.get_n_items() - 1, -1, -1):
            row = tree.get_item(i)
            if isinstance(row, Gtk.TreeListRow) and row.get_expanded():
                row.set_expanded(False)

    @property
    def root(self) -> str:
        return self._root

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def tree(self) -> Gtk.TreeListModel:
        return self.selection.get_model()

    @property
    def grid_model(self) -> Gio.ListModel | None:
        return self.grid_selection.get_model()

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

    def set_view_mode(self, mode: str, _force: bool = False) -> None:
        if mode not in VIEW_MODES or (mode == self._view_mode and not _force):
            return
        changed = mode != self._view_mode
        self._view_mode = mode
        if mode == "grid":
            self._ensure_grid_model()
        self.stack.set_visible_child_name(mode)
        btn = self.btn_grid if mode == "grid" else self.btn_list
        if not btn.get_active():
            btn.set_active(True)
        if changed:
            self.emit("view-mode-changed", mode)

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
        """True while any expanded folder (or the grid's folder) is still being enumerated."""
        tree = self.tree
        if tree is None:
            return False
        if self._root_list.is_loading():
            return True
        gm = self.grid_model
        if self._view_mode == "grid" and gm is not None and gm.get_model().get_model().is_loading():
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

    def cells(self) -> list[str]:
        """Grid mode: names in display order."""
        gm = self.grid_model
        return [] if gm is None else [gm.get_item(i).get_name() for i in range(gm.get_n_items())]

    def row_for(self, rel_path: str) -> Gtk.TreeListRow | None:
        """The visible row of `root/rel_path`, or None (not loaded / inside a collapsed folder)."""
        target = os.path.join(self._root, rel_path)
        tree = self.tree
        for i in range(tree.get_n_items()):
            row = tree.get_item(i)
            if _info_file(row.get_item()).get_path() == target:
                return row
        return None

    def selected_path(self) -> str | None:
        sel = self.grid_selection if self._view_mode == "grid" else self.selection
        info = _as_info(sel.get_selected_item())
        return _info_file(info).get_path() if info is not None else None

    def select_path(self, path: str) -> bool:
        """Select the visible row / cell for an absolute path."""
        sel = self.grid_selection if self._view_mode == "grid" else self.selection
        model = sel.get_model()
        for i in range(model.get_n_items() if model is not None else 0):
            info = _as_info(model.get_item(i))
            if _info_file(info).get_path() == path:
                sel.set_selected(i)
                return True
        return False

    def badge_for(self, path: str, is_dir: bool = False) -> str | None:
        return self.badges.badge(path, is_dir)

    # ---- models -----------------------------------------------------------------------------------
    def _dir_model(self, gfile: Gio.File, attrs: str = ATTRS) -> Gtk.SortListModel:
        dl = Gtk.DirectoryList.new(attrs, gfile)
        dl.set_monitored(True)
        dl.connect("items-changed", lambda *_: self._schedule_git())   # created / deleted / renamed on disk: badges may change
        return Gtk.SortListModel.new(Gtk.FilterListModel.new(dl, self._filter), self._sorter)

    def _create_child(self, info: Gio.FileInfo) -> Gio.ListModel | None:
        return self._dir_model(_info_file(info)) if _is_dir(info) else None

    def _load_root(self) -> None:
        root_model = self._dir_model(Gio.File.new_for_path(self._root))
        self._root_list = root_model.get_model().get_model()
        tree = Gtk.TreeListModel.new(root_model, False, False, self._create_child)
        self.selection.set_model(tree)
        if self._view_mode == "grid":
            self._ensure_grid_model()
        self.path_label.set_label(self._display_path(self._root))
        self.path_label.set_tooltip_text(self._root)
        self.btn_back.set_sensitive(bool(self._back))
        self.btn_forward.set_sensitive(bool(self._forward))
        self.btn_up.set_sensitive(Path(self._root).parent != Path(self._root))
        self.refresh_git()
        if self.git_button is not None:
            self.git_button.set_root(self._root)
            self.diff_view.set_root(self._root)

    def show_diff(self) -> None:
        if self.diff_view is not None:
            self.diff_view.set_visible(True)
            self.diff_view.reload()

    def _after_git_action(self) -> None:
        """Commit / pull / checkout / stash... changed the work tree: badges, and the diff when it is open."""
        self.refresh_git()
        if self.diff_view is not None and self.diff_view.get_visible():
            self.diff_view.reload()

    def _ensure_grid_model(self) -> None:
        if self._grid_root != self._root or self.grid_model is None:
            self._grid_root = self._root
            self.grid_selection.set_model(self._dir_model(Gio.File.new_for_path(self._root), GRID_ATTRS))

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

    # ---- git badges -------------------------------------------------------------------------------
    def refresh_git(self) -> None:
        if self._git is None:
            return
        if self._git_timer:
            GLib.source_remove(self._git_timer)
            self._git_timer = 0
        root = self._root
        self._git.refresh(root, lambda b: self._apply_badges(root, b))

    def _schedule_git(self) -> None:
        if self._git is None or self._git_timer:
            return

        def fire():
            self._git_timer = 0
            self.refresh_git()
            return GLib.SOURCE_REMOVE
        self._git_timer = GLib.timeout_add(GIT_DEBOUNCE_MS, fire)

    def _apply_badges(self, root: str, badges: GitBadges) -> None:
        if root != self._root:
            return   # a stale answer for a root we already left
        self.badges = badges
        for widget, (path, is_dir) in list(self._bound.items()):
            self._show_badge(widget, path, is_dir)
        self.emit("git-badges-changed")

    def _show_badge(self, badge: Gtk.Label, path: str, is_dir: bool) -> None:
        b = self.badges.badge(path, is_dir)
        for cls in CSS_CLASS.values():
            badge.remove_css_class(cls)
        badge.set_visible(b is not None)
        if b is not None:
            badge.set_label(b)
            badge.add_css_class(CSS_CLASS[b])
            badge.set_tooltip_text(TOOLTIP[b])

    # ---- rows (list) ------------------------------------------------------------------------------
    def _setup_row(self, _f, list_item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander()
        self._add_item_controllers(expander, list_item)
        box = Gtk.Box(spacing=6)
        icon = Gtk.Image(pixel_size=ICON_PX)
        label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        badge = Gtk.Label(visible=False, margin_end=4)
        badge.add_css_class("maestri-git-badge")
        box.append(icon)
        box.append(label)
        box.append(badge)
        expander.set_child(box)
        list_item.set_child(expander)

    def _bind_row(self, _f, list_item: Gtk.ListItem) -> None:
        row: Gtk.TreeListRow = list_item.get_item()
        info: Gio.FileInfo = row.get_item()
        expander: Gtk.TreeExpander = list_item.get_child()
        expander.set_list_row(row)
        box = expander.get_child()
        icon = box.get_first_child()
        label = icon.get_next_sibling()
        badge = box.get_last_child()
        gicon = info.get_icon() or info.get_symbolic_icon()
        if gicon is not None:
            icon.set_from_gicon(gicon)
        else:
            icon.set_from_icon_name("folder" if _is_dir(info) else "text-x-generic")
        label.set_label(info.get_display_name())
        path = _info_file(info).get_path()
        expander.set_tooltip_text(path)
        (label.add_css_class if info.get_is_hidden() else label.remove_css_class)("dim-label")
        self._bound[badge] = (path, _is_dir(info))
        self._show_badge(badge, path, _is_dir(info))

    def _unbind_row(self, _f, list_item: Gtk.ListItem) -> None:
        expander = list_item.get_child()
        expander.set_list_row(None)
        self._bound.pop(expander.get_child().get_last_child(), None)

    # ---- cells (grid) -----------------------------------------------------------------------------
    def _setup_cell(self, _f, list_item: Gtk.ListItem) -> None:
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, width_request=THUMB_PX + 24)
        cell.add_css_class("maestri-file-cell")
        self._add_item_controllers(cell, list_item)
        over = Gtk.Overlay()
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, width_request=THUMB_PX, height_request=THUMB_PX)
        pic.add_css_class("maestri-file-thumb")
        img = Gtk.Image(pixel_size=THUMB_PX * 2 // 3, width_request=THUMB_PX, height_request=THUMB_PX)
        thumb = Gtk.Stack()
        thumb.add_named(img, "icon")
        thumb.add_named(pic, "thumb")
        over.set_child(thumb)
        badge = Gtk.Label(visible=False, halign=Gtk.Align.END, valign=Gtk.Align.START)
        badge.add_css_class("maestri-git-badge")
        over.add_overlay(badge)
        label = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2, ellipsize=Pango.EllipsizeMode.MIDDLE,
                          justify=Gtk.Justification.CENTER, max_width_chars=12)
        cell.append(over)
        cell.append(label)
        list_item.set_child(cell)

    @staticmethod
    def _cell_parts(cell: Gtk.Box):
        over = cell.get_first_child()
        thumb: Gtk.Stack = over.get_child()
        badge = over.get_last_child()
        return thumb, thumb.get_child_by_name("icon"), thumb.get_child_by_name("thumb"), badge, cell.get_last_child()

    def _bind_cell(self, _f, list_item: Gtk.ListItem) -> None:
        info: Gio.FileInfo = list_item.get_item()
        cell = list_item.get_child()
        thumb, img, pic, badge, label = self._cell_parts(cell)
        path = _info_file(info).get_path()
        cell.maestri_path = path
        gicon = info.get_icon() or info.get_symbolic_icon()
        if gicon is not None:
            img.set_from_gicon(gicon)
        else:
            img.set_from_icon_name("folder" if _is_dir(info) else "text-x-generic")
        thumb.set_visible_child_name("icon")
        pic.set_paintable(None)
        label.set_label(info.get_display_name())
        cell.set_tooltip_text(path)
        (label.add_css_class if info.get_is_hidden() else label.remove_css_class)("dim-label")
        self._bound[badge] = (path, _is_dir(info))
        self._show_badge(badge, path, _is_dir(info))
        if not _is_dir(info):
            def got(tex, cell=cell, path=path):
                if getattr(cell, "maestri_path", None) == path:   # the cell was not recycled for another file meanwhile
                    t, _i, p, _b, _l = self._cell_parts(cell)
                    p.set_paintable(tex)
                    t.set_visible_child_name("thumb")
            self.thumbnailer.request(info, path, got)

    def _unbind_cell(self, _f, list_item: Gtk.ListItem) -> None:
        cell = list_item.get_child()
        cell.maestri_path = None
        self._bound.pop(self._cell_parts(cell)[3], None)

    # ---- per-item controllers: drag source + context menu -----------------------------------------
    def _add_item_controllers(self, widget: Gtk.Widget, list_item: Gtk.ListItem) -> None:
        # Each row is a drag source: drop it on a terminal to type its (quoted) path, on the canvas for a preview node,
        # or on a note / external app.
        src = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        src.connect("prepare", lambda _s, _x, _y: self._drag_prepare(list_item))
        src.connect("drag-begin", lambda _s, drag: self._drag_begin(list_item, drag))
        widget.add_controller(src)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda g, _n, x, y: self._item_menu(g, list_item, widget, x, y))
        widget.add_controller(click)

    def _add_background_menu(self, scroller: Gtk.ScrolledWindow) -> None:
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda g, _n, x, y: self._background_menu(g, scroller, x, y))
        scroller.add_controller(click)

    # ---- drag source ------------------------------------------------------------------------------
    def dragged_files(self, list_item: Gtk.ListItem) -> list[Gio.File]:
        """The file under the pointer (single selection today)."""
        info = _as_info(list_item.get_item())
        return [] if info is None else [_info_file(info)]

    def _drag_prepare(self, list_item: Gtk.ListItem) -> Gdk.ContentProvider | None:
        files = self.dragged_files(list_item)
        return file_content_provider(files) if files else None

    @staticmethod
    def _drag_begin(list_item: Gtk.ListItem, drag: Gdk.Drag) -> None:
        info = _as_info(list_item.get_item())
        if info is None:
            return
        box = Gtk.Box(spacing=6)
        box.add_css_class("maestri-drag-icon")
        img = Gtk.Image(pixel_size=ICON_PX)
        gicon = info.get_icon()
        if gicon is not None:
            img.set_from_gicon(gicon)
        box.append(img)
        box.append(Gtk.Label(label=info.get_display_name()))
        Gtk.DragIcon.get_for_drag(drag).set_child(box)

    # ---- activation -------------------------------------------------------------------------------
    def _on_activate(self, _lv: Gtk.ListView, position: int) -> None:
        row: Gtk.TreeListRow = self.tree.get_item(position)
        if row is None:
            return
        info = row.get_item()
        if _is_dir(info):
            row.set_expanded(not row.get_expanded())
        else:
            self.emit("file-activated", _info_file(info).get_path())

    def _on_grid_activate(self, _gv: Gtk.GridView, position: int) -> None:
        info = self.grid_model.get_item(position) if self.grid_model is not None else None
        if info is None:
            return
        path = _info_file(info).get_path()
        if _is_dir(info):
            self.set_root(path)
        else:
            self.emit("file-activated", path)

    # ---- context menu -----------------------------------------------------------------------------
    MENU_ACTIONS = ("open", "quick-look", "new-file", "new-folder", "rename", "move", "copy-path", "delete")

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        handlers = {
            "open": lambda p: self._open(p),
            "quick-look": lambda p: self.quick_look(p),
            "new-file": lambda p: self.prompt_new(p, folder=False),
            "new-folder": lambda p: self.prompt_new(p, folder=True),
            "rename": lambda p: self.prompt_rename(p),
            "move": lambda p: self.request_move(p),
            "copy-path": lambda p: self.copy_path(p),
            "delete": lambda p: self.request_delete(p),
        }
        for name, fn in handlers.items():
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, f=fn: f(self._menu_path or self._root))
            group.add_action(act)
        self.action_group = group
        self.insert_action_group("ft", group)

    def menu_model(self, path: str | None) -> Gio.Menu:
        """Item menu for `path`, or the folder-background menu (None)."""
        menu = Gio.Menu()
        if path is not None:
            top = Gio.Menu()
            top.append("Open", "ft.open")
            if not os.path.isdir(path):
                top.append("Quick Look", "ft.quick-look")
            menu.append_section(None, top)
        new = Gio.Menu()
        new.append("New File…", "ft.new-file")
        new.append("New Folder…", "ft.new-folder")
        menu.append_section(None, new)
        if path is not None:
            edit = Gio.Menu()
            edit.append("Rename…", "ft.rename")
            edit.append("Move to…", "ft.move")
            edit.append("Copy Path", "ft.copy-path")
            menu.append_section(None, edit)
            danger = Gio.Menu()
            danger.append("Move to Trash…", "ft.delete")
            menu.append_section(None, danger)
        return menu

    def _popup_menu(self, path: str | None, anchor: Gtk.Widget, x: float, y: float) -> Gtk.PopoverMenu:
        self._menu_path = path
        pop = Gtk.PopoverMenu.new_from_model(self.menu_model(path))
        pop.set_has_arrow(False)
        pop.set_halign(Gtk.Align.START)
        pop.set_parent(self)
        ok, pt = anchor.compute_point(self, _graphene_point(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = (int(pt.x), int(pt.y), 1, 1) if ok else (0, 0, 1, 1)
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()
        self.context_menu = pop
        return pop

    def _item_menu(self, gesture: Gtk.GestureClick, list_item: Gtk.ListItem, widget: Gtk.Widget, x: float, y: float) -> None:
        info = _as_info(list_item.get_item())
        if info is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        path = _info_file(info).get_path()
        self.select_path(path)
        self._popup_menu(path, widget, x, y)

    def _background_menu(self, gesture: Gtk.GestureClick, scroller: Gtk.Widget, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._popup_menu(None, scroller, x, y)

    def _target_folder(self, path: str | None) -> str:
        """Where "New File/Folder" creates: the folder itself, or the folder containing a file; the root for the background."""
        if not path:
            return self._root
        return path if os.path.isdir(path) else os.path.dirname(path)

    def _open(self, path: str) -> None:
        if os.path.isdir(path):
            self.set_root(path)
        else:
            self.emit("file-activated", path)

    def copy_path(self, path: str) -> None:
        self.get_clipboard().set(path)
        self._status(f"Copied {path}", True)

    # ---- operations (each one reports via status-changed) ------------------------------------------
    def create(self, folder: str, name: str, is_folder: bool) -> str | None:
        try:
            path = (file_ops.create_folder if is_folder else file_ops.create_file)(folder, name)
        except FileOpError as e:
            self._status(str(e), False)
            return None
        self._status(f"Created {os.path.basename(path)}", True)
        self._after_change(path)
        return path

    def rename(self, path: str, new_name: str) -> str | None:
        try:
            dest = file_ops.rename(path, new_name)
        except FileOpError as e:
            self._status(str(e), False)
            return None
        if dest != path:
            self._status(f"Renamed to {os.path.basename(dest)}", True)
            if path == self._root:
                self.set_root(dest)
            self._after_change(dest)
        return dest

    def move(self, path: str, dest_dir: str, cb=None) -> None:
        """Gio move (falls back to copy + delete across file systems), async."""
        try:
            dest = file_ops.check_move(path, dest_dir)
        except FileOpError as e:
            self._status(str(e), False)
            if cb:
                cb(None)
            return

        def done(src, res):
            try:
                src.move_finish(res)
            except GLib.Error as e:
                self._status(f"Move failed: {e.message}", False)
                if cb:
                    cb(None)
                return
            self._status(f"Moved {os.path.basename(path)} to {self._display_path(dest_dir)}", True)
            self._after_change(dest)
            if cb:
                cb(dest)

        Gio.File.new_for_path(path).move_async(Gio.File.new_for_path(dest), Gio.FileCopyFlags.NOFOLLOW_SYMLINKS,
                                               GLib.PRIORITY_DEFAULT, None, None, done)

    def trash(self, path: str, cb=None) -> None:
        """Move to the freedesktop Trash (recoverable). Where the file system has no trash, nothing is deleted."""
        def done(f, res):
            try:
                f.trash_finish(res)
            except GLib.Error as e:
                msg = "This location has no Trash; nothing was deleted" if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED) \
                    else f"Delete failed: {e.message}"
                self._status(msg, False)
                if cb:
                    cb(False)
                return
            self._status(f"Moved {os.path.basename(path)} to Trash", True)
            if os.path.realpath(path) == self._root:
                self.go_up()
            self._after_change(None)
            if cb:
                cb(True)

        Gio.File.new_for_path(path).trash_async(GLib.PRIORITY_DEFAULT, None, done)

    def _after_change(self, select: str | None) -> None:
        self.refresh_git()
        if select:
            # the monitored DirectoryList picks the change up asynchronously: select once it shows up
            tries = {"n": 0}
            def attempt():
                tries["n"] += 1
                return GLib.SOURCE_REMOVE if self.select_path(select) or tries["n"] > 20 else GLib.SOURCE_CONTINUE
            GLib.timeout_add(50, attempt)

    def _status(self, text: str, ok: bool) -> None:
        self.last_error = None if ok else text
        self.status_label.set_label(text)
        self.status_label.set_visible(bool(text))
        self.status_icon.set_visible(bool(text) and not ok)
        (self.status_label.remove_css_class if ok else self.status_label.add_css_class)("error")
        self.emit("status-changed", text, ok)

    # ---- dialogs ----------------------------------------------------------------------------------
    def _name_prompt(self, title: str, initial: str, select_stem: bool, commit) -> Gtk.Popover:
        """A small popover with an entry: Enter commits (commit(name) -> True closes, False keeps it open with the error)."""
        if self._prompt is not None:
            self._prompt.popdown()
        pop = Gtk.Popover()
        pop.set_parent(self)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        box.append(Gtk.Label(label=title, xalign=0))
        entry = Gtk.Entry(text=initial, activates_default=False, width_chars=28)
        error = Gtk.Label(xalign=0, visible=False, wrap=True)
        error.add_css_class("error")
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        ok = Gtk.Button(label="OK")
        ok.add_css_class("suggested-action")
        buttons.append(cancel)
        buttons.append(ok)
        box.append(entry)
        box.append(error)
        box.append(buttons)
        pop.set_child(box)

        def do_commit(*_):
            msg = commit(entry.get_text())
            if msg is None:
                pop.popdown()
            else:
                error.set_label(msg)
                error.set_visible(True)

        entry.connect("activate", do_commit)
        ok.connect("clicked", do_commit)
        cancel.connect("clicked", lambda _b: pop.popdown())
        pop.connect("closed", lambda p: (setattr(self, "_prompt", None) if self._prompt is p else None,
                                         GLib.idle_add(lambda: (p.unparent(), False)[1])))
        pop.entry, pop.error, pop.commit = entry, error, do_commit
        self._prompt = pop
        pop.popup()
        entry.grab_focus()
        if select_stem and "." in initial.lstrip("."):
            entry.select_region(0, len(initial) - len(os.path.splitext(initial)[1]))
        else:
            entry.select_region(0, -1)
        return pop

    @property
    def name_prompt(self) -> Gtk.Popover | None:
        return self._prompt

    def prompt_new(self, near: str | None, folder: bool) -> Gtk.Popover:
        target = self._target_folder(near)
        initial = file_ops.unique_child(target, "New Folder" if folder else "untitled.txt")

        def commit(name: str):
            try:
                file_ops.validate_name(name)
            except FileOpError as e:
                return str(e)
            return None if self.create(target, name, folder) else self.last_error
        return self._name_prompt(f"New {'folder' if folder else 'file'} in {os.path.basename(target) or target}", initial, not folder, commit)

    def prompt_rename(self, path: str) -> Gtk.Popover:
        def commit(name: str):
            return None if self.rename(path, name) else self.last_error
        return self._name_prompt(f"Rename {os.path.basename(path)}", os.path.basename(path), not os.path.isdir(path), commit)

    def request_move(self, path: str) -> None:
        dlg = Gtk.FileDialog(title=f"Move {os.path.basename(path)} to…", modal=True)
        dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(path)))

        def chosen(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return   # cancelled
            if folder is not None and folder.get_path():
                self.move(path, folder.get_path())
        dlg.select_folder(self.get_root() if isinstance(self.get_root(), Gtk.Window) else None, None, chosen)

    def request_delete(self, path: str, cb=None) -> Gtk.AlertDialog:
        name = os.path.basename(path)
        kind = "folder" if os.path.isdir(path) else "file"
        dlg = Gtk.AlertDialog(message=f"Move “{name}” to the Trash?",
                              detail=f"The {kind} can be restored from the Trash." + (" Everything inside it goes too." if kind == "folder" else ""),
                              buttons=["Cancel", "Move to Trash"], cancel_button=0, default_button=0, modal=True)

        def answered(d, res):
            try:
                idx = d.choose_finish(res)
            except GLib.Error:
                idx = 0
            if idx == 1:
                self.trash(path, cb)
            elif cb:
                cb(False)
        dlg.choose(self.get_root() if isinstance(self.get_root(), Gtk.Window) else None, None, answered)
        self.delete_dialog = dlg
        return dlg

    # ---- Quick Look -------------------------------------------------------------------------------
    def quick_look(self, path: str | None = None):
        from .quick_look import QuickLookWindow
        path = path or self.selected_path()
        if not path:
            return None
        if self.quick_look_window is None:
            root = self.get_root()
            win = QuickLookWindow(root if isinstance(root, Gtk.Window) else None, navigate=self._ql_navigate)
            win.connect("close-request", self._ql_closed)
            self.quick_look_window = win
        self.quick_look_window.show_path(path)
        self.quick_look_window.present()
        return self.quick_look_window

    def toggle_quick_look(self) -> None:
        if self.quick_look_window is not None:
            self.quick_look_window.close()
        else:
            self.quick_look()

    def _ql_closed(self, win) -> bool:
        win._set_body(None)   # stop video / web loads
        if self.quick_look_window is win:
            self.quick_look_window = None
        return False

    def _ql_navigate(self, step: int) -> str | None:
        """Move the selection by one file (folders skipped) and return it."""
        sel = self.grid_selection if self._view_mode == "grid" else self.selection
        model = sel.get_model()
        n = model.get_n_items() if model is not None else 0
        i = sel.get_selected()
        i = -1 if i == Gtk.INVALID_LIST_POSITION else i
        j = i + step
        while 0 <= j < n:
            info = _as_info(model.get_item(j))
            if not _is_dir(info):
                sel.set_selected(j)
                return _info_file(info).get_path()
            j += step
        return None

    def _key_pressed(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK):
            return False
        name = (Gdk.keyval_name(keyval) or "").lower()
        if self._prompt is not None:
            return False   # typing a name: the entry owns every key
        if name == "space":
            if self.selected_path() or self.quick_look_window is not None:
                self.toggle_quick_look()
                return True
        elif name == "f2" and self.selected_path():
            self.prompt_rename(self.selected_path())
            return True
        elif name == "delete" and self.selected_path():
            self.request_delete(self.selected_path())
            return True
        return False

    def dispose(self) -> None:
        if self.quick_look_window is not None:
            self.quick_look_window.close()
        if self._git_timer:
            GLib.source_remove(self._git_timer)
            self._git_timer = 0

    def _tool_button(self, icon: str, tip: str, cb) -> Gtk.Button:
        b = Gtk.Button(icon_name=icon, tooltip_text=tip)
        b.add_css_class("flat")
        b.connect("clicked", lambda _b: cb())
        return b


def _graphene_point(x: float, y: float):
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene
    return Graphene.Point().init(x, y)
