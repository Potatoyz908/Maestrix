"""GTK 4 glue. Everything pointer/keyboard-related is delegated to the pure CanvasController; this file only translates GTK events.

Structure (Gtk.Overlay):
    CairoLayer   - Gtk.Widget whose snapshot is a pycairo ImageSurface uploaded as a Gdk.MemoryTexture
    Gtk.Fixed    - node widgets (NodeFrame), positioned/zoomed with per-child GskTransforms

CairoLayer is a Gtk.DrawingArea. When PyGObject has the foreign-struct bridge `gi._gi_cairo` it uses set_draw_func; otherwise
it renders into its own ImageSurface and uploads a texture (same drawing routine, same pixels: see tests/test_widget.py).

Input model: ONE capture-phase GestureDrag on the CanvasWidget sees every press, asks `pick()` what is under the pointer and tags it with a
role (header / edge / pin / content / background). Chrome roles are claimed by the engine; content roles are DENIED so the child widget
(terminal, text view, ...) gets the event untouched. Child widgets therefore need no gestures of their own - only a role tag.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
import os
import signal

import cairo
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk

from .constants import CANVAS_GRID_SPACING  # noqa: F401
from .geometry import Point, Rect, round_half_away
from .file_dnd import install_terminal_drop
from .file_tree_node import FileTreeView
from .connection_rules import plan_connection
from .drawn import DRAWN_KINDS
from .drawn_editor import DrawnTextEditor
from .gi_env import webkit_available
from .note_model import NoteMeta
from .portal_model import PortalMeta
from .note_storage import CoreNotesClient, CoreNoteStorage, LocalNoteStorage, MemoryNoteStorage
from .sticky_note import StickyNoteView
from .interaction import (WHEEL_NOTCH_PX, Background, CanvasController, ConnectRequested, DeleteRequested, DrawRequested,
                          DuplicateRequested, Event, FreehandDrawn, Mods, NodeContent, NodeEdge, NodeHeader, NodePin, NodeResized, NodesMoved,
                          SelectionChanged, Target, ToolChanged, ViewportChanged)
from .renderer import CairoRenderer
from .resize import Edge
from .scene import WIDGET_KINDS, ConnItem, NodeItem, Scene, norm
from .viewport import Viewport

HEADER_H = 28
SIDE, SIDE_OUT = 10, 5        # side resize handle: 10px thick, 5 outside the border
CORNER, CORNER_OUT = 16, 6    # corner handle: 16px square, 6 outside
_ROLE = "_canvas_role"   # python attribute set on chrome widgets: ("header"|"edge"|"pin", payload)


def _cairo_foreign_available() -> bool:
    """True when PyGObject can hand a cairo.Context to Python callbacks (gi._gi_cairo / python3-gi-cairo)."""
    try:
        gi.require_foreign("cairo")
        return True
    except ImportError:
        return False


USE_DRAWING_AREA = _cairo_foreign_available()   # tests flip this to exercise the fallback


# ---- native terminal (VTE for GTK 4 = namespace Vte 3.91) ------------------------------------------------------------------
VTE_TYPELIB_DIRS = (os.path.expanduser("~/.local/lib/girepository-1.0"),)   # user-installed Vte-3.91.typelib
_vte_module = None
_vte_tried = False


def load_vte():
    """Import Vte 3.91 (GTK 4 build) once; None when unavailable (the canvas then falls back to a placeholder label).
    The typelib may live outside the default search path, so the user dirs are prepended to the GI repository first."""
    global _vte_module, _vte_tried
    if _vte_tried:
        return _vte_module
    _vte_tried = True
    dirs = [d for d in VTE_TYPELIB_DIRS if os.path.isdir(d)]
    if dirs:
        try:
            gi.require_version("GIRepository", "3.0")
            from gi.repository import GIRepository
            repo = GIRepository.Repository.dup_default()
            for d in dirs:
                if d not in repo.get_search_path():
                    repo.prepend_search_path(d)
        except (ValueError, ImportError, AttributeError):
            try:   # PyGObject < 3.52: girepository-2.0, class-level API
                gi.require_version("GIRepository", "2.0")
                from gi.repository import GIRepository
                for d in dirs:
                    GIRepository.Repository.prepend_search_path(d)
            except (ValueError, ImportError, AttributeError):
                pass
    try:
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte
        _vte_module = Vte
    except (ValueError, ImportError):
        _vte_module = None
    return _vte_module


def default_shell_argv() -> list[str]:
    """Login shell: the user's shell when it is bash-compatible enough to take -l, else /bin/bash."""
    Vte = load_vte()
    shell = os.environ.get("SHELL") or (Vte.get_user_shell() if Vte else None) or "/bin/bash"
    if not os.access(shell, os.X_OK):
        shell = "/bin/bash"
    return [shell, "-l"]


