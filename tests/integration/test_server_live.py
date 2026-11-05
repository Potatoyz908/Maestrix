"""
Live end-to-end integration test: Python MaestriClient against running open-maestri-server.
"""

from pathlib import Path
import re
import subprocess
import time
import pytest

from maestri_core.client.api import MaestriClient, PTYWebSocketClient


@pytest.fixture(scope="module")
def live_server():
    server_bin = Path(".build/release/open-maestri-server")
    if not server_bin.is_file():
        server_bin = Path(".build/x86_64-unknown-linux-gnu/release/open-maestri-server")
    if not server_bin.is_file():
        pytest.skip(f"Binary {server_bin} does not exist")

    proc = subprocess.Popen(
        [str(server_bin)],
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    port = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError(f"Server exited prematurely: code {proc.returncode}")
            time.sleep(0.05)
            continue

        match = re.search(r"POSIX TCP ready on 127\.0\.0\.1:(\d+)", line)
        if match:
            port = int(match.group(1))
            break

    if port is None:
        proc.kill()
        raise TimeoutError("Failed to detect server port within timeout")

    yield (port, proc)

    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_live_rest_and_pty_stream(live_server):
    port, proc = live_server
    client = MaestriClient(port=port)
    assert client.auth_token, "Client should auto-resolve token from ~/.open-maestri/run/auth.token"

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
