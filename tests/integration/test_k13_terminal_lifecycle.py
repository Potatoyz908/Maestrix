"""K13 against a real, isolated open-maestri-server: restart keeps the terminal id, and the scrollback written to
<dataDir>/workspaces/<ws>/terminals/<id>.scrollback on server shutdown comes back after the SERVER restarts."""
import time
import uuid

import pytest

from maestri_core.client.api import MaestriClient
from tests.data_dir_isolation import start_isolated_server, stop_server


def _read_until(pty, needle: bytes, timeout: float = 6.0) -> bytes:
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and needle not in buf:
        frame = pty.receive_frame(timeout=0.3)
        if frame:
            buf.extend(frame[1])
    return bytes(buf)


def test_restart_keeps_id_and_scrollback_survives_server_restart(tmp_path):
    home = tmp_path / "home"
    srv = start_isolated_server(home)
    tid = str(uuid.uuid4()).upper()
    try:
        client = MaestriClient(port=srv["port"], auth_token=srv["token"])
        ws_id = client.get_workspace().payload.id
        client.create_terminal(name="K13", command="bash", workspace_id=ws_id, terminal_id=tid, display_name="K13")

        pty = client.connect_pty(tid)
        try:
            pty.send_text("echo K13_BEFORE_$((40+2))\n")
            assert b"K13_BEFORE_42" in _read_until(pty, b"K13_BEFORE_42")
        finally:
            pty._close_socket()

        r = client.session.post(f"{client.base_url}/api/terminals/{tid}/restart", headers=client._headers(), timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == tid
        meta = client.get_terminal(tid)
        assert meta and meta["terminalId"] == tid and meta["running"], meta

        pty = client.connect_pty(tid)
        try:   # the restarted PTY replays the previous output and runs new commands
            pty.send_text("echo K13_AFTER_$((70+7))\n")
            out = _read_until(pty, b"K13_AFTER_77")
            assert b"K13_AFTER_77" in out and b"K13_BEFORE_42" in out, out
        finally:
            pty._close_socket()
    finally:
        stop_server(srv["proc"])   # SIGTERM: the server saves every scrollback before exiting

    scrollback = srv["data_dir"] / "workspaces" / ws_id / "terminals" / f"{tid}.scrollback"
    assert scrollback.is_file(), "scrollback not written on server shutdown"
    assert "K13_AFTER_77" in scrollback.read_text()

    srv2 = start_isolated_server(home)
    try:
        client2 = MaestriClient(port=srv2["port"], auth_token=srv2["token"])
        assert client2.get_terminal(tid) is None, "a fresh server has no PTYs until the window recreates them"
        client2.create_terminal(name="K13", command="bash", workspace_id=ws_id, terminal_id=tid, display_name="K13")
        pty = client2.connect_pty(tid)
        try:
            replay = _read_until(pty, b"K13_AFTER_77", timeout=4.0)
            assert b"K13_AFTER_77" in replay, f"scrollback not restored: {replay!r}"
        finally:
            pty._close_socket()
    finally:
        stop_server(srv2["proc"])
