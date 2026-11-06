"""E2E (real OpenMaestriWindow, offline): View → Snap to Alignment Guides toggles the canvas guides and persists the choice."""
import sys
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.geometry import Point  # noqa: E402
from maestri_canvas.interaction import Mods, NodeHeader  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.preferences import PreferencesStore  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def pump(seconds=0.2):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.snapping", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    return a


def test_view_menu_toggle_and_drag_guides(app, tmp_path):
    win = OpenMaestriWindow(app, MaestriClient(port=0))
    win.prefs_store = PreferencesStore(tmp_path)          # never write the user's real preferences.json
    win.present()
    assert win.wait_loaded()
    pump(0.3)
    cw = win.canvas_widget
    assert cw.snapping is win.preferences.canvas_snapping
    win.lookup_action("snapping").change_state(GLib.Variant.new_boolean(True))
    assert cw.snapping and win.lookup_action("snapping").get_state().get_boolean()
    # two nodes; drag the second near the first's left edge: it is pulled onto it and a guide is shown mid-drag
    win._create_node("stickyNote", at=Point(10000, 8600))
    win._create_node("stickyNote", at=Point(10004, 9000))
    a, b = win.document.payload.nodes[-2:]
    ctl = cw.controller
    ctl.size = (float(cw.get_width() or 1200), float(cw.get_height() or 800))
    start = cw.viewport.to_screen(Point(b.frame.x + 20, b.frame.y + 10))
    ctl.press(start, 1, Mods.NONE, NodeHeader(b.id))
    ctl.move(Point(start.x + 1 * cw.viewport.zoom, start.y + 10), Mods.NONE)
    ctl.move(Point(start.x + 2 * cw.viewport.zoom, start.y + 20), Mods.NONE)
    assert cw.scene.get(b.id).frame.x == pytest.approx(a.frame.x)
    assert any(g.axis == "vertical" and g.position == pytest.approx(a.frame.x) for g in ctl.overlay.guides)
    ctl.release(Point(start.x + 2 * cw.viewport.zoom, start.y + 20), Mods.NONE)
    # toggle off from the menu: persisted, and no more pull
    win.lookup_action("snapping").change_state(GLib.Variant.new_boolean(False))
    assert not cw.snapping and PreferencesStore(tmp_path).load().canvas_snapping is False
    win.close()
    pump(0.05)