def create_terminal(cwd: str | None = None, argv: list[str] | None = None, env: dict[str, str] | None = None,
                    on_exit=None) -> Gtk.Widget | None:
    """A Vte.Terminal running a login shell on its own PTY. VTE resizes the PTY (TIOCSWINSZ) on every size-allocate, so
    resizing the node resizes the shell; canvas zoom is a Gsk transform, so cols/rows stay those of the unscaled frame.
    Returns None when Vte 3.91 is not importable. `on_exit(status)` runs when the child exits."""
    Vte = load_vte()
    if Vte is None:
        return None
    term = Vte.Terminal()
    term.set_focusable(True)
    term.set_focus_on_click(True)
    term.set_scrollback_lines(10000)
    term.set_mouse_autohide(True)
    term.set_size_request(80, 40)   # keep the node shrinkable: VTE's natural size (80x24) must not become a minimum
    term.maestri_pid = -1
    envv = [f"{k}={v}" for k, v in {"TERM": "xterm-256color", "COLORTERM": "truecolor", **(env or {})}.items()]

    def spawned(_t, pid, error, *_):
        if error is not None:
            term.feed(f"\r\n[failed to start shell: {error.message}]\r\n".encode())
        else:
            term.maestri_pid = pid

    def exited(_t, status):
        term.maestri_pid = -1
        term.feed(f"\r\n[process exited with status {status}]\r\n".encode())
        if on_exit:
            on_exit(status)

    term.connect("child-exited", exited)
    term.drop_target = install_terminal_drop(term, term.paste_text)   # bracketed paste when the shell enabled it
    term.spawn_async(Vte.PtyFlags.DEFAULT, cwd or os.path.expanduser("~"), argv or default_shell_argv(), envv,
                     GLib.SpawnFlags.DEFAULT, None, None, -1, None, spawned, None)
    return term


def is_terminal(w: Gtk.Widget | None) -> bool:
    Vte = _vte_module
    return Vte is not None and isinstance(w, Vte.Terminal)


def terminal_of(w: Gtk.Widget | None):
    """The Vte.Terminal behind a node's content: the widget itself (local shell) or `.term` of a core-backed VteTerminalView."""
    if is_terminal(w):
        return w
    t = getattr(w, "term", None)
    return t if is_terminal(t) else None


def terminate_terminal(w: Gtk.Widget) -> None:
    """SIGHUP the shell, like closing a terminal window."""
    pid = getattr(w, "maestri_pid", -1)
    if pid > 0:
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        w.maestri_pid = -1


class CairoLayer(Gtk.DrawingArea):
    """A Cairo-painted layer. `top=False`: grid, ropes, canvas-drawn nodes (below the node widgets).
    `top=True`: gesture overlays on a transparent surface (above them); it paints nothing when no gesture is active.

    Two paint paths, one drawing routine (`_paint`):
      * DrawingArea draw_func (needs gi._gi_cairo): GTK records the Cairo calls; HiDPI is automatic. Measured ~15-25% less
        whole-process CPU per frame than the texture path on a 100-node / 30-rope scene.
      * fallback: draw into our own ImageSurface and upload it as a Gdk.MemoryTexture (no foreign-struct bridge needed)."""
    __gtype_name__ = "MaestriCairoLayer"

    def __init__(self, canvas: "CanvasWidget", top: bool = False) -> None:
        super().__init__()
        self._canvas, self._top = canvas, top
        self._da = USE_DRAWING_AREA
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False)   # pure paint: never steals pointer events
        if self._da:
            self.set_draw_func(lambda _a, cr, w, h: self._paint(cr, w, h))

    def _paint(self, cr: cairo.Context, w: int, h: int) -> None:
        c = self._canvas
        if self._top:
            if not c.controller.overlay.is_empty():
                c.renderer.draw_overlay(cr, c.scene, c.viewport, c.controller.overlay)
        else:
            c.renderer.draw(cr, c.scene, c.viewport, w, h, c.controller.ropes, None)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._da:
            Gtk.DrawingArea.do_snapshot(self, snapshot)
            return
        w, h, s = self.get_width(), self.get_height(), self.get_scale_factor()
        if w <= 0 or h <= 0 or (self._top and self._canvas.controller.overlay.is_empty()):
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * s, h * s)
        surf.set_device_scale(s, s)   # draw in logical px; HiDPI handled by the surface
        self._paint(cairo.Context(surf), w, h)
        surf.flush()
        tex = Gdk.MemoryTexture.new(w * s, h * s, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                    GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())
        snapshot.append_texture(tex, Graphene.Rect().init(0, 0, w, h))


