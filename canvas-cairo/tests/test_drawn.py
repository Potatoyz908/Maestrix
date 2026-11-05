"""Canvas-drawn kinds (text / shape / freehand): pure model, engine gestures and Cairo pixels. No display needed."""
import math
import re
import sys
from pathlib import Path

import cairo
import pytest

from maestri_canvas.drawn import (FREEHAND_PADDING, TEXT_DEFAULT_SIZE, content_for_tool, default_freehand, default_shape, default_text,
                                  kind_for_tool, measure_text, normalize_freehand, resolve_rgb)
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.interaction import (CanvasController, DrawRequested, FreehandDrawn, Mods, NodeEdge, NodeHeader, NodesMoved,
                                        NodeResized, Background)
from maestri_canvas.renderer import BG, CairoRenderer
from maestri_canvas.resize import Edge
from maestri_canvas.scene import NodeItem, Scene
from maestri_canvas.viewport import Viewport

ROOT = Path(__file__).resolve().parents[2]
N = Mods.NONE


def _swift_fields(struct: str) -> set[str]:
    """Stored properties of a Swift struct in NodeContent.swift (the persisted keys)."""
    src = (ROOT / "Sources/Workspace/Models/NodeContent.swift").read_text()
    body = re.search(rf"struct {struct}: Codable, Equatable \{{(.*?)\n    init", src, re.S).group(1)
    return set(re.findall(r"^\s+var (\w+):", body, re.M))


@pytest.mark.parametrize("struct, content", [("TextContent", default_text()), ("ShapeContent", default_shape()),
                                             ("FreehandContent", default_freehand())])
def test_defaults_have_exactly_the_swift_keys(struct, content):
    # Swift decodes these with synthesized Codable: a missing key makes the WHOLE workspace fail to load on macOS.
    assert set(content) == _swift_fields(struct)


def test_swift_initialiser_values():
    assert default_freehand("highlighter")["strokeWidth"] == 12.0 and default_freehand("highlighter")["opacity"] == 0.4
    assert default_freehand("pen")["strokeWidth"] == 3.0 and default_freehand("pen")["opacity"] == 1.0
    assert default_text()["fontSize"] == 18 and default_text()["color"] == "#1a1a1a"
    assert content_for_tool("ellipse")["shapeType"] == "ellipse" and content_for_tool("freehand_highlighter")["freehandType"] == "highlighter"
    assert [kind_for_tool(t) for t in ("text", "rect", "diamond", "freehand_pen", "portal", "select")] == \
           ["text", "shape", "shape", "freehand", None, None]


def test_colors_follow_swift_palette():
    assert resolve_rgb("blue") == (0.73, 0.84, 0.96)
    assert resolve_rgb("#FF0000") == (1.0, 0.0, 0.0) and resolve_rgb("00ff00") == (0.0, 1.0, 0.0)
    assert resolve_rgb("#11223344") == pytest.approx((0x11 / 255, 0x22 / 255, 0x33 / 255))
    assert resolve_rgb("nonsense") == resolve_rgb("yellow") == resolve_rgb(None)


def test_freehand_normalisation_matches_macos_handler():
    frame, pts = normalize_freehand([Point(10, 20), Point(110, 20), Point(110, 70)])
    assert frame == Rect(10 - FREEHAND_PADDING, 20 - FREEHAND_PADDING, 100 + 16, 50 + 16)
    assert pts[0] == pytest.approx([8 / 116, 8 / 66]) and pts[2] == pytest.approx([108 / 116, 58 / 66])
    assert normalize_freehand([Point(1, 1)]) is None
    frame, pts = normalize_freehand([Point(5, 5), Point(5, 5)])   # zero-size stroke: padding keeps the frame positive
    assert frame.w == frame.h == 16 and pts[0] == pytest.approx([0.5, 0.5])


