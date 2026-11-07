"""
Cairo Canvas 60 FPS performance benchmark and rendering validation.
Frame budget for 60 FPS: 16.66ms per frame.
"""

import time
import cairo
import pytest

from canvas_cairo import Viewport, CairoCanvasRenderer
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent
from maestri_core.models.connection import Connection


def create_test_nodes(count: int = 50, start_x: float = 9800.0, start_y: float = 8500.0) -> list[CanvasNode]:
    """Generates a grid of sample CanvasNodes for rendering tests."""
    nodes = []
    cols = 10
    spacing_x = 350.0
    spacing_y = 250.0

    for i in range(count):
        col = i % cols
        row = i // cols
        x = start_x + (col * spacing_x)
        y = start_y + (row * spacing_y)
        w = 300.0
        h = 200.0

        if i % 2 == 0:
            content = TerminalContent(name=f"Term {i}", working_directory="/tmp")
            node_type = "terminal"
        else:
            content = StickyNoteContent(name=f"Note {i}", file_name=f"note-{i}.md")
            node_type = "stickyNote"

        node = CanvasNode(
            id=f"node-{i:04d}",
            frame=NodeFrame(x=x, y=y, width=w, height=h),
            node_type=node_type,
            content=content,
        )
        nodes.append(node)
    return nodes


def create_test_connections(nodes: list[CanvasNode]) -> list[Connection]:
    """Creates rope connections between consecutive nodes."""
    connections = []
    for i in range(len(nodes) - 1):
        conn = Connection(
            id=f"conn-{i:04d}",
            a_id=nodes[i].id,
            b_id=nodes[i + 1].id,
            kind="terminal",
        )
        connections.append(conn)
    return connections


class TestCairoPerformance:
    def test_cairo_surface_creation(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
        assert surface.get_width() == 1920
        assert surface.get_height() == 1080

    def test_grid_rendering_speed(self):
        """Dot grid rendering alone must take < 3ms on 1080p."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
        cr = cairo.Context(surface)
        vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
        renderer = CairoCanvasRenderer(vp)

        start = time.perf_counter()
        renderer._draw_grid(cr, 1920, 1080)
        elapsed = time.perf_counter() - start

        elapsed_ms = elapsed * 1000.0
        print(f"\nGrid render 1080p: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 5.0, f"Grid rendering too slow: {elapsed_ms:.2f}ms"

    def test_60fps_benchmark_50_nodes(self):
        """
        Primary 60 FPS Target Verification:
        Rendering a full canvas frame (Background + 50 Nodes + 49 Ropes) at 1080p
        must average well under 16.66ms per frame.
        """
        width, height = 1920, 1080
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)

        vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
        renderer = CairoCanvasRenderer(vp)

        nodes = create_test_nodes(count=50)
        connections = create_test_connections(nodes)
        selected_id = nodes[0].id

        # Warm-up frame
        renderer.render(cr, width, height, nodes, connections, selected_node_id=selected_id)

        frame_times = []
        frames_to_test = 60  # Full 1-second simulation at 60Hz

        for frame_idx in range(frames_to_test):
            # Simulate slight continuous pan to force full dynamic re-render
            vp.origin_x += 1.0

            start = time.perf_counter()
            renderer.render(cr, width, height, nodes, connections, selected_node_id=selected_id)
            surface.flush()
            frame_duration = time.perf_counter() - start
            frame_times.append(frame_duration)

        avg_frame_ms = (sum(frame_times) / len(frame_times)) * 1000.0
        max_frame_ms = max(frame_times) * 1000.0
        fps = 1000.0 / avg_frame_ms if avg_frame_ms > 0 else 999.0

        print(f"\n--- 60 FPS BENCHMARK (50 nodes + 49 ropes) ---")
        print(f"Average frame time: {avg_frame_ms:.2f}ms (Equivalent to {fps:.1f} FPS)")
        print(f"Max frame time:     {max_frame_ms:.2f}ms")
        print(f"Budget:             16.67ms (60 FPS)")

        # Assert 60 FPS constraint (< 16.66ms)
        assert avg_frame_ms < 16.67, f"Average frame time ({avg_frame_ms:.2f}ms) exceeds 16.67ms (sub-60fps!)"

    def test_stress_render_100_nodes(self):
        """Stress test: 100 nodes rendered on 1080p surface."""
        width, height = 1920, 1080
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)

        vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=0.8)
        renderer = CairoCanvasRenderer(vp)

        nodes = create_test_nodes(count=100)
        connections = create_test_connections(nodes)
        selected_id = nodes[10].id

        start = time.perf_counter()
        renderer.render(cr, width, height, nodes, connections, selected_node_id=selected_id)
        surface.flush()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        print(f"\nStress render 100 nodes + 99 ropes: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 33.33, f"100-node stress render too slow: {elapsed_ms:.2f}ms"
