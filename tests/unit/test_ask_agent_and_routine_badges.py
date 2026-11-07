"""'Ask agent' end to end (DiffView -> FileTreeView -> CanvasWidget -> bus "ask-agent" -> active terminal -> status back) and
the ⏱ routine badge on terminal headers. Real CanvasWidget + Vte.Terminal (no child process: VTE's "commit" signal carries
what would reach the PTY). Needs a display and Vte 3.91."""
import os
import pty
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gtk4-ui" / "src"))
from maestri_canvas.geometry import Rect  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.widget import CanvasWidget, load_vte  # noqa: E402
from maestri_core.models.node import CanvasNode, FileTreeContent, NodeFrame, TerminalContent  # noqa: E402
from maestri_core.routines import Routine  # noqa: E402
from maestri_core.signals.bus import bus  # noqa: E402
from ui.ask_agent import AGENT_RUNNING, SHELL_PROMPT, UNKNOWN, AskAgentRouter, foreground_job  # noqa: E402
from ui.routines_window import BADGE_KEY, RoutinesController, TerminalBridge, apply_routine_badges, routine_badges  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)
Vte = load_vte()
if Vte is None:
    pytest.skip("Vte 3.91 not available", allow_module_level=True)

AGENT = "AAAAAAAA-0000-4000-8000-000000000001"
SHELL = "AAAAAAAA-0000-4000-8000-000000000002"
FT = "FFFFFFFF-0000-4000-8000-000000000003"
QUOTE = "Explain this change:\n\n```diff\n-old\n+new\n```"


def pump(cond=lambda: False, timeout=3.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.003)
    return cond()


def term_node(nid, name, agent_type):
    return CanvasNode(id=nid, frame=NodeFrame(x=0, y=0, width=420, height=300), node_type="terminal",
                      content=TerminalContent(name=name, agent_type=agent_type, terminal_id=nid))


@pytest.fixture
def env(tmp_path):
    nodes = [term_node(AGENT, "Claude", "claude_code"), term_node(SHELL, "Shell", "generic_shell"),
             CanvasNode(id=FT, frame=NodeFrame(x=0, y=400, width=420, height=300), node_type="fileTree",
                        content=FileTreeContent(root_path=str(tmp_path)))]
    cw = CanvasWidget()
    win = Gtk.Window(default_width=1000, default_height=800)
    win.set_child(cw)
    commits = {AGENT: [], SHELL: []}
    for i, nid in enumerate((AGENT, SHELL)):
        t = Vte.Terminal()
        t.connect("commit", lambda _t, text, _n, k=nid: commits[k].append(text))
        cw.add_node(NodeItem(nid, Rect(9800 + 450 * i, 8500, 420, 300), i, "terminal", nid[:4]), t)
    cw.add_node(NodeItem(FT, Rect(9800, 8900, 420, 300), 3, "fileTree", "Files"), cwd=str(tmp_path))
    win.present()
    pump(timeout=0.3)
    probe = {"state": AGENT_RUNNING, "pids": [], "threads": []}

    def fake_probe(pid):
        probe["pids"].append(pid)
        probe["threads"].append(threading.current_thread())
        if probe["state"] == "raise":
            raise RuntimeError("boom")
        return probe["state"], "claude"

    router = AskAgentRouter(cw, lambda: nodes, bus, probe=fake_probe)
    router.probe_state = probe
    yield cw, router, commits, nodes
    router.dispose()
    win.destroy()
    pump(timeout=0.3)


def ask_from_diff(cw, router=None):
    if router is not None:
        router.last = None
    cw.frames[FT].content.diff_view.emit("ask-requested", QUOTE)
    if router is not None:
        assert pump(lambda: router.last is not None)


def pasted(chunks):
    return "".join(chunks).replace("\x1b[200~", "").replace("\x1b[201~", "")


