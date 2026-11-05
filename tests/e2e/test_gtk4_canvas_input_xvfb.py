"""Sprint 4 canvas input: drawn nodes (text / shape / freehand), portal<->portal sessions and Ctrl+scroll over a portal.

Two groups:
  * REAL input (XTEST events from xdotool): only on an X11 display, so run it under Xvfb:
        scripts/run-e2e-xvfb.sh tests/e2e/test_gtk4_canvas_input_xvfb.py
    Wayland does not accept synthetic input from a test, so these skip there.
  * Window integration (any display): DrawingTools -> document (schema v2) -> canvas, portal sessions through the window's
    connect flow with a stubbed core client.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas import gi_env  # noqa: E402
from maestri_canvas.drawn import default_shape, default_text  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import Background, NodeEdge, NodeHeader  # noqa: E402
from maestri_canvas.resize import Edge  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.connection import Connection  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame, PortalContent  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402

pytestmark = pytest.mark.e2e

HAS_WEBKIT = gi_env.webkit_available()
REAL_INPUT = type(Gdk.Display.get_default()).__name__ == "X11Display" and shutil.which("xdotool") is not None
needs_real_input = pytest.mark.skipif(not REAL_INPUT, reason="real pointer input needs X11 + xdotool (run under Xvfb)")
needs_webkit = pytest.mark.skipif(not HAS_WEBKIT, reason="WebKit 6.0 typelib not available")


def wait_until(cond, timeout: float = 10.0, interval: float = 0.01) -> bool:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(interval)
    return bool(cond())


def pump(seconds: float = 0.2) -> None:
    wait_until(lambda: False, timeout=seconds)


# ---- real input helpers --------------------------------------------------------------------------------------
class Xdo:
    """xdotool against one window: coordinates are relative to that window (= the canvas, which fills it)."""

    def __init__(self, title: str) -> None:
        assert wait_until(lambda: self._find(title), timeout=5), "window not mapped"
        self.wid = self._find(title)

    @staticmethod
    def _find(title: str) -> str | None:
        out = subprocess.run(["xdotool", "search", "--name", f"^{title}$"], capture_output=True, text=True).stdout.split()
        return out[0] if out else None

    def __call__(self, *args: str) -> None:
        subprocess.run(["xdotool", *args], check=True)

    def move(self, x: float, y: float) -> None:
        self("mousemove", "--window", self.wid, str(int(x)), str(int(y)))
        pump(0.03)


@pytest.fixture
def canvas_window(request):
    title = f"maestri-input-{request.node.name}"
    cw = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    win = Gtk.Window(title=title, default_width=1000, default_height=700)
    win.set_child(cw)
    win.present()
    assert wait_until(lambda: cw.get_width() > 0, timeout=5)
    pump(0.3)
    yield cw, title
    win.destroy()
    pump(0.05)


@needs_real_input
@needs_webkit
def test_ctrl_wheel_over_portal_zooms_canvas_and_plain_wheel_scrolls_page(canvas_window):
    cw, title = canvas_window
    cw.add_node(NodeItem("P1", Rect(100, 100, 500, 400), 1, "portal", "P"), portal={"name": "P", "currentURL": "about:blank"})
    pump(0.3)
    view = cw.frames["P1"].content
    view.web.load_html("<html><body style='height:5000px;margin:0'>tall</body></html>", None)
    assert wait_until(lambda: not view.web.is_loading(), timeout=10)
    x = Xdo(title)
    x.move(350, 400)
    assert cw.target_at(350, 400).id == "P1"   # the pointer is over the portal's web content

    z0 = cw.viewport.zoom
    x("keydown", "ctrl"); x("click", "4"); x("click", "4"); x("keyup", "ctrl")
    assert wait_until(lambda: cw.viewport.zoom > z0 * 1.1, timeout=3), "Ctrl+wheel over the portal did not reach the canvas"
    assert view.web.get_zoom_level() == pytest.approx(1.0)   # WebKit did not zoom the page as well

    z1, o1 = cw.viewport.zoom, cw.viewport.origin
    scroll_y = []
    x("click", "5"); x("click", "5"); x("click", "5")
    pump(0.4)
    view.evaluate("window.scrollY", lambda v, *_: scroll_y.append(v))
    assert wait_until(lambda: scroll_y, timeout=3)
    assert (cw.viewport.zoom, cw.viewport.origin) == (z1, o1)   # a plain wheel belongs to the page...
    assert float(scroll_y[0]) > 0                               # ...and it scrolled


@needs_real_input
def test_real_pointer_draws_shape_moves_it_and_freehand(canvas_window):
    cw, title = canvas_window
    draws, strokes, moves = [], [], []
    cw.connect("draw-requested", lambda _c, *a: draws.append(a))
    cw.connect("freehand-drawn", lambda _c, *a: strokes.append(a))
    cw.connect("node-moved", lambda _c, *a: moves.append(a))
    x = Xdo(title)

    cw.set_tool("rect")
    x.move(100, 100); x("mousedown", "1")
    for i in range(1, 11):
        x.move(100 + i * 20, 100 + i * 10)
    x("mouseup", "1")
    assert wait_until(lambda: draws, timeout=3)
    tool, fx, fy, fw, fh = draws[0]
    assert tool == "rect" and (fx, fy, fw, fh) == (96, 96, 208, 96)   # grid-snapped (16)
    # the app answers by adding the node; then the body of a drawn node drags it (canvas-space hit test, no widget)
    cw.add_node(NodeItem("S1", Rect(fx, fy, fw, fh), 1, "shape", content=default_shape()))
    pump(0.1)
    x.move(200, 150); x("mousedown", "1")
    for i in range(1, 6):
        x.move(200 + i * 10, 150)
    x("mouseup", "1")
    assert wait_until(lambda: any(m[0] == "S1" and m[-1] for m in moves), timeout=3)
    assert cw.scene.get("S1").frame.x == pytest.approx(fx + 50)

    cw.set_tool("freehand_highlighter")
    x.move(500, 400); x("mousedown", "1")
    for i in range(1, 21):
        x.move(500 + i * 8, 400 + (i % 5) * 6)
    x("mouseup", "1")
    assert wait_until(lambda: strokes, timeout=3)
    tool, _x, _y, w, h, pts = strokes[0]
    assert tool == "freehand_highlighter" and len(pts) >= 15 and w > 150
    assert all(0 <= px <= 1 and 0 <= py <= 1 for px, py in pts)
    assert cw.controller.tool == "select"


@needs_real_input
def test_real_typing_into_text_label_editor(canvas_window):
    cw, title = canvas_window
    edits = []
    cw.connect("drawn-content-changed", lambda _c, nid, d: edits.append((nid, d)))
    cw.add_node(NodeItem("T1", Rect(200, 200, 45, 35), 1, "text", content=default_text()))
    x = Xdo(title)
    assert cw.edit_drawn_text("T1")
    pump(0.3)
    x("type", "--delay", "20", "Hello canvas")
    x("key", "Return")
    assert wait_until(lambda: edits, timeout=3)
    assert edits[0] == ("T1", {**default_text(), "text": "Hello canvas"})


# ---- widget hit-testing of drawn nodes (any display) -------------------------------------------------------------
def test_drawn_nodes_are_hit_tested_in_canvas_space(canvas_window):
    cw, _ = canvas_window
    cw.add_node(NodeItem("S1", Rect(100, 100, 200, 100), 1, "shape", content=default_shape()))
    assert "S1" not in cw.frames                          # painted by Cairo, no NodeFrame
    assert cw.target_at(150, 150) == NodeHeader("S1")      # body drags
    assert isinstance(cw.target_at(298, 150), NodeHeader)  # not selected: no resize edges yet
    cw.scene.selection = {"S1"}
    assert cw.target_at(298, 150) == NodeEdge("S1", Edge.RIGHT)
    assert cw.target_at(300, 200) == NodeEdge("S1", Edge.BOTTOM_RIGHT)
    assert isinstance(cw.target_at(500, 500), Background)


def test_double_click_opens_editor_and_escape_cancels(canvas_window):
    cw, _ = canvas_window
    edits = []
    cw.connect("drawn-content-changed", lambda _c, nid, d: edits.append(d))
    cw.add_node(NodeItem("T1", Rect(100, 100, 120, 40), 1, "text", content=default_text("old")))
    assert cw.edit_drawn_text("T1")
    ed = cw._drawn_editor
    assert ed.active and ed.text() == "old"
    ed.view.get_buffer().set_text("new")
    ed.cancel()
    pump(0.1)
    assert not edits and not ed.active
    cw.edit_drawn_text("T1")
    ed.view.get_buffer().set_text("new")
    ed.commit()
    assert wait_until(lambda: edits, timeout=2) and edits[0]["text"] == "new"
    cw.set_node_content("T1", edits[0])
    cw.remove_node("T1")
    assert not ed.active


# ---- window integration (any display) -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.canvasinput", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def window(app):
    win = OpenMaestriWindow(app, MaestriClient(port=0))   # offline standalone
    win.present()
    assert win.wait_loaded(10)
    pump(0.2)
    yield win
    win.close()
    pump(0.05)


def _drawn(win, kind):
    return [n for n in win.document.payload.nodes if n.node_type == kind]


def test_drawing_tools_create_schema_v2_nodes(window):
    cw, tools = window.canvas_widget, window.drawing
    tools.select("ellipse")
    assert cw.controller.tool == "ellipse" and "Elipse" in tools.button.get_label()
    cw.emit("draw-requested", "ellipse", 9900.0, 8600.0, 200.0, 160.0)
    assert cw.controller.tool == "ellipse"   # the signal alone does not reset the tool; the gesture does
    cw.set_tool("select")
    (shape,) = _drawn(window, "shape")
    d = shape.to_dict()
    assert d["frame"] == [[9900.0, 8600.0], [200.0, 160.0]] and d["content"]["shape"]["_0"]["shapeType"] == "ellipse"
    assert cw.scene.get(shape.id).content["shapeType"] == "ellipse"

    cw.emit("freehand-drawn", "freehand_pen", 10.0, 20.0, 100.0, 50.0, [[0.1, 0.2], [0.9, 0.8]])
    (fh,) = _drawn(window, "freehand")
    assert fh.to_dict()["content"]["freehand"]["_0"]["points"] == [[0.1, 0.2], [0.9, 0.8]]

    cw.emit("draw-requested", "text", 9800.0, 8500.0, 45.0, 35.0)
    (text,) = _drawn(window, "text")
    assert cw._drawn_editor is not None and cw._drawn_editor.node_id == text.id.upper()   # a new label starts editing
    cw._drawn_editor.view.get_buffer().set_text("A label that is long enough to grow")
    cw._drawn_editor.commit()
    assert wait_until(lambda: text.content.get("text"), timeout=2)
    assert text.frame.width > 200 and cw.scene.get(text.id).frame.w == text.frame.width   # grew to fit, canvas in sync
    assert text.to_dict()["content"]["text"]["_0"]["text"] == "A label that is long enough to grow"


def test_widget_tool_frame_creates_widget_node_at_drawn_frame(window):
    before = len(window.document.payload.nodes)
    window.canvas_widget.emit("draw-requested", "fileTree", 9000.0, 8000.0, 320.0, 480.0)
    assert len(window.document.payload.nodes) == before + 1
    node = window.document.payload.nodes[-1]
    assert node.node_type == "fileTree" and (node.frame.x, node.frame.y, node.frame.width, node.frame.height) == (9000, 8000, 320, 480)


def test_persisted_drawn_nodes_load_with_content(window):
    shape = CanvasNode(frame=NodeFrame(x=1, y=2, width=300, height=200), node_type="shape",
                       content={**default_shape("diamond"), "futureKey": 1})
    window._add_node_to_canvas(CanvasNode.from_dict(shape.to_dict()))
    item = window.canvas_widget.scene.get(shape.id)
    assert item.kind == "shape" and item.content["shapeType"] == "diamond" and item.content["futureKey"] == 1


@needs_webkit
def test_connected_portals_share_session_through_window_flow(window, monkeypatch):
    cw = window.canvas_widget
    a = CanvasNode(frame=NodeFrame(x=9800, y=8500, width=400, height=300), node_type="portal",
                   content=PortalContent(name="A", current_url="about:blank"))
    b = CanvasNode(frame=NodeFrame(x=10300, y=8500, width=400, height=300), node_type="portal",
                   content=PortalContent(name="B", current_url="about:blank"))
    for n in (a, b):
        window.document.payload.nodes.append(n)
        window._add_node_to_canvas(n)
    va, vb = cw.frames[a.id.upper()].content, cw.frames[b.id.upper()].content
    assert va.session is not vb.session

    monkeypatch.setattr(window.client, "create_connection",
                        lambda x, y, kind="terminal", **_: Connection(a_id=x, b_id=y, kind=kind))
    cw.emit("connect-requested", a.id, "right", b.id, "left")
    assert va.session is vb.session   # optimistic: shared while the POST is in flight
    assert wait_until(lambda: window.document.payload.portal_to_portal_connections, timeout=5)
    conn = window.document.payload.portal_to_portal_connections[0]
    assert conn.kind == "portalToPortal" and conn.id in cw.scene.connections and va.session is vb.session

    window._on_canvas_delete_requested(cw, (b.id,))   # deleting one side purges the connection and isolates the survivor
    assert not window.document.payload.portal_to_portal_connections and not cw.scene.connections


@needs_webkit
def test_failed_portal_connection_splits_sessions_again(window, monkeypatch):
    cw = window.canvas_widget
    nodes = [CanvasNode(frame=NodeFrame(x=9800 + i * 500, y=8500, width=400, height=300), node_type="portal",
                        content=PortalContent(name=f"P{i}", current_url="about:blank")) for i in range(2)]
    for n in nodes:
        window.document.payload.nodes.append(n)
        window._add_node_to_canvas(n)

    def boom(*_a, **_k):
        raise RuntimeError("core down")

    monkeypatch.setattr(window.client, "create_connection", boom)
    cw.emit("connect-requested", nodes[0].id, "right", nodes[1].id, "left")
    va, vb = (cw.frames[n.id.upper()].content for n in nodes)
    assert wait_until(lambda: not cw.scene.connections, timeout=5)
    assert va.session is not vb.session and not window.document.payload.portal_to_portal_connections
