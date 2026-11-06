"""
SettingsWindow: preferences dialog (General / Terminal / Shortcuts).
Edits are applied and persisted immediately (preferences.json), then `on_changed(prefs)` is called.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from typing import Callable, Optional

from maestri_core.preferences import (
    AUTOSAVE_RANGE, CANVAS_BACKGROUNDS, FONT_SIZE_RANGE, IDES, LANGUAGES, TERMINAL_THEMES,
    Preferences, PreferencesStore,
)

LANGUAGE_LABELS = {"en": "English", "zh-Hans": "简体中文"}
BACKGROUND_LABELS = {"dotGrid": "Dot grid", "solid": "Solid", "transparent": "Transparent"}
IDE_LABELS = {"cursor": "Cursor", "vscode": "VS Code", "xcode": "Xcode"}

# (label, accelerator) — display only; the real bindings live in OpenMaestriWindow.
SHORTCUTS = (
    ("New terminal", "Ctrl+N"),
    ("Save workspace", "Ctrl+S"),
    ("Preferences", "Ctrl+,"),
    ("Reset zoom", "Ctrl+0"),
    ("Toggle sidebar", "Ctrl+B"),
    ("Quit", "Ctrl+Q"),
)


class SettingsWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, store: Optional[PreferencesStore] = None,
                 on_changed: Optional[Callable[[Preferences], None]] = None):
        super().__init__(title="Preferences", transient_for=parent, modal=False)
        self.set_default_size(520, 420)
        self.set_destroy_with_parent(True)
        self.store = store or PreferencesStore()
        self.prefs = self.store.load()
        self._on_changed = on_changed
        self._loading = True

        notebook = Gtk.Notebook()
        notebook.append_page(self._build_general(), Gtk.Label(label="General"))
        notebook.append_page(self._build_terminal(), Gtk.Label(label="Terminal"))
        notebook.append_page(self._build_shortcuts(), Gtk.Label(label="Shortcuts"))
        self.set_child(notebook)
        self._loading = False

    # ---- helpers -------------------------------------------------------------------------------
    @staticmethod
    def _page() -> Gtk.Grid:
        grid = Gtk.Grid(column_spacing=16, row_spacing=12, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        return grid

    @staticmethod
    def _row(grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> None:
        lbl = Gtk.Label(label=label, xalign=0, hexpand=True)
        widget.set_halign(Gtk.Align.END)
        grid.attach(lbl, 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)

    @staticmethod
    def _dropdown(values, labels, current) -> Gtk.DropDown:
        model = Gtk.StringList.new([labels.get(v, v) for v in values])
        dd = Gtk.DropDown(model=model)
        dd.set_selected(values.index(current) if current in values else 0)
        return dd

    def _commit(self) -> None:
        if self._loading:
            return
        self.store.save(self.prefs)
        if self._on_changed:
            self._on_changed(self.prefs)

    # ---- pages ---------------------------------------------------------------------------------
    def _build_general(self) -> Gtk.Widget:
        g = self._page()
        lang = self._dropdown(LANGUAGES, LANGUAGE_LABELS, self.prefs.language)
        lang.connect("notify::selected", lambda d, _p: self._set("language", LANGUAGES[d.get_selected()]))
        self._row(g, 0, "Language (applies on restart)", lang)

        bg = self._dropdown(CANVAS_BACKGROUNDS, BACKGROUND_LABELS, self.prefs.canvas_background)
        bg.connect("notify::selected", lambda d, _p: self._set("canvas_background", CANVAS_BACKGROUNDS[d.get_selected()]))
        self._row(g, 1, "Canvas background", bg)

        ide = self._dropdown(IDES, IDE_LABELS, self.prefs.preferred_ide)
        ide.connect("notify::selected", lambda d, _p: self._set("preferred_ide", IDES[d.get_selected()]))
        self._row(g, 2, "Preferred IDE", ide)

        auto = Gtk.SpinButton.new_with_range(AUTOSAVE_RANGE[0], AUTOSAVE_RANGE[1], 5)
        auto.set_value(self.prefs.autosave_interval_seconds)
        auto.connect("value-changed", lambda s: self._set("autosave_interval_seconds", int(s.get_value())))
        self._row(g, 3, "Autosave interval (seconds)", auto)

        self.snap_switch = Gtk.Switch(active=self.prefs.canvas_snapping, valign=Gtk.Align.CENTER)
        self.snap_switch.connect("notify::active", lambda s, _p: self._set("canvas_snapping", s.get_active()))
        self._row(g, 4, "Snap to alignment guides", self.snap_switch)
        return g

    def _build_terminal(self) -> Gtk.Widget:
        g = self._page()
        font = Gtk.Entry(text=self.prefs.terminal_font_family, width_chars=20)
        font.connect("changed", lambda e: self._set("terminal_font_family", e.get_text().strip() or "Monospace"))
        self._row(g, 0, "Font family", font)

        size = Gtk.SpinButton.new_with_range(FONT_SIZE_RANGE[0], FONT_SIZE_RANGE[1], 1)
        size.set_value(self.prefs.terminal_font_size)
        size.connect("value-changed", lambda s: self._set("terminal_font_size", float(s.get_value())))
        self._row(g, 1, "Font size", size)

        theme = self._dropdown(TERMINAL_THEMES, {}, self.prefs.terminal_theme)
        theme.connect("notify::selected", lambda d, _p: self._set("terminal_theme", TERMINAL_THEMES[d.get_selected()]))
        self._row(g, 2, "Theme", theme)

        limit = Gtk.Switch(active=self.prefs.scrollback_memory_limit, valign=Gtk.Align.CENTER)
        limit.connect("notify::active", lambda s, _p: self._set("scrollback_memory_limit", s.get_active()))
        self._row(g, 3, "Limit scrollback memory", limit)

        meta = Gtk.Switch(active=self.prefs.option_as_meta, valign=Gtk.Align.CENTER)
        meta.connect("notify::active", lambda s, _p: self._set("option_as_meta", s.get_active()))
        self._row(g, 4, "Use Alt as Meta key", meta)
        return g

    def _build_shortcuts(self) -> Gtk.Widget:
        g = self._page()
        for i, (name, accel) in enumerate(SHORTCUTS):
            self._row(g, i, name, Gtk.Label(label=accel, css_classes=["dim-label"]))
        return g

    def _set(self, attr: str, value) -> None:
        if getattr(self.prefs, attr) == value:
            return
        setattr(self.prefs, attr, value)
        self._commit()
