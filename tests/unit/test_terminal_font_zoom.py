"""Ctrl +/-/0 inside a terminal changes only that terminal's font (pure key mapping)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "canvas-cairo"))

from maestri_canvas.terminal_features import font_zoom_action  # noqa: E402


def test_font_zoom_keys():
    assert font_zoom_action("minus", True, False) == "smaller"
    assert font_zoom_action("equal", True, False) == "bigger"
    assert font_zoom_action("plus", True, True) == "bigger"
    assert font_zoom_action("0", True, False) == "reset"


def test_font_zoom_needs_ctrl():
    assert font_zoom_action("minus", False, False) is None
    assert font_zoom_action("a", True, False) is None
