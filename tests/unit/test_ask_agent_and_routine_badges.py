"""'Ask agent' end to end (DiffView -> FileTreeView -> CanvasWidget -> bus "ask-agent" -> active terminal -> status back) and
the ⏱ routine badge on terminal headers. Real CanvasWidget + Vte.Terminal (no child process: VTE's "commit" signal carries
what would reach the PTY). Needs a display and Vte 3.91."""
import sys
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
from ui.ask_agent import AskAgentRouter  # noqa: E402
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
    router = AskAgentRouter(cw, lambda: nodes, bus)
    yield cw, router, commits, nodes
    router.dispose()
    win.destroy()
    pump(timeout=0.3)


def ask_from_diff(cw):
    cw.frames[FT].content.diff_view.emit("ask-requested", QUOTE)


def test_ask_reaches_the_active_agent_and_presses_enter(env):
    cw, router, commits, _ = env
    cw.active_terminal_id = AGENT
    ask_from_diff(cw)
    assert "".join(commits[AGENT]).replace("\x1b[200~", "").replace("\x1b[201~", "").startswith("Explain this change:")
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
    ask_from_diff(cw)
    assert commits[AGENT] and router.last[1] == AGENT


def test_bus_is_the_delivery_path(env):
    cw, router, commits, _ = env
    cw.active_terminal_id = AGENT
    done = []
    hid = bus.connect("ask-agent-done", lambda _b, *a: done.append(a))
    bus.emit("ask-agent", "", "from another emitter")             # e.g. a note or the CLI bridge
    bus.disconnect(hid)
    assert done == [("", AGENT, True, "Sent to Claude.")] and commits[AGENT]


# ---- ⏱ routine badge ------------------------------------------------------------------------------------------------
def test_routine_badges_pure():
    terms = [(AGENT, "Claude", AGENT), (SHELL, "Shell", SHELL)]
    a = Routine.create("Tests", "run tests", 300, AGENT)
    b = Routine.create("Review", "review", 3600, AGENT)
    paused = Routine.create("Old", "x", 60, SHELL)
    paused.is_active = False
    out = routine_badges([a, b, paused], terms, lambda rid: rid == b.id)
    text, tip, css = out[AGENT]
    assert text == "⏱ 2" and "Tests — every 5m" in tip and "▶ Review — every 1h" in tip and css == ("routine-running",)
    assert out[SHELL] is None                                    # only a paused routine: no badge
    assert routine_badges([a], terms, lambda _r: False)[AGENT] == ("⏱", "Routines:\nTests — every 5m", ())


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
    assert badge.get_label() == "⏱" and cw.frames[SHELL].badge(BADGE_KEY) is None
    ctl.run_now(r.id)                                            # chain waits for idle: running -> pulsing badge
    assert badge.has_css_class("routine-running")
    ctl.set_active(r.id, False)
    assert pump(lambda: cw.frames[AGENT].badge(BADGE_KEY) is None)
    ctl.stop()
