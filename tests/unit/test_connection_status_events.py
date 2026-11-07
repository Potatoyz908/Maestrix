"""connectionChanged {status}: the open canvas's rope gets the status (no document change, no echo); old ropePoints-only
events keep emitting connection-changed."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "canvas-cairo"), str(ROOT / "gtk4-ui" / "src"), str(ROOT)]

from maestri_canvas.scene import ConnItem, Scene  # noqa: E402
from maestri_core.client.api import EventsWebSocketClient  # noqa: E402
from maestri_core.models.workspace import WorkspacePayload  # noqa: E402
from ui.live_events import LiveEvents  # noqa: E402


def _window():
    scene = Scene()
    scene.add_connection(ConnItem("C1", "A", "B"))
    statuses = []
    cw = NS(scene=scene, controller=NS(dirty=False), queue_draw=Mock(), frames={},
            set_connection_status=lambda cid, st: statuses.append((cid, st)))
    win = NS(document=NS(payload=WorkspacePayload(id="11111111-1111-1111-1111-111111111111", nodes=[])),
             canvas_widget=cw, loaded=True, client=Mock(), sync=Mock())
    return win, statuses


def test_status_reaches_the_rope_of_the_open_workspace_only():
    win, statuses = _window()
    live = LiveEvents(win)
    ws = win.document.payload.id
    assert live.handle("connectionChanged", {"workspaceId": ws, "connectionId": "c1", "status": "communicating"})
    assert not live.handle("connectionChanged", {"workspaceId": "OTHER", "connectionId": "C1", "status": "idle"})
    assert not live.handle("connectionChanged", {"workspaceId": ws, "connectionId": "C1", "ropePoints": [[0, 0]]})
    assert not live.handle("connectionChanged", {"workspaceId": ws, "connectionId": "NOPE", "status": "idle"})
    assert statuses == [("C1", "communicating")]
    assert win.client.mock_calls == [] and win.sync.mock_calls == []   # no re-post, no save


def test_forward_to_bus_status_and_legacy_rope_points():
    bus = Mock()
    c = EventsWebSocketClient.__new__(EventsWebSocketClient)
    c.signal_bus = bus
    c._forward_to_bus({"type": "connectionChanged", "connectionId": "C1", "status": "communicating"})
    c._forward_to_bus({"type": "connectionChanged", "connectionId": "C2", "ropePoints": [[1, 2]]})
    assert [call.args for call in bus.emit.call_args_list] == [
        ("connection-status-changed", "C1", "communicating"), ("connection-changed", "C2", "[[1, 2]]")]
