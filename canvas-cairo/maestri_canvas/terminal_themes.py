"""Terminal colour themes + font for every Vte.Terminal on the canvas (core-backed VteTerminalView and local create_terminal shells).

Theme names are the `terminalTheme` values of preferences.json (maestri_core.preferences.TERMINAL_THEMES) and the macOS theme ids. "system" follows
the appearance (maestri-dark / maestri-light). The table is pure data (no GI import) so it can be unit-tested headless; `apply_terminal_style` does the GTK part lazily.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalTheme:
    fg: str
    bg: str
    palette: tuple[str, ...]   # 16 ANSI colours: 0-7 normal, 8-15 bright
    cursor: str | None = None
    selection: str | None = None


# The 15 built-in themes of TerminalThemeRegistry.builtInThemes (Sources/Terminal/TerminalThemeRegistry.swift), same ids and values.
THEMES: dict[str, TerminalTheme] = {
    # Maestri Dark (TerminalThemeRegistry.swift:129)
    "maestri-dark": TerminalTheme("#d4d4d4", "#1e1e1e", (
        "#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
        "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"), cursor="#aeafad", selection="#264f78"),
    # Maestri Light (TerminalThemeRegistry.swift:154)
    "maestri-light": TerminalTheme("#383a42", "#ffffff", (
        "#000000", "#cd3131", "#008000", "#949800", "#0451a5", "#bc05bc", "#0598bc", "#e5e5e5",
        "#666666", "#cd3131", "#14ce14", "#b5ba00", "#0451a5", "#bc05bc", "#0598bc", "#a5a5a5"), cursor="#526fff", selection="#add6ff"),
    # Dracula (TerminalThemeRegistry.swift:179)
    "dracula": TerminalTheme("#f8f8f2", "#282a36", (
        "#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
        "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"), cursor="#f8f8f2", selection="#44475a"),
    # Solarized Dark (TerminalThemeRegistry.swift:204)
    "solarized-dark": TerminalTheme("#839496", "#002b36", (
        "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
        "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"), cursor="#839496", selection="#073642"),
    # Solarized Light (TerminalThemeRegistry.swift:229)
    "solarized-light": TerminalTheme("#657b83", "#fdf6e3", (
        "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
        "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"), cursor="#657b83", selection="#eee8d5"),
    # Nord (TerminalThemeRegistry.swift:254)
    "nord": TerminalTheme("#d8dee9", "#2e3440", (
        "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
        "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"), cursor="#d8dee9", selection="#434c5e"),
    # One Dark (TerminalThemeRegistry.swift:279)
    "one-dark": TerminalTheme("#abb2bf", "#282c34", (
        "#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
        "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff"), cursor="#528bff", selection="#3e4451"),
    # Tokyo Night (TerminalThemeRegistry.swift:304)
    "tokyo-night": TerminalTheme("#a9b1d6", "#1a1b26", (
        "#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
        "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"), cursor="#c0caf5", selection="#33467c"),
    # Tokyo Night Day (TerminalThemeRegistry.swift:329)
    "tokyo-night-day": TerminalTheme("#3760bf", "#e1e2e7", (
        "#e9e9ed", "#f52a65", "#587539", "#8c6c3e", "#2e7de9", "#9854f1", "#007197", "#6172b0",
        "#a1a6c5", "#f52a65", "#587539", "#8c6c3e", "#2e7de9", "#9854f1", "#007197", "#3760bf"), cursor="#3760bf", selection="#b6bfe2"),
    # Catppuccin Mocha (TerminalThemeRegistry.swift:354)
    "catppuccin-mocha": TerminalTheme("#cdd6f4", "#1e1e2e", (
        "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
        "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"), cursor="#f5e0dc", selection="#45475a"),
    # Catppuccin Macchiato (TerminalThemeRegistry.swift:379)
    "catppuccin-macchiato": TerminalTheme("#cad3f5", "#24273a", (
        "#494d64", "#ed8796", "#a6da95", "#eed49f", "#8aadf4", "#f5bde6", "#8bd5ca", "#b8c0e0",
        "#5b6078", "#ed8796", "#a6da95", "#eed49f", "#8aadf4", "#f5bde6", "#8bd5ca", "#a5adcb"), cursor="#f4dbd6", selection="#494d64"),
    # Catppuccin Latte (TerminalThemeRegistry.swift:404)
    "catppuccin-latte": TerminalTheme("#4c4f69", "#eff1f5", (
        "#5c5f77", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#acb0be",
        "#6c6f85", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#bcc0cc"), cursor="#dc8a78", selection="#acb0be"),
    # Gruvbox Dark (TerminalThemeRegistry.swift:429)
    "gruvbox-dark": TerminalTheme("#ebdbb2", "#282828", (
        "#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
        "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"), cursor="#ebdbb2", selection="#504945"),
    # Gruvbox Light (TerminalThemeRegistry.swift:454)
    "gruvbox-light": TerminalTheme("#3c3836", "#fbf1c7", (
        "#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64",
        "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836"), cursor="#3c3836", selection="#d5c4a1"),
    # Rosé Pine (TerminalThemeRegistry.swift:479)
    "rose-pine": TerminalTheme("#e0def4", "#191724", (
        "#26233a", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4",
        "#6e6a86", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4"), cursor="#524f67", selection="#2a283e"),
}
# preferences.json stores "dark" / "light" (resolveThemeId maps them to maestri-dark / maestri-light).
THEME_ALIASES = {"dark": "maestri-dark", "light": "maestri-light"}
for _alias, _id in THEME_ALIASES.items():
    THEMES[_alias] = THEMES[_id]
DEFAULT_THEME = "dark"
# Constants.defaultFontFamily = "system" -> NSFont.monospacedSystemFont (SF Mono), Constants.defaultFontSize = 13.
SYSTEM_FONT_FAMILY = "system"
MONO_FONT_STACK = "SF Mono,JetBrains Mono,Cascadia Mono,DejaVu Sans Mono,Monospace"
DEFAULT_FONT_FAMILY = MONO_FONT_STACK
DEFAULT_FONT_SIZE = 13.0


def resolve_theme(name: str | None, dark: bool | None = None) -> TerminalTheme | None:
    """Theme for a preferences value or a node themeId (macOS ids such as "maestri-dark" resolve to themselves).
    "system" follows the appearance like TerminalThemeRegistry.resolveThemeId: maestri-dark / maestri-light when `dark` is
    known, None (VTE defaults) when it is not. Unknown names fall back to the default dark theme."""
    if name == "system":
        if dark is None:
            return None
        return THEMES["maestri-dark" if dark else "maestri-light"]
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])


def resolve_font_family(family: str | None) -> str:
    """"system" / empty (the macOS default) -> the monospace stack; any other family is the user's explicit choice."""
    if not family or family == SYSTEM_FONT_FAMILY:
        return MONO_FONT_STACK
    return family


