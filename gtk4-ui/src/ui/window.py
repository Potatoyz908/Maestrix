"""
Main ApplicationWindow for Open Maestri GTK 4 Native.
Integrates HeaderBar, Sidebar, CanvasWidget, and REST synchronization.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib

import os
import sys
from pathlib import Path

# Add paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))  # provides the canonical `maestri_canvas` package
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent
from maestri_core.client.api import MaestriClient
from maestri_core.signals.bus import bus
from maestri_core.sync import WorkspaceSync
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import WIDGET_KINDS, ConnItem, NodeItem
from maestri_canvas.viewport import Viewport
from maestri_canvas.widget import CanvasWidget
from .sidebar import SidebarView


class OpenMaestriWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, client: MaestriClient):
        super().__init__(application=app, title="Open Maestri (Native GTK 4)")
        self.client = client
        self.set_default_size(1280, 800)

        # 1. Load initial workspace or create default
        doc = self.client.get_workspace()
        if doc and doc.payload:
            self.document = doc
        else:
            payload = WorkspacePayload(
                name="Default",
                working_directory=os.path.expanduser("~")
            )
            # Add demo terminals if empty
            term_a = CanvasNode(
                frame=NodeFrame(x=9900, y=8600, width=420, height=300),
                node_type="terminal",
                content=TerminalContent(name="Terminal A", command="bash"),
                z_index=1
            )
            term_b = CanvasNode(
                frame=NodeFrame(x=10400, y=8650, width=420, height=300),
                node_type="terminal",
                content=TerminalContent(name="Terminal B", command="htop"),
                z_index=2
            )
            payload.nodes = [term_a, term_b]
            self.document = WorkspaceDocument(payload=payload)

        # 2. Setup HeaderBar
        self._setup_header_bar()

        # 3. Main Paned Layout (Sidebar + Canvas)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_position(250)

        self.sidebar = SidebarView(self.client)
        self.canvas_widget = CanvasWidget(viewport=self._viewport_from_payload())
        self.canvas_widget.set_hexpand(True)
        self.canvas_widget.set_vexpand(True)
        self._load_payload_into_canvas()
        self.canvas_widget.connect("node-moved", self._on_canvas_frame_changed)
        self.canvas_widget.connect("node-resized", self._on_canvas_frame_changed)
        self.canvas_widget.connect("viewport-changed", lambda _c, x, y, z: bus.emit("viewport-changed", x, y, z))
        self.canvas_widget.connect("delete-requested", self._on_canvas_delete_requested)
        self.canvas_widget.connect("connect-requested", self._on_canvas_connect_requested)

        self.paned.set_start_child(self.sidebar)
        self.paned.set_end_child(self.canvas_widget)
        self.set_child(self.paned)

        # 4. Setup Shortcuts
        self._setup_shortcuts()

        # 5. Real-time sync: bus events -> debounced snapshot -> REST (schemaVersion 2)
        self.sync = WorkspaceSync(
            self.client,
            get_document=lambda: self.document,
            get_viewport=lambda: (
                self.canvas_widget.viewport.origin.x,
                self.canvas_widget.viewport.origin.y,
                self.canvas_widget.viewport.zoom,
            ),
        )
        bus.connect("workspace-switched", self._on_workspace_switched)
        self.connect("close-request", self._on_close_request)

        # Apply dark theme styling
        self._setup_theme()

    def _setup_theme(self):
        css_provider = Gtk.CssProvider()
        css = """
        window {
            background-color: #161618;
            color: #e0e0e0;
        }
        headerbar {
            background-color: #1f1f22;
            border-bottom: 1px solid #2d2d30;
            color: #ffffff;
        }
        .sidebar-view {
            background-color: #1a1a1c;
            border-right: 1px solid #2d2d30;
        }
        listview, listbox {
            background-color: transparent;
            color: #d0d0d0;
        }
        listbox row:selected {
            background-color: #2b3b55;
            color: #ffffff;
        }
        button {
            background-color: #262629;
            color: #e5e5e5;
            border: 1px solid #38383c;
            border-radius: 6px;
            padding: 4px 10px;
        }
        button:hover {
            background-color: #323236;
        }
        """
        css_provider.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _setup_header_bar(self):
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        # Toggle Sidebar button
        btn_sidebar = Gtk.Button(label="☰")
        btn_sidebar.set_tooltip_text("Toggle Sidebar")
        btn_sidebar.connect("clicked", self._on_toggle_sidebar)
        header.pack_start(btn_sidebar)

        # Add Terminal button
        btn_term = Gtk.Button(label="➕ Terminal")
        btn_term.set_tooltip_text("Create new Terminal node")
        btn_term.connect("clicked", lambda b: self._create_node("terminal"))
        header.pack_start(btn_term)

        # Add Note button
        btn_note = Gtk.Button(label="📝 Nota")
        btn_note.set_tooltip_text("Create new Sticky Note")
        btn_note.connect("clicked", lambda b: self._create_node("stickyNote"))
        header.pack_start(btn_note)

        # Add Portal button
        btn_portal = Gtk.Button(label="🌐 Portal")
        btn_portal.set_tooltip_text("Create Web Portal")
        btn_portal.connect("clicked", lambda b: self._create_node("portal"))
        header.pack_start(btn_portal)

        # Add FileTree button
        btn_files = Gtk.Button(label="📁 Arquivos")
        btn_files.set_tooltip_text("Create File Explorer")
        btn_files.connect("clicked", lambda b: self._create_node("fileTree"))
        header.pack_start(btn_files)

        # Reset Zoom button (Right side)
        btn_zoom = Gtk.Button(label="🔍 100%")
        btn_zoom.set_tooltip_text("Reset Zoom to 100%")
        btn_zoom.connect("clicked", lambda b: self.reset_zoom())
        header.pack_end(btn_zoom)

        # Backend Connection status indicator
        is_online = self.client.is_connected()
        status_text = f"🟢 Porta {self.client.port}" if is_online else "⚪ Standalone"
        self.lbl_status = Gtk.Label(label=status_text)
        self.lbl_status.set_margin_end(8)
        header.pack_end(self.lbl_status)

    def _setup_shortcuts(self):
        """Ctrl+N (new terminal), Ctrl+S (save), Ctrl+0 (reset zoom), Ctrl+Q (quit).
        CAPTURE phase so they work even while a node's content widget (terminal/text view) has focus."""
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        bindings = (
            ("<Control>n", lambda: self._create_node("terminal")),
            ("<Control>s", lambda: self._save_to_backend()),
            ("<Control>0", lambda: self.reset_zoom()),
            ("<Control>q", lambda: self.close()),
        )
        for accel, fn in bindings:
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(lambda _w, _a, f=fn: (f(), True)[1]),
            ))
        self.add_controller(controller)

    def _on_toggle_sidebar(self, btn):
        is_visible = self.sidebar.get_visible()
        self.sidebar.set_visible(not is_visible)

    # ---- canvas <-> document -------------------------------------------------------------------
    def _viewport_from_payload(self) -> Viewport:
        p = self.document.payload
        ox = p.canvas_origin[0] if len(p.canvas_origin) > 0 else 9800.0
        oy = p.canvas_origin[1] if len(p.canvas_origin) > 1 else 8500.0
        return Viewport(Point(ox, oy), p.canvas_zoom or 1.0)

    @staticmethod
    def _make_content_widget(node: CanvasNode) -> Gtk.Widget:
        """Placeholder body per node kind (real PTY/WebKit/file-tree widgets plug in here)."""
        c = node.content
        if node.node_type == "stickyNote":
            view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
            view.set_left_margin(8)
            view.set_top_margin(8)
            return view
        if node.node_type == "portal":
            text = getattr(c, "current_url", "") or "about:blank"
        elif node.node_type == "fileTree":
            text = getattr(c, "root_path", "") or "~"
        else:
            text = getattr(c, "command", "") or "bash"
        return Gtk.Label(label=text, xalign=0, yalign=0, margin_start=8, margin_top=8, selectable=False)

    def _add_node_to_canvas(self, node: CanvasNode) -> None:
        f = node.frame
        item = NodeItem(node.id, Rect(f.x, f.y, f.width, f.height), node.z_index, node.node_type, node.title)
        self.canvas_widget.add_node(item, self._make_content_widget(node))

    def _load_payload_into_canvas(self) -> None:
        for node in self.document.payload.nodes:
            self._add_node_to_canvas(node)
        for conn in self.document.payload.all_connections():
            if conn.a_id and conn.b_id:
                self.canvas_widget.add_connection(ConnItem(conn.id, conn.a_id, conn.b_id))

    def _find_node(self, node_id: str):
        nid = node_id.upper()
        return next((n for n in self.document.payload.nodes if n.id.upper() == nid), None)

    def _on_canvas_frame_changed(self, _canvas, node_id, x, y, w, h, final):
        node = self._find_node(node_id)
        if node is None:
            return
        old = node.frame
        resized = (old.width, old.height) != (w, h)
        node.frame = NodeFrame(x=x, y=y, width=w, height=h)
        # Bridge canvas-widget signals onto the SignalBus; WorkspaceSync debounces + persists.
        if resized:
            bus.emit("node-resized", node.id, x, y, w, h)
        else:
            bus.emit("node-moved", node.id, x, y)
        if final:
            self.sync.flush()

    def _on_canvas_delete_requested(self, _canvas, ids):
        for nid in tuple(ids):
            node = self._find_node(nid)
            if node is not None:
                self.document.payload.nodes.remove(node)
            self.canvas_widget.remove_node(nid)
            bus.emit("node-removed", nid)

    def _on_canvas_connect_requested(self, _canvas, a, side_a, b, side_b):
        if not self.canvas_widget.scene.can_connect(a, b):
            return
        conn = None
        try:
            conn = self.client.create_connection(a, b)
        except Exception:
            conn = None  # standalone / backend offline: draw locally only
        cid = conn.id if conn is not None else f"local-{a[:8]}-{b[:8]}"
        self.canvas_widget.add_connection(ConnItem(cid, a, b, side_a, side_b, pending=conn is None))
        if conn is not None:
            self.document.payload.connections.append(conn)
            bus.emit("connection-created", conn)

    def reset_zoom(self):
        """Ctrl+0: zoom back to 100% keeping the viewport center fixed."""
        cw = self.canvas_widget
        w = cw.get_width() or 1200
        h = cw.get_height() or 800
        cw.viewport.set_zoom_at(Point(w / 2.0, h / 2.0), 1.0)
        cw.controller.dirty = True

    def _create_node(self, node_type: str):
        """Add a node of a valid kind (terminal | stickyNote | portal | fileTree) at the viewport center."""
        if node_type not in WIDGET_KINDS:
            raise ValueError(f"invalid node type: {node_type!r}")
        cw = self.canvas_widget
        w = cw.get_width() or 1200
        h = cw.get_height() or 800
        center = cw.viewport.to_canvas(Point(w / 2.0, h / 2.0))
        cx, cy = center.x - 210, center.y - 150

        n = len(self.document.payload.nodes) + 1
        max_z = max((x.z_index for x in self.document.payload.nodes), default=0)

        if node_type == "terminal":
            content = TerminalContent(name=f"Terminal {n}", command="bash")
        elif node_type == "stickyNote":
            content = StickyNoteContent(name=f"Note-{n}")
        elif node_type == "portal":
            content = PortalContent(name="Portal", current_url="https://google.com")
        else:
            content = FileTreeContent(name="Files", root_path=os.path.expanduser("~"))

        new_node = CanvasNode(
            frame=NodeFrame(x=cx, y=cy, width=420, height=300),
            node_type=node_type,
            content=content,
            z_index=max_z + 1,
        )
        self.document.payload.nodes.append(new_node)
        self._add_node_to_canvas(new_node)
        bus.emit("node-added", new_node)  # WorkspaceSync schedules the save

    def _save_to_backend(self):
        """Ctrl+S: flush any pending change immediately."""
        self.sync.flush()

    def _on_close_request(self, *_args):
        self.sync.close()
        return False

    def _on_workspace_switched(self, bus_obj, workspace_id: str):
        # Persist the workspace we are leaving before replacing the document.
        self.sync.flush()
        doc = self.client.get_workspace(workspace_id)
        if doc and doc.payload:
            self.document = doc
            cw = self.canvas_widget
            for nid in list(cw.scene.nodes):
                cw.remove_node(nid)
            cw.viewport.origin = self._viewport_from_payload().origin
            cw.viewport.zoom = self._viewport_from_payload().zoom
            self._load_payload_into_canvas()
            cw.controller.dirty = True
            bus.emit("workspace-loaded", doc.payload)
