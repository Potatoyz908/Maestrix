"""
Floating Island Toolbars for Maestrix GTK 4 Native.
Provides:
1. CanvasTopToolbar: Top-center floating pill with the 8 primary creation/selection tools + selection actions.
2. CanvasBottomRightControls: four bottom-right capsules — Floor selector, agent rings, Minimap, Zoom (− % +).
Matches the macOS reference layout (docs/images/home.png and docs/reference/official/03-canvas.md).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.constants import CANVAS_MAX_ZOOM, CANVAS_MIN_ZOOM, CANVAS_ZOOM_STEP
from maestri_canvas.geometry import Point, Rect
from maestri_core.signals.bus import bus


# The two top islands (tools + node actions) as in macOS Maestri: a dark translucent pill, icons with NO button
# background (the theme's default button gradient is cleared with background-image: none), the active tool is only
# tinted blue. Scoped to these pills so the rest of the app keeps its own button style.
TOP_CSS = """
.maestri-top-toolbar, .maestri-context-actions-pill {
    background-color: rgba(28, 28, 30, 0.86);
    background-image: none;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 18px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
    padding: 4px 10px;
}
.maestri-top-toolbar button.maestri-island-btn,
.maestri-context-actions-pill button.maestri-island-btn {
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    outline: none;
    border-radius: 8px;
    min-width: 0;
    min-height: 0;
    padding: 6px 9px;
    color: rgba(235, 235, 245, 0.80);
}
/* The grey squares behind the glyphs were painted on the inner `image` node (found by painting each node of the button),
   not on the button: clear it too. */
