"""
Context menus of the canvas (Sprint 7 look, Sprint 9 behaviour).

- Canvas background (uploaded_media_1): Add ▸ [Terminal ▸, Note, File Tree, Portal, Text], Paste, Partituras ▸
- Node menus, one variant per kind (docs/reference/maestri-reference-index.md §15, Sprint 9 spec §4 Pixel):
  terminal  Copy · Paste · Select All | Rename · Edit… · Monitor Activity | Assign Role ▸ · Enable Maestro |
            Blur · Reload · Unload · New Element Defaults ▸ | Copy Element · Duplicate · Lock | Delete | Partituras ▸
  note      Rename · Move to… · Raw/Formatted | New Element Defaults ▸ | Copy Element · Duplicate · Lock | Delete | Partituras ▸
  portal    Reload | Copy Element · Duplicate · Lock | Delete
  fileTree, text/shape/freehand   Copy Element · Duplicate · Lock | Delete
- Connection (right click on a rope): Delete Connection.

Every row calls a real method. Rows whose target belongs to another workstream (window.reload_terminal, copy_node,
paste_nodes, delete_connection, save_partitura, apply_partitura - contracts K9/K10/K11/K13) are shown DISABLED, with a
tooltip naming the missing method, while that method does not exist: the menu never pretends.

`open_context_menu(window, gesture, x, y)` is the single right-click dispatcher (window.py only delegates to it): it
claims the click for the canvas/connection/node menus and DENIES it over the content of notes, portals and file trees,
so their own menus (note text view, file tree / Git) keep working.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import norm
from maestri_core.signals.bus import bus

DRAWN_KINDS = ("text", "shape", "freehand")
SEPARATOR = None   # in an item list


# ---------------------------------------------------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------------------------------------------------
def make_menu_row(
    icon_name: str,
    label_text: str,
    callback: Optional[Callable[[], None]] = None,
    is_danger: bool = False,
    has_chevron: bool = False,
    is_checked: bool = False,
) -> Gtk.Button:
    """One dark-glass menu row with a monochrome outline icon (a checked row shows the check where its icon goes)."""
    btn = Gtk.Button()
    btn.add_css_class("maestri-menu-item-btn")
    if is_danger:
        btn.add_css_class("danger")
    btn.set_has_frame(False)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    box.set_margin_start(4)
    box.set_margin_end(4)

    img = Gtk.Image.new_from_icon_name("maestri-check-symbolic" if is_checked else icon_name)
    img.set_icon_size(Gtk.IconSize.NORMAL)
    img.add_css_class("maestri-menu-icon")
    if is_checked:
        img.add_css_class("maestri-menu-check")
    box.append(img)

    lbl = Gtk.Label(label=label_text, xalign=0.0)
    lbl.set_hexpand(True)
    box.append(lbl)

    if has_chevron:
        chev = Gtk.Image.new_from_icon_name("maestri-chevron-right-symbolic")
        chev.add_css_class("maestri-menu-chevron")
        box.append(chev)

    btn.set_child(box)
    btn.maestri_label = label_text
    btn.maestri_checked = is_checked
    if callback is not None:
        btn.connect("clicked", lambda _b: callback())
    return btn


@dataclass
class Item:
    key: str
    label: str
    icon: str
    action: Optional[Callable[[], None]] = None   # None + submenu = opens that page
    submenu: Optional[str] = None
    checked: bool = False
    danger: bool = False
    sensitive: bool = True
    tooltip: str = ""


class _StackMenu(Gtk.Popover):
    """A popover of pages (main + submenus, each with a back header). Subclasses fill `self.pages` and call `_build()`.

    For tests and callers: `rows[key]` -> Gtk.Button, `layout[page]` -> keys in order (SEPARATOR = None),
    `activate(key)` clicks a row like the user would (no-op if insensitive)."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.cw = window.canvas_widget
        self.set_has_arrow(False)
        self.add_css_class("maestri-context-popover")
        self.pages: dict[str, tuple[str, list[Optional[Item]]]] = {}   # name -> (title, items)
        self.rows: dict[str, Gtk.Button] = {}
        self.layout: dict[str, list[Optional[str]]] = {}
        self._parent_page: dict[str, str] = {}
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=150)
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(True)

    def add_page(self, name: str, title: str, items: list[Optional[Item]], parent: str = "main") -> None:
        self.pages[name] = (title, items)
        if name != "main":
            self._parent_page[name] = parent

    def _build(self) -> None:
        for name, (title, items) in self.pages.items():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4,
                          margin_start=4, margin_end=4)
            if name != "main":
                hdr = Gtk.Box(spacing=6)
                back = make_menu_row("maestri-chevron-right-symbolic", title,
                                     callback=lambda p=self._parent_page[name]: self.show_page(p))
                back.get_child().get_first_child().add_css_class("maestri-menu-back")   # chevron rotated to point back
                back.set_hexpand(True)
                hdr.append(back)
                box.append(hdr)
                box.append(Gtk.Separator())
            keys: list[Optional[str]] = []
            for it in items:
                if it is SEPARATOR:
                    if keys and keys[-1] is not None:
                        box.append(Gtk.Separator())
                        keys.append(None)
                    continue
                cb = it.action if it.action is not None else (lambda p=it.submenu: self.show_page(p)) if it.submenu else None
                row = make_menu_row(it.icon, it.label, callback=cb, is_danger=it.danger,
                                    has_chevron=it.submenu is not None, is_checked=it.checked)
                row.set_sensitive(it.sensitive)
                if it.tooltip:
                    row.set_tooltip_text(it.tooltip)
                self.rows[it.key] = row
                keys.append(it.key)
                box.append(row)
            if keys and keys[-1] is None:   # no trailing separator
                box.remove(box.get_last_child())
                keys.pop()
            self.layout[name] = keys
            self.stack.add_named(box, name)
        self.stack.set_visible_child_name("main")
        self.set_child(self.stack)

    def show_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def activate(self, key: str) -> bool:
        row = self.rows.get(key)
        if row is None or not row.get_sensitive():
            return False
        row.emit("clicked")
        return True

    # ---- shared actions ---------------------------------------------------------------------------------------
    def _close_then(self, fn: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            self.popdown()
            fn()
        return run

    def _contract(self, key: str, label: str, icon: str, method: str, *args, danger: bool = False) -> Item:
        """A row that calls window.<method>(*args); disabled (with the reason) while that method does not exist."""
        fn = getattr(self.window, method, None)
        if not callable(fn):
            return Item(key, label, icon, action=lambda: None, sensitive=False, danger=danger,
                        tooltip=f"Not available yet: window.{method}() is not implemented")
        return Item(key, label, icon, action=self._close_then(lambda: fn(*args)), danger=danger)

    def _partituras_items(self, at: Point, node_ids: Optional[list[str]]) -> list[Optional[Item]]:
        """Partituras ▸ : saved layouts (filled asynchronously) + Save layout… (K11)."""
        items: list[Optional[Item]] = []
        apply = getattr(self.window, "apply_partitura", None)
        for p in self._partituras:
            pid, name = str(p.get("id") or p.get("name") or ""), str(p.get("name") or p.get("id") or "Partitura")
            if callable(apply):
                items.append(Item(f"partitura:{pid}", name, "maestri-book-open-symbolic",
                                  action=self._close_then(lambda i=pid: apply(i, at))))
            else:
                items.append(Item(f"partitura:{pid}", name, "maestri-book-open-symbolic", sensitive=False,
                                  tooltip="Not available yet: window.apply_partitura() is not implemented"))
        if not items:
            items.append(Item("partituras-empty", "No saved partituras", "maestri-book-open-symbolic", sensitive=False))
        items.append(SEPARATOR)
        label = "Save Layout with This Terminal…" if node_ids else "Save Layout…"
        save = getattr(self.window, "save_partitura", None)
        if callable(save):
            items.append(Item("partitura-save", label, "maestri-plus-symbolic",
                              action=self._close_then(lambda: _ask_partitura_name(self.window, node_ids))))
        else:
            items.append(Item("partitura-save", label, "maestri-plus-symbolic", sensitive=False,
                              tooltip="Not available yet: window.save_partitura() is not implemented"))
        return items

    _partituras: list[dict] = []


def list_partituras(window) -> list[dict]:
    """Saved partituras: window.list_partituras() if Vertice provides it, else GET /api/partituras (K11). [] on failure.
    Blocking: call it off the main thread."""
    fn = getattr(window, "list_partituras", None)
    try:
        if callable(fn):
            return list(fn() or [])
        client = window.client
        r = client.session.get(f"{client.base_url}/api/partituras", headers=client._headers(), timeout=2.0)
        if r.status_code != 200:
            return []
        data = r.json()
        items = data.get("partituras", data) if isinstance(data, dict) else data
        return [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []
    except Exception:
        return []


def _ask_partitura_name(window, node_ids: Optional[list[str]]) -> None:
    """Small modal: the partitura name; OK -> window.save_partitura(name, node_ids)."""
    dlg = Gtk.Window(title="Save Partitura", transient_for=window, modal=True, resizable=False)
    dlg.add_css_class("maestri-modal-window")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18, margin_start=20,
                  margin_end=20)
    box.append(Gtk.Label(label="Partitura name", xalign=0))
    entry = Gtk.Entry(placeholder_text="Team layout", activates_default=True)
    box.append(entry)
    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel, ok = Gtk.Button(label="Cancel"), Gtk.Button(label="Save")
    ok.add_css_class("suggested-action")
    buttons.append(cancel)
    buttons.append(ok)
    box.append(buttons)
    dlg.set_child(box)
    dlg.set_default_widget(ok)

    def close() -> None:
        try:
            dlg.set_visible(False)
            dlg.set_modal(False)
            dlg.set_transient_for(None)
        except Exception:
            pass
        dlg.close()

    def save(*_a) -> None:
        name = entry.get_text().strip()
        if not name:
            entry.add_css_class("error")
            return
        close()
        window.save_partitura(name, node_ids)

    ok.connect("clicked", save)
    entry.connect("activate", save)
    cancel.connect("clicked", lambda _b: close())
    window.partitura_name_dialog = dlg
    dlg.present()


def _load_partituras_async(menu: _StackMenu, rebuild: Callable[[], None]) -> None:
    def work() -> None:
        items = list_partituras(menu.window)
        if items:
            GLib.idle_add(lambda: (rebuild_with(items), GLib.SOURCE_REMOVE)[1])

    def rebuild_with(items: list[dict]) -> None:
        menu._partituras = items
        rebuild()

    threading.Thread(target=work, name="maestri-partituras", daemon=True).start()


# ---------------------------------------------------------------------------------------------------------------------
# canvas background
# ---------------------------------------------------------------------------------------------------------------------
class CanvasContextMenu(_StackMenu):
    """Right-click on the empty canvas (uploaded_media_1)."""

    def __init__(self, window, canvas_point: Point, load_partituras: bool = True) -> None:
        super().__init__(window)
        self.pt = canvas_point
        self._partituras = []
        self._fill()
        self._build()
        if load_partituras:
            _load_partituras_async(self, self._refill_partituras)

    def _fill(self) -> None:
        from .terminal_dialog import PRESETS
        self.add_page("main", "", [
            Item("add", "Add", "maestri-plus-symbolic", submenu="add"),
            self._contract("paste", "Paste", "maestri-paste-symbolic", "paste_nodes", self.pt),
            SEPARATOR,
            Item("partituras", "Partituras", "maestri-book-open-symbolic", submenu="partituras"),
        ])
        self.add_page("add", "Add", [
            Item("add-terminal", "Terminal", "maestri-terminal-symbolic", submenu="terminal"),
            Item("add-note", "Note", "maestri-note-symbolic", action=self._close_then(lambda: self._create("stickyNote"))),
            Item("add-file-tree", "File Tree", "maestri-file-tree-symbolic",
                 action=self._close_then(lambda: self._create("fileTree"))),
            Item("add-portal", "Portal", "maestri-globe-symbolic", action=self._close_then(lambda: self._create("portal"))),
            Item("add-text", "Text", "maestri-text-symbolic", action=self._close_then(self._add_text)),
        ])
        presets = [Item(f"preset:{p['id']}", p["name"], f"maestri-{p['icon']}-symbolic",
                        action=self._close_then(lambda pid=p["id"]: self.window.create_terminal_preset(pid, at=self.pt)))
                   for p in PRESETS]
        self.add_page("terminal", "Terminal", presets + [
            SEPARATOR,
            Item("terminal-custom", "Custom…", "maestri-sliders-symbolic",
                 action=self._close_then(lambda: self.window.open_new_terminal_dialog(at=self.pt))),
        ], parent="add")
        self.add_page("partituras", "Partituras", self._partituras_items(self.pt, None))

    def _refill_partituras(self) -> None:
        page = self.stack.get_child_by_name("partituras")
        visible = self.stack.get_visible_child_name()
        for name in list(self.pages):
            child = self.stack.get_child_by_name(name)
            if child is not None:
                self.stack.remove(child)
        self.pages.clear()
        self.rows.clear()
        self.layout.clear()
        del page
        self._fill()
        self._build()
        self.stack.set_visible_child_name(visible or "main")

    def _create(self, kind: str) -> None:
        if kind == "portal":
            from .portal_prompt import ask_portal_url
            ask_portal_url(self.window, lambda name, url: self.window._create_node(
                "portal", at=self.pt, content=_portal_content(name, url)))
            return
        self.window._create_node(kind, at=self.pt)

    def _add_text(self) -> None:
        """Add ▸ Text: a label at the clicked point, opened in the inline editor (as when drawn with the T tool)."""
        from maestri_canvas.drawn import TEXT_DEFAULT_SIZE
        node = self.window.drawing.create("text", Rect(self.pt.x, self.pt.y, *TEXT_DEFAULT_SIZE))
        if node is not None:
            GLib.idle_add(lambda: (self.cw.edit_drawn_text(node.id), GLib.SOURCE_REMOVE)[1])


def _portal_content(name: str, url: str):
    from maestri_core.models.node import PortalContent
    return PortalContent(name=name or "Portal", current_url=url)


# ---------------------------------------------------------------------------------------------------------------------
# node menus
# ---------------------------------------------------------------------------------------------------------------------
class NodeContextMenu(_StackMenu):
    """Right-click on a node: one variant per kind (see the module docstring)."""

    def __init__(self, window, node_id: str, load_partituras: bool = True) -> None:
        super().__init__(window)
        self.node_id = norm(node_id)
        self.node = window._find_node(node_id)
        self.kind = getattr(self.node, "node_type", None) or getattr(self.cw.scene.get(self.node_id), "kind", "")
        self._partituras = []
        self._fill()
        self._build()
        if load_partituras and self.kind in ("terminal", "stickyNote"):
            _load_partituras_async(self, self._refill)

    def _refill(self) -> None:
        visible = self.stack.get_visible_child_name()
        for name in list(self.pages):
            child = self.stack.get_child_by_name(name)
            if child is not None:
                self.stack.remove(child)
        self.pages.clear()
        self.rows.clear()
        self.layout.clear()
        self._fill()
        self._build()
        self.stack.set_visible_child_name(visible or "main")

    # ---- state ------------------------------------------------------------------------------------------------
    def _frame(self):
        return self.cw.frames.get(self.node_id)

    def _term(self):
        from maestri_canvas.widget import terminal_of
        f = self._frame()
        return terminal_of(f.content) if f is not None else None

    @property
    def locked(self) -> bool:
        item = self.cw.scene.get(self.node_id)
        return bool((item is not None and item.locked) or getattr(self.node, "is_locked", False))

    # ---- layout -----------------------------------------------------------------------------------------------
    def _fill(self) -> None:
        k = self.kind
        content = getattr(self.node, "content", None)
        locked = self.locked
        common = [
            self._contract("copy-element", "Copy Element", "maestri-copy-symbolic", "copy_node", self.node_id),
            Item("duplicate", "Duplicate", "maestri-duplicate-symbolic", action=self._close_then(self._duplicate)),
            Item("lock", "Unlock" if locked else "Lock", "maestri-lock-open-symbolic" if locked else "maestri-lock-symbolic",
                 action=self._close_then(self._toggle_lock)),
            SEPARATOR,
            Item("delete", "Delete", "maestri-trash-symbolic", action=self._close_then(self._delete), danger=True,
                 sensitive=not locked, tooltip="Unlock the element to delete it" if locked else ""),
        ]
        if k == "terminal":
            items = [
                Item("copy", "Copy", "maestri-copy-symbolic", action=self._close_then(self._copy)),
                Item("paste", "Paste", "maestri-paste-symbolic", action=self._close_then(self._paste)),
                Item("select-all", "Select All", "maestri-select-all-symbolic", action=self._close_then(self._select_all)),
                SEPARATOR,
                Item("rename", "Rename", "maestri-pencil-symbolic", action=self._close_then(self._rename)),
                Item("edit", "Edit…", "maestri-sliders-symbolic", action=self._close_then(self._edit)),
                Item("monitor", "Monitor Activity", "maestri-eye-symbolic", action=self._close_then(self._toggle_monitor),
                     checked=bool(getattr(content, "monitor_with_ombro", False))),
                SEPARATOR,
                Item("assign-role", "Assign Role", "maestri-masks-symbolic", submenu="roles"),
                Item("maestro", "Disable Maestro" if getattr(content, "is_manager", False) else "Enable Maestro",
                     "maestri-sparkles-symbolic", action=self._close_then(self._toggle_maestro)),
                SEPARATOR,
                Item("blur", "Blur", "maestri-eye-off-symbolic", action=self._close_then(self._toggle_blur),
                     checked=self._blurred()),
                self._contract("reload", "Reload", "maestri-reload-symbolic", "reload_terminal", self.node_id),
                self._contract("unload", "Unload", "maestri-moon-symbolic", "unload_terminal", self.node_id),
                Item("defaults", "New Element Defaults", "maestri-toggles-symbolic", submenu="defaults"),
                SEPARATOR,
                *common,
                SEPARATOR,
                Item("partituras", "Partituras", "maestri-book-open-symbolic", submenu="partituras"),
            ]
            self.add_page("roles", "Assign Role", self._role_items(content))
            self.add_page("defaults", "New Element Defaults", self._defaults_items("terminal"))
            self.add_page("partituras", "Partituras", self._partituras_items(self._below(), [self.node_id]))
        elif k == "stickyNote":
            previewing = bool(getattr(getattr(self._frame(), "content", None), "meta", None)
                              and self._frame().content.meta.is_previewing)
            items = [
                Item("rename", "Rename", "maestri-pencil-symbolic", action=self._close_then(self._rename)),
                Item("move", "Move to…", "maestri-folder-open-symbolic", action=self._close_then(self._move_note)),
                Item("raw" if previewing else "formatted", "Show Raw Markdown" if previewing else "Show Formatted",
                     "maestri-file-text-symbolic", action=self._close_then(self._toggle_note_mode)),
                SEPARATOR,
                Item("defaults", "New Element Defaults", "maestri-toggles-symbolic", submenu="defaults"),
                SEPARATOR,
                *common,
                SEPARATOR,
                Item("partituras", "Partituras", "maestri-book-open-symbolic", submenu="partituras"),
            ]
            self.add_page("defaults", "New Element Defaults", self._defaults_items("stickyNote"))
            self.add_page("partituras", "Partituras", self._partituras_items(self._below(), [self.node_id]))
        elif k == "portal":
            items = [Item("reload", "Reload", "maestri-reload-symbolic", action=self._close_then(self._reload_portal)),
                     SEPARATOR, *common]
        else:   # fileTree, text, shape, freehand
            items = list(common)
        self.add_page("main", "", items)

    def _below(self) -> Point:
        f = getattr(self.node, "frame", None)
        return Point(f.x, f.y + f.height + 48) if f is not None else Point(0.0, 0.0)

    def _role_items(self, content) -> list[Optional[Item]]:
        current = getattr(content, "assigned_role_id", None)
        items: list[Optional[Item]] = [Item("role:none", "No Role (Clear)", "maestri-close-symbolic",
                                            action=self._close_then(lambda: self._assign_role(None)), checked=current is None)]
        roles = role_presets(self.window)
        if roles:
            items.append(SEPARATOR)
        for r in roles:
            rid = str(r.get("id", "")).upper()
            items.append(Item(f"role:{rid}", str(r.get("name", "Role")), "maestri-masks-symbolic",
                              action=self._close_then(lambda i=rid: self._assign_role(i)),
                              checked=bool(current) and str(current).upper() == rid))
        if not roles:
            items.append(Item("roles-empty", "Add roles in Settings to get started", "maestri-settings-symbolic",
                              action=self._close_then(self._open_agents_settings)))
        return items

    def _defaults_items(self, kind: str) -> list[Optional[Item]]:
        noun = "Terminals" if kind == "terminal" else "Notes"
        has = bool(new_element_defaults(self.window, kind))
        return [
            Item("defaults-size", f"Use This Size for New {noun}", "maestri-square-symbolic",
                 action=self._close_then(lambda: self._save_defaults(kind, "size"))),
            Item("defaults-appearance", f"Use This Appearance for New {noun}", "maestri-brush-symbolic",
                 action=self._close_then(lambda: self._save_defaults(kind, "appearance"))),
            SEPARATOR,
            Item("defaults-reset", "Reset Defaults", "maestri-reload-symbolic",
                 action=self._close_then(lambda: self._save_defaults(kind, None)), sensitive=has),
        ]

    # ---- terminal clipboard (P0: terminal_of(frame.content), not a `.terminal` attribute) -----------------------
    def _copy(self) -> None:
        term = self._term()
        if term is None:
            return
        from gi.repository import Vte
        if term.get_has_selection():
            term.copy_clipboard_format(Vte.Format.TEXT)

    def _paste(self) -> None:
        term = self._term()
        if term is not None:
            term.paste_clipboard()
            term.grab_focus()

    def _select_all(self) -> None:
        term = self._term()
        if term is not None:
            term.select_all()

    # ---- identity ------------------------------------------------------------------------------------------------
    def _rename(self) -> None:
        f = self._frame()
        if f is None:
            return
        begin = getattr(f.content, "begin_rename", None)   # a note renames through its own EditableLabel title
        if callable(begin):
            begin()
        else:
            f.start_inline_rename()

    def _edit(self) -> None:
        self.window.edit_selected_node(self.node_id)

    def _changed(self) -> None:
        """Persist a content change of this node (WorkspaceSync listens to node-updated)."""
        bus.emit("node-updated", self.node_id)
        if getattr(self.window, "sync", None) is not None:
            self.window.sync.flush()

    def _toggle_monitor(self) -> None:
        c = getattr(self.node, "content", None)
        if c is None:
            return
        c.monitor_with_ombro = not c.monitor_with_ombro   # serialized as monitorWithOmbro
        self._changed()

    def _toggle_maestro(self) -> None:
        c = getattr(self.node, "content", None)
        if c is None:
            return
        c.is_manager = not c.is_manager   # serialized as isManager (K8: recruit reads it from workspace.json)
        f = self._frame()
        if f is not None:
            f.set_badge("maestro", "" if c.is_manager else None, "Maestro", icon="maestri-sparkles-symbolic")
        self._changed()

    def _assign_role(self, role_id: Optional[str]) -> None:
        c = getattr(self.node, "content", None)
        if c is None:
            return
        c.assigned_role_id = role_id
        self._changed()

    def _open_agents_settings(self) -> None:
        self.window.show_preferences()
        sw = getattr(self.window, "settings_window", None)
        if sw is not None and hasattr(sw, "show_tab"):
            sw.show_tab("agents")

    # ---- view ------------------------------------------------------------------------------------------------------
    def _blurred(self) -> bool:
        f = self._frame()
        return bool(f is not None and f.has_css_class("maestri-node-blurred"))

    def _toggle_blur(self) -> None:
        f = self._frame()
        if f is not None:
            (f.remove_css_class if f.has_css_class("maestri-node-blurred") else f.add_css_class)("maestri-node-blurred")

    def _reload_portal(self) -> None:
        f = self._frame()
        if f is not None and callable(getattr(f.content, "reload", None)):
            f.content.reload()

    def _move_note(self) -> None:
        f = self._frame()
        if f is not None:
            f.content.choose_folder_and_move()

    def _toggle_note_mode(self) -> None:
        f = self._frame()
        if f is not None:
            f.content.set_previewing(not f.content.meta.is_previewing)

    # ---- element ----------------------------------------------------------------------------------------------------
    def _duplicate(self) -> None:
        if self.node is not None:
            f = self.node.frame
            self.cw.emit("duplicate-requested", {self.node_id: Rect(f.x + 30, f.y + 30, f.width, f.height)})

    def _toggle_lock(self) -> None:
        locked = not self.locked
        item = self.cw.scene.get(self.node_id)
        if item is not None:
            item.locked = locked
            self.cw.controller.dirty = True
        f = self._frame()
        if f is not None:
            f.set_badge("lock", "" if locked else None, "Locked", icon="maestri-lock-symbolic")
        if GObject.signal_lookup("node-lock-changed", type(self.cw)):
            self.cw.emit("node-lock-changed", self.node_id, locked)   # K9: Vertice persists CanvasNode.is_locked
        elif self.node is not None:   # until the K9 signal exists, persist the field directly
            self.node.is_locked = locked
            self._changed()

    def _delete(self) -> None:
        if not self.locked:
            self.cw.emit("delete-requested", (self.node_id,))

    def _save_defaults(self, kind: str, what: Optional[str]) -> None:
        save_new_element_defaults(self.window, kind, self.node, what)


# ---------------------------------------------------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------------------------------------------------
class ConnectionContextMenu(_StackMenu):
    """Right-click on a rope (K9 ConnectionTarget): Delete Connection -> window.delete_connection(conn_id)."""

    def __init__(self, window, conn_id: str) -> None:
        super().__init__(window)
        self.conn_id = conn_id
        self.add_page("main", "", [self._contract("delete-connection", "Delete Connection", "maestri-trash-symbolic",
                                                  "delete_connection", conn_id, danger=True)])
        self._build()


# ---------------------------------------------------------------------------------------------------------------------
# preferences helpers (rolePresets / newElementDefaults live in preferences.json, shared with macOS; kept in _extra)
# ---------------------------------------------------------------------------------------------------------------------
def role_presets(window) -> list[dict]:
    prefs = getattr(window, "preferences", None)
    roles = getattr(prefs, "_extra", {}).get("rolePresets") if prefs is not None else None
    return [r for r in roles if isinstance(r, dict) and r.get("id")] if isinstance(roles, list) else []


def new_element_defaults(window, kind: str) -> dict:
    """preferences.newElementDefaults[kind] = {"size": [w, h], "appearance": {...}} (Linux-port key; Swift ignores it)."""
    prefs = getattr(window, "preferences", None)
    alld = getattr(prefs, "_extra", {}).get("newElementDefaults") if prefs is not None else None
    d = alld.get(kind) if isinstance(alld, dict) else None
    return dict(d) if isinstance(d, dict) else {}


TERMINAL_APPEARANCE_FIELDS = ("theme_id", "font_family", "font_size", "icon", "color")
NOTE_APPEARANCE_FIELDS = ("color", "font_size")


def save_new_element_defaults(window, kind: str, node, what: Optional[str]) -> None:
    """what = "size" | "appearance" | None (reset). Persists preferences.json and notifies the window."""
    prefs = window.preferences
    alld = prefs._extra.get("newElementDefaults")
    alld = dict(alld) if isinstance(alld, dict) else {}
    cur = dict(alld.get(kind) or {})
    if what is None:
        cur = {}
    elif what == "size" and node is not None:
        cur["size"] = [float(node.frame.width), float(node.frame.height)]
    elif what == "appearance" and node is not None:
        c = node.content
        fields = TERMINAL_APPEARANCE_FIELDS if kind == "terminal" else NOTE_APPEARANCE_FIELDS
        cur["appearance"] = {f: getattr(c, f) for f in fields if getattr(c, f, None) is not None}
    if cur:
        alld[kind] = cur
    else:
        alld.pop(kind, None)
    prefs._extra["newElementDefaults"] = alld
    window.prefs_store.save(prefs)
    bus.emit("preferences-changed") if GObject.signal_lookup("preferences-changed", type(bus)) else None


def apply_new_element_defaults(window, node) -> None:
    """Apply preferences.newElementDefaults to a NEW node (size + appearance). Call at creation, before adding it to the
    canvas; never on load."""
    kind = getattr(node, "node_type", "")
    d = new_element_defaults(window, kind)
    size = d.get("size")
    if isinstance(size, list) and len(size) == 2:
        try:
            node.frame.width, node.frame.height = float(size[0]), float(size[1])
        except (TypeError, ValueError):
            pass
    appearance = d.get("appearance")
    if isinstance(appearance, dict):
        allowed = TERMINAL_APPEARANCE_FIELDS if kind == "terminal" else NOTE_APPEARANCE_FIELDS
        for f, v in appearance.items():
            if f in allowed and hasattr(node.content, f):
                setattr(node.content, f, v)


# ---------------------------------------------------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------------------------------------------------
def _popup(window, pop: Gtk.Popover, x: float, y: float) -> None:
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    pop.set_parent(window.canvas_widget)
    pop.set_pointing_to(rect)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), GLib.SOURCE_REMOVE)[1]))
    pop.popup()
    window._ctx_popover = pop


