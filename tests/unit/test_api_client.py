"""
Unit tests for maestri_core.client.api (MaestriClient & PTYWebSocketClient).
"""

import base64
import hashlib
import json
import os
import socket
import struct
import threading
from unittest.mock import MagicMock, patch
import pytest

from maestri_core.client.api import (
    AuthenticationError,
    EventsWebSocketClient,
    MaestriAPIError,
    MaestriClient,
    NotFoundError,
    PTYWebSocketClient,
)
from maestri_core.models.workspace import WorkspaceDocument, WorkspaceEntry, WorkspacePayload


# ============================================================================
# Client Configuration & Auth Tests
# ============================================================================

def test_client_token_and_port_resolution(tmp_path, monkeypatch):
    # 1. Direct parameters
    client = MaestriClient(host="127.0.0.1", port=45678, auth_token="explicit-token")
    assert client.port == 45678
    assert client.auth_token == "explicit-token"
    assert client.base_url == "http://127.0.0.1:45678"

    # 2. Environment variables
    monkeypatch.setenv("MAESTRI_AUTH_TOKEN", "env-token-xyz")
    monkeypatch.setenv("MAESTRI_SERVER_PORT", "54321")
    client_env = MaestriClient()
    assert client_env.port == 54321
    assert client_env.auth_token == "env-token-xyz"

    # 3. File fallback <data_dir>/run/auth.token (OPEN_MAESTRI_DATA_DIR, else ~/.open-maestri)
    monkeypatch.delenv("MAESTRI_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MAESTRI_SERVER_PORT", raising=False)
    data = tmp_path / "data"
    run_dir = data / "run"
    run_dir.mkdir(parents=True)
    token_file = run_dir / "auth.token"
    token_file.write_text("file-token-123", encoding="utf-8")
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(data))
    assert MaestriClient().auth_token == "file-token-123"

    # 4. Without the variable: ~/.open-maestri under the (patched) home
    monkeypatch.delenv("OPEN_MAESTRI_DATA_DIR")
    fake_home = tmp_path / "home"
    (fake_home / ".open-maestri" / "run").mkdir(parents=True)
    (fake_home / ".open-maestri" / "run" / "auth.token").write_text("home-token-456", encoding="utf-8")
    with patch("pathlib.Path.home", return_value=fake_home):
        assert MaestriClient().auth_token == "home-token-456"


def test_client_headers_contain_auth_and_origin():
    client = MaestriClient(port=39345, auth_token="secret-token-42")
    headers = client._headers({"Custom": "Value"})
    assert headers["Authorization"] == "Bearer secret-token-42"
    assert headers["Origin"] == "http://127.0.0.1:39345"
    assert headers["Host"] == "127.0.0.1:39345"
    assert headers["Custom"] == "Value"


# ============================================================================
# REST Endpoints Tests with Mocked Session
# ============================================================================

@pytest.fixture
def client():
    return MaestriClient(port=39345, auth_token="test-token")


def test_execute_cli(client):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.text = "terminal list: 2 active"

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        out = client.execute_cli(["list"], terminal_id="TERM-1")
        assert out == "terminal list: 2 active"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"] == {"args": ["list"]}
        assert call_kwargs["headers"]["X-Terminal-ID"] == "TERM-1"


def test_get_workspaces(client):
    raw_data = [
        {
            "id": "WS-1",
            "name": "Project Alpha",
            "workingDirectory": "/projects/alpha",
            "icon": "folder",
            "color": "blue",
            "isPinned": True,
            "locationType": "local",
            "createdAt": "2026-11-05T12:00:00Z",
        }
    ]
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = raw_data

    with patch.object(client.session, "get", return_value=mock_resp):
        workspaces = client.get_workspaces()
        assert len(workspaces) == 1
        assert isinstance(workspaces[0], WorkspaceEntry)
        assert workspaces[0].id == "WS-1"
        assert workspaces[0].name == "Project Alpha"
        assert workspaces[0].is_pinned is True


def test_create_workspace(client):
    created_raw = {
        "id": "WS-NEW",
        "name": "New Project",
        "workingDirectory": "/tmp/new",
        "icon": "terminal",
        "color": "green",
        "isPinned": False,
        "locationType": "local",
        "createdAt": "2026-11-05T12:00:00Z",
    }
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 201
    mock_resp.json.return_value = created_raw

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        ws = client.create_workspace(name="New Project", working_directory="/tmp/new", color="green", icon="terminal")
        assert ws.id == "WS-NEW"
        assert ws.name == "New Project"
        assert ws.color == "green"
        mock_post.assert_called_once()