.maestri-top-toolbar button.maestri-island-btn image,
.maestri-context-actions-pill button.maestri-island-btn image {
    background: none;
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    outline: none;
    padding: 0;
    margin: 0;
    -gtk-icon-shadow: none;
}
.maestri-top-toolbar button.maestri-island-btn:hover,
.maestri-context-actions-pill button.maestri-island-btn:hover {
    background-color: rgba(255, 255, 255, 0.07);
    color: #ffffff;
}
.maestri-top-toolbar button.maestri-island-btn.active,
.maestri-context-actions-pill button.maestri-island-btn.active {
    background-color: transparent;
    color: #0A84FF;
}
.maestri-context-actions-pill button.maestri-island-btn.danger:hover {
    background-color: transparent;
    color: #FF453A;
}
"""
_top_css_installed = False


def _install_top_css() -> None:
    global _top_css_installed
    display = Gdk.Display.get_default()
    if _top_css_installed or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(TOP_CSS)
    # USER + 1, not APPLICATION: a user GTK theme (e.g. WhiteSur in ~/.config/gtk-4.0) loads at USER priority and gives
    # icon buttons its own background, which is what painted grey squares behind the glyphs. The window's generic
    # .maestri-floating-pill / .maestri-island-btn rules (APPLICATION) also lose to it. context_toolbar.py does the same.
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    _top_css_installed = True


class CanvasTopToolbar(Gtk.Box):
    """Floating top-center island toolbar matching macOS Maestri."""

    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _install_top_css()
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

        # macOS CanvasToolbar.swift order + canvas.toolbar.* texts; a shortcut is named only when the key exists
        # (interaction.py: V, T, P, L). Note / File Tree / Browser are "tool + drag a frame" like Terminal.
        tools = [
            ("select", "maestri-pointer-symbolic", "Select (V)"),
            ("terminal", "maestri-terminal-symbolic", "Terminal (T)"),
            ("stickyNote", "maestri-note-symbolic", "Note"),
            ("file", "maestri-paperclip-symbolic", "Attach File"),
            ("fileTree", "maestri-file-tree-symbolic", "File Tree"),
            ("portal", "maestri-globe-symbolic", "Browser (P)"),
            ("text", "maestri-text-symbolic", "Text Label"),
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

        # Draw: the DrawingTools popover (Pen, Highlighter, Rectangle, Ellipse, Diamond - ShapeSubtoolbar.swift)
        drawing = getattr(window, "drawing", None)
        if drawing is not None:
            draw_btn = drawing.button
            if draw_btn.get_parent() is not None:
                draw_btn.unparent()
            draw_btn.add_css_class("maestri-island-btn")
            draw_btn.set_has_frame(False)
            self.tool_buttons["draw"] = draw_btn
            pill.append(draw_btn)

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
        btn_edit.set_tooltip_text("Edit")
        btn_edit.connect("clicked", self._on_edit_clicked)
        self.context_pill.append(btn_edit)

        btn_conn = Gtk.Button()
        img_conn = Gtk.Image.new_from_icon_name("maestri-link-symbolic")
        img_conn.set_pixel_size(15)
        btn_conn.set_child(img_conn)
        btn_conn.add_css_class("maestri-island-btn")
        btn_conn.set_tooltip_text("Start Connection (L)")
        btn_conn.connect("clicked", self._on_connect_clicked)
        self.context_pill.append(btn_conn)

        btn_dup = Gtk.Button()
        img_dup = Gtk.Image.new_from_icon_name("maestri-duplicate-symbolic")
        img_dup.set_pixel_size(15)
        btn_dup.set_child(img_dup)
        btn_dup.add_css_class("maestri-island-btn")
        btn_dup.set_tooltip_text("Duplicate")
        btn_dup.connect("clicked", self._on_duplicate_clicked)
        self.context_pill.append(btn_dup)

        btn_del = Gtk.Button()
        img_del = Gtk.Image.new_from_icon_name("maestri-trash-symbolic")
        img_del.set_pixel_size(15)
        btn_del.set_child(img_del)
        btn_del.add_css_class("maestri-island-btn")
        btn_del.add_css_class("danger")
        btn_del.set_tooltip_text("Delete (Del)")
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
            self.cw.set_tool("terminal")
        elif tool_id in ("stickyNote", "fileTree", "portal"):   # tool + drag a frame (DrawingTools creates the node)
            self.cw.set_tool(tool_id)
            self.cw.grab_focus()   # Esc drops the tool
        elif tool_id == "file":
            self._choose_and_attach_file()
            self._set_active_tool_btn("select")
        elif tool_id == "text":
            self.cw.set_tool("text")

    def _on_canvas_tool_changed(self, _cw, tool: str) -> None:
        draw = {"freehand_pen", "freehand_highlighter", "rect", "ellipse", "diamond"}
        tid = "draw" if tool in draw else tool if tool in self.tool_buttons else "select"
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
        """Start Connection (L): a rope from the selected node follows the pointer; clicking another node connects (K9).
        Two selected nodes are connected directly."""
        sel = sorted(self.cw.scene.selection)
        if len(sel) == 2:
            self.cw.emit("connect-requested", sel[0], "right", sel[1], "left")
        elif len(sel) == 1:
            self.cw.begin_connect_mode(sel[0])

    def _on_delete_clicked(self, _btn) -> None:
        sel = tuple(self.cw.scene.selection)
        if sel:
            self.cw.request_delete(sel)

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


def _cairo_foreign_available() -> bool:
    try:
        gi.require_foreign("cairo")
        return True
    except ImportError:
        return False


_USE_DRAW_FUNC = _cairo_foreign_available()


class CairoPaintArea(Gtk.DrawingArea):
    """DrawingArea whose `paint(cr, w, h)` works with or without python3-gi-cairo: with the foreign-struct bridge it is the
    regular draw func; without it (set_draw_func raises "Couldn't find foreign struct converter for 'cairo.Context'"), the
    same routine draws into an ImageSurface uploaded as a texture (the canvas' CairoLayer does the same)."""
    __gtype_name__ = "MaestriCairoPaintArea"

    def __init__(self) -> None:
        super().__init__()
        if _USE_DRAW_FUNC:
            self.set_draw_func(lambda _a, cr, w, h: self.paint(cr, w, h))

    def paint(self, cr, width: int, height: int) -> None:   # pragma: no cover - overridden
        pass

    def do_snapshot(self, snapshot) -> None:
        if _USE_DRAW_FUNC:
            Gtk.DrawingArea.do_snapshot(self, snapshot)
            return
        import cairo
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene
        w, h, sc = self.get_width(), self.get_height(), self.get_scale_factor()
        if w <= 0 or h <= 0:
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * sc, h * sc)
        surf.set_device_scale(sc, sc)
        self.paint(cairo.Context(surf), w, h)
        surf.flush()
        tex = Gdk.MemoryTexture.new(w * sc, h * sc, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                    GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())
        snapshot.append_texture(tex, Graphene.Rect().init(0, 0, w, h))


def terminal_activity_counts(nodes, frames):
    """Observed busy terminals / present terminals; never token or provider quota estimates."""
    counts = {"claude": [0, 0], "codex": [0, 0], "gemini": [0, 0]}
    aliases = {"claude_code": "claude", "codex": "codex", "gemini_cli": "gemini"}
    from maestri_core.models.node import agent_type_for_command
    for node in nodes:
        if node.node_type != "terminal":
            continue
        c = node.content
        kind = aliases.get(getattr(c, "agent_type", "")) or aliases.get(agent_type_for_command(c.command))
        if kind is None:
            continue
        counts[kind][1] += 1
        frame = frames.get(node.id.upper())
        features = getattr(frame, "terminal_features", None)
        if features is not None and features.busy and features.exited is None:
            counts[kind][0] += 1
    return counts


AGENT_KINDS = (("claude_code", "Claude Code"), ("codex", "Codex"), ("gemini_cli", "Gemini CLI"),
               ("antigravity", "Antigravity"), ("open_code", "OpenCode"))
ACTIVITY_POLL_MS = 2000


