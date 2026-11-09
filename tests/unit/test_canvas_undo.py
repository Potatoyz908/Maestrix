import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "gtk4-ui" / "src"), str(ROOT), str(ROOT / "canvas-cairo")]

from maestri_core.models.node import NodeFrame  # noqa: E402
from ui.canvas_undo import CanvasUndo  # noqa: E402


class FakeWin:
    def __init__(self):
        self.nodes = {"A": SimpleNamespace(id="A", frame=NodeFrame(x=0, y=0, width=100, height=100), is_locked=False)}
        self.deleted, self.pasted, self.connected = [], [], []
        self.canvas_widget = SimpleNamespace(
            update_node_frame=lambda nid, r: None, request_delete=lambda ids: self.deleted.append(ids),
            scene=SimpleNamespace(selection=set()), _sync_selection_css=lambda: None,
            controller=SimpleNamespace(dirty=False))
        self.document = SimpleNamespace(payload=SimpleNamespace(
            nodes=list(self.nodes.values()), all_connections=lambda: [SimpleNamespace(a_id="A", b_id="B")]))

    def _find_node(self, nid):
        return self.nodes.get(nid.upper())

    def _on_canvas_frame_changed(self, _cw, nid, x, y, w, h, final):
        old = self.nodes[nid].frame
        self.nodes[nid].frame = NodeFrame(x=x, y=y, width=w, height=h)
        self.undo.frame_changed(nid, old, self.nodes[nid].frame, final)

    def _element_json(self, nodes):
        return json.dumps({"nodes": [{"id": n.id, "frame": [[n.frame.x, n.frame.y], [n.frame.width, n.frame.height]]}
                                     for n in nodes]})

    def _paste_payload(self, data, at):
        self.pasted.append((data, at))
        new = SimpleNamespace(id="A2")
        self.nodes["A2"] = new
        return [new]

    def _on_canvas_connect_requested(self, _cw, a, _sa, b, _sb):
        self.connected.append((a, b))


def make():
    win = FakeWin()
    win.undo = CanvasUndo(win)
    return win


def test_drag_is_undone_to_the_frame_before_the_gesture():
    win = make()
    for x, final in ((10, False), (20, False), (30, True)):
        win._on_canvas_frame_changed(None, "A", x, 0, 100, 100, final)
    assert win.undo.undo()
    assert (win.nodes["A"].frame.x, win.nodes["A"].frame.y) == (0, 0)
    assert not win.undo.undo()   # the replay itself was not recorded


def test_click_without_move_records_nothing():
    win = make()
    win._on_canvas_frame_changed(None, "A", 0, 0, 100, 100, True)
    assert not win.undo.can_undo() or not win.undo.undo()


def test_created_node_is_removed_and_its_async_delete_is_not_recorded():
    win = make()
    win.undo._node_added(SimpleNamespace(id="N"))
    assert win.undo.undo()
    assert win.deleted == [("N",)]
    win.undo.nodes_deleting(("N",))   # the note reports its delete later
    assert not win.undo.undo()


def test_deleted_node_comes_back_in_place_with_its_ropes():
    win = make()
    win.nodes["B"] = SimpleNamespace(id="B")
    win.undo.nodes_deleting(("A",))
    assert win.undo.undo()
    _data, at = win.pasted[0]
    assert (at.x, at.y) == (0, 0)
    assert win.connected == [("A2", "B")]
