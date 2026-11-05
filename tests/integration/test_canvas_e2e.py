"""
Integration test for full Canvas pipeline:
Models -> Viewport -> Interaction -> Cairo Render Surface.
"""

import cairo
import pytest

from canvas_cairo import Viewport, CairoCanvasRenderer, CanvasInteractionHandler
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent
from maestri_core.models.connection import Connection


class TestCanvasE2E:
    def test_full_pipeline_render(self):
        # 1. Create Workspace with Nodes & Connections
        n1 = CanvasNode(
            id="T1",
            frame=NodeFrame(x=9800.0, y=8500.0, width=400.0, height=280.0),
            node_type="terminal",
            content=TerminalContent(name="term-1", working_directory="/home"),
        )
        n2 = CanvasNode(
            id="N1",
            frame=NodeFrame(x=10300.0, y=8550.0, width=320.0, height=220.0),
            node_type="stickyNote",
            content=StickyNoteContent(name="todo", file_name="todo.md", color="#007AFF"),
        )
        c1 = Connection(a_id="T1", b_id="N1", kind="terminalToNote")

        doc = WorkspaceDocument(
            payload=WorkspacePayload(
                id="WS-E2E",
                name="E2E Pipeline Test",
                canvas_origin=[9800.0, 8500.0],
                canvas_zoom=1.0,
                nodes=[n1, n2],
                connections=[c1],
            )
        )

        # 2. Setup Viewport & Renderer
        vp = Viewport(
            origin_x=doc.payload.canvas_origin[0],
            origin_y=doc.payload.canvas_origin[1],
            zoom=doc.payload.canvas_zoom,
        )
        renderer = CairoCanvasRenderer(vp)
        handler = CanvasInteractionHandler(vp)

        # 3. Simulate User Interaction (select node T1 and drag it)
        screen_t1_x, screen_t1_y = vp.canvas_to_screen(9850.0, 8550.0)
        handler.on_mouse_down(screen_t1_x, screen_t1_y, 1, doc.payload.nodes)
        assert handler.selected_node_id == "T1"

        # Drag 100px right, 60px down -> snaps to grid
        handler.on_mouse_move(screen_t1_x + 100.0, screen_t1_y + 60.0, doc.payload.nodes)
        assert n1.frame.x == 9900.0
        assert n1.frame.y == 8560.0
        handler.on_mouse_up(screen_t1_x + 100.0, screen_t1_y + 60.0, 1, doc.payload.nodes)

        # 4. Render to 1080p ImageSurface
        width, height = 1920, 1080
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)

        renderer.render(
            cr=cr,
            width=width,
            height=height,
            nodes=doc.payload.nodes,
            connections=doc.payload.connections,
            selected_node_id=handler.selected_node_id,
        )
        surface.flush()

        assert surface.get_width() == 1920
        assert surface.get_height() == 1080

        # 5. Zoom in at (960, 540)
        vp.zoom_at(1.5, 960.0, 540.0)
        renderer.render(
            cr=cr,
            width=width,
            height=height,
            nodes=doc.payload.nodes,
            connections=doc.payload.connections,
            selected_node_id=handler.selected_node_id,
        )
        surface.flush()

        assert surface.get_width() == 1920
