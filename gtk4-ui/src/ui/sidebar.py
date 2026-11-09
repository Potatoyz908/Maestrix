"""
Sidebar View for Maestrix GTK 4 Native.
Matches the official macOS Maestri sidebar design (Sources/Workspace/WorkspaceSidebarView.swift):
- Rounded search entry ('Search Workspaces')
- Collapsible Groups (DisclosureGroup / SidebarGroup)
- Card-based workspace items with outline icons, color tints, path, and a terminal count capsule (hidden at 0, like macOS)
- Full Workspace Functions Context Menu:
  * Edit Workspace… (opens EditWorkspaceDialog)
  * Duplicate Workspace (creates "<name> Copy")
  * Open in File Manager (xdg-open on working directory)
  * Pin / Unpin Workspace (pinned sort first)
  * Move to Group / Remove from Group / New Group with Workspace…
  * Delete Workspace… (with confirmation alert)
- Group context menu: Rename Group… / Delete Group (its workspaces go back to the top level)

Data (Sprint 8): every action updates the UI at once, then persists through `WorkspaceStore` (core_link.py): the core's REST API
when it is reachable, else the same files the core writes (~/.open-maestri/manifest.json, sidebar-layout.json,
workspaces/<id>/workspace.json). Terminal counts (terminal nodes persisted on the canvas, not live PTYs): the open
workspace uses its in-memory payload (recounted on every node-added / node-removed, so unsaved edits show); the others use the
core's "terminalCount" (GET /api/workspaces, and the bus 'terminal-count-changed' fed by /ws/events) and, when the core is
offline or too old to send it, workspaces/<id>/workspace.json (re-checked every COUNT_POLL_S seconds, re-parsed only on change).
"""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from maestri_core.client.api import MaestriClient
from maestri_core.models.workspace import SidebarGroup, SidebarLayout, WorkspaceEntry
from maestri_core.signals.bus import bus

from . import core_link
from .ssh_dialog import card_location
from .core_link import WorkspaceStore, count_terminals
from .workspace_dialogs import (
    CreateWorkspaceDialog,
    DeleteWorkspaceDialog,
    EditWorkspaceDialog,
    GroupDialog,
    create_workspace_record,
)

COUNT_POLL_S = 5   # terminal counts of the workspaces that are not open (other agents may add terminals there)

# SF Symbol name (workspace "icon" field, shared with macOS) -> outline symbolic icon. Unknown names fall back to the folder.
SYMBOLIC_ICON_MAP = {
    "folder": "maestri-folder-symbolic",
    "folder.fill": "maestri-folder-open-symbolic",
    "terminal": "maestri-terminal-symbolic",
    "terminal.fill": "maestri-terminal-symbolic",
    "cpu": "maestri-cpu-symbolic",
    "brain": "maestri-brain-symbolic",
    "desktopcomputer": "maestri-desktop-symbolic",
    "network": "maestri-network-symbolic",
    "server.rack": "maestri-server-symbolic",
    "star.fill": "maestri-star-symbolic",
    "theatermasks": "maestri-masks-symbolic",
    "gear": "maestri-settings-symbolic",
    "bubble.left.and.bubble.right": "maestri-chat-symbolic",
    "rectangle": "maestri-square-symbolic",
    "list.bullet.rectangle": "maestri-list-box-symbolic",
    "globe": "maestri-globe-symbolic",
    "hammer.fill": "maestri-hammer-symbolic",
    "wrench.and.screwdriver": "maestri-wrench-symbolic",
    "bolt.fill": "maestri-bolt-symbolic",
    "square.on.square": "maestri-squares-symbolic",
    "photo.on.rectangle.angled": "maestri-photo-symbolic",
    "laptopcomputer": "maestri-laptop-symbolic",
    "paintbrush.fill": "maestri-brush-symbolic",
    "doc.text": "maestri-file-text-symbolic",
    "shippingbox": "maestri-box-symbolic",
    "cube": "maestri-cube-symbolic",
    "eye": "maestri-eye-symbolic",
    "face.smiling": "maestri-smile-symbolic",
}
FALLBACK_ICON = "maestri-folder-symbolic"


_hex_classes: set = set()


def _icon_color_class(color: str) -> str:
    """`icon-color-<name>` for the named palette; Maestri-imported workspaces carry a hex (#AF52DE): a class per hex."""
    if not (color.startswith("#") and len(color) == 7):
        return f"icon-color-{color}"
    cls = f"icon-hex-{color[1:].upper()}"
    if cls not in _hex_classes:
        display = Gdk.Display.get_default()
        if display is not None:
            provider = Gtk.CssProvider()
            provider.load_from_string(f".{cls} {{ color: {color}; }}")
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 30)
        _hex_classes.add(cls)
    return cls