class NodeFrame(Gtk.Overlay):
    """Chrome for one node: header (drag + close), the content widget, 8 resize handles and 2 connection pins.
    Handles/pins carry role tags only; the CanvasWidget's gesture does the work."""

    def __init__(self, item: NodeItem, content: Gtk.Widget, on_close) -> None:
        super().__init__()
        self.node_id = item.id
        self.add_css_class("maestri-node")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(spacing=6)
        header.add_css_class("maestri-node-header")
        header.set_size_request(-1, HEADER_H)
        setattr(header, _ROLE, ("header", item.id))
        self.title = Gtk.Label(label=item.title or item.id[:8], xalign=0, hexpand=True, ellipsize=3)
        close = Gtk.Button(label="×")
        close.add_css_class("flat")
        close.connect("clicked", lambda _b: on_close(self.node_id))
        setattr(close, _ROLE, ("content", item.id))   # a real button: it keeps its own click
        # Optional header protocol for a content widget (the sticky note uses it): header_left / header_title / header_right.
        for w in getattr(content, "header_left", ()):
            setattr(w, _ROLE, ("content", item.id))   # real controls: they keep their own clicks
            header.append(w)
        title_widget = getattr(content, "header_title", None)
        if title_widget is not None:
            setattr(title_widget, _ROLE, ("header", item.id))   # draggable by its title; the widget retags itself while being edited
            header.append(title_widget)
        else:
            header.append(self.title)
        for w in getattr(content, "header_right", ()):
            setattr(w, _ROLE, ("content", item.id))
            header.append(w)
        header.append(close)
        content.set_vexpand(True)
        content.set_hexpand(True)
        setattr(content, _ROLE, ("content", item.id))
        box.append(header)
        box.append(content)
        self.set_child(box)
        self.content = content
        for edge in Edge:
            self.add_overlay(self._handle(edge))
        for side in ("left", "right"):
            self.add_overlay(self._pin(side))
        attach = getattr(content, "frame_attached", None)
        if attach is not None:
            attach(self)   # lets a content widget style the card and retag its header widgets

    def tag(self, widget: Gtk.Widget, kind: str) -> None:
        """Set a widget's canvas role ("header" = drags the node, "content" = keeps its own pointer events)."""
        setattr(widget, _ROLE, (kind, self.node_id))

    def _handle(self, edge: Edge) -> Gtk.Widget:
        """Resize handle straddling the border (part outside, part inside) so the edge is easy to grab.
        Side handles run the full side minus the corner handles; corners are bigger squares."""
        w = Gtk.Box()
        w.add_css_class("maestri-handle")
        setattr(w, _ROLE, ("edge", (self.node_id, edge)))
        parts = edge.value.split("_")
        corner = len(parts) == 2
        thick, out = (CORNER, CORNER_OUT) if corner else (SIDE, SIDE_OUT)
        horiz = "left" in parts or "right" in parts
        vert = "top" in parts or "bottom" in parts
        w.set_halign(Gtk.Align.START if "left" in parts else Gtk.Align.END if "right" in parts else Gtk.Align.FILL)
        w.set_valign(Gtk.Align.START if "top" in parts else Gtk.Align.END if "bottom" in parts else Gtk.Align.FILL)
        w.set_size_request(thick if horiz else -1, thick if vert else -1)
        # push the outer part past the frame boundary
        if "left" in parts: w.set_margin_start(-out)
        if "right" in parts: w.set_margin_end(-out)
        if "top" in parts: w.set_margin_top(-out)
        if "bottom" in parts: w.set_margin_bottom(-out)
        if not corner:   # leave the corners to the corner handles
            for setter in (("set_margin_top", "set_margin_bottom") if horiz else ("set_margin_start", "set_margin_end")):
                getattr(w, setter)(CORNER - CORNER_OUT)
        return w

    def _pin(self, side: str) -> Gtk.Widget:
        w = Gtk.Box()
        w.add_css_class("maestri-pin")
        setattr(w, _ROLE, ("pin", (self.node_id, side)))
        w.set_size_request(14, 14)
        w.set_halign(Gtk.Align.START if side == "left" else Gtk.Align.END)
        w.set_valign(Gtk.Align.CENTER)
        w.set_margin_start(-7 if side == "left" else 0)
        w.set_margin_end(-7 if side == "right" else 0)
        return w


CSS = b"""
.maestri-node { background: @window_bg_color; border: 1px solid alpha(currentColor, .25); border-radius: 8px; }
.maestri-node.maestri-refused { border: 2px solid #ef4444; }
.maestri-toast { background: #ef4444; color: white; padding: 6px 14px; border-radius: 14px; }
.maestri-node.selected { border: 2px dashed #3b82f6; }
.maestri-node-header { background: alpha(currentColor, .06); padding: 0 8px; border-radius: 7px 7px 0 0; }
.maestri-pin { background: #3b82f6; border: 2px solid white; border-radius: 50%; opacity: 0; transition: opacity 120ms; }
.maestri-file-tree-list > row { padding: 2px 6px; min-height: 22px; }
.maestri-drop-hover { box-shadow: inset 0 0 0 2px #3b82f6; }
.maestri-canvas-drop-hover { box-shadow: inset 0 0 0 3px alpha(#3b82f6, .6); }
.maestri-git-badge { font-family: monospace; font-weight: bold; font-size: 0.85em; padding: 0 4px; border-radius: 4px; }
.maestri-git-badge.git-modified { color: #f59e0b; } .maestri-git-badge.git-added { color: #22c55e; }
.maestri-git-badge.git-deleted { color: #ef4444; } .maestri-git-badge.git-renamed { color: #3b82f6; }
.maestri-git-badge.git-conflict { color: white; background: #ef4444; } .maestri-git-badge.git-untracked { color: alpha(currentColor, .55); }
.maestri-git-branch { font-size: 0.85em; padding: 0 6px; opacity: .8; }
.maestri-file-grid > child { padding: 6px; border-radius: 8px; }
.maestri-file-thumb { border-radius: 4px; }
.maestri-drag-icon { background: alpha(@window_bg_color, .9); border-radius: 6px; padding: 4px 8px; }
.maestri-node:hover .maestri-pin, .maestri-connecting .maestri-pin { opacity: 1; }
"""


