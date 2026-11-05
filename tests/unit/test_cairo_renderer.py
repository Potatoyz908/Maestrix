"""
Unit tests and performance benchmarks for CairoCanvasRenderer.
Validates 60 FPS frame time budget (< 16.6ms) for rendering 50+ nodes and connections.
"""

import time
import sys
from pathlib import Path
import pytest
import cairo

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from viewport import Viewport
from renderer import CairoCanvasRenderer
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent
from maestri_core.models.connection import Connection


def test_cairo_canvas_renderer_initialization():
    vp = Viewport()
    renderer = CairoCanvasRenderer(vp)
    assert renderer.viewport == vp


def test_cairo_render_single_frame():
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    renderer = CairoCanvasRenderer(vp)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 800)
    cr = cairo.Context(surface)

    node_a = CanvasNode(
        frame=NodeFrame(x=9900.0, y=8600.0, width=400.0, height=300.0),
        node_type="terminal",
        content=TerminalContent(name="Term A")
    )
    node_b = CanvasNode(
        frame=NodeFrame(x=10400.0, y=8650.0, width=400.0, height=300.0),
        node_type="stickyNote",
        content=StickyNoteContent(name="Note B")
    )
    conn = Connection(a_id=node_a.id, b_id=node_b.id)

    # Render without errors
    renderer.render(
        cr=cr,
        width=1280,
        height=800,
        nodes=[node_a, node_b],
        connections=[conn],
        selected_node_id=node_a.id
    )

    assert surface.get_width() == 1280
    assert surface.get_height() == 800


def test_60fps_performance_budget_with_50_nodes():
    """
    Performance benchmark:
    Verify that rendering a full frame with 50 nodes and 25 connections
    takes less than 16.6ms (60 FPS rendering budget).
    """
    vp = Viewport(origin_x=9800.0, origin_y=8500.0, zoom=1.0)
    renderer = CairoCanvasRenderer(vp)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
    cr = cairo.Context(surface)

    # Generate 50 nodes
    nodes = []
    connections = []
    for i in range(50):
        x = 9000.0 + (i % 10) * 350.0
        y = 8000.0 + (i // 10) * 320.0
        node = CanvasNode(
            frame=NodeFrame(x=x, y=y, width=300.0, height=220.0),
            node_type="terminal" if i % 2 == 0 else "stickyNote",
            content=TerminalContent(name=f"Node {i}") if i % 2 == 0 else StickyNoteContent(name=f"Note {i}"),
            z_index=i
        )
        nodes.append(node)
        if i > 0 and i % 2 == 1:
            connections.append(Connection(a_id=nodes[i - 1].id, b_id=nodes[i].id))

    # Measure render time
    start_time = time.perf_counter()
    renderer.render(
        cr=cr,
        width=1920,
        height=1080,
        nodes=nodes,
        connections=connections,
        selected_node_id=nodes[0].id
    )
    render_time_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"\n[Benchmark] 50 nodes + 25 connections rendered in {render_time_ms:.2f} ms")
    assert render_time_ms < 16.6, f"Render time {render_time_ms:.2f}ms exceeds 60 FPS budget (16.6ms)"