def test_schema_v2_roundtrip_keeps_content_and_unknown_keys():
    sys.path.insert(0, str(ROOT))
    from maestri_core.models.node import CanvasNode, NodeFrame
    shape = {**default_shape("diamond"), "futureKey": [1, 2]}
    node = CanvasNode(frame=NodeFrame(x=1, y=2, width=3, height=4), node_type="shape", content=shape)
    d = node.to_dict()
    assert d["content"] == {"shape": {"_0": shape}}
    back = CanvasNode.from_dict(d)
    assert back.node_type == "shape" and back.content == shape


# ---- engine ----------------------------------------------------------------------------------------------
def _ctl(zoom=1.0):
    scene = Scene()
    ctl = CanvasController(scene, Viewport(Point(0, 0), zoom))
    events = []
    ctl.on_event = events.append
    return ctl, scene, events


def test_freehand_tool_collects_points_and_emits_normalised_stroke():
    ctl, _, ev = _ctl(zoom=2.0)
    ctl.set_tool("freehand_pen")
    assert ctl.press(Point(100, 100), 1, N, Background())
    for i in range(1, 21):
        ctl.move(Point(100 + i * 10, 100 + 20 * math.sin(i)), N)
        assert ctl.overlay.draw_path and ctl.overlay.draw_tool == "freehand_pen"
    ctl.release(Point(300, 100), N)
    fh = [e for e in ev if isinstance(e, FreehandDrawn)]
    assert len(fh) == 1 and fh[0].tool == "freehand_pen"
    f = fh[0].frame
    assert f.x == pytest.approx(50 - FREEHAND_PADDING) and f.max_x == pytest.approx(150 + FREEHAND_PADDING)   # canvas units (zoom 2)
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in fh[0].points) and len(fh[0].points) >= 20
    assert ctl.tool == "select" and not ctl.overlay.draw_path


def test_freehand_click_draws_nothing():
    ctl, _, ev = _ctl()
    ctl.set_tool("freehand_highlighter")
    ctl.press(Point(10, 10), 1, N, Background())
    ctl.move(Point(11, 10), N)
    ctl.release(Point(11, 10), N)
    assert not [e for e in ev if isinstance(e, FreehandDrawn)]


def test_text_click_places_default_label_and_shape_click_default_shape():
    ctl, _, ev = _ctl()
    ctl.set_tool("text")
    ctl.press(Point(101, 99), 1, N, Background())
    ctl.release(Point(101, 99), N)
    ctl.set_tool("ellipse")
    ctl.press(Point(400, 400), 1, N, Background())
    ctl.release(Point(401, 400), N)
    draws = [e for e in ev if isinstance(e, DrawRequested)]
    assert draws[0] == DrawRequested("text", Rect(96, 96, *TEXT_DEFAULT_SIZE))
    assert draws[1].kind == "ellipse" and (draws[1].frame.w, draws[1].frame.h) == (200, 160)


def test_shape_drag_is_grid_snapped_and_may_be_small():
    ctl, _, ev = _ctl()
    ctl.set_tool("rect")
    ctl.press(Point(17, 15), 1, N, Background())
    ctl.move(Point(70, 50), N)
    ctl.release(Point(70, 50), N)
    (d,) = [e for e in ev if isinstance(e, DrawRequested)]
    assert d == DrawRequested("rect", Rect(16, 16, 48, 32))   # smaller than a widget node's 160x80 minimum


def test_widget_tool_still_requires_a_real_frame():
    ctl, _, ev = _ctl()
    ctl.set_tool("portal")
    ctl.press(Point(0, 0), 1, N, Background())
    ctl.release(Point(30, 30), N)
    assert not [e for e in ev if isinstance(e, DrawRequested)]


