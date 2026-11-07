"""
Tests for Sprint 7 Context Menus (Canvas + Node) and 4-Pill Bottom Controls.
Matches macOS uploaded screenshots:
- uploaded_media_0: 4 bottom pills (Floors, Agent Rings, Minimap, Zoom).
- uploaded_media_1: Canvas background right-click context menu with Add > Terminal presets, Note, etc.
- uploaded_media_2: Node right-click context menu (Copy, Paste, Select All, Rename, Edit, Monitor, Blur, Delete, etc.).
"""
from __future__ import annotations

import os
from pathlib import Path
import time
import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from maestri_canvas.geometry import Point
from maestri_canvas.interaction import Background, NodeHeader
from maestri_core.client.api import MaestriClient
from ui.context_menus import CanvasContextMenu, NodeContextMenu
from ui.floating_bars import CanvasBottomRightControls, AgentActivityRingsWidget
from ui.window import OpenMaestriWindow


@pytest.fixture
def win():
    app = Gtk.Application(application_id="org.maestri.test.sprint7")
    client = MaestriClient(port=0)
    w = OpenMaestriWindow(app, client)
    w.wait_loaded()
    yield w
    w.close()


def pump_events(seconds: float = 0.05) -> None:
    deadline = time.time() + seconds
    ctx = GLib.MainContext.default()
    while time.time() < deadline:
        ctx.iteration(False)


def test_bottom_controls_4_pills_and_agent_rings(win):
    bottom = win.bottom_controls
    assert isinstance(bottom, CanvasBottomRightControls)
    assert hasattr(bottom, "pill_floor")
    assert hasattr(bottom, "pill_agents")
    assert hasattr(bottom, "pill_minimap")
    assert hasattr(bottom, "pill_zoom")
    assert hasattr(bottom, "rings_widget")
    assert isinstance(bottom.rings_widget, AgentActivityRingsWidget)

    # Floor button exists as Pill 1 in bottom controls
    assert win.floor_button is not None
    assert "maestri-bottom-pill" in win.floor_button.get_css_classes()
    assert win.floor_button.get_parent() is bottom.pill_floor


def test_canvas_context_menu_structure_and_presets(win):
    pt = Point(100.0, 150.0)
    menu = CanvasContextMenu(win, pt)
    assert isinstance(menu, Gtk.Popover)
    assert "maestri-context-popover" in menu.get_css_classes()

    # Verify stack pages: main, add, terminal_presets, partituras
    assert menu.stack.get_child_by_name("main") is not None
    assert menu.stack.get_child_by_name("add") is not None
    assert menu.stack.get_child_by_name("terminal_presets") is not None
    assert menu.stack.get_child_by_name("partituras") is not None

    # Test preset creation via context menu
    n0 = len(win.document.payload.nodes)
    menu._create_terminal_preset("Claude Code", "claude")
    pump_events(0.1)
    assert len(win.document.payload.nodes) == n0 + 1
    new_node = win.document.payload.nodes[-1]
    assert new_node.content.name == "Claude Code"
    assert new_node.content.command == "claude"


def test_node_context_menu_items_and_actions(win):
    # Ensure at least one terminal node exists
    if not win.document.payload.nodes:
        win._create_node("terminal")
        pump_events(0.1)
    node = win.document.payload.nodes[0]

    menu = NodeContextMenu(win, node.id)
    assert isinstance(menu, Gtk.Popover)
    assert "maestri-context-popover" in menu.get_css_classes()

    # Test inline rename trigger
    menu._on_rename()
    pump_events(0.05)

    # Test toggle monitor activity
    curr = getattr(node.content, "monitor_activity", True)
    menu._on_toggle_monitor()
    pump_events(0.05)
    assert getattr(node.content, "monitor_activity", True) != curr

    # Test blur toggle
    frame = win.canvas_widget.frames.get(node.id.upper())
    if frame:
        assert not frame.has_css_class("maestri-node-blurred")
        menu._on_toggle_blur()
        assert frame.has_css_class("maestri-node-blurred")
        menu._on_toggle_blur()
        assert not frame.has_css_class("maestri-node-blurred")

    # Test duplicate
    n_before = len(win.document.payload.nodes)
    menu._on_duplicate()
    pump_events(0.1)
    assert len(win.document.payload.nodes) >= n_before + 1
