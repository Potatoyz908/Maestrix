"""Header pill shown while the sidebar is collapsed: [workspace icon in its colour] Workspace name.

Dark capsule next to the sidebar toggle (reference capture: Maestri macOS header with the sidebar hidden). It follows workspace
switches and renames; it hides as soon as the sidebar is visible again.

The CSS is installed at Gtk.STYLE_PROVIDER_PRIORITY_USER + 1: a user GTK theme (WhiteSur in ~/.config/gtk-4.0) loads at USER
priority and would otherwise repaint the capsule and recolour the icon.
"""
from __future__ import annotations

from typing import Optional

from gi.repository import Gdk, Gtk

from .workspace_dialogs import COLOR_OPTIONS

_PALETTE = {name: (value if value.startswith("#") else "#FF9500") for name, value in COLOR_OPTIONS}   # rainbow -> orange

PILL_CSS = """
.maestri-ws-pill {
  background-color: #262626;
  background-image: none;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 999px;
  padding: 3px 12px 3px 9px;
  min-height: 22px;
  box-shadow: none;
}
.maestri-ws-pill label {
  color: #f2f2f2;
  font-size: 13px;
  font-weight: 600;
}
""" + "".join(f".maestri-ws-pill image.pill-color-{n} {{ color: {c}; -gtk-icon-style: symbolic; }}\n" for n, c in _PALETTE.items())

_css_installed = False


def _install_css() -> None:
    global _css_installed
    display = Gdk.Display.get_default()
    if _css_installed or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(PILL_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    _css_installed = True


class WorkspacePill(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=7, valign=Gtk.Align.CENTER)
        _install_css()
        self.add_css_class("maestri-ws-pill")
        self.icon = Gtk.Image(pixel_size=14)
        self.label = Gtk.Label(ellipsize=3, max_width_chars=28)
        self.append(self.icon)
        self.append(self.label)
        self._color: Optional[str] = None
        self.set_visible(False)

    def update(self, name: str, icon_name: str, color: str) -> None:
        self.label.set_label(name or "Workspace")
        self.set_tooltip_text(name or None)
        self.icon.set_from_icon_name(icon_name)
        color = color if color in _PALETTE else "blue"
        if self._color != color:
            if self._color:
                self.icon.remove_css_class(f"pill-color-{self._color}")
            self.icon.add_css_class(f"pill-color-{color}")
            self._color = color

    @property
    def color(self) -> Optional[str]:
        return self._color

