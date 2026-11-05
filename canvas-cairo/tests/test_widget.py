"""GTK 4 integration. Needs a display (Wayland/X11); skipped otherwise. Drives the engine through the widget's real picking and
placement, because synthetic pointer input cannot be injected into a Wayland compositor from a test."""
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import Background, Mods, NodeContent, NodeEdge, NodeHeader, NodePin  # noqa: E402
from maestri_canvas.resize import Edge  # noqa: E402
from maestri_canvas.scene import ConnItem, NodeItem  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import HEADER_H, CanvasWidget  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

A, B = "AAAAAAAA-0000-0000-0000-000000000001", "BBBBBBBB-0000-0000-0000-000000000002"


def pump(seconds=0.25):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)


@pytest.fixture
def canvas():
    c = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    c.add_node(NodeItem(A, Rect(100, 100, 300, 200), 1, "terminal", "A"), Gtk.Label(label="term"))
    c.add_node(NodeItem(B, Rect(600, 100, 300, 200), 2, "stickyNote", "B"), Gtk.TextView())
    win = Gtk.Window(default_width=1000, default_height=600)
    win.set_child(c)
    win.present()
    pump(0.4)
    yield c
    win.destroy()
    pump(0.05)


def bounds(canvas, node_id) -> Rect:
    ok, r = canvas.frames[node_id].compute_bounds(canvas)
    assert ok
    return Rect(r.get_x(), r.get_y(), r.get_width(), r.get_height())


def close(a: Rect, b: Rect, tol=1.5):
    return all(abs(x - y) <= tol for x, y in zip((a.x, a.y, a.w, a.h), (b.x, b.y, b.w, b.h)))


def test_nodes_are_placed_and_scaled_by_the_viewport(canvas):
    assert close(bounds(canvas, A), Rect(100, 100, 300, 200))
    canvas.viewport.zoom = 2.0
    canvas.viewport.origin = Point(50, 20)
    canvas.controller.dirty = True
    pump(0.15)
    assert close(bounds(canvas, A), Rect((100 - 50) * 2, (100 - 20) * 2, 600, 400))   # real widgets follow zoom via GskTransform


def test_picking_resolves_roles_even_under_zoom(canvas):
    for zoom, origin in ((1.0, Point(0, 0)), (1.5, Point(30, 10))):
        canvas.viewport.zoom, canvas.viewport.origin = zoom, origin
        canvas.controller.dirty = True
        pump(0.15)
        b = bounds(canvas, A)
        assert canvas.target_at(b.x + b.w / 2, b.y + 14 * zoom) == NodeHeader(A)                     # titlebar (middle of the 28px header)
        assert canvas.target_at(b.x + b.w / 2, b.y + b.h / 2) == NodeContent(A)                      # body: child widget keeps it
        assert canvas.target_at(b.x + b.w + 40 * zoom, b.y + b.h + 40 * zoom) == Background()        # empty canvas
        assert canvas.target_at(b.x + b.w - 3, b.y + b.h - 3) == NodeEdge(A, Edge.BOTTOM_RIGHT)       # corner handle
        assert canvas.target_at(b.x + 2, b.y + b.h / 2) == NodePin(A, "left")                         # pin, inside the frame
        assert canvas.target_at(b.x + b.w - 2, b.y + b.h / 2) == NodePin(A, "right")


def test_edges_are_grabbable_from_outside_and_inside_the_border(canvas):
    b = bounds(canvas, A)
    mid_y = b.y + b.h / 2 + 30                                     # away from the pin at mid-height
    assert canvas.target_at(b.x - 3, mid_y) == NodeEdge(A, Edge.LEFT)       # outside the border
    assert canvas.target_at(b.x + 3, mid_y) == NodeEdge(A, Edge.LEFT)       # inside the border
    assert canvas.target_at(b.x + b.w / 2 - 40, b.y - 3) == NodeEdge(A, Edge.TOP)
    assert canvas.target_at(b.x + b.w / 2 + 40, b.y + b.h + 3) == NodeEdge(A, Edge.BOTTOM)
    assert canvas.target_at(b.x + 40, b.y + 14) == NodeHeader(A)            # the header is no longer shadowed by the side handles
    assert canvas.target_at(b.x - 3, b.y - 3) == NodeEdge(A, Edge.TOP_LEFT)


def test_half_of_the_pin_outside_the_frame_is_clickable(canvas):
    b = bounds(canvas, A)
    t = canvas.target_at(b.x - 4, b.y + b.h / 2)
    assert t == NodePin(A, "left"), f"pin overhang not pickable: {t}"


def test_drag_through_resolved_targets_moves_the_widget_and_rope(canvas):
    canvas.add_connection(ConnItem("11111111-0000-0000-0000-000000000001", A, B))
    b = bounds(canvas, A)
    start = Point(b.x + b.w / 2, b.y + 14)
    target = canvas.target_at(start.x, start.y)
    assert canvas.controller.press(start, 1, Mods.NONE, target)
    canvas.controller.move(Point(start.x + 80, start.y + 48), Mods.NONE)
    canvas.controller.release(Point(start.x + 80, start.y + 48), Mods.NONE)
    pump(0.2)
    assert close(bounds(canvas, A), Rect(180, 148, 300, 200))
    assert canvas.controller.ropes.get("11111111-0000-0000-0000-000000000001").ax == 480           # rope follows the right pin


