"""Regression: leaving a workspace while the app is offline threw away everything created in it.

With no server there is no WorkspaceSync, so nothing wrote the open workspace to disk. Switching to another workspace
(or closing the app) discarded every node created since it was loaded; coming back showed an empty canvas and
workspace.json on disk still had zero nodes. This is what a user sees after a stale agent.sock makes the launcher skip
starting the server.

Needs a display: run under Xvfb (DISPLAY=:99). No server is started at all.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "canvas-cairo", REPO / "gtk4-ui" / "src", REPO):
    sys.path.insert(0, str(p))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs a display (run under Xvfb)")


def _pump(ms: int) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


def _wait(pred, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump(30)
        if pred():
            return True
    return bool(pred())


def _disk_node_count(data_dir: Path, ws_id: str) -> int:
    path = data_dir / "workspaces" / ws_id.upper() / "workspace.json"
    return len(json.loads(path.read_text(encoding="utf-8"))["payload"]["nodes"])


def test_nodes_survive_leaving_and_returning_while_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maestri_canvas.widget import load_vte
    if load_vte() is None:
        pytest.skip("Vte 3.91 typelib is not available")

    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(data_dir))
    from maestri_core.client.api import MaestriClient
    from maestri_core.models.node import NodeFrame
    from ui.window import OpenMaestriWindow
    from ui.workspace_dialogs import create_workspace_record

    client = MaestriClient(port=1, auth_token="offline", timeout=0.5)      # nothing listens on port 1: fully offline
    alpha = create_workspace_record(client, "Alpha", str(tmp_path))        # offline fallback writes manifest + workspace.json
    beta = create_workspace_record(client, "Beta", str(tmp_path))

    app = Gtk.Application(application_id="com.openmaestri.regression.offline", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    win = OpenMaestriWindow(app, client)
    win.present()
    assert win.wait_loaded(timeout=12.0)
    assert not win.core_connected, "the test must run offline"

    def switch_to(ws_id: str) -> None:
        win._on_workspace_switched(None, ws_id)
        assert _wait(lambda: win.document.payload.id.upper() == ws_id.upper()), f"did not switch to {ws_id}"
        _pump(300)

    switch_to(alpha)
    for i in range(2):
        node = win.new_terminal_node(NodeFrame(x=9900 + i * 480, y=8600, width=440, height=300),
                                     name=f"Terminal {i + 1}", command="bash", cwd=str(tmp_path))
        win._insert_node(node)
    _pump(500)
    assert len(win.document.payload.nodes) == 2

    switch_to(beta)                                                        # leave Alpha
    assert len(win.document.payload.nodes) == 0
    assert _disk_node_count(data_dir, alpha) == 2, "leaving Alpha offline must have written its nodes to disk"

    switch_to(alpha)                                                       # come back
    assert len(win.document.payload.nodes) == 2, "Alpha came back empty"
    assert len(win.canvas_widget.scene.nodes) == 2, "the canvas of Alpha came back empty"


def test_closing_the_window_offline_saves_the_open_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maestri_canvas.widget import load_vte
    if load_vte() is None:
        pytest.skip("Vte 3.91 typelib is not available")

    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(data_dir))
    from maestri_core.client.api import MaestriClient
    from maestri_core.models.node import NodeFrame
    from ui.window import OpenMaestriWindow
    from ui.workspace_dialogs import create_workspace_record

    client = MaestriClient(port=1, auth_token="offline", timeout=0.5)
    ws = create_workspace_record(client, "Solo", str(tmp_path))
    app = Gtk.Application(application_id="com.openmaestri.regression.offline.close", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    win = OpenMaestriWindow(app, client)
    win.present()
    assert win.wait_loaded(timeout=12.0)
    win._on_workspace_switched(None, ws)
    assert _wait(lambda: win.document.payload.id.upper() == ws.upper())
    _pump(300)
    win._insert_node(win.new_terminal_node(NodeFrame(x=9900, y=8600, width=440, height=300),
                                           name="T", command="bash", cwd=str(tmp_path)))
    _pump(300)

    win._on_close_request()                                                # what closing the window runs

    assert _disk_node_count(data_dir, ws) == 1, "closing offline must persist the open workspace"
