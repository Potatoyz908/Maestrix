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
from .context_toolbar import ContextToolbarHost
from .gi_env import webkit_available
from .note_model import NoteMeta
from .portal_model import PortalMeta
from .note_storage import CoreNotesClient, CoreNoteStorage, LocalNoteStorage, MemoryNoteStorage
from .sticky_note import StickyNoteView
from .terminal_features import TerminalFeatures
from .interaction import (WHEEL_NOTCH_PX, Background, CanvasController, ConnectionTarget, ConnectRequested, CopyRequested, DeleteRequested,
                          DrawRequested, DuplicateRequested, Event, FreehandDrawn, JumpToTerminal, Mods, NodeContent, NodeEdge, NodeHeader,
                          NodePin, NodeResized, NodesMoved, NodeZChanged, PasteRequested, SelectionChanged, Target, ToolChanged,
                          ViewportChanged)
from .renderer import CairoRenderer
from .resize import Edge
from .scene import WIDGET_KINDS, ConnItem, NodeItem, Scene, norm
from .viewport import Viewport

HEADER_H = 32   # CanvasNodeConstants.headerHeight
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
    from .gi_env import ensure_typelib_paths
    ensure_typelib_paths()   # honours MAESTRI_TYPELIB_PATH too (isolated HOME in tests)
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
                    on_exit=None, command: str | None = None) -> Gtk.Widget | None:
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

    if argv is None:
        if command and command.strip():
            spawn_argv = ["/bin/bash", "-i", "-l", "-c", f"{command.strip()}; exec bash"]
        else:
            spawn_argv = default_shell_argv()
    else:
        spawn_argv = argv

    term.spawn_async(Vte.PtyFlags.DEFAULT, cwd or os.path.expanduser("~"), spawn_argv, envv,
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
            if not c.controller.overlay.is_empty() or c.scene.selection:
                c.renderer.draw_overlay(cr, c.scene, c.viewport, c.controller.overlay)
        else:
            c.renderer.draw(cr, c.scene, c.viewport, w, h, c.controller.ropes, None)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._da:
            Gtk.DrawingArea.do_snapshot(self, snapshot)
            return
        w, h, s = self.get_width(), self.get_height(), self.get_scale_factor()
        if w <= 0 or h <= 0 or (self._top and self._canvas.controller.overlay.is_empty() and not self._canvas.scene.selection):
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
        self.title.add_css_class("maestri-node-title")
        # macOS NodeHeader: kind icon (11 pt) + title 12 pt medium; no close button (Delete / Ctrl+W / menu remove a node).
        self.on_close = on_close
        self.header_icon = Gtk.Image.new_from_icon_name(HEADER_ICONS.get(item.kind, "maestri-square-symbolic"))
        self.header_icon.set_pixel_size(12)
        self.header_icon.add_css_class("maestri-node-icon")
        setattr(self.header_icon, _ROLE, ("header", item.id))
        header.append(self.header_icon)
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
            title_click = Gtk.GestureClick()
            title_click.set_button(1)
            title_click.connect("pressed", lambda _g, n_press, _x, _y, it=item, h=header: self._on_title_clicked(it, h, n_press))
            self.title.add_controller(title_click)
        for w in getattr(content, "header_right", ()):
            setattr(w, _ROLE, ("content", item.id))
            header.append(w)
        # Dynamic status badges (e.g. ⏱ active routine): set_badge(key, text) adds / updates / removes one.
        self.badges = Gtk.Box(spacing=4)
        self.badges.add_css_class("maestri-node-badges")
        setattr(self.badges, _ROLE, ("header", item.id))   # dragging over a badge still moves the node
        self._badges: dict[str, Gtk.Label] = {}
        header.append(self.badges)
        close = Gtk.Button(icon_name="maestri-close-symbolic", tooltip_text="Close")   # outline icon, not a "×" glyph
        close.add_css_class("flat")
        close.add_css_class("maestri-node-close")
        close.connect("clicked", lambda _b: self.on_close(self.node_id))
        setattr(close, _ROLE, ("content", item.id))   # a real button: it keeps its own click
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
        if item.kind in ("terminal", "stickyNote", "portal"):
            for side in ("left", "right"):
                self.add_overlay(self._pin(side))
        attach = getattr(content, "frame_attached", None)
        if attach is not None:
            attach(self)   # lets a content widget style the card and retag its header widgets
        # Terminal nodes (local VTE or core-backed VteTerminalView): Ctrl+Shift+C/V/A/F, find bar, links, bell, busy/idle.
        term = terminal_of(content)
        self.terminal_features = TerminalFeatures(term, self, on_activity=self._terminal_activity,
                                                 on_unread=self._terminal_unread) if term is not None else None
        if self.terminal_features is not None:
            self.terminal_features.on_font_size = self._terminal_font_size

    def _terminal_activity(self, busy: bool) -> None:
        canvas = self.get_ancestor(CanvasWidget)
        if canvas is not None:
            canvas.emit("terminal-activity", self.node_id, busy)

    def _terminal_font_size(self, size: float) -> None:
        canvas = self.get_ancestor(CanvasWidget)
        if canvas is not None:
            canvas.emit("terminal-font-size", self.node_id, float(size))

    def _terminal_unread(self, unread: bool) -> None:
        canvas = self.get_ancestor(CanvasWidget)
        if canvas is not None:
            canvas.emit("terminal-unread", self.node_id, unread)

    def set_number_badge(self, n: int | None) -> None:
        """K9: the Ctrl+number shortcut badge shown in a terminal header while Ctrl is held (None hides it)."""
        self.set_badge("number", None if n is None else str(n), tooltip="" if n is None else f"Ctrl+{n}",
                       css=("maestri-number-badge",))

    def start_inline_rename(self) -> None:
        header = self.title.get_parent()
        if header is None or not isinstance(header, Gtk.Box):
            return
        entry = Gtk.Entry(text=self.title.get_label())
        entry.set_hexpand(True)
        entry.add_css_class("maestri-title-entry")
        setattr(entry, _ROLE, ("content", self.node_id))

        try:
            header.remove(self.title)
            header.prepend(entry)
            entry.grab_focus()
        except Exception:
            return

        def finish_rename(_w):
            new_val = entry.get_text().strip()
            if new_val:
                self.title.set_label(new_val)
                canvas = self.get_ancestor(CanvasWidget)
                if canvas is not None:
                    node_item = canvas.scene.get(self.node_id)
                    if node_item:
                        node_item.title = new_val
                    canvas.controller.dirty = True
                    canvas.emit("node-title-changed", self.node_id, new_val)
            if entry.get_parent() == header:
                header.remove(entry)
                header.prepend(self.title)

        entry.connect("activate", finish_rename)
        focus_c = Gtk.EventControllerFocus()
        focus_c.connect("leave", lambda _c: finish_rename(entry))
        entry.add_controller(focus_c)

    def _on_title_clicked(self, item: NodeItem, header: Gtk.Box, n_press: int) -> None:
        if n_press == 2:
            self.start_inline_rename()

    def set_badge(self, key: str, text: str | None, tooltip: str = "", css: tuple[str, ...] = (), icon: str | None = None) -> None:
        """Show `text` as the header badge `key`; None removes it. `css` replaces the badge's extra style classes.
        `icon` = a symbolic icon name drawn before the text (outline, theme colour; the text may then be "" or a count)."""
        lbl = self._badges.get(key)
        if text is None:
            if lbl is not None:
                if lbl.maestri_icon is not None:
                    self.badges.remove(lbl.maestri_icon)
                self.badges.remove(lbl)
                del self._badges[key]
            return
        if lbl is None:
            lbl = Gtk.Label()
            lbl.add_css_class("maestri-node-badge")
            setattr(lbl, _ROLE, ("header", self.node_id))
            lbl.maestri_css = ()
            lbl.maestri_icon = None
            self._badges[key] = lbl
            self.badges.append(lbl)
        if icon is not None:
            if lbl.maestri_icon is None:
                lbl.maestri_icon = Gtk.Image()
                lbl.maestri_icon.add_css_class("maestri-node-badge-icon")
                setattr(lbl.maestri_icon, _ROLE, ("header", self.node_id))
                self.badges.insert_child_after(lbl.maestri_icon, lbl.get_prev_sibling())
            lbl.maestri_icon.set_from_icon_name(icon)
            lbl.maestri_icon.set_tooltip_text(tooltip or None)
        elif lbl.maestri_icon is not None:
            self.badges.remove(lbl.maestri_icon)
            lbl.maestri_icon = None
        lbl.set_label(text)
        lbl.set_visible(bool(text) or lbl.maestri_icon is None)
        lbl.set_tooltip_text(tooltip or None)
        for w in (lbl, lbl.maestri_icon):
            if w is None:
                continue
            for c in lbl.maestri_css:
                w.remove_css_class(c)
            for c in css:
                w.add_css_class(c)
        lbl.maestri_css = tuple(css)

    def badge(self, key: str) -> Gtk.Label | None:
        return self._badges.get(key)

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


HEADER_ICONS = {"terminal": "maestri-terminal-symbolic", "stickyNote": "maestri-note-symbolic",
                "portal": "maestri-globe-symbolic", "fileTree": "maestri-file-tree-symbolic"}
# CanvasNodeConstants: cornerRadius 10, headerHeight 32; NodeHeader: title 12 pt medium, icon 11 pt, padding 0 8.
SHELL_CSS = """
.maestri-node { border-radius: 10px; }
.maestri-node-header { min-height: 32px; padding: 0 8px; border-radius: 10px 10px 0 0; }
.maestri-node-header .maestri-node-title { font-size: 12px; font-weight: 500; }
.maestri-file-tree-list > row { min-height: 32px; padding: 0 6px; }
.maestri-file-tree-list treeexpander indent { -gtk-icon-size: 20px; min-width: 20px; }
.maestri-file-name { font-size: 13px; }
.maestri-file-name.git-modified { color: #FF9500; }
.maestri-file-name.git-added, .maestri-file-name.git-untracked { color: #34C759; }
.maestri-file-name.git-deleted { color: #FF3B30; }
.maestri-node-header image.maestri-node-icon { -gtk-icon-size: 12px; min-width: 12px; min-height: 12px; padding: 0; }
"""

CSS = b"""
.maestri-node { background: @window_bg_color; border: 1px solid alpha(currentColor, .25); border-radius: 8px; }
.maestri-node.maestri-refused { border: 2px solid #ef4444; }
.maestri-toast { background: #ef4444; color: white; padding: 6px 14px; border-radius: 14px; }
.maestri-node.selected { }   /* the dashed selection ring is drawn by Cairo, 3 px outside (renderer._selection) */
.maestri-node-header { background: alpha(currentColor, .06); padding: 0 8px; border-radius: 7px 7px 0 0; }
.maestri-node-badge { font-size: 12px; padding: 0 5px; border-radius: 8px; background: alpha(currentColor, .10); }
.maestri-node-badge.routine-paused, .maestri-node-badge-icon.routine-paused { opacity: .45; }
.maestri-node-badge-icon { margin-left: 2px; }
.maestri-node-badge-icon.maestri-term-status { -gtk-icon-size: 8px; }
.maestri-node-badge-icon.maestri-term-status { -gtk-icon-size: 10px; }
.maestri-node-badge-icon.maestri-term-status.busy { color: #23d18b; animation: maestri-badge-pulse 0.9s ease-in-out infinite alternate; }
.maestri-node-badge.maestri-term-unread { background: #0a84ff; color: white; font-weight: 700; font-size: 10px; }
.maestri-node.term-focused { box-shadow: 0 0 0 2px alpha(#0a84ff, .55); }
.maestri-node-badge-icon.maestri-term-status.idle { color: alpha(currentColor, .35); }
.maestri-node-badge-icon.maestri-badge-attention { color: #ff9f0a; animation: maestri-badge-pulse 0.8s ease-in-out infinite alternate; }
.maestri-node-badge.maestri-badge-exited { color: #ff453a; background: alpha(#ff453a, .12); }
.maestri-node-badge.maestri-number-badge { font-weight: 700; background: #0a84ff; color: white; min-width: 12px; }
.maestri-term-search { background: @window_bg_color; border: 1px solid alpha(currentColor, .2); border-radius: 8px; padding: 2px 4px;
                       box-shadow: 0 2px 8px alpha(black, .25); }
.maestri-node-badge-icon.routine-running { color: #23d18b; animation: maestri-badge-pulse 1s ease-in-out infinite alternate; }
.maestri-git-dirty-dot { min-width: 6px; min-height: 6px; border-radius: 50%; background-color: #f59e0b; }
.maestri-node-badge.routine-running { color: #23d18b; animation: maestri-badge-pulse 1s ease-in-out infinite alternate; }
@keyframes maestri-badge-pulse { from { opacity: 1; } to { opacity: .35; } }
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
        "terminal-font-size": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),                       # id, new size (Ctrl +/-/0)
        "terminal-unread": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),                           # id, unread (False = read)
        "terminal-activity": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),                         # id, busy (False = idle)
        "copy-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),                              # K9/K10 Ctrl+C: tuple[str]
        "paste-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),                             # K9/K10 Ctrl+V: Point | None
        "node-lock-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),                         # K9: id, locked
        "node-z-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),                             # K9: id, zIndex
        "connection-rope-settled": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                 # id, [[x, y], ...] (21 pts)
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),                             # fileTree id, path
        "file-tree-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                       # fileTree id, {rootPath, viewMode}
        "ask-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),                              # fileTree id, question + quoted diff
        "files-dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),                  # [paths], canvas x, y
        "note-meta-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                       # stickyNote id, StickyNoteContent dict
        "portal-meta-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),                     # portal id, PortalContent dict
        "portal-popup": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),                               # portal id, url (target=_blank)
        "snapping-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),                              # alignment guides on/off
        "node-title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),                         # node id, new title
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
        self.active_terminal_id: str | None = None   # last focused / solely selected terminal node
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
        self.context_toolbar = ContextToolbarHost(self, _ROLE)   # floating Note/Drawing toolbars (above every layer)
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
        keys.connect("key-released", self._key_released)
        self.add_controller(keys)
        self._numbers_shown = False
        # Rope points are persisted when the simulation falls asleep (ConnectionOverlay -> ropePoints, audit 3.2).
        self.connections_client = None   # set by the window: any object with update_connection(id, points, workspace_id=)
        self.connections_workspace_id: str | None = None
        self._settled_points: dict[str, list] = {}
        self.controller.ropes.on_sleep = self._ropes_slept

        self._install_file_drop()
        self.add_tick_callback(self._tick)

    # ---- public API ---------------------------------------------------------------------------
    def create_terminal(self, cwd: str | None = None, argv: list[str] | None = None,
                        env: dict[str, str] | None = None, on_exit=None,
                        command: str | None = None) -> Gtk.Widget | None:
        """Create a Vte.Terminal running on the canvas."""
        return create_terminal(cwd=cwd, argv=argv, env=env, on_exit=on_exit, command=command)

    def add_node(self, item: NodeItem, content: Gtk.Widget | None = None, cwd: str | None = None,
                 env: dict[str, str] | None = None, focus: bool = False, note: dict | None = None,
                 portal: dict | None = None, file_tree: dict | None = None,
                 command: str | None = None) -> None:
        """`content=None` builds the native content: a VTE login shell for "terminal" (cwd/env/command apply to it), a FileTreeView rooted at
        `cwd` (default ~) for "fileTree" (`file_tree` = the persisted FileTreeContent dict: viewMode), a StickyNoteView for
        "stickyNote" (`note` = the persisted StickyNoteContent dict).
        `focus` gives the content the keyboard."""
        self.scene.add_node(item)
        self.controller.dirty = True
        if item.kind == "terminal" and content is None:
            nid = item.id
            term_cmd = command
            if term_cmd is None and isinstance(item.content, dict):
                term_cmd = item.content.get("command")
            content = create_terminal(cwd=cwd, env=env, command=term_cmd, on_exit=lambda st: self._local_terminal_exited(nid, st))
            if content is None:
                content = Gtk.Label(label="Vte 3.91 (GTK 4) not available")
        elif item.kind == "fileTree" and content is None:
            nid = item.id
            # root: explicit cwd, else the persisted rootPath, else ~ (the window passes the workspace's working directory)
            content = FileTreeView(cwd or (file_tree or {}).get("rootPath") or None, view_mode=(file_tree or {}).get("viewMode", "list"))
            content.connect("file-activated", lambda _v, path: self.emit("file-activated", nid, path))
            content.connect("ask-requested", lambda _v, text: self.emit("ask-requested", nid, text))
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
            content.can_remove = lambda: bool(self.scene.get(nid) and not self.scene.get(nid).locked)
            content.connect("meta-changed", lambda _v, d: self.emit("note-meta-changed", nid, d))
            content.connect("delete-requested", lambda _v: self.emit("delete-requested", (nid,)))
            content.connect("file-dropped", lambda v, path: v.adopt_file(path))   # drop a .md on a note = use that file
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
            frame = NodeFrame(item, content, on_close=lambda i: self.request_delete((i,)))
            self.frames[item.id] = frame
            self.fixed.put(frame, 0, 0)
            if item.kind == "terminal":
                # "Active terminal" = the last terminal that held the keyboard (or was selected alone): the target of
                # "Ask agent" from a diff. The focus controller fires when focus enters the content or any descendant.
                focus_ctl = Gtk.EventControllerFocus()
                focus_ctl.connect("enter", lambda _c, i=item.id: setattr(self, "active_terminal_id", i))
                content.add_controller(focus_ctl)
            if focus:
                GLib.idle_add(lambda: (content.grab_focus(), False)[1])

    def set_node_badge(self, node_id: str, key: str, text: str | None, tooltip: str = "", css: tuple[str, ...] = (),
                       icon: str | None = None) -> bool:
        """Header badge on a widget node (see NodeFrame.set_badge). False when the node has no frame."""
        frame = self.frames.get(norm(node_id))
        if frame is None:
            return False
        frame.set_badge(key, text, tooltip, css, icon)
        return True

    def remove_node(self, node_id: str) -> None:
        k = norm(node_id)
        if self.active_terminal_id == k:
            self.active_terminal_id = None
        frame = self.frames.pop(k, None)
        if frame is not None:
            if frame.terminal_features is not None:
                frame.terminal_features.cleanup()
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

    def request_delete(self, ids) -> None:
        """Del / Ctrl+W / close button: notes go through StickyNoteView.remove() first (a managed note deletes its .md, a custom
        one is only unlinked) and emit "delete-requested" when that is done; every other node is requested directly."""
        rest = []
        for nid in ids:
            item = self.scene.get(nid)
            if item is not None and item.locked:
                continue
            frame = self.frames.get(norm(nid))
            if frame is not None and isinstance(frame.content, StickyNoteView):
                frame.content.remove()
            else:
                rest.append(nid)
        if rest:
            self.emit("delete-requested", tuple(rest))

    # ---- terminal state (Sprint 9: exit indicator, prefs) -------------------------------------------------------------
    def _local_terminal_exited(self, node_id: str, status: int) -> None:
        code = os.waitstatus_to_exitcode(status) if status >= 0 else status
        self.set_terminal_exited(node_id, code)
        self.emit("terminal-exited", node_id, status)

    def set_terminal_exited(self, node_id: str, code: int | None) -> None:
        """Show (or clear with None) the "process exited" header indicator; the window calls it on the core's terminalExited."""
        frame = self.frames.get(norm(node_id))
        if frame is not None and frame.terminal_features is not None:
            frame.terminal_features.set_exited(code)

    def apply_terminal_prefs(self, scrollback_memory_limit: bool = False) -> None:
        """Preferences consumers: scrollback length of every terminal (macOS uses 10000 lines; "limit memory" keeps 2000)."""
        lines = 2000 if scrollback_memory_limit else 10000
        self.terminal_scrollback_lines = lines
        for f in self.frames.values():
            term = terminal_of(f.content)
            if term is not None:
                term.set_scrollback_lines(lines)

    def update_node_frame(self, node_id: str, frame: Rect) -> None:
        self.scene.set_frame(node_id, frame)
        self.controller._reanchor({norm(node_id)})
        self.controller.dirty = True

    def add_connection(self, conn: ConnItem, rope_points=None) -> None:
        points = [Point(float(p[0]), float(p[1])) for p in rope_points] if rope_points else None
        self.controller.add_connection(conn, points)
        self._sync_portal_sessions()

    def set_connection_status(self, conn_id: str, status: str) -> None:
        """Rope state from the core (ConnectionStatus): "communicating" draws it green and solid, the rest grey dashed."""
        c = self.scene.connections.get(conn_id)
        if c is not None and c.status != status:
            c.status = status
            self.controller.dirty = True

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

    @property
    def snapping(self) -> bool:
        """Smart alignment guides while dragging nodes (alignment_guides.py). Ctrl+drag tile snapping works either way."""
        return self.controller.snapping

    def set_snapping(self, on: bool) -> None:
        on = bool(on)
        if on == self.controller.snapping:
            return
        self.controller.snapping = on
        self.controller.overlay.guides = []
        self.controller.dirty = True
        self.emit("snapping-changed", on)

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
            self.context_toolbar.content_changed(node.id, content)

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
            if len(e.ids) == 1:
                node = self.scene.nodes.get(norm(next(iter(e.ids))))
                if node is not None and node.kind == "terminal":
                    self.active_terminal_id = node.id
            self.emit("selection-changed", e.ids)
        elif isinstance(e, ConnectRequested):
            na, nb = self.scene.nodes.get(norm(e.a)), self.scene.nodes.get(norm(e.b))
            plan = plan_connection(e.a, na.kind if na else "", e.side_a, e.b, nb.kind if nb else "", e.side_b)
            if isinstance(plan, str):
                self.refuse_connection(e.a, e.b, plan)
            else:
                self.emit("connect-requested", e.a, e.side_a, e.b, e.side_b)
        elif isinstance(e, DeleteRequested):
            self.request_delete(e.ids)
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
        elif isinstance(e, NodeZChanged):
            self.emit("node-z-changed", e.id, e.z)
        elif isinstance(e, JumpToTerminal):
            self.jump_to_terminal(e.number)
        elif isinstance(e, CopyRequested):
            self.emit("copy-requested", e.ids)
            fn = getattr(self.get_root(), "copy_selected_nodes", None)   # K10 (window)
            if callable(fn):
                fn()
        elif isinstance(e, PasteRequested):
            self.emit("paste-requested", e.at)
            fn = getattr(self.get_root(), "paste_nodes", None)
            if callable(fn):
                fn(at=e.at)

    def _sync_selection_css(self) -> None:
        for i, f in self.frames.items():
            (f.add_css_class if i in self.scene.selection else f.remove_css_class)("selected")

    def _tick(self, _w: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        w, h = self.get_width(), self.get_height()
        self.controller.size = (float(w), float(h))
        moving = self.controller.ropes.tick()
        self._place_nodes(w, h)
        self.context_toolbar.sync(w, h)
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
        drawn = self._drawn_target(x, y)
        if drawn is not None:
            return drawn
        conn = self.scene.hit_connection(self.viewport.to_canvas(Point(x, y)), tol=8.0 / self.viewport.zoom,
                                         points_for=self.controller.ropes.points)
        return ConnectionTarget(conn.id) if conn is not None else Background()

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
        self.controller.hover(self._pointer)
        if self.controller.connect_mode:
            self.set_cursor_from_name("crosshair")
            return
        if not self.controller.busy:
            t = self.target_at(x, y)
            self._update_rope_hover(t)
            self.set_cursor_from_name({NodeHeader: "grab", NodePin: "crosshair"}.get(type(t)) or (t.edge.cursor if isinstance(t, NodeEdge) else
                                      ("crosshair" if self.controller.tool != "select" and isinstance(t, Background) else
                                       ("grab" if self.controller.space_held else None))))

    def _update_rope_hover(self, target: Target) -> None:
        """Hovering a rope names the two nodes it connects (tooltip) and thickens the rope."""
        cid = target.conn_id if isinstance(target, ConnectionTarget) else None
        if cid == getattr(self.scene, "hover_connection", None):
            return
        self.scene.hover_connection = cid
        conn = self.scene.connections.get(cid) if cid else None
        text = None
        if conn is not None:
            def name(nid: str) -> str:
                n = self.scene.get(nid)
                return (n.title if n is not None and n.title else None) or (nid[:8])
            text = f"{name(conn.a)}  ↔  {name(conn.b)}"
        self.set_tooltip_text(text)
        self.controller.dirty = True
        self.queue_draw()

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

    def _focus_in_text(self) -> bool:
        f = self._focus_widget()
        while f is not None and f is not self:
            if isinstance(f, (Gtk.TextView, Gtk.Editable)):
                return True
            f = f.get_parent()
        return False

    # Ctrl+=/-/0 still zoom the canvas and Ctrl+1..9 jump between terminals, even from inside a shell (macOS: Cmd+number)
    _TERMINAL_PASSTHROUGH = frozenset({"equal", "plus", "minus", "underscore", "0", *"123456789"})

    def _key_pressed(self, _c: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        name = (Gdk.keyval_name(keyval) or "").lower()
        mods = self._mods(state)
        if name in ("control_l", "control_r"):
            self.show_number_badges(True)   # macOS: holding Cmd shows each terminal's number
            return False
        # Ctrl+Z: canvas undo (moves, resizes, new/deleted nodes), unless a shell or a text field has the focus (they own it)
        if name == "z" and mods & Mods.CTRL and not mods & Mods.SHIFT and not self._focus_in_terminal() \
                and not self._focus_in_text():
            undo = getattr(self.get_root(), "canvas_undo", None)
            return bool(undo is not None and undo.undo())
        # A focused shell owns the keyboard (Ctrl+W = delete word, Ctrl+A = line start...): only canvas zoom chords are taken.
        if self._focus_in_terminal() and not (mods & Mods.CTRL and name in self._TERMINAL_PASSTHROUGH):
            return False
        return self.controller.key(name, mods, canvas_focused=not self._focus_in_node())

    def _key_released(self, _c, keyval: int, _code: int, _state) -> None:
        name = (Gdk.keyval_name(keyval) or "").lower()
        if name in ("control_l", "control_r"):
            self.show_number_badges(False)
        self.controller.key_release(name)

    # ---- K9: connect mode, terminal numbers, lock, rope persistence -----------------------------------------------
    def begin_connect_mode(self, node_id: str | None = None) -> bool:
        """Toolbar "Connection" / key L: a rope from `node_id` (default: the selected node) follows the pointer until a second node
        is clicked (-> "connect-requested") or Esc."""
        ok = self.controller.begin_connect_mode(norm(node_id) if node_id else None)
        if ok:
            self.grab_focus()   # Esc must reach the canvas
            self.set_cursor_from_name("crosshair")
        return ok

    def terminal_order(self) -> list[str]:
        """Terminal node ids in creation order (the order of the Ctrl+number shortcuts)."""
        return [nid for nid, n in self.scene.nodes.items() if n.kind == "terminal" and nid in self.frames]

    def show_number_badges(self, on: bool) -> None:
        if on == self._numbers_shown:
            return
        self._numbers_shown = on
        for i, nid in enumerate(self.terminal_order()):
            self.frames[nid].set_number_badge(i + 1 if on and i < 9 else None)

    def jump_to_terminal(self, n: int) -> bool:
        """Ctrl+n: select the n-th terminal (1-9), bring it into view if needed and give it the keyboard."""
        order = self.terminal_order()
        if not 1 <= n <= min(9, len(order)):
            return False
        return self.focus_node(order[n - 1])

    def focus_node(self, nid: str) -> bool:
        """Select a terminal, bring it into view and give it the keyboard (Ctrl+n, sidebar agent list)."""
        nid = nid.upper()
        if nid not in self.scene.nodes or nid not in self.frames:
            return False
        node = self.scene.nodes[nid]
        self.controller._set_selection({nid})
        if self.scene.raise_to_front(nid):
            self.emit("node-z-changed", nid, node.z)
        w, h = max(1, self.get_width()), max(1, self.get_height())
        view = self.viewport.visible_rect(w, h, margin=0)
        f = node.frame
        if not (view.contains(Point(f.x, f.y)) and view.contains(Point(f.max_x, f.max_y))):
            c = node.frame.center
            self.viewport.origin = Point(c.x - w / (2 * self.viewport.zoom), c.y - h / (2 * self.viewport.zoom))
            self.controller._viewport_changed()
        self.controller.dirty = True
        term = terminal_of(self.frames[nid].content)
        (term or self.frames[nid].content).grab_focus()
        return True

    def set_node_locked(self, node_id: str, locked: bool) -> None:
        """Lock / Unlock (menu): a locked node can't be moved, resized or deleted; "node-lock-changed" lets the window persist it."""
        node = self.scene.get(node_id)
        if node is None or node.locked == locked:
            return
        node.locked = locked
        frame = self.frames.get(node.id)
        if frame is not None:
            frame.set_badge("lock", "" if locked else None, tooltip="Locked", icon="maestri-lock-symbolic" if locked else None)
        self.emit("node-lock-changed", node.id, locked)

    def _ropes_slept(self, ropes: dict) -> None:
        """Rope simulation at rest: report connections whose points changed, and PUT them when a client is attached."""
        changed = {}
        for cid, pts in ropes.items():
            if cid.startswith("local-") or cid not in self.scene.connections:
                continue   # optimistic ropes have no server id yet
            flat = [[round(p.x, 2), round(p.y, 2)] for p in pts]
            if self._settled_points.get(cid) != flat:
                self._settled_points[cid] = flat
                changed[cid] = flat
        for cid, flat in changed.items():
            self.emit("connection-rope-settled", cid, flat)
        client = self.connections_client
        if client is None or not changed:
            return
        ws = self.connections_workspace_id

        def push() -> None:
            for cid, flat in changed.items():
                try:
                    client.update_connection(cid, flat, workspace_id=ws)
                except Exception:   # offline / unknown id: the next sleep retries with fresh points
                    self._settled_points.pop(cid, None)

        import threading
        threading.Thread(target=push, daemon=True).start()

    def _install_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        shell = Gtk.CssProvider()   # USER+1: the shell geometry must win over themes such as WhiteSur
        shell.load_from_string(SHELL_CSS)
        Gtk.StyleContext.add_provider_for_display(display, shell, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
