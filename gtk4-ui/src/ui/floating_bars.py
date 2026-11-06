"""
Floating Island Toolbars for Open Maestri GTK 4 Native.
Provides:
1. CanvasTopToolbar: Top-center floating pill with the 8 primary creation/selection tools + selection actions.
2. CanvasBottomRightControls: Bottom-right floating pill with Floors, Minimap, and Zoom controls.
Matches the macOS reference layout (docs/images/home.png and docs/reference/official/03-canvas.md).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.geometry import Point, Rect
from maestri_core.signals.bus import bus


class CanvasTopToolbar(Gtk.Box):
    """Floating top-center island toolbar matching macOS Maestri."""

    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window = window
        self.cw = window.canvas_widget
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(16)

        # 1. Main Tools Pill
        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pill.add_css_class("maestri-floating-pill")
        pill.add_css_class("maestri-top-toolbar")

        self.tool_buttons: dict[str, Gtk.Button] = {}

        tools = [
            ("select", "↖", "Select & Move (V)"),
            ("terminal", ">_", "New Terminal (T)"),
            ("stickyNote", "📝", "New Sticky Note (N)"),
            ("file", "📎", "Attach File"),
            ("fileTree", "📁", "New File Tree (F)"),
            ("portal", "🌐", "New Web Portal (P)"),
            ("text", "Aa", "Text Label"),
            ("draw", "✏️", "Drawing & Shapes"),
        ]

        for tid, icon, tooltip in tools:
            btn = Gtk.Button(label=icon)
            btn.add_css_class("maestri-island-btn")
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", lambda _b, t=tid: self._on_tool_clicked(t))
            self.tool_buttons[tid] = btn
            pill.append(btn)

        self.append(pill)

        # 2. Contextual Actions Pill (Edit, Connect, Duplicate, Delete)
        self.context_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.context_pill.add_css_class("maestri-floating-pill")
        self.context_pill.add_css_class("maestri-context-actions-pill")
        self.context_pill.set_halign(Gtk.Align.CENTER)

        btn_edit = Gtk.Button(label="✏️")
        btn_edit.add_css_class("maestri-island-btn")
        btn_edit.set_tooltip_text("Edit Node Name & Settings")
        btn_edit.connect("clicked", self._on_edit_clicked)
        self.context_pill.append(btn_edit)

        btn_conn = Gtk.Button(label="🔗")
        btn_conn.add_css_class("maestri-island-btn")
        btn_conn.set_tooltip_text("Connect Nodes")
        btn_conn.connect("clicked", self._on_connect_clicked)
        self.context_pill.append(btn_conn)

        btn_dup = Gtk.Button(label="📋")
        btn_dup.add_css_class("maestri-island-btn")
        btn_dup.set_tooltip_text("Duplicate Selection (Alt+Drag)")
        btn_dup.connect("clicked", self._on_duplicate_clicked)
        self.context_pill.append(btn_dup)

        btn_del = Gtk.Button(label="🗑")
        btn_del.add_css_class("maestri-island-btn")
        btn_del.add_css_class("danger")
        btn_del.set_tooltip_text("Delete Selection (Del / Cmd+W)")
        btn_del.connect("clicked", self._on_delete_clicked)
        self.context_pill.append(btn_del)

        self.context_pill.set_visible(False)
        self.append(self.context_pill)

        # Connect to selection changed to toggle contextual actions
        self.cw.connect("selection-changed", self._on_selection_changed)
        self.cw.connect("tool-changed", self._on_canvas_tool_changed)

        # Active initial tool
        self._set_active_tool_btn("select")

    def _set_active_tool_btn(self, tool_id: str) -> None:
        for tid, btn in self.tool_buttons.items():
            if tid == tool_id:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def _on_tool_clicked(self, tool_id: str) -> None:
        self._set_active_tool_btn(tool_id)
        if tool_id == "select":
            self.cw.set_tool("select")
        elif tool_id == "terminal":
            self.window.open_new_terminal_dialog()
            self._set_active_tool_btn("select")
        elif tool_id == "stickyNote":
            self.window._create_node("stickyNote")
            self._set_active_tool_btn("select")
        elif tool_id == "file":
            self._choose_and_attach_file()
            self._set_active_tool_btn("select")
        elif tool_id == "fileTree":
            self.window._create_node("fileTree")
            self._set_active_tool_btn("select")
        elif tool_id == "portal":
            self.window._create_node("portal")
            self._set_active_tool_btn("select")
        elif tool_id == "text":
            self.cw.set_tool("text")
        elif tool_id == "draw":
            self.cw.set_tool("freehand_pen")

    def _on_canvas_tool_changed(self, _cw, tool: str) -> None:
        mapping = {"select": "select", "text": "text", "freehand_pen": "draw", "freehand_highlighter": "draw"}
        tid = mapping.get(tool, "select")
        self._set_active_tool_btn(tid)

    def _on_selection_changed(self, _cw, ids: tuple[str, ...]) -> None:
        has_sel = bool(ids)
        self.context_pill.set_visible(has_sel)

    def _on_duplicate_clicked(self, _btn) -> None:
        sel = list(self.cw.scene.selection)
        if not sel:
            return
        mapping = {}
        for nid in sel:
            item = self.cw.scene.get(nid)
            if item:
                f = item.frame
                mapping[nid] = Rect(f.x + 30, f.y + 30, f.w, f.h)
        if mapping:
            self.cw.emit("duplicate-requested", mapping)

    def _on_edit_clicked(self, _btn) -> None:
        if hasattr(self.window, "edit_selected_node"):
            self.window.edit_selected_node()

    def _on_connect_clicked(self, _btn) -> None:
        sel = list(self.cw.scene.selection)
        if len(sel) >= 2:
            self.cw.emit("connect-requested", sel[0], "right", sel[1], "left")
        elif len(sel) == 1:
            all_other = [n.id for n in self.cw.scene.nodes if n.id != sel[0]]
            if all_other:
                self.cw.emit("connect-requested", sel[0], "right", all_other[0], "left")
            else:
                self.window._create_node("terminal")
                nodes = [n.id for n in self.cw.scene.nodes if n.id != sel[0]]
                if nodes:
                    self.cw.emit("connect-requested", sel[0], "right", nodes[-1], "left")
        else:
            nodes = [n.id for n in self.cw.scene.nodes]
            if len(nodes) >= 2:
                self.cw.emit("connect-requested", nodes[0], "right", nodes[1], "left")

    def _on_delete_clicked(self, _btn) -> None:
        sel = tuple(self.cw.scene.selection)
        if sel:
            self.cw.emit("delete-requested", sel)

    def _choose_and_attach_file(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            title="Attach File to Canvas",
            parent=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Attach",
            cancel_label="Cancel",
        )

        def response(d, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = d.get_file()
                if gfile:
                    path = gfile.get_path()
                    if path:
                        # Drop onto canvas center
                        cw = self.window.canvas_widget
                        center_x = cw.viewport.origin.x + cw.get_width() / 2 / cw.viewport.zoom
                        center_y = cw.viewport.origin.y + cw.get_height() / 2 / cw.viewport.zoom
                        cw.emit("files-dropped", [path], center_x, center_y)

        dialog.connect("response", response)
        dialog.show()


class AgentActivityRingsWidget(Gtk.DrawingArea):
    """Draws the 3 macOS agent activity rings matching uploaded_media_0:
    - Ring 1 (coral/orange): notched active loop with central core.
    - Ring 2 (teal/green): circular ring with bright active segment.
    - Ring 3 (royal blue): concentric circular arc spiral.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(74)
        self.set_content_height(26)
        self.set_draw_func(self._draw_func)

    def _draw_func(self, area, cr, width, height) -> None:
        cy = height / 2.0

        # Ring 1: Coral / Orange (#E07A5F)
        cr.save()
        cr.set_line_width(2.2)
        cr.set_source_rgba(0.88, 0.48, 0.37, 0.35)
        cr.arc(15, cy, 7.5, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgb(0.92, 0.52, 0.40)
        cr.arc(15, cy, 7.5, -0.75 * math.pi, 0.85 * math.pi)
        cr.stroke()
        cr.arc(15, cy, 1.8, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # Ring 2: Emerald / Teal (#2A9D8F)
        cr.save()
        cr.set_line_width(2.2)
        cr.set_source_rgba(0.16, 0.61, 0.56, 0.35)
        cr.arc(37, cy, 7.5, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgb(0.25, 0.88, 0.68)
        cr.arc(37, cy, 7.5, -0.6 * math.pi, 0.15 * math.pi)
        cr.stroke()
        cr.restore()

        # Ring 3: Royal Blue (#3A86FF)
        cr.save()
        cr.set_line_width(2.0)
        cr.set_source_rgba(0.23, 0.53, 1.0, 0.35)
        cr.arc(59, cy, 7.5, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgb(0.28, 0.58, 1.0)
        cr.arc(59, cy, 7.5, -0.8 * math.pi, 0.45 * math.pi)
        cr.stroke()
        cr.arc(59, cy, 4.2, -0.2 * math.pi, 1.25 * math.pi)
        cr.stroke()
        cr.restore()


class CanvasBottomRightControls(Gtk.Box):
    """Floating bottom-right controls matching macOS Maestri (Floors, Agent Rings, Minimap, Zoom).
    Separated into 4 distinct dark-glass capsules (pills) side-by-side (uploaded_media_0).
    """

    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.window = window
        self.cw = window.canvas_widget
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_margin_end(16)
        self.set_margin_bottom(16)

        # Retained compatibility button for floors (delegates to window.floor_button)
        self.btn_floors = Gtk.Button(label="🥞")
        self.btn_floors.connect("clicked", self._on_floors_clicked)

        # Pill 2: Agent Activity Rings (Claude Code, Codex, Gemini CLI)
        self.pill_agents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pill_agents.add_css_class("maestri-bottom-pill")
        self.pill_agents.set_tooltip_text("Active Canvas Agents (Claude Code, Codex, Gemini CLI)")
        self.rings_widget = AgentActivityRingsWidget()
        self.pill_agents.append(self.rings_widget)
        self.append(self.pill_agents)

        # Pill 3: Minimap Toggle Button Capsule
        self.pill_minimap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.pill_minimap.add_css_class("maestri-bottom-pill")
        self.btn_minimap = Gtk.Button(label="🗺")
        self.btn_minimap.add_css_class("maestri-island-btn")
        self.btn_minimap.set_tooltip_text("Toggle Mini Map / Reset View")
        self.btn_minimap.connect("clicked", self._on_minimap_clicked)
        self.pill_minimap.append(self.btn_minimap)
        self.append(self.pill_minimap)

        # Pill 4: Zoom Controls Capsule [ —  XX%  + ]
        self.pill_zoom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pill_zoom.add_css_class("maestri-bottom-pill")
        self.pill_zoom.add_css_class("maestri-zoom-controls")

        btn_zoom_out = Gtk.Button(label="−")
        btn_zoom_out.add_css_class("maestri-island-btn")
        btn_zoom_out.set_tooltip_text("Zoom Out (Ctrl+-)")
        btn_zoom_out.connect("clicked", lambda _b: self._zoom_step(0.8))
        self.pill_zoom.append(btn_zoom_out)

        self.zoom_lbl = Gtk.Label(label="100%")
        self.zoom_lbl.add_css_class("maestri-zoom-label")
        self.pill_zoom.append(self.zoom_lbl)

        btn_zoom_in = Gtk.Button(label="+")
        btn_zoom_in.add_css_class("maestri-island-btn")
        btn_zoom_in.set_tooltip_text("Zoom In (Ctrl++)")
        btn_zoom_in.connect("clicked", lambda _b: self._zoom_step(1.25))
        self.pill_zoom.append(btn_zoom_in)

        self.append(self.pill_zoom)

        # Track viewport zoom changes
        self.cw.connect("viewport-changed", self._on_viewport_changed)
        self._update_zoom_label(self.cw.viewport.zoom)

    def _zoom_step(self, factor: float) -> None:
        vp = self.cw.viewport
        new_zoom = max(0.1, min(4.0, vp.zoom * factor))
        # Keep center pinned
        w = max(1.0, float(self.cw.get_width()))
        h = max(1.0, float(self.cw.get_height()))
        cx = vp.origin.x + w / (2.0 * vp.zoom)
        cy = vp.origin.y + h / (2.0 * vp.zoom)
        vp.zoom = new_zoom
        vp.origin = Point(cx - w / (2.0 * vp.zoom), cy - h / (2.0 * vp.zoom))
        self.cw.controller.dirty = True
        self.cw.emit("viewport-changed", vp.origin.x, vp.origin.y, vp.zoom)

    def _update_zoom_label(self, zoom: float) -> None:
        pct = int(round(zoom * 100))
        self.zoom_lbl.set_label(f"{pct}%")

    def _on_viewport_changed(self, _cw, _x, _y, zoom) -> None:
        self._update_zoom_label(zoom)

    def _on_floors_clicked(self, _btn) -> None:
        if hasattr(self.window, "floors") and hasattr(self.window.floors, "button"):
            # Trigger the floors popover directly
            self.window.floors.button.emit("clicked")

    def _on_minimap_clicked(self, _btn) -> None:
        # Reset zoom to fit or toggle overview
        vp = self.cw.viewport
        vp.origin = Point(0, 0)
        vp.zoom = 1.0
        self.cw.controller.dirty = True
        self.cw.emit("viewport-changed", 0, 0, 1.0)