def test_ask_reaches_the_active_agent_and_presses_enter(env):
    cw, router, commits, _ = env
    cw.active_terminal_id = AGENT
    ask_from_diff(cw, router)
    assert pasted(commits[AGENT]).startswith("Explain this change:")
    assert pump(lambda: commits[AGENT][-1] == "\r")              # Enter after the paste
    assert commits[SHELL] == []
    assert router.last == (FT, AGENT, True, "Sent to Claude.")
    status = cw.frames[FT].content.diff_view.ask_status
    assert status.get_visible() and status.get_label() == "Sent to Claude." and status.has_css_class("success")


def test_plain_shell_gets_the_text_without_enter(env):
    cw, router, commits, _ = env
    cw.active_terminal_id = SHELL
    ask_from_diff(cw)
    pump(timeout=0.3)                                            # give a (wrong) delayed Enter the time to show up
    assert commits[SHELL]
    # Only the paste itself (VTE turns the newlines into CRs, or brackets them when the program enabled bracketed paste,
    # as bash/readline does): no separate Enter keystroke afterwards.
    assert len(commits[SHELL]) == 1 and commits[SHELL][0] != "\r"
    assert router.last[2] and "press Enter" in router.last[3]


def test_no_active_terminal_with_several_is_reported(env):
    cw, router, commits, _ = env
    assert cw.active_terminal_id is None
    ask_from_diff(cw)
    assert commits == {AGENT: [], SHELL: []}
    assert router.last[:3] == (FT, "", False) and "Click the terminal" in router.last[3]
    assert cw.frames[FT].content.diff_view.ask_status.has_css_class("error")


def test_single_terminal_is_the_fallback_target(env):
    cw, router, commits, _ = env
    cw.remove_node(SHELL)
    ask_from_diff(cw, router)
    assert commits[AGENT] and router.last[1] == AGENT


def test_bus_is_the_delivery_path(env):
    cw, router, commits, _ = env
    cw.active_terminal_id = AGENT
    done = []
    hid = bus.connect("ask-agent-done", lambda _b, *a: done.append(a))
    bus.emit("ask-agent", "", "from another emitter")             # e.g. a note or the CLI bridge
    assert pump(lambda: done)
    bus.disconnect(hid)
    assert done == [("", AGENT, True, "Sent to Claude.")] and commits[AGENT]


# ---- L4: never auto-Enter unless the agent is verifiably running ------------------------------------------------------
@pytest.mark.parametrize("state, phrase", [(SHELL_PROMPT, "no longer running"), (UNKNOWN, "Could not confirm"),
                                           ("raise", "Could not confirm")])
def test_exited_or_unverifiable_agent_gets_no_enter(env, state, phrase):
    cw, router, commits, _ = env
    router.probe_state["state"] = state
    cw.active_terminal_id = AGENT
    ask_from_diff(cw, router)
    pump(timeout=0.4)                                            # longer than ENTER_DELAY_MS
    assert pasted(commits[AGENT]).startswith("Explain this change:")
    assert len(commits[AGENT]) == 1 and commits[AGENT][-1] != "\r"
    assert router.last[:3] == (FT, AGENT, True) and phrase in router.last[3] and "without Enter" in router.last[3]


def test_probe_runs_off_the_main_thread_with_the_local_shell_pid(env):
    cw, router, _commits, _ = env
    cw.frames[AGENT].content.maestri_pid = 4242                  # set by create_terminal for a VTE-spawned shell
    cw.active_terminal_id = AGENT
    ask_from_diff(cw, router)
    assert router.probe_state["pids"] == [4242]
    assert router.probe_state["threads"][0] is not threading.main_thread()


