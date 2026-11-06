"""New Terminal / Edit Terminal dialogs (macOS Sources/Terminal/NewTerminalSheet.swift, EditTerminalSheet.swift).

Both return a dict (`on_create(data)` / `on_save(data)`) that maestri_core.terminal_fields.terminal_fields_from_dialog turns into
TerminalContent fields (Sprint 9 Vertice item 6):

    name, command, cwd, preset             New only: preset id (agent type comes from the command)
    icon, color                            SF Symbol name + hex, like the macOS schema (iconography.SF_SYMBOL_ICONS draws it)
    theme_id                               an id of maestri_canvas.terminal_themes.THEMES (no decorative names)
    font_family, font_size
    monitor / monitor_with_ombro           Monitor Activity  (serialized monitorWithOmbro)
    maestro / is_manager                   Maestro           (serialized isManager, contract K8)
    role_id                                preferences.rolePresets id, or None (No Role)

Texts follow Sources/Resources/Localizable.xcstrings (en).
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .iconography import check_button, icon_name, sf_symbol_icon  # noqa: E402

# "icon" = outline icon of gtk4-ui/src/ui/icons (maestri-<icon>-symbolic), never an emoji; "sf_icon"/"color"/"agent_type" =
# Preferences.swift AgentPreset.defaults (what the node stores).
PRESETS = [
    {"id": "claude", "name": "Claude Code", "command": "claude", "icon": "asterisk", "sf_icon": "seal",
     "color": "#007AFF", "agent_type": "claude_code"},
    {"id": "codex", "name": "Codex", "command": "codex", "icon": "brain", "sf_icon": "brain",
     "color": "#5856D6", "agent_type": "codex"},
    {"id": "gemini", "name": "Gemini CLI", "command": "gemini", "icon": "sparkle", "sf_icon": "sparkle",
     "color": "#34C759", "agent_type": "gemini_cli"},
    {"id": "opencode", "name": "OpenCode", "command": "opencode", "icon": "book-open", "sf_icon": "rectangle.portrait",
     "color": "#FF9500", "agent_type": "open_code"},
    {"id": "shell", "name": "Shell", "command": "bash", "icon": "terminal", "sf_icon": "terminal",
     "color": "#8E8E93", "agent_type": "generic_shell"},
]

# EditTerminalSheet.swift IconPickerView (the glyphs this port has: "hare" and "shield" have no outline here yet)
ICON_CHOICES = ("face.smiling", "terminal", "star", "sparkle", "bubble.left.and.bubble.right", "gearshape", "rectangle",
                "server.rack", "globe", "hammer", "wrench", "bolt", "tray.full", "desktopcomputer",
                "rectangle.inset.filled", "display", "paintbrush", "folder", "doc", "cube", "eye", "wand.and.stars")
# EditTerminalSheet.swift ColorPickerGridView
COLOR_CHOICES = ("#007AFF", "#FF3B30", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5AC8FA", "#FFCC00", "#8E8E93")
THEME_LABELS = {"dark": "Dark", "light": "Light", "dracula": "Dracula", "solarized-dark": "Solarized Dark",
                "solarized-light": "Solarized Light", "nord": "Nord", "one-dark": "One Dark", "tokyo-night": "Tokyo Night"}
FONT_SIZE_RANGE = (8, 32)

ROLES: list = []   # kept for importers; roles come from preferences.rolePresets (Settings → Agents)

_CSS = """
.maestri-term-dialog .maestri-form-label { font-size: 11px; }
.maestri-term-dialog entry { font-size: 12px; }
.maestri-term-dialog .maestri-section-label { font-size: 13px; font-weight: 500; opacity: .65; }
.maestri-preset-btn { border-radius: 10px; min-width: 44px; min-height: 44px; padding: 0; }
.maestri-icon-choice { min-width: 34px; min-height: 34px; padding: 0; border-radius: 8px; }
.maestri-icon-choice.active { background-color: alpha(@accent_color, .18); box-shadow: inset 0 0 0 1.5px @accent_color; }
.maestri-color-choice { min-width: 26px; min-height: 26px; padding: 0; border-radius: 13px; }
.maestri-color-choice.active { box-shadow: 0 0 0 2px @window_bg_color, 0 0 0 4px currentColor; }
.maestri-role-row { padding: 6px 8px; border-radius: 6px; }
"""


def _hex_class(hx: str) -> str:
    return "maestri-hex-" + hx.lstrip("#").lower()


def _color_css() -> str:
    """Per-colour classes: swatches are filled with their colour; preset tiles tint their icon (AgentIconButton)."""
    out = []
    for hx in sorted({*COLOR_CHOICES, *(p["color"] for p in PRESETS)}):
        c = _hex_class(hx)
        out.append(f".maestri-color-choice.{c} {{ background: {hx}; color: {hx}; }}")
        out.append(f".maestri-preset-btn.{c} {{ color: {hx}; }}")
        out.append(f".maestri-preset-btn.{c}.active {{ background: alpha({hx}, .15); box-shadow: inset 0 0 0 1.5px {hx}; }}")
    out.append(".maestri-maestro-check image { color: #AF52DE; }")
    out.append(".maestri-color-choice { border-radius: 13px; min-width: 26px; min-height: 26px; padding: 0; border: none; }")
    out.append(".maestri-color-choice.active { box-shadow: 0 0 0 2px @window_bg_color, 0 0 0 4px currentColor; }")
    return "\n".join(out)
_css_done: set[int] = set()


def _install_css() -> None:
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None or id(display) in _css_done:
        return
    prov = Gtk.CssProvider()
    prov.load_from_string(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    # Swatch / preset colours are data, not theme: above a user gtk.css (USER priority wins over specificity).
    colors = Gtk.CssProvider()
    colors.load_from_string(_color_css())
    Gtk.StyleContext.add_provider_for_display(display, colors, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    _css_done.add(id(display))


def available_themes() -> list[str]:
    try:
        from maestri_canvas.terminal_themes import THEMES
        return list(THEMES)
    except Exception:   # noqa: BLE001 - canvas package always present in the app
        return list(THEME_LABELS)


def role_presets(parent) -> list[dict]:
    prefs = getattr(parent, "preferences", None)
    roles = getattr(prefs, "_extra", {}).get("rolePresets") if prefs is not None else None
    return [r for r in roles if isinstance(r, dict) and r.get("id")] if isinstance(roles, list) else []


def _label(text: str, css: str = "maestri-form-label") -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0.0)
    lbl.add_css_class(css)
    return lbl


def _close_modal(win: Gtk.Window) -> None:
    try:
        win.set_visible(False)
        win.set_modal(False)
        win.set_transient_for(None)
    except Exception:
        pass


class _TerminalForm:
    """The Details / Appearance / Roles tabs shared by both dialogs."""

    def _build_tabs(self, root: Gtk.Box, role_tab_label: str) -> None:
        tabs_box = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
        tabs_box.add_css_class("linked")
        tabs_box.add_css_class("maestri-segmented-control")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.tab_buttons: dict[str, Gtk.ToggleButton] = {}
        group = None
        for tab_id, tab_label in (("details", "Details"), ("appearance", "Appearance"), ("roles", role_tab_label)):
            tb = Gtk.ToggleButton(label=tab_label, group=group)
            group = group or tb
            tb.add_css_class("maestri-segment-btn")
            tb.connect("toggled", lambda b, tid=tab_id: b.get_active() and self._select_tab(tid))
            self.tab_buttons[tab_id] = tb
            tabs_box.append(tb)
        root.append(tabs_box)
        self.stack.add_named(self._build_details_page(), "details")
        self.stack.add_named(self._wrap(self._build_appearance_page()), "appearance")
        self.stack.add_named(self._wrap(self._build_roles_page()), "roles")
        root.append(self.stack)

    @staticmethod
    def _wrap(child: Gtk.Widget) -> Gtk.Widget:
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sc.set_child(child)
        return sc

    def _select_tab(self, tab_id: str) -> None:
        self.stack.set_visible_child_name(tab_id)
        btn = self.tab_buttons.get(tab_id)
        if btn is not None and not btn.get_active():
            btn.set_active(True)
        for tid, b in self.tab_buttons.items():
            (b.add_css_class if tid == tab_id else b.remove_css_class)("active")

    # ---- details -----------------------------------------------------------------------------------------------
    def _build_details_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8)
        vbox.append(_label("Terminal Name"))
        self.name_entry = Gtk.Entry(text=self._init["name"], placeholder_text="Terminal name")
        vbox.append(self.name_entry)
        vbox.append(_label("Command"))
        self.cmd_entry = Gtk.Entry(text=self._init["command"], placeholder_text="e.g. claude, codex, gemini")
        vbox.append(self.cmd_entry)

        checks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8)
        self.chk_monitor = check_button("eye", "Monitor Activity")
        self.chk_monitor.set_active(self._init["monitor"])
        self.chk_monitor.set_tooltip_text("Notify when terminal activity is detected")
        checks.append(self.chk_monitor)
        self.chk_maestro = check_button("sparkles", self._maestro_label)
        self.chk_maestro.set_active(self._init["maestro"])
        self.chk_maestro.add_css_class("maestri-maestro-check")
        self.chk_maestro.set_tooltip_text("Maestro terminals can recruit sub-agents with omaestri recruit")
        checks.append(self.chk_maestro)
        vbox.append(checks)

        vbox.append(_label("Working Directory"))
        cwd_box = Gtk.Box(spacing=8)
        self.cwd_entry = Gtk.Entry(text=self._init["cwd"], hexpand=True)
        cwd_box.append(self.cwd_entry)
        choose = Gtk.Button(label="Choose…")
        choose.connect("clicked", lambda _b: self._choose_folder())
        cwd_box.append(choose)
        vbox.append(cwd_box)
        return vbox

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose Working Directory")

        def done(d, res) -> None:
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            if folder is not None and folder.get_path():
                self.cwd_entry.set_text(folder.get_path())

        dialog.select_folder(self, None, done)

    # ---- appearance (EditTerminalSheet appearanceTabView) ------------------------------------------------------
    def _build_appearance_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin_top=12, margin_bottom=12)

        sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sec.append(_label("Icon", "maestri-section-label"))
        grid = Gtk.FlowBox(max_children_per_line=9, min_children_per_line=9, selection_mode=Gtk.SelectionMode.NONE,
                           column_spacing=4, row_spacing=4, homogeneous=True)
        self.icon_buttons: dict[str, Gtk.Button] = {}
        for sf in ICON_CHOICES:
            b = Gtk.Button(icon_name=sf_symbol_icon(sf), tooltip_text=sf)
            b.add_css_class("flat")
            b.add_css_class("maestri-icon-choice")
            b.connect("clicked", lambda _b, v=sf: self._select_icon(v))
            self.icon_buttons[sf] = b
            grid.append(b)
        sec.append(grid)
        vbox.append(sec)

        sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sec.append(_label("Color", "maestri-section-label"))
        row = Gtk.Box(spacing=8)
        self.color_buttons: dict[str, Gtk.Button] = {}
        for hx in COLOR_CHOICES:
            b = Gtk.Button(tooltip_text=hx)
            b.add_css_class("maestri-color-choice")
            b.add_css_class(_hex_class(hx))
            b.connect("clicked", lambda _b, v=hx: self._select_color(v))
            self.color_buttons[hx] = b
            row.append(b)
        sec.append(row)
        vbox.append(sec)

        sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sec.append(_label("Theme", "maestri-section-label"))
        self.theme_ids = available_themes()
        self.theme_dropdown = Gtk.DropDown(model=Gtk.StringList.new([THEME_LABELS.get(t, t) for t in self.theme_ids]))
        sec.append(self.theme_dropdown)
        vbox.append(sec)

        sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sec.append(_label("Font", "maestri-section-label"))
        frow = Gtk.Box(spacing=8)
        self.font_entry = Gtk.Entry(text=self._init["font_family"], hexpand=True, placeholder_text="Monospace")
        self.font_spin = Gtk.SpinButton.new_with_range(*FONT_SIZE_RANGE, 1)
        self.font_spin.set_value(self._init["font_size"])
        frow.append(self.font_entry)
        frow.append(self.font_spin)
        sec.append(frow)
        vbox.append(sec)

        self._select_icon(self._init["icon"])
        self._select_color(self._init["color"])
        self._select_theme(self._init["theme_id"])
        return vbox

    def _select_icon(self, sf: str) -> None:
        self.selected_icon = sf
        for v, b in self.icon_buttons.items():
            (b.add_css_class if v == sf else b.remove_css_class)("active")

    def _select_color(self, hx: str) -> None:
        self.selected_color = hx
        for v, b in self.color_buttons.items():
            (b.add_css_class if v.lower() == (hx or "").lower() else b.remove_css_class)("active")

    def _select_theme(self, theme_id: Optional[str]) -> None:
        if theme_id in self.theme_ids:
            self.theme_dropdown.set_selected(self.theme_ids.index(theme_id))
        elif "dark" in self.theme_ids:
            self.theme_dropdown.set_selected(self.theme_ids.index("dark"))

    # ---- roles (RolePickerView) ----------------------------------------------------------------------------------
    def _build_roles_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8)
        desc = Gtk.Label(label="Assign a role to define this agent's focus.", xalign=0, wrap=True)
        desc.add_css_class("dim-label")
        vbox.append(desc)
        self.role_buttons: dict[Optional[str], Gtk.CheckButton] = {}
        none = Gtk.CheckButton(label="No Role")
        self.role_buttons[None] = none
        vbox.append(none)
        self._roles = role_presets(self._parent)
        for r in self._roles:
            rid = str(r["id"]).upper()
            b = Gtk.CheckButton(label=str(r.get("name", "Role")), group=none)
            prompt = r.get("prompt") or r.get("instructions") or ""
            if prompt:
                b.set_tooltip_text(str(prompt))
            self.role_buttons[rid] = b
            vbox.append(b)
        if not self._roles:
            hint = Gtk.Label(label="No custom roles. Add one in Settings", xalign=0)
            hint.add_css_class("dim-label")
            vbox.append(hint)
        current = (self._init["role_id"] or "").upper() or None
        (self.role_buttons.get(current) or none).set_active(True)
        return vbox

    def selected_role_id(self) -> Optional[str]:
        return next((rid for rid, b in self.role_buttons.items() if b.get_active() and rid), None)

    # ---- output ------------------------------------------------------------------------------------------------
    def _common_data(self) -> Dict[str, Any]:
        theme_idx = self.theme_dropdown.get_selected()
        theme_id = self.theme_ids[theme_idx] if 0 <= theme_idx < len(self.theme_ids) else None
        monitor, maestro = self.chk_monitor.get_active(), self.chk_maestro.get_active()
        return {
            "name": self.name_entry.get_text().strip() or "Terminal",
            "command": self.cmd_entry.get_text().strip() or "bash",
            "cwd": os.path.expanduser(self.cwd_entry.get_text().strip() or self._init["cwd"]),
            "icon": self.selected_icon,
            "color": self.selected_color,
            "theme_id": theme_id,
            "font_family": self.font_entry.get_text().strip() or None,
            "font_size": float(self.font_spin.get_value()),
            "monitor": monitor, "monitor_with_ombro": monitor,
            "maestro": maestro, "is_manager": maestro,
            "role_id": self.selected_role_id(),
        }


class NewTerminalDialog(_TerminalForm, Gtk.Window):
    """NewTerminalSheet: Quick Start presets + Details / Appearance / Roles, Cancel / Create."""
    _maestro_label = "Maestro"

    def __init__(self, parent: Gtk.Window, on_create: Callable[[Dict[str, Any]], None], default_cwd: Optional[str] = None,
                 default_preset: str = "claude") -> None:
        Gtk.Window.__init__(self, title="New Terminal", transient_for=parent, modal=True, resizable=False)
        _install_css()
        self.set_default_size(400, 480)
        self.add_css_class("maestri-modal-dialog")
        self.add_css_class("maestri-term-dialog")
        self._parent = parent
        self.on_create = on_create
        self.cwd = os.path.expanduser(default_cwd or "~")
        self.selected_preset = default_preset
        prefs = getattr(parent, "preferences", None)
        self._init = {"name": "", "command": "", "cwd": self.cwd, "monitor": True, "maestro": False, "icon": "terminal",
                      "color": "#8E8E93", "theme_id": getattr(prefs, "terminal_theme", "dark"),
                      "font_family": getattr(prefs, "terminal_font_family", "") or "",
                      "font_size": float(getattr(prefs, "terminal_font_size", 13.0) or 13.0), "role_id": None}

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, margin_start=20, margin_end=20, margin_bottom=16)
        title = Gtk.Label(label="New Terminal", margin_top=16, margin_bottom=12)
        title.add_css_class("heading")
        root.append(title)
        qs = _label("Quick Start")
        qs.set_margin_bottom(6)
        root.append(qs)

        preset_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_bottom=14)
        self.preset_buttons: dict[str, Gtk.Button] = {}
        for p in PRESETS:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            btn = Gtk.Button(icon_name=icon_name(p["icon"]), tooltip_text=p["name"], halign=Gtk.Align.CENTER)
            btn.add_css_class("maestri-preset-btn")
            btn.add_css_class(_hex_class(p["color"]))
            btn.connect("clicked", lambda _b, pid=p["id"]: self._select_preset(pid))
            name = Gtk.Label(label=p["name"], ellipsize=3, max_width_chars=10)
            name.add_css_class("caption")
            col.append(btn)
            col.append(name)
            self.preset_buttons[p["id"]] = btn
            preset_box.append(col)
        root.append(preset_box)

        self._build_tabs(root, "Roles")

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.create_btn = Gtk.Button(label="Create")
        self.create_btn.add_css_class("suggested-action")
        self.create_btn.connect("clicked", self._on_submit)
        actions.append(cancel)
        actions.append(self.create_btn)
        root.append(actions)
        self.set_child(root)
        self.set_default_widget(self.create_btn)

        self._select_preset(default_preset)
        self._select_tab("details")

    def _select_preset(self, preset_id: str) -> None:
        self.selected_preset = preset_id
        for pid, btn in self.preset_buttons.items():
            (btn.add_css_class if pid == preset_id else btn.remove_css_class)("active")
        p = next((x for x in PRESETS if x["id"] == preset_id), None)
        if p:
            self.name_entry.set_text(p["name"])
            self.cmd_entry.set_text(p["command"])
            self._select_icon(p["sf_icon"] if p["sf_icon"] in self.icon_buttons else "terminal")
            self._select_color(p["color"])
            self.selected_icon = p["sf_icon"]   # the preset's own symbol even when the grid has no tile for it

    def _on_submit(self, _btn) -> None:
        data = self._common_data()
        data["preset"] = self.selected_preset
        self.close()
        self.on_create(data)

    def close(self) -> None:
        _close_modal(self)
        super().close()


class EditTerminalDialog(_TerminalForm, Gtk.Window):
    """EditTerminalSheet: Details (name, command, Monitor Activity, Maestro Mode, directory), Appearance (icon, color,
    theme, font), Role. Save returns every field."""
    _maestro_label = "Maestro Mode"

    def __init__(self, parent: Gtk.Window, node, on_save: Callable[[dict], None]) -> None:
        Gtk.Window.__init__(self, title="Edit Terminal", transient_for=parent, modal=True, resizable=False)
        _install_css()
        self.set_default_size(460, 520)
        self.add_css_class("maestri-modal-window")
        self.add_css_class("maestri-term-dialog")
        self._parent = parent
        self.node = node
        self.on_save = on_save
        c = getattr(node, "content", None)
        prefs = getattr(parent, "preferences", None)
        self._init = {
            "name": getattr(c, "name", "") or getattr(node, "title", "") or "Terminal",
            "command": getattr(c, "command", "bash") or "bash",
            "cwd": getattr(c, "working_directory", "") or os.path.expanduser("~"),
            "monitor": bool(getattr(c, "monitor_with_ombro", False)),
            "maestro": bool(getattr(c, "is_manager", False)),
            "icon": getattr(c, "icon", "terminal") or "terminal",
            "color": getattr(c, "color", "#8E8E93") or "#8E8E93",
            "theme_id": getattr(c, "theme_id", None) or getattr(prefs, "terminal_theme", "dark"),
            "font_family": getattr(c, "font_family", None) or getattr(prefs, "terminal_font_family", "") or "",
            "font_size": float(getattr(c, "font_size", None) or getattr(prefs, "terminal_font_size", 13.0) or 13.0),
            "role_id": getattr(c, "assigned_role_id", None),
        }
        self.current_name, self.current_cmd, self.current_cwd = self._init["name"], self._init["command"], self._init["cwd"]

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=20, margin_bottom=20, margin_start=24,
                       margin_end=24)
        title = Gtk.Label(label="Edit Terminal", xalign=0.0)
        title.add_css_class("heading")
        root.append(title)
        self._build_tabs(root, "Role")

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._on_save_clicked)
        actions.append(cancel)
        actions.append(self.save_btn)
        root.append(actions)
        self.set_child(root)
        self.set_default_widget(self.save_btn)
        self._select_tab("details")

    def _on_save_clicked(self, _btn) -> None:
        data = self._common_data()
        self.close()
        self.on_save(data)

    def close(self) -> None:
        _close_modal(self)
        super().close()
