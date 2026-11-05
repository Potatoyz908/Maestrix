"""
Main ApplicationWindow for Open Maestri GTK 4 Native.
Integrates HeaderBar, Sidebar, CanvasWidget, and REST synchronization.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio

import os
import sys
import threading
from pathlib import Path

# Add paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))  # provides the canonical `maestri_canvas` package
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent, current_iso8601
from maestri_core.client.api import MaestriClient
from maestri_core.preferences import PreferencesStore
from maestri_core.signals.bus import bus
from maestri_core.sync import WorkspaceSync
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import WIDGET_KINDS, ConnItem, NodeItem
from maestri_canvas.viewport import Viewport
from maestri_canvas.widget import CanvasWidget
from .settings_window import SettingsWindow
from .sidebar import SidebarView
from .filetree import FileTreeWidget


class OpenMaestriWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, client: MaestriClient):
        super().__init__(application=app, title="Open Maestri (Native GTK 4)")
        self.client = client
        self.prefs_store = PreferencesStore()
        self.preferences = self.prefs_store.load()
        self.settings_window = None
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
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(Gtk.PopoverMenuBar.new_from_model(self._build_menu_model()))
        self.paned.set_vexpand(True)
        root.append(self.paned)
        self.set_child(root)

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

        # Backend Connection status indicator (checked asynchronously)
        self.lbl_status = Gtk.Label(label="⚪ Conectando...")
        self.lbl_status.set_margin_end(8)
        header.pack_end(self.lbl_status)
        self._check_connection_async()

    def _check_connection_async(self):
        def worker():
            is_online = self.client.is_connected()
            status_text = f"🟢 Porta {self.client.port}" if is_online else "⚪ Standalone"
            GLib.idle_add(self._update_status_label, status_text)

        threading.Thread(target=worker, daemon=True).start()

    def _update_status_label(self, status_text: str):
        self.lbl_status.set_label(status_text)
        return GLib.SOURCE_REMOVE

    def _build_menu_model(self) -> Gio.Menu:
        def section(items):
            m = Gio.Menu()
            for label, action in items:
                m.append(label, action)
            return m

        menubar = Gio.Menu()
        file_menu = Gio.Menu()
        file_menu.append_section(None, section([
            ("New Terminal", "win.new-terminal"), ("New Note", "win.new-note"),
            ("New Portal", "win.new-portal"), ("New File Tree", "win.new-file-tree"),
        ]))
        file_menu.append_section(None, section([("Save", "win.save")]))
        file_menu.append_section(None, section([("Preferences…", "win.preferences"), ("Quit", "win.quit")]))
        menubar.append_submenu("_File", file_menu)

        view_menu = Gio.Menu()
        view_menu.append_section(None, section([("Toggle Sidebar", "win.toggle-sidebar"), ("Reset Zoom", "win.reset-zoom")]))
        menubar.append_submenu("_View", view_menu)

        help_menu = Gio.Menu()
        help_menu.append_section(None, section([("About Open Maestri", "win.about")]))
        menubar.append_submenu("_Help", help_menu)
        return menubar

    def _setup_shortcuts(self):
        """Menu actions + accelerators: Ctrl+N new terminal, Ctrl+S save, Ctrl+0 reset zoom, Ctrl+Q quit,
        Ctrl+, preferences, Ctrl+B sidebar. Application accels run before focused widgets, so they work inside terminals."""
        handlers = {
            "new-terminal": lambda: self._create_node("terminal"),
            "new-note": lambda: self._create_node("stickyNote"),
            "new-portal": lambda: self._create_node("portal"),
            "new-file-tree": lambda: self._create_node("fileTree"),
            "save": lambda: self._save_to_backend(),
            "preferences": lambda: self.show_preferences(),
            "quit": lambda: self.close(),
            "toggle-sidebar": lambda: self._on_toggle_sidebar(None),
            "reset-zoom": lambda: self.reset_zoom(),
            "about": lambda: self._show_about(),
        }
        self._actions = handlers
        for name, fn in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, f=fn: f())
            self.add_action(action)

        app = self.get_application()
        if app is not None:
            for action, accel in (
                ("win.new-terminal", "<Control>n"), ("win.save", "<Control>s"), ("win.reset-zoom", "<Control>0"),
                ("win.quit", "<Control>q"), ("win.preferences", "<Control>comma"), ("win.toggle-sidebar", "<Control>b"),
            ):
                app.set_accels_for_action(action, [accel])

    def show_preferences(self):
        """Open (or raise) the SettingsWindow."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self, self.prefs_store, self._on_preferences_changed)
            self.settings_window.connect("close-request", self._on_settings_closed)
        self.settings_window.present()

    def _on_settings_closed(self, *_args):
        self.settings_window = None
        return False

    def _on_preferences_changed(self, prefs):
        # Persisted by SettingsWindow already; consumers (terminal font/theme, canvas background) read self.preferences.
        self.preferences = prefs

    def _show_about(self):
        about = Gtk.AboutDialog(transient_for=self, modal=True, program_name="Open Maestri",
                                comments="Native GTK 4 port of Maestri for Linux.")
        about.present()

    def _on_toggle_sidebar(self, btn):
        is_visible = self.sidebar.get_visible()
        self.sidebar.set_visible(not is_visible)

    # ---- canvas <-> document -------------------------------------------------------------------
    def _viewport_from_payload(self) -> Viewport:
        p = self.document.payload
        ox = p.canvas_origin[0] if len(p.canvas_origin) > 0 else 9800.0
        oy = p.canvas_origin[1] if len(p.canvas_origin) > 1 else 8500.0
        return Viewport(Point(ox, oy), p.canvas_zoom or 1.0)

    def _make_content_widget(self, node: CanvasNode) -> Gtk.Widget:
        """Placeholder body per node kind (real PTY/WebKit/file-tree widgets plug in here)."""
        c = node.content
        if node.node_type == "stickyNote":
            view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
            view.set_left_margin(8)
            view.set_top_margin(8)
            return view
        if node.node_type == "fileTree":
            if not c.root_path:
                c.root_path = os.path.expanduser("~")
            return FileTreeWidget(self.client, c)
        if node.node_type == "portal":
            text = getattr(c, "current_url", "") or "about:blank"
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
                self.document.payload.last_modified_at = current_iso8601()
            # Clean orphan connections from all 5 connection arrays
            nid_u = nid.upper()
            payload = self.document.payload
            payload.connections = [c for c in payload.connections if c.a_id.upper() != nid_u and c.b_id.upper() != nid_u]
            payload.note_connections = [c for c in payload.note_connections if c.a_id.upper() != nid_u and c.b_id.upper() != nid_u]
            payload.portal_connections = [c for c in payload.portal_connections if c.a_id.upper() != nid_u and c.b_id.upper() != nid_u]
            payload.portal_to_portal_connections = [c for c in payload.portal_to_portal_connections if c.a_id.upper() != nid_u and c.b_id.upper() != nid_u]
            payload.note_to_note_connections = [c for c in payload.note_to_note_connections if c.a_id.upper() != nid_u and c.b_id.upper() != nid_u]

            self.canvas_widget.remove_node(nid)
            bus.emit("node-removed", nid)

    def _on_canvas_connect_requested(self, _canvas, a, side_a, b, side_b):
        if not self.canvas_widget.scene.can_connect(a, b):
            return
        node_a = self._find_node(a)
        node_b = self._find_node(b)
        type_a = node_a.node_type if node_a else "terminal"
        type_b = node_b.node_type if node_b else "terminal"

        payload = self.document.payload
        # Determine connection kind based on endpoint types
        if type_a == "terminal" and type_b == "terminal":
            kind = "terminal"
            target_list = payload.connections
        elif (type_a == "terminal" and type_b == "stickyNote") or (type_a == "stickyNote" and type_b == "terminal"):
            kind = "terminalToNote"
            target_list = payload.note_connections
            if type_a != "terminal":
                a, b = b, a
                side_a, side_b = side_b, side_a
        elif (type_a == "terminal" and type_b == "portal") or (type_a == "portal" and type_b == "terminal"):
            kind = "terminalToPortal"
            target_list = payload.portal_connections
            if type_a != "terminal":
                a, b = b, a
                side_a, side_b = side_b, side_a
        elif type_a == "portal" and type_b == "portal":
            kind = "portalToPortal"
            target_list = payload.portal_to_portal_connections
        elif type_a == "stickyNote" and type_b == "stickyNote":
            kind = "noteToNote"
            target_list = payload.note_to_note_connections
        else:
            # Other combinations (e.g. fileTree, note-to-portal) are unsupported in schema v2
            return

        cid = f"local-{a[:8]}-{b[:8]}"
        self.canvas_widget.add_connection(ConnItem(cid, a, b, side_a, side_b, pending=True))

        def create_worker():
            try:
                conn = self.client.create_connection(a, b, kind=kind)
            except Exception:
                conn = None
            GLib.idle_add(self._apply_connection_created, cid, conn, target_list)

        threading.Thread(target=create_worker, daemon=True).start()

    def _apply_connection_created(self, temp_cid: str, conn, target_list):
        if conn is not None:
            # Upgrade temporary connection item to confirmed id and pending=False
            if temp_cid in self.canvas_widget.scene.connections:
                old = self.canvas_widget.scene.connections.pop(temp_cid)
                confirmed = ConnItem(conn.id, old.a, old.b, old.side_a, old.side_b, pending=False)
                self.canvas_widget.scene.connections[conn.id] = confirmed
                self.canvas_widget.controller.dirty = True
            target_list.append(conn)
            bus.emit("connection-created", conn)
        return GLib.SOURCE_REMOVE

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

        def switch_worker():
            try:
                doc = self.client.get_workspace(workspace_id)
            except Exception:
                doc = None
            GLib.idle_add(self._apply_workspace_switched, doc)

        threading.Thread(target=switch_worker, daemon=True).start()

    def _apply_workspace_switched(self, doc):
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
        return GLib.SOURCE_REMOVE