def menu_for(window, target, canvas_pt: Point) -> Optional[Gtk.Popover]:
    """The menu a right click on `target` opens, or None to let the child widget handle it."""
    from maestri_canvas.interaction import Background, NodeContent
    from maestri_canvas.widget import terminal_of
    cw = window.canvas_widget
    conn_id = getattr(target, "conn_id", None)
    if conn_id:
        return ConnectionContextMenu(window, conn_id)
    if isinstance(target, Background):
        hit = getattr(cw.scene, "hit_connection", None)   # K9: a rope under an otherwise empty point
        conn = hit(canvas_pt) if callable(hit) else None
        if conn is not None:
            return ConnectionContextMenu(window, conn.id)
        return CanvasContextMenu(window, canvas_pt)
    node_id = getattr(target, "id", None)
    if not node_id:
        return None
    if isinstance(target, NodeContent):
        frame = cw.frames.get(norm(node_id))
        if frame is not None and terminal_of(frame.content) is None:
            return None   # note text view, portal page, file tree / Git menus keep their own right click
    return NodeContextMenu(window, str(node_id))


def open_context_menu(window, gesture: Gtk.GestureClick, x: float, y: float) -> Optional[Gtk.Popover]:
    """Right-click dispatcher (CAPTURE phase on the canvas widget)."""
    cw = window.canvas_widget
    pt = cw.viewport.to_canvas(Point(x, y))
    window._ctx_point = pt
    pop = menu_for(window, cw.target_at(x, y), pt)
    if pop is None:
        gesture.set_state(Gtk.EventSequenceState.DENIED)
        return None
    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
    _popup(window, pop, x, y)
    return pop
