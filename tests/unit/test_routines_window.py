"""RoutinesWindow / RoutineEditor / RoutinesController / TerminalBridge under a real GTK display (skipped without one)."""
import sys
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gtk4-ui" / "src"))
from maestri_core.routines import Routine  # noqa: E402
from ui.routines_window import RoutineEditor, RoutinesController, RoutinesWindow, TerminalBridge  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

TID = "AAAAAAAA-0000-4000-8000-000000000001"
NODE = "BBBBBBBB-0000-4000-8000-000000000002"


def pump(cond=lambda: False, timeout=3.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.003)
    return cond()


class Store:
    """In-memory stand-in for RoutinesClient (same methods, blocking)."""

    def __init__(self):
        self.items = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise RuntimeError("core unreachable")

    def list(self):
        self._check()
        return list(self.items.values())

    def create(self, r):
        self._check()
        self.items[r.id] = r
        return r

    def update(self, r):
        self._check()
        self.items[r.id] = r
        return r

    def set_active(self, rid, active):
        self._check()
        self.items[rid].is_active = active
        return self.items[rid]

    def delete(self, rid):
        self._check()
        self.items.pop(rid)


class Transport:
    def __init__(self):
        self.written = []

    def write(self, s):
        self.written.append(s)


class FakeTerminalView:
    def __init__(self):
        self.transport = Transport()


@pytest.fixture(autouse=True)
def windows():
    """Every toplevel a test opens is presented, then destroyed with the queue drained: closing windows faster than the
    Wayland compositor acknowledges them can crash GTK's Wayland backend (event for an already destroyed surface)."""
    before = set(Gtk.Window.list_toplevels())
    yield
    for w in Gtk.Window.list_toplevels():
        if w not in before:
            w.destroy()
    pump(timeout=0.3)


@pytest.fixture
def env():
    view = FakeTerminalView()
    terminals = lambda: [(TID, "Claude", NODE)]  # noqa: E731
    bridge = TerminalBridge(terminals, lambda nid: view if nid == NODE else None)
    store = Store()
    ctl = RoutinesController(store, bridge, start_driver=False)
    yield ctl, store, view, terminals
    ctl.stop()


def test_bridge_types_prompt_then_enter_and_reports_missing_terminal(env):
    ctl, _store, view, _t = env
    assert ctl.bridge.send(TID, "run the tests")
    assert view.transport.written == ["run the tests"]
    assert pump(lambda: view.transport.written == ["run the tests", "\r"])
    assert ctl.bridge.send(NODE, "by node id")                # node id resolves too
    assert not ctl.bridge.send("CCCCCCCC-0000-4000-8000-000000000003", "x")
    assert not ctl.bridge.is_idle(TID)                         # just written to: busy until output settles


def test_editor_validates_and_builds_routine(env):
    ctl, _s, _v, terminals = env
    saved = []
    ed = RoutineEditor(None, terminals(), None, lambda r, new: saved.append((r, new)))
    ed.present()
    assert not ed.save_button.get_sensitive()
    ed.name_entry.set_text("Continuous testing")
    ed.prompt_buffer.set_text("pull\n&&\nrun make && make test", -1)
    ed.interval_spin.set_value(2)
    ed.unit_dropdown.set_selected(1)                           # hours
    assert ed.save_button.get_sensitive()
    ed.save_button.emit("clicked")
    (r, new), = saved
    assert new and r.name == "Continuous testing" and r.prompts == ["pull", "run make && make test"]
    assert r.interval_seconds == 7200 and r.target_terminal_id == TID


def test_editor_without_terminals_cannot_save():
    ed = RoutineEditor(None, [], None, lambda *_: None)
    ed.present()
    ed.name_entry.set_text("x")
    ed.prompt_buffer.set_text("y", -1)
    assert not ed.save_button.get_sensitive() and "terminal" in ed.error_label.get_label()


def test_editor_prefills_existing_routine(env):
    _c, _s, _v, terminals = env
    r = Routine.create("Review", "check commits\n&&\nleave feedback", 1800, TID)
    ed = RoutineEditor(None, terminals(), r, lambda *_: None)
    ed.present()
    assert ed.name_entry.get_text() == "Review" and ed.prompt_text() == "check commits\n&&\nleave feedback"
    assert ed.interval_spin.get_value_as_int() == 30 and ed.unit_dropdown.get_selected() == 0
    built = ed.build()
    assert built.id == r.id and built.created_at == r.created_at


def test_window_lists_toggles_runs_and_deletes(env):
    ctl, store, view, terminals = env
    r = Routine.create("Tests", "run tests", 300, TID)
    store.items[r.id] = r
    win = RoutinesWindow(None, ctl, terminals)
    win.present()
    assert pump(lambda: len(win.rows()) == 1)
    row = win.rows()[0]
    assert row.dot.has_css_class("active") and row.switch.get_active()

    row.switch.set_active(False)                               # pause -> REST -> list rebuilt
    assert pump(lambda: not store.items[r.id].is_active and not win.rows()[0].switch.get_active())
    assert not ctl.scheduler.routines[r.id].is_active

    assert ctl.run_now(r.id)                                   # immediate run types into the terminal
    assert view.transport.written[0] == "run tests"

    ctl.delete(r.id)
    assert pump(lambda: not win.rows() and not store.items)


def test_running_routine_shows_pulsing_indicator(env):
    ctl, store, _view, terminals = env
    r = Routine.create("Chain", "one\n&&\ntwo", 300, TID)
    store.items[r.id] = r
    win = RoutinesWindow(None, ctl, terminals)
    win.present()
    assert pump(lambda: len(win.rows()) == 1)
    ctl.run_now(r.id)                                          # waits on idle before "two": still running
    assert ctl.is_running(r.id)
    assert pump(lambda: win.rows()[0].dot.has_css_class("running"))


def test_save_new_routine_through_window(env):
    ctl, store, _v, terminals = env
    win = RoutinesWindow(None, ctl, terminals)
    win.present()
    ed = win.open_editor(None)
    ed.name_entry.set_text("Ping")
    ed.prompt_buffer.set_text("ping", -1)
    ed.save_button.emit("clicked")
    assert pump(lambda: len(win.rows()) == 1 and len(store.items) == 1)
    assert list(ctl.scheduler.routines) == list(store.items)


def test_core_errors_are_shown(env):
    ctl, store, _v, terminals = env
    store.fail = True
    win = RoutinesWindow(None, ctl, terminals)
    win.present()
    assert pump(lambda: win.error_label.get_visible())
    assert "core unreachable" in win.error_label.get_label()


def test_offline_controller_reports_unavailable():
    ctl = RoutinesController(None, TerminalBridge(lambda: [], lambda _n: None), start_driver=False)
    ctl.refresh()
    assert "offline" in ctl.error
