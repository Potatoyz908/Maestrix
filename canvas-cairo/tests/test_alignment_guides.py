"""Smart alignment guides: pure maths, the drag state machine, the renderer and the CanvasWidget toggle."""
import cairo
import pytest

from maestri_canvas.alignment_guides import SNAP_PX, align, candidates
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.interaction import CanvasController, Mods, NodeHeader, Overlay
from maestri_canvas.renderer import GUIDE, CairoRenderer
from maestri_canvas.scene import NodeItem, Scene
from maestri_canvas.snapping import GuideLine
from maestri_canvas.viewport import Viewport

N = Mods.NONE
OTHER = Rect(0, 0, 200, 100)            # left 0, center x 100, right 200 / top 0, middle 50, bottom 100


# ---- pure -------------------------------------------------------------------------------------------------------
def test_left_edges_snap_within_screen_threshold():
    dx, dy, guides = align(Rect(4, 300, 100, 50), [OTHER])
    assert (dx, dy) == (-4, 0)
    (g,) = guides
    assert g.axis == "vertical" and g.position == 0 and g.kind == "edge"
    assert g.start < 0 and g.end > 350           # spans both boxes plus a small overhang
    assert {0, 50, 100, 300, 325, 350} <= set(g.marks)


def test_out_of_threshold_does_nothing():
    assert align(Rect(SNAP_PX + 1, 300, 100, 50), [OTHER]) == (0, 0, [])


def test_threshold_scales_with_zoom():
    # 10 canvas units = 5 screen px at zoom 0.5 -> snaps; = 20 px at zoom 2 -> does not
    assert align(Rect(10, 300, 100, 50), [OTHER], zoom=0.5)[0] == -10
    assert align(Rect(10, 300, 100, 50), [OTHER], zoom=2.0)[0] == 0


def test_centers_snap_to_centers_and_are_dashed():
    dx, _dy, guides = align(Rect(53, 300, 100, 50), [OTHER])     # center 103 -> 100
    assert dx == -3 and [g.kind for g in guides if g.axis == "vertical"] == ["center"]


def test_edge_never_snaps_to_center():
    # right edge 98 is 2 px from OTHER's center (100), but edges only pair with edges (0 / 200) and centers with centers
    assert align(Rect(38, 300, 60, 50), [OTHER]) == (0, 0, [])


def test_side_by_side_touching_edges():
    dx, _dy, guides = align(Rect(203, 0, 100, 100), [OTHER])     # left 203 -> OTHER right 200
    assert dx == -3
    # tops and bottoms already align too: both horizontal guides are reported
    assert sorted(g.position for g in guides if g.axis == "horizontal") == [0, 50, 100]


def test_both_axes_and_nearest_wins():
    dx, dy, _ = align(Rect(3, 205, 100, 50), [OTHER, Rect(1, 200, 80, 80)])
    assert dx == -2 and dy == -5     # x: 1 (2 away) beats 0 (3 away); y: 200 top


def test_several_nodes_on_one_line_merge_into_one_guide():
    others = [Rect(0, 0, 100, 100), Rect(0, 500, 60, 60)]
    _dx, _dy, guides = align(Rect(2, 250, 80, 80), others)
    v = [g for g in guides if g.axis == "vertical" and g.position == 0]
    assert len(v) == 1 and v[0].start < 0 and v[0].end > 560


def test_candidates_are_the_visible_nodes():
    view = Rect(0, 0, 1000, 600)
    near, far = Rect(10, 10, 50, 50), Rect(5000, 10, 50, 50)
    assert candidates([near, far], view, Rect(100, 100, 50, 50)) == [near]


# ---- drag state machine ---------------------------------------------------------------------------------------------
def make():
    sc = Scene()
    sc.add_node(NodeItem("aa", Rect(0, 0, 200, 100), 1, "terminal"))
    sc.add_node(NodeItem("bb", Rect(0, 300, 200, 100), 2, "terminal"))
    sc.add_node(NodeItem("cc", Rect(600, 300, 100, 100), 3, "terminal"))
    ctl = CanvasController(sc, Viewport(Point(0, 0), 1.0))
    ctl.size = (1200, 800)
    return ctl, sc


