"""
Sidebar View for Open Maestri GTK 4 Native.
Matches the official macOS Maestri sidebar design (Sources/Workspace/WorkspaceSidebarView.swift):
- Rounded search entry ('Search Workspaces')
- Collapsible Groups (DisclosureGroup / SidebarGroup)
- Card-based workspace items with custom icons, color tints, path, and terminal count capsules
- Full Workspace Functions Context Menu:
  * Edit Workspace… (opens EditWorkspaceDialog)
  * Duplicate Workspace (creates copy)
  * Open in File Manager (xdg-open on working directory)
  * Pin / Unpin Workspace (with pin icon and pinned sorting)
  * Move to Group / Remove from Group (with group submenu)
  * Delete Workspace… (with confirmation alert)
- Floors (Worktrees) section
- Canvas Agents section
"""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_core.client.api import MaestriClient
from maestri_core.models.workspace import SidebarGroup, SidebarLayout, WorkspaceEntry
from maestri_core.signals.bus import bus

from .workspace_dialogs import (
    CreateWorkspaceDialog,
    DeleteWorkspaceDialog,
    EditWorkspaceDialog,
    GroupDialog,
)


ICON_MAP = {
    "folder": "📁",
    "folder.fill": "📂",
    "terminal.fill": "💻",
    "terminal": "💻",
    "cpu": "⚙️",
    "brain": "🧠",
    "desktopcomputer": "🖥️",
    "network": "🌐",
    "server.rack": "🗄️",
    "star.fill": "⭐",
    "theatermasks": "🎭",
    "gear": "⚙️",
    "bubble.left.and.bubble.right": "💬",
    "globe": "🌐",
    "hammer.fill": "🔨",
    "wrench.and.screwdriver": "🔧",
    "bolt.fill": "⚡",
    "square.on.square": "⏹️",
    "photo.on.rectangle.angled": "📸",
    "laptopcomputer": "💻",
    "paintbrush.fill": "🎨",
    "doc.text": "📄",
    "shippingbox": "📦",
    "cube": "🧊",
    "eye": "👁️",
    "face.smiling": "😀",
}


