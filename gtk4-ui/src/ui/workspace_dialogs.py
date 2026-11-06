"""
Workspace Dialogs for Open Maestri GTK 4 Native.
Faithful implementation of macOS sheets:
- CreateWorkspaceDialog: matches Sources/Workspace/CreateWorkspaceSheet.swift
- EditWorkspaceDialog: matches Sources/Workspace/EditWorkspaceSheet.swift
- DeleteWorkspaceDialog: matches delete confirmation in WorkspaceSidebarView.swift
- GroupDialog: manages SidebarGroup creation and renaming
"""
from __future__ import annotations

import os
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from maestri_core.models.workspace import WorkspaceEntry

from .iconography import sf_symbol_icon


def _close_modal(win: Gtk.Window) -> None:
    """GTK4 / Python 3.14: drop modality and the transient parent BEFORE closing, else the parent can stay insensitive; then
    really close (hiding alone kept every dialog ever opened alive). Idempotent."""
    if getattr(win, "_maestri_closed", False):
        return
    win._maestri_closed = True
    try:
        win.set_visible(False)
        win.set_modal(False)
        win.set_transient_for(None)
    except Exception:
        pass
    try:
        Gtk.Window.close(win)
    except Exception:
        pass


def create_workspace_record(client, name: str, wd: str, icon: str = "folder") -> str:
    """Create a workspace via the core client if available, or locally on filesystem when offline.
    Returns the workspace id."""
    import json
    import uuid
    from datetime import datetime, timezone

    # 1. Try core client
    try:
        res = client.create_workspace(name=name, working_directory=wd, icon=icon)
        if res and getattr(res, "id", None):
            return str(res.id).upper()
    except Exception:
        pass

    # 2. Offline fallback: local filesystem (~/.open-maestri)
    new_id = str(uuid.uuid4()).upper()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_dir = os.path.expanduser("~/.open-maestri")
    manifest_path = os.path.join(manifest_dir, "manifest.json")
    os.makedirs(manifest_dir, exist_ok=True)
    manifest = {
        "app": "open-maestri",
        "appVersion": "1.0.0",
        "dataFormat": 2,
        "files": {},
        "schemaVersion": 1,
        "type": "appState",
        "workspaces": [],
    }
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and "workspaces" in loaded:
                    manifest = loaded
        except Exception:
            pass

    new_entry = {
        "color": "blue",
        "createdAt": now_iso,
        "icon": icon or "folder",
        "id": new_id,
        "isPinned": False,
        "locationType": "local",
        "name": name,
        "workingDirectory": wd or os.path.expanduser("~"),
    }
    manifest["workspaces"].append(new_entry)
    tmp_path = manifest_path + f".tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp_path, manifest_path)

    ws_dir = os.path.join(manifest_dir, "workspaces", new_id)
    os.makedirs(os.path.join(ws_dir, "notes"), exist_ok=True)
    ws_doc = {
        "schemaVersion": 2,
        "type": "workspace",
        "payload": {
            "id": new_id,
            "name": name,
            "workingDirectory": wd or os.path.expanduser("~"),
            "icon": icon or "folder",
            "color": "blue",
            "isPinned": False,
            "locationType": "local",
            "canvasOrigin": [9800, 8500],
            "canvasZoom": 1,
            "nodes": [],
            "connections": [],
            "drawings": [],
            "floors": [],
            "crossFloorConnections": [],
            "noteConnections": [],
            "noteToNoteConnections": [],
            "portalConnections": [],
            "portalToPortalConnections": [],
            "preferredIDE": "cursor",
            "syncConfigFiles": False,
            "createdAt": now_iso,
            "lastModifiedAt": now_iso,
        },
    }
    ws_file = os.path.join(ws_dir, "workspace.json")
    ws_tmp = ws_file + f".tmp.{os.getpid()}"
    with open(ws_tmp, "w", encoding="utf-8") as f:
        json.dump(ws_doc, f, indent=2)
    os.replace(ws_tmp, ws_file)
    return new_id


# (outline icon name, SF Symbol name stored in the workspace). 8 core icon options for creation (CreateWorkspaceSheet.swift)
CREATE_ICON_OPTIONS = [(sf_symbol_icon(n), n) for n in (
    "folder",
    "folder.fill",
    "terminal.fill",
    "cpu",
    "brain",
    "desktopcomputer",
    "network",
    "server.rack",
)]

