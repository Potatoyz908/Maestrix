"""Drawing tools for the GTK window: text labels, shapes (rect / ellipse / diamond) and freehand (pen / highlighter).

Kept out of window.py on purpose (see docs/linux-gtk-sprint4-spec.md §4): the window only calls `DrawingTools(window)` and packs
`tools.button` in its header bar. The canvas widget paints these kinds with Cairo (maestri_canvas.drawn) and reports gestures
as signals; this class turns them into CanvasNodes in the document (schema v2, `{"shape": {"_0": {...}}}`) and lets
WorkspaceSync persist them through the bus, like every other node.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from maestri_canvas.drawn import DRAW_TOOLS, TEXT_DEFAULT_SIZE, content_for_tool, kind_for_tool, measure_text
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import WIDGET_KINDS
from maestri_core.models.node import CanvasNode, NodeFrame, current_iso8601
from maestri_core.signals.bus import bus

# tool -> (label, outline icon of gtk4-ui/src/ui/icons - built by scripts/build-gtk-icons.py). No emoji/glyph icons.
TOOL_LABELS = (
    ("text", "Texto", "maestri-text-symbolic"), ("rect", "Retângulo", "maestri-square-symbolic"),
    ("ellipse", "Elipse", "maestri-circle-symbolic"), ("diamond", "Losango", "maestri-diamond-symbolic"),
    ("freehand_pen", "Caneta", "maestri-pencil-symbolic"), ("freehand_highlighter", "Marca-texto", "maestri-highlighter-symbolic"),
)
DEFAULT_LABEL, DEFAULT_ICON = "Desenho", "maestri-shapes-symbolic"


def _icon_label(icon: str, label: str) -> tuple[Gtk.Box, Gtk.Image, Gtk.Label]:
    box = Gtk.Box(spacing=8)
    img = Gtk.Image.new_from_icon_name(icon)
    lbl = Gtk.Label(label=label, xalign=0)
    box.append(img)
    box.append(lbl)
    return box, img, lbl


class DrawingTools:
    def __init__(self, window) -> None:
        self.window = window
        cw = window.canvas_widget
        cw.connect("draw-requested", self._on_draw_requested)
        cw.connect("freehand-drawn", self._on_freehand_drawn)
        cw.connect("drawn-content-changed", self._on_content_changed)
        cw.connect("tool-changed", self._on_tool_changed)
        self.button = self._build_button()

    # ---- toolbar ---------------------------------------------------------------------------------------
    def _build_button(self) -> Gtk.MenuButton:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.popover = Gtk.Popover(child=box)
        self.tool_buttons: dict[str, Gtk.Button] = {}
        for tool, label, icon in TOOL_LABELS:
            b = Gtk.Button(child=_icon_label(icon, label)[0], has_frame=False)
            b.connect("clicked", lambda _b, t=tool: self.select(t))
            box.append(b)
            self.tool_buttons[tool] = b
        child, self._button_icon, self._button_label = _icon_label(DEFAULT_ICON, DEFAULT_LABEL)
        btn = Gtk.MenuButton(child=child, popover=self.popover)
        btn.set_tooltip_text("Texto, formas e desenho livre: escolha a ferramenta e arraste no canvas (Esc cancela)")
        return btn

    def select(self, tool: str) -> None:
        if tool not in DRAW_TOOLS:
            raise ValueError(f"not a drawing tool: {tool!r}")
        self.popover.popdown()
        self.window.canvas_widget.set_tool(tool)
        self.window.canvas_widget.grab_focus()   # Esc (cancel the tool) is a canvas-focus key

    def _on_tool_changed(self, _cw, tool: str) -> None:
        label, icon = next(((lb, ic) for t, lb, ic in TOOL_LABELS if t == tool), (DEFAULT_LABEL, DEFAULT_ICON))
        self._button_label.set_label(label)
        self._button_icon.set_from_icon_name(icon)

    # ---- canvas gestures -> document -----------------------------------------------------------------
    def _on_draw_requested(self, _cw, tool: str, x: float, y: float, w: float, h: float) -> None:
        if tool in WIDGET_KINDS:   # a widget tool (P = portal) dragged a frame: same node as the toolbar button, at that frame
            self.window._create_node(tool, at=Point(x, y), size=(w, h))
            return
        if kind_for_tool(tool) is None:
            return
        node = self.create(tool, Rect(x, y, w, h))
        if node is not None and node.node_type == "text":
            self.window.canvas_widget.edit_drawn_text(node.id)   # a new label starts in edit mode, like macOS

    def _on_freehand_drawn(self, _cw, tool: str, x: float, y: float, w: float, h: float, points) -> None:
        self.create(tool, Rect(x, y, w, h), points=[[float(p[0]), float(p[1])] for p in points])

    def create(self, tool: str, frame: Rect, points: list | None = None) -> CanvasNode | None:
        """Add a drawn node to the document and the canvas; WorkspaceSync persists it (bus `node-added`)."""
        win = self.window
        kind = kind_for_tool(tool)
        if kind is None or not win.loaded:
            return None
        content = content_for_tool(tool)
        if points is not None:
            content["points"] = points
        nodes = win.document.payload.nodes
        node = CanvasNode(frame=NodeFrame(x=frame.x, y=frame.y, width=frame.w, height=frame.h), node_type=kind,
                          content=content, z_index=max((n.z_index for n in nodes), default=0) + 1)
        nodes.append(node)
        win._add_node_to_canvas(node)
        bus.emit("node-added", node)
        return node

    def _on_content_changed(self, _cw, node_id: str, content: dict) -> None:
        win = self.window
        node = win._find_node(node_id)
        if node is None or not isinstance(node.content, dict):
            return
        node.content = {**node.content, **content}   # keep keys this UI does not know about
        node.last_modified_at = current_iso8601()
        cw = win.canvas_widget
        cw.set_node_content(node.id, node.content)
        if node.node_type == "text":   # a label is as big as its text (the macOS label auto-sizes)
            w, h = measure_text(node.content)
            f = node.frame
            w, h = max(TEXT_DEFAULT_SIZE[0], w), max(TEXT_DEFAULT_SIZE[1], h)
            if (w, h) != (f.width, f.height):
                node.frame = NodeFrame(x=f.x, y=f.y, width=w, height=h)
                cw.update_node_frame(node.id, Rect(f.x, f.y, w, h))
                bus.emit("node-resized", node.id, f.x, f.y, w, h)
        bus.emit("node-updated", node.id)