def agent_activity_rows(nodes, frames) -> list[tuple[str, list[dict]]]:
    """(label, terminals) per agent kind, in AGENT_KINDS order; each terminal = {id, name, state, cwd}, state in
    busy / idle / exited. Same source as the rings: observed output activity, never quotas."""
    import os
    from maestri_core.models.node import agent_type_for_command
    by_kind: dict[str, list[dict]] = {k: [] for k, _ in AGENT_KINDS}
    for node in nodes:
        if node.node_type != "terminal":
            continue
        c = node.content
        kind = getattr(c, "agent_type", "") or ""
        if kind not in by_kind:
            kind = agent_type_for_command(getattr(c, "command", "") or "")
        if kind not in by_kind:
            words = (getattr(c, "command", "") or "").split()
            kind = "antigravity" if any(os.path.basename(w).lower() == "antigravity" for w in words[:3]) else ""
        if kind not in by_kind:
            continue
        f = getattr(frames.get(node.id.upper()), "terminal_features", None)
        state = "exited" if f is not None and f.exited is not None else ("busy" if f is not None and f.busy else "idle")
        by_kind[kind].append({"id": node.id.upper(), "name": getattr(c, "name", "") or "Terminal", "state": state,
                              "cwd": getattr(c, "working_directory", "") or ""})
    return [(label, by_kind[k]) for k, label in AGENT_KINDS]


class AgentActivityPopover(Gtk.Popover):
    """What the activity capsule opens: per agent kind, "N of M active" and each terminal (name, busy/idle, directory).
    Refreshed on "terminal-activity" and, only while open, every ACTIVITY_POLL_MS; both stop when it closes."""

    def __init__(self, window) -> None:
        super().__init__(position=Gtk.PositionType.TOP, css_classes=["maestri-activity-popover"])
        self.window = window
        self._timer = 0
        self._handler = 0
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=8, margin_bottom=8,
                           margin_start=8, margin_end=8, width_request=300)
        self.set_child(self.box)
        self.connect("show", self._opened)
        self.connect("closed", lambda _p: self._stop())

    def _opened(self, *_a) -> None:
        self.rebuild()
        cw = getattr(self.window, "canvas_widget", None)
        if cw is not None and not self._handler:
            self._handler = cw.connect("terminal-activity", lambda *_x: self.rebuild())
        if not self._timer:
            self._timer = GLib.timeout_add(ACTIVITY_POLL_MS, self._tick)

    def _tick(self) -> bool:
        self.rebuild()
        return GLib.SOURCE_CONTINUE

    def _stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        cw = getattr(self.window, "canvas_widget", None)
        if self._handler and cw is not None:
            if cw.handler_is_connected(self._handler):
                cw.disconnect(self._handler)
        self._handler = 0

    cleanup = _stop

    def rows(self) -> list[tuple[str, list[dict]]]:
        w = self.window
        if w is None:
            return [(label, []) for _k, label in AGENT_KINDS]
        return agent_activity_rows(w.document.payload.nodes, w.canvas_widget.frames)

    def rebuild(self) -> None:
        child = self.box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt
        self.box.append(Gtk.Label(label="AGENT ACTIVITY", xalign=0, css_classes=["maestri-activity-title"]))
        groups = self.rows()
        if not any(terms for _l, terms in groups):
            self.box.append(Gtk.Label(label="No agent terminals in this workspace", xalign=0,
                                      css_classes=["maestri-activity-empty"]))
        for label, terms in groups:
            if not terms:
                continue
            active = sum(1 for t in terms if t["state"] == "busy")
            head = Gtk.Box(spacing=6, css_classes=["maestri-activity-kind"])
            head.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            head.append(Gtk.Label(label=f"{active} of {len(terms)} active", css_classes=["maestri-activity-count"]))
            self.box.append(head)
            for t in terms:
                self.box.append(self._row(t))
        self.box.append(Gtk.Label(label="Activity = recent terminal output. Token quotas are not available.",
                                  xalign=0, wrap=True, max_width_chars=40, css_classes=["maestri-activity-note"]))

    def _row(self, t: dict) -> Gtk.Widget:
        dot = Gtk.Image.new_from_icon_name("maestri-circle-symbolic")
        dot.set_pixel_size(8)
        dot.add_css_class("maestri-activity-dot")
        dot.add_css_class(t["state"])
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        texts.append(Gtk.Label(label=t["name"], xalign=0, ellipsize=3))
        if t["cwd"]:
            texts.append(Gtk.Label(label=t["cwd"], xalign=0, ellipsize=2, css_classes=["maestri-activity-cwd"]))
        state = Gtk.Label(label={"busy": "Busy", "idle": "Idle", "exited": "Exited"}[t["state"]],
                          css_classes=["maestri-activity-state", t["state"]])
        row = Gtk.Box(spacing=8)
        for w in (dot, texts, state):
            row.append(w)
        btn = Gtk.Button(child=row, has_frame=False, css_classes=["flat", "maestri-activity-row"],
                         tooltip_text="Show this terminal")
        btn.connect("clicked", lambda _b, nid=t["id"]: self._show(nid))
        return btn

    def _show(self, node_id: str) -> None:
        cw = self.window.canvas_widget
        order = cw.terminal_order()
        if node_id in order and order.index(node_id) < 9:
            cw.jump_to_terminal(order.index(node_id) + 1)
        self.popdown()


