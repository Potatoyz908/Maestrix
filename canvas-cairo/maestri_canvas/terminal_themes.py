"""Terminal colour themes + font for every Vte.Terminal on the canvas (core-backed VteTerminalView and local create_terminal shells).

Theme names are the `terminalTheme` values of preferences.json (maestri_core.preferences.TERMINAL_THEMES). "system" keeps VTE's own
defaults. The table is pure data (no GI import) so it can be unit-tested headless; `apply_terminal_style` does the GTK part lazily.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalTheme:
    fg: str
    bg: str
    palette: tuple[str, ...]   # 16 ANSI colours: 0-7 normal, 8-15 bright


THEMES: dict[str, TerminalTheme] = {
    # VS Code Dark+: the VTE default dark blue is nearly unreadable on #1e1e1e.
    "dark": TerminalTheme("#d4d4d4", "#1e1e1e", (
        "#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
        "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#e5e5e5")),
    "light": TerminalTheme("#333333", "#ffffff", (
        "#000000", "#cd3131", "#00bc00", "#949800", "#0451a5", "#bc05bc", "#0598bc", "#555555",
        "#666666", "#cd3131", "#14ce14", "#b5ba00", "#0451a5", "#bc05bc", "#0598bc", "#a5a5a5")),
    "dracula": TerminalTheme("#f8f8f2", "#282a36", (
        "#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
        "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff")),
    "solarized-dark": TerminalTheme("#839496", "#002b36", (
        "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
        "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    "solarized-light": TerminalTheme("#657b83", "#fdf6e3", (
        "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
        "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    "nord": TerminalTheme("#d8dee9", "#2e3440", (
        "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
        "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4")),
    "one-dark": TerminalTheme("#abb2bf", "#282c34", (
        "#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
        "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff")),
    "tokyo-night": TerminalTheme("#a9b1d6", "#1a1b26", (
        "#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
        "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5")),
}
DEFAULT_THEME = "dark"
DEFAULT_FONT_FAMILY = "Monospace"
DEFAULT_FONT_SIZE = 11.0


def resolve_theme(name: str | None) -> TerminalTheme | None:
    """None = "system" (VTE defaults). Unknown names fall back to the default dark theme."""
    if name == "system":
        return None
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])


def apply_terminal_style(term, family: str | None = None, size: float | None = None, theme: str | None = None) -> None:
    """Font + colours on a Vte.Terminal. Safe to call again at any time (live preference changes)."""
    from gi.repository import Gdk, Pango   # lazy: keeps the table importable without a display

    fd = Pango.FontDescription()
    fd.set_family(family or DEFAULT_FONT_FAMILY)
    fd.set_size(int(round((size or DEFAULT_FONT_SIZE) * Pango.SCALE)))
    term.set_font(fd)

    t = resolve_theme(theme)
    if t is None:
        term.set_default_colors()
        return

    def rgba(hex_: str) -> Gdk.RGBA:
        c = Gdk.RGBA()
        c.parse(hex_)
        return c

    term.set_colors(rgba(t.fg), rgba(t.bg), [rgba(h) for h in t.palette])
    term.set_bold_is_bright(True)   # "bold blue" (ls directories, prompts) uses the bright blue