def test_z_order_stacking_follows_raise_to_front(canvas):
    order = lambda: [w.node_id for w in canvas._stack]
    assert order() == [A, B]
    canvas.controller.press(Point(0, 0), 1, Mods.NONE, NodeContent(A))   # content press selects + raises, without claiming
    pump(0.15)
    assert order() == [B, A] and canvas.scene.selection == {A}
    assert canvas.frames[A].has_css_class("selected") and not canvas.frames[B].has_css_class("selected")


def test_keyboard_zoom_and_signal(canvas):
    got = []
    canvas.connect("viewport-changed", lambda _w, x, y, z: got.append(z))
    assert canvas._key_pressed(None, Gdk.KEY_equal, 0, Gdk.ModifierType.CONTROL_MASK)
    assert canvas.viewport.zoom == 1.25 and got == [1.25]
    assert not canvas._key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType(0))   # unbound plain key: not swallowed


def test_selection_signal_and_css(canvas):
    got = []
    canvas.connect("selection-changed", lambda _w, ids: got.append(ids))
    canvas.controller.press(Point(0, 0), 1, Mods.NONE, NodeHeader(B))
    canvas.controller.release(Point(0, 0), Mods.NONE)
    assert got == [(B,)] and canvas.frames[B].has_css_class("selected")


def test_offscreen_nodes_are_hidden_and_remove_cleans_up(canvas):
    canvas.add_node(NodeItem("CCCCCCCC-0000-0000-0000-000000000003", Rect(50000, 50000, 200, 100), 3, "portal", "far"), Gtk.Label(label="x"))
    pump(0.2)
    assert not canvas.frames["CCCCCCCC-0000-0000-0000-000000000003"].get_visible()
    assert canvas.frames[A].get_visible()
    canvas.remove_node("cccccccc-0000-0000-0000-000000000003")
    assert "CCCCCCCC-0000-0000-0000-000000000003" not in canvas.frames and "CCCCCCCC-0000-0000-0000-000000000003" not in canvas.scene.nodes


def test_close_button_requests_delete_without_a_drag(canvas):
    got = []
    canvas.connect("delete-requested", lambda _w, ids: got.append(ids))
    frame = canvas.frames[A]
    header = frame.get_first_child().get_first_child()   # box -> header
    close_btn = header.get_last_child()
    b = close_btn.compute_bounds(canvas)[1]
    assert canvas.target_at(b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2) == NodeContent(A)   # button keeps its click
    close_btn.emit("clicked")
    assert got == [(A,)]


def test_connecting_reveals_pins_and_top_layer_paints_only_during_gestures(canvas):
    assert canvas.controller.overlay.is_empty() and not canvas.has_css_class("maestri-connecting")
    b = bounds(canvas, A)
    canvas.controller.press(Point(b.x + b.w, b.y + b.h / 2), 1, Mods.NONE, NodePin(A, "right"))
    pump(0.15)
    assert canvas.has_css_class("maestri-connecting") and not canvas.controller.overlay.is_empty()
    canvas.controller.release(Point(900, 500), Mods.NONE)
    pump(0.15)
    assert not canvas.has_css_class("maestri-connecting") and canvas.controller.overlay.is_empty()


def _shot(canvas, path):
    from maestri_canvas.demo import screenshot
    screenshot(canvas, path)
    surf = __import__("cairo").ImageSurface.create_from_png(path)
    return surf.get_width(), surf.get_height(), bytes(surf.get_data())


def test_drawing_area_and_texture_paths_paint_the_same_pixels(tmp_path):
    import maestri_canvas.widget as W
    from maestri_canvas.demo import build_canvas
    results = {}
    for use_da in ([True, False] if W.USE_DRAWING_AREA else [False]):
        saved = W.USE_DRAWING_AREA
        W.USE_DRAWING_AREA = use_da
        try:
            c = build_canvas()
            c.controller.vp.zoom_at(Point(500, 300), 0.9)
            win = Gtk.Window(default_width=1000, default_height=620); win.set_child(c); win.present()
            pump(0.6)
            results[use_da] = _shot(c, str(tmp_path / f"{use_da}.png"))
            win.destroy(); pump(0.05)
        finally:
            W.USE_DRAWING_AREA = saved
    assert results[False][2].count(b"\xf2") > 1000                # the fallback really painted the background (0.949 -> 242)
    if len(results) == 2:
        (w1, h1, a), (w2, h2, b) = results[True], results[False]
        assert (w1, h1) == (w2, h2)
        diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)       # mean abs difference per byte
        assert diff < 1.0, f"paint paths diverge (mean abs byte diff {diff:.2f})"
