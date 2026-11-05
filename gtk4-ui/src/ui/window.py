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
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent
from maestri_core.client.api import MaestriClient
from maestri_core.signals.bus import bus
from .canvas_widget import CanvasWidget
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
        self.canvas_widget = CanvasWidget(self.document.payload)

        self.paned.set_start_child(self.sidebar)
        self.paned.set_end_child(self.canvas_widget)
        self.set_child(self.paned)

        # 4. Setup Shortcuts
        self._setup_shortcuts()

        # 5. Connect bus signals for persistence
        bus.connect("node-moved", self._on_node_changed)
        bus.connect("node-resized", self._on_node_changed)
        bus.connect("node-removed", self._on_node_changed)
        bus.connect("connection-created", self._on_node_changed)
        bus.connect("workspace-switched", self._on_workspace_switched)

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
        btn_zoom.connect("clicked", lambda b: self.canvas_widget.reset_zoom())
        header.pack_end(btn_zoom)

        # Backend Connection status indicator
        is_online = self.client.is_connected()
        status_text = f"🟢 Porta {self.client.port}" if is_online else "⚪ Standalone"
        self.lbl_status = Gtk.Label(label=status_text)
        self.lbl_status.set_margin_end(8)
        header.pack_end(self.lbl_status)

    def _setup_shortcuts(self):
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl:
            if keyval == Gdk.KEY_n:
                self._create_node("terminal")
                return True
            elif keyval == Gdk.KEY_s:
                self._save_to_backend()
                return True
            elif keyval == Gdk.KEY_0:
                self.canvas_widget.reset_zoom()
                return True
            elif keyval == Gdk.KEY_q:
                self.close()
                return True
        return False

    def _on_toggle_sidebar(self, btn):
        is_visible = self.sidebar.get_visible()
        self.sidebar.set_visible(not is_visible)

    def _create_node(self, node_type: str):
        # Place node near center of current viewport
        vp = self.canvas_widget.viewport
        w = self.canvas_widget.get_width() or 1200
        h = self.canvas_widget.get_height() or 800
        cx, cy = vp.screen_to_canvas(w / 2.0 - 200, h / 2.0 - 150)

        max_z = max((n.z_index for n in self.document.payload.nodes), default=0)

        if node_type == "terminal":
            content = TerminalContent(name=f"Terminal {len(self.document.payload.nodes) + 1}", command="bash")
        elif node_type == "stickyNote":
            content = StickyNoteContent(name=f"Note-{len(self.document.payload.nodes) + 1}")
        elif node_type == "portal":
            content = PortalContent(name="Portal", current_url="https://google.com")
        elif node_type == "fileTree":
            content = FileTreeContent(name="Files", root_path=os.path.expanduser("~"))
        else:
            content = TerminalContent()

        new_node = CanvasNode(
            frame=NodeFrame(x=cx, y=cy, width=420, height=300),
            node_type=node_type,
            content=content,
            z_index=max_z + 1
        )
        self.document.payload.nodes.append(new_node)
        bus.emit("node-added", new_node)
        self._save_to_backend()

    def _on_node_changed(self, *args):
        # Debounced save to backend
        GLib.timeout_add(500, self._save_to_backend)

    def _save_to_backend(self):
        # Update origin & zoom
        self.document.payload.canvas_origin = [
            self.canvas_widget.viewport.origin_x,
            self.canvas_widget.viewport.origin_y
        ]
        self.document.payload.canvas_zoom = self.canvas_widget.viewport.zoom
        self.client.save_workspace(self.document)
        return False  # Run once in GLib timeout

    def _on_workspace_switched(self, bus_obj, workspace_id: str):
        doc = self.client.get_workspace(workspace_id)
        if doc and doc.payload:
            self.document = doc
            bus.emit("workspace-loaded", doc.payload)