class CanvasWidget(Gtk.Overlay):
    """Infinite canvas. Public API: add_node / remove_node / update_node_frame / add_connection / signals below."""
    __gtype_name__ = "MaestriCanvasWidget"
    __gsignals__ = {
        "node-moved": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float, bool)),   # id, x, y, w, h, final
        "node-resized": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float, bool)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),                          # tuple[str]
        "connect-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str, str)),               # a, side_a, b, side_b
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "duplicate-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),                        # {id: Rect}
        "draw-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float)),     # tool, frame
        "freehand-drawn": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float, object)),  # tool, frame, [[x,y]] 0..1
        "drawn-content-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                   # text/shape id, new content dict
        "tool-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "viewport-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float, float)),
        "terminal-exited": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),                            # id, wait status
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),                             # fileTree id, path
        "file-tree-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                       # fileTree id, {rootPath, viewMode}
        "files-dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),                  # [paths], canvas x, y
        "note-meta-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                       # stickyNote id, StickyNoteContent dict
        "portal-meta-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                     # portal id, PortalContent dict
        "portal-popup": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),                               # portal id, url (target=_blank)
        "connection-refused": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),                    # a, b, human reason
    }

    # Sticky notes: set `notes_client` (core REST API) before adding managed notes; without it they live in memory (offline demo).
    # `notes_dir` is the folder where pasted images of managed notes are saved (next to the .md); None disables image paste for them.
    notes_client: CoreNotesClient | None = None
    notes_dir: str | None = None

    def __init__(self, scene: Scene | None = None, viewport: Viewport | None = None) -> None:
        super().__init__()
        self.scene = scene or Scene()
        self.viewport = viewport or Viewport()
        self.controller = CanvasController(self.scene, self.viewport)
        self.controller.on_event = self._on_event
        self.renderer = CairoRenderer()
        self.frames: dict[str, NodeFrame] = {}
        self._pointer = Point(0, 0)
        self._last_applied: dict[str, tuple] = {}
        self._pinch_prev = 1.0
        self._stack: list[NodeFrame] = []
        self._drawn_editor: DrawnTextEditor | None = None
        self._install_css()
        self.layer = CairoLayer(self)
        self.set_child(self.layer)
        self.fixed = Gtk.Fixed()
        self.add_overlay(self.fixed)
        self.top_layer = CairoLayer(self, top=True)
        self.add_overlay(self.top_layer)
        self.set_focusable(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)

        drag = Gtk.GestureDrag(button=0)                        # any button
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        self._drag_gesture = drag

        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self._motion)
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        self._scroll_ctl = scroll

        zoom = Gtk.GestureZoom()
        zoom.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        zoom.connect("begin", lambda *_: setattr(self, "_pinch_prev", 1.0))
        zoom.connect("scale-changed", self._pinch)
        self.add_controller(zoom)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        keys.connect("key-released", lambda _c, kv, *_: self.controller.key_release((Gdk.keyval_name(kv) or "").lower()))
        self.add_controller(keys)

        self._install_file_drop()
        self.add_tick_callback(self._tick)

    # ---- public API ---------------------------------------------------------------------------
    def add_node(self, item: NodeItem, content: Gtk.Widget | None = None, cwd: str | None = None,
                 env: dict[str, str] | None = None, focus: bool = False, note: dict | None = None,
                 portal: dict | None = None, file_tree: dict | None = None) -> None:
        """`content=None` builds the native content: a VTE login shell for "terminal" (cwd/env apply to it), a FileTreeView rooted at
        `cwd` (default ~) for "fileTree" (`file_tree` = the persisted FileTreeContent dict: viewMode), a StickyNoteView for
        "stickyNote" (`note` = the persisted StickyNoteContent dict).
        `focus` gives the content the keyboard."""
        self.scene.add_node(item)
        self.controller.dirty = True
        if item.kind == "terminal" and content is None:
            nid = item.id
            content = create_terminal(cwd=cwd, env=env, on_exit=lambda st: self.emit("terminal-exited", nid, st))
            if content is None:
                content = Gtk.Label(label="Vte 3.91 (GTK 4) not available")
        elif item.kind == "fileTree" and content is None:
            nid = item.id
            content = FileTreeView(cwd, view_mode=(file_tree or {}).get("viewMode", "list"))
            content.connect("file-activated", lambda _v, path: self.emit("file-activated", nid, path))
            content.connect("root-changed", lambda v, _p: self.emit("file-tree-changed", nid, {"rootPath": v.root, "viewMode": v.view_mode}))
            content.connect("view-mode-changed", lambda v, _m: self.emit("file-tree-changed", nid, {"rootPath": v.root, "viewMode": v.view_mode}))
        elif item.kind == "stickyNote" and content is None:
            nid = item.id
            meta = NoteMeta.from_dict(note) if note else NoteMeta(file_name=f"{item.title or 'Note'}.md")
            client = self.notes_client
            if meta.is_custom:
                storage = LocalNoteStorage(meta.custom_path)
            elif client is not None:
                storage = CoreNoteStorage(client, meta.name)
            else:
                storage = MemoryNoteStorage()
            content = StickyNoteView(meta, storage, client=client, name_taken=lambda n: self._note_name_taken(nid, n), base_dir=self.notes_dir)
            content.connect("meta-changed", lambda _v, d: self.emit("note-meta-changed", nid, d))
            content.connect("delete-requested", lambda _v: self.emit("delete-requested", (nid,)))
        elif item.kind == "portal" and content is None:
            nid = item.id
            if webkit_available():
                from .portal_node import PortalView   # lazy: importing it needs the WebKit-6.0 typelib
                meta = PortalMeta.from_dict(portal) if portal else PortalMeta(name=item.title or "Portal", portal_id=nid)
                content = PortalView(meta)
                content.connect("meta-changed", lambda _v, d: self.emit("portal-meta-changed", nid, d))
                content.connect("popup-requested", lambda _v, url: self.emit("portal-popup", nid, url))
            else:
                content = Gtk.Label(label="WebKit 6.0 (GTK 4) not available", wrap=True)
        if item.kind in WIDGET_KINDS and content is not None:
            frame = NodeFrame(item, content, on_close=lambda i: self.emit("delete-requested", (i,)))
            self.frames[item.id] = frame
            self.fixed.put(frame, 0, 0)
            if focus:
                GLib.idle_add(lambda: (content.grab_focus(), False)[1])

    def remove_node(self, node_id: str) -> None:
        k = norm(node_id)
        frame = self.frames.pop(k, None)
        if frame is not None:
            if is_terminal(frame.content):
                terminate_terminal(frame.content)   # local shell: SIGHUP
            else:
                # One protocol for every content view: StickyNoteView flushes + stops its monitor, PortalView closes the
                # WebView, VteTerminalView closes its /ws/pty socket (the core owns that shell). GTK widgets have no `dispose`.
                dispose = getattr(frame.content, "dispose", None)
                if callable(dispose):
                    dispose()
            self.fixed.remove(frame)
        if self._drawn_editor is not None and self._drawn_editor.node_id == k:
            self._drawn_editor.cancel()
        for c in self.scene.connections_of({k}):
            self.controller.ropes.remove(c.id)
        self.scene.remove_node(k)
        self._last_applied.pop(k, None)
        self.controller.dirty = True
        self._sync_portal_sessions()

    def update_node_frame(self, node_id: str, frame: Rect) -> None:
        self.scene.set_frame(node_id, frame)
        self.controller._reanchor({norm(node_id)})
        self.controller.dirty = True

    def add_connection(self, conn: ConnItem) -> None:
        self.controller.add_connection(conn)
        self._sync_portal_sessions()

    def remove_connection(self, conn_id: str) -> bool:
        """Drop a connection (scene + rope). Used for confirmed deletes and to discard an optimistic `local-…` rope whose POST failed."""
        if self.scene.connections.pop(conn_id, None) is None:
            return False
        self.controller.ropes.remove(conn_id)
        self.controller.dirty = True
        self._sync_portal_sessions()
        return True

    def replace_connection_id(self, old_id: str, new_id: str) -> bool:
        """The optimistic id becomes the core's id after the POST (keeps the rope simulation state)."""
        c = self.scene.connections.pop(old_id, None)
        if c is None:
            return False
        c.id, c.pending = new_id, False
        self.scene.connections[new_id] = c
        self.controller.ropes.rename(old_id, new_id)
        self.controller.dirty = True
        return True

    def _sync_portal_sessions(self) -> None:
        """portal<->portal connections share ONE NetworkSession per connected group (= macOS shared storage); a portal left alone
        gets a fresh isolated session again. Only portals that are not already correct are touched (reload)."""
        views = {nid: f.content for nid, f in self.frames.items() if hasattr(f.content, "use_session")}
        if not views:
            return
        parent = {n: n for n in views}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for c in self.scene.connections.values():
            if c.a in views and c.b in views:
                parent[find(c.a)] = find(c.b)
        groups: dict[str, list[str]] = {}
        for n in views:
            groups.setdefault(find(n), []).append(n)
        from .portal_node import _session
        for members in groups.values():
            if len(members) == 1:
                v = views[members[0]]
                if getattr(v, "_group_shared", False):
                    v._group_shared = False
                    v.use_session(_session("isolated"))
            else:
                session = next((views[m].session for m in members if getattr(views[m], "_group_shared", False)), None) \
                    or views[members[0]].session
                for m in members:
                    views[m]._group_shared = True
                    views[m].use_session(session)

    def refuse_connection(self, a: str, b: str, reason: str) -> None:
        """Visible feedback for a refused pair: a toast plus a red flash on both nodes (instead of silently ignoring the drop)."""
        self.emit("connection-refused", a, b, reason)
        toast = Gtk.Label(label=reason, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=24)
        toast.add_css_class("maestri-toast")
        self.add_overlay(toast)
        flashed = [self.frames[n] for n in (norm(a), norm(b)) if n in self.frames]
        for f in flashed:
            f.add_css_class("maestri-refused")
        def done():
            for f in flashed:
                f.remove_css_class("maestri-refused")
            self.remove_overlay(toast)
            return False
        GLib.timeout_add(1800, done)

    def set_tool(self, tool: str) -> None:
        self.controller.set_tool(tool)

    # ---- files dropped on the empty canvas (FileTree rows, Nautilus...) -> preview nodes -------------
    def _install_file_drop(self) -> None:
        """Bubble-phase DropTarget: a terminal / sticky note under the pointer has its own target and wins; only drops on the empty
        background reach this one, which emits `files-dropped(paths, canvas_x, canvas_y)` for the app to create preview nodes."""
        t = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        t.set_gtypes([Gdk.FileList, Gio.File])
        t.connect("motion", self._file_drop_motion)
        t.connect("leave", lambda *_: self.remove_css_class("maestri-canvas-drop-hover"))
        t.connect("drop", self._file_drop)
        self.add_controller(t)
        self.file_drop_target = t

    def _file_drop_motion(self, _t, x: float, y: float) -> Gdk.DragAction:
        ok = isinstance(self.target_at(x, y), Background)
        (self.add_css_class if ok else self.remove_css_class)("maestri-canvas-drop-hover")
        return Gdk.DragAction.COPY if ok else 0

    def _file_drop(self, _t, value, x: float, y: float) -> bool:
        self.remove_css_class("maestri-canvas-drop-hover")
        if not isinstance(self.target_at(x, y), Background):
            return False   # over a node without its own drop target (portal, file tree): not a canvas drop
        files = value.get_files() if isinstance(value, Gdk.FileList) else [value] if isinstance(value, Gio.File) else []
        paths = [f.get_path() for f in files if f.get_path()]   # local (or FUSE-mounted) files only
        if not paths:
            return False
        p = self.viewport.to_canvas(Point(x, y))
        self.emit("files-dropped", paths, p.x, p.y)
        return True

    # ---- canvas-drawn nodes (text / shape / freehand: painted by Cairo, no NodeFrame) ------------------
    def set_node_content(self, node_id: str, content: dict) -> None:
        """Replace the persisted content of a drawn node (after the app applied an edit) and repaint."""
        node = self.scene.get(node_id)
        if node is not None:
            node.content = content
            self.controller.dirty = True

    def edit_drawn_text(self, node_id: str) -> bool:
        """Open the inline editor on a text label or a shape caption. The result arrives as `drawn-content-changed`."""
        node = self.scene.get(node_id)
        if node is None or node.kind not in ("text", "shape") or node.locked:
            return False
        if self._drawn_editor is None:
            self._drawn_editor = DrawnTextEditor(self)
        vp, f = self.viewport, node.frame
        tl = vp.to_screen(Point(f.x, f.y))
        nid, base = node.id, dict(node.content or {})

        def commit(text: str) -> None:
            self.emit("drawn-content-changed", nid, {**base, "text": text})

        self._drawn_editor.open(nid, str(base.get("text") or ""), (tl.x, tl.y, f.w * vp.zoom, f.h * vp.zoom), commit)
        return True

    def _drawn_target(self, x: float, y: float) -> Target | None:
        """Drawn nodes are not widgets, so GTK picking cannot see them: hit-test them in canvas space (topmost first).
        The body drags the node (a header role); a selected node also exposes its 8 resize edges."""
        p = self.viewport.to_canvas(Point(x, y))
        tol = 6.0 / self.viewport.zoom
        for n in self.scene.descending():
            if n.kind not in DRAWN_KINDS:
                continue
            f = n.frame
            if not f.inset(-tol, -tol).contains(p):
                continue
            if n.id in self.scene.selection and not n.locked:
                left, right = abs(p.x - f.min_x) <= tol, abs(p.x - f.max_x) <= tol
                top, bottom = abs(p.y - f.min_y) <= tol, abs(p.y - f.max_y) <= tol
                edge = {(True, False, True, False): Edge.TOP_LEFT, (False, True, True, False): Edge.TOP_RIGHT,
                        (True, False, False, True): Edge.BOTTOM_LEFT, (False, True, False, True): Edge.BOTTOM_RIGHT,
                        (True, False, False, False): Edge.LEFT, (False, True, False, False): Edge.RIGHT,
                        (False, False, True, False): Edge.TOP, (False, False, False, True): Edge.BOTTOM}.get((left, right, top, bottom))
                if edge is not None:
                    return NodeEdge(n.id, edge)
            if f.contains(p):
                return NodeHeader(n.id)
        return None

    # ---- engine -> GTK ---------------------------------------------------------------------------
    def _on_event(self, e: Event) -> None:
        if isinstance(e, NodesMoved):
            for i, r in e.frames.items():
                self.emit("node-moved", i, r.x, r.y, r.w, r.h, e.final)
        elif isinstance(e, NodeResized):
            self.emit("node-resized", e.id, e.frame.x, e.frame.y, e.frame.w, e.frame.h, e.final)
        elif isinstance(e, SelectionChanged):
            self._sync_selection_css()
            self.emit("selection-changed", e.ids)
        elif isinstance(e, ConnectRequested):
            na, nb = self.scene.nodes.get(norm(e.a)), self.scene.nodes.get(norm(e.b))
            plan = plan_connection(e.a, na.kind if na else "", e.side_a, e.b, nb.kind if nb else "", e.side_b)
            if isinstance(plan, str):
                self.refuse_connection(e.a, e.b, plan)
            else:
                self.emit("connect-requested", e.a, e.side_a, e.b, e.side_b)
        elif isinstance(e, DeleteRequested):
            self.emit("delete-requested", e.ids)
        elif isinstance(e, DuplicateRequested):
            self.emit("duplicate-requested", e.frames)
        elif isinstance(e, DrawRequested):
            self.emit("draw-requested", e.kind, e.frame.x, e.frame.y, e.frame.w, e.frame.h)
        elif isinstance(e, FreehandDrawn):
            self.emit("freehand-drawn", e.tool, e.frame.x, e.frame.y, e.frame.w, e.frame.h, [list(p) for p in e.points])
        elif isinstance(e, ToolChanged):
            self.emit("tool-changed", e.tool)
        elif isinstance(e, ViewportChanged):
            self.emit("viewport-changed", e.x, e.y, e.zoom)

    def _sync_selection_css(self) -> None:
        for i, f in self.frames.items():
            (f.add_css_class if i in self.scene.selection else f.remove_css_class)("selected")

    def _tick(self, _w: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        w, h = self.get_width(), self.get_height()
        self.controller.size = (float(w), float(h))
        moving = self.controller.ropes.tick()
        self._place_nodes(w, h)
        connecting = self.controller.overlay.connecting is not None
        if connecting != self.has_css_class("maestri-connecting"):   # reveal every pin while a rope is being dragged
            (self.add_css_class if connecting else self.remove_css_class)("maestri-connecting")
        if self.controller.dirty or moving:
            self.controller.dirty = False
            self.layer.queue_draw()
            self.top_layer.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _place_nodes(self, w: int, h: int) -> None:
        """Position/scale node widgets. Skips GTK calls when nothing changed; hides nodes outside view+margin (saves terminal rendering)."""
        vp = self.viewport
        view = vp.visible_rect(w, h)
        for i, frame in self.frames.items():
            item = self.scene.nodes[i]
            visible = view.intersects(item.frame)
            if frame.get_visible() != visible:
                frame.set_visible(visible)
            if not visible:
                continue
            key = (item.frame, vp.origin, vp.zoom, item.z)
            if self._last_applied.get(i) == key:
                continue
            self._last_applied[i] = key
            f = item.frame
            frame.set_size_request(round_half_away(f.w), round_half_away(f.h))
            t = Gsk.Transform.new().translate(Graphene.Point().init((f.x - vp.origin.x) * vp.zoom, (f.y - vp.origin.y) * vp.zoom)).scale(vp.zoom, vp.zoom)
            self.fixed.set_child_transform(frame, t)
        # stacking follows z: reorder only when the order actually differs
        order = [self.frames[n.id] for n in self.scene.ascending() if n.id in self.frames]
        if order != self._stack:
            prev = None
            for fr in order:
                fr.insert_after(self.fixed, prev)
                prev = fr
            self._stack = order

    # ---- GTK -> engine ---------------------------------------------------------------------------
    @staticmethod
    def _mods(state: Gdk.ModifierType) -> Mods:
        m = Mods.NONE
        if state & Gdk.ModifierType.SHIFT_MASK: m |= Mods.SHIFT
        if state & Gdk.ModifierType.CONTROL_MASK: m |= Mods.CTRL
        if state & Gdk.ModifierType.ALT_MASK: m |= Mods.ALT
        return m

    def target_at(self, x: float, y: float) -> Target:
        """Resolve what is under (x, y) via GTK picking (transform-aware). Chrome widgets carry role tags; anything else inside a node is content."""
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self:
            role = getattr(w, _ROLE, None)
            if role:
                kind, payload = role
                if kind == "header": return NodeHeader(payload)
                if kind == "edge": return NodeEdge(*payload)
                if kind == "pin": return NodePin(*payload)
                if kind == "content": return NodeContent(payload)
            if isinstance(w, NodeFrame):
                return NodeContent(w.node_id)   # padding/border of a frame that is not tagged: leave it to GTK
            w = w.get_parent()
        return self._drawn_target(x, y) or Background()

    def _note_name_taken(self, node_id: str, name: str) -> bool:
        return any(isinstance(f.content, StickyNoteView) and f.node_id != node_id and f.content.meta.name.lower() == name.lower()
                   for f in self.frames.values())

    def _header_double_click(self, g: Gtk.GestureDrag, target: Target, x: float, y: float) -> None:
        """Two presses on the same node header within 400 ms / 6 px: tell the content (a note starts renaming)."""
        if not isinstance(target, NodeHeader):
            return
        now = g.get_current_event_time()
        last = getattr(self, "_last_header_press", None)
        if last and last[0] == target.id and now - last[1] < 400 and abs(x - last[2]) < 6 and abs(y - last[3]) < 6:
            self._last_header_press = None
            frame = self.frames.get(target.id)
            handler = getattr(frame.content, "on_header_double_click", None) if frame is not None else None
            if frame is None:   # a drawn node: double-click edits its text (label or shape caption)
                handler = lambda nid=target.id: self.edit_drawn_text(nid)
            if handler is not None:
                GLib.idle_add(lambda: (handler(), False)[1])   # after this press finishes, so the drag gesture is not confused
        else:
            self._last_header_press = (target.id, now, x, y)

    def _drag_begin(self, g: Gtk.GestureDrag, x: float, y: float) -> None:
        target = self.target_at(x, y)
        self._header_double_click(g, target, x, y)
        frame = self.frames.get(target.id) if isinstance(target, NodeContent) else None
        term = terminal_of(frame.content) if frame is not None else None
        if term is not None:
            term.grab_focus()   # click into a terminal = keyboard goes to the shell
        elif not self._focus_in_node():
            self.grab_focus()
        ev = g.get_current_event()
        mods = self._mods(ev.get_modifier_state() if ev else 0)
        claimed = self.controller.press(Point(x, y), g.get_current_button(), mods, target)
        g.set_state(Gtk.EventSequenceState.CLAIMED if claimed else Gtk.EventSequenceState.DENIED)
        self._apply_cursor()

    def _drag_update(self, g: Gtk.GestureDrag, ox: float, oy: float) -> None:
        ok, sx, sy = g.get_start_point()
        ev = g.get_current_event()
        self.controller.move(Point(sx + ox, sy + oy), self._mods(ev.get_modifier_state() if ev else 0))
        self._apply_cursor()

    def _drag_end(self, g: Gtk.GestureDrag, ox: float, oy: float) -> None:
        ok, sx, sy = g.get_start_point()
        ev = g.get_current_event()
        self.controller.release(Point(sx + ox, sy + oy), self._mods(ev.get_modifier_state() if ev else 0))
        self._apply_cursor()

    def _motion(self, _c: Gtk.EventControllerMotion, x: float, y: float) -> None:
        self._pointer = Point(x, y)
        if not self.controller.busy:
            t = self.target_at(x, y)
            self.set_cursor_from_name({NodeHeader: "grab", NodePin: "crosshair"}.get(type(t)) or (t.edge.cursor if isinstance(t, NodeEdge) else
                                      ("crosshair" if self.controller.tool != "select" and isinstance(t, Background) else
                                       ("grab" if self.controller.space_held else None))))

    def _apply_cursor(self) -> None:
        if self.controller.cursor:
            self.set_cursor_from_name(self.controller.cursor)

    def _scroll(self, c: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        ev = c.get_current_event()
        mods = self._mods(ev.get_modifier_state() if ev else 0)
        # Over a node's content a plain scroll belongs to that widget (terminal scrollback, text view): only Ctrl zooms there.
        if not mods & Mods.CTRL and isinstance(self.target_at(self._pointer.x, self._pointer.y), (NodeContent,)):
            return False
        if c.get_unit() == Gdk.ScrollUnit.WHEEL:
            dx, dy = dx * WHEEL_NOTCH_PX, dy * WHEEL_NOTCH_PX
        return self.controller.scroll(self._pointer, dx, dy, mods)

    def _pinch(self, _g: Gtk.GestureZoom, scale: float) -> None:
        self.controller.pinch(self._pointer, scale / self._pinch_prev)   # scale is cumulative since 'begin'
        self._pinch_prev = scale

    def _focus_widget(self) -> Gtk.Widget | None:
        root = self.get_root()
        return root.get_focus() if root else None

    def _focus_in_node(self) -> bool:
        f = self._focus_widget()
        return f is not None and f is not self and f.is_ancestor(self)

    def _focus_in_terminal(self) -> bool:
        f = self._focus_widget()
        while f is not None and f is not self:
            if is_terminal(f):
                return True
            f = f.get_parent()
        return False

    _TERMINAL_PASSTHROUGH = frozenset({"equal", "plus", "minus", "underscore", "0"})   # Ctrl+=/-/0 still zoom the canvas

    def _key_pressed(self, _c: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        name = (Gdk.keyval_name(keyval) or "").lower()
        mods = self._mods(state)
        # A focused shell owns the keyboard (Ctrl+W = delete word, Ctrl+A = line start...): only canvas zoom chords are taken.
        if self._focus_in_terminal() and not (mods & Mods.CTRL and name in self._TERMINAL_PASSTHROUGH):
            return False
        return self.controller.key(name, mods, canvas_focused=not self._focus_in_node())

    def _install_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