class SidebarView(Gtk.Box):
    def __init__(self, client: MaestriClient) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.client = client
        self.set_size_request(260, -1)
        self.add_css_class("sidebar-view")
        self.add_css_class("maestri-sidebar")

        # Top padding & container
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # 1. Search Bar
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search Workspaces")
        self.search_entry.add_css_class("maestri-search-entry")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # 2. Scrollable container
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.content_box.set_margin_top(6)

        # 2.1 Workspaces Section
        self.ws_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ws_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ws_header = Gtk.Label(label="<b>WORKSPACES</b>", use_markup=True, xalign=0.0)
        ws_header.add_css_class("maestri-sidebar-header")
        ws_header.set_hexpand(True)
        ws_header_box.append(ws_header)

        btn_add_ws = Gtk.Button(label="＋")
        btn_add_ws.add_css_class("maestri-sidebar-add-btn")
        btn_add_ws.set_tooltip_text("New Workspace or Group")
        btn_add_ws.connect("clicked", self._on_add_header_clicked)
        ws_header_box.append(btn_add_ws)

        self.ws_box.append(ws_header_box)

        # Container for grouped and top-level workspaces
        self.groups_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.ws_box.append(self.groups_container)

        self.ws_list_box = Gtk.ListBox()
        self.ws_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.ws_list_box.add_css_class("maestri-workspace-list")
        self.ws_list_box.connect("row-activated", self._on_ws_selected)
        self.ws_box.append(self.ws_list_box)
        self.content_box.append(self.ws_box)

        scrolled.set_child(self.content_box)
        self.append(scrolled)

        self._raw_workspaces: list[WorkspaceEntry] = []
        self._sidebar_layout: SidebarLayout = SidebarLayout()
        self._active_workspace_id: Optional[str] = None

        self.refresh()

    def set_active_workspace_id(self, ws_id: str) -> None:
        self._active_workspace_id = ws_id

    def refresh(self) -> None:
        """Fetch workspaces and sidebar layout asynchronously in background thread."""
        def worker():
            try:
                workspaces = self.client.get_workspaces()
            except Exception:
                workspaces = []
            try:
                layout = self.client.get_sidebar_layout()
            except Exception:
                layout = SidebarLayout()
            GLib.idle_add(self._apply_refresh, workspaces, layout)

        threading.Thread(target=worker, name="maestri-sidebar-refresh", daemon=True).start()

    def _apply_refresh(self, workspaces: list[WorkspaceEntry], layout: SidebarLayout) -> bool:
        self._raw_workspaces = workspaces
        self._sidebar_layout = layout
        self._render_all(workspaces)
        return GLib.SOURCE_REMOVE

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip().lower()
        if not text:
            self._render_all(self._raw_workspaces)
        else:
            filtered = [
                ws for ws in self._raw_workspaces
                if text in ws.name.lower() or text in ws.working_directory.lower()
            ]
            self._render_all(filtered, is_search=True)

    def _render_all(self, workspaces: list[WorkspaceEntry], is_search: bool = False) -> None:
        # Clear groups container and list box
        while self.groups_container.get_first_child():
            self.groups_container.remove(self.groups_container.get_first_child())
        while self.ws_list_box.get_first_child():
            self.ws_list_box.remove(self.ws_list_box.get_first_child())

        if not workspaces:
            default_entry = WorkspaceEntry(
                id="default",
                name="Default Workspace",
                working_directory=os.path.expanduser("~"),
                icon="folder",
                color="blue",
            )
            card = self._create_workspace_card(default_entry, count=2)
            self.ws_list_box.append(card)
            return

        # Sort: pinned first, then by name or order
        all_by_id = {ws.id.upper(): ws for ws in workspaces}
        grouped_ids = set()
        for g in self._sidebar_layout.groups:
            for it in g.items:
                grouped_ids.add(it.upper())

        # Render Groups (if not searching)
        if not is_search and self._sidebar_layout.groups:
            for group in self._sidebar_layout.groups:
                g_box = self._create_group_box(group, all_by_id)
                self.groups_container.append(g_box)

        # Render Top-Level Workspaces
        top_level = [
            ws for ws in workspaces
            if is_search or (ws.id.upper() not in grouped_ids)
        ]
        top_level.sort(key=lambda w: (not w.is_pinned, w.name.lower()))

        for ws in top_level:
            card = self._create_workspace_card(ws, count=getattr(ws, "node_count", 2))
            self.ws_list_box.append(card)

    def _create_group_box(self, group: SidebarGroup, all_by_id: dict[str, WorkspaceEntry]) -> Gtk.Box:
        group_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group_vbox.add_css_class("maestri-group-container")

        # Header row
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_row.add_css_class("maestri-group-header")

        chevron_lbl = Gtk.Label(label="▼" if not group.is_collapsed else "▶")
        chevron_lbl.add_css_class("maestri-group-chevron")
        header_row.append(chevron_lbl)

        icon_lbl = Gtk.Label(label="📁")
        header_row.append(icon_lbl)

        name_lbl = Gtk.Label(label=f"<b>{group.name}</b>", use_markup=True, xalign=0.0)
        name_lbl.add_css_class("maestri-group-title")
        name_lbl.set_hexpand(True)
        header_row.append(name_lbl)

        # Toggle collapse on click
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(1)
        click_gesture.connect("pressed", lambda _g, _n, _x, _y, grp=group: self._toggle_group_collapse(grp))
        header_row.add_controller(click_gesture)

        # Right-click context menu on group
        right_click = Gtk.GestureClick()
        right_click.set_button(3)
        right_click.connect("pressed", lambda _g, _n, x, y, grp=group, r=header_row: self._show_group_context_menu(r, x, y, grp))
        header_row.add_controller(right_click)

        group_vbox.append(header_row)

        # Items ListBox
        items_list = Gtk.ListBox()
        items_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        items_list.add_css_class("maestri-workspace-list")
        items_list.add_css_class("maestri-group-items-list")
        items_list.set_margin_start(14)
        items_list.connect("row-activated", self._on_ws_selected)

        for item_id in group.items:
            ws = all_by_id.get(item_id.upper())
            if ws:
                card = self._create_workspace_card(ws, group_id=group.id, count=getattr(ws, "node_count", 2))
                items_list.append(card)

        items_list.set_visible(not group.is_collapsed)
        group_vbox.append(items_list)
        return group_vbox

    def _create_workspace_card(
        self,
        entry: WorkspaceEntry,
        group_id: Optional[str] = None,
        count: int = 1,
    ) -> Gtk.Widget:
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row_box.add_css_class("maestri-workspace-card")

        # Icon with color indicator
        emoji = ICON_MAP.get(entry.icon, "📁")
        icon = Gtk.Label(label=emoji)
        icon.add_css_class("maestri-card-icon")
        color = getattr(entry, "color", "blue") or "blue"
        icon.add_css_class(f"icon-color-{color}")
        row_box.append(icon)

        # Name & Path
        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_vbox.set_hexpand(True)

        name_lbl = Gtk.Label(label=f"<b>{entry.name}</b>", use_markup=True, xalign=0.0, ellipsize=3)
        name_lbl.add_css_class("maestri-card-title")
        path_str = entry.working_directory.replace(os.path.expanduser("~"), "~")
        path_lbl = Gtk.Label(label=path_str, xalign=0.0, ellipsize=3)
        path_lbl.add_css_class("maestri-card-path")

        info_vbox.append(name_lbl)
        info_vbox.append(path_lbl)
        row_box.append(info_vbox)

        # Right Badges (Unread + Terminal count + Pin)
        badges_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        if getattr(entry, "is_pinned", False):
            pin_lbl = Gtk.Label(label="📌")
            pin_lbl.add_css_class("maestri-card-pin")
            badges_box.append(pin_lbl)

        badge = Gtk.Label(label=f"🗂️ {count}")
        badge.add_css_class("maestri-card-badge")
        badges_box.append(badge)

        row_box.append(badges_box)
        row_box.ws_id = entry.id
        row_box.entry = entry

        # Right-Click Context Menu on Workspace Card
        rc_gesture = Gtk.GestureClick()
        rc_gesture.set_button(3)
        rc_gesture.connect(
            "pressed",
            lambda _g, _n, x, y, ent=entry, gid=group_id, w=row_box: self._show_workspace_context_menu(w, x, y, ent, gid)
        )
        row_box.add_controller(rc_gesture)

        return row_box

    # ---- Workspace Context Menu (macOS Parity) -------------------------------------------------------------
    def _show_workspace_context_menu(
        self,
        anchor: Gtk.Widget,
        x: float,
        y: float,
        entry: WorkspaceEntry,
        group_id: Optional[str] = None,
    ) -> None:
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        popover.set_has_arrow(False)
        popover.add_css_class("maestri-context-popover")

        menu_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_vbox.set_margin_top(6)
        menu_vbox.set_margin_bottom(6)
        menu_vbox.set_margin_start(6)
        menu_vbox.set_margin_end(6)

        # 1. Edit Workspace…
        btn_edit = self._make_menu_item("✏️  Edit Workspace…", lambda: self._on_edit_workspace(entry, popover))
        menu_vbox.append(btn_edit)

        # 2. Duplicate Workspace
        btn_dup = self._make_menu_item("📋  Duplicate Workspace", lambda: self._on_duplicate_workspace(entry, popover))
        menu_vbox.append(btn_dup)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 3. Open in File Manager (Finder equivalent)
        btn_finder = self._make_menu_item("📂  Open in File Manager", lambda: self._on_open_in_file_manager(entry, popover))
        menu_vbox.append(btn_finder)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 4. Pin / Unpin Workspace
        pin_label = "📌  Unpin Workspace" if entry.is_pinned else "📌  Pin Workspace"
        btn_pin = self._make_menu_item(pin_label, lambda: self._on_toggle_pin(entry, popover))
        menu_vbox.append(btn_pin)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 5. Groups (Move to Group / Remove from Group)
        if group_id:
            btn_remove_grp = self._make_menu_item(
                "📁  Remove from Group",
                lambda: self._on_remove_from_group(entry, group_id, popover)
            )
            menu_vbox.append(btn_remove_grp)
        elif self._sidebar_layout.groups:
            for g in self._sidebar_layout.groups:
                btn_g = self._make_menu_item(
                    f"📁  Move to: {g.name}",
                    lambda grp_id=g.id: self._on_move_to_group(entry, grp_id, popover)
                )
                menu_vbox.append(btn_g)

        btn_new_grp = self._make_menu_item("➕  New Group with Workspace…", lambda: self._on_new_group_with_workspace(entry, popover))
        menu_vbox.append(btn_new_grp)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 6. Delete Workspace (Destructive)
        btn_delete = self._make_menu_item("🗑  Delete Workspace…", lambda: self._on_delete_workspace(entry, popover), is_danger=True)
        menu_vbox.append(btn_delete)

        popover.set_child(menu_vbox)
        popover.popup()

    def _show_group_context_menu(
        self,
        anchor: Gtk.Widget,
        x: float,
        y: float,
        group: SidebarGroup,
    ) -> None:
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        popover.set_has_arrow(False)
        popover.add_css_class("maestri-context-popover")

        menu_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_vbox.set_margin_top(6)
        menu_vbox.set_margin_bottom(6)
        menu_vbox.set_margin_start(6)
        menu_vbox.set_margin_end(6)

        btn_rename = self._make_menu_item("✏️  Rename Group…", lambda: self._on_rename_group(group, popover))
        menu_vbox.append(btn_rename)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        btn_del = self._make_menu_item("🗑  Delete Group", lambda: self._on_delete_group(group, popover), is_danger=True)
        menu_vbox.append(btn_del)

        popover.set_child(menu_vbox)
        popover.popup()

    def _make_menu_item(self, label: str, callback: Callable[[], None], is_danger: bool = False) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.add_css_class("maestri-menu-item-btn")
        if is_danger:
            btn.add_css_class("danger")
        btn.set_has_frame(False)
        btn.connect("clicked", lambda _b: callback())
        return btn

    # ---- Actions -------------------------------------------------------------------------------------------
    def _on_edit_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_save(updated: WorkspaceEntry):
            def worker():
                self.client.update_workspace(
                    workspace_id=updated.id,
                    name=updated.name,
                    working_directory=updated.working_directory,
                    icon=updated.icon,
                    color=updated.color,
                    is_pinned=updated.is_pinned,
                )
                self.refresh()
            threading.Thread(target=worker, daemon=True).start()

        dlg = EditWorkspaceDialog(window, entry, handle_save)
        dlg.present()

    def _on_duplicate_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        def worker():
            self.client.duplicate_workspace(entry.id)
            self.refresh()
        threading.Thread(target=worker, daemon=True).start()

    def _on_open_in_file_manager(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        path = os.path.expanduser(entry.working_directory or "~")
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _on_toggle_pin(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        new_pinned = not entry.is_pinned
        def worker():
            self.client.update_workspace(entry.id, is_pinned=new_pinned)
            self.refresh()
        threading.Thread(target=worker, daemon=True).start()

    def _on_delete_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_delete():
            def worker():
                self.client.delete_workspace(entry.id)
                self.refresh()
            threading.Thread(target=worker, daemon=True).start()

        dlg = DeleteWorkspaceDialog(window, entry.name, handle_delete)
        dlg.present()

    def _on_move_to_group(self, entry: WorkspaceEntry, group_id: str, popover: Gtk.Popover) -> None:
        popover.popdown()
        # Remove from other groups
        for g in self._sidebar_layout.groups:
            g.items = [it for it in g.items if it.upper() != entry.id.upper()]
        for g in self._sidebar_layout.groups:
            if g.id.upper() == group_id.upper():
                g.items.append(entry.id.upper())
        self._save_sidebar_layout()

    def _on_remove_from_group(self, entry: WorkspaceEntry, group_id: str, popover: Gtk.Popover) -> None:
        popover.popdown()
        for g in self._sidebar_layout.groups:
            if g.id.upper() == group_id.upper():
                g.items = [it for it in g.items if it.upper() != entry.id.upper()]
        self._save_sidebar_layout()

    def _on_new_group_with_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_new_grp(name: str):
            new_grp = SidebarGroup(name=name, items=[entry.id.upper()])
            self._sidebar_layout.groups.append(new_grp)
            self._save_sidebar_layout()

        dlg = GroupDialog(window, "New Group", on_ok=handle_new_grp)
        dlg.present()

    def _on_add_header_clicked(self, btn: Gtk.Button) -> None:
        popover = Gtk.Popover()
        popover.set_parent(btn)
        popover.set_has_arrow(False)
        popover.add_css_class("maestri-context-popover")

        menu_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_vbox.set_margin_top(6)
        menu_vbox.set_margin_bottom(6)
        menu_vbox.set_margin_start(6)
        menu_vbox.set_margin_end(6)

        btn_new_ws = self._make_menu_item("➕  New Workspace…", lambda: self._on_header_new_workspace(popover))
        menu_vbox.append(btn_new_ws)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        btn_new_grp = self._make_menu_item("📁  New Group…", lambda: self._on_header_new_group(popover))
        menu_vbox.append(btn_new_grp)

        popover.set_child(menu_vbox)
        popover.popup()

    def _on_header_new_workspace(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_created(name: str, wd: str, icon: str):
            def worker():
                self.client.create_workspace(name=name, working_directory=wd, icon=icon)
                self.refresh()
            threading.Thread(target=worker, daemon=True).start()

        dlg = CreateWorkspaceDialog(window, handle_created)
        dlg.present()

    def _on_header_new_group(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_ok(name: str):
            new_grp = SidebarGroup(name=name, items=[])
            self._sidebar_layout.groups.append(new_grp)
            self._save_sidebar_layout()

        dlg = GroupDialog(window, "New Group", on_ok=handle_ok)
        dlg.present()

    def _toggle_group_collapse(self, group: SidebarGroup) -> None:
        group.is_collapsed = not group.is_collapsed
        self._save_sidebar_layout()

    def _on_rename_group(self, group: SidebarGroup, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_rename(name: str):
            group.name = name
            self._save_sidebar_layout()

        dlg = GroupDialog(window, "Rename Group", initial_name=group.name, on_ok=handle_rename)
        dlg.present()

    def _on_delete_group(self, group: SidebarGroup, popover: Gtk.Popover) -> None:
        popover.popdown()
        self._sidebar_layout.groups = [g for g in self._sidebar_layout.groups if g.id != group.id]
        self._save_sidebar_layout()

    def _save_sidebar_layout(self) -> None:
        def worker():
            self.client.save_sidebar_layout(self._sidebar_layout)
            self.refresh()
        threading.Thread(target=worker, daemon=True).start()

    def _on_ws_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        child = row.get_child()
        if hasattr(child, "ws_id"):
            bus.emit("workspace-switched", child.ws_id)