def test_get_and_save_workspace(client):
    doc = WorkspaceDocument(schema_version=2, type="workspace", payload=WorkspacePayload(name="Active WS"))
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = doc.to_dict()

    with patch.object(client.session, "get", return_value=mock_resp):
        loaded = client.get_workspace(workspace_id="WS-123")
        assert loaded.schema_version == 2
        assert loaded.payload.name == "Active WS"

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        success = client.save_workspace(loaded, workspace_id="WS-123")
        assert success is True
        mock_post.assert_called_once()


def test_terminals_lifecycle(client):
    create_resp = MagicMock()
    create_resp.ok = True
    create_resp.status_code = 201
    create_resp.json.return_value = {"id": "TERM-88", "name": "Worker", "agentType": "generic_shell"}

    with patch.object(client.session, "post", return_value=create_resp):
        res = client.create_terminal(name="Worker", command="bash")
        assert res["id"] == "TERM-88"

    del_resp = MagicMock()
    del_resp.ok = True
    del_resp.status_code = 200
    with patch.object(client.session, "delete", return_value=del_resp):
        assert client.delete_terminal("TERM-88") is True


def test_notes_operations(client):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.text = "# Note Content\nHello World"

    with patch.object(client.session, "get", return_value=mock_resp):
        text = client.get_note("Plan")
        assert "Hello World" in text

    with patch.object(client.session, "put", return_value=mock_resp):
        assert client.put_note("Plan", "# New Content") is True

    with patch.object(client.session, "delete", return_value=mock_resp):
        assert client.delete_note("Plan") is True


def test_connections_operations(client):
    conn_raw = {
        "id": "CONN-1",
        "terminalIdA": "TA",
        "terminalIdB": "TB",
        "ropePoints": [[0, 0], [10, 10]],
        "createdAt": "2026-11-05T12:00:00Z",
    }
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = [conn_raw]

    with patch.object(client.session, "get", return_value=mock_resp):
        conns = client.get_connections()
        assert len(conns) == 1
        assert conns[0].id == "CONN-1"

    post_resp = MagicMock()
    post_resp.ok = True
    post_resp.status_code = 201
    post_resp.json.return_value = conn_raw
    with patch.object(client.session, "post", return_value=post_resp):
        created = client.create_connection("TA", "TB", "terminal")
        assert created.id == "CONN-1"


def test_files_and_git_status(client):
    files_data = [
        {"name": "src", "path": "src", "isDir": True},
        {"name": "README.md", "path": "README.md", "isDir": False, "gitStatus": "M"},
    ]
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = files_data

    with patch.object(client.session, "get", return_value=mock_resp):
        files = client.list_files(root="/repo", path="")
        assert len(files) == 2
        assert files[0]["isDir"] is True
        assert files[1]["gitStatus"] == "M"


def test_sidebar_layout_operations(client):
    layout = {"schemaVersion": 2, "topLevelItems": ["WS-1"], "groups": []}
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = layout

    with patch.object(client.session, "get", return_value=mock_resp):
        res = client.get_sidebar_layout()
        assert res["schemaVersion"] == 2

    with patch.object(client.session, "put", return_value=mock_resp):
        assert client.save_sidebar_layout(layout) is True


def test_error_handling(client):
    err_401 = MagicMock()
    err_401.ok = False
    err_401.status_code = 401
    err_401.text = "Unauthorized"
    with patch.object(client.session, "get", return_value=err_401):
        with pytest.raises(AuthenticationError):
            client.get_workspaces()

    err_404 = MagicMock()
    err_404.ok = False
    err_404.status_code = 404
    err_404.text = "Not Found"
    with patch.object(client.session, "get", return_value=err_404):
        with pytest.raises(NotFoundError):
            client.get_note("NonExistent")

    err_500 = MagicMock()
    err_500.ok = False
    err_500.status_code = 500
    err_500.text = "Internal Server Error"
    with patch.object(client.session, "get", return_value=err_500):
        with pytest.raises(MaestriAPIError):
            client.get_floors()


# ============================================================================
# RFC 6455 WebSocket Client Tests (PTY Streaming)
# ============================================================================

