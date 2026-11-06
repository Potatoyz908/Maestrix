"""
Sidebar View for Open Maestri GTK 4 Native.
Matches the official macOS Maestri sidebar design (docs/images/home.png):
- Rounded search entry ('Search Workspaces')
- Card-based workspace items with folder icon, title, path, and node count badge
- Floors (Worktrees) section
- Canvas Agents section
"""
from __future__ import annotations

import os
import threading
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from maestri_core.client.api import MaestriClient
from maestri_core.models.workspace import SidebarLayout, WorkspaceEntry
from maestri_core.signals.bus import bus


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

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content_box.set_margin_top(6)

        # 2.1 Workspaces Section
        self.ws_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ws_header = Gtk.Label(label="<b>WORKSPACES</b>", use_markup=True, xalign=0.0)
        ws_header.add_css_class("maestri-sidebar-header")
        self.ws_box.append(ws_header)

        self.ws_list_box = Gtk.ListBox()
        self.ws_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.ws_list_box.add_css_class("maestri-workspace-list")
        self.ws_list_box.connect("row-activated", self._on_ws_selected)
        self.ws_box.append(self.ws_list_box)
        content_box.append(self.ws_box)

        # 2.2 Floors Section
        self.floors_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        floors_header = Gtk.Label(label="<b>FLOORS (WORKTREES)</b>", use_markup=True, xalign=0.0)
        floors_header.add_css_class("maestri-sidebar-header")
        self.floors_box.append(floors_header)

        self.floors_list_box = Gtk.ListBox()
        self.floors_list_box.add_css_class("maestri-floors-list")
        self.floors_box.append(self.floors_list_box)
        content_box.append(self.floors_box)

        # 2.3 Canvas Agents Section
        self.agents_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        agents_header = Gtk.Label(label="<b>CANVAS AGENTS</b>", use_markup=True, xalign=0.0)
        agents_header.add_css_class("maestri-sidebar-header")
        self.agents_box.append(agents_header)

        self.agents_list_box = Gtk.ListBox()
        self.agents_list_box.add_css_class("maestri-agents-list")
        self.agents_box.append(self.agents_list_box)
        content_box.append(self.agents_box)

        scrolled.set_child(content_box)
        self.append(scrolled)

        self._raw_workspaces: list[WorkspaceEntry] = []
        self._raw_agents: list[dict] = []
        self.refresh()

    def refresh(self) -> None:
        """Fetch workspaces, floors, and agents asynchronously in background thread."""
        def worker():
            try:
                workspaces = self.client.get_workspaces()
            except Exception:
                workspaces = []
            try:
                agents = self.client.get_agents()
            except Exception:
                agents = []
            GLib.idle_add(self._apply_refresh, workspaces, agents)

        threading.Thread(target=worker, name="maestri-sidebar-refresh", daemon=True).start()

    def _apply_refresh(self, workspaces: list[WorkspaceEntry], agents: list[dict]) -> bool:
        self._raw_workspaces = workspaces
        self._raw_agents = agents
        self._render_workspaces(workspaces)
        self._render_floors()
        self._render_agents(agents)
        return GLib.SOURCE_REMOVE

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip().lower()
        if not text:
            self._render_workspaces(self._raw_workspaces)
        else:
            filtered = [ws for ws in self._raw_workspaces if text in ws.name.lower() or text in ws.path.lower()]
            self._render_workspaces(filtered)

    def _render_workspaces(self, workspaces: list[WorkspaceEntry]) -> None:
        while self.ws_list_box.get_first_child():
            self.ws_list_box.remove(self.ws_list_box.get_first_child())

        if not workspaces:
            card = self._create_workspace_card("Default Workspace", os.path.expanduser("~"), count=2)
            self.ws_list_box.append(card)
        else:
            for ws in workspaces:
                card = self._create_workspace_card(ws.name, ws.path, ws_id=ws.id, count=getattr(ws, "node_count", 2))
                self.ws_list_box.append(card)

    def _create_workspace_card(self, name: str, path: str, ws_id: Optional[str] = None, count: int = 1) -> Gtk.Widget:
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row_box.add_css_class("maestri-workspace-card")

        # Folder Icon
        icon = Gtk.Label(label="📁")
        icon.add_css_class("maestri-card-icon")
        row_box.append(icon)

        # Name & Path
        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_vbox.set_hexpand(True)

        name_lbl = Gtk.Label(label=f"<b>{name}</b>", use_markup=True, xalign=0.0, ellipsize=3)
        name_lbl.add_css_class("maestri-card-title")
        path_lbl = Gtk.Label(label=path, xalign=0.0, ellipsize=3)
        path_lbl.add_css_class("maestri-card-path")

        info_vbox.append(name_lbl)
        info_vbox.append(path_lbl)
        row_box.append(info_vbox)

        # Count Badge
        badge = Gtk.Label(label=f"🗂️ {count}")
        badge.add_css_class("maestri-card-badge")
        row_box.append(badge)

        if ws_id:
            row_box.ws_id = ws_id
        return row_box

    def _render_floors(self) -> None:
        while self.floors_list_box.get_first_child():
            self.floors_list_box.remove(self.floors_list_box.get_first_child())

        floor_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        floor_card.add_css_class("maestri-floor-card")
        icon = Gtk.Label(label="🌿")
        lbl = Gtk.Label(label="<b>main</b> (Ground active)", use_markup=True, xalign=0.0)
        lbl.add_css_class("maestri-floor-name")
        floor_card.append(icon)
        floor_card.append(lbl)
        self.floors_list_box.append(floor_card)

    def _render_agents(self, agents: list[dict]) -> None:
        while self.agents_list_box.get_first_child():
            self.agents_list_box.remove(self.agents_list_box.get_first_child())

        default_agents = [
            ("🏛️ Arquiteto", "Tech Lead & Specs"),
            ("🎨 Pixel", "UI & Canvas"),
            ("🛡️ Sentinela", "Core Server & IPC"),
            ("⚙️ Forja", "QA & E2E Validation"),
        ]
        list_to_render = [(a.get("name", "Agent"), a.get("role", "Active")) for a in agents] if agents else default_agents

        for a_name, a_role in list_to_render:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card.add_css_class("maestri-agent-card")
            lbl_name = Gtk.Label(label=f"<b>{a_name}</b>", use_markup=True, xalign=0.0)
            lbl_name.add_css_class("maestri-agent-name")
            lbl_role = Gtk.Label(label=a_role, xalign=0.0)
            lbl_role.add_css_class("maestri-agent-role")
            card.append(lbl_name)
            card.append(lbl_role)
            self.agents_list_box.append(card)

    def _on_ws_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        child = row.get_child()
        if hasattr(child, "ws_id"):
            bus.emit("workspace-switched", child.ws_id)
