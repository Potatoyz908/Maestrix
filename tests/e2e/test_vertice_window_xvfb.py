"""Vertice Sprint 9 (window.py core): offline window under Xvfb with an isolated OPEN_MAESTRI_DATA_DIR.

K1a equal ids · K2 server events applied without echo · K9 lock/z persist + locked delete refused + delete_connection ·
K10 copy/paste of elements, URL -> portal, text -> note · Ctrl+1..9 jump · Open in Editor (mocked) · menu bar ·
File Tree root = workspace dir · last workspace closed -> nothing re-created.
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires a display (run under Xvfb DISPLAY=:99)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "canvas-cairo", ROOT / "gtk4-ui" / "src", ROOT):
    sys.path.insert(0, str(p))

from maestri_canvas.geometry import Point  # noqa: E402
from maestri_canvas.scene import norm  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame, StickyNoteContent, TerminalContent  # noqa: E402
from maestri_core.signals.bus import bus  # noqa: E402


def pump(t: float = 0.1) -> None:
    ctx, end = GLib.MainContext.default(), time.time() + t
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)


def until(cond, t: float = 3.0) -> bool:
    end = time.time() + t
    while time.time() < end:
        pump(0.02)
        if cond():
            return True
    return bool(cond())


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path / "data"))
    from ui.window import OpenMaestriWindow
    app = Gtk.Application(application_id=f"org.openmaestri.vertice.t{uuid.uuid4().hex[:6]}",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    w = OpenMaestriWindow(app, MaestriClient(port=0))   # offline: nothing leaves the process
    w.present()
    assert w.wait_loaded()
    pump(0.3)
    w.document.payload.working_directory = str(tmp_path)
    for n in list(w.document.payload.nodes):   # start from an empty canvas (the offline placeholder has demo terminals)
        w.canvas_widget.remove_node(n.id)
    w.document.payload.nodes.clear()
    yield w
    w.close()
    pump(0.05)


def test_k1a_every_creation_path_has_equal_ids(win):
    nodes = [win._insert_node(win.new_terminal_node(NodeFrame(x=0, y=0, width=400, height=300), "A", "claude", "/tmp")),
             win.create_terminal_preset("codex", at=Point(500, 0))]
    win._create_node("terminal", at=Point(1000, 0))
    nodes.append(win.document.payload.nodes[-1])
    for n in nodes:
        assert n.id == n.content.terminal_id == n.content.id == n.id.upper()
    assert [n.content.agent_type for n in nodes] == ["claude_code", "codex", "generic_shell"]
    from maestri_canvas.geometry import Rect
    win._on_canvas_duplicate_requested(win.canvas_widget, {nodes[1].id: Rect(1500, 0, 400, 300)})
    dup = win.document.payload.nodes[-1]
    assert dup.id != nodes[1].id and dup.id == dup.content.terminal_id


def test_k2_server_events_apply_without_echo(win):
    ws = win.document.payload.id
    echoes: list = []
    hids = [bus.connect(sig, lambda *a, s=sig: echoes.append(s)) for sig in ("node-added", "connection-created")]
    try:
        note = CanvasNode(id=str(uuid.uuid4()).upper(), frame=NodeFrame(x=0, y=0, width=280, height=200),
                          node_type="stickyNote", content=StickyNoteContent(name="Live", file_name="Live.md"))
        tid = str(uuid.uuid4()).upper()
        term = CanvasNode(id=tid, frame=NodeFrame(x=400, y=0, width=400, height=300), node_type="terminal",
                          content=TerminalContent(id=tid, terminal_id=tid, name="Recruit"))
        assert win._on_ws_event({"type": "nodeAdded", "workspaceId": ws, "node": note.to_dict(), "terminalCount": 0})
        assert win._on_ws_event("nodeAdded", {"workspaceId": ws, "node": term.to_dict()})
        assert not win._on_ws_event("nodeAdded", {"workspaceId": ws, "node": term.to_dict()})   # known id: ignored
        assert not win._on_ws_event("nodeAdded", {"workspaceId": "OTHER", "node": CanvasNode().to_dict()})
        assert win._find_node(note.id) and norm(tid) in win.canvas_widget.scene.nodes
        cid = str(uuid.uuid4()).upper()
        assert win._on_ws_event({"type": "connectionCreated", "workspaceId": ws, "connectionId": cid,
                                 "kind": "terminalToNote", "a": tid, "b": note.id})
        assert any(c.id == cid for c in win.document.payload.note_connections) and cid in win.canvas_widget.scene.connections
        assert not win._on_ws_event("connectionCreated", {"workspaceId": ws, "id": cid, "sourceNodeId": tid,
                                                          "targetNodeId": note.id, "kind": "note"})
        assert win._on_ws_event("connectionRemoved", {"workspaceId": ws, "connectionId": cid})
        assert not win.document.payload.note_connections and cid not in win.canvas_widget.scene.connections
        assert win._on_ws_event("nodeRemoved", {"workspaceId": ws, "nodeId": note.id})
        assert win._find_node(note.id) is None and norm(note.id) not in win.canvas_widget.scene.nodes
        pump(0.05)
        assert echoes == []   # nothing re-announced on the bus -> WorkspaceSync never re-posts server-made items
    finally:
        for h in hids:
            bus.disconnect(h)


def test_k9_lock_z_persist_and_locked_delete_refused(win):
    win._create_node("stickyNote", at=Point(0, 0))
    node = win.document.payload.nodes[-1]
    assert win.set_node_locked(node.id, True)
    win._on_canvas_delete_requested(win.canvas_widget, (node.id,))
    assert win._find_node(node.id) is not None
    win._on_node_z_changed(node.id, 42)
    d = node.to_dict()
    assert d["isLocked"] is True and d["zIndex"] == 42
    # reload from the saved form: the canvas item comes back locked
    win.canvas_widget.remove_node(node.id)
    win._add_node_to_canvas(CanvasNode.from_dict(d))
    assert win.canvas_widget.scene.get(node.id).locked is True


def test_k9_delete_connection(win):
    a = win._insert_node(win.new_terminal_node(NodeFrame(x=0, y=0, width=400, height=300), "A"))
    b = win._insert_node(win.new_terminal_node(NodeFrame(x=600, y=0, width=400, height=300), "B"))
    win._on_canvas_connect_requested(win.canvas_widget, a.id, "right", b.id, "left")
    assert until(lambda: len(win.document.payload.connections) == 1)
    cid = win.document.payload.connections[0].id
    assert win.delete_connection(cid)
    assert not win.document.payload.connections and norm(cid) not in win.canvas_widget.scene.connections


def test_k10_copy_paste_element_url_and_text(win):
    win._create_node("stickyNote", at=Point(0, 0))
    src = win.document.payload.nodes[-1]
    n0 = len(win.document.payload.nodes)
    assert win.copy_node(src.id)
    win.paste_nodes(at=Point(500, 500))
    assert until(lambda: len(win.document.payload.nodes) == n0 + 1)
    pasted = win.document.payload.nodes[-1]
    assert pasted.id != src.id and pasted.node_type == "stickyNote" and (pasted.frame.x, pasted.frame.y) == (500, 500)
    assert pasted.content.name == f"{src.content.name} Copy"   # collided -> " Copy"
    clip = Gdk.Display.get_default().get_clipboard()
    portals = sum(n.node_type == "portal" for n in win.document.payload.nodes)
    clip.set("https://example.org/x")
    win.paste_nodes(at=Point(900, 0))
    # (the portal widget may rewrite currentUrl while WebKit loads offline; the node and its title are what paste makes)
    assert until(lambda: sum(n.node_type == "portal" for n in win.document.payload.nodes) == portals + 1)
    clip.set("just some text")
    before = len(win.document.payload.nodes)
    win.paste_nodes(at=Point(1300, 0))
    assert until(lambda: len(win.document.payload.nodes) == before + 1)
    assert win.document.payload.nodes[-1].node_type == "stickyNote"
    view = win.canvas_widget.frames[norm(win.document.payload.nodes[-1].id)].content
    assert until(lambda: view.text() == "just some text")


def test_terminal_jump_editor_menu_and_file_tree_root(win, tmp_path):
    t1 = win._insert_node(win.new_terminal_node(NodeFrame(x=0, y=0, width=400, height=300), "One"))
    win._insert_node(win.new_terminal_node(NodeFrame(x=5000, y=4000, width=400, height=300), "Two"))
    assert win.jump_to_terminal(2) and not win.jump_to_terminal(9)
    vp = win.canvas_widget.viewport
    c = vp.to_canvas(Point(win.canvas_widget.get_width() / 2, win.canvas_widget.get_height() / 2))
    assert abs(c.x - 5200) < 1 and abs(c.y - 4150) < 1
    assert win.terminals_in_order()[0].id == t1.id
    with patch("subprocess.Popen") as popen:
        assert win.open_in_editor()
        assert popen.call_args[0][0][-1] == str(tmp_path)
    menu = win._build_menu_model()
    labels = [menu.get_item_attribute_value(i, "label", None).get_string() for i in range(menu.get_n_items())]
    assert labels == ["_File", "_Edit", "_View", "_Window", "_Help"]
    win._create_node("fileTree", at=Point(0, 800))
    assert win.document.payload.nodes[-1].content.root_path == str(tmp_path)


def test_close_workspace_leaves_nothing_to_autosave(win):
    win._create_node("stickyNote", at=Point(0, 0))
    win.close_workspace()
    assert win.document.payload.nodes == [] and not win.canvas_widget.scene.nodes and win.sync is None
    win._create_node("stickyNote", at=Point(0, 0))
    assert win.document.payload.nodes == []