def workspace_icon_name(icon: str) -> str:
    """Symbolic icon for a workspace's SF Symbol name; the folder when unknown or not installed in the icon theme."""
    name = SYMBOLIC_ICON_MAP.get(icon or "", FALLBACK_ICON)
    display = Gdk.Display.get_default()
    if display is not None and name != FALLBACK_ICON and not Gtk.IconTheme.get_for_display(display).has_icon(name):
        return FALLBACK_ICON
    return name


def _copy_layout(layout: SidebarLayout) -> SidebarLayout:
    """Immutable snapshot for a worker thread (the live layout keeps being edited on the main loop)."""
    return SidebarLayout.from_dict(layout.to_dict())


class SidebarView(Gtk.Box):
    def __init__(self, client: MaestriClient, menu_model: Optional[Gio.MenuModel] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.client = client
        self.store = WorkspaceStore(client)
        self.summary_provider = None     # callable(ws_id) -> (working, unread) terminals
        self.agents_provider = None      # callable(ws_id) -> [{id, name, state, unread}] (set by the window)
        self.on_focus_agent = None       # callable(node_id)
        self._agent_widgets: dict[str, tuple] = {}
        # Include the 12px outer margins in the 260px pane width so text and search are never clipped.
        self.set_size_request(236, -1)
        self.add_css_class("sidebar-view")
        self.add_css_class("maestri-sidebar")

        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            icons_dir = os.path.join(os.path.dirname(__file__), "icons")
            if os.path.isdir(icons_dir):
                theme.add_search_path(icons_dir)

        # Top padding & container
        self.set_margin_top(8)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # 0. Menubar (File, Edit, View, Window, Help) positioned above workspace search
        if menu_model is not None:
            self.menubar = Gtk.PopoverMenuBar.new_from_model(menu_model)
            self.menubar.add_css_class("maestri-sidebar-menubar")
            self.append(self.menubar)
        else:
            self.menubar = None

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

        btn_add_ws = Gtk.Button()
        add_icon = Gtk.Image.new_from_icon_name("maestri-plus-symbolic")
        add_icon.set_pixel_size(12)
        btn_add_ws.set_child(add_icon)
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
        self._active_payload = None                       # the open workspace's live payload (from bus 'workspace-loaded')
        self._counts: Dict[str, int] = {}                 # ws id (upper) -> terminal count
        self._badges: Dict[str, list] = {}                # ws id (upper) -> [(badge box, count label)] of the rendered cards
        self._refresh_gen = 0                             # only the newest refresh is applied
        self._count_poll_inflight = False
        self._pending_delete: Optional[WorkspaceEntry] = None   # active workspace being deleted once we switched away
        self._active_popover: Optional[Gtk.Popover] = None
        self._disposed = False

        self._bus_handlers = [
            bus.connect("workspace-loaded", self._on_workspace_loaded),
            bus.connect("node-added", self._on_nodes_changed),
            bus.connect("node-removed", self._on_nodes_changed),
            bus.connect("terminal-count-changed", self._on_core_count),
        ]
        self._count_timer = GLib.timeout_add_seconds(COUNT_POLL_S, self._poll_counts)
        self.connect("destroy", lambda *_: self.dispose())

        self.refresh()

    # ---- lifecycle -----------------------------------------------------------------------------------------------------------
    def dispose(self) -> None:
        """Stop the count poll and leave the bus (window _cleanup and widget destroy both call it; idempotent)."""
        if self._disposed:
            return
        self._disposed = True
        if self._count_timer:
            GLib.source_remove(self._count_timer)
            self._count_timer = 0
        for hid in self._bus_handlers:
            if bus.handler_is_connected(hid):
                bus.disconnect(hid)
        self._bus_handlers = []
        self.close_active_popovers()

    # ---- agents dropdown / activity indicators ---------------------------------------------------------------------------
    def refresh_agent_indicators(self) -> None:
        """Every workspace's terminal pill: blue dot + tint when a terminal has an unread answer, pulsing green dot while one is working."""
        if self._disposed or self.summary_provider is None:
            return
        for key, pairs in self._agent_widgets.items():
            busy, unread = self.summary_provider(key)
            for badge, dot in pairs:
                for c in ("busy", "unread"):
                    badge.remove_css_class(c)
                    dot.remove_css_class(c)
                dot.set_visible(bool(busy or unread))
                state = "unread" if unread else ("busy" if busy else "")
                if state:
                    badge.add_css_class(state)
                    dot.add_css_class(state)
                tip = [t for t in (f"{unread} unread" if unread else "", f"{busy} working" if busy else "") if t]
                badge.set_tooltip_text(", ".join(tip) if tip else "Terminals")

    def _agents_popover(self, ws_id: str, anchor: Gtk.Widget) -> Gtk.Popover:
        pop = Gtk.Popover(has_arrow=False, position=Gtk.PositionType.RIGHT, css_classes=["maestri-context-popover"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6,
                      margin_end=6, width_request=220)
        pop.set_child(box)
        agents = self.agents_provider(ws_id) if self.agents_provider is not None else []
        if not agents:
            msg = "No terminals"
            box.append(Gtk.Label(label=msg, xalign=0, margin_start=6, margin_end=6, css_classes=["dim-label"]))
        for a in agents:
            row = Gtk.Button(css_classes=["flat", "maestri-menu-item-btn", "maestri-agent-row"])
            line = Gtk.Box(spacing=8)
            state = "unread" if a["unread"] else a["state"]
            label = {"busy": "Working", "idle": "Idle", "exited": "Exited", "unread": "Unread"}.get(state, "Idle")
            dot = Gtk.Box(width_request=9, height_request=9, valign=Gtk.Align.CENTER)
            dot.add_css_class("maestri-agent-dot")
            dot.add_css_class(state)
            line.append(dot)
            line.append(Gtk.Label(label=a["name"], xalign=0, hexpand=True, ellipsize=3))
            line.append(Gtk.Label(label=label, css_classes=["dim-label", "maestri-agent-state"]))
            row.set_child(line)
            row.connect("clicked", lambda _b, nid=a["id"], p=pop: (p.popdown(), self.on_focus_agent and self.on_focus_agent(nid)))
            box.append(row)
        return pop

    def set_active_workspace_id(self, ws_id: str) -> None:
        self._active_workspace_id = ws_id

    # ---- loading -------------------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Fetch workspaces, sidebar layout and terminal counts on a worker thread (online first, local files when offline)."""
        if self._disposed:
            return
        self._refresh_gen += 1
        gen, store = self._refresh_gen, self.store

        def worker():
            workspaces = store.list_workspaces()
            layout = store.load_layout()
            counts = store.counts_for(workspaces)
            GLib.idle_add(self._deliver_refresh, gen, workspaces, layout, counts)

        threading.Thread(target=worker, name="maestri-sidebar-refresh", daemon=True).start()

    def _deliver_refresh(self, gen: int, workspaces, layout, counts) -> bool:
        if not self._disposed and gen == self._refresh_gen:
            self._apply_refresh(workspaces, layout, counts)
        return GLib.SOURCE_REMOVE

    def _apply_refresh(self, workspaces: list[WorkspaceEntry], layout: SidebarLayout, counts: Optional[Dict[str, int]] = None) -> bool:
        self._raw_workspaces = list(workspaces)
        self._sidebar_layout = layout if isinstance(layout, SidebarLayout) else SidebarLayout()
        if counts:
            self._counts.update(counts)
        self._rerender()
        self._refresh_header_pill()
        return GLib.SOURCE_REMOVE

    def _refresh_header_pill(self) -> None:
        window = self.get_root()
        if window is not None and hasattr(window, "update_workspace_pill"):
            window.update_workspace_pill()

    def _rerender(self) -> None:
        text = self.search_entry.get_text().strip().lower()
        if text:
            self._render_all([ws for ws in self._raw_workspaces
                              if text in ws.name.lower() or text in (ws.working_directory or "").lower()], is_search=True)
        else:
            self._render_all(self._raw_workspaces)

    # ---- terminal counts (C1) ------------------------------------------------------------------------------------------------
    def _count_for(self, ws_id: str) -> int:
        key = (ws_id or "").upper()
        if self._active_payload is not None and key == (self._active_workspace_id or "").upper():
            return count_terminals(self._active_payload.nodes)
        return self._counts.get(key, 0)

    def _set_badge(self, ws_id: str, count: int) -> None:
        key = ws_id.upper()
        self._counts[key] = count
        for badge, label in self._badges.get(key, []):
            label.set_label(str(count))
            badge.set_visible(count > 0)

    def _on_workspace_loaded(self, _bus, payload) -> None:
        self._active_payload = payload
        self._active_workspace_id = str(getattr(payload, "id", "") or "").upper() or None
        if self._active_workspace_id:
            self._set_badge(self._active_workspace_id, count_terminals(payload.nodes))
            self._select_active_row()
            GLib.idle_add(lambda: (self.refresh_agent_indicators(), False)[1])
        pending = self._pending_delete
        if pending is not None and pending.id.upper() != (self._active_workspace_id or ""):
            self._pending_delete = None   # we left the workspace being deleted: now it is safe to remove it
            self._delete_now(pending)

    def _on_core_count(self, _bus, ws_id: str, count: int) -> None:
        """Core event (nodeAdded / nodeRemoved / workspaceSaved with terminalCount): update that badge without a refetch.
        The open workspace keeps its live count (it may hold edits the core has not saved yet)."""
        if self._active_payload is not None and ws_id.upper() == (self._active_workspace_id or "").upper():
            self._recount_active()
        else:
            self._set_badge(ws_id, count)

    def _on_nodes_changed(self, *_args) -> None:
        # run after the window finished mutating its payload (some emitters fire before the list changes)
        GLib.idle_add(self._recount_active)

    def _recount_active(self) -> bool:
        if not self._disposed and self._active_payload is not None and self._active_workspace_id:
            self._set_badge(self._active_workspace_id, count_terminals(self._active_payload.nodes))
        return GLib.SOURCE_REMOVE

    def _poll_counts(self) -> bool:
        if self._disposed:
            self._count_timer = 0
            return GLib.SOURCE_REMOVE
        if self._count_poll_inflight or not self._raw_workspaces:
            return GLib.SOURCE_CONTINUE
        self._count_poll_inflight = True
        ids = [w.id for w in self._raw_workspaces]
        store = self.store

        def worker():
            counts = store.terminal_counts(ids)
            GLib.idle_add(self._apply_counts, counts)

        threading.Thread(target=worker, name="maestri-sidebar-counts", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _apply_counts(self, counts: Dict[str, int]) -> bool:
        self._count_poll_inflight = False
        if self._disposed:
            return GLib.SOURCE_REMOVE
        active = (self._active_workspace_id or "").upper()
        for key, n in counts.items():
            if key != active or self._active_payload is None:   # the open workspace's live payload wins over the file
                if self._counts.get(key) != n or key not in self._counts:
                    self._set_badge(key, n)
        self._recount_active()
        return GLib.SOURCE_REMOVE

    # ---- rendering -----------------------------------------------------------------------------------------------------------
    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._rerender()

    def _render_all(self, workspaces: list[WorkspaceEntry], is_search: bool = False) -> None:
        # Clear groups container and list box
        while self.groups_container.get_first_child():
            self.groups_container.remove(self.groups_container.get_first_child())
        while self.ws_list_box.get_first_child():
            self.ws_list_box.remove(self.ws_list_box.get_first_child())
        self._badges = {}
        self._agent_widgets = {}

        if not workspaces:
            if is_search:
                return
            default_entry = WorkspaceEntry(
                id=self._active_workspace_id or "default",
                name="Default Workspace",
                working_directory=os.path.expanduser("~"),
                icon="folder",
                color="blue",
            )
            self.ws_list_box.append(self._create_workspace_card(default_entry, count=self._count_for(default_entry.id)))
            return

        all_by_id = {ws.id.upper(): ws for ws in workspaces}
        grouped_ids = {it.upper() for g in self._sidebar_layout.groups for it in g.items}

        # Render Groups (if not searching)
        if not is_search and self._sidebar_layout.groups:
            for group in self._sidebar_layout.groups:
                self.groups_container.append(self._create_group_box(group, all_by_id))

        # Render Top-Level Workspaces: pinned first, then by name
        top_level = [ws for ws in workspaces if is_search or ws.id.upper() not in grouped_ids]
        top_level.sort(key=lambda w: (not w.is_pinned, w.name.lower()))
        for ws in top_level:
            self.ws_list_box.append(self._create_workspace_card(ws, count=self._count_for(ws.id)))
        self._select_active_row()
        GLib.idle_add(lambda: (self.refresh_agent_indicators(), False)[1])

    def _select_active_row(self) -> None:
        active = (self._active_workspace_id or "").upper()
        lists = [self.ws_list_box] + [c for g in self.groups_container for c in g if isinstance(c, Gtk.ListBox)]
        for lb in lists:
            row = lb.get_first_child()
            target = None
            while row is not None:
                child = row.get_child() if isinstance(row, Gtk.ListBoxRow) else None
                if child is not None and str(getattr(child, "ws_id", "")).upper() == active:
                    target = row
                row = row.get_next_sibling()
            if target is not None:
                lb.select_row(target)
            else:
                lb.unselect_all()

    def _create_group_box(self, group: SidebarGroup, all_by_id: dict[str, WorkspaceEntry]) -> Gtk.Box:
        group_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group_vbox.add_css_class("maestri-group-container")

        # Header row
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_row.add_css_class("maestri-group-header")

        chevron = Gtk.Image.new_from_icon_name(
            "maestri-chevron-right-symbolic" if group.is_collapsed else "maestri-chevron-down-symbolic")
        chevron.set_pixel_size(10)
        chevron.add_css_class("maestri-group-chevron")
        header_row.append(chevron)

        group_icon = Gtk.Image.new_from_icon_name("maestri-folder-symbolic")
        group_icon.set_pixel_size(14)
        group_icon.add_css_class("maestri-group-folder-icon")
        header_row.append(group_icon)

        name_lbl = Gtk.Label(label=f"<b>{GLib.markup_escape_text(group.name)}</b>", use_markup=True, xalign=0.0, ellipsize=3)
        name_lbl.add_css_class("maestri-group-title")
        name_lbl.set_hexpand(True)
        name_lbl.set_tooltip_text(group.name)
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
                items_list.append(self._create_workspace_card(ws, group_id=group.id, count=self._count_for(ws.id)))

        items_list.set_visible(not group.is_collapsed)
        group_vbox.append(items_list)
        return group_vbox

    def _create_workspace_card(
        self,
        entry: WorkspaceEntry,
        group_id: Optional[str] = None,
        count: int = 0,
    ) -> Gtk.Widget:
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row_box.add_css_class("maestri-workspace-card")

        # Icon with outline/symbolic SVG and color indicator
        icon = Gtk.Image.new_from_icon_name(workspace_icon_name(entry.icon))
        icon.set_pixel_size(18)
        icon.add_css_class("maestri-card-icon")
        color = getattr(entry, "color", "blue") or "blue"
        icon.add_css_class(_icon_color_class(color))
        row_box.append(icon)

        # Name & Path (ellipsized, full text in the tooltip: never clipped)
        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_vbox.set_hexpand(True)
        info_vbox.set_valign(Gtk.Align.CENTER)

        name_lbl = Gtk.Label(label=f"<b>{GLib.markup_escape_text(entry.name)}</b>", use_markup=True, xalign=0.0, ellipsize=3)
        name_lbl.add_css_class("maestri-card-title")
        remote = card_location(entry)   # SSH workspace: user@host:path instead of a local path
        path_str = remote or (entry.working_directory or "").replace(os.path.expanduser("~"), "~", 1)
        path_lbl = Gtk.Label(label=path_str, xalign=0.0, ellipsize=3)
        path_lbl.add_css_class("maestri-card-path")
        row_box.set_tooltip_text(f"{entry.name}\n{path_str}" if path_str else entry.name)

        info_vbox.append(name_lbl)
        info_vbox.append(path_lbl)
        row_box.append(info_vbox)

        # Right Badges (Pin + Terminal count capsule)
        badges_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if remote is not None:   # SSH badge
            ssh_badge = Gtk.Image.new_from_icon_name("maestri-network-symbolic")
            ssh_badge.set_tooltip_text("Remote SSH")
            ssh_badge.add_css_class("maestri-card-ssh")
            badges_box.append(ssh_badge)
            row_box.ssh_badge = ssh_badge
        badges_box.set_valign(Gtk.Align.CENTER)

        if getattr(entry, "is_pinned", False):
            pin_icon = Gtk.Image.new_from_icon_name("maestri-pin-symbolic")
            pin_icon.set_pixel_size(12)
            pin_icon.add_css_class("maestri-card-pin")
            pin_icon.set_tooltip_text("Pinned")
            badges_box.append(pin_icon)

        # Terminal count pill with maestri-terminal-symbolic icon (hidden when there are no terminals, like macOS)
        badge = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badge.add_css_class("maestri-card-badge")
        term_icon = Gtk.Image.new_from_icon_name("maestri-terminal-symbolic")
        term_icon.set_pixel_size(10)
        badge.append(term_icon)
        term_lbl = Gtk.Label(label=str(count))
        badge.append(term_lbl)
        badge.set_visible(count > 0)
        badge.set_tooltip_text("Terminals")
        # Compatibility helper for tests querying get_label()
        badge.get_label = lambda lbl=term_lbl: f">_ {lbl.get_label()}"
        self._badges.setdefault(entry.id.upper(), []).append((badge, term_lbl))

        # the terminal-count pill doubles as the activity indicator: a dot (blue = unread answer, green = working)
        dot = Gtk.Box(width_request=8, height_request=8, valign=Gtk.Align.CENTER, visible=False, css_classes=["maestri-act-dot"])
        badge.prepend(dot)
        self._agent_widgets.setdefault(entry.id.upper(), []).append((badge, dot))
        # Clicking the terminal pill opens the list of agents in this workspace (the pill is the MenuButton's content)
        menu = Gtk.MenuButton(child=badge, has_frame=False, valign=Gtk.Align.CENTER, always_show_arrow=False,
                              tooltip_text="Agents in this workspace", css_classes=["flat", "maestri-card-agents-btn"])
        badge.set_tooltip_text(None)
        badge.bind_property("visible", menu, "visible", GObject.BindingFlags.SYNC_CREATE)
        menu.set_create_popup_func(lambda mb, ws=entry.id.upper(): mb.set_popover(self._agents_popover(ws, mb)))
        badges_box.append(menu)

        row_box.append(badges_box)
        row_box.ws_id = entry.id
        row_box.entry = entry
        row_box.count_label = term_lbl

        # Right-Click Context Menu on Workspace Card
        rc_gesture = Gtk.GestureClick()
        rc_gesture.set_button(3)
        rc_gesture.connect(
            "pressed",
            lambda _g, _n, x, y, ent=entry, gid=group_id, w=row_box: self._show_workspace_context_menu(w, x, y, ent, gid)
        )
        row_box.add_controller(rc_gesture)

        return row_box

    def close_active_popovers(self) -> None:
        if self._active_popover is not None:
            try:
                self._active_popover.popdown()
                self._active_popover.unparent()
            except Exception:
                pass
            self._active_popover = None

    # ---- Context Menus (macOS Parity) ----------------------------------------------------------------------------------------
    def _new_popover(self, anchor: Gtk.Widget, x: Optional[float] = None, y: Optional[float] = None) -> tuple[Gtk.Popover, Gtk.Box]:
        self.close_active_popovers()
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        popover.set_has_arrow(False)
        popover.add_css_class("maestri-context-popover")
        if x is not None and y is not None:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
        popover.connect("closed", self._on_popover_closed)
        self._active_popover = popover

        menu_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for side in ("top", "bottom", "start", "end"):
            getattr(menu_vbox, f"set_margin_{side}")(6)
        popover.set_child(menu_vbox)
        return popover, menu_vbox

    def _on_popover_closed(self, popover: Gtk.Popover) -> None:
        def unparent():
            if popover.get_parent() is not None:
                popover.unparent()
            if self._active_popover is popover:
                self._active_popover = None
            return GLib.SOURCE_REMOVE
        GLib.idle_add(unparent)   # the clicked item's handler may still be running inside the popover

    def _show_workspace_context_menu(
        self,
        anchor: Gtk.Widget,
        x: float,
        y: float,
        entry: WorkspaceEntry,
        group_id: Optional[str] = None,
    ) -> None:
        popover, menu = self._new_popover(anchor, x, y)
        sep = lambda: menu.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))  # noqa: E731

        menu.append(self._make_menu_item("maestri-pencil-symbolic", "Edit Workspace…", lambda: self._on_edit_workspace(entry, popover)))
        menu.append(self._make_menu_item("maestri-duplicate-symbolic", "Duplicate Workspace", lambda: self._on_duplicate_workspace(entry, popover)))
        sep()
        menu.append(self._make_menu_item("maestri-folder-open-symbolic" if Gtk.IconTheme.get_for_display(self.get_display()).has_icon("maestri-folder-open-symbolic")
                                         else "maestri-folder-symbolic",
                                         "Open in File Manager", lambda: self._on_open_in_file_manager(entry, popover)))
        sep()
        menu.append(self._make_menu_item("maestri-pin-symbolic", "Unpin Workspace" if entry.is_pinned else "Pin Workspace",
                                         lambda: self._on_toggle_pin(entry, popover)))
        sep()
        # Groups: Remove from Group (when grouped) / Move to <other group> / New Group with Workspace…
        current = (group_id or "").upper()
        if group_id:
            menu.append(self._make_menu_item("maestri-minus-symbolic", "Remove from Group",
                                             lambda: self._on_remove_from_group(entry, group_id, popover)))
        for g in self._sidebar_layout.groups:
            if g.id.upper() != current:
                menu.append(self._make_menu_item("maestri-folder-symbolic", f"Move to: {g.name}",
                                                 lambda grp_id=g.id: self._on_move_to_group(entry, grp_id, popover)))
        menu.append(self._make_menu_item("maestri-plus-symbolic", "New Group with Workspace…",
                                         lambda: self._on_new_group_with_workspace(entry, popover)))
        sep()
        menu.append(self._make_menu_item("maestri-trash-symbolic", "Delete Workspace…",
                                         lambda: self._on_delete_workspace(entry, popover), is_danger=True))
        popover.popup()

    def _show_group_context_menu(
        self,
        anchor: Gtk.Widget,
        x: float,
        y: float,
        group: SidebarGroup,
    ) -> None:
        popover, menu = self._new_popover(anchor, x, y)
        menu.append(self._make_menu_item("maestri-pencil-symbolic", "Rename Group…", lambda: self._on_rename_group(group, popover)))
        menu.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        menu.append(self._make_menu_item("maestri-trash-symbolic", "Delete Group", lambda: self._on_delete_group(group, popover), is_danger=True))
        popover.popup()

    def _make_menu_item(self, icon_name: str, label_text: str, callback: Callable[[], None], is_danger: bool = False) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("maestri-menu-item-btn")
        if is_danger:
            btn.add_css_class("danger")
        btn.set_has_frame(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(14)
        box.append(img)

        lbl = Gtk.Label(label=label_text, xalign=0.0)
        lbl.set_hexpand(True)
        box.append(lbl)

        btn.set_child(box)
        btn.get_label = lambda: label_text   # a custom child makes Gtk.Button.get_label() return None
        btn.connect("clicked", lambda _b: callback())
        return btn

    # ---- persistence helpers -------------------------------------------------------------------------------------------------
    def _persist(self, job: Callable[[], object], then_refresh: bool = True, on_done: Optional[Callable[[object], None]] = None) -> None:
        """Run `job` (store I/O) off the main loop, then refresh from the source of truth (core or disk)."""
        def worker():
            try:
                result = job()
            except Exception as exc:   # noqa: BLE001 - surface it in the log, the refresh restores the real state
                print(f"[sidebar] action failed: {exc!r}")
                result = None

            def done():
                if on_done is not None and not self._disposed:
                    on_done(result)
                if then_refresh:
                    self.refresh()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(done)

        threading.Thread(target=worker, name="maestri-sidebar-action", daemon=True).start()

    def _save_sidebar_layout(self) -> None:
        """Re-render now, persist a snapshot of the layout in the background."""
        self._rerender()
        snapshot, store = _copy_layout(self._sidebar_layout), self.store
        self._persist(lambda: store.save_layout(snapshot))

    def _entry(self, ws_id: str) -> Optional[WorkspaceEntry]:
        return next((w for w in self._raw_workspaces if w.id.upper() == ws_id.upper()), None)

    def _sync_active_payload(self, entry: WorkspaceEntry) -> None:
        """The open workspace's document is saved from memory: keep its metadata in step with the manifest edit."""
        p = self._active_payload
        if p is None or str(getattr(p, "id", "")).upper() != entry.id.upper():
            return
        for attr, value in (("name", entry.name), ("working_directory", entry.working_directory),
                            ("icon", entry.icon), ("is_pinned", entry.is_pinned)):
            if hasattr(p, attr):
                setattr(p, attr, value)
        window = self.get_root()
        if window is not None and hasattr(window, "set_title"):
            try:
                window.set_title(entry.name)
            except Exception:
                pass
        self._refresh_header_pill()   # collapsed-sidebar capsule shows the new name / icon / colour

    # ---- Actions -------------------------------------------------------------------------------------------------------------
    def _on_edit_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()

        def handle_save(updated: WorkspaceEntry):
            live = self._entry(updated.id) or updated
            for attr in ("name", "working_directory", "icon", "color", "is_pinned"):
                setattr(live, attr, getattr(updated, attr))
            self._sync_active_payload(live)
            self._rerender()
            store, ws_id = self.store, updated.id
            fields = dict(name=updated.name, working_directory=updated.working_directory, icon=updated.icon,
                          color=updated.color, is_pinned=updated.is_pinned)
            sync_on = getattr(updated, "sync_config_files", None)
            window = self.get_root()
            is_active = self._active_payload is not None and ws_id.upper() == (self._active_workspace_id or "").upper()
            if sync_on is not None and is_active:   # the open document is saved from memory: set it there and (re)attach
                self._active_payload._extra["syncConfigFiles"] = bool(sync_on)
                if getattr(window, "sync", None) is not None:
                    window.sync._on_dirty()   # a payload option changed: mark the document dirty so the flush saves it
                    window.sync.flush()
                if hasattr(window, "config_sync"):
                    window.config_sync.attach(self._active_payload)
            elif sync_on is not None:
                fields["sync_config_files"] = bool(sync_on)
            self._persist(lambda: store.update_workspace(ws_id, **fields))

        active = self._active_payload is not None and entry.id.upper() == (self._active_workspace_id or "").upper()
        sync_now = (bool((getattr(self._active_payload, "_extra", {}) or {}).get("syncConfigFiles", False)) if active
                    else self.store.read_sync_config(entry.id))
        EditWorkspaceDialog(self.get_root(), entry, handle_save, sync_config_files=sync_now).present()

    def _on_duplicate_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        store, ws_id = self.store, entry.id
        if self._active_payload is not None and ws_id.upper() == (self._active_workspace_id or "").upper():
            window = self.get_root()
            sync = getattr(window, "sync", None)
            if sync is not None:   # duplicate what is on screen, not the last autosave
                try:
                    sync.flush()
                except Exception:
                    pass
        self._persist(lambda: store.duplicate_workspace(ws_id))

    def _on_open_in_file_manager(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        path = os.path.expanduser(entry.working_directory or "~")
        try:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass

    def _on_toggle_pin(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        live = self._entry(entry.id) or entry
        live.is_pinned = not live.is_pinned
        if live is not entry:
            entry.is_pinned = live.is_pinned
        self._sync_active_payload(live)
        self._rerender()
        store, ws_id, pinned = self.store, live.id, live.is_pinned
        self._persist(lambda: store.update_workspace(ws_id, is_pinned=pinned))

    def _on_delete_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()

        def handle_delete():
            is_active = entry.id.upper() == (self._active_workspace_id or "").upper() and self._active_payload is not None
            others = [w for w in self._raw_workspaces if w.id.upper() != entry.id.upper()]
            # hide it at once
            self._raw_workspaces = others
            from .core_link import remove_from_layout
            remove_from_layout(self._sidebar_layout, entry.id)
            self._rerender()
            if is_active and others:
                # leave it first: the window flushes the open document on switch, which would otherwise re-create it
                self._pending_delete = entry
                target = sorted(others, key=lambda w: (not w.is_pinned, w.name.lower()))[0]
                bus.emit("workspace-switched", target.id)
            else:
                if is_active:   # the last workspace: the window drops the document (no autosave) and shows the empty state
                    window = self.get_root()
                    if window is not None and hasattr(window, "close_workspace"):
                        window.close_workspace()
                    self._active_payload, self._active_workspace_id = None, None
                self._delete_now(entry)

        DeleteWorkspaceDialog(self.get_root(), entry.name, handle_delete).present()

    def _delete_now(self, entry: WorkspaceEntry) -> None:
        store, ws_id = self.store, entry.id
        self._counts.pop(ws_id.upper(), None)
        self._persist(lambda: store.delete_workspace(ws_id))

    def _on_move_to_group(self, entry: WorkspaceEntry, group_id: str, popover: Gtk.Popover) -> None:
        popover.popdown()
        core_link.move_to_group(self._sidebar_layout, entry.id, group_id)
        self._save_sidebar_layout()

    def _on_remove_from_group(self, entry: WorkspaceEntry, group_id: str, popover: Gtk.Popover) -> None:
        popover.popdown()
        core_link.remove_from_group(self._sidebar_layout, entry.id)
        self._save_sidebar_layout()

    def _on_new_group_with_workspace(self, entry: WorkspaceEntry, popover: Gtk.Popover) -> None:
        popover.popdown()
        GroupDialog(self.get_root(), "New Group", on_ok=lambda name: self.add_group(name, entry.id)).present()

    def add_group(self, name: str, ws_id: Optional[str] = None) -> SidebarGroup:
        """New group (optionally moving a workspace into it); used by the header menu and the window's File > New Group."""
        group = core_link.new_group(self._sidebar_layout, name, ws_id)
        self._save_sidebar_layout()
        return group

    def _on_add_header_clicked(self, btn: Gtk.Button) -> None:
        popover, menu = self._new_popover(btn)
        menu.append(self._make_menu_item("maestri-plus-symbolic", "New Workspace…", lambda: self._on_header_new_workspace(popover)))
        from .ssh_dialog import open_connect_to_host, ssh_unavailable_reason
        window = self.get_root()
        ssh_item = self._make_menu_item("maestri-network-symbolic", "Connect to Host…",
                                        lambda: (popover.popdown(), open_connect_to_host(window)))
        reason = ssh_unavailable_reason(window)
        ssh_item.set_sensitive(reason is None)
        ssh_item.set_tooltip_text(reason or "Open a workspace on a remote machine over SSH")
        self.ssh_menu_item = ssh_item
        menu.append(ssh_item)
        menu.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        menu.append(self._make_menu_item("maestri-folder-symbolic", "New Group…", lambda: self._on_header_new_group(popover)))
        popover.popup()

    def _on_header_new_workspace(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        window = self.get_root()

        def handle_created(name: str, wd: str, icon: str, color: str = "blue"):
            def worker():
                new_id = create_workspace_record(self.client, name, wd, icon, color)

                def on_done():
                    self.refresh()
                    if window and hasattr(window, "_on_workspace_switched"):
                        window._on_workspace_switched(None, new_id)
                    return GLib.SOURCE_REMOVE
                GLib.idle_add(on_done)
            threading.Thread(target=worker, daemon=True).start()

        CreateWorkspaceDialog(window, handle_created).present()

    def _on_header_new_group(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        GroupDialog(self.get_root(), "New Group", on_ok=lambda name: self.add_group(name)).present()

    def _toggle_group_collapse(self, group: SidebarGroup) -> None:
        group.is_collapsed = not group.is_collapsed
        self._save_sidebar_layout()

    def _on_rename_group(self, group: SidebarGroup, popover: Gtk.Popover) -> None:
        popover.popdown()

        def handle_rename(name: str):
            group.name = name
            self._save_sidebar_layout()

        GroupDialog(self.get_root(), "Rename Group", initial_name=group.name, on_ok=handle_rename).present()

    def _on_delete_group(self, group: SidebarGroup, popover: Gtk.Popover) -> None:
        popover.popdown()
        core_link.delete_group(self._sidebar_layout, group.id)
        self._save_sidebar_layout()

    def _on_ws_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        child = row.get_child()
        if hasattr(child, "ws_id") and child.ws_id != "default":
            bus.emit("workspace-switched", child.ws_id)