def test_core_pty_pid_comes_from_the_lookup(env):
    cw, router, commits, _ = env
    calls = []

    class Transport:
        terminal_id = "CCCCCCCC-0000-4000-8000-00000000000C"

    class CoreView(Gtk.Box):                                     # stands in for VteTerminalView: .term + .transport
        def __init__(self):
            super().__init__()
            self.term = Vte.Terminal()
            self.transport = Transport()
            self.append(self.term)

    cw.remove_node(AGENT)
    view = CoreView()
    view.term.connect("commit", lambda _t, text, _n: commits[AGENT].append(text))
    cw.add_node(NodeItem(AGENT, Rect(9800, 8500, 420, 300), 0, "terminal", "core"), view)
    router.pid_lookup = lambda tid: calls.append((tid, threading.current_thread())) or 777
    cw.active_terminal_id = AGENT
    ask_from_diff(cw, router)
    assert calls[0][0] == Transport.terminal_id and calls[0][1] is not threading.main_thread()
    assert router.probe_state["pids"] == [777]
    assert pump(lambda: commits[AGENT] and commits[AGENT][-1] == "\r")


def test_terminal_closed_while_checking(env):
    cw, router, commits, _ = env
    gate = threading.Event()
    real = router.probe
    router.probe = lambda pid: (gate.wait(2), real(pid))[1]
    cw.active_terminal_id = AGENT
    router.last = None
    ask_from_diff(cw)
    cw.remove_node(AGENT)
    gate.set()
    assert pump(lambda: router.last is not None)
    assert router.last[:3] == (FT, "", False) and "closed" in router.last[3]
    assert commits[AGENT] == []


# ---- foreground_job (/proc) ------------------------------------------------------------------------------------------
def fake_stat(proc, pid, comm, pgrp, tty_nr, tpgid):
    d = proc / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    # pid (comm) state ppid pgrp session tty_nr tpgid flags ...; comm may contain spaces and ')'
    (d / "stat").write_text(f"{pid} ({comm}) S 1 {pgrp} {pgrp} {tty_nr} {tpgid} 4194560 0 0 0\n")
    (d / "comm").write_text(comm + "\n")


def test_foreground_job_parses_proc(tmp_path):
    fake_stat(tmp_path, 100, "bash", 100, 34816, 100)
    assert foreground_job(100, str(tmp_path)) == (SHELL_PROMPT, "bash")
    fake_stat(tmp_path, 200, "bash", 200, 34817, 250)
    fake_stat(tmp_path, 250, "claude (node) )", 250, 34817, 250)
    assert foreground_job(200, str(tmp_path)) == (AGENT_RUNNING, "claude (node) )")
    fake_stat(tmp_path, 300, "bash", 300, 0, -1)                 # no controlling tty
    assert foreground_job(300, str(tmp_path))[0] == UNKNOWN
    assert foreground_job(999, str(tmp_path))[0] == UNKNOWN      # process gone
    assert foreground_job(None)[0] == UNKNOWN and foreground_job(-1)[0] == UNKNOWN
    (tmp_path / "400").mkdir()
    (tmp_path / "400" / "stat").write_text("garbage")
    assert foreground_job(400, str(tmp_path))[0] == UNKNOWN


