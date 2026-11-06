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
            ("select", "maestri-pointer-symbolic", "Select & Move (V)"),
            ("terminal", "maestri-terminal-symbolic", "New Terminal (T)"),
            ("stickyNote", "maestri-note-symbolic", "New Sticky Note (N)"),
            ("file", "maestri-paperclip-symbolic", "Attach File"),
            ("fileTree", "maestri-file-tree-symbolic", "New File Tree (F)"),
            ("portal", "maestri-globe-symbolic", "New Web Portal (P)"),
            ("text", "maestri-text-symbolic", "Text Label"),
            ("draw", "maestri-pencil-symbolic", "Drawing & Shapes"),
        ]

        for tid, icon_name, tooltip in tools:
            btn = Gtk.Button()
            img = Gtk.Image.new_from_icon_name(icon_name)
            img.set_pixel_size(16)
            btn.set_child(img)
            btn.add_css_class("maestri-island-btn")
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", lambda _b, t=tid: self._on_tool_clicked(t))
            self.tool_buttons[tid] = btn
            pill.append(btn)

        self.append(pill)

        # 2. Contextual Actions Pill (Sliders, Link, Duplicate, Trash)
        self.context_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.context_pill.add_css_class("maestri-floating-pill")
        self.context_pill.add_css_class("maestri-context-actions-pill")
        self.context_pill.set_halign(Gtk.Align.CENTER)

        btn_edit = Gtk.Button()
        img_edit = Gtk.Image.new_from_icon_name("maestri-sliders-symbolic")
        img_edit.set_pixel_size(15)
        btn_edit.set_child(img_edit)
        btn_edit.add_css_class("maestri-island-btn")
        btn_edit.set_tooltip_text("Edit Node Name & Settings")
        btn_edit.connect("clicked", self._on_edit_clicked)
        self.context_pill.append(btn_edit)

        btn_conn = Gtk.Button()
        img_conn = Gtk.Image.new_from_icon_name("maestri-link-symbolic")
        img_conn.set_pixel_size(15)
        btn_conn.set_child(img_conn)
        btn_conn.add_css_class("maestri-island-btn")
        btn_conn.set_tooltip_text("Connect Nodes")
        btn_conn.connect("clicked", self._on_connect_clicked)
        self.context_pill.append(btn_conn)

        btn_dup = Gtk.Button()
        img_dup = Gtk.Image.new_from_icon_name("maestri-duplicate-symbolic")
        img_dup.set_pixel_size(15)
        btn_dup.set_child(img_dup)
        btn_dup.add_css_class("maestri-island-btn")
        btn_dup.set_tooltip_text("Duplicate Selection (Alt+Drag)")
        btn_dup.connect("clicked", self._on_duplicate_clicked)
        self.context_pill.append(btn_dup)

        btn_del = Gtk.Button()
        img_del = Gtk.Image.new_from_icon_name("maestri-trash-symbolic")
        img_del.set_pixel_size(15)
        btn_del.set_child(img_del)
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
    """Draws Apple Activity-style dynamic progress rings representing
    token and quota usage for active terminal AI models in the workspace:
    - Ring 1 (Coral / Orange #E07A5F): Claude Code (% context / quota)
    - Ring 2 (Emerald / Teal #2A9D8F): Codex (% context / quota)
    - Ring 3 (Royal Blue #3A86FF): Gemini CLI (% context / quota)
    """

    def __init__(self, window=None) -> None:
        super().__init__()
        self.window = window
        self.set_content_width(78)
        self.set_content_height(26)

        # Dynamic model quota & token usage states
        self.models: dict[str, dict[str, Any]] = {
            "claude": {
                "name": "Claude Code",
                "ratio": 0.68,
                "used": "136k",
                "max": "200k",
                "color": (0.94, 0.45, 0.35),       # Coral / Orange
                "cx": 15,
            },
            "codex": {
                "name": "Codex",
                "ratio": 0.42,
                "used": "54k",
                "max": "128k",
                "color": (0.18, 0.76, 0.62),       # Emerald / Teal
                "cx": 39,
            },
            "gemini": {
                "name": "Gemini CLI",
                "ratio": 0.25,
                "used": "250k",
                "max": "1.0M",
                "color": (0.23, 0.58, 1.00),       # Royal Blue
                "cx": 63,
            },
        }

        self.set_draw_func(self._draw_func)
        self.update_tooltip()

    def set_model_usage(self, model_id: str, ratio: float, used: str, max_tokens: str) -> None:
        if model_id in self.models:
            self.models[model_id]["ratio"] = max(0.0, min(1.0, ratio))
            self.models[model_id]["used"] = used
            self.models[model_id]["max"] = max_tokens
            self.update_tooltip()
            self.queue_draw()

    def update_tooltip(self) -> None:
        lines = ["Model Quotas & Context Usage:"]
        for m in self.models.values():
            pct = int(round(m["ratio"] * 100))
            lines.append(f"• {m['name']}: {pct}% ({m['used']} / {m['max']})")
        tip = "\n".join(lines)
        self.set_tooltip_text(tip)

    def _draw_func(self, area, cr, width, height) -> None:
        import cairo
        cy = height / 2.0
        radius = 8.0
        line_w = 2.4

        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        for m in self.models.values():
            cx = m["cx"]
            r, g, b = m["color"]
            ratio = max(0.02, min(1.0, m["ratio"]))

            # 1. Background ring track (translucent)
            cr.save()
            cr.set_line_width(line_w)
            cr.set_source_rgba(r, g, b, 0.22)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.stroke()
            cr.restore()

            # 2. Dynamic progress arc (starting at -pi/2, top of clock)
            start_angle = -0.5 * math.pi
            end_angle = start_angle + (ratio * 2 * math.pi)

            cr.save()
            cr.set_line_width(line_w)
            cr.set_source_rgb(r, g, b)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.stroke()

            # 3. Inner core dot
            cr.set_source_rgba(r, g, b, 0.8)
            cr.arc(cx, cy, 1.6, 0, 2 * math.pi)
            cr.fill()
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

        # Pill 1: Floor Button Capsule (holds FloorButton from install_floors)
        self.pill_floor = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.btn_floors = None
        self.append(self.pill_floor)

        # Pill 2: Agent Activity Rings (Claude Code, Codex, Gemini CLI)
        self.pill_agents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pill_agents.add_css_class("maestri-bottom-pill")
        self.rings_widget = AgentActivityRingsWidget(window)
        self.pill_agents.append(self.rings_widget)
        self.pill_agents.set_tooltip_text(self.rings_widget.get_tooltip_text())
        self.append(self.pill_agents)

        # Pill 3: Minimap Toggle Button Capsule
        self.pill_minimap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.pill_minimap.add_css_class("maestri-bottom-pill")
        img_map = Gtk.Image.new_from_icon_name("maestri-map-symbolic")
        img_map.set_pixel_size(15)
        self.btn_minimap = Gtk.Button(child=img_map)
        self.btn_minimap.add_css_class("maestri-island-btn")
        self.btn_minimap.set_tooltip_text("Toggle Mini Map / Reset View")
        self.btn_minimap.connect("clicked", self._on_minimap_clicked)
        self.pill_minimap.append(self.btn_minimap)
        self.append(self.pill_minimap)

        # Pill 4: Zoom Controls Capsule [ —  XX%  + ]
        self.pill_zoom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pill_zoom.add_css_class("maestri-bottom-pill")
        self.pill_zoom.add_css_class("maestri-zoom-controls")

        img_out = Gtk.Image.new_from_icon_name("maestri-minus-symbolic")
        img_out.set_pixel_size(12)
        btn_zoom_out = Gtk.Button(child=img_out)
        btn_zoom_out.add_css_class("maestri-island-btn")
        btn_zoom_out.set_tooltip_text("Zoom Out (Ctrl+-)")
        btn_zoom_out.connect("clicked", lambda _b: self._zoom_step(0.8))
        self.pill_zoom.append(btn_zoom_out)

        self.zoom_lbl = Gtk.Label(label="100%")
        self.zoom_lbl.add_css_class("maestri-zoom-label")
        self.pill_zoom.append(self.zoom_lbl)

        img_in = Gtk.Image.new_from_icon_name("maestri-plus-symbolic")
        img_in.set_pixel_size(12)
        btn_zoom_in = Gtk.Button(child=img_in)
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
        if self.btn_floors:
            self.btn_floors.emit("clicked")
        elif hasattr(self.window, "floor_button") and self.window.floor_button:
            self.window.floor_button.emit("clicked")

    def _on_minimap_clicked(self, _btn) -> None:
        # Reset zoom to fit or toggle overview
        vp = self.cw.viewport
        vp.origin = Point(0, 0)
        vp.zoom = 1.0
        self.cw.controller.dirty = True
        self.cw.emit("viewport-changed", 0, 0, 1.0)
