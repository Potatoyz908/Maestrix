"""
Unit tests for Canvas coordinate transformations and performance.
Validates Viewport coordinate system, anchor invariant during zoom, pan, and speed.
"""

import math
import time
import pytest
from canvas_cairo import Viewport


class TestCoordinates:
    def test_default_viewport(self):
        vp = Viewport()
        assert vp.origin_x == 9800.0
        assert vp.origin_y == 8500.0
        assert vp.zoom == 1.0

    def test_screen_canvas_round_trip(self):
        vp = Viewport(origin_x=1000.0, origin_y=2000.0, zoom=1.5)
        test_points = [
            (0.0, 0.0),
            (1920.0, 1080.0),
            (960.0, 540.0),
            (-500.0, -300.0),
            (12345.67, 8910.11),
        ]
        for sx, sy in test_points:
            cx, cy = vp.screen_to_canvas(sx, sy)
            res_sx, res_sy = vp.canvas_to_screen(cx, cy)
            assert math.isclose(res_sx, sx, abs_tol=1e-6)
            assert math.isclose(res_sy, sy, abs_tol=1e-6)

    def test_pan_behavior(self):
        vp = Viewport(origin_x=0.0, origin_y=0.0, zoom=1.0)
        # Pan 100px right, 50px down (screen drag right moves canvas right -> origin decreases)
        vp.pan(100.0, 50.0)
        assert math.isclose(vp.origin_x, -100.0)
        assert math.isclose(vp.origin_y, -50.0)

        # Pan with zoom = 2.0 (screen delta corresponds to half canvas delta)
        vp2 = Viewport(origin_x=0.0, origin_y=0.0, zoom=2.0)
        vp2.pan(100.0, 50.0)
        assert math.isclose(vp2.origin_x, -50.0)
        assert math.isclose(vp2.origin_y, -25.0)

    def test_zoom_at_anchor_invariant(self):
        """
        Crucial UI invariant:
        The virtual canvas point under the cursor (screen_x, screen_y)
        must remain at the EXACT same screen position after zooming.
        """
        vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
        cursor_x, cursor_y = 640.0, 360.0

        # Point on canvas before zoom
        canvas_x_before, canvas_y_before = vp.screen_to_canvas(cursor_x, cursor_y)

        # Zoom in by 1.25x
        vp.zoom_at(1.25, cursor_x, cursor_y)
        assert math.isclose(vp.zoom, 1.25)

        # Screen position of that canvas point after zoom
        screen_x_after, screen_y_after = vp.canvas_to_screen(canvas_x_before, canvas_y_before)
        assert math.isclose(screen_x_after, cursor_x, abs_tol=1e-6)
        assert math.isclose(screen_y_after, cursor_y, abs_tol=1e-6)

        # Zoom out by 0.5x
        vp.zoom_at(0.5, cursor_x, cursor_y)
        screen_x_after2, screen_y_after2 = vp.canvas_to_screen(canvas_x_before, canvas_y_before)
        assert math.isclose(screen_x_after2, cursor_x, abs_tol=1e-6)
        assert math.isclose(screen_y_after2, cursor_y, abs_tol=1e-6)

    def test_zoom_clamping(self):
        vp = Viewport(origin_x=0.0, origin_y=0.0, zoom=1.0)
        # Try to zoom beyond max (3.0)
        vp.zoom_at(10.0, 500.0, 500.0)
        assert vp.zoom == vp.max_zoom == 3.0

        # Try to zoom below min (0.1)
        vp.zoom_at(0.01, 500.0, 500.0)
        assert vp.zoom == vp.min_zoom == 0.1

    def test_reset_zoom(self):
        vp = Viewport(origin_x=1000.0, origin_y=1000.0, zoom=2.5)
        vp.reset_zoom(500.0, 500.0)
        assert math.isclose(vp.zoom, 1.0, abs_tol=1e-6)

    def test_coordinate_conversion_performance(self):
        """
        Performance validation:
        100,000 coordinate conversions must take < 50ms (< 0.5 microseconds per call).
        """
        vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.2)
        count = 100_000

        start = time.perf_counter()
        for i in range(count):
            _ = vp.screen_to_canvas(float(i % 1920), float(i % 1080))
        elapsed = time.perf_counter() - start

        avg_us = (elapsed / count) * 1_000_000.0
        print(f"\n100k screen_to_canvas: {elapsed*1000:.2f}ms (avg {avg_us:.3f}µs per call)")
        assert elapsed < 0.10, f"Coordinate conversion too slow: {elapsed*1000:.2f}ms"
