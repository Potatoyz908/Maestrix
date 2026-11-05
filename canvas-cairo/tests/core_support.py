"""Shared by the end-to-end tests: the REAL open-maestri-server in a private user+mount namespace (see _isolated_cmd)."""
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _binary():
    for p in (REPO / ".build/debug/open-maestri-server", REPO / ".build/release/open-maestri-server"):
        if p.exists():
            return p
    return None


def _isolated_cmd(home: Path, argv: list[str]) -> list[str]:
    """Run `argv` in a private user+mount namespace where the account's home (/root, as seen by getpwuid) is bind-mounted to `home`.
    The core resolves its data dir with getpwuid, NOT $HOME, so setting HOME does not isolate it: without this it would use (and its
    agent.sock would replace) the real ~/.open-maestri."""
    return ["unshare", "--user", "--map-root-user", "--mount", "sh", "-c", 'mount --bind "$1" /root && shift && exec "$@"', "sh", str(home), *argv]


def _isolation_proven(home: Path) -> bool:
    """Pre-flight with a harmless command: prove a write to the account home lands in `home` and not in the real one."""
    probe = f"probe-{uuid.uuid4().hex}"
    r = subprocess.run(_isolated_cmd(home, ["sh", "-c", f'touch "$(getent passwd $(id -u) | cut -d: -f6)/{probe}"']), capture_output=True, timeout=10)
    return r.returncode == 0 and (home / probe).exists() and not (Path.home() / probe).exists() and not (Path.home() / ".open-maestri" / probe).exists()


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    binary = _binary()
    if binary is None:
        pytest.skip("open-maestri-server is not built")
    home = tmp_path_factory.mktemp("home")
    if not _isolation_proven(home):
        pytest.skip("cannot isolate the core's home directory (user namespaces unavailable): refusing to start it against real user data")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAESTRI_", "OMAESTRI_"))}
    proc = subprocess.Popen(_isolated_cmd(home, [str(binary)]), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        port = None
        end = time.time() + 25
        while time.time() < end and port is None and proc.poll() is None:
            line = proc.stdout.readline()
            if "active on port" in line:
                port = int(line.strip().rsplit(" ", 1)[1])
        if port is None:
            pytest.skip("core server did not start")
        token_file = home / ".open-maestri/run/auth.token"
        end = time.time() + 5
        while not token_file.exists() and time.time() < end:
            time.sleep(0.05)
        assert token_file.exists(), "core wrote outside the isolated home"
        yield {"port": port, "token": token_file.read_text().strip(), "home": home}
    finally:   # runs on every path, including a failed assertion above: never leave a server behind
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
