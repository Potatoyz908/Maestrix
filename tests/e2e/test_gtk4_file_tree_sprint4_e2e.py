"""E2E (real OpenMaestriWindow, offline client): FileTree Sprint 4 integration.

* files dropped on the empty canvas become preview nodes in the document (text -> custom sticky note, image -> file:// portal,
  folder -> File Tree), stacked from the drop point
* List/Grid toggle and root navigation persist into FileTreeContent (viewMode / rootPath) and survive a reload of the node
"""
import sys
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.node import CanvasNode  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def pump_until(cond, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.005)
    return cond()


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.filetree", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    return a


@pytest.fixture
def window(app):
    win = OpenMaestriWindow(app, MaestriClient(port=0))   # offline: nothing is sent anywhere
    win.present()
    assert win.wait_loaded()
    pump_until(lambda: False, 0.3)
    yield win
    win.close()
    pump_until(lambda: False, 0.05)


def test_drop_on_canvas_creates_preview_nodes(window, tmp_path):
    (tmp_path / "notes.py").write_text("x = 1\n")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "pkg").mkdir()
    cw = window.canvas_widget
    before = {n.id for n in window.document.payload.nodes}
    # find an empty spot of the canvas
    w, h = cw.get_width(), cw.get_height()
    spot = next((x, y) for y in range(40, h - 40, 60) for x in range(40, w - 40, 60)
                if type(cw.target_at(x, y)).__name__ == "Background")
    files = [Gio.File.new_for_path(str(tmp_path / n)) for n in ("notes.py", "shot.png", "pkg")]
    assert cw.file_drop_target.emit("drop", GObject.Value(Gdk.FileList, Gdk.FileList.new_from_list(files)), float(spot[0]), float(spot[1]))
    new = [n for n in window.document.payload.nodes if n.id not in before]
    assert [n.node_type for n in new] == ["stickyNote", "portal", "fileTree"]
    note, portal, tree = new
    origin = cw.viewport.to_canvas(type(cw.viewport.origin)(*spot))
    assert (note.frame.x, note.frame.y, note.frame.width, note.frame.height) == pytest.approx((origin.x, origin.y, 320, 240))
    assert portal.frame.y == pytest.approx(origin.y + 260) and tree.frame.y == pytest.approx(origin.y + 520)
    nd = note.content.to_dict()
    assert nd["storageMode"] == {"custom": {"_0": str(tmp_path / "notes.py")}} and nd["fileName"] == "notes.py"
    assert portal.content.current_url == (tmp_path / "shot.png").as_uri()
    assert tree.content.root_path == str(tmp_path / "pkg")
    for n in new:
        assert n.id.upper() in {k.upper() for k in cw.frames}
    # the schema-v2 round trip keeps the custom note's real file name (not "notes.py.md")
    assert CanvasNode.from_dict(note.to_dict()).content.to_dict()["fileName"] == "notes.py"
    # the sticky note really edits that file
    view = cw.frames[note.id.upper()].content if note.id.upper() in cw.frames else cw.frames[note.id].content
    assert pump_until(lambda: "x = 1" in view.text())


def test_view_mode_and_root_persist(window, tmp_path):
    (tmp_path / "sub").mkdir()
    window.canvas_widget.set_size_request(1000, 700)
    before = {n.id for n in window.document.payload.nodes}
    window._create_node("fileTree")
    node = next(n for n in window.document.payload.nodes if n.id not in before)
    view = next(f.content for k, f in window.canvas_widget.frames.items() if k.upper() == node.id.upper())
    assert isinstance(view, FileTreeView)
    view.set_root(tmp_path)
    view.set_view_mode("grid")
    assert node.content.root_path == str(tmp_path) and node.content.view_mode == "grid"
    assert node.content.to_dict()["viewMode"] == "grid"
    # rebuilding the node from the document restores both
    window.canvas_widget.remove_node(node.id)
    window._add_node_to_canvas(node)
    view2 = next(f.content for k, f in window.canvas_widget.frames.items() if k.upper() == node.id.upper())
    assert view2.root == str(tmp_path) and view2.view_mode == "grid"
    assert pump_until(lambda: view2.cells() == ["sub"])
