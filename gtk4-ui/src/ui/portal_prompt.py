"""New Portal sheet (macOS Sources/Portal/NewPortalSheet.swift): Name (optional) + URL, Cancel / Create.

`ask_portal_url(window, on_confirm)` presents it modal over `window`; Create calls `on_confirm(name, url)` with a normalised
URL (a bare host gets https://). Create is disabled while the URL is empty, like the macOS sheet.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def normalize_url(text: str) -> str:
    url = (text or "").strip()
    if not url or url in ("https://", "http://"):
        return ""
    if "://" not in url and not url.startswith(("about:", "file:", "data:")):
        url = "https://" + url
    return url


class NewPortalDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, on_confirm: Callable[[str, str], None]) -> None:
        super().__init__(title="New Portal", transient_for=parent, modal=True, resizable=False)
        self.set_default_size(480, 190)
        self.add_css_class("maestri-modal-window")
        self.on_confirm = on_confirm

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="New Portal", margin_top=20, margin_bottom=16)
        title.add_css_class("heading")
        root.append(title)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin_start=24, margin_end=24)
        self.name_entry = Gtk.Entry(placeholder_text="Optional", hexpand=True)
        self.url_entry = Gtk.Entry(text="https://", placeholder_text="Enter URL…", hexpand=True, activates_default=True)
        for row, (label, entry) in enumerate((("Name", self.name_entry), ("URL", self.url_entry))):
            lbl = Gtk.Label(label=label, xalign=1, width_chars=5)
            lbl.add_css_class("dim-label")
            grid.attach(lbl, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
        root.append(grid)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=20, margin_bottom=20, margin_end=24)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.create_btn = Gtk.Button(label="Create")
        self.create_btn.add_css_class("suggested-action")
        self.create_btn.connect("clicked", lambda _b: self.confirm())
        buttons.append(cancel)
        buttons.append(self.create_btn)
        root.append(buttons)
        self.set_child(root)
        self.set_default_widget(self.create_btn)

        self.url_entry.connect("changed", lambda _e: self.create_btn.set_sensitive(bool(normalize_url(self.url_entry.get_text()))))
        self.create_btn.set_sensitive(False)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _code, _st: (self.close(), True)[1] if kv == 0xff1b else False)   # Esc
        self.add_controller(keys)
        self.url_entry.grab_focus()
        self.url_entry.set_position(-1)

    def confirm(self) -> bool:
        url = normalize_url(self.url_entry.get_text())
        if not url:
            return False
        name = self.name_entry.get_text().strip()
        self.close()
        self.on_confirm(name, url)
        return True

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        super().close()


def ask_portal_url(window: Gtk.Window, on_confirm: Callable[[str, str], None]) -> NewPortalDialog:
    dlg = NewPortalDialog(window, on_confirm)
    window.portal_dialog = dlg
    dlg.present()
    return dlg