@pytest.mark.skipif(shutil.which("bash") is None or not os.path.isdir("/proc/self"), reason="needs bash and /proc")
def test_foreground_job_on_a_real_pty():
    """A real interactive bash on a PTY: at its prompt it owns the foreground; while `sleep` runs (as an agent TUI
    would), the foreground job is sleep; after it exits the shell is back in front."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("bash", ["bash", "--norc", "--noprofile", "-i"], {"PS1": "$ ", "PATH": os.environ.get("PATH", "")})
    try:
        def state():
            return foreground_job(pid)[0]

        def wait(expected, timeout=5.0):
            end = time.time() + timeout
            while time.time() < end:
                try:
                    os.read(fd, 4096) if _readable(fd) else None
                except OSError:
                    pass
                if state() == expected:
                    return True
                time.sleep(0.02)
            return state() == expected

        assert wait(SHELL_PROMPT)
        os.write(fd, b"sleep 3\n")
        assert wait(AGENT_RUNNING) and foreground_job(pid)[1] == "sleep"
        os.write(fd, b"\x03")                                     # Ctrl+C: the "agent" exits, plain prompt again
        assert wait(SHELL_PROMPT)
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)


def _readable(fd):
    import select
    return bool(select.select([fd], [], [], 0)[0])


# ---- ⏱ routine badge ------------------------------------------------------------------------------------------------
def test_routine_badges_pure():
    terms = [(AGENT, "Claude", AGENT), (SHELL, "Shell", SHELL)]
    a = Routine.create("Tests", "run tests", 300, AGENT)
    b = Routine.create("Review", "review", 3600, AGENT)
    paused = Routine.create("Old", "x", 60, SHELL)
    paused.is_active = False
    out = routine_badges([a, b, paused], terms, lambda rid: rid == b.id)
    text, tip, css = out[AGENT]
    assert text == "2" and "Tests — every 5m" in tip and "Review — every 1h (running)" in tip and css == ("routine-running",)
    assert out[SHELL] is None                                    # only a paused routine: no badge
    assert routine_badges([a], terms, lambda _r: False)[AGENT] == ("", "Routines:\nTests — every 5m", ())


def test_routine_badge_on_terminal_header_follows_controller(env):
    cw, _router, _commits, nodes = env
    terminals = lambda: [(n.id, n.content.name, n.id) for n in nodes if n.node_type == "terminal"]  # noqa: E731

    class Store:
        items = {}

        def list(self):
            return list(self.items.values())

        def set_active(self, rid, active):
            self.items[rid].is_active = active
            return self.items[rid]

    r = Routine.create("Tests", "one\n&&\ntwo", 300, AGENT)
    Store.items = {r.id: r}
    ctl = RoutinesController(Store(), TerminalBridge(terminals, lambda nid: cw.frames[nid].content), start_driver=False)
    ctl.listeners.append(lambda: apply_routine_badges(cw, ctl, terminals))
    ctl.refresh()
    assert pump(lambda: cw.frames[AGENT].badge(BADGE_KEY) is not None)
    badge = cw.frames[AGENT].badge(BADGE_KEY)
    assert badge.get_label() == "" and badge.maestri_icon.get_icon_name() == "maestri-timer-symbolic"   # icon, no emoji
    assert cw.frames[SHELL].badge(BADGE_KEY) is None
    ctl.run_now(r.id)                                            # chain waits for idle: running -> pulsing badge
    assert badge.has_css_class("routine-running")
    ctl.set_active(r.id, False)
    assert pump(lambda: cw.frames[AGENT].badge(BADGE_KEY) is None)
    ctl.stop()


def test_only_the_owning_window_answers(env, tmp_path):
    """Two windows share the process-wide bus: an ask from window A's FileTree is delivered once, by A; a source-less ask
    is delivered by a single (visible) window."""
    cw, router, commits, nodes = env
    cw2 = CanvasWidget()
    win2 = Gtk.Window(default_width=600, default_height=400)
    win2.set_child(cw2)
    other = []
    t = Vte.Terminal()
    t.connect("commit", lambda _t, text, _n: other.append(text))
    cw2.add_node(NodeItem(SHELL, Rect(9800, 8500, 420, 300), 0, "terminal", "B"), t)
    win2.present()
    pump(timeout=0.3)
    router2 = AskAgentRouter(cw2, lambda: [term_node(SHELL, "B", "claude_code")], bus,
                             probe=lambda pid: (AGENT_RUNNING, "claude"))
    try:
        cw.active_terminal_id = AGENT
        cw2.active_terminal_id = SHELL
        ask_from_diff(cw, router)                                 # source = FT, only on window A
        pump(timeout=0.3)
        assert pasted(commits[AGENT]).startswith("Explain") and other == [] and router2.last is None
        done = []
        hid = bus.connect("ask-agent-done", lambda _b, *a: done.append(a))
        bus.emit("ask-agent", "", "external")
        assert pump(lambda: done)
        pump(timeout=0.3)
        bus.disconnect(hid)
        assert len(done) == 1                                    # exactly one window took it
        assert (router.last and router.last[0] == "") != (router2.last is not None and router2.last[0] == "")
    finally:
        router2.dispose()
        win2.destroy()
        pump(timeout=0.2)
