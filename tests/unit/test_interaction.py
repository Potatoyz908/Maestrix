"""
Unit tests for Canvas interaction handling:
- Hit testing on nodes
- Resize handle detection
- Drag translation and selection
"""

import pytest
from canvas_cairo import Viewport, CanvasInteractionHandler
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent


class TestInteraction:
    def test_node_hit_testing(self):
        vp = Viewport(origin_x=0.0, origin_y=0.0, zoom=1.0)
        handler = CanvasInteractionHandler(vp)

        node = CanvasNode(
            id="node-1",
            frame=NodeFrame(x=100.0, y=100.0, width=200.0, height=150.0),
            node_type="terminal",
            content=TerminalContent(name="t1"),
        )
        nodes = [node]

        # Inside node bounds (canvas coordinates: 150, 150) -> screen (150, 150)
        hit_node = handler.hit_test_node(150.0, 150.0, nodes)
        assert hit_node is not None
        assert hit_node.id == "node-1"

        # Outside node bounds
        hit_none = handler.hit_test_node(50.0, 50.0, nodes)
        assert hit_none is None

        hit_none2 = handler.hit_test_node(350.0, 300.0, nodes)
        assert hit_none2 is None

    def test_resize_handle_hit_testing(self):
        vp = Viewport(origin_x=0.0, origin_y=0.0, zoom=1.0)
        handler = CanvasInteractionHandler(vp)

        node = CanvasNode(
            id="node-1",
            frame=NodeFrame(x=100.0, y=100.0, width=200.0, height=150.0),
            node_type="terminal",
            content=TerminalContent(name="t1"),
        )

        # Bottom-right corner (canvas: 300, 250) -> screen (300, 250)
        handle = handler.hit_test_handle(300.0, 250.0, node)
        assert handle == "se"

        # Top-left corner (canvas: 100, 100)
        handle_tl = handler.hit_test_handle(100.0, 100.0, node)
        assert handle_tl == "nw"

        # Middle of node -> no handle
        handle_mid = handler.hit_test_handle(200.0, 175.0, node)
        assert handle_mid is None

    def test_selection_and_drag(self):
        vp = Viewport(origin_x=0.0, origin_y=0.0, zoom=1.0)
        handler = CanvasInteractionHandler(vp)

        node = CanvasNode(
            id="node-1",
            frame=NodeFrame(x=100.0, y=100.0, width=200.0, height=150.0),
            node_type="terminal",
            content=TerminalContent(name="t1"),
        )
        nodes = [node]

        # Click inside node -> should select it
        handler.on_mouse_down(150.0, 150.0, 1, nodes)
        assert handler.selected_node_id == "node-1"
        assert handler.is_dragging_node is True

        # Drag 50px right, 30px down -> snaps to 20px grid (150 -> 160, 130 -> 120)
        handler.on_mouse_move(200.0, 180.0, nodes)
        assert node.frame.x == 160.0
        assert node.frame.y == 120.0

        # Release mouse
        handler.on_mouse_up(200.0, 180.0, 1, nodes)
        assert handler.is_dragging_node is False