class AgentActivityRingsWidget(CairoPaintArea):
    """Live output activity of Claude, Codex and Gemini terminals in this workspace."""
    def __init__(self, window=None) -> None:
        super().__init__()
        self.window = window
        self.set_content_width(78)
        self.set_content_height(26)
        self.models = {
            "claude": dict(name="Claude Code", ratio=0, active=0, total=0, color=(0.94, 0.45, 0.35), cx=15),
            "codex": dict(name="Codex", ratio=0, active=0, total=0, color=(0.18, 0.76, 0.62), cx=39),
            "gemini": dict(name="Gemini CLI", ratio=0, active=0, total=0, color=(0.23, 0.58, 1.00), cx=63),
        }
        self._counts = None
        self.refresh()

    def refresh(self) -> None:
        win = self.window
        nodes = win.document.payload.nodes if win is not None else []
        frames = win.canvas_widget.frames if win is not None else {}
        counts = terminal_activity_counts(nodes, frames)
        if counts == self._counts:
            return
        self._counts = counts
        for kind, (active, total) in counts.items():
            self.models[kind].update(active=active, total=total, ratio=active / total if total else 0)
        self.update_tooltip()
        self.queue_draw()

    def update_tooltip(self) -> None:
        self.set_tooltip_text("Terminal activity (recent output):\n" + "\n".join(
            f"{m['name']}: {m['active']} of {m['total']} terminals active" for m in self.models.values()))

    def paint(self, cr, width, height) -> None:
        self._draw_func(self, cr, width, height)

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
            ratio = max(0.0, min(1.0, m["ratio"]))

            # 1. Background ring track (translucent)
            cr.save()
            cr.set_line_width(line_w)
            cr.set_source_rgba(r, g, b, 0.22)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.stroke()
            cr.restore()

            if ratio == 0:
                continue

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


# ---------------------------------------------------------------------------------------------------------------------
# Bottom-right capsules (Sprint 8 D1-D5). macOS reference: WorkspaceCanvasView.swift (bottom HStack) +
# CanvasMinimapPopover.swift + Constants.canvasZoomStep. All glyphs are outline SVGs of gtk4-ui/src/ui/icons (no emoji).
# ---------------------------------------------------------------------------------------------------------------------
ZOOM_PRESETS = (0.5, 0.75, 1.0, 1.5, 2.0)
CAPSULE_H = 32                 # every capsule has the same height, so their centers line up (D1)
MINIMAP_SIZE = (280, 180)      # CanvasMinimapPopover.mapSize
MINIMAP_PAD = 100.0            # computeBounds() padding (canvas units)
JUMP_MS = 220                  # canvasJumpToOrigin animates; ease-out over this duration

# CanvasMinimapPopover.nodeColor(for:)
MINIMAP_COLORS: Dict[str, tuple] = {
    "terminal": (0.04, 0.52, 1.0), "stickyNote": (0.6, 0.88, 0.88), "portal": (1.0, 0.92, 0.6),
    "fileTree": (1.0, 0.82, 0.6), "text": (0.7, 0.7, 0.9), "shape": (0.9, 0.75, 0.85),
    "stroke": (0.59, 0.51, 0.94), "freehand": (0.6, 0.85, 0.75),
}

