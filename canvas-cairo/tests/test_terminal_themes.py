"""Terminal themes (preferences terminalTheme) and canvas background modes (preferences canvasBackground): pure, headless."""
import re
import sys
from pathlib import Path

import cairo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maestri_canvas.geometry import Point
from maestri_canvas.renderer import BG, GRID_STEP, SOLID_BG, CairoRenderer
from maestri_canvas.scene import Scene
from maestri_canvas.terminal_themes import THEMES, resolve_theme
from maestri_canvas.viewport import Viewport
from maestri_core.preferences import TERMINAL_THEMES

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_every_preference_theme_resolves():
    for name in TERMINAL_THEMES:
        t = resolve_theme(name)
        if name == "system":
            assert t is None   # VTE defaults
            continue
        assert t is THEMES[name]
        assert len(t.palette) == 16
        assert all(HEX.match(c) for c in (t.fg, t.bg, *t.palette))


def test_unknown_theme_falls_back_to_dark():
    assert resolve_theme("nope") is THEMES["dark"] and resolve_theme(None) is THEMES["dark"]


def _render(background):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
    CairoRenderer(background=background).draw(cairo.Context(s), Scene(), Viewport(Point(0, 0), 1.0), 200, 200)
    s.flush()
    return s


def _pixel(s, x, y):
    d = s.get_data(); o = y * s.get_stride() + x * 4
    return d[o + 2], d[o + 1], d[o], d[o + 3]


def test_background_modes():
    bg = tuple(round(c * 255) for c in BG) + (255,)
    solid = tuple(round(c * 255) for c in SOLID_BG) + (255,)
    dot = int(GRID_STEP)   # a grid line crosses every multiple of GRID_STEP at zoom 1
    assert _pixel(_render("dotGrid"), dot, dot) != bg and _pixel(_render("dotGrid"), 7, 7) == bg
    assert _pixel(_render("solid"), dot, dot) == solid and _pixel(_render("solid"), 7, 7) == solid
    assert _pixel(_render("transparent"), 7, 7)[3] == 0 and _pixel(_render("transparent"), dot, dot)[3] == 0
    assert CairoRenderer(background="bogus").background == "dotGrid"


def test_macos_theme_ids_resolve_to_themselves():
    """The 15 TerminalThemeRegistry ids (node themeId / preferences written by macOS) never fall back to "dark"."""
    macos_ids = ("maestri-dark", "maestri-light", "dracula", "solarized-dark", "solarized-light", "nord", "one-dark", "tokyo-night",
                 "tokyo-night-day", "catppuccin-mocha", "catppuccin-macchiato", "catppuccin-latte", "gruvbox-dark", "gruvbox-light",
                 "rose-pine")
    for name in macos_ids:
        t = resolve_theme(name)
        assert t is THEMES[name] and len(t.palette) == 16
        assert all(HEX.match(c) for c in (t.fg, t.bg, t.cursor, t.selection, *t.palette))
    assert resolve_theme("maestri-light") is not THEMES["dark"] and resolve_theme("light") is THEMES["maestri-light"]
    assert THEMES["maestri-dark"].palette[15] == "#ffffff"
    assert (THEMES["maestri-light"].fg, THEMES["maestri-light"].palette[2], THEMES["maestri-light"].palette[7]) == ("#383a42", "#008000", "#e5e5e5")


def test_system_theme_follows_appearance():
    assert resolve_theme("system", dark=True) is THEMES["maestri-dark"]
    assert resolve_theme("system", dark=False) is THEMES["maestri-light"]
