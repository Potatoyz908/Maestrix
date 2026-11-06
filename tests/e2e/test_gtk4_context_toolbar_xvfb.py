"""Sprint 5: floating context toolbars (NoteContextToolbar / DrawingContextToolbar) over the selected node.

Run headless, never on the user's display:
    scripts/run-e2e-xvfb.sh tests/e2e/test_gtk4_context_toolbar_xvfb.py
The real-input tests (xdotool XTEST clicks) need X11 and skip elsewhere.
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
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.context_toolbar import (DrawingContextToolbar, NoteContextToolbar,  # noqa: E402
                                            STROKE_RANGE)
from maestri_canvas.drawn import default_freehand, default_shape, default_text  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import NodeContent  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402

pytestmark = pytest.mark.e2e
REAL_INPUT = type(Gdk.Display.get_default()).__name__ == "X11Display" and shutil.which("xdotool") is not None
needs_real_input = pytest.mark.skipif(not REAL_INPUT, reason="real pointer input needs X11 + xdotool (Xvfb)")


def wait_until(cond, timeout: float = 5.0, interval: float = 0.01) -> bool:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(interval)
    return bool(cond())


def pump(seconds: float = 0.15) -> None:
    wait_until(lambda: False, timeout=seconds)


def toolbar_rect(cw, tb):
    ok, r = tb.compute_bounds(cw)
    assert ok
    return r.get_x(), r.get_y(), r.get_width(), r.get_height()


def center_in(cw, w):
    ok, p = w.compute_point(cw, Graphene.Point().init(w.get_width() / 2, w.get_height() / 2))
    assert ok
    return p.x, p.y


@pytest.fixture
def canvas(request):
    title = f"maestri-ctx-{request.node.name}"
    cw = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    win = Gtk.Window(title=title, default_width=1000, default_height=700)
    win.set_child(cw)
    win.present()
    assert wait_until(lambda: cw.get_width() > 0)
    pump(0.2)
    cw.title = title
    yield cw
    win.destroy()
    pump(0.05)


def add_note(cw, nid="N1", rect=Rect(200, 200, 340, 260), text="hello world"):
    cw.add_node(NodeItem(nid, rect, 1, "stickyNote", "Note"), note={"name": "Note", "fileName": "Note.md", "color": "yellow"})
    view = cw.frames[nid].content
    assert wait_until(lambda: view._loaded)
    view.buf.set_text(text)
    return view


# ---- which node gets a toolbar, and where ---------------------------------------------------------------
def test_note_toolbar_floats_above_selected_note(canvas):
    add_note(canvas)
    assert canvas.context_toolbar.toolbar is None
    canvas.scene.selection = {"N1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None and canvas.context_toolbar.toolbar.get_visible())
    tb = canvas.context_toolbar.toolbar
    assert isinstance(tb, NoteContextToolbar)
    pump(0.1)
    x, y, w, h = toolbar_rect(canvas, tb)
    assert y + h <= 200 - 8 and abs((x + w / 2) - 370) <= 1      # above the node, centred on it
    assert canvas.target_at(x + 5, y + h / 2) == NodeContent("N1")  # clicks on it are left to the toolbar (not a marquee)


def test_drawing_toolbar_goes_below_when_node_is_at_the_top_and_hides_when_not_single(canvas):
    canvas.add_node(NodeItem("S1", Rect(300, 4, 200, 120), 1, "shape", content=default_shape()))
    canvas.add_node(NodeItem("S2", Rect(600, 300, 200, 120), 2, "shape", content=default_shape()))
    canvas.scene.selection = {"S1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None and canvas.context_toolbar.toolbar.get_visible())
    pump(0.1)
    x, y, w, h = toolbar_rect(canvas, canvas.context_toolbar.toolbar)
    assert y >= 124 + 8                                             # no room above: below the node
    canvas.scene.selection = {"S1", "S2"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is None)
    canvas.scene.selection = {"S2"}
    assert wait_until(lambda: canvas.context_toolbar.key == ("S2", "shape"))
    canvas.viewport.origin = Point(5000, 5000)                       # node off screen: hidden
    canvas.controller.dirty = True
    assert wait_until(lambda: not canvas.context_toolbar.toolbar.get_visible())


def test_hidden_while_editing_text_and_absent_for_widget_kinds_and_locked(canvas):
    canvas.add_node(NodeItem("T1", Rect(100, 300, 120, 40), 1, "text", content=default_text("hi")))
    canvas.add_node(NodeItem("TERM", Rect(500, 300, 300, 200), 2, "terminal", "T"), Gtk.Label(label="term"))
    canvas.add_node(NodeItem("L1", Rect(100, 500, 120, 60), 3, "shape", content=default_shape(), locked=True))
    canvas.scene.selection = {"T1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None and canvas.context_toolbar.toolbar.get_visible())
    canvas.edit_drawn_text("T1")
    assert wait_until(lambda: not canvas.context_toolbar.toolbar.get_visible())
    canvas._drawn_editor.cancel()
    assert wait_until(lambda: canvas.context_toolbar.toolbar.get_visible())
    for nid in ("TERM", "L1"):
        canvas.scene.selection = {nid}
        assert wait_until(lambda: canvas.context_toolbar.toolbar is None)


# ---- NoteContextToolbar ------------------------------------------------------------------------------------
def test_note_buttons_format_selection_and_switch_formatted_note_to_raw(canvas):
    view = add_note(canvas, text="say hello\nsecond")
    canvas.scene.selection = {"N1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None)
    tb = canvas.context_toolbar.toolbar
    buf = view.buf
    buf.select_range(buf.get_iter_at_offset(4), buf.get_iter_at_offset(9))
    view.set_previewing(True)
    tb.buttons["bold"].emit("clicked")
    assert view.text() == "say **hello**\nsecond" and not view.meta.is_previewing
    s, e = buf.get_selection_bounds()
    assert buf.get_text(s, e, False) == "hello"                       # still selected: Italic next works on the same word
    tb.buttons["italic"].emit("clicked")
    assert view.text() == "say ***hello***\nsecond"
    buf.place_cursor(buf.get_iter_at_offset(len("say ***hello***\n") + 2))
    tb.buttons["h2"].emit("clicked")
    tb.buttons["bullet"].emit("clicked")                               # heading + list are different families: both stay
    assert view.text().endswith("- ## second")
    assert buf.can_undo()                                              # went through the buffer as user actions (undo + save path)


def test_note_buttons_are_not_focusable(canvas):
    add_note(canvas)
    canvas.scene.selection = {"N1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None)
    for b in canvas.context_toolbar.toolbar.buttons.values():
        assert not b.get_focus_on_click() and not b.get_can_focus()


# ---- DrawingContextToolbar ----------------------------------------------------------------------------------
def test_shape_toolbar_emits_patches_with_macos_ranges(canvas):
    changes = []
    canvas.connect("drawn-content-changed", lambda _c, nid, d: (changes.append(d), canvas.set_node_content(nid, d)))
    canvas.add_node(NodeItem("S1", Rect(300, 300, 200, 120), 1, "shape", content=default_shape()))
    canvas.scene.selection = {"S1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None)
    tb = canvas.context_toolbar.toolbar
    assert isinstance(tb, DrawingContextToolbar) and tb.stroke_style_btn is not None and tb.fill_style_btn is not None

    tb.palette_buttons["purple"].emit("clicked")
    assert changes[-1]["strokeColor"] == changes[-1]["fillColor"] == "purple"   # theme colour = stroke + fill
    assert tb.color_swatch.color == "purple"                                     # synced back via set_node_content
    tb.plus_btn.emit("clicked")
    assert changes[-1]["strokeWidth"] == 3.0 and tb.value_label.get_text() == "3pt"
    for _ in range(40):
        if tb.plus_btn.get_sensitive():
            tb.plus_btn.emit("clicked")
    assert canvas.scene.get("S1").content["strokeWidth"] == STROKE_RANGE[1] and not tb.plus_btn.get_sensitive()
    tb.fill_items["hatched"].emit("clicked")
    tb.stroke_items["dotted"].emit("clicked")
    assert changes[-2]["fillStyle"] == "hatched" and changes[-1]["strokeStyle"] == "dotted"
    assert tb.fill_items["hatched"].get_label().startswith("✓")
    n = len(changes)
    tb.palette_buttons["purple"].emit("clicked")                                 # same value: no change emitted
    assert len(changes) == n
    assert canvas.scene.get("S1").content["shapeType"] == "rect"                 # untouched keys preserved


def test_text_toolbar_is_font_size_and_freehand_has_no_style_menus(canvas):
    canvas.add_node(NodeItem("T1", Rect(100, 300, 120, 40), 1, "text", content=default_text("hi")))
    canvas.add_node(NodeItem("F1", Rect(400, 300, 120, 60), 2, "freehand", content=default_freehand("highlighter")))
    changes = []
    canvas.connect("drawn-content-changed", lambda _c, nid, d: changes.append((nid, d)))
    canvas.scene.selection = {"T1"}
    assert wait_until(lambda: canvas.context_toolbar.key == ("T1", "text"))
    tb = canvas.context_toolbar.toolbar
    assert tb.value_key == "fontSize" and tb.value_label.get_text() == "18pt" and tb.stroke_style_btn is None
    tb.palette_buttons["darkblue"].emit("clicked")
    assert changes[-1] == ("T1", {**default_text("hi"), "color": "darkblue"})
    canvas.scene.selection = {"F1"}
    assert wait_until(lambda: canvas.context_toolbar.key == ("F1", "freehand"))
    tb = canvas.context_toolbar.toolbar
    assert tb.value_label.get_text() == "12pt" and tb.fill_style_btn is None
    tb.minus_btn.emit("clicked")
    assert changes[-1] == ("F1", {**default_freehand("highlighter"), "strokeWidth": 11.0})


def test_delete_button_requests_delete(canvas):
    asked = []
    canvas.connect("delete-requested", lambda _c, ids: asked.append(tuple(ids)))
    canvas.add_node(NodeItem("S1", Rect(300, 300, 200, 120), 1, "shape", content=default_shape()))
    canvas.scene.selection = {"S1"}
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None)
    last = canvas.context_toolbar.toolbar.get_last_child()
    last.emit("clicked")
    assert asked == [("S1",)]


# ---- window: changes reach the document (schema v2) ----------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.ctxtoolbar", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def window(app):
    win = OpenMaestriWindow(app, MaestriClient(port=0))
    win.present()
    assert win.wait_loaded(10)
    pump(0.2)
    yield win
    win.close()
    pump(0.05)


def test_window_persists_toolbar_changes_and_grows_text(window):
    cw = window.canvas_widget
    shape = window.drawing.create("ellipse", Rect(9900, 8600, 200, 160))
    cw.scene.selection = {shape.id}
    assert wait_until(lambda: cw.context_toolbar.key == (shape.id, "shape"))
    tb = cw.context_toolbar.toolbar
    tb.fill_items["crossHatched"].emit("clicked")
    tb.palette_buttons["green"].emit("clicked")
    d = shape.to_dict()["content"]["shape"]["_0"]
    assert (d["fillStyle"], d["strokeColor"], d["fillColor"], d["shapeType"]) == ("crossHatched", "green", "green", "ellipse")

    text = window.drawing.create("text", Rect(9800, 8500, 45, 35))
    window.drawing._on_content_changed(cw, text.id, {**text.content, "text": "Label"})
    w0 = text.frame.width
    cw.scene.selection = {text.id}
    assert wait_until(lambda: cw.context_toolbar.key == (text.id, "text"))
    for _ in range(10):
        cw.context_toolbar.toolbar.plus_btn.emit("clicked")
    assert text.content["fontSize"] == 28.0 and text.frame.width > w0      # bigger font -> the label grew
    assert cw.scene.get(text.id).frame.w == text.frame.width

    cw.context_toolbar.toolbar.get_last_child().emit("clicked")            # delete through the window's handler
    assert text not in window.document.payload.nodes


# ---- real clicks (XTEST) ---------------------------------------------------------------------------------
class Xdo:
    def __init__(self, title):
        assert wait_until(lambda: self._find(title))
        self.wid = self._find(title)

    @staticmethod
    def _find(title):
        out = subprocess.run(["xdotool", "search", "--name", f"^{title}$"], capture_output=True, text=True).stdout.split()
        return out[0] if out else None

    def click(self, x, y):
        subprocess.run(["xdotool", "mousemove", "--window", self.wid, str(int(x)), str(int(y))], check=True)
        pump(0.05)
        subprocess.run(["xdotool", "click", "1"], check=True)
        pump(0.15)


@needs_real_input
def test_real_click_on_bold_formats_and_keeps_note_editing(canvas):
    view = add_note(canvas, text="make this bold")
    x = Xdo(canvas.title)
    fx, fy = center_in(canvas, view.raw)
    x.click(fx, fy)                                                    # a real click into the note selects it
    assert wait_until(lambda: canvas.context_toolbar.toolbar is not None and canvas.context_toolbar.toolbar.get_visible())
    buf = view.buf
    buf.select_range(buf.get_iter_at_offset(10), buf.get_iter_at_offset(14))
    pump(0.1)
    bx, by = center_in(canvas, canvas.context_toolbar.toolbar.buttons["bold"])
    x.click(bx, by)
    assert wait_until(lambda: view.text() == "make this **bold**"), view.text()
    assert canvas.scene.selection == {"N1"}                            # the click did not start a marquee/deselect
    assert view.raw.has_focus()                                        # caret still in the note


@needs_real_input
def test_real_clicks_on_drawing_toolbar_and_palette_popover(canvas):
    canvas.connect("drawn-content-changed", lambda _c, nid, d: canvas.set_node_content(nid, d))
    canvas.add_node(NodeItem("S1", Rect(300, 300, 200, 120), 1, "shape", content=default_shape()))
    x = Xdo(canvas.title)
    x.click(400, 360)                                                  # select by clicking the drawn shape
    assert wait_until(lambda: canvas.context_toolbar.key == ("S1", "shape") and canvas.context_toolbar.toolbar.get_visible())
    pump(0.1)
    tb = canvas.context_toolbar.toolbar
    x.click(*center_in(canvas, tb.plus_btn))
    assert wait_until(lambda: canvas.scene.get("S1").content["strokeWidth"] == 3.0)
    x.click(*center_in(canvas, tb.color_btn))
    assert wait_until(lambda: tb.color_popover.get_visible())         # the real click reached the MenuButton
    tb.palette_buttons["orange"].emit("clicked")                       # the popover is its own surface: pick the swatch directly
    assert wait_until(lambda: canvas.scene.get("S1").content["strokeColor"] == "orange")
    assert canvas.scene.get("S1").frame == Rect(300, 300, 200, 120)   # toolbar clicks never moved the node