# 24 icons for edit grid (matching EditWorkspaceSheet.swift 8x3 grid)
EDIT_ICON_OPTIONS = [(sf_symbol_icon(n), n) for n in (
    "folder",
    "folder.fill",
    "terminal.fill",
    "star.fill",
    "theatermasks",
    "gear",
    "bubble.left.and.bubble.right",
    "cpu",
    "rectangle",
    "list.bullet.rectangle",
    "globe",
    "hammer.fill",
    "wrench.and.screwdriver",
    "bolt.fill",
    "square.on.square",
    "photo.on.rectangle.angled",
    "laptopcomputer",
    "desktopcomputer",
    "paintbrush.fill",
    "doc.text",
    "shippingbox",
    "cube",
    "eye",
    "face.smiling",
)]

# 9 color options (matching EditWorkspaceSheet.swift)
COLOR_OPTIONS = [
    ("blue", "#007AFF"),
    ("red", "#FF3B30"),
    ("green", "#34C759"),
    ("orange", "#FF9500"),
    ("purple", "#AF52DE"),
    ("pink", "#FF2D55"),
    ("cyan", "#32ADE6"),
    ("yellow", "#FFCC00"),
    ("rainbow", "linear-gradient(135deg, #ff3b30, #ff9500, #ffcc00, #34c759, #007aff, #af52de)"),
]


