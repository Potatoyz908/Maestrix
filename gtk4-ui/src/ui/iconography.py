"""Monochrome outline icons for the GTK4 UI (no emoji anywhere in the chrome).

The SVGs live in gtk4-ui/src/ui/icons/hicolor/scalable/actions/maestri-<name>-symbolic.svg (built by
scripts/build-gtk-icons.py). They are symbolic icons: GTK paints them with the widget's CSS `color`, so they follow the
theme, hover and destructive colours like text does.

Note on Gio.Menu: GtkPopoverMenu only shows a menu item's `icon` attribute when the item has no label (GtkModelButton
hides the image next to text), so menus that must show icons next to labels build their own rows (context_menus.py).
"""
from __future__ import annotations

import os

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")
_installed: set[int] = set()


def install_icon_theme(display: Gdk.Display | None = None) -> None:
    """Add the maestri-* icons to the display's icon theme (idempotent)."""
    display = display or Gdk.Display.get_default()
    if display is None or id(display) in _installed or not os.path.isdir(ICONS_DIR):
        return
    Gtk.IconTheme.get_for_display(display).add_search_path(ICONS_DIR)
    _installed.add(id(display))


def icon_name(name: str) -> str:
    """'terminal' -> 'maestri-terminal-symbolic' (full names pass through)."""
    return name if name.endswith("-symbolic") else f"maestri-{name}-symbolic"


def gicon(name: str) -> Gio.Icon:
    """For Gio.MenuItem.set_icon (shown by GTK only on label-less items, e.g. icon-only menu sections)."""
    return Gio.ThemedIcon.new(icon_name(name))


def icon_label(icon: str, text: str, spacing: int = 6) -> Gtk.Box:
    """[icon] text — the child of a button/check button. `.icon` and `.label` give access to the parts."""
    box = Gtk.Box(spacing=spacing)
    box.icon = Gtk.Image.new_from_icon_name(icon_name(icon))
    box.label = Gtk.Label(label=text, xalign=0)
    box.append(box.icon)
    box.append(box.label)
    return box


def check_button(icon: str, text: str) -> Gtk.CheckButton:
    """Gtk.CheckButton whose label carries an outline icon (Gtk.CheckButton.set_child, GTK >= 4.8)."""
    chk = Gtk.CheckButton()
    chk.set_child(icon_label(icon, text))
    chk.get_label = lambda: text   # the label text, as with Gtk.CheckButton(label=...)
    return chk


# Workspace icons are stored as SF Symbol names (Maestri schema); the GTK picker and sidebar draw these outlines.
SF_SYMBOL_ICONS: dict[str, str] = {
    "folder": "folder", "folder.fill": "folder-open", "terminal": "terminal", "terminal.fill": "terminal",
    "cpu": "cpu", "brain": "brain", "desktopcomputer": "desktop", "laptopcomputer": "laptop", "network": "network",
    "server.rack": "server", "star.fill": "star", "star": "star", "theatermasks": "masks", "gear": "settings",
    "bubble.left.and.bubble.right": "chat", "rectangle": "square", "list.bullet.rectangle": "list-box", "globe": "globe",
    "hammer.fill": "hammer", "hammer": "hammer", "wrench.and.screwdriver": "wrench", "bolt.fill": "bolt", "bolt": "bolt",
    "square.on.square": "squares", "photo.on.rectangle.angled": "photo", "paintbrush.fill": "brush",
    "paintbrush": "brush", "doc.text": "file-text", "shippingbox": "box", "cube": "cube", "eye": "eye",
    "face.smiling": "smile",
    # terminal icons (EditTerminalSheet.swift IconPickerView) + AgentPreset.defaults icons
    "gearshape": "settings", "wrench": "wrench", "tray.full": "archive", "display": "desktop", "doc": "file",
    "wand.and.stars": "sparkles", "sparkle": "sparkle", "sparkles": "sparkles", "seal": "asterisk",
    "rectangle.portrait": "book-open", "rectangle.inset.filled": "fill-solid",
    "hare": "hare", "shield": "shield",
}


def sf_symbol_icon(sf_name: str | None) -> str:
    """Icon name for a workspace's SF Symbol (unknown names fall back to the folder outline)."""
    return icon_name(SF_SYMBOL_ICONS.get(sf_name or "", "folder"))


class StatusIndicator(Gtk.Box):
    """Header connection status: a CSS-drawn dot in the semantic colour + the text. Replaces the 🟢/🔴/⚪/⚠️ prefixes.

    States: connected (green), error (red), warning (orange), offline / pending (grey). `get_label()` returns only the text,
    so callers and tests that look for "Porta …", "Standalone", "Token Inválido" or "Falha ao Salvar" keep working."""
    STATES = ("connected", "error", "warning", "offline", "pending")

    def __init__(self, state: str = "pending", text: str = "") -> None:
        super().__init__(spacing=6, valign=Gtk.Align.CENTER)
        self.add_css_class("maestri-status")
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("maestri-status-dot")
        self.label = Gtk.Label()
        self.append(self.dot)
        self.append(self.label)
        self.state = ""
        self.set_status(state, text)

    def set_status(self, state: str, text: str) -> None:
        if state not in self.STATES:
            raise ValueError(f"unknown status state: {state!r}")
        if self.state:
            self.dot.remove_css_class(self.state)
        self.state = state
        self.dot.add_css_class(state)
        self.label.set_label(text)

    def get_label(self) -> str:
        return self.label.get_label()


CSS = """
.maestri-status-dot { min-width: 8px; min-height: 8px; border-radius: 50%; background-color: #8e8e93; }
.maestri-status-dot.connected { background-color: #34C759; }
.maestri-status-dot.error { background-color: #FF3B30; }
.maestri-status-dot.warning { background-color: #FF9500; }
.maestri-status-dot.offline, .maestri-status-dot.pending { background-color: #8e8e93; }
.maestri-preset-icon-img { -gtk-icon-size: 22px; }
.maestri-icon-choice image { -gtk-icon-size: 18px; }
.maestri-expanded { -gtk-icon-transform: rotate(90deg); }
"""
_css_installed: set[int] = set()


def install_css(display: Gdk.Display | None = None) -> None:
    display = display or Gdk.Display.get_default()
    if display is None or id(display) in _css_installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed.add(id(display))
