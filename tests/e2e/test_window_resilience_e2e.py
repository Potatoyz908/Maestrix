"""Sprint 6 (Opus-Reviewer audit) under Xvfb: WorkspaceSync survives an offline start and core drops (R1), _cleanup is
idempotent (R2), floors/routines release their bus handlers on destroy alone (R3), git-* statuses never paint the save
label (R4), and the Land error survives the preview reload (L2). Fake core client, no network; DISPLAY=:99 only."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires a display (Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.client.api import MaestriAPIError, MaestriClient  # noqa: E402
from maestri_core.signals.bus import bus  # noqa: E402
from ui.floors import LandFloorDialog  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402

FLOOR = {"id": "AAAAAAAA-0000-0000-0000-000000000001", "name": "Fix", "branchName": "floor/fix",
         "worktreePath": "/tmp/r/.open-maestri/floors/fix", "hooks": {}}


def pump_until(cond, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class FlakyCore(MaestriClient):
    """A core whose reachability and save result the test flips; records saves."""

    def __init__(self, status="offline"):
        super().__init__(port=1, auth_token="t")
        self.status = status
        self.save_ok = True
        self.saved = []
        self.land_fails = False
        self.lock = threading.Lock()

    def check_connection_status(self):
        return self.status

    def get_workspace(self, workspace_id=None):
        return None   # the window falls back to its local document

    def save_workspace_dict(self, data, workspace_id=None):
        with self.lock:
            self.saved.append(workspace_id)
        return self.save_ok

    def connect_events(self, on_event=None, workspace_id=None, **_kw):
        return SimpleNamespace(close=lambda: None)

    def get_floors(self, workspace_id=None):
        return [dict(FLOOR)]

    def floor_status(self, floor_id, workspace_id=None):
        return {"dirty": False, "ahead": 2, "insertions": 1, "deletions": 0}

    def land_preview(self, floor_id, target=None, workspace_id=None):
        target = target or "dev"
        return {"floorBranch": "floor/fix", "target": target, "branches": ["dev", "floor/fix"], "ahead": 2,
                "fastForward": True, "insertions": 1, "deletions": 0, "conflicts": [], "dirty": False,
                "canLand": target == "dev"}

    def land_floor(self, floor_id, target, workspace_id=None):
        if self.land_fails:
            raise MaestriAPIError("HTTP 409", status_code=409, response_body="error: dev moved: update-ref lost the race")
        return {"status": "ok", "commit": "abc", "target": target}

    # routines (install_routines refreshes on load)
    def get_routines(self):
        return []


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.resilience", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def make_win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    made = []

    def make(core):
        w = OpenMaestriWindow(app, core)
        w.present()
        assert w.wait_loaded()
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()
    pump_until(lambda: False, 0.05)


def probe_now(w):
    """Run one tick of the reconnect probe instead of waiting for its 5 s timer."""
    w._core_link._tick()


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestWindowResilience:
    def test_offline_start_then_core_appears_enables_sync_and_pushes(self, make_win):
        core = FlakyCore("offline")
        w = make_win(core)
        assert w.sync is None and not w.core_connected and "Standalone" in w.lbl_status.get_label()
        assert w._core_link.running, "offline at boot keeps probing (sync not disabled for good)"
        assert not w.floor_button.get_sensitive()

        core.status = "connected"
        probe_now(w)
        assert pump_until(lambda: w.core_connected and w.sync is not None)
        assert "Porta" in w.lbl_status.get_label() and not w._core_link.running
        assert pump_until(lambda: w.document.payload.id in core.saved), "reconnect flushes the offline document"
        assert pump_until(lambda: w.floor_button.get_sensitive() and len(w.floors.floors) == 1)

    def test_save_failure_with_core_gone_keeps_standalone_label(self, make_win):
        core = FlakyCore("connected")
        w = make_win(core)
        assert w.core_connected and w.sync is not None
        core.status, core.save_ok = "offline", False
        bus.emit("status-changed", "save-failed", False)
        assert pump_until(lambda: not w.core_connected)
        assert "Standalone" in w.lbl_status.get_label() and "Falha" not in w.lbl_status.get_label()
        assert w._core_link.running
        bus.emit("status-changed", "save-failed", False)   # later failures while offline do not repaint the label
        pump_until(lambda: False, 0.2)
        assert "Standalone" in w.lbl_status.get_label()

        core.status, core.save_ok = "connected", True
        probe_now(w)
        assert pump_until(lambda: w.core_connected and "Porta" in w.lbl_status.get_label())

    def test_save_failure_with_core_up_shows_falha_ao_salvar(self, make_win):
        core = FlakyCore("connected")
        w = make_win(core)
        bus.emit("status-changed", "save-failed", False)
        assert pump_until(lambda: "Falha ao Salvar" in w.lbl_status.get_label())
        assert w.core_connected and not w._core_link.running

    def test_git_statuses_never_touch_the_save_label(self, make_win):
        w = make_win(FlakyCore("connected"))
        before = w.lbl_status.get_label()
        bus.emit("status-changed", "git-commit", False)
        bus.emit("status-changed", "git-push", True)
        pump_until(lambda: False, 0.2)
        assert w.lbl_status.get_label() == before

        off = make_win(FlakyCore("offline"))
        bus.emit("status-changed", "git-pull", False)
        pump_until(lambda: False, 0.1)
        assert "Standalone" in off.lbl_status.get_label()

    def test_cleanup_is_idempotent(self, make_win):
        w = make_win(FlakyCore("connected"))
        closes = []
        real_close = w.sync.close
        w.sync.close = lambda: (closes.append(1), real_close())
        w._cleanup()
        w._cleanup()
        w._on_destroy()
        assert closes == [1] and w._cleaned_up and w.sync is None

    def test_destroy_alone_releases_floor_and_routine_bus_handlers(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        w = OpenMaestriWindow(app, FlakyCore("connected"))
        w.present()
        assert w.wait_loaded()
        refreshed = []
        w.floors.refresh = lambda: refreshed.append(1)
        # GTK 4: Window.destroy() only drops GTK's own reference; the "destroy" signal fires at dispose, which a live
        # Python reference would postpone. run_dispose() is what finally happens when the last reference goes.
        w.destroy()                       # no close-request
        w.run_dispose()
        pump_until(lambda: False, 0.1)
        bus.emit("workspace-loaded", w.document.payload)
        bus.emit("node-added", None)
        pump_until(lambda: False, 0.1)
        assert refreshed == [], "floors handler disconnected on destroy"
        assert w._cleaned_up

    def test_land_error_survives_preview_reload(self, make_win):
        core = FlakyCore("connected")
        w = make_win(core)
        assert pump_until(lambda: len(w.floors.floors) == 1)
        dlg = w.floor_button.open_land(w.floors.floors[0])
        assert isinstance(dlg, LandFloorDialog)
        assert pump_until(lambda: dlg.preview_data is not None)   # default target: the floor's own branch
        dlg.target.set_selected(dlg._branches.index("dev"))
        assert pump_until(lambda: dlg.preview_data.get("target") == "dev" and dlg.merge_btn.get_sensitive())
        core.land_fails = True
        dlg.submit()
        assert pump_until(lambda: dlg.land_error is not None)
        reloads = dlg._request
        assert pump_until(lambda: not dlg.spinner.get_visible() and dlg._request == reloads)
        pump_until(lambda: False, 0.3)   # let the reload land
        assert dlg.reason.get_visible() and "update-ref lost the race" in dlg.reason.get_label()
        assert dlg.merge_btn.get_sensitive(), "the user can retry"
        dlg.close()

        delete = w.floor_button.open_delete(w.floors.floors[0])
        assert "DESCARTA" in delete.alert.get_detail()