BOTTOM_CSS = """
.maestri-bottom-controls .maestri-capsule {
    background-color: rgba(28, 28, 31, 0.86);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 16px;
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.05) inset, 0 6px 18px rgba(0, 0, 0, 0.32), 0 1px 3px rgba(0, 0, 0, 0.28);
    padding: 0 3px;
    min-height: 32px;
    color: #ececf0;
}
.maestri-bottom-controls .maestri-capsule:hover { background-color: rgba(28, 28, 31, 0.86); }
/* plain buttons and the inner button of a GtkMenuButton (menubutton > button) share one look */
.maestri-bottom-controls button.maestri-capsule-btn,
.maestri-bottom-controls menubutton.maestri-capsule-btn > button,
.maestri-bottom-controls menubutton.floor-button > button {
    background: transparent;
    border: none;
    box-shadow: none;
    border-radius: 13px;
    min-height: 26px;
    min-width: 26px;
    padding: 0 7px;
    margin: 3px 0;
    color: #ececf0;
    transition: background-color 120ms ease-out;
}
.maestri-bottom-controls menubutton.maestri-capsule-btn,
.maestri-bottom-controls menubutton.floor-button { background: transparent; border: none; box-shadow: none; padding: 0; }
.maestri-bottom-controls menubutton.floor-button > button { padding: 0 10px 0 8px; }
.maestri-bottom-controls button.maestri-capsule-btn:hover,
.maestri-bottom-controls menubutton.maestri-capsule-btn > button:hover,
.maestri-bottom-controls menubutton.floor-button > button:hover { background-color: rgba(255, 255, 255, 0.09); }
.maestri-bottom-controls button.maestri-capsule-btn:active,
.maestri-bottom-controls menubutton.maestri-capsule-btn > button:active,
.maestri-bottom-controls menubutton.maestri-capsule-btn > button:checked,
.maestri-bottom-controls menubutton.floor-button > button:active,
.maestri-bottom-controls menubutton.floor-button > button:checked { background-color: rgba(255, 255, 255, 0.16); }
.maestri-bottom-controls button.maestri-capsule-btn:focus-visible,
.maestri-bottom-controls menubutton.maestri-capsule-btn > button:focus-visible,
.maestri-bottom-controls menubutton.floor-button > button:focus-visible { outline: 2px solid rgba(10, 132, 255, 0.85); outline-offset: -2px; }
.maestri-bottom-controls button.maestri-capsule-btn:disabled image { opacity: 0.35; }
/* same cause as the top islands: the user theme paints the inner `image` node of icon buttons */
.maestri-bottom-controls button.maestri-capsule-btn image,
.maestri-bottom-controls menubutton.maestri-capsule-btn > button image,
.maestri-bottom-controls menubutton.floor-button > button image {
    background: none; background-color: transparent; background-image: none; border: none; box-shadow: none; outline: none;
}
.maestri-bottom-controls .maestri-zoom-label {
    font-size: 11px;
    font-weight: 500;
    font-feature-settings: "tnum";
    font-family: monospace;
    color: #ececf0;
    padding: 0;
    min-width: 40px;
}
.maestri-bottom-controls .maestri-floor-label { font-size: 12px; font-weight: 500; }
.maestri-bottom-controls .maestri-capsule-sep { background-color: rgba(255, 255, 255, 0.10); min-width: 1px; margin: 8px 1px; }
.maestri-zoom-preset { padding: 4px 10px; border-radius: 6px; min-width: 140px; }
popover.maestri-activity-popover > contents { background-color: rgba(28, 28, 31, 0.97); color: #ececf0; border-radius: 12px;
                                              border: 1px solid rgba(255, 255, 255, 0.10); }
popover.maestri-activity-popover label { color: #ececf0; }
.maestri-activity-row:hover { background-color: rgba(255, 255, 255, 0.08); }
.maestri-activity-title { font-size: 11px; font-weight: 600; letter-spacing: 0.04em; opacity: 0.6; margin: 0 4px 4px 4px; }
.maestri-activity-kind { font-weight: 600; font-size: 12px; margin: 6px 4px 2px 4px; }
.maestri-activity-count { font-size: 11px; font-feature-settings: "tnum"; opacity: 0.7; }
.maestri-activity-row { padding: 4px 6px; border-radius: 6px; }
.maestri-activity-cwd { font-family: monospace; font-size: 10px; opacity: 0.6; }
.maestri-activity-state { font-size: 11px; opacity: 0.75; }
.maestri-activity-state.busy, .maestri-activity-dot.busy { color: #30D158; opacity: 1; }
.maestri-activity-dot.idle { color: alpha(currentColor, 0.35); }
.maestri-activity-state.exited, .maestri-activity-dot.exited { color: #FF453A; }
.maestri-activity-empty, .maestri-activity-note { font-size: 11px; opacity: 0.6; margin: 4px; }
.maestri-zoom-preset label { font-feature-settings: "tnum"; }
.maestri-minimap-empty { opacity: 0.6; font-size: 12px; }
"""
_bottom_css_installed = False


def _install_bottom_css() -> None:
    global _bottom_css_installed
    display = Gdk.Display.get_default()
    if _bottom_css_installed or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(BOTTOM_CSS)
    # USER + 1: a user GTK theme (WhiteSur, ~/.config/gtk-4.0) loads at USER priority and would override these rules
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    _bottom_css_installed = True


def _icon(name: str, size: int = 16) -> Gtk.Image:
    img = Gtk.Image.new_from_icon_name(name)
    img.set_pixel_size(size)
    return img


def zoom_percent(zoom: float) -> str:
    """Label text of the zoom capsule (macOS: `Int(zoom * 100)%`; rounded here so 0.85 never reads 84%)."""
    return f"{int(round(zoom * 100))}%"


def clamp_zoom(zoom: float) -> float:
    return min(CANVAS_MAX_ZOOM, max(CANVAS_MIN_ZOOM, zoom))


def stepped_zoom(zoom: float, direction: int) -> float:
    """−/+ buttons: additive step of Constants.canvasZoomStep (0.25), clamped to the canvas limits (0.1 ... 3.0)."""
    return clamp_zoom(round(zoom + direction * CANVAS_ZOOM_STEP, 4))


def minimap_bounds(frames: list, viewport: Optional[Rect] = None) -> Optional[Rect]:
    """Bounding box of all node frames padded by 100 (CanvasMinimapPopover.computeBounds); the visible viewport is included so
    its indicator is always inside the map. None when there is nothing to show."""
    rects = list(frames) + ([viewport] if viewport is not None and frames else [])
    if not rects:
        return None
    x0 = min(r.x for r in rects) - MINIMAP_PAD
    y0 = min(r.y for r in rects) - MINIMAP_PAD
    x1 = max(r.x + r.w for r in rects) + MINIMAP_PAD
    y1 = max(r.y + r.h for r in rects) + MINIMAP_PAD
    return Rect(x0, y0, x1 - x0, y1 - y0)


