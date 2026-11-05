"""WorkspaceSync: bus signals -> debounced snapshot -> client.save_workspace_dict (schemaVersion 2)."""
import time
from unittest.mock import patch

import pytest

gi = pytest.importorskip("gi")
from gi.repository import GLib

from maestri_core.client.api import MaestriClient, SCHEMA_VERSION
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.signals.bus import SignalBus
from maestri_core.sync import WorkspaceSync


class FakeClient:
    def __init__(self, ok=True):
        self.saved = []
        self.ok = ok

    def save_workspace_dict(self, data, workspace_id=None):
        self.saved.append((workspace_id, data))
        return self.ok


def pump(ms):
    ctx = GLib.MainContext.default()
    end = time.time() + ms / 1000.0
    while time.time() < end:
        while ctx.iteration(False):
            pass
        time.sleep(0.005)


@pytest.fixture
def setup():
    node = CanvasNode(frame=NodeFrame(x=10, y=20, width=300, height=200), node_type="terminal",
                      content=TerminalContent(name="T", command="bash"))
    doc = WorkspaceDocument(payload=WorkspacePayload(name="W", nodes=[node]))
    bus = SignalBus()
    client = FakeClient()
    vp = {"v": (9800.0, 8500.0, 1.0)}
    sync = WorkspaceSync(client, lambda: doc, lambda: vp["v"], signal_bus=bus, debounce_ms=50)
    yield doc, node, bus, client, sync, vp
    sync.close()


def test_burst_of_moves_is_debounced_to_one_save(setup):
    doc, node, bus, client, sync, _ = setup
    for i in range(20):
        node.frame.x = 100 + i
        bus.emit("node-moved", node.id, node.frame.x, node.frame.y)
    pump(300)
    sync.wait_idle()
    assert len(client.saved) == 1
    wid, data = client.saved[0]
    assert wid == doc.payload.id
    assert data["schemaVersion"] == 2 == SCHEMA_VERSION
    assert data["payload"]["nodes"][0]["frame"] == [[119.0, 20.0], [300.0, 200.0]]


def test_resize_and_viewport_are_persisted(setup):
    doc, node, bus, client, sync, vp = setup
    node.frame.width = 640.0
    vp["v"] = (1.0, 2.0, 1.5)
    bus.emit("node-resized", node.id, node.frame.x, node.frame.y, 640.0, 200.0)
    pump(200)
    sync.wait_idle()
    data = client.saved[-1][1]
    assert data["payload"]["nodes"][0]["frame"][1][0] == 640.0
    assert data["payload"]["canvasOrigin"] == [1.0, 2.0]
    assert data["payload"]["canvasZoom"] == 1.5


def test_flush_saves_immediately_and_forces_schema_v2(setup):
    doc, _, _, client, sync, _ = setup
    doc.schema_version = 1
    sync.flush()
    sync.wait_idle()
    assert client.saved[0][1]["schemaVersion"] == 2


def test_failed_save_emits_status_changed():
    doc = WorkspaceDocument()
    bus = SignalBus()
    seen = []
    bus.connect("status-changed", lambda _b, msg, ok: seen.append((msg, ok)))
    sync = WorkspaceSync(FakeClient(ok=False), lambda: doc, signal_bus=bus, debounce_ms=10)
    sync.flush()
    sync.wait_idle()
    pump(100)
    assert seen == [("save-failed", False)]


def test_save_workspace_dict_forces_schema_and_hits_rest():
    client = MaestriClient(port=1, auth_token="t")
    with patch.object(client.session, "post") as post:
        post.return_value.status_code = 200
        post.return_value.text = "{}"
        assert client.save_workspace_dict({"schemaVersion": 1, "payload": {}}, "ABC")
    assert post.call_args.kwargs["json"]["schemaVersion"] == 2
    assert "id=ABC" in post.call_args.args[0]


def test_get_workspace_rejects_other_schema_versions():
    client = MaestriClient(port=1, auth_token="t")
    with patch.object(client.session, "get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {"schemaVersion": 1, "payload": {}}
        assert client.get_workspace() is None
        get.return_value.json.return_value = WorkspaceDocument().to_dict()
        assert client.get_workspace() is not None
