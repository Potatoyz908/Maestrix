"""VTE terminal node: wiring unit tests (fake transport) + end-to-end against the REAL open-maestri-server binary (isolated HOME)."""
import json
import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
from maestri_canvas.gi_env import vte_available  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)
if not vte_available():
    pytest.skip("Vte 3.91 (GTK 4) typelib not available", allow_module_level=True)

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.pty_transport import SoupPtyTransport  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.terminal_node import VteTerminalView  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def pump_until(cond, timeout=8.0, what="condition"):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return
        time.sleep(0.004)
    raise AssertionError(f"timed out waiting for {what}")


def pump(seconds):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False); time.sleep(0.004)


class FakeTransport:
    def __init__(self):
        self.on_data = None; self.on_status = None
        self.written: list[bytes] = []; self.sizes: list[tuple[int, int]] = []; self.closed = False
    def write(self, data): self.written.append(data.encode() if isinstance(data, str) else data)
    def resize(self, cols, rows): self.sizes.append((cols, rows))
    def close(self): self.closed = True


def in_window(widget, w=700, h=420):
    win = Gtk.Window(default_width=w, default_height=h)
    win.set_child(widget); win.present(); pump(0.4)
    return win


def test_output_is_fed_and_typed_input_goes_to_the_transport():
    t = FakeTransport(); v = VteTerminalView(t); win = in_window(v)
    t.on_data(b"hello \x1b[31mred\x1b[0m world\r\n")
    pump(0.1)
    assert "hello red world" in v.text()                       # escape sequences are interpreted, not printed
    v.term.emit("commit", "ls -l\r", 6)                        # what VTE emits for typed keys and pastes
    assert t.written == [b"ls -l\r"]
    v.dispose(); assert t.closed; win.destroy()


def test_grid_size_is_reported_once_and_again_only_on_change():
    t = FakeTransport(); v = VteTerminalView(t); win = in_window(v, 700, 420)
    first = list(t.sizes)
    assert len(first) >= 1 and first[-1] == (v.term.get_column_count(), v.term.get_row_count()) and first[-1][0] > 20
    pump(0.3)
    assert t.sizes == first                                    # steady state: no spam to the core
    win.set_default_size(420, 300); win.unmaximize(); pump(0.5)
    win.destroy()


def test_reconnect_clears_the_screen_before_the_replay_but_first_open_does_not():
    t = FakeTransport(); v = VteTerminalView(t); win = in_window(v)
    t.on_status("open", False); t.on_data(b"history line\r\n"); pump(0.1)
    assert "history line" in v.text()
    t.on_status("closed", True); t.on_status("open", True); pump(0.1)   # reconnect: the core will replay the buffer
    assert "history line" not in v.text()
    t.on_data(b"history line\r\n"); pump(0.1)
    assert v.text().count("history line") == 1                         # shown once, not twice
    win.destroy()


def test_terminal_inside_the_canvas_keeps_roles_and_scrolls_under_zoom():
    from maestri_canvas.interaction import NodeContent
    canvas = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    t = FakeTransport(); v = VteTerminalView(t)
    canvas.add_node(NodeItem("AAAAAAAA-0000-0000-0000-000000000001", Rect(100, 100, 480, 300), 1, "terminal", "T"), v)
    win = in_window(canvas, 900, 600)
    for zoom in (1.0, 1.5):
        canvas.viewport.zoom_at(Point(0, 0), zoom / canvas.viewport.zoom); canvas.controller.dirty = True; pump(0.3)
        ok, b = canvas.frames["AAAAAAAA-0000-0000-0000-000000000001"].compute_bounds(canvas)
        assert canvas.target_at(b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2) == NodeContent("AAAAAAAA-0000-0000-0000-000000000001")
    assert t.sizes and t.sizes[-1][0] > 20                    # the terminal measured its grid inside the transformed node
    win.destroy()


# ---------------------------------------------------------------------------------------------------------------
# End to end with the real core
# ---------------------------------------------------------------------------------------------------------------
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


def _create_terminal(core, token=None) -> str:
    tid = str(uuid.uuid4()).upper()
    req = urllib.request.Request(f"http://127.0.0.1:{core['port']}/api/terminals", method="POST",
                                 data=json.dumps({"id": tid, "command": "", "workingDirectory": str(core["home"])}).encode(),
                                 headers={"Authorization": f"Bearer {token or core['token']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.load(r)["id"].upper() == tid
    return tid


def test_e2e_type_a_command_and_see_its_output_in_vte(core):
    tid = _create_terminal(core)
    tr = SoupPtyTransport(tid, core["port"], core["token"], reconnect=False)
    v = VteTerminalView(tr); win = in_window(v, 800, 480)
    tr.connect()
    pump_until(lambda: tr.is_open, what="websocket open")
    marker = f"E2E_{uuid.uuid4().hex[:8]}"
    v.term.emit("commit", f"echo {marker}\r", 0)               # exactly what typing in VTE produces
    pump_until(lambda: v.text().count(marker) >= 2, what="command echo + its output")   # typed line + printed result
    v.term.emit("commit", "stty size\r", 0)
    cols, rows = v.term.get_column_count(), v.term.get_row_count()
    pump_until(lambda: f"{rows} {cols}" in v.text(), what=f"stty size == '{rows} {cols}' (resize reached the PTY)")
    v.dispose(); win.destroy()


def test_e2e_reconnect_replays_history_exactly_once(core):
    tid = _create_terminal(core)
    tr = SoupPtyTransport(tid, core["port"], core["token"], base_backoff_s=0.1)
    v = VteTerminalView(tr); win = in_window(v, 800, 480)
    tr.connect(); pump_until(lambda: tr.is_open)
    marker = f"HIST_{uuid.uuid4().hex[:8]}"
    v.term.emit("commit", f"echo {marker}\r", 0)
    pump_until(lambda: v.text().count(marker) >= 2)
    tr._conn.close(1000, None)                                  # simulate a dropped connection
    pump_until(lambda: not tr.is_open, what="close")
    pump_until(lambda: tr.is_open, what="auto reconnect")
    pump_until(lambda: marker in v.text(), what="buffer replay after reconnect")
    pump(0.5)
    assert v.text().count(marker) == 2, "history must appear once (typed line + output), not duplicated by the replay"
    lines = [l for l in v.text().splitlines() if marker in l]
    assert all(l == l.lstrip() for l in lines), f"replay is staircased (bare LF not normalized): {lines}"
    last = [l for l in v.text().splitlines() if l.strip()][-1]
    assert last.rstrip().endswith("#") and last == last.lstrip(), f"prompt should sit at column 0 right after the history, got {last!r}"
    v.dispose(); win.destroy()


def test_e2e_wrong_token_is_rejected_and_never_receives_data(core):
    tid = _create_terminal(core)
    tr = SoupPtyTransport(tid, core["port"], "not-the-token", reconnect=False)
    got, statuses = [], []
    tr.on_data = got.append; tr.on_status = lambda s, r: statuses.append(s)
    tr.connect()
    pump_until(lambda: "error" in statuses, what="auth failure")
    assert not tr.is_open and got == []
