"""Regression: the core dying while the app is open left Floors (Ground) disabled and every click did nothing. With the launcher's
OPEN_MAESTRI_SERVER_BIN the app starts the core again and reconnects. Needs a display (Xvfb)."""
import os, sys, time
from pathlib import Path
import pytest
REPO = Path(__file__).resolve().parents[2]
for p in (REPO/"canvas-cairo", REPO/"gtk4-ui"/"src", REPO, REPO/"tests"): sys.path.insert(0, str(p))
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, GLib
from data_dir_isolation import start_isolated_server, server_binary
from maestri_core.client.api import MaestriClient
from ui.window import OpenMaestriWindow


def pump_until(pred, secs):
    ctx = GLib.MainContext.default(); end = time.monotonic() + secs
    while time.monotonic() < end:
        while ctx.pending(): ctx.iteration(False)
        if pred(): return True
        time.sleep(0.02)
    return False


def test_app_restarts_a_dead_core_and_floors_come_back(tmp_path, monkeypatch):
    binary = server_binary()
    if binary is None:
        pytest.skip("server not built")
    with start_isolated_server(tmp_path) as srv:
        monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(srv["data_dir"]))
        monkeypatch.setenv("OPEN_MAESTRI_SERVER_BIN", str(binary))
        monkeypatch.setenv("HOME", str(tmp_path))
        client = MaestriClient()          # discovers the port from run/server.port
        app = Gtk.Application(application_id="x.core%d" % os.getpid(), flags=Gio.ApplicationFlags.NON_UNIQUE); app.register(None)
        win = OpenMaestriWindow(app, client); win.present(); assert win.wait_loaded(timeout=10)
        assert win.core_connected and win.floor_button.get_sensitive()
        srv["proc"].kill(); srv["proc"].wait()                    # the core dies under the open app
        assert pump_until(lambda: client.check_connection_status() == "connected" and win.core_connected
                          and win.floor_button.get_sensitive(), 90), "the app did not bring the core back"
