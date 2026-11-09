import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "gtk4-ui" / "src"), str(ROOT), str(ROOT / "canvas-cairo")]

from ui.floor_transition import plan  # noqa: E402


def test_starts_and_ends_flat_on_the_canvas():
    assert plan(0.0)[:2] == (0.0, 0.0)
    tilt, travel, fade = plan(1.0)
    assert tilt == 0.0 and travel == 1.0 and abs(fade) < 1e-9


def test_travels_while_the_stack_is_tilted():
    tilt, travel, _ = plan(0.5)
    assert tilt == 1.0 and 0.0 < travel < 1.0