def _prefers_dark() -> bool | None:
    try:
        from gi.repository import Adw
        return bool(Adw.StyleManager.get_default().get_dark())
    except Exception:   # noqa: BLE001 - no libadwaita / no display: keep VTE defaults
        return None


def apply_terminal_style(term, family: str | None = None, size: float | None = None, theme: str | None = None) -> None:
    """Font + colours on a Vte.Terminal. Safe to call again at any time (live preference changes)."""
    from gi.repository import Gdk, Pango   # lazy: keeps the table importable without a display

    fd = Pango.FontDescription()
    fd.set_family(resolve_font_family(family))
    fd.set_size(int(round((size or DEFAULT_FONT_SIZE) * Pango.SCALE)))
    term.set_font(fd)

    t = resolve_theme(theme, _prefers_dark() if theme == "system" else None)
    if t is None:
        term.set_default_colors()
        return

    def rgba(hex_: str) -> Gdk.RGBA:
        c = Gdk.RGBA()
        c.parse(hex_)
        return c

    term.set_colors(rgba(t.fg), rgba(t.bg), [rgba(h) for h in t.palette])
    term.set_bold_is_bright(True)   # "bold blue" (ls directories, prompts) uses the bright blue
    # TerminalThemeRegistry.apply: caretColor + selectedTextBackgroundColor
    term.set_color_cursor(rgba(t.cursor) if t.cursor else None)
    term.set_color_cursor_foreground(rgba(t.bg) if t.cursor else None)
    term.set_color_highlight(rgba(t.selection) if t.selection else None)
    term.set_color_highlight_foreground(None)
