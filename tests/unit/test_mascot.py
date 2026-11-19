import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "gtk4-ui" / "src"), str(ROOT / "canvas-cairo")]

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from maestri_core.preferences import Preferences  # noqa: E402
from ui.mascot import ASSETS, MascotController, finished_message, is_minimized  # noqa: E402


def test_frames_ship_as_transparent_pngs():
    assert len(list(ASSETS.glob("idle_*.png"))) >= 2 and len(list(ASSETS.glob("alert_*.png"))) >= 2
    tex = Gdk.Texture.new_from_filename(str(sorted(ASSETS.glob("idle_*.png"))[0]))
    assert tex.get_width() == tex.get_height() == 128


def test_minimized_or_suspended_counts_as_hidden():
    assert is_minimized(Gdk.ToplevelState.MINIMIZED)
    assert not is_minimized(Gdk.ToplevelState.FOCUSED)
    if hasattr(Gdk.ToplevelState, "SUSPENDED"):
        assert is_minimized(Gdk.ToplevelState.SUSPENDED)


def test_messages():
    assert finished_message(["RavenX"]) == "RavenX terminou a tarefa"
    assert finished_message(["A", "B", "C"]) == "A, B e C terminaram"


def test_preference_round_trip_and_default_off():
    assert Preferences().mascot_enabled is False
    assert Preferences.from_dict(Preferences(mascot_enabled=True).to_dict()).mascot_enabled is True


def test_controller_shows_only_when_enabled_and_minimized_and_speaks():
    win = Gtk.Window()
    ctl = MascotController(win, enabled=False)
    ctl._state_changed(Gdk.ToplevelState.MINIMIZED)
    assert ctl.mascot is None                      # disabled: nothing appears
    ctl.set_enabled(True)                          # enabling while minimized shows it
    assert ctl.mascot is not None and ctl.mascot.get_visible()
    ctl.agents_finished(["RavenX"])
    assert ctl.mascot.bubble.get_visible() and "RavenX" in ctl.mascot.bubble.get_label()
    ctl._state_changed(Gdk.ToplevelState.FOCUSED)  # restored: the mascot goes away
    assert not ctl.mascot.get_visible()
    ctl.destroy()
    win.destroy()
