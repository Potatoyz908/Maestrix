"""Regression: when the core lost a terminal's shell (restart), reopening the workspace relaunches the agent with its
"continue" flag, not as an empty chat. A brand-new terminal must NOT get the flag. Needs a display (Xvfb)."""
import os, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
for p in (REPO/"canvas-cairo", REPO/"gtk4-ui"/"src", REPO, REPO/"tests"): sys.path.insert(0, str(p))
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, GLib
from data_dir_isolation import start_isolated_server
from maestri_core.client.api import MaestriClient
from maestri_core.models.node import NodeFrame
from ui.window import OpenMaestriWindow


def pump(ms):
    ctx = GLib.MainContext.default(); end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        while ctx.pending(): ctx.iteration(False)
        time.sleep(0.01)


def test_agent_reopens_with_continue_only_when_restored(tmp_path, monkeypatch):
    fake = tmp_path / "bin"; fake.mkdir()
    exe = fake / "claude"
    exe.write_text('#!/bin/bash\necho "FAKE_CLAUDE_ARGS: $*"\nsleep 30\n'); exe.chmod(0o755)
    with start_isolated_server(tmp_path) as srv:
        monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(srv["data_dir"]))
        c = MaestriClient(port=srv["port"], auth_token=srv["token"])
        app = Gtk.Application(application_id="x.res%d" % os.getpid(), flags=Gio.ApplicationFlags.NON_UNIQUE); app.register(None)
        win = OpenMaestriWindow(app, c); win.present(); assert win.wait_loaded(timeout=10)
        a = c.create_workspace("A", working_directory=str(tmp_path)).id
        b = c.create_workspace("B", working_directory=str(tmp_path)).id
        def sw(w): win._on_workspace_switched(None, w); pump(2500)
        sw(a)
        n = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), name="Agent", command=str(exe), cwd=str(tmp_path))
        win._insert_node(n); pump(3000)
        tid = n.id.upper()
        def screen():
            fr = win.canvas_widget.frames[tid]; t = fr.content.term
            from gi.repository import Vte
            return str(t.get_text_format(Vte.Format.TEXT) or "")
        assert "FAKE_CLAUDE_ARGS:" in screen(), screen()
        assert "--continue" not in screen(), "a brand-new agent must not be told to continue"
        sw(b)
        c.delete_terminal(tid); pump(800)           # the core lost the shell (what a server restart does)
        sw(a); pump(2500)
        assert "FAKE_CLAUDE_ARGS: --continue" in screen().replace("\n", ""), screen()
