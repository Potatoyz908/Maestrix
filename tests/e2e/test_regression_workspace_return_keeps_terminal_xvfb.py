"""Regression guard: with the core running, leaving a workspace and coming back keeps the same shell (same pid) and shows its
previous screen (a full-screen app included). Run under Xvfb."""
import os, sys, time, uuid
from pathlib import Path
import pytest
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
    ctx = GLib.MainContext.default(); end = time.monotonic()+ms/1000
    while time.monotonic() < end:
        while ctx.pending(): ctx.iteration(False)
        time.sleep(0.01)

def test_terminal_session_survives_leaving_and_returning_to_a_workspace(tmp_path, monkeypatch):
    with start_isolated_server(tmp_path) as srv:
        monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(srv["data_dir"]))
        c = MaestriClient(port=srv["port"], auth_token=srv["token"])
        app = Gtk.Application(application_id="x.sw%d" % os.getpid(), flags=Gio.ApplicationFlags.NON_UNIQUE); app.register(None)
        win = OpenMaestriWindow(app, c); win.present(); assert win.wait_loaded(timeout=10)
        a = c.create_workspace("A", working_directory=str(tmp_path)).id
        b = c.create_workspace("B", working_directory=str(tmp_path)).id
        def sw(w):
            win._on_workspace_switched(None, w); pump(2500)
        sw(a)
        n = win.new_terminal_node(NodeFrame(x=9900, y=8600, width=420, height=300), name="T1", command="bash", cwd=str(tmp_path))
        win._insert_node(n); pump(3000)
        tid = n.id
        meta1 = c.get_terminal(tid); print("META1", meta1)
        fr = win.canvas_widget.frames[tid.upper()] if tid.upper() in win.canvas_widget.frames else win.canvas_widget.frames[tid]
        term = fr.content.term
        term.feed_child(b"less /etc/services\n"); pump(1500)
        sw(b); print("META_B", c.get_terminal(tid))
        sw(a); pump(2500)
        print("META2", c.get_terminal(tid))
        fr = win.canvas_widget.frames[tid.upper()] if tid.upper() in win.canvas_widget.frames else win.canvas_widget.frames[tid]
        term = fr.content.term
        txt = term.get_text_format(2) or ""
        txt = txt if isinstance(txt, str) else str(txt)
        print("TEXT_AFTER", repr(txt[-300:]))
        pty = c.connect_pty(tid); got = bytearray(); end = time.time()+3
        while time.time() < end:
            f = pty.receive_frame(timeout=0.5)
            if f: got.extend(f[1])
        pty.close() if hasattr(pty, "close") else None
        assert c.get_terminal(tid)["pid"] == meta1["pid"], "the shell was restarted"
        assert b"services" in got, "the core lost the screen"
        assert "services" in txt, "the returning view did not show the previous screen"
