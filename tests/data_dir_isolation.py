"""Keep every test away from the user's real ~/.open-maestri (shared by tests/conftest.py and canvas-cairo/tests).

* `isolate_session(tmp_root)` points OPEN_MAESTRI_DATA_DIR at a throwaway dir for the whole session (set before any test
  module is imported, so module-level code and module-scoped fixtures see it too).
* `snapshot(real)` / `diff(before, after)` record and compare the real data dir; the conftests fail the session when a test
  wrote to it.
* `start_isolated_server(data_dir)` runs the REAL open-maestri-server against `data_dir`: through OPEN_MAESTRI_DATA_DIR when
  the binary honours it, else inside a private user+mount namespace whose account home is a bind mount (the core resolves
  its home with getpwuid, so HOME alone would not isolate it). When neither is possible the caller is skipped, never run
  against real user data.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

DATA_DIR_ENV = "OPEN_MAESTRI_DATA_DIR"
REPO = Path(__file__).resolve().parents[1]
REAL_DATA_DIR = Path.home() / ".open-maestri"

# Written by the user's own long-running cores, not by tests.
_IGNORED_SUFFIXES = (".log",)


def isolate_session(tmp_root: Path) -> Path:
    data = Path(tmp_root) / "open-maestri-data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ[DATA_DIR_ENV] = str(data)
    return data


def snapshot(root: Path = REAL_DATA_DIR) -> dict[str, tuple[int, int]]:
    """{relative path: (mtime_ns, size)} of everything under `root`, directories included (a removed or added entry
    changes its parent's mtime)."""
    state: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return state
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            if name.endswith(_IGNORED_SUFFIXES):
                continue
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            is_dir = p.is_dir() and not p.is_symlink()
            # A directory's own mtime moves whenever an ignored log inside it is rotated; compare its listing instead.
            state[str(p.relative_to(root))] = (0, 0) if is_dir else (st.st_mtime_ns, st.st_size)
    return state


def diff(before: dict, after: dict) -> list[str]:
    out = [f"+ {k}" for k in sorted(after.keys() - before.keys())]
    out += [f"- {k}" for k in sorted(before.keys() - after.keys())]
    out += [f"~ {k}" for k in sorted(before.keys() & after.keys()) if before[k] != after[k]]
    return out


def server_binary() -> Path | None:
    for rel in (".build/debug/open-maestri-server", ".build/release/open-maestri-server",
                ".build/x86_64-unknown-linux-gnu/release/open-maestri-server"):
        p = REPO / rel
        if p.is_file():
            return p
    return None


def binary_honours_data_dir(binary: Path) -> bool:
    try:
        return DATA_DIR_ENV.encode() in binary.read_bytes()
    except OSError:
        return False


def _namespaced(home: Path, argv: list[str]) -> list[str]:
    return ["unshare", "--user", "--map-root-user", "--mount", "sh", "-c", 'mount --bind "$1" /root && shift && exec "$@"',
            "sh", str(home), *argv]


def _namespace_proven(home: Path) -> bool:
    """Pre-flight with a harmless command: a write to the account home lands in `home`, not in the real one."""
    probe = f"probe-{uuid.uuid4().hex}"
    try:
        r = subprocess.run(_namespaced(home, ["sh", "-c", f'touch "$(getent passwd $(id -u) | cut -d: -f6)/{probe}"']),
                           capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (r.returncode == 0 and (home / probe).exists() and not (Path.home() / probe).exists()
            and not (REAL_DATA_DIR / probe).exists())


class IsolatedServer(dict):
    """What start_isolated_server returns. Works both ways the tests use it:

        srv = start_isolated_server(tmp)          # dict:   srv["port"], srv["token"], stop_server(srv["proc"])
        with start_isolated_server(tmp) as srv:   # object: srv.port, srv.token, srv.base_url; stopped on exit
    """

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self['port']}"

    def stop(self) -> None:
        stop_server(self)

    def __enter__(self) -> "IsolatedServer":
        return self

    def __exit__(self, *_exc) -> None:
        stop_server(self)   # idempotent: fine when the test already stopped it


def start_isolated_server(home: Path, timeout_s: float = 25.0) -> IsolatedServer:
    """Start the core with its data in <home>/.open-maestri; returns an IsolatedServer (a dict with the keys
    "proc", "port", "token", "home", "data_dir", also readable as attributes, plus base_url and `with` support).
    Skips the calling test when the binary is missing or isolation cannot be guaranteed."""
    binary = server_binary()
    if binary is None:
        pytest.skip("open-maestri-server is not built (swift build): live core tests skipped")
    home = Path(home)
    data = home / ".open-maestri"
    data.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAESTRI_", "OMAESTRI_"))}
    env[DATA_DIR_ENV] = str(data)
    if binary_honours_data_dir(binary):
        # Only the SERVER process gets a private HOME: the shells it spawns (PTYs) must not read the user's real
        # ~/.zshrc / ~/.bashrc (a two-line prompt broke the 'prompt at column 0' checks). The GUI keeps its own HOME.
        env["HOME"] = str(home)
        env["SHELL"] = "/bin/bash"
        argv = [str(binary)]
    elif _namespace_proven(home):
        argv = _namespaced(home, [str(binary)])
    else:
        pytest.skip(f"{binary.name} ignores {DATA_DIR_ENV} and user namespaces are unavailable: refusing to start it "
                    "against the real ~/.open-maestri")
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        port = None
        end = time.time() + timeout_s
        while time.time() < end and port is None and proc.poll() is None:
            line = proc.stdout.readline()
            if "active on port" in line:
                port = int(line.strip().rsplit(" ", 1)[1])
        if port is None:
            raise RuntimeError("core server did not report its port")
        token_file = data / "run" / "auth.token"
        end = time.time() + 5
        while not token_file.exists() and time.time() < end:
            time.sleep(0.05)
        if not token_file.exists():
            raise RuntimeError(f"core wrote its auth.token outside {data}")
        # Keep draining the log so a chatty core never blocks on a full pipe.
        threading.Thread(target=lambda: [None for _ in proc.stdout], name="core-log-drain", daemon=True).start()
        return IsolatedServer(proc=proc, port=port, token=token_file.read_text().strip(), home=home, data_dir=data)
    except BaseException:
        stop_server(proc)
        raise


def stop_server(proc) -> None:
    """Stop the core: accepts the Popen or the IsolatedServer. Idempotent (an already stopped process is left alone)."""
    if isinstance(proc, dict):
        proc = proc.get("proc")
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)
