"""Regression: the launcher trusted a stale agent.sock and opened the app with no backend.

A dead server (crash, reboot, kill) leaves run/agent.sock behind. The launcher only checked that the file existed,
so it skipped starting a server; the app then ran OFFLINE: clicking a workspace did nothing and the Floors button
stayed disabled. The launcher must probe the socket, drop an orphan one and start a server.

Uses a private OPEN_MAESTRI_DATA_DIR and only kills the server this test started. The final `exec` of the GTK app is
stripped from a COPY of scripts/open-maestri, so no window opens.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "scripts" / "open-maestri"
BINARIES = [REPO / ".build" / "debug" / "open-maestri-server", REPO / ".build" / "release" / "open-maestri-server"]

pytestmark = [
    pytest.mark.skipif(not any(b.is_file() for b in BINARIES), reason="open-maestri-server is not built (swift build)"),
    pytest.mark.skipif(shutil.which("ss") is None, reason="`ss` is needed to find the server this test started"),
]


def _listener_pids(sock: Path) -> list[int]:
    out = subprocess.run(["ss", "-xlpH"], capture_output=True, text=True).stdout
    return sorted({int(m) for line in out.splitlines() if str(sock) in line for m in re.findall(r"pid=(\d+)", line)})


def _accepts(sock: Path) -> bool:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(1.0)
    try:
        s.connect(str(sock))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _run_launcher_without_exec(tmp: Path, data_dir: Path) -> subprocess.CompletedProcess:
    body = LAUNCHER.read_text(encoding="utf-8").rstrip("\n").splitlines()
    assert body[-1].startswith("exec python3"), "launcher layout changed: the GTK exec must stay the last line"
    script = tmp / "launcher_noexec.sh"
    script.write_text("\n".join(body[:-1]) + "\n", encoding="utf-8")
    # BASH_SOURCE points at the copy, so pin DIR/REPO_DIR to the real checkout.
    text = script.read_text(encoding="utf-8").replace(
        'DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"', f'DIR="{REPO / "scripts"}"')
    script.write_text(text, encoding="utf-8")
    env = dict(os.environ, OPEN_MAESTRI_DATA_DIR=str(data_dir))
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30)


def test_orphan_socket_is_replaced_by_a_live_server(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    run = data_dir / "run"
    run.mkdir(parents=True)
    sock = run / "agent.sock"
    orphan = socket.socket(socket.AF_UNIX)
    orphan.bind(str(sock))
    orphan.close()                       # file stays, nobody listens: exactly what a dead server leaves
    assert sock.exists() and not _accepts(sock)

    started: list[int] = []
    try:
        result = _run_launcher_without_exec(tmp_path, data_dir)
        assert result.returncode == 0, result.stderr
        started = _listener_pids(sock)
        assert _accepts(sock), f"no live server after the launcher ran\n{result.stdout}\n{result.stderr}"
        assert started, "could not identify the server the launcher started"

        again = _run_launcher_without_exec(tmp_path, data_dir)   # server alive: must NOT start another one
        assert "Starting Open Maestri background server" not in again.stdout
        assert _listener_pids(sock) == started
    finally:
        for pid in started:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(0.3)
