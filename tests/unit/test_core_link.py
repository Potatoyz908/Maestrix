"""CoreReconnector (Sprint 6 R1): periodic core probe on a worker thread, results on the main loop, silent after dispose."""
import sys
import time
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402

from ui.core_link import CoreReconnector  # noqa: E402


def pump(seconds):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        while ctx.iteration(False):
            pass
        time.sleep(0.005)


class Client:
    def __init__(self, status="offline"):
        self.status, self.calls = status, 0

    def check_connection_status(self):
        self.calls += 1
        if self.status == "raise":
            raise OSError("refused")
        return self.status


def test_probe_reports_on_main_loop_and_maps_errors_to_offline():
    got = []
    link = CoreReconnector(Client("connected"), lambda s: None)
    link.probe(got.append)
    pump(0.2)
    assert got == ["connected"]
    CoreReconnector(Client("raise"), lambda s: None).probe(got.append)
    pump(0.2)
    assert got == ["connected", "offline"]


def test_start_ticks_until_stop_and_is_idempotent():
    seen, client = [], Client("offline")
    link = CoreReconnector(client, seen.append, interval_s=1)
    link.start()
    first = link._timer
    link.start()
    assert link._timer == first and link.running
    pump(2.3)
    assert seen and set(seen) == {"offline"}
    link.stop()
    assert not link.running
    n = len(seen)
    pump(1.3)
    assert len(seen) == n


def test_dispose_drops_pending_results():
    got = []
    link = CoreReconnector(Client("connected"), lambda s: None)
    link.probe(got.append)
    link.dispose()
    pump(0.2)
    assert got == [] and not link.running
    link.start()
    assert not link.running, "a disposed link never restarts"
