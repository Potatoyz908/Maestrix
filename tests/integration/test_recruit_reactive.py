"""K8/K2 against a real, isolated open-maestri-server: `recruit` from a Maestro terminal adds the recruit's node BELOW
the Maestro (y = maestro.y + h + 48) and the Maestro↔recruit connection to workspace.json, raises terminalCount, and
publishes nodeAdded/connectionCreated for the GUI (live_events.py)."""
import json
import time
import uuid

from maestri_core.client.api import MaestriClient
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent
from tests.data_dir_isolation import start_isolated_server, stop_server


def test_recruit_adds_node_below_maestro_and_connection(tmp_path):
    srv = start_isolated_server(tmp_path / "home")
    try:
        client = MaestriClient(port=srv["port"], auth_token=srv["token"])
        doc = client.get_workspace()
        ws_id = doc.payload.id
        mid = str(uuid.uuid4()).upper()
        x, y, w, h = 9800.0, 8600.0, 420.0, 300.0
        doc.payload.nodes = [CanvasNode(id=mid, frame=NodeFrame(x=x, y=y, width=w, height=h), node_type="terminal",
                                        content=TerminalContent(id=mid, terminal_id=mid, name="Maestro", command="bash",
                                                                is_manager=True))]
        assert client.save_workspace(doc)
        client.create_terminal(name="Maestro", command="bash", workspace_id=ws_id, terminal_id=mid, display_name="Maestro")

        events = []
        ws = client.connect_events(on_event=events.append, workspace_id=ws_id, use_glib_idle=False)
        try:
            time.sleep(0.15)
            r = client.session.post(f"{client.base_url}/cli", json={"args": ["recruit", "Scout", "--command", "bash"]},
                                    headers=client._headers({"Content-Type": "application/json", "X-Terminal-ID": mid}),
                                    timeout=30)
            assert r.status_code == 200 and not r.text.lower().startswith("error"), r.text

            saved = json.loads((srv["data_dir"] / "workspaces" / ws_id / "workspace.json").read_text())["payload"]
            recruits = [n for n in saved["nodes"] if n["id"] != mid and "terminal" in n["content"]]
            assert len(recruits) == 1, saved["nodes"]
            rec = recruits[0]
            (rx, ry), (rw, rh) = rec["frame"]
            assert (rx, ry) == (x, y + h + 48), rec["frame"]
            rid = rec["id"]
            assert rec["content"]["terminal"]["_0"]["id"] == rid, "K1: recruit node id == terminal id"
            assert any({c["terminalIdA"], c["terminalIdB"]} == {mid, rid} for c in saved["connections"]), saved["connections"]

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (
                    any(e.get("type") == "nodeAdded" and e.get("nodeId") == rid for e in events)
                    and any(e.get("type") == "connectionCreated" for e in events)):
                time.sleep(0.05)
            added = [e for e in events if e.get("type") == "nodeAdded" and e.get("nodeId") == rid]
            assert added, events
            assert added[0]["terminalCount"] == 2, added[0]
            assert any(e.get("type") == "connectionCreated" for e in events), events
        finally:
            ws.close()
    finally:
        stop_server(srv["proc"])