class MinimapTransform:
    """Canvas <-> minimap mapping: aspect-fit of `bounds` into (w, h), centered (CanvasMinimapPopover.scaledRect)."""

    def __init__(self, bounds: Rect, w: float, h: float) -> None:
        self.bounds = bounds
        self.scale = min(w / max(bounds.w, 1e-6), h / max(bounds.h, 1e-6))
        self.ox = (w - bounds.w * self.scale) / 2.0
        self.oy = (h - bounds.h * self.scale) / 2.0

    def to_map(self, r: Rect) -> Rect:
        s = self.scale
        return Rect((r.x - self.bounds.x) * s + self.ox, (r.y - self.bounds.y) * s + self.oy, r.w * s, r.h * s)

    def to_canvas(self, x: float, y: float) -> Point:
        s = self.scale
        return Point((x - self.ox) / s + self.bounds.x, (y - self.oy) / s + self.bounds.y)


class MinimapView(CairoPaintArea):
    """The map inside the minimap popover: node blocks coloured by kind + the real viewport rectangle. A click on a node centers
    that node, a click elsewhere centers the viewport on that point (MinimapView.mouseDown); `on_jump(center)` does the move."""

    def __init__(self, canvas, on_jump: Callable[[Point], None]) -> None:
        super().__init__()
        self.cw, self.on_jump = canvas, on_jump
        self.set_content_width(MINIMAP_SIZE[0])
        self.set_content_height(MINIMAP_SIZE[1])
        self.set_cursor_from_name("pointer")
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._clicked)
        self.add_controller(click)

    def _viewport_rect(self) -> Rect:
        vp = self.cw.viewport
        w = max(1.0, float(self.cw.get_width() or 1200))
        h = max(1.0, float(self.cw.get_height() or 800))
        return Rect(vp.origin.x, vp.origin.y, w / vp.zoom, h / vp.zoom)

    def transform(self) -> Optional[MinimapTransform]:
        frames = [n.frame for n in self.cw.scene.nodes.values()]
        b = minimap_bounds(frames, self._viewport_rect())
        return MinimapTransform(b, *MINIMAP_SIZE) if b is not None else None

    def paint(self, cr, width, height) -> None:
        cr.set_source_rgba(1, 1, 1, 0.06)
        _rounded(cr, 0, 0, width, height, 8)
        cr.fill()
        t = self.transform()
        if t is None:
            return
        for n in self.cw.scene.ascending():
            r = t.to_map(n.frame)
            cr.set_source_rgba(*MINIMAP_COLORS.get(n.kind, (0.6, 0.6, 0.65)), 0.9)
            _rounded(cr, r.x, r.y, max(r.w, 6), max(r.h, 4), 2)
            cr.fill()
        v = t.to_map(self._viewport_rect())
        cr.set_source_rgba(0.04, 0.52, 1.0, 0.12)
        _rounded(cr, v.x, v.y, v.w, v.h, 2)
        cr.fill_preserve()
        cr.set_source_rgba(0.04, 0.52, 1.0, 0.75)
        cr.set_line_width(1.5)
        cr.stroke()

    def node_at(self, x: float, y: float):
        t = self.transform()
        if t is None:
            return None
        for n in self.cw.scene.descending():
            r = t.to_map(n.frame)
            if r.x <= x <= r.x + max(r.w, 6) and r.y <= y <= r.y + max(r.h, 4):
                return n
        return None

    def _clicked(self, _g, _n, x: float, y: float) -> None:
        t = self.transform()
        if t is None:
            return
        node = self.node_at(x, y)
        self.on_jump(node.frame.center if node is not None else t.to_canvas(x, y))