def test_drag_snaps_with_guides_only_when_enabled():
    ctl, sc = make()
    assert ctl.snapping is False                       # the engine default; the app turns it on from preferences
    ctl.press(Point(10, 310), 1, N, NodeHeader("bb"))
    ctl.move(Point(14, 330), N)                         # bb.x -> 4: free movement
    assert sc.get("bb").frame.x == 4 and not ctl.overlay.guides
    ctl.release(Point(14, 330), N)

    ctl.snapping = True
    ctl.press(Point(14, 330), 1, N, NodeHeader("bb"))
    ctl.move(Point(18, 340), N)                         # x would be 8: 8 px from aa's left edge -> outside 6 px
    assert sc.get("bb").frame.x == 8
    ctl.move(Point(15, 340), N)                         # x would be 5 -> pulled to 0, guide on x=0
    assert sc.get("bb").frame.x == 0 and any(g.axis == "vertical" and g.position == 0 for g in ctl.overlay.guides)
    ctl.release(Point(15, 340), N)
    assert not ctl.overlay.guides and sc.get("bb").frame.x == 0


def test_group_drag_aligns_the_bounding_box():
    from maestri_canvas.scene import norm
    ctl, sc = make()
    ctl.snapping = True
    sc.selection = {norm("bb"), norm("cc")}
    ctl.press(Point(10, 310), 1, N, NodeHeader("bb"))   # pressing a selected node drags the whole selection
    ctl.move(Point(10, 310 - 197), N)                   # group box (0..700, 300..400) -> top 103, 3 px under aa's bottom 100
    assert sc.get("bb").frame.y == 100 and sc.get("cc").frame.y == 100   # both moved by the same snapped delta
    assert sc.get("bb").frame.x == 0 and sc.get("cc").frame.x == 600
    ctl.release(Point(10, 113), N)


def test_ctrl_keeps_magnetic_tiles_even_with_guides_on():
    ctl, sc = make()
    ctl.snapping = True
    ctl.press(Point(610, 310), 1, N, NodeHeader("cc"))
    ctl.move(Point(610 - 395, 310), Mods.CTRL)          # cc.min_x 205 -> bb.max_x 200 via snap()
    assert sc.get("cc").frame.x == 200
    assert all(g.kind == "edge" and not g.marks for g in ctl.overlay.guides)   # snap() guides: no marks
    ctl.release(Point(215, 310), Mods.CTRL)


# ---- renderer -------------------------------------------------------------------------------------------------------
def test_renderer_draws_rose_guides_and_marks():
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 300)
    cr = cairo.Context(s)
    ov = Overlay(guides=[GuideLine("vertical", 100.5, 10, 290, "edge", (50.0,)),
                         GuideLine("horizontal", 200.5, 10, 390, "center", ())])
    CairoRenderer().draw_overlay(cr, Scene(), Viewport(Point(0, 0), 1.0), ov)
    s.flush()
    d, stride = s.get_data(), s.get_stride()
    px = lambda x, y: (d[y * stride + x * 4 + 2], d[y * stride + x * 4 + 1], d[y * stride + x * 4], d[y * stride + x * 4 + 3])
    r, g, b, a = px(100, 150)
    assert 0 < a < 255 and r > b > g                    # translucent rose hairline
    assert px(102, 52)[3] > 0 and px(102, 48)[3] > 0    # both arms of the cross on the mark at (100.5, 50)
    dashed = [px(x, 200)[3] for x in range(20, 60)]
    assert 0 in dashed and max(dashed) > 0              # center guide is dashed
    assert GUIDE[0] > GUIDE[2] > GUIDE[1]


def test_overlay_with_guides_is_not_empty():
    assert not Overlay(guides=[GuideLine("vertical", 0, 0, 1, "center", (0.0,))]).is_empty()


# ---- CanvasWidget toggle (needs a display) ----------------------------------------------------------------------------
def test_canvas_widget_toggle():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        pytest.skip("no display")
    from maestri_canvas.widget import CanvasWidget
    c = CanvasWidget()
    seen = []
    c.connect("snapping-changed", lambda _c, on: seen.append(on))
    assert c.snapping is False
    c.controller.overlay.guides = [GuideLine("vertical", 0, 0, 1)]
    c.set_snapping(True); c.set_snapping(True)
    assert c.snapping and c.controller.snapping and seen == [True]
    c.set_snapping(False)
    assert seen == [True, False] and not c.controller.overlay.guides
