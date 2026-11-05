"""
Sidebar View for Open Maestri GTK 4 Native.
Provides workspaces list, floors list, and active canvas agents.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from typing import List, Optional
from maestri_core.models.workspace import WorkspaceEntry, SidebarLayout
from maestri_core.client.api import MaestriClient
from maestri_core.signals.bus import bus


class SidebarView(Gtk.Box):
    def __init__(self, client: MaestriClient):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.client = client
        self.set_size_request(240, -1)
        self.add_css_class("sidebar-view")

        # Scrollable container
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(10)
        content_box.set_margin_end(10)

        # 1. Workspaces Section
        ws_header = Gtk.Label(label="<b>WORKSPACES</b>", use_markup=True, xalign=0.0)
        content_box.append(ws_header)

        self.ws_list_box = Gtk.ListBox()
        self.ws_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.ws_list_box.connect("row-activated", self._on_ws_selected)
        content_box.append(self.ws_list_box)

        # 2. Floors Section
        floors_header = Gtk.Label(label="<b>FLOORS (WORKTREES)</b>", use_markup=True, xalign=0.0)
        content_box.append(floors_header)

        self.floors_list_box = Gtk.ListBox()
        content_box.append(self.floors_list_box)

        # 3. Canvas Agents Section
        agents_header = Gtk.Label(label="<b>CANVAS AGENTS</b>", use_markup=True, xalign=0.0)
        content_box.append(agents_header)

        self.agents_list_box = Gtk.ListBox()
        content_box.append(self.agents_list_box)

        scrolled.set_child(content_box)
        self.append(scrolled)

        # Initial data load
        GLib.idle_add(self.refresh)

    def refresh(self):
        """Fetch workspaces, floors, and agents from backend."""
        # 1. Populate Workspaces
        while self.ws_list_box.get_first_child():
            self.ws_list_box.remove(self.ws_list_box.get_first_child())

        workspaces = self.client.get_workspaces()
        if not workspaces:
            lbl = Gtk.Label(label="Default Workspace", xalign=0.0)
            lbl.set_margin_start(8)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            self.ws_list_box.append(lbl)
        else:
            for ws in workspaces:
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                icon = Gtk.Label(label="📁")
                name = Gtk.Label(label=ws.name, xalign=0.0)
                box.append(icon)
                box.append(name)
                box.ws_id = ws.id
                box.set_margin_top(4)
                box.set_margin_bottom(4)
                self.ws_list_box.append(box)

        # 2. Populate Floors
        while self.floors_list_box.get_first_child():
            self.floors_list_box.remove(self.floors_list_box.get_first_child())

        floor_lbl = Gtk.Label(label="🌿 main (active)", xalign=0.0)
        floor_lbl.set_margin_start(8)
        self.floors_list_box.append(floor_lbl)

        # 3. Populate Agents
        while self.agents_list_box.get_first_child():
            self.agents_list_box.remove(self.agents_list_box.get_first_child())

        try:
            agents = self.client.get_agents()
        except Exception:
            agents = []
        if not agents:
            # Show canvas squad
            default_agents = [
                ("🏛️ Arquiteto", "Tech Lead & Core"),
                ("🎨 Pixel", "UI & Canvas"),
                ("🛡️ Sentinela", "IPC & Backend"),
                ("⚙️ Forja", "QA & Toolchain"),
            ]
            for a_name, a_role in default_agents:
                escaped_role = GLib.markup_escape_text(a_role)
                lbl = Gtk.Label(label=f"{a_name}\n<small>{escaped_role}</small>", use_markup=True, xalign=0.0)
                lbl.set_margin_start(8)
                lbl.set_margin_top(3)
                lbl.set_margin_bottom(3)
                self.agents_list_box.append(lbl)
        else:
            for a in agents:
                lbl = Gtk.Label(label=f"🤖 {a.get('name', 'Agent')}", xalign=0.0)
                lbl.set_margin_start(8)
                self.agents_list_box.append(lbl)

    def _on_ws_selected(self, listbox, row):
        child = row.get_child()
        if hasattr(child, "ws_id"):
            bus.emit("workspace-switched", child.ws_id)