def _rounded(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class CanvasBottomRightControls(Gtk.Box):
    """Bottom-right capsules, left to right: [Floor] [agent rings] [Map] [− % +] — four separate dark-glass capsules of equal
    height, 8 px apart (uploaded_media_0). Behaviour follows the macOS app:

    * Floor: `FloorButton` (floors.py) is mounted into `pill_floor` by `install_floors`; it shows the active floor.
    * Map: opens the minimap popover (CanvasMinimapPopover); clicking a node / a spot jumps there (animated) and closes it.
    * Zoom: −/+ step 0.25 within 0.1 ... 3.0 around the viewport center; the % opens presets (50-200%). The label follows every
      zoom change (scroll, pinch, keyboard, Ctrl+0, `\\` focus) — it is refreshed from a frame tick, not only from signals.
    """

    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        _install_bottom_css()
        self.window = window
        self.cw = window.canvas_widget
        self.add_css_class("maestri-bottom-controls")
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_margin_end(16)
        self.set_margin_bottom(16)
        self._tick_id = 0
        self._jump_tick = 0
        self._shown_zoom: Optional[float] = None
        self._handlers: list = []

        # Capsule 1: Floor selector (FloorButton is appended by install_floors)
        self.pill_floor = self._capsule()
        self.btn_floors = None

        # Capsule 2: agent rings (Cairo)
        self.pill_agents = self._capsule()
        self.rings_widget = AgentActivityRingsWidget(window)
        self.rings_widget.set_valign(Gtk.Align.CENTER)
        self.rings_widget.set_margin_start(4)
        self.rings_widget.set_margin_end(4)
        self.activity_popover = AgentActivityPopover(window)
        self.btn_activity = Gtk.MenuButton(child=self.rings_widget, popover=self.activity_popover, direction=Gtk.ArrowType.UP,
                                           css_classes=["maestri-capsule-btn"], valign=Gtk.Align.CENTER)
        self.pill_agents.append(self.btn_activity)
        self.pill_agents.set_tooltip_text(self.rings_widget.get_tooltip_text())

        # Capsule 3: minimap
        self.pill_minimap = self._capsule()
        self.minimap = MinimapView(self.cw, self.jump_to)
        empty = Gtk.Label(label="Canvas vazio", css_classes=["maestri-minimap-empty"],
                          width_request=MINIMAP_SIZE[0], height_request=MINIMAP_SIZE[1])
        self._minimap_stack = Gtk.Stack()
        self._minimap_stack.add_named(self.minimap, "map")
        self._minimap_stack.add_named(empty, "empty")
        map_box = Gtk.Box(margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        map_box.append(self._minimap_stack)
        self.minimap_popover = Gtk.Popover(child=map_box, position=Gtk.PositionType.TOP)
        self.minimap_popover.connect("show", lambda _p: self._refresh_minimap())
        self.btn_minimap = Gtk.MenuButton(child=_icon("maestri-map-symbolic"), popover=self.minimap_popover,
                                          direction=Gtk.ArrowType.UP, tooltip_text="Minimapa",
                                          css_classes=["maestri-capsule-btn"], valign=Gtk.Align.CENTER)
        self.pill_minimap.append(self.btn_minimap)

        # Capsule 4: zoom [ −  87%  + ]
        self.pill_zoom = self._capsule()
        self.pill_zoom.add_css_class("maestri-zoom-controls")
        self.btn_zoom_out = Gtk.Button(child=_icon("maestri-zoom-out-symbolic", 14), tooltip_text="Reduzir (Ctrl+−)",
                                       css_classes=["maestri-capsule-btn"], valign=Gtk.Align.CENTER)
        self.btn_zoom_out.connect("clicked", lambda _b: self.zoom_by_step(-1))
        self.zoom_lbl = Gtk.Label(label=zoom_percent(self.cw.viewport.zoom), css_classes=["maestri-zoom-label"],
                                  width_chars=4, xalign=0.5)
        self.zoom_popover = self._build_zoom_presets()
        self.btn_zoom_menu = Gtk.MenuButton(child=self.zoom_lbl, popover=self.zoom_popover, direction=Gtk.ArrowType.UP,
                                            tooltip_text="Níveis de zoom", css_classes=["maestri-capsule-btn"],
                                            valign=Gtk.Align.CENTER, always_show_arrow=False)
        self.btn_zoom_in = Gtk.Button(child=_icon("maestri-zoom-in-symbolic", 14), tooltip_text="Ampliar (Ctrl++)",
                                      css_classes=["maestri-capsule-btn"], valign=Gtk.Align.CENTER)
        self.btn_zoom_in.connect("clicked", lambda _b: self.zoom_by_step(+1))
        for w in (self.btn_zoom_out, self.btn_zoom_menu, self.btn_zoom_in):
            self.pill_zoom.append(w)

        self._handlers.append(self.cw.connect("viewport-changed", self._on_viewport_changed))
        self._tick_id = self.add_tick_callback(self._on_tick)
        self.connect("unrealize", lambda *_: self._cleanup())
        self.connect("destroy", lambda *_: self._cleanup())
        self._update_zoom_label(self.cw.viewport.zoom)

    # -- building blocks ---------------------------------------------------------------------------------------------
    def _capsule(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, valign=Gtk.Align.END,
                      css_classes=["maestri-floating-pill", "maestri-bottom-pill", "maestri-capsule"])
        box.set_size_request(-1, CAPSULE_H)
        self.append(box)
        return box

    def _build_zoom_presets(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_top=4, margin_bottom=4,
                      margin_start=4, margin_end=4)
        self._preset_checks: list[tuple[float, Gtk.Image]] = []
        for z in ZOOM_PRESETS:
            row = Gtk.Box(spacing=8)
            check = _icon("maestri-check-symbolic", 14)
            row.append(check)
            row.append(Gtk.Label(label=zoom_percent(z), xalign=0, hexpand=True))
            btn = Gtk.Button(child=row, has_frame=False, css_classes=["flat", "maestri-zoom-preset"])
            btn.connect("clicked", lambda _b, zz=z: (self.set_zoom(zz), self.zoom_popover.popdown()))
            box.append(btn)
            self._preset_checks.append((z, check))
        pop = Gtk.Popover(child=box, position=Gtk.PositionType.TOP)
        pop.connect("show", lambda _p: self._sync_preset_checks())
        return pop

    def _sync_preset_checks(self) -> None:
        z = self.cw.viewport.zoom
        for preset, check in self._preset_checks:
            check.set_opacity(1.0 if abs(preset - z) < 0.005 else 0.0)

    # -- zoom --------------------------------------------------------------------------------------------------------
    def _center(self) -> Point:
        return Point(max(1.0, float(self.cw.get_width())) / 2.0, max(1.0, float(self.cw.get_height())) / 2.0)

    def set_zoom(self, zoom: float) -> None:
        """Absolute zoom around the viewport center (canvasZoom(toAbsolute:)); notifies through the canvas controller."""
        vp = self.cw.viewport
        target = clamp_zoom(zoom)
        if abs(target - vp.zoom) < 1e-9:
            return
        vp.set_zoom_at(self._center(), target)
        self._viewport_changed()

    def zoom_by_step(self, direction: int) -> None:
        """−/+ capsule buttons and Ctrl+−/+: additive 0.25 step (Constants.canvasZoomStep)."""
        self.set_zoom(stepped_zoom(self.cw.viewport.zoom, direction))

    def _zoom_step(self, factor: float) -> None:
        """Multiplicative zoom around the center (kept for callers/tests that pass a factor)."""
        self.set_zoom(self.cw.viewport.zoom * factor)

    def _viewport_changed(self) -> None:
        ctl = self.cw.controller
        ctl.dirty = True
        notify = getattr(ctl, "_viewport_changed", None)
        if notify is not None:
            notify()   # -> ViewportChanged -> CanvasWidget "viewport-changed" -> bus/sync, like scroll/pinch
        else:
            vp = self.cw.viewport
            self.cw.emit("viewport-changed", vp.origin.x, vp.origin.y, vp.zoom)
        self._update_zoom_label(self.cw.viewport.zoom)

    def _update_zoom_label(self, zoom: float) -> None:
        self._shown_zoom = zoom
        self.zoom_lbl.set_label(zoom_percent(zoom))
        self.btn_zoom_out.set_sensitive(zoom > CANVAS_MIN_ZOOM + 1e-6)
        self.btn_zoom_in.set_sensitive(zoom < CANVAS_MAX_ZOOM - 1e-6)

    def _on_viewport_changed(self, _cw, _x, _y, zoom) -> None:
        self._update_zoom_label(zoom)

    def _on_tick(self, _w, _clock) -> bool:
        self.rings_widget.refresh()
        self.pill_agents.set_tooltip_text(self.rings_widget.get_tooltip_text())
        z = self.cw.viewport.zoom
        if self._shown_zoom is None or abs(z - self._shown_zoom) > 1e-9:
            self._update_zoom_label(z)   # zoom changed without a signal (reset_zoom, focus_on, workspace switch)
        return GLib.SOURCE_CONTINUE

    # -- minimap -----------------------------------------------------------------------------------------------------
    def _refresh_minimap(self) -> None:
        self._minimap_stack.set_visible_child_name("map" if self.cw.scene.nodes else "empty")
        self.minimap.queue_draw()

    def jump_to(self, center: Point) -> None:
        """Center the viewport on canvas point `center` keeping the zoom, with a short ease-out (canvasJumpToOrigin),
        then close the minimap popover like the macOS one."""
        vp = self.cw.viewport
        w = max(1.0, float(self.cw.get_width() or 1200))
        h = max(1.0, float(self.cw.get_height() or 800))
        start = Point(vp.origin.x, vp.origin.y)
        goal = Point(center.x - w / (2.0 * vp.zoom), center.y - h / (2.0 * vp.zoom))
        self.minimap_popover.popdown()
        if self._jump_tick:
            self.cw.remove_tick_callback(self._jump_tick)
            self._jump_tick = 0
        if not self.cw.get_mapped():
            vp.origin = goal
            self._viewport_changed()
            return
        t0 = self.cw.get_frame_clock().get_frame_time() if self.cw.get_frame_clock() else GLib.get_monotonic_time()

        def step(widget, clock) -> bool:
            p = min(1.0, (clock.get_frame_time() - t0) / (JUMP_MS * 1000.0))
            e = 1.0 - (1.0 - p) ** 3
            vp.origin = Point(start.x + (goal.x - start.x) * e, start.y + (goal.y - start.y) * e)
            self.cw.controller.dirty = True
            if p >= 1.0:
                self._jump_tick = 0
                self._viewport_changed()   # one notification at the end: sync persists the final origin only
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._jump_tick = self.cw.add_tick_callback(step)

    # -- floors (kept for older callers) -----------------------------------------------------------------------------
    def _on_floors_clicked(self, _btn=None) -> None:
        btn = self.btn_floors or getattr(self.window, "floor_button", None)
        if btn is not None:
            btn.popup()

    def _on_minimap_clicked(self, _btn=None) -> None:
        self.btn_minimap.popup()

    def _cleanup(self) -> None:
        self.activity_popover.cleanup()
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        if self._jump_tick:
            try:
                self.cw.remove_tick_callback(self._jump_tick)
            except Exception:
                pass
            self._jump_tick = 0
        for hid in self._handlers:
            try:
                if self.cw.handler_is_connected(hid):
                    self.cw.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