def test_pty_websocket_handshake_and_framing():
    """Starts a minimal test loopback server to verify RFC 6455 WebSocket handshake and framing."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _srv_worker():
        conn, _ = srv.accept()
        # Read handshake
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(1024)
            if not chunk:
                break
            req += chunk

        req_str = req.decode("utf-8")
        sec_key = ""
        for line in req_str.splitlines():
            if line.lower().startswith("sec-websocket-key:"):
                sec_key = line.split(":", 1)[1].strip()

        accept = base64.b64encode(
            hashlib.sha1((sec_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()
        ).decode("ascii")

        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("utf-8")
        conn.sendall(resp)

        # Server sends initial binary output frame
        payload = b"hello pty"
        frame = bytearray([0x82, len(payload)]) + payload
        conn.sendall(frame)

        # Read client frame (must be masked)
        client_frame = conn.recv(1024)
        assert len(client_frame) >= 6
        assert (client_frame[1] & 0x80) != 0  # mask bit set

        conn.close()
        srv.close()

    t = threading.Thread(target=_srv_worker, daemon=True)
    t.start()

    ws = PTYWebSocketClient(
        host="127.0.0.1",
        port=port,
        terminal_id="TERM-TEST-1",
        auth_token="token-abc",
        timeout=3.0,
    )
    ws.connect()
    assert ws.is_open is True

    # Receive binary frame from server
    frame = ws.receive_frame(timeout=2.0)
    assert frame is not None
    opcode, data = frame
    assert opcode == PTYWebSocketClient.OPCODE_BINARY
    assert data == b"hello pty"

    # Send resize command
    ws.resize(cols=120, rows=40)

    # Close connection
    ws.close()
    assert ws.is_open is False
    t.join(timeout=2.0)


def test_create_terminal_pins_id_and_display_name(client):
    resp = MagicMock(ok=True, status_code=200)
    resp.json.return_value = {"id": "AAAAAAAA-0000-0000-0000-000000000001"}
    with patch.object(client.session, "post", return_value=resp) as post:
        client.create_terminal(name="T", command="bash", terminal_id="AAAAAAAA-0000-0000-0000-000000000001", display_name="T")
    body = post.call_args.kwargs["json"]
    assert body["id"] == "AAAAAAAA-0000-0000-0000-000000000001" and body["displayName"] == "T"


def test_ensure_terminal_reuses_a_running_session(client):
    meta = MagicMock(ok=True, status_code=200)
    meta.json.return_value = {"id": "X", "running": True}
    with patch.object(client.session, "get", return_value=meta), patch.object(client.session, "post") as post:
        assert client.ensure_terminal("X") is False
    post.assert_not_called()   # POST would replace (kill) the live shell


@pytest.mark.parametrize("get_status, meta", [(404, None), (200, {"id": "X", "running": False})])
def test_ensure_terminal_creates_missing_or_exited_session(client, get_status, meta):
    got = MagicMock(ok=get_status == 200, status_code=get_status)
    got.json.return_value = meta
    created = MagicMock(ok=True, status_code=200)
    created.json.return_value = {"id": "X"}
    with patch.object(client.session, "get", return_value=got), patch.object(client.session, "post", return_value=created) as post:
        assert client.ensure_terminal("X", command="bash", working_directory="/tmp", workspace_id="W") is True
    body = post.call_args.kwargs["json"]
    assert body["id"] == "X" and body["workingDirectory"] == "/tmp" and body["workspaceId"] == "W"


def test_events_websocket_reconnect_and_glib_idle_add():
    """Verifies /ws/events client reconnects on disconnect and dispatches via GLib.idle_add."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    port = srv.getsockname()[1]

    def _accept_and_send(conn, event_dict):
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(1024)
            if not chunk:
                return
            req += chunk
        req_str = req.decode("utf-8")
        assert "GET /ws/events?workspace=WS-1 HTTP/1.1" in req_str
        sec_key = ""
        for line in req_str.splitlines():
            if line.lower().startswith("sec-websocket-key:"):
                sec_key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1((sec_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()
        ).decode("ascii")
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("utf-8")
        conn.sendall(resp)
        payload = json.dumps(event_dict).encode("utf-8")
        frame = bytearray([0x81, len(payload)]) + payload
        conn.sendall(frame)

    def _srv_worker():
        # First connection: sends workspaceSaved then drops immediately
        c1, _ = srv.accept()
        _accept_and_send(c1, {"type": "workspaceSaved", "workspaceId": "WS-1"})
        c1.close()
        # Second connection (after automatic reconnect): sends connectionChanged
        c2, _ = srv.accept()
        _accept_and_send(c2, {"type": "connectionChanged", "connectionId": "C-1", "ropePoints": [[1.0, 2.0]]})
        c2.close()
        srv.close()

    t = threading.Thread(target=_srv_worker, daemon=True)
    t.start()

    idle_calls = []
    received = []
    done = threading.Event()

    def fake_idle_add(cb, *args):
        idle_calls.append(cb)
        cb(*args)
        return 1

    def on_event(evt):
        received.append(evt)
        if len(received) >= 2:
            done.set()

    client = MaestriClient(host="127.0.0.1", port=port, auth_token="tok-123")
    ws = client.connect_events(
        on_event=on_event,
        workspace_id="WS-1",
        reconnect=True,
        reconnect_delay=0.05,
        use_glib_idle=True,
        idle_add=fake_idle_add,
    )
    assert done.wait(timeout=3.0), f"Timed out waiting for reconnect events, got: {received}"
    ws.close()
    t.join(timeout=2.0)

    assert len(idle_calls) == 2
    assert received[0]["type"] == "workspaceSaved"
    assert received[1]["type"] == "connectionChanged"
    assert ws.reconnect_count >= 1


def test_client_sprint4_endpoints(client):
    """Tests update_connection, patch_floor, delete_floor, and routines CRUD methods."""
    ok_resp = MagicMock(ok=True, status_code=200)
    ok_resp.json.return_value = {"status": "ok", "id": "R-1"}

    with patch.object(client.session, "put", return_value=ok_resp) as put:
        assert client.update_connection("C-1", [[0.0, 0.0], [10.0, 10.0]], workspace_id="W-1") is True
        assert "/api/connections/C-1?workspace=W-1" in put.call_args.args[0]

    with patch.object(client.session, "patch", return_value=ok_resp) as patch_fn:
        client.patch_floor("F-1", name="Renamed", hooks={"setup": ["make"]}, workspace_id="W-1")
        assert "/api/floors/F-1?workspace=W-1" in patch_fn.call_args.args[0]

    with patch.object(client.session, "delete", return_value=ok_resp) as del_fn:
        assert client.delete_floor("F-1", workspace_id="W-1", remove_worktree=False) is True
        assert "removeWorktree=0" in del_fn.call_args.args[0]

    list_resp = MagicMock(ok=True, status_code=200)
    list_resp.json.return_value = [{"id": "R-1", "name": "Routine 1"}]
    with patch.object(client.session, "get", return_value=list_resp):
        assert client.get_routines()[0]["id"] == "R-1"

    with patch.object(client.session, "post", return_value=ok_resp):
        assert client.create_routine("R", "echo 1", 60.0, "T-1")["id"] == "R-1"

    with patch.object(client.session, "patch", return_value=ok_resp):
        assert client.update_routine("R-1", isActive=False)["id"] == "R-1"

    with patch.object(client.session, "delete", return_value=ok_resp):
        assert client.delete_routine("R-1") is True



# ---- floors (Sprint 5 T2.2) ------------------------------------------------------------------------------------------
def _floor_resp(status=200, payload=None):
    r = MagicMock(ok=200 <= status < 300, status_code=status, text="error: nope")
    r.json.return_value = payload
    return r


def test_create_floor_sends_branch_options(client):
    with patch.object(client.session, "post", return_value=_floor_resp(201, {"id": "F"})) as post:
        client.create_floor("Fix", branch_name="feature/x", workspace_id="W", base_branch="dev")
        assert post.call_args.kwargs["json"] == {"name": "Fix", "branchName": "feature/x", "workspaceId": "W", "baseBranch": "dev"}
        client.create_floor("Fix", branch_name="feature/x", existing_branch=True)
        assert post.call_args.kwargs["json"]["existingBranch"] is True


def test_delete_floor_delete_branch_query(client):
    with patch.object(client.session, "delete", return_value=_floor_resp()) as delete:
        client.delete_floor("F", workspace_id="W", delete_branch=True)
        assert delete.call_args.args[0].endswith("/api/floors/F?workspace=W&deleteBranch=1")
        client.delete_floor("F")
        assert delete.call_args.args[0].endswith("/api/floors/F")


def test_floor_status_land_preview_and_land(client):
    with patch.object(client.session, "get", return_value=_floor_resp(200, {"dirty": True})) as get:
        assert client.floor_status("F", workspace_id="W") == {"dirty": True}
        assert get.call_args.args[0].endswith("/api/floors/F/status?workspace=W")
        client.land_preview("F", target="release/1 x", workspace_id="W")
        assert get.call_args.args[0].endswith("/api/floors/F/land-preview?target=release%2F1+x&workspace=W")
    with patch.object(client.session, "post", return_value=_floor_resp(200, {"status": "ok", "commit": "abc"})) as post:
        assert client.land_floor("F", "main", workspace_id="W")["commit"] == "abc"
        assert post.call_args.args[0].endswith("/api/floors/F/land?workspace=W")
        assert post.call_args.kwargs["json"] == {"target": "main"}
    with patch.object(client.session, "post", return_value=_floor_resp(409)):
        with pytest.raises(MaestriAPIError) as e:
            client.land_floor("F", "main")
        assert e.value.status_code == 409
