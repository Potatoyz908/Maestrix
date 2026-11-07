"""Regression: the terminal theme dropdown listed dark/light AND maestri-dark/maestri-light (the same theme twice)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "canvas-cairo", REPO / "gtk4-ui" / "src", REPO):
    sys.path.insert(0, str(p))


def test_each_theme_is_listed_once_and_aliases_are_not_listed() -> None:
    from maestri_canvas.terminal_themes import THEME_ALIASES, THEMES
    from ui.terminal_dialog import available_themes
    ids = available_themes()
    assert len(ids) == len(set(ids))
    assert not set(THEME_ALIASES) & set(ids), "legacy aliases must not appear as separate entries"
    assert "maestri-dark" in ids and "maestri-light" in ids
    assert len({id(THEMES[t]) for t in ids}) == len(ids), "two listed ids must not be the same theme"
