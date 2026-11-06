"""Rope drop on the target's whole box (not only its 14px pin), anchored at the border center nearest the source."""
import pytest

from maestri_canvas.geometry import Point, Rect
from maestri_canvas.interaction import CanvasController, ConnectRequested, Mods, NodePin
from maestri_canvas.scene import ConnItem, NodeItem, Scene, nearest_side, pin_point
from maestri_canvas.viewport import Viewport

N = Mods.NONE


def setup(target: Rect, kind: str = "portal"):
    sc = Scene()
    sc.add_node(NodeItem("src", Rect(0, 0, 200, 100), 1, "terminal", "S"))
    sc.add_node(NodeItem("dst", target, 2, kind, "D"))
    ctl = CanvasController(sc, Viewport(Point(0, 0), 1.0))
    ctl.size = (3000, 3000)
    ev = []
    ctl.on_event = ev.append
    return ctl, sc, ev


def drop(ctl, ev, at: Point):
    ctl.press(Point(200, 50), 1, N, NodePin("src", "right"))
    ctl.move(at, N)
    hover = ctl.overlay.connecting[2]
    ctl.release(at, N)
    return hover, [e for e in ev if isinstance(e, ConnectRequested)]


@pytest.mark.parametrize("target, side", [
    (Rect(600, 0, 200, 100), "left"),       # target to the right of the source
    (Rect(-800, 0, 200, 100), "right"),     # to the left
    (Rect(0, 600, 200, 100), "top"),        # below
    (Rect(0, -600, 200, 100), "bottom"),    # above
])
def test_release_anywhere_in_box_anchors_on_nearest_border(target, side):
    ctl, sc, ev = setup(target)
    at = Point(target.x + target.w * 0.8, target.y + target.h * 0.7)   # deep inside the body, far from any pin
    hover, req = drop(ctl, ev, at)
    assert hover == ("DST", side)
    assert req == [ConnectRequested("SRC", "right", "DST", side)]


def test_anchor_is_center_of_that_border_and_rope_uses_it():
    ctl, sc, ev = setup(Rect(0, 600, 300, 120))
    _, [req] = drop(ctl, ev, Point(250, 700))
    sc.add_connection(ConnItem("c", req.a, req.b, req.side_a, req.side_b))
    a, b = sc.anchors(sc.connections["c"])
    assert (b.x, b.y) == (150, 600) and (a.x, a.y) == (200, 50)


def test_pin_hit_still_wins_with_its_own_side():
    ctl, sc, ev = setup(Rect(600, 0, 200, 100))
    p = pin_point(sc.get("dst").frame, "right")             # the far pin, explicitly chosen
    hover, req = drop(ctl, ev, Point(p.x - 3, p.y))
    assert hover == ("DST", "right") and req[0].side_b == "right"


def test_wide_target_uses_aspect_aware_side():
    # source sits above-left of a very wide node: the line between centers exits through its top border, not its left one
    assert nearest_side(Rect(0, 400, 2000, 100), Rect(0, 0, 200, 100)) == "top"
    assert nearest_side(Rect(600, 0, 200, 100), Rect(0, 0, 200, 100)) == "left"
    assert nearest_side(Rect(0, 0, 200, 100), Rect(0, 0, 200, 100)) == "left"   # coincident centers: classic pin side


def test_invalid_targets_still_refused():
    for kind in ("fileTree", "shape"):                     # non-connectable kinds: no hover, no request
        ctl, sc, ev = setup(Rect(600, 0, 200, 100), kind)
        assert drop(ctl, ev, Point(700, 50)) == (None, [])
    ctl, sc, ev = setup(Rect(600, 0, 200, 100))
    assert drop(ctl, ev, Point(100, 50))[1] == []          # back on the source body
    assert drop(ctl, ev, Point(1500, 1500))[1] == []       # empty canvas
    sc.add_connection(ConnItem("c", "src", "dst"))
    assert drop(ctl, ev, Point(700, 50)) == (None, [])     # already connected


def test_drawn_shape_over_target_is_looked_through():
    ctl, sc, ev = setup(Rect(600, 0, 200, 100))
    sc.add_node(NodeItem("sh", Rect(650, 20, 50, 50), 9, "shape", "x"))
    assert drop(ctl, ev, Point(670, 40))[1] == [ConnectRequested("SRC", "right", "DST", "left")]


def test_zoomed_out_body_drop():
    ctl, sc, ev = setup(Rect(600, 0, 200, 100))
    ctl.vp = Viewport(Point(0, 0), 0.25)
    at = ctl.vp.to_screen(Point(780, 90))
    ctl.press(ctl.vp.to_screen(Point(200, 50)), 1, N, NodePin("src", "right"))
    ctl.release(at, N)
    assert [e.side_b for e in ev if isinstance(e, ConnectRequested)] == ["left"]