def test_drawing_over_a_drawn_node_but_not_over_a_widget_header():
    ctl, scene, ev = _ctl()
    scene.add_node(NodeItem("S", Rect(0, 0, 200, 200), 1, "shape", content=default_shape()))
    scene.add_node(NodeItem("T", Rect(300, 0, 200, 200), 2, "terminal"))
    ctl.set_tool("freehand_pen")
    ctl.press(Point(50, 50), 1, N, NodeHeader("S"))
    ctl.move(Point(120, 120), N)
    ctl.release(Point(150, 150), N)
    assert [e for e in ev if isinstance(e, FreehandDrawn)]
    ctl.set_tool("freehand_pen")
    ctl.press(Point(350, 10), 1, N, NodeHeader("T"))   # a widget header drags its node even with a tool active
    ctl.move(Point(380, 10), N)
    ctl.release(Point(380, 10), N)
    assert [e for e in ev if isinstance(e, NodesMoved)]


def test_drawn_nodes_resize_below_widget_minimum():
    ctl, scene, ev = _ctl()
    scene.add_node(NodeItem("X", Rect(0, 0, 64, 48), 1, "text", content=default_text("hi")))
    ctl.press(Point(64, 48), 1, N, NodeEdge("X", Edge.BOTTOM_RIGHT))
    ctl.move(Point(40, 30), N)
    ctl.release(Point(40, 30), N)
    final = [e for e in ev if isinstance(e, NodeResized) and e.final][-1]
    assert final.frame == Rect(0, 0, 48, 32)


# ---- rendering -------------------------------------------------------------------------------------------
def _render(scene, overlay=None, size=(300, 200)):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)
    CairoRenderer().draw(cairo.Context(surf), scene, Viewport(Point(0, 0), 1.0), *size, None, overlay)
    surf.flush()
    return surf


def _px(surf, x, y):
    b = surf.get_data()
    i = y * surf.get_stride() + x * 4
    return b[i + 2] / 255, b[i + 1] / 255, b[i] / 255   # BGRA


def test_shapes_and_freehand_paint_their_colours():
    scene = Scene()
    scene.add_node(NodeItem("R", Rect(10, 10, 100, 80), 1, "shape", content=default_shape("rect", "#FF0000")))
    pts = normalize_freehand([Point(150, 100), Point(280, 100)])
    fh = {**default_freehand("pen", "#0000FF"), "points": pts[1], "strokeWidth": 6.0}
    scene.add_node(NodeItem("F", pts[0], 2, "freehand", content=fh))
    s = _render(scene)
    fill = _px(s, 60, 50)                     # 30% red over the light background
    assert fill[0] > 0.9 and fill[1] < 0.75 and fill[2] < 0.75
    assert _px(s, 10, 50) == pytest.approx((1.0, 0.0, 0.0), abs=0.02)   # the stroke itself
    assert _px(s, 215, 100) == pytest.approx((0.0, 0.0, 1.0), abs=0.02)
    assert _px(s, 215, 120) == pytest.approx(BG, abs=0.01)


def test_text_paints_and_measure_grows_with_content():
    assert measure_text(default_text("a much longer label")) [0] > measure_text(default_text("a"))[0]
    assert measure_text(default_text("one\ntwo"))[1] > measure_text(default_text("one"))[1]
    scene = Scene()
    scene.add_node(NodeItem("T", Rect(0, 0, 300, 60), 1, "text", content={**default_text("██████"), "color": "#000000"}))
    s = _render(scene)
    dark = sum(1 for x in range(0, 200) for y in range(0, 40) if sum(_px(s, x, y)) < 0.5)
    assert dark > 100


def test_selected_drawn_node_gets_an_outline_without_placeholders():
    scene = Scene()
    scene.add_node(NodeItem("S", Rect(50, 50, 100, 60), 1, "shape", content={**default_shape(), "fillStyle": "none", "strokeWidth": 0}))
    assert _px(_render(scene), 47, 80) == pytest.approx(BG, abs=0.01)
    scene.selection = {"S"}
    assert _px(_render(scene), 47, 80) != pytest.approx(BG, abs=0.01)