class CreateWorkspaceDialog(Gtk.Window):
    """Matches macOS CreateWorkspaceSheet.swift (480x360)."""

    def __init__(
        self,
        parent: Gtk.Window,
        on_create: Callable[[str, str, str], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True, title="New Workspace")
        self.on_create = on_create
        self.set_default_size(480, 360)
        self.set_resizable(False)
        self.add_css_class("maestri-modal-window")

        self.selected_icon = "folder"
        self.working_directory = os.path.expanduser("~")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("maestri-sheet-root")

        # 1. Header with Title & Action Buttons
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("maestri-sheet-header")
        header.set_margin_top(16)
        header.set_margin_bottom(12)
        header.set_margin_start(20)
        header.set_margin_end(20)

        title = Gtk.Label(label="<b>New Workspace</b>", use_markup=True, xalign=0.0)
        title.add_css_class("maestri-sheet-title")
        title.set_hexpand(True)
        header.append(title)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.add_css_class("maestri-dialog-btn")
        btn_cancel.connect("clicked", lambda _b: self.destroy())
        header.append(btn_cancel)

        self.btn_create = Gtk.Button(label="Create")
        self.btn_create.add_css_class("maestri-dialog-btn-primary")
        self.btn_create.set_sensitive(False)
        self.btn_create.connect("clicked", self._on_create_clicked)
        header.append(self.btn_create)

        root.append(header)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 2. Body Form
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        body.set_margin_start(20)
        body.set_margin_end(20)

        # Basic Info
        lbl_info = Gtk.Label(label="<b>BASIC INFO</b>", use_markup=True, xalign=0.0)
        lbl_info.add_css_class("maestri-form-section-title")
        body.append(lbl_info)

        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("Workspace Name")
        self.name_entry.add_css_class("maestri-text-input")
        self.name_entry.connect("changed", self._on_name_changed)
        body.append(self.name_entry)

        # Working Directory
        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dir_box.add_css_class("maestri-dir-row")

        folder_icon = Gtk.Image.new_from_icon_name("maestri-folder-symbolic")
        folder_icon.set_pixel_size(16)
        dir_box.append(folder_icon)

        self.dir_label = Gtk.Label(label="~", xalign=0.0, ellipsize=3)
        self.dir_label.set_hexpand(True)
        self.dir_label.add_css_class("maestri-dir-path")
        dir_box.append(self.dir_label)

        btn_choose = Gtk.Button(label="Choose…")
        btn_choose.add_css_class("maestri-dialog-btn")
        btn_choose.connect("clicked", self._on_choose_directory)
        dir_box.append(btn_choose)

        body.append(dir_box)

        # Icon Section
        lbl_icon = Gtk.Label(label="<b>ICON</b>", use_markup=True, xalign=0.0)
        lbl_icon.add_css_class("maestri-form-section-title")
        body.append(lbl_icon)

        icon_flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.icon_buttons: dict[str, Gtk.Button] = {}

        for icon, name in CREATE_ICON_OPTIONS:
            btn = Gtk.Button(icon_name=icon)
            btn.add_css_class("maestri-icon-btn")
            if name == self.selected_icon:
                btn.add_css_class("active")
            btn.connect("clicked", lambda _b, n=name: self._select_icon(n))
            self.icon_buttons[name] = btn
            icon_flow.append(btn)

        body.append(icon_flow)

        root.append(body)
        self.set_child(root)

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        has_text = bool(entry.get_text().strip())
        self.btn_create.set_sensitive(has_text)

    def _select_icon(self, icon_name: str) -> None:
        self.selected_icon = icon_name
        for n, btn in self.icon_buttons.items():
            if n == icon_name:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def _on_choose_directory(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Workspace Directory")
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                if path:
                    self.working_directory = path
                    self.dir_label.set_label(path.replace(os.path.expanduser("~"), "~"))
                    if not self.name_entry.get_text().strip():
                        self.name_entry.set_text(os.path.basename(path))
        except Exception:
            pass

    def _close_dialog(self) -> None:
        _close_modal(self)

    def destroy(self) -> None:
        self._close_dialog()

    def _on_create_clicked(self, _btn: Gtk.Button) -> None:
        name = self.name_entry.get_text().strip()
        if not name:
            return
        self.on_create(name, self.working_directory, self.selected_icon)
        self._close_dialog()


class EditWorkspaceDialog(Gtk.Window):
    """Matches macOS EditWorkspaceSheet.swift (480x520)."""

    def __init__(
        self,
        parent: Gtk.Window,
        entry: WorkspaceEntry,
        on_save: Callable[[WorkspaceEntry], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True, title="Edit Workspace")
        self.entry = entry
        self.on_save = on_save
        self.set_default_size(480, 520)
        self.set_resizable(False)
        self.add_css_class("maestri-modal-window")

        self.selected_icon = entry.icon
        self.selected_color = getattr(entry, "color", "blue") or "blue"
        self.working_directory = entry.working_directory

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("maestri-sheet-root")

        # 1. Header with Title & Action Buttons
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("maestri-sheet-header")
        header.set_margin_top(16)
        header.set_margin_bottom(12)
        header.set_margin_start(20)
        header.set_margin_end(20)

        title = Gtk.Label(label="<b>Edit Workspace</b>", use_markup=True, xalign=0.0)
        title.add_css_class("maestri-sheet-title")
        title.set_hexpand(True)
        header.append(title)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.add_css_class("maestri-dialog-btn")
        btn_cancel.connect("clicked", lambda _b: self.destroy())
        header.append(btn_cancel)

        self.btn_save = Gtk.Button(label="Save")
        self.btn_save.add_css_class("maestri-dialog-btn-primary")
        self.btn_save.connect("clicked", self._on_save_clicked)
        header.append(self.btn_save)

        root.append(header)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 2. Scrollable Body
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(20)
        body.set_margin_bottom(20)
        body.set_margin_start(20)
        body.set_margin_end(20)

        # Name
        lbl_name = Gtk.Label(label="<b>WORKSPACE NAME</b>", use_markup=True, xalign=0.0)
        lbl_name.add_css_class("maestri-form-section-title")
        body.append(lbl_name)

        self.name_entry = Gtk.Entry()
        self.name_entry.set_text(entry.name)
        self.name_entry.add_css_class("maestri-text-input")
        self.name_entry.connect("changed", self._on_name_changed)
        body.append(self.name_entry)

        # Icon Grid (8 columns x 3 rows)
        lbl_icon = Gtk.Label(label="<b>ICON</b>", use_markup=True, xalign=0.0)
        lbl_icon.add_css_class("maestri-form-section-title")
        body.append(lbl_icon)

        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.icon_buttons: dict[str, Gtk.Button] = {}

        # Chunk into rows of 8
        for i in range(0, len(EDIT_ICON_OPTIONS), 8):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            for icon, name in EDIT_ICON_OPTIONS[i:i+8]:
                btn = Gtk.Button(icon_name=icon)
                btn.add_css_class("maestri-icon-btn")
                if name == self.selected_icon:
                    btn.add_css_class("active")
                btn.connect("clicked", lambda _b, n=name: self._select_icon(n))
                self.icon_buttons[name] = btn
                row.append(btn)
            grid_box.append(row)

        body.append(grid_box)

        # Color Palette
        lbl_color = Gtk.Label(label="<b>COLOR</b>", use_markup=True, xalign=0.0)
        lbl_color.add_css_class("maestri-form-section-title")
        body.append(lbl_color)

        color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.color_buttons: dict[str, Gtk.Button] = {}

        for cid, css_bg in COLOR_OPTIONS:
            btn = Gtk.Button()
            btn.add_css_class("maestri-color-dot")
            btn.add_css_class(f"color-{cid}")
            if cid == self.selected_color:
                btn.add_css_class("active")
            btn.connect("clicked", lambda _b, c=cid: self._select_color(c))
            self.color_buttons[cid] = btn
            color_row.append(btn)

        body.append(color_row)

        # Working Directory
        lbl_dir = Gtk.Label(label="<b>WORKING DIRECTORY</b>", use_markup=True, xalign=0.0)
        lbl_dir.add_css_class("maestri-form-section-title")
        body.append(lbl_dir)

        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dir_box.add_css_class("maestri-dir-row")

        folder_icon = Gtk.Image.new_from_icon_name("maestri-folder-symbolic")
        folder_icon.set_pixel_size(16)
        dir_box.append(folder_icon)

        self.dir_label = Gtk.Label(label=entry.working_directory.replace(os.path.expanduser("~"), "~"), xalign=0.0, ellipsize=3)
        self.dir_label.set_hexpand(True)
        self.dir_label.add_css_class("maestri-dir-path")
        dir_box.append(self.dir_label)

        btn_choose = Gtk.Button(label="Browse…")
        btn_choose.add_css_class("maestri-dialog-btn")
        btn_choose.connect("clicked", self._on_choose_directory)
        dir_box.append(btn_choose)

        body.append(dir_box)

        scrolled.set_child(body)
        root.append(scrolled)
        self.set_child(root)

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        has_text = bool(entry.get_text().strip())
        self.btn_save.set_sensitive(has_text)

    def _select_icon(self, icon_name: str) -> None:
        self.selected_icon = icon_name
        for n, btn in self.icon_buttons.items():
            if n == icon_name:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def _select_color(self, color_name: str) -> None:
        self.selected_color = color_name
        for c, btn in self.color_buttons.items():
            if c == color_name:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def _on_choose_directory(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Workspace Directory")
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                if path:
                    self.working_directory = path
                    self.dir_label.set_label(path.replace(os.path.expanduser("~"), "~"))
        except Exception:
            pass

    def _close_dialog(self) -> None:
        _close_modal(self)

    def destroy(self) -> None:
        self._close_dialog()

    def _on_save_clicked(self, _btn: Gtk.Button) -> None:
        name = self.name_entry.get_text().strip()
        if not name:
            return
        updated = self.entry
        updated.name = name
        updated.working_directory = self.working_directory
        updated.icon = self.selected_icon
        updated.color = self.selected_color
        self.on_save(updated)
        self._close_dialog()


class DeleteWorkspaceDialog(Gtk.Window):
    """Destructive delete confirmation modal."""

    def __init__(
        self,
        parent: Gtk.Window,
        workspace_name: str,
        on_confirm: Callable[[], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True, title="Delete Workspace")
        self.on_confirm = on_confirm
        self.set_default_size(360, 160)
        self.set_resizable(False)
        self.add_css_class("maestri-modal-window")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(20)
        root.set_margin_end(20)

        lbl = Gtk.Label(
            label=f"<b>Are you sure you want to delete '{workspace_name}'?</b>\nThis action cannot be undone.",
            use_markup=True,
            wrap=True,
            xalign=0.0,
        )
        lbl.add_css_class("maestri-dialog-warning-text")
        root.append(lbl)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.add_css_class("maestri-dialog-btn")
        btn_cancel.connect("clicked", lambda _b: self.destroy())
        btn_box.append(btn_cancel)

        btn_del = Gtk.Button(label="Delete")
        btn_del.add_css_class("maestri-dialog-btn-danger")
        btn_del.connect("clicked", self._on_delete_clicked)
        btn_box.append(btn_del)

        root.append(btn_box)
        self.set_child(root)

    def _close_dialog(self) -> None:
        _close_modal(self)

    def destroy(self) -> None:
        self._close_dialog()

    def _on_delete_clicked(self, _btn: Gtk.Button) -> None:
        self.on_confirm()
        self._close_dialog()


class GroupDialog(Gtk.Window):
    """Prompt for creating or renaming a SidebarGroup."""

    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        initial_name: str = "",
        on_ok: Callable[[str], None] = lambda _n: None,
    ) -> None:
        super().__init__(transient_for=parent, modal=True, title=title)
        self.on_ok = on_ok
        self.set_default_size(320, 140)
        self.set_resizable(False)
        self.add_css_class("maestri-modal-window")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(16)
        root.set_margin_end(16)

        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("Group Name")
        self.name_entry.set_text(initial_name)
        self.name_entry.add_css_class("maestri-text-input")
        root.append(self.name_entry)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.add_css_class("maestri-dialog-btn")
        btn_cancel.connect("clicked", lambda _b: self.destroy())
        btn_box.append(btn_cancel)

        btn_ok = Gtk.Button(label="OK")
        btn_ok.add_css_class("maestri-dialog-btn-primary")
        btn_ok.connect("clicked", self._on_ok_clicked)
        btn_box.append(btn_ok)

        root.append(btn_box)
        self.set_child(root)

    def _close_dialog(self) -> None:
        _close_modal(self)

    def destroy(self) -> None:
        self._close_dialog()

    def _on_ok_clicked(self, _btn: Gtk.Button) -> None:
        name = self.name_entry.get_text().strip()
        if name:
            self.on_ok(name)
        self._close_dialog()
