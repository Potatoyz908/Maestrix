"""
New Terminal modal dialog for Open Maestri GTK 4 Native.
Matches the official Maestri macOS UI (Quick Start presets, Details/Appearance/Roles tabs).
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .iconography import check_button, icon_name  # noqa: E402

# "icon" = outline icon of gtk4-ui/src/ui/icons (maestri-<icon>-symbolic), never an emoji
PRESETS = [
    {
        "id": "claude",
        "name": "Claude Code",
        "command": "claude",
        "icon": "asterisk",
        "color": "#D97706",
    },
    {
        "id": "codex",
        "name": "Codex",
        "command": "codex",
        "icon": "brain",
        "color": "#8B5CF6",
    },
    {
        "id": "gemini",
        "name": "Gemini CLI",
        "command": "gemini",
        "icon": "sparkle",
        "color": "#2563EB",
    },
    {
        "id": "opencode",
        "name": "OpenCode",
        "command": "opencode",
        "icon": "book-open",
        "color": "#059669",
    },
    {
        "id": "shell",
        "name": "Shell",
        "command": "bash",
        "icon": "terminal",
        "color": "#4B5563",
    },
]

ROLES = [
    ("None", "Default shell behavior"),
    ("Lead", "Coordinator that plans and delegates work"),
    ("Coder", "Focuses purely on code implementation"),
    ("Reviewer", "Reviews, critiques, and audits code changes"),
    ("Tester", "Writes and executes automated test suites"),
]


class NewTerminalDialog(Gtk.Window):
    """Modal dialog for creating and configuring new terminal nodes."""

    def __init__(
        self,
        parent: Gtk.Window,
        on_create: Callable[[Dict[str, Any]], None],
        default_cwd: Optional[str] = None,
        default_preset: str = "claude",
    ) -> None:
        super().__init__(title="New Terminal", transient_for=parent, modal=True)
        self.set_default_size(480, 560)
        self.set_resizable(False)
        self.add_css_class("maestri-modal-dialog")

        self.on_create = on_create
        self.cwd = os.path.expanduser(default_cwd or "~")
        self.selected_preset = default_preset

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(24)
        root.set_margin_end(24)

        # Title
        title_lbl = Gtk.Label(label="<b>New Terminal</b>", use_markup=True)
        title_lbl.add_css_class("maestri-dialog-title")
        root.append(title_lbl)

        # 1. Quick Start Section
        quick_lbl = Gtk.Label(label="QUICK START", xalign=0.5)
        quick_lbl.add_css_class("maestri-section-label")
        root.append(quick_lbl)

        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        preset_box.set_halign(Gtk.Align.CENTER)
        self.preset_buttons: dict[str, Gtk.Button] = {}

        for p in PRESETS:
            btn = Gtk.Button()
            btn.add_css_class("maestri-preset-btn")
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vbox.set_size_request(68, 64)

            icon_lbl = Gtk.Image.new_from_icon_name(icon_name(p["icon"]))   # outline icon (theme colour)
            icon_lbl.add_css_class("maestri-preset-icon")
            icon_lbl.add_css_class("maestri-preset-icon-img")
            name_lbl = Gtk.Label(label=p["name"], ellipsize=3)
            name_lbl.add_css_class("maestri-preset-name")

            vbox.append(icon_lbl)
            vbox.append(name_lbl)
            btn.set_child(vbox)
            btn.connect("clicked", lambda _b, pid=p["id"]: self._select_preset(pid))
            self.preset_buttons[p["id"]] = btn
            preset_box.append(btn)

        root.append(preset_box)

        # 2. Segmented Tabs
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        tabs_box.set_halign(Gtk.Align.CENTER)
        tabs_box.add_css_class("maestri-segmented-control")

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.tab_buttons: dict[str, Gtk.Button] = {}
        for tab_id, tab_label in (("details", "Details"), ("appearance", "Appearance"), ("roles", "Roles")):
            tb = Gtk.Button(label=tab_label)
            tb.add_css_class("maestri-segment-btn")
            tb.connect("clicked", lambda _b, tid=tab_id: self._select_tab(tid))
            self.tab_buttons[tab_id] = tb
            tabs_box.append(tb)

        root.append(tabs_box)

        # 3. Stack Pages
        # Page 1: Details
        details_page = self._build_details_page()
        self.stack.add_named(details_page, "details")

        # Page 2: Appearance
        appearance_page = self._build_appearance_page()
        self.stack.add_named(appearance_page, "appearance")

        # Page 3: Roles
        roles_page = self._build_roles_page()
        self.stack.add_named(roles_page, "roles")

        self.stack.set_vexpand(True)
        root.append(self.stack)

        # 4. Action Buttons (Cancel / Create)
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        actions_box.set_halign(Gtk.Align.END)
        actions_box.set_margin_top(8)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("maestri-btn-secondary")
        cancel_btn.connect("clicked", lambda _b: self.close())
        actions_box.append(cancel_btn)

        self.create_btn = Gtk.Button(label="Create")
        self.create_btn.add_css_class("suggested-action")
        self.create_btn.add_css_class("maestri-btn-primary")
        self.create_btn.connect("clicked", self._on_submit)
        actions_box.append(self.create_btn)

        root.append(actions_box)
        self.set_child(root)

        # Initialize selection
        self._select_preset(default_preset)
        self._select_tab("details")

    # ---- Tabs & Preset Selection -----------------------------------------------------------------
    def _select_preset(self, preset_id: str) -> None:
        self.selected_preset = preset_id
        for pid, btn in self.preset_buttons.items():
            if pid == preset_id:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

        p = next((x for x in PRESETS if x["id"] == preset_id), None)
        if p:
            self.name_entry.set_text(p["name"])
            self.cmd_entry.set_text(p["command"])

    def _select_tab(self, tab_id: str) -> None:
        self.stack.set_visible_child_name(tab_id)
        for tid, btn in self.tab_buttons.items():
            if tid == tab_id:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    # ---- Pages ------------------------------------------------------------------------------------
    def _build_details_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(8)

        # Terminal Name
        lbl_name = Gtk.Label(label="Terminal Name", xalign=0.0)
        lbl_name.add_css_class("maestri-form-label")
        vbox.append(lbl_name)
        self.name_entry = Gtk.Entry()
        self.name_entry.add_css_class("maestri-form-entry")
        vbox.append(self.name_entry)

        # Command
        lbl_cmd = Gtk.Label(label="Command", xalign=0.0)
        lbl_cmd.add_css_class("maestri-form-label")
        vbox.append(lbl_cmd)
        self.cmd_entry = Gtk.Entry()
        self.cmd_entry.add_css_class("maestri-form-entry")
        vbox.append(self.cmd_entry)

        # Checkboxes
        checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        checks_box.set_margin_top(4)

        self.chk_monitor = check_button("eye", "Monitor Activity")
        self.chk_monitor.set_active(True)
        checks_box.append(self.chk_monitor)

        self.chk_maestro = check_button("sparkles", "Maestro")
        self.chk_maestro.set_active(False)
        checks_box.append(self.chk_maestro)

        vbox.append(checks_box)

        # Working Directory
        lbl_cwd = Gtk.Label(label="Working Directory", xalign=0.0)
        lbl_cwd.add_css_class("maestri-form-label")
        vbox.append(lbl_cwd)

        cwd_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.cwd_entry = Gtk.Entry(text=self.cwd)
        self.cwd_entry.set_hexpand(True)
        self.cwd_entry.add_css_class("maestri-form-entry")
        cwd_box.append(self.cwd_entry)

        choose_btn = Gtk.Button(label="Choose…")
        choose_btn.add_css_class("maestri-btn-secondary")
        choose_btn.connect("clicked", self._on_choose_folder)
        cwd_box.append(choose_btn)

        vbox.append(cwd_box)
        return vbox

    def _build_appearance_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(8)

        lbl_theme = Gtk.Label(label="Color Theme", xalign=0.0)
        lbl_theme.add_css_class("maestri-form-label")
        vbox.append(lbl_theme)

        themes = ["Maestri Dark", "Maestri Light", "Solarized Dark", "Hacker Matrix", "Monokai"]
        model = Gtk.StringList.new(themes)
        self.theme_dropdown = Gtk.DropDown.new(model, None)
        self.theme_dropdown.set_selected(0)
        vbox.append(self.theme_dropdown)

        lbl_font = Gtk.Label(label="Font Size", xalign=0.0)
        lbl_font.add_css_class("maestri-form-label")
        vbox.append(lbl_font)

        self.font_spin = Gtk.SpinButton.new_with_range(8, 32, 1)
        self.font_spin.set_value(12)
        vbox.append(self.font_spin)
        return vbox

    def _build_roles_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(8)

        lbl_role = Gtk.Label(label="Assign Role Instructions", xalign=0.0)
        lbl_role.add_css_class("maestri-form-label")
        vbox.append(lbl_role)

        role_strings = [f"{name} — {desc}" for name, desc in ROLES]
        model = Gtk.StringList.new(role_strings)
        self.role_dropdown = Gtk.DropDown.new(model, None)
        self.role_dropdown.set_selected(0)
        vbox.append(self.role_dropdown)
        return vbox

    def _on_choose_folder(self, _btn) -> None:
        dialog = Gtk.FileChooserNative.new(
            title="Choose Working Directory",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Select",
            cancel_label="Cancel",
        )

        def response(d, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = d.get_file()
                if gfile:
                    path = gfile.get_path()
                    if path:
                        self.cwd = path
                        self.cwd_entry.set_text(path)

        dialog.connect("response", response)
        dialog.show()

    def _on_submit(self, _btn) -> None:
        name = self.name_entry.get_text().strip() or "Terminal"
        cmd = self.cmd_entry.get_text().strip() or "bash"
        cwd = self.cwd_entry.get_text().strip() or self.cwd

        theme_item = self.theme_dropdown.get_selected_item()
        theme_str = theme_item.get_string() if theme_item else "Maestri Dark"
        font_size = int(self.font_spin.get_value())

        role_idx = self.role_dropdown.get_selected()
        role_name = ROLES[role_idx][0] if 0 <= role_idx < len(ROLES) else "None"

        data = {
            "name": name,
            "command": cmd,
            "cwd": os.path.expanduser(cwd),
            "monitor": self.chk_monitor.get_active(),
            "maestro": self.chk_maestro.get_active(),
            "theme": theme_str,
            "font_size": font_size,
            "role": role_name if role_name != "None" else None,
            "preset": self.selected_preset,
        }
        self.close()
        self.on_create(data)

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        super().close()


class EditTerminalDialog(Gtk.Window):
    """Matches macOS EditTerminalSheet.swift: edit Name, Command, Directory."""

    def __init__(
        self,
        parent: Gtk.Window,
        node,
        on_save: Callable[[dict], None],
    ) -> None:
        super().__init__(transient_for=parent, modal=True, title="Edit Terminal")
        self.node = node
        self.on_save = on_save
        self.set_default_size(440, 360)
        self.set_resizable(False)
        self.add_css_class("maestri-modal-window")

        content = getattr(node, "content", None)
        self.current_name = getattr(content, "name", "") or getattr(node, "title", "") or "Terminal"
        self.current_cmd = getattr(content, "command", "bash") or "bash"
        self.current_cwd = getattr(content, "working_directory", "") or os.path.expanduser("~")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(24)
        root.set_margin_end(24)

        title = Gtk.Label(label="<b>Edit Terminal</b>", use_markup=True, xalign=0.0)
        title.add_css_class("maestri-dialog-title")
        root.append(title)

        # Form fields
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Name
        lbl_name = Gtk.Label(label="Terminal Name", xalign=0.0)
        lbl_name.add_css_class("maestri-form-label")
        vbox.append(lbl_name)
        self.name_entry = Gtk.Entry(text=self.current_name)
        self.name_entry.add_css_class("maestri-form-entry")
        vbox.append(self.name_entry)

        # Command
        lbl_cmd = Gtk.Label(label="Command", xalign=0.0)
        lbl_cmd.add_css_class("maestri-form-label")
        vbox.append(lbl_cmd)
        self.cmd_entry = Gtk.Entry(text=self.current_cmd)
        self.cmd_entry.add_css_class("maestri-form-entry")
        vbox.append(self.cmd_entry)

        # Working Directory
        lbl_cwd = Gtk.Label(label="Working Directory", xalign=0.0)
        lbl_cwd.add_css_class("maestri-form-label")
        vbox.append(lbl_cwd)

        cwd_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.cwd_entry = Gtk.Entry(text=self.current_cwd)
        self.cwd_entry.set_hexpand(True)
        self.cwd_entry.add_css_class("maestri-form-entry")
        cwd_box.append(self.cwd_entry)

        choose_btn = Gtk.Button(label="Choose…")
        choose_btn.add_css_class("maestri-dialog-btn")
        choose_btn.connect("clicked", self._on_choose_folder)
        cwd_box.append(choose_btn)
        vbox.append(cwd_box)

        root.append(vbox)

        # Buttons
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions_box.set_halign(Gtk.Align.END)
        actions_box.set_margin_top(8)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("maestri-dialog-btn")
        cancel_btn.connect("clicked", lambda _b: self.close())
        actions_box.append(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("maestri-dialog-btn-primary")
        save_btn.connect("clicked", self._on_save_clicked)
        actions_box.append(save_btn)

        root.append(actions_box)
        self.set_child(root)

    def _on_choose_folder(self, _btn) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Working Directory")
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                if path:
                    self.current_cwd = path
                    self.cwd_entry.set_text(path)
        except Exception:
            pass

    def _on_save_clicked(self, _btn) -> None:
        name = self.name_entry.get_text().strip() or "Terminal"
        cmd = self.cmd_entry.get_text().strip() or "bash"
        cwd = self.cwd_entry.get_text().strip() or self.current_cwd

        data = {
            "name": name,
            "command": cmd,
            "cwd": os.path.expanduser(cwd),
        }
        self.close()
        self.on_save(data)

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        super().close()
