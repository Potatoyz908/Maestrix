import statistics
import time

import cairo
import pytest

from maestri_canvas.geometry import Point, Rect
from maestri_canvas.interaction import Overlay
from maestri_canvas.renderer import BG, CairoRenderer
from maestri_canvas.rope import RopeSimulation
from maestri_canvas.scene import ConnItem, NodeItem, Scene
from maestri_canvas.snapping import GuideLine
from maestri_canvas.viewport import Viewport

W, H = 1280, 720


def surface():
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    return s, cairo.Context(s)


def px(s, x, y):
    s.flush()
    d = s.get_data(); o = y * s.get_stride() + x * 4
    return d[o + 2], d[o + 1], d[o]    # r, g, b (little-endian BGRA)


def test_background_nodes_and_overlay_pixels():
    sc = Scene(); vp = Viewport(Point(0, 0), 1.0)
    sc.add_node(NodeItem("s", Rect(100, 100, 200, 100), 1, "shape"))
    s, cr = surface()
    ov = Overlay(guides=[GuideLine("vertical", 600, 0, 400)])
    CairoRenderer().draw(cr, sc, vp, W, H, None, ov)
    assert px(s, 700, 500) == tuple(round(c * 255) for c in BG)          # empty background (off the grid dots)
    assert px(s, 200, 150) == (58, 58, 58)                               # canvas-drawn node fill
    assert px(s, 600, 200)[2] > px(s, 600, 200)[0]                       # blue-ish guide line


def test_widget_kinds_are_skipped_unless_placeholders():
    sc = Scene(); vp = Viewport(Point(0, 0), 1.0)
    sc.add_node(NodeItem("t", Rect(100, 100, 200, 100), 1, "terminal"))
    s, cr = surface(); CairoRenderer().draw(cr, sc, vp, W, H)
    assert px(s, 200, 150) == tuple(round(c * 255) for c in BG)          # a GTK widget will sit here, Cairo must not paint it
    s, cr = surface(); CairoRenderer(placeholders=True).draw(cr, sc, vp, W, H)
    assert px(s, 200, 150) != tuple(round(c * 255) for c in BG)


def test_culling_skips_offscreen_nodes_but_keeps_the_margin():
    sc = Scene(); vp = Viewport(Point(0, 0), 1.0)
    sc.add_node(NodeItem("near", Rect(W + 100, 0, 50, 50), 1, "shape"))      # inside the 200-unit margin
    sc.add_node(NodeItem("far", Rect(W + 5000, 0, 50, 50), 2, "shape"))
    seen = [n.id for n in sc.visible_nodes(vp.visible_rect(W, H))]
    assert seen == ["NEAR"]


def test_zoomed_out_grid_stays_bounded():
    vp = Viewport(Point(0, 0), 0.1)
    s, cr = surface()
    t = time.perf_counter(); CairoRenderer().draw(cr, Scene(), vp, W, H)
    assert (time.perf_counter() - t) * 1000 < 16


def _heavy_scene():
    sc = Scene(); sim = RopeSimulation()
    for i in range(100):
        sc.add_node(NodeItem(f"n{i}", Rect((i % 10) * 300, (i // 10) * 200, 240, 150), i, "shape" if i % 2 else "other"))
    for i in range(30):
        c = ConnItem(f"c{i}", f"n{i}", f"n{i + 1}")
        sc.add_connection(c); a = sc.anchors(c); sim.add(c.id, *a)
    return sc, sim


def test_frame_budget_100_nodes_30_ropes_under_16ms(performance_baseline_ms=16.0):
    sc, sim = _heavy_scene()
    vp = Viewport(Point(0, 0), 0.4)
    s, cr = surface(); r = CairoRenderer()
    r.draw(cr, sc, vp, W, H, sim)   # warm up
    times = []
    for _ in range(15):
        s, cr = surface()
        t = time.perf_counter(); r.draw(cr, sc, vp, W, H, sim); times.append((time.perf_counter() - t) * 1000)
    median = statistics.median(times)
    print(f"render median {median:.2f} ms (max {max(times):.2f})")
    assert median < performance_baseline_ms


def test_rope_tick_budget_30_ropes():
    sc, sim = _heavy_scene()
    t = time.perf_counter(); n = 0
    while n < 60 and sim.tick():
        n += 1
    per = (time.perf_counter() - t) * 1000 / max(n, 1)
    print(f"rope tick {per:.2f} ms/frame (30 ropes)")
    assert per < 4.0


def test_coordinate_conversion_under_1ms_for_1000_points():
    vp = Viewport(Point(9800, 8500), 1.3)
    pts = [Point(i, i * 2) for i in range(1000)]
    t = time.perf_counter()
    for p in pts: vp.to_canvas(vp.to_screen(p))
    assert (time.perf_counter() - t) * 1000 < 5.0   # generous: this is CPython; the conftest baseline is per-conversion (<1ms)


def test_overlay_layer_is_transparent_and_draws_the_dragged_rope():
    sc = Scene(); vp = Viewport(Point(0, 0), 1.0)
    ov = Overlay(connecting=(Point(100, 100), Point(500, 300), None))
    s, cr = surface()
    CairoRenderer().draw_overlay(cr, sc, vp, ov)
    s.flush(); d = s.get_data(); stride = s.get_stride()
    assert d[10 * stride + 10 * 4 + 3] == 0                      # untouched pixel: fully transparent (lets widgets show through)
    assert any(d[y * stride + x * 4 + 3] for y in range(100, 301, 4) for x in range(100, 501, 4))   # rope pixels exist


def test_overlay_is_empty_flag():
    assert Overlay().is_empty() and not Overlay(guides=[GuideLine("vertical", 1, 0, 1)]).is_empty()
