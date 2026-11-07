"""
Unit tests for Viewport coordinate conversions and zoom mathematics.
"""

import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo" / "src"))

from viewport import Viewport


def test_viewport_initial_coordinates():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    assert vp.origin_x == 9800.0
    assert vp.origin_y == 8500.0
    assert vp.zoom == 1.0


def test_screen_to_canvas_at_unit_zoom():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    cx, cy = vp.screen_to_canvas(100.0, 200.0)
    assert cx == 9900.0
    assert cy == 8700.0


def test_canvas_to_screen_at_unit_zoom():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    sx, sy = vp.canvas_to_screen(9900.0, 8700.0)
    assert sx == 100.0
    assert sy == 200.0


def test_coordinate_round_trip():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.5)
    orig_sx, orig_sy = 450.0, 320.0
    cx, cy = vp.screen_to_canvas(orig_sx, orig_sy)
    res_sx, res_sy = vp.canvas_to_screen(cx, cy)
    assert abs(res_sx - orig_sx) < 1e-6
    assert abs(res_sy - orig_sy) < 1e-6


def test_pan_updates_origin():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    vp.pan(50.0, -30.0)
    assert vp.origin_x == 9750.0
    assert vp.origin_y == 8530.0


def test_zoom_limits():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    # Zoom out repeatedly
    for _ in range(20):
        vp.zoom_at(0.5, 400.0, 300.0)
    assert vp.zoom >= vp.min_zoom

    # Zoom in repeatedly
    for _ in range(20):
        vp.zoom_at(2.0, 400.0, 300.0)
    assert vp.zoom <= vp.max_zoom


def test_zoom_anchoring_around_cursor():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    cursor_x, cursor_y = 500.0, 400.0
    # Canvas point under cursor before zoom
    anchor_cx, anchor_cy = vp.screen_to_canvas(cursor_x, cursor_y)

    vp.zoom_at(1.5, cursor_x, cursor_y)
    # Canvas point under cursor after zoom must be identical
    after_cx, after_cy = vp.screen_to_canvas(cursor_x, cursor_y)
    assert abs(after_cx - anchor_cx) < 1e-6
    assert abs(after_cy - anchor_cy) < 1e-6
