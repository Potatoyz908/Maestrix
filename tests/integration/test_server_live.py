"""
Live end-to-end integration test: Python MaestriClient against running open-maestri-server.
"""

import time
import pytest

from maestri_core.client.api import MaestriClient, PTYWebSocketClient
from tests import data_dir_isolation as isolation


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Our OWN core on a throwaway data dir (never the user's ~/.open-maestri); skipped when the binary is not built."""
    server = isolation.start_isolated_server(tmp_path_factory.mktemp("home"))
    try:
        yield server
    finally:
        isolation.stop_server(server["proc"])


def test_live_rest_and_pty_stream(live_server, monkeypatch):
    port = live_server["port"]
    monkeypatch.delenv("MAESTRI_AUTH_TOKEN", raising=False)
    monkeypatch.setenv(isolation.DATA_DIR_ENV, str(live_server["data_dir"]))
    client = MaestriClient(port=port)
    assert client.auth_token == live_server["token"], "Client should auto-resolve token from <data_dir>/run/auth.token"

    # 1. Test Workspaces API
    workspaces = client.get_workspaces()
    assert isinstance(workspaces, list)

    # 2. Test Terminal Creation
    term_res = client.create_terminal(name="LiveIntegrationTest", command="bash")
    assert "id" in term_res
    terminal_id = term_res["id"]
    time.sleep(0.2)

    # 3. Test WebSocket PTY Connection
    pty = client.connect_pty(terminal_id)
    assert pty.is_open is True

    # 4. Test Resize Window
    pty.resize(cols=100, rows=30)

    # 5. Send shell input command and receive output
    pty.send_raw(b"echo OPEN_MAESTRI_LIVE_OK\n")

    received_output = bytearray()
    deadline = time.time() + 6.0
    while time.time() < deadline:
        frame = pty.receive_frame(timeout=0.5)
        if frame:
            opcode, data = frame
            if opcode in (PTYWebSocketClient.OPCODE_BINARY, PTYWebSocketClient.OPCODE_TEXT):
                received_output.extend(data)
                if b"OPEN_MAESTRI_LIVE_OK" in received_output:
                    break

    # 6. Cleanup
    pty.close()
    assert pty.is_open is False

    del_ok = client.delete_terminal(terminal_id)
    assert del_ok is True
    assert b"OPEN_MAESTRI_LIVE_OK" in received_output
