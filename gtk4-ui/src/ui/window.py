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

from maestri_core.models.workspace import SidebarGroup, WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent, current_iso8601
from maestri_core.client.api import MaestriClient, AuthenticationError
from maestri_core.preferences import PreferencesStore
from maestri_core.signals.bus import bus
from maestri_core.sync import WorkspaceSync
from maestri_canvas.connection_rules import plan_connection, purge_node
from maestri_canvas.file_ops import preview_frames, preview_spec
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.interaction import Background
from maestri_canvas.note_model import unique_name
from maestri_canvas.note_storage import CoreNotesClient
from maestri_canvas.pty_transport import SoupPtyTransport
from maestri_canvas.scene import WIDGET_KINDS, ConnItem, NodeItem, norm
from maestri_canvas.terminal_themes import apply_terminal_style
from maestri_canvas.viewport import Viewport
from maestri_canvas.widget import CanvasWidget, is_terminal, load_vte, terminal_of, terminate_terminal
from .drawing import DrawingTools
from .ask_agent import install_ask_agent
from .routines_window import install_routines
from .floors import install_floors
from .core_link import CoreReconnector
from .settings_window import SettingsWindow
from .sidebar import SidebarView
from .terminal_dialog import EditTerminalDialog, NewTerminalDialog, PRESETS
from .workspace_dialogs import CreateWorkspaceDialog, GroupDialog
from .floating_bars import CanvasTopToolbar, CanvasBottomRightControls


class OpenMaestriWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, client: MaestriClient):
        super().__init__(application=app, title="Open Maestri (Native GTK 4)")
        self.client = client
        self.prefs_store = PreferencesStore()
        self.preferences = self.prefs_store.load()
        self.settings_window = None
        self.set_default_size(1280, 800)

        # 1. The workspace is fetched off the main thread (_start_initial_load): the window shows at once with an empty
        #    placeholder document and a "Conectando..." overlay, and is filled in by _apply_initial_load.
        self.auth_failed = False
        self.core_connected = False
        self.loaded = False
        self.sync = None
        self._autosave_id = 0
        self._cleaned_up = False
        self._core_link = CoreReconnector(client, self._on_core_probe)   # re-probes the core while it is unreachable (R1)
        self.document = WorkspaceDocument(payload=WorkspacePayload(name="Conectando...", working_directory=os.path.expanduser("~")))
        load_vte()   # so is_terminal()/terminal_of() recognise VTE widgets before the first terminal exists

        # 2. Setup HeaderBar
        self._setup_header_bar()

        # 3. Main Paned Layout (Sidebar + Canvas)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_position(250)

        self.sidebar = SidebarView(self.client)
        self.canvas_widget = CanvasWidget(viewport=self._viewport_from_payload())
        self.canvas_widget.set_hexpand(True)
        self.canvas_widget.set_vexpand(True)
        self.canvas_widget.renderer.background = self.preferences.canvas_background
        self.canvas_widget.set_snapping(self.preferences.canvas_snapping)
        self.canvas_widget.connect("node-moved", self._on_canvas_frame_changed)
        self.canvas_widget.connect("node-resized", self._on_canvas_frame_changed)
        self.canvas_widget.connect("viewport-changed", lambda _c, x, y, z: bus.emit("viewport-changed", x, y, z))
        self.canvas_widget.connect("delete-requested", self._on_canvas_delete_requested)
        self.canvas_widget.connect("connect-requested", self._on_canvas_connect_requested)
        self.canvas_widget.connect("note-meta-changed", self._on_note_meta_changed)
        self.canvas_widget.connect("portal-meta-changed", self._on_portal_meta_changed)
        self.canvas_widget.connect("file-activated", self._on_file_activated)
        self.canvas_widget.connect("file-tree-changed", self._on_file_tree_changed)
        self.canvas_widget.connect("files-dropped", self._on_files_dropped)
        self.canvas_widget.connect("node-title-changed", self._on_node_title_changed)
        self._setup_context_menu()
        self.drawing = DrawingTools(self)   # text / shape / freehand tools + P-tool frames (ui/drawing.py)

        self.paned.set_start_child(self.sidebar)
        self.canvas_stack = canvas_stack = Gtk.Overlay()
        canvas_stack.set_child(self.canvas_widget)
        self.loading_label = Gtk.Label(label="Conectando...", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                       css_classes=["maestri-loading"])
        self.loading_label.set_can_target(False)
        canvas_stack.add_overlay(self.loading_label)

        # Floating macOS Island Toolbar (Top Center) & Floating Controls (Bottom Right)
        self.top_toolbar = CanvasTopToolbar(self)
        canvas_stack.add_overlay(self.top_toolbar)

        self.bottom_controls = CanvasBottomRightControls(self)
        canvas_stack.add_overlay(self.bottom_controls)

        self.paned.set_end_child(canvas_stack)
        self.paned.set_vexpand(True)
        self.set_child(self.paned)

        # 4. Setup Shortcuts
        self._bus_handlers: list[tuple[object, int]] = []
        self._is_offline_placeholder: bool = False
        self._offline_mutated: bool = False
        self._demo_node_ids: set[str] = set()
        self._mutated_node_ids: set[str] = set()
        for sig in ("node-moved", "node-resized", "node-added", "node-updated", "node-removed",
                    "connection-created", "connection-changed", "connection-removed"):
            self._bus_handlers.append((bus, bus.connect(sig, self._on_canvas_mutated)))
        self._setup_shortcuts()
        self.floors = install_floors(self)       # floating Floors button (bottom-right) + New/Land/Delete dialogs
        self.routines = install_routines(self)   # File → Routines (Ctrl+Shift+R), scheduler on the main loop
        self.ask_agent = install_ask_agent(self)  # DiffView 'Ask agent' -> bus 'ask-agent' -> active terminal

        self._bus_handlers.append((bus, bus.connect("workspace-switched", self._on_workspace_switched)))
        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

        # Apply dark theme styling
        self._setup_theme()
        self._start_initial_load()

    # ---- initial load (background thread -> main loop) ------------------------------------------
    def _start_initial_load(self) -> None:
        def worker():
            status, doc = "offline", None
            try:
                status = self.client.check_connection_status()
                if status == "connected":
                    doc = self.client.get_workspace()
            except AuthenticationError:
                status = "unauthorized"
            except Exception:
                status = "offline"
            GLib.idle_add(self._apply_initial_load, status, doc)

        threading.Thread(target=worker, name="maestri-initial-load", daemon=True).start()

    def wait_loaded(self, timeout: float = 10.0) -> bool:
        """Pump the main loop until the initial workspace is on the canvas (tests, scripted startup)."""
        import time
        ctx = GLib.MainContext.default()
        end = time.monotonic() + timeout
        while not self.loaded and time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.005)
        return self.loaded

    def _on_canvas_mutated(self, _bus, *args) -> None:
        if not self.core_connected or getattr(self, "_is_offline_placeholder", False):
            self._offline_mutated = True
            if args and isinstance(args[0], str):
                self._mutated_node_ids.add(args[0])

    def _apply_initial_load(self, status: str, doc) -> bool:
        if self.loaded:
            return GLib.SOURCE_REMOVE
        self.auth_failed = status == "unauthorized"
        self.core_connected = status == "connected"

        if self.auth_failed:
            # When authentication fails, do not create demo workspace or enable sync
            payload = WorkspacePayload(name="Não Conectado", working_directory=os.path.expanduser("~"))
            self.document = WorkspaceDocument(payload=payload)
            self._is_offline_placeholder = True
        elif doc and doc.payload:
            self.document = doc
            self._is_offline_placeholder = False
        else:
            payload = WorkspacePayload(name="Default", working_directory=os.path.expanduser("~"))
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
            self._is_offline_placeholder = not self.core_connected
            self._demo_node_ids = {term_a.id, term_b.id}

        self._offline_mutated = False

        if self.auth_failed:
            self._update_status_label("🔴 Token Inválido")
            self.lbl_status.set_tooltip_text("Falha de autenticação: token inválido ou ausente.")
        elif self.core_connected:
            self._update_status_label(f"🟢 Porta {self.client.port}")
        else:
            self._update_status_label("⚪ Standalone")

        cw = self.canvas_widget
        vp = self._viewport_from_payload()
        cw.viewport.origin, cw.viewport.zoom = vp.origin, vp.zoom
        self._configure_notes_backend()
        self._load_payload_into_canvas()
        cw.controller.dirty = True

        # Real-time sync: bus events -> debounced snapshot -> REST (schemaVersion 2). Offline at startup is not final:
        # the probe keeps running and _on_core_probe turns the sync on as soon as the core answers.
        if self.core_connected:
            self._enable_sync()
        elif not self.auth_failed:
            self._core_link.start()

        self.loading_label.set_visible(False)
        self.loaded = True
        bus.emit("workspace-loaded", self.document.payload)
        return GLib.SOURCE_REMOVE

    # ---- core connection (R1) -----------------------------------------------------------------------------------
    def _enable_sync(self) -> None:
        """Create the WorkspaceSync once (idempotent) and start the autosave timer."""
        if self.sync is None:
            self.sync = WorkspaceSync(
                self.client,
                get_document=lambda: self.document,
                get_viewport=lambda: (
                    self.canvas_widget.viewport.origin.x,
                    self.canvas_widget.viewport.origin.y,
                    self.canvas_widget.viewport.zoom,
                ),
            )
            self._bus_handlers.append((bus, bus.connect("status-changed", self._on_sync_status_changed)))
        self._restart_autosave()

    def _on_core_probe(self, status: str) -> None:
        """Periodic probe result while the core is unreachable."""
        if self._cleaned_up or self.auth_failed:
            return
        if status == "connected" and not self.core_connected:
            self._core_link.stop()
            self.core_connected = True
            self._update_status_label(f"🟢 Porta {self.client.port}")
            self.lbl_status.set_tooltip_text("Conectado ao core: sincronizando.")
            self._configure_notes_backend()

            if getattr(self, "_is_offline_placeholder", False):
                has_edits = getattr(self, "_offline_mutated", False) or (self.sync is not None and self.sync.dirty)
                if not has_edits:
                    def fetch_worker():
                        try:
                            real_doc = self.client.get_workspace()
                        except Exception:
                            real_doc = None
                        GLib.idle_add(self._apply_reconnected_workspace, real_doc)
                    threading.Thread(target=fetch_worker, name="maestri-reconnect-fetch", daemon=True).start()
                    return
                else:
                    def merge_worker():
                        try:
                            server_doc = self.client.get_workspace()
                        except Exception:
                            server_doc = None
                        GLib.idle_add(self._apply_reconnected_merge, server_doc)
                    threading.Thread(target=merge_worker, name="maestri-reconnect-merge", daemon=True).start()
                    return

            self._enable_sync()
            self.sync.flush()   # push what was edited while offline
            self.floors.refresh()
            self.floors.start_events()
        elif status == "unauthorized":
            self._core_link.stop()
            self.auth_failed, self.core_connected = True, False
            self._update_status_label("🔴 Token Inválido")
            self.lbl_status.set_tooltip_text("Falha de autenticação: token inválido ou ausente.")

    def _apply_reconnected_workspace(self, doc) -> bool:
        if self._cleaned_up:
            return GLib.SOURCE_REMOVE
        if not doc or not doc.payload:
            # B1: GET failed (core still starting or transient error): keep offline placeholder, no sync, keep probing
            self.core_connected = False
            self._update_status_label("⚪ Standalone")
            self.lbl_status.set_tooltip_text("Core indisponível: as mudanças serão sincronizadas quando ele voltar.")
            self._core_link.start()
            return GLib.SOURCE_REMOVE

        # M-d: If an edit occurred while GET was in flight, merge rather than discarding
        if getattr(self, "_offline_mutated", False):
            return self._apply_reconnected_merge(doc)

        self._apply_workspace_switched(doc)
        self._is_offline_placeholder = False
        self._offline_mutated = False
        self._enable_sync()
        self.floors.refresh()
        self.floors.start_events()
        return GLib.SOURCE_REMOVE

    def _apply_reconnected_merge(self, server_doc) -> bool:
        if self._cleaned_up:
            return GLib.SOURCE_REMOVE
        if not server_doc or not server_doc.payload:
            # B1: GET failed: keep offline placeholder, do not save demo orphan, retry probe
            self.core_connected = False
            self._update_status_label("⚪ Standalone")
            self.lbl_status.set_tooltip_text("Core indisponível: as mudanças serão sincronizadas quando ele voltar.")
            self._core_link.start()
            return GLib.SOURCE_REMOVE

        # M-b: Merge only user-created or user-mutated nodes (exclude unedited demo terminals)
        demo_ids = getattr(self, "_demo_node_ids", set())
        mutated_ids = getattr(self, "_mutated_node_ids", set())
        existing = {n.id.upper() for n in server_doc.payload.nodes}
        for n in self.document.payload.nodes:
            if n.id.upper() not in existing:
                if n.id not in demo_ids or n.id in mutated_ids:
                    server_doc.payload.nodes.append(n)

        # M-a: Merge valid connections whose both endpoints exist in the final node set
        final_ids = {n.id.upper() for n in server_doc.payload.nodes}
        for conn in self.document.payload.all_connections():
            if conn.a_id and conn.b_id and conn.a_id.upper() in final_ids and conn.b_id.upper() in final_ids:
                if not any(c.id == conn.id for c in server_doc.payload.all_connections()):
                    server_doc.payload.connections.append(conn)

        # B2: Render the merged workspace on canvas via _apply_workspace_switched!
        self._apply_workspace_switched(server_doc)
        self._is_offline_placeholder = False
        self._offline_mutated = False
        self._enable_sync()
        self.sync.flush()
        self.floors.refresh()
        self.floors.start_events()
        return GLib.SOURCE_REMOVE

    def _on_core_lost(self) -> None:
        """A save failed because the core went away: back to Standalone (not 'Falha ao Salvar') and keep probing."""
        if self._cleaned_up or not self.core_connected:
            return
        self.core_connected = False
        self._update_status_label("⚪ Standalone")
        self.lbl_status.set_tooltip_text("Core indisponível: as mudanças serão sincronizadas quando ele voltar.")
        self.floors.stop_events()
        self.floors.refresh()   # unavailable -> the floor button disables itself
        self._core_link.start()

    def _restart_autosave(self) -> None:
        """Periodic autosave every `autosaveIntervalSeconds` on top of the debounced real-time sync: it persists anything still
        unsaved (e.g. a save that failed while the core was down)."""
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = 0
        if self.sync is not None:
            self._autosave_id = GLib.timeout_add_seconds(max(1, int(self.preferences.autosave_interval_seconds)), self._autosave_tick)

    def _autosave_tick(self) -> bool:
        if self.sync is None:
            self._autosave_id = 0
            return GLib.SOURCE_REMOVE
        if self.core_connected:   # offline: the probe flushes on reconnect instead of failing every tick
            self.sync.autosave()
        return GLib.SOURCE_CONTINUE

    def _setup_theme(self):
        css_provider = Gtk.CssProvider()
        css = """
        window {
            background-color: #f4f4f6;
            color: #1d1d1f;
        }
        headerbar {
            background-color: #f5f5f7;
            border-bottom: 1px solid rgba(0, 0, 0, 0.08);
            color: #1d1d1f;
            min-height: 44px;
            padding: 0 12px;
        }
        .maestri-traffic-lights {
            margin-right: 14px;
        }
        .maestri-traffic-dot {
            min-width: 12px;
            min-height: 12px;
            border-radius: 50%;
            border: 0.5px solid rgba(0, 0, 0, 0.15);
            padding: 0;
            margin: 0 3px;
        }
        .dot-close {
            background-color: #FF5F56;
        }
        .dot-close:hover {
            background-color: #E0443E;
        }
        .dot-min {
            background-color: #FFBD2E;
        }
        .dot-min:hover {
            background-color: #DEA123;
        }
        .dot-max {
            background-color: #27C93F;
        }
        .dot-max:hover {
            background-color: #1AAB29;
        }
        .maestri-header-btn {
            background-color: transparent;
            border: none;
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 13px;
            font-weight: 500;
            color: #48484a;
            margin: 0 2px;
        }
        .maestri-header-btn:hover {
            background-color: rgba(0, 0, 0, 0.06);
        }
        .maestri-title-entry {
            border: 1px solid #007AFF;
            border-radius: 4px;
            background: #ffffff;
            color: #1d1d1f;
            font-size: 12px;
            padding: 1px 4px;
            min-height: 20px;
        }
        .maestri-loading {
            font-size: 18px;
            color: #9a9aa0;
            background-color: alpha(#161618, 0.85);
            padding: 12px 24px;
            border-radius: 10px;
        }
        .maestri-sidebar {
            background-color: #f7f7f9;
            border-right: 1px solid #e2e2e6;
        }
        .maestri-search-entry {
            border-radius: 8px;
            background-color: #e5e5ea;
            border: none;
            padding: 6px 8px;
        }
        .maestri-sidebar-header {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.8px;
            color: #8e8e93;
            margin-top: 6px;
        }
        .maestri-sidebar-add-btn {
            background: transparent;
            border: none;
            font-size: 14px;
            color: #555558;
            padding: 2px 6px;
            border-radius: 4px;
        }
        .maestri-sidebar-add-btn:hover {
            background-color: #e5e5ea;
        }
        .maestri-group-header {
            padding: 4px 6px;
            border-radius: 6px;
        }
        .maestri-group-header:hover {
            background-color: rgba(0, 0, 0, 0.04);
        }
        .maestri-group-title {
            font-size: 12px;
            font-weight: 600;
            color: #48484a;
        }
        .maestri-group-chevron {
            font-size: 9px;
            color: #8e8e93;
        }
        .maestri-workspace-card {
            background-color: #ffffff;
            border: 1px solid #e5e5ea;
            border-radius: 10px;
            padding: 8px 12px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
            margin-bottom: 2px;
        }
        .maestri-workspace-card:hover {
            border-color: #007AFF;
        }
        .maestri-card-title {
            font-size: 13px;
            font-weight: 700;
            color: #1c1c1e;
        }
        .maestri-card-path {
            font-size: 11px;
            color: #8e8e93;
        }
        .maestri-card-pin {
            font-size: 11px;
        }
        .maestri-card-badge {
            font-size: 11px;
            color: #636366;
            background-color: #f2f2f7;
            padding: 2px 6px;
            border-radius: 6px;
        }
        .maestri-floor-card, .maestri-agent-card {
            background-color: #ffffff;
            border: 1px solid #e5e5ea;
            border-radius: 8px;
            padding: 6px 10px;
        }
        .maestri-floor-name, .maestri-agent-name {
            font-size: 12px;
            color: #1c1c1e;
        }
        .maestri-agent-role {
            font-size: 11px;
            color: #8e8e93;
        }
        .maestri-floating-pill {
            background-color: rgba(255, 255, 255, 0.96);
            border: 1px solid rgba(0, 0, 0, 0.12);
            border-radius: 16px;
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.12);
            padding: 4px 8px;
        }
        .maestri-island-btn {
            background: transparent;
            border: none;
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 15px;
            color: #333336;
        }
        .maestri-island-btn:hover {
            background-color: rgba(0, 0, 0, 0.06);
        }
        .maestri-island-btn.active {
            background-color: #007AFF;
            color: #ffffff;
        }
        .maestri-island-btn.danger:hover {
            background-color: #ff3b30;
            color: #ffffff;
        }
        .maestri-zoom-label {
            font-size: 13px;
            font-weight: 600;
            color: #555558;
            padding: 0 6px;
        }
        .maestri-context-popover {
            background-color: #ffffff;
            border: 1px solid rgba(0, 0, 0, 0.12);
            border-radius: 10px;
            box-shadow: 0 6px 22px rgba(0, 0, 0, 0.15);
        }
        .maestri-menu-item-btn {
            background: transparent;
            border: none;
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 13px;
            color: #1d1d1f;
        }
        .maestri-menu-item-btn:hover {
            background-color: #007AFF;
            color: #ffffff;
        }
        .maestri-menu-item-btn.danger {
            color: #FF3B30;
        }
        .maestri-menu-item-btn.danger:hover {
            background-color: #FF3B30;
            color: #ffffff;
        }
        .maestri-modal-window {
            background-color: #ffffff;
            border-radius: 14px;
        }
        .maestri-sheet-header {
            background-color: #f7f7f9;
        }
        .maestri-sheet-title {
            font-size: 16px;
            font-weight: 700;
            color: #111113;
        }
        .maestri-form-section-title {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.8px;
            color: #8e8e93;
        }
        .maestri-text-input {
            border: 1px solid #d1d1d6;
            border-radius: 8px;
            padding: 6px 10px;
            background-color: #ffffff;
        }
        .maestri-text-input:focus {
            border-color: #007AFF;
        }
        .maestri-dir-row {
            background-color: #f2f2f7;
            border: 1px solid #e5e5ea;
            border-radius: 8px;
            padding: 4px 8px;
        }
        .maestri-dir-path {
            font-size: 12px;
            color: #333336;
        }
        .maestri-dialog-btn {
            background-color: #ffffff;
            border: 1px solid #d1d1d6;
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 13px;
            color: #333336;
        }
        .maestri-dialog-btn-primary {
            background-color: #007AFF;
            border: none;
            border-radius: 8px;
            padding: 6px 16px;
            font-size: 13px;
            font-weight: 600;
            color: #ffffff;
        }
        .maestri-dialog-btn-danger {
            background-color: #FF3B30;
            border: none;
            border-radius: 8px;
            padding: 6px 16px;
            font-size: 13px;
            font-weight: 600;
            color: #ffffff;
        }
        .maestri-dialog-warning-text {
            font-size: 13px;
            color: #333336;
            line-height: 1.4;
        }
        .maestri-icon-btn {
            background: transparent;
            border: 2px solid transparent;
            border-radius: 8px;
            padding: 6px;
            font-size: 18px;
        }
        .maestri-icon-btn:hover {
            background-color: #f2f2f7;
        }
        .maestri-icon-btn.active {
            background-color: #eef5ff;
            border-color: #007AFF;
        }
        .maestri-color-dot {
            min-width: 26px;
            min-height: 26px;
            border-radius: 50%;
            border: 2.5px solid transparent;
            padding: 0;
        }
        .maestri-color-dot.active {
            border-color: #1d1d1f;
        }
        .color-blue { background-color: #007AFF; }
        .color-red { background-color: #FF3B30; }
        .color-green { background-color: #34C759; }
        .color-orange { background-color: #FF9500; }
        .color-purple { background-color: #AF52DE; }
        .color-pink { background-color: #FF2D55; }
        .color-cyan { background-color: #32ADE6; }
        .color-yellow { background-color: #FFCC00; }
        .color-rainbow { background: linear-gradient(135deg, #ff3b30, #ff9500, #ffcc00, #34c759, #007aff, #af52de); }
        .icon-color-blue { color: #007AFF; }
        .icon-color-red { color: #FF3B30; }
        .icon-color-green { color: #34C759; }
        .icon-color-orange { color: #FF9500; }
        .icon-color-purple { color: #AF52DE; }
        .icon-color-pink { color: #FF2D55; }
        .icon-color-cyan { color: #32ADE6; }
        .icon-color-yellow { color: #FFCC00; }
        .maestri-modal-dialog {
            background-color: #ffffff;
            border-radius: 16px;
        }
        .maestri-dialog-title {
            font-size: 17px;
            font-weight: 700;
            color: #111113;
        }
        .maestri-section-label {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.8px;
            color: #88888c;
        }
        .maestri-preset-btn {
            background-color: #f2f2f7;
            border: 2px solid transparent;
            border-radius: 12px;
            padding: 6px;
        }
        .maestri-preset-btn:hover {
            background-color: #e5e5ea;
        }
        .maestri-preset-btn.active {
            background-color: #eef5ff;
            border-color: #007AFF;
        }
        .maestri-preset-icon {
            font-size: 22px;
        }
        .maestri-preset-name {
            font-size: 11px;
            font-weight: 600;
            color: #333336;
        }
        .maestri-segmented-control {
            background-color: #e5e5ea;
            border-radius: 8px;
            padding: 2px;
        }
        .maestri-segment-btn {
            background: transparent;
            border: none;
            border-radius: 6px;
            padding: 4px 16px;
            font-size: 13px;
            font-weight: 500;
            color: #333336;
        }
        .maestri-segment-btn.active {
            background-color: #ffffff;
            color: #007AFF;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
        }
        .maestri-form-label {
            font-size: 13px;
            font-weight: 600;
            color: #333336;
        }
        .maestri-form-entry {
            border: 1px solid #d1d1d6;
            border-radius: 8px;
            padding: 6px 10px;
            background-color: #ffffff;
        }
        .maestri-form-entry:focus {
            border-color: #007AFF;
        }
        .maestri-btn-primary {
            background-color: #007AFF;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 6px 18px;
            font-weight: 600;
        }
        .maestri-btn-secondary {
            background-color: #e5e5ea;
            color: #333336;
            border: none;
            border-radius: 8px;
            padding: 6px 16px;
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

        # macOS Window Traffic Lights (Red, Yellow, Green)
        traffic_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        traffic_box.add_css_class("maestri-traffic-lights")
        traffic_box.set_margin_start(4)
        traffic_box.set_margin_end(12)
        self.traffic_box = traffic_box

        dot_close = Gtk.Button()
        dot_close.add_css_class("maestri-traffic-dot")
        dot_close.add_css_class("dot-close")
        dot_close.set_tooltip_text("Close")
        dot_close.connect("clicked", lambda _b: self.close())
        traffic_box.append(dot_close)

        dot_min = Gtk.Button()
        dot_min.add_css_class("maestri-traffic-dot")
        dot_min.add_css_class("dot-min")
        dot_min.set_tooltip_text("Minimize")
        dot_min.connect("clicked", lambda _b: self.minimize())
        traffic_box.append(dot_min)

        dot_max = Gtk.Button()
        dot_max.add_css_class("maestri-traffic-dot")
        dot_max.add_css_class("dot-max")
        dot_max.set_tooltip_text("Zoom / Fullscreen")
        dot_max.connect("clicked", lambda _b: self.unmaximize() if self.is_maximized() else self.maximize())
        traffic_box.append(dot_max)

        header.pack_start(traffic_box)

        # Quick Add (+ Add) MenuButton
        btn_quick_add = Gtk.MenuButton(label="＋ Add")
        btn_quick_add.set_tooltip_text("Quick Create Node")
        btn_quick_add.add_css_class("maestri-header-btn")
        quick_menu = Gio.Menu()
        quick_menu.append("New Workspace…", "win.new-workspace")
        quick_menu.append("New Group…", "win.new-group")
        term_submenu = Gio.Menu()
        for preset in PRESETS:
            term_submenu.append(f"{preset['icon']}  {preset['name']}", f"win.new-terminal-{preset['id']}")
        term_submenu.append("New Terminal…", "win.new-terminal")
        quick_menu.append_submenu("Terminal", term_submenu)
        quick_menu.append("New Sticky Note", "win.new-note")
        quick_menu.append("New Web Portal", "win.new-portal")
        quick_menu.append("New File Tree", "win.new-file-tree")
        btn_quick_add.set_menu_model(quick_menu)
        self.btn_quick_add = btn_quick_add
        self.quick_menu = quick_menu
        self.terminal_presets_menu = term_submenu
        header.pack_start(btn_quick_add)

        # Toggle Sidebar button (macOS [|] style)
        btn_sidebar = Gtk.Button(label="[|]")
        btn_sidebar.set_tooltip_text("Toggle Sidebar")
        btn_sidebar.add_css_class("maestri-header-btn")
        btn_sidebar.connect("clicked", self._on_toggle_sidebar)
        header.pack_start(btn_sidebar)
        self._header = header

        # Right: Settings button
        btn_settings = Gtk.Button(label="⚙️")
        btn_settings.set_tooltip_text("Preferences")
        btn_settings.add_css_class("maestri-header-btn")
        btn_settings.connect("clicked", lambda _b: self.show_preferences())
        header.pack_end(btn_settings)

        # Backend Connection status indicator
        self.lbl_status = Gtk.Label(label="⚪ Conectando...")
        self.lbl_status.set_margin_end(8)
        header.pack_end(self.lbl_status)

    def _check_connection_async(self):
        """Re-probe the core off the main thread and refresh the header label (the initial load sets it the first time)."""
        def worker():
            status = self.client.check_connection_status()
            if status == "connected":
                status_text = f"🟢 Porta {self.client.port}"
            elif status == "unauthorized":
                status_text = "🔴 Token Inválido"
            else:
                status_text = "⚪ Standalone"
            GLib.idle_add(self._update_status_label, status_text)

        threading.Thread(target=worker, daemon=True).start()

    def _on_sync_status_changed(self, _emitter, status: str, ok: bool = True, *args):
        if str(status).startswith("git-"):
            return   # R4: FileTree git actions share this bus signal; they never describe the workspace save
        if not getattr(self, "core_connected", False) or getattr(self, "auth_failed", False):
            return   # 'Standalone' / 'Token Inválido' win over any save status
        if status in ("save-failed", "sync-failed") or not ok:
            # Tell "the core refused this save" from "the core is gone" before painting the label.
            self._core_link.probe(self._on_save_failed_probe)
        elif status in ("idle", "saved", "save-ok") or ok:
            if getattr(self, "core_connected", False) and not getattr(self, "auth_failed", False):
                self.lbl_status.set_label(f"🟢 Porta {self.client.port}")
                self.lbl_status.set_tooltip_text("Conectado e sincronizado.")

    def _on_save_failed_probe(self, status: str) -> None:
        if self._cleaned_up or not self.core_connected:
            return
        if status == "connected":
            self.lbl_status.set_label("⚠️ Falha ao Salvar")
            self.lbl_status.set_tooltip_text("O último salvamento para o servidor falhou.")
        elif status == "unauthorized":
            self._on_core_probe(status)
        else:
            self._on_core_lost()

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
        routines_menu = section([("Manage Routines…", "win.routines"), ("New Routine…", "win.new-routine")])
        routines_section = Gio.Menu()
        routines_section.append_submenu("Routines", routines_menu)
        file_menu.append_section(None, routines_section)
        file_menu.append_section(None, section([("Save", "win.save")]))
        file_menu.append_section(None, section([("Preferences…", "win.preferences"), ("Quit", "win.quit")]))
        menubar.append_submenu("_File", file_menu)

        view_menu = Gio.Menu()
        view_menu.append_section(None, section([("Toggle Sidebar", "win.toggle-sidebar"), ("Reset Zoom", "win.reset-zoom")]))
        view_menu.append_section(None, section([("Snap to Alignment Guides", "win.snapping")]))   # check item (stateful action)
        menubar.append_submenu("_View", view_menu)

        help_menu = Gio.Menu()
        help_menu.append_section(None, section([("About Open Maestri", "win.about")]))
        menubar.append_submenu("_Help", help_menu)
        return menubar

    def _setup_shortcuts(self):
        """Menu actions + accelerators: Ctrl+N new terminal, Ctrl+S save, Ctrl+0 reset zoom, Ctrl+Q quit,
        Ctrl+, preferences, Ctrl+B sidebar. Application accels run before focused widgets, so they work inside terminals."""
        handlers = {
            "new-workspace": lambda: self._on_new_workspace(),
            "new-group": lambda: self._on_new_group(),
            "new-terminal": lambda: self.open_new_terminal_dialog(),
            "new-terminal-claude": lambda: self.create_terminal_preset("claude"),
            "new-terminal-codex": lambda: self.create_terminal_preset("codex"),
            "new-terminal-gemini": lambda: self.create_terminal_preset("gemini"),
            "new-terminal-opencode": lambda: self.create_terminal_preset("opencode"),
            "new-terminal-shell": lambda: self.create_terminal_preset("shell"),
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

        # View → Snap to Alignment Guides (Ctrl+;): stateful, persisted in preferences.json as canvasSnapping
        snap = Gio.SimpleAction.new_stateful("snapping", None, GLib.Variant.new_boolean(self.canvas_widget.snapping))
        snap.connect("change-state", lambda a, v: self._set_snapping(v.get_boolean()))
        self.add_action(snap)
        self._snap_action = snap
        self.canvas_widget.connect("snapping-changed", lambda _c, on: snap.set_state(GLib.Variant.new_boolean(on)))

        app = self.get_application()
        if app is not None:
            for action, accel in (
                ("win.new-terminal", "<Control>n"), ("win.save", "<Control>s"), ("win.reset-zoom", "<Control>0"),
                ("win.quit", "<Control>q"), ("win.preferences", "<Control>comma"), ("win.toggle-sidebar", "<Control>b"),
                ("win.snapping", "<Control>semicolon"),
            ):
                app.set_accels_for_action(action, [accel])

    def _set_snapping(self, on: bool) -> None:
        """Toggle the canvas alignment guides and remember the choice."""
        self.canvas_widget.set_snapping(on)
        sw = self.settings_window
        if sw is not None and sw.prefs.canvas_snapping != on:   # an open Preferences window must not save the old value back
            sw.prefs.canvas_snapping = on
            sw.snap_switch.set_active(on)
        if self.preferences.canvas_snapping != on:
            self.preferences.canvas_snapping = on
            try:
                self.prefs_store.save(self.preferences)
            except OSError:
                pass   # the toggle still applies for this session

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
        """Persisted by SettingsWindow already; apply live: terminal font/theme, canvas background, autosave interval."""
        old = self.preferences
        self.preferences = prefs
        cw = self.canvas_widget
        cw.renderer.background = prefs.canvas_background
        cw.set_snapping(prefs.canvas_snapping)
        cw.controller.dirty = True
        cw.queue_draw()
        for node in self.document.payload.nodes:
            if node.node_type == "terminal":
                self._style_terminal(node)
        if old.autosave_interval_seconds != prefs.autosave_interval_seconds or not self._autosave_id:
            self._restart_autosave()

    # ---- terminals -------------------------------------------------------------------------------
    def _terminal_style(self, node: CanvasNode):
        """(family, size, theme): the node's own overrides (fontFamily/fontSize/themeId) win over preferences."""
        c, p = node.content, self.preferences
        return (getattr(c, "font_family", None) or p.terminal_font_family,
                getattr(c, "font_size", None) or p.terminal_font_size,
                getattr(c, "theme_id", None) or p.terminal_theme)

    def _style_terminal(self, node: CanvasNode) -> None:
        frame = self.canvas_widget.frames.get(norm(node.id))
        term = terminal_of(frame.content) if frame is not None else None
        if term is not None:
            apply_terminal_style(term, *self._terminal_style(node))

    @staticmethod
    def _terminal_id(node: CanvasNode) -> str:
        return (getattr(node.content, "terminal_id", "") or node.id).upper()

    def _make_core_terminal(self, node: CanvasNode):
        """VteTerminalView streaming the core's PTY over /ws/pty/<terminalId>. The core owns the shell, so `omaestri ask`,
        scrollback persistence and agent features keep working, and the shell survives a UI restart. The session is ensured
        (GET, POST only when missing) off the main thread; the socket opens once that is done."""
        from maestri_canvas.terminal_node import VteTerminalView   # needs the Vte 3.91 typelib
        c = node.content
        tid = self._terminal_id(node)
        family, size, theme = self._terminal_style(node)
        transport = SoupPtyTransport(tid, self.client.port, self.client.token or "")
        view = VteTerminalView(transport, font=f"{family} {size:g}", theme=theme)
        cwd = getattr(c, "working_directory", "") or self.document.payload.working_directory or os.path.expanduser("~")
        command = getattr(c, "command", "") or "bash"
        workspace_id = self.document.payload.id

        def worker():
            try:
                self.client.ensure_terminal(tid, command=command, working_directory=cwd,
                                            workspace_id=workspace_id, display_name=getattr(c, "name", None))
            except Exception:
                pass   # the socket still tries (and reconnects with backoff) - the core may come back
            GLib.idle_add(lambda: (None if view.disposed else transport.connect(), GLib.SOURCE_REMOVE)[1])

        threading.Thread(target=worker, name=f"maestri-pty-{tid[:8]}", daemon=True).start()
        return view

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

    def _configure_notes_backend(self) -> None:
        """Managed notes go through the core REST API when it is reachable; offline they live in memory."""
        cw = self.canvas_widget
        if self.core_connected:
            cw.notes_client = CoreNotesClient(self.client.port, self.client.token)
        ws_dir = Path.home() / ".open-maestri" / "workspaces" / self.document.payload.id
        cw.notes_dir = str(ws_dir / "notes")

    def _make_content_widget(self, node: CanvasNode):
        """Content for a node. None = CanvasWidget.add_node builds the native view (StickyNoteView, FileTreeView,
        PortalView, and for terminals a local VTE login shell). A terminal is core-backed (VteTerminalView over /ws/pty)
        whenever the core is reachable; offline (standalone) it falls back to the local shell."""
        if node.node_type != "terminal" or not self.core_connected or load_vte() is None:
            return None
        return self._make_core_terminal(node)

    def _add_node_to_canvas(self, node: CanvasNode, focus: bool = False) -> None:
        f = node.frame
        drawn = node.content if isinstance(node.content, dict) else None   # text/shape/freehand keep their raw `_0` payload
        item = NodeItem(node.id, Rect(f.x, f.y, f.width, f.height), node.z_index, node.node_type, node.title, content=drawn)
        kwargs = {}
        if node.node_type == "stickyNote":
            kwargs["note"] = node.content.to_dict()
        elif node.node_type == "portal":
            kwargs["portal"] = node.content.to_dict()
        elif node.node_type == "fileTree":
            kwargs["cwd"] = getattr(node.content, "root_path", "") or os.path.expanduser("~")
            kwargs["file_tree"] = node.content.to_dict()
        elif node.node_type == "terminal":
            kwargs["cwd"] = (getattr(node.content, "working_directory", "") or self.document.payload.working_directory
                             or os.path.expanduser("~"))
            kwargs["env"] = self.floors.terminal_env(kwargs["cwd"])   # MAESTRI_FLOOR_* inside a floor's worktree (local VTE)
        self.canvas_widget.add_node(item, self._make_content_widget(node), focus=focus, **kwargs)
        if node.node_type == "terminal":
            self._style_terminal(node)

    def _on_portal_meta_changed(self, _canvas, node_id, meta):
        node = self._find_node(node_id)
        if node is None or node.node_type != "portal":
            return
        node.content = PortalContent.from_dict(meta)
        node.last_modified_at = current_iso8601()
        bus.emit("node-updated", node.id)

    def _on_note_meta_changed(self, _canvas, node_id, meta):
        node = self._find_node(node_id)
        if node is None or node.node_type != "stickyNote":
            return
        node.content = StickyNoteContent.from_dict(meta)
        node.last_modified_at = current_iso8601()
        bus.emit("node-updated", node.id)   # WorkspaceSync persists the new color/name/mode

    def _on_file_tree_changed(self, _canvas, node_id, d):
        """Root navigation and List/Grid toggles persist into FileTreeContent (rootPath / viewMode)."""
        node = self._find_node(node_id)
        if node is None or node.node_type != "fileTree":
            return
        c = node.content
        if (c.root_path, c.view_mode) == (d["rootPath"], d["viewMode"]):
            return
        c.root_path, c.view_mode = d["rootPath"], d["viewMode"]
        node.last_modified_at = current_iso8601()
        bus.emit("node-updated", node.id)

    def _on_node_title_changed(self, _canvas, node_id, new_title):
        """Title renamed directly on node header via inline entry."""
        node = self._find_node(node_id)
        if node is None:
            return
        if hasattr(node.content, "name"):
            node.content.name = new_title
        elif isinstance(node.content, dict):
            node.content["name"] = new_title
        node.last_modified_at = current_iso8601()
        bus.emit("node-updated", node.id)
        if self.sync is not None:
            self.sync.flush()

    def _on_files_dropped(self, _canvas, paths, x, y):
        """Files dropped on the empty canvas (FileTree rows, file managers) become preview nodes, stacked from the drop point
        (macOS Maestri: WorkspaceCanvasView+FileDrop). Text -> sticky note editing that file (storageMode custom); folder -> File
        Tree rooted there; image / PDF / video / other -> read-only portal on the file:// URI."""
        if not self.loaded:
            return
        for path, (fx, fy, fw, fh) in zip(paths, preview_frames(len(paths), x, y)):
            spec = preview_spec(path)
            if spec.kind == "stickyNote":
                content = StickyNoteContent(name=os.path.splitext(spec.title)[0] or spec.title, file_name=spec.title,
                                            has_custom_name=True, storage_mode={"custom": {"_0": path}})
            elif spec.kind == "fileTree":
                content = FileTreeContent(name=spec.title, root_path=path)
            else:
                content = PortalContent(name=spec.title, current_url=Gio.File.new_for_path(path).get_uri())
            self._create_node(spec.kind, at=Point(fx, fy), size=(fw, fh), content=content)

    def _on_file_activated(self, _canvas, _node_id, path):
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error:
            pass

    # ---- canvas context menu (right click on empty canvas) --------------------------------------
    def _setup_context_menu(self) -> None:
        cw = self.canvas_widget
        self._ctx_point = Point(0.0, 0.0)  # canvas coords of the last right click
        menu = Gio.Menu()
        for label, name in (("New Terminal here", "terminal"), ("New Note here", "stickyNote"),
                            ("New Portal here", "portal"), ("New File Tree here", "fileTree")):
            menu.append(label, f"ctx.new-{name}")
        group = Gio.SimpleActionGroup()
        for kind in ("terminal", "stickyNote", "portal", "fileTree"):
            act = Gio.SimpleAction.new(f"new-{kind}", None)
            act.connect("activate", lambda _a, _p, k=kind: self._create_node(k, at=self._ctx_point))
            group.add_action(act)
        cw.insert_action_group("ctx", group)

        self._ctx_popover = Gtk.PopoverMenu.new_from_model(menu)
        self._ctx_popover.set_has_arrow(False)
        self._ctx_popover.set_halign(Gtk.Align.START)
        self._ctx_popover.set_parent(cw)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_canvas_right_click)
        cw.add_controller(click)

    def _on_canvas_right_click(self, gesture, _n_press, x, y):
        cw = self.canvas_widget
        # Only the empty background: nodes keep their own right-click (text view menus, etc).
        if not isinstance(cw.target_at(x, y), Background):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._ctx_point = cw.viewport.to_canvas(Point(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self._ctx_popover.set_pointing_to(rect)
        self._ctx_popover.popup()

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
        if final and self.sync is not None:   # None before the initial load and with an invalid token
            self.sync.flush()

    def _on_canvas_delete_requested(self, _canvas, ids):
        for nid in tuple(ids):
            node = self._find_node(nid)
            if node is not None:
                self.document.payload.nodes.remove(node)
                self.document.payload.last_modified_at = current_iso8601()
            purge_node(self.document.payload, nid)   # all 6 connection lists (the canvas scene is cleaned by remove_node)

            core_tid = (self._terminal_id(node) if node is not None and node.node_type == "terminal"
                        and self.core_connected else None)
            self.canvas_widget.remove_node(nid)
            bus.emit("node-removed", nid)
            if core_tid:   # deleting the node ends its shell, like closing a local terminal
                threading.Thread(target=self._delete_core_terminal, args=(core_tid,), daemon=True).start()

    def _delete_core_terminal(self, tid: str) -> None:
        try:
            self.client.delete_terminal(tid)
        except Exception:
            pass

    def _on_canvas_connect_requested(self, _canvas, a, side_a, b, side_b):
        if not self.canvas_widget.scene.can_connect(a, b):
            self.canvas_widget.refuse_connection(a, b, "Already connected" if a != b else "Can't connect a node to itself")
            return
        node_a = self._find_node(a)
        node_b = self._find_node(b)
        type_a = node_a.node_type if node_a else "terminal"
        type_b = node_b.node_type if node_b else "terminal"

        plan = plan_connection(a, type_a, side_a, b, type_b, side_b)
        if isinstance(plan, str):   # not one of the 5 schema-v2 pairs: say so instead of ignoring the drop
            self.canvas_widget.refuse_connection(a, b, plan)
            return
        a, b, side_a, side_b, kind = plan.a, plan.b, plan.side_a, plan.side_b, plan.kind
        ws_id = self.document.payload.id
        target_list = getattr(self.document.payload, plan.list_attr)

        cid = f"local-{a[:8]}-{b[:8]}"
        self.canvas_widget.add_connection(ConnItem(cid, a, b, side_a, side_b, pending=True))

        def create_worker():
            try:
                conn = self.client.create_connection(a, b, kind=kind, workspace_id=ws_id)
            except Exception:
                conn = None
            GLib.idle_add(self._apply_connection_created, cid, conn, target_list, a, b, ws_id)

        threading.Thread(target=create_worker, daemon=True).start()

    def _apply_connection_created(self, temp_cid: str, conn, target_list, a: str = "", b: str = "", ws_id: str = ""):
        cw = self.canvas_widget
        if ws_id and self.document.payload.id != ws_id:
            # Workspace switched while connection was being created; discard
            cw.remove_connection(temp_cid)
            return GLib.SOURCE_REMOVE
        if conn is not None:
            if not cw.replace_connection_id(temp_cid, conn.id):   # the rope vanished meanwhile (node deleted): nothing to confirm
                return GLib.SOURCE_REMOVE
            target_list.append(conn)
            bus.emit("connection-created", conn)
        else:
            # Standalone/offline fallback: create and register locally in document payload
            import uuid
            from maestri_core.models.connection import Connection
            local_id = str(uuid.uuid4()).upper()
            local_conn = Connection(id=local_id, a=a, b=b, kind=kind, side_a=side_a, side_b=side_b)
            if not cw.replace_connection_id(temp_cid, local_conn.id):
                return GLib.SOURCE_REMOVE
            target_list.append(local_conn)
            bus.emit("connection-created", local_conn)
            cw.controller.dirty = True
            cw.queue_draw()
        return GLib.SOURCE_REMOVE

    def reset_zoom(self):
        """Ctrl+0: zoom back to 100% keeping the viewport center fixed."""
        cw = self.canvas_widget
        w = cw.get_width() or 1200
        h = cw.get_height() or 800
        cw.viewport.set_zoom_at(Point(w / 2.0, h / 2.0), 1.0)
        cw.controller.dirty = True

    def create_terminal_preset(self, preset_id: str, at: Point | None = None) -> CanvasNode:
        """Creates a terminal node directly from a Quick Start preset (Claude Code, Codex, Gemini CLI, OpenCode, Shell)."""
        import uuid
        preset = next((p for p in PRESETS if p["id"] == preset_id), PRESETS[0])
        cwd = self.floors.terminal_cwd() or self.document.payload.working_directory or os.path.expanduser("~")
        spawn_at = at or Point(
            self.canvas_widget.viewport.origin.x + (self.canvas_widget.get_width() or 1000) / 2 / self.canvas_widget.viewport.zoom - 210,
            self.canvas_widget.viewport.origin.y + (self.canvas_widget.get_height() or 700) / 2 / self.canvas_widget.viewport.zoom - 150,
        )
        node_id = str(uuid.uuid4()).upper()
        frame = NodeFrame(x=spawn_at.x, y=spawn_at.y, width=420, height=300)
        content = TerminalContent(name=preset["name"], command=preset["command"], working_directory=cwd)
        node = CanvasNode(id=node_id, frame=frame, node_type="terminal", content=content, z_index=len(self.document.payload.nodes) + 1)
        self.document.payload.nodes.append(node)
        self._add_node_to_canvas(node)
        self.canvas_widget.controller.dirty = True
        bus.emit("node-added", node)
        if self.sync is not None:
            self.sync.flush()
        return node

    def open_new_terminal_dialog(self, at: Point | None = None, default_preset: str = "claude") -> NewTerminalDialog:
        """Opens the macOS-matching 'New Terminal' modal dialog with Quick Start presets."""
        def on_create(data: dict):
            import uuid
            cwd = data.get("cwd") or self.floors.terminal_cwd() or os.path.expanduser("~")
            name = data.get("name") or "Terminal"
            cmd = data.get("command") or "bash"
            spawn_at = at or Point(
                self.canvas_widget.viewport.origin.x + (self.canvas_widget.get_width() or 1000) / 2 / self.canvas_widget.viewport.zoom - 210,
                self.canvas_widget.viewport.origin.y + (self.canvas_widget.get_height() or 700) / 2 / self.canvas_widget.viewport.zoom - 150,
            )
            node_id = str(uuid.uuid4()).upper()
            frame = NodeFrame(x=spawn_at.x, y=spawn_at.y, width=420, height=300)
            content = TerminalContent(name=name, command=cmd, working_directory=cwd)
            node = CanvasNode(id=node_id, frame=frame, node_type="terminal", content=content, z_index=len(self.document.payload.nodes) + 1)
            self.document.payload.nodes.append(node)
            self._add_node_to_canvas(node)
            self.canvas_widget.controller.dirty = True
            bus.emit("node-added", node)
            if self.sync is not None:
                self.sync.flush()

        dlg = NewTerminalDialog(
            self,
            on_create=on_create,
            default_cwd=self.document.payload.working_directory,
            default_preset=default_preset,
        )
        self._active_terminal_dialog = dlg
        dlg.connect("close-request", lambda *_a: setattr(self, "_active_terminal_dialog", None) or False)
        dlg.present()
        return dlg

    def edit_selected_node(self) -> None:
        """Edits the currently selected node (rename, settings, etc.)."""
        sel = list(self.canvas_widget.scene.selection)
        if not sel:
            return
        node_id = sel[0]
        node = self._find_node(node_id)
        if node is None:
            return
        if node.node_type == "terminal":
            def on_save(data: dict):
                node.content.name = data["name"]
                node.content.command = data["command"]
                node.content.working_directory = data["cwd"]
                frame = self.canvas_widget.frames.get(norm(node.id))
                if frame and hasattr(frame, "title"):
                    frame.title.set_label(data["name"])
                self.canvas_widget.controller.dirty = True
                self.canvas_widget.queue_draw()
                bus.emit("node-updated", node.id)
                if self.sync is not None:
                    self.sync.flush()

            dlg = EditTerminalDialog(self, node, on_save)
            dlg.present()
        elif node.node_type == "stickyNote":
            frame = self.canvas_widget.frames.get(norm(node.id))
            if frame and hasattr(frame.content, "edit_button"):
                frame.content.edit_button.emit("clicked")

    def _on_new_workspace(self) -> None:
        """Opens macOS-matching 'New Workspace' modal sheet."""
        def handle_created(name: str, wd: str, icon: str):
            def worker():
                self.client.create_workspace(name=name, working_directory=wd, icon=icon)
                self.sidebar.refresh()
            threading.Thread(target=worker, daemon=True).start()

        dlg = CreateWorkspaceDialog(self, handle_created)
        dlg.present()

    def _on_new_group(self) -> None:
        """Opens macOS-matching 'New Group' modal dialog."""
        def handle_ok(name: str):
            layout = self.sidebar._sidebar_layout
            layout.groups.append(SidebarGroup(name=name, items=[]))
            def worker():
                self.client.save_sidebar_layout(layout)
                self.sidebar.refresh()
            threading.Thread(target=worker, daemon=True).start()

        dlg = GroupDialog(self, "New Group", on_ok=handle_ok)
        dlg.present()

    def _create_node(self, node_type: str, at: Point = None, size: tuple = None, content=None):
        """Add a node of a valid kind (terminal | stickyNote | portal | fileTree).
        `at` = canvas point for the node's top-left corner (context menu); default: centered in the viewport.
        `size` = (w, h) of a frame drawn with a tool (default 420x300). `content` overrides the default content (file drops).
        Ignored until the initial workspace is loaded (the placeholder document would be replaced)."""
        if node_type not in WIDGET_KINDS:
            raise ValueError(f"invalid node type: {node_type!r}")
        if not self.loaded:
            return
        cw = self.canvas_widget
        w = cw.get_width() or 1200
        h = cw.get_height() or 800
        if at is not None:
            cx, cy = at.x, at.y
        else:
            center = cw.viewport.to_canvas(Point(w / 2.0, h / 2.0))
            cx, cy = center.x - 210, center.y - 150

        n = len(self.document.payload.nodes) + 1
        max_z = max((x.z_index for x in self.document.payload.nodes), default=0)

        if content is not None:
            pass
        elif node_type == "terminal":
            content = TerminalContent(name=f"Terminal {n}", command="bash", working_directory=self.floors.terminal_cwd())
        elif node_type == "stickyNote":
            taken = {x.content.file_name[:-3] if x.content.file_name.endswith(".md") else x.content.file_name
                     for x in self.document.payload.nodes if x.node_type == "stickyNote"}
            base = unique_name("Note", taken)
            content = StickyNoteContent(name=base, file_name=f"{base}.md")
        elif node_type == "portal":
            content = PortalContent(name="Portal", current_url="https://google.com")
        else:
            content = FileTreeContent(name="Files", root_path=os.path.expanduser("~"))

        new_node = CanvasNode(
            frame=NodeFrame(x=cx, y=cy, width=size[0] if size else 420, height=size[1] if size else 300),
            node_type=node_type,
            content=content,
            z_index=max_z + 1,
        )
        self.document.payload.nodes.append(new_node)
        self._add_node_to_canvas(new_node, focus=node_type == "stickyNote")
        bus.emit("node-added", new_node)  # WorkspaceSync schedules the save

    def _save_to_backend(self):
        """Ctrl+S: flush any pending change immediately."""
        if self.sync is not None:
            self.sync.flush()

    def _cleanup(self) -> None:
        """Runs on close-request AND destroy: only the first call does the work (R2)."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._core_link.dispose()
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = 0
        if self.sync is not None:
            self.sync.close()
            self.sync = None
        if hasattr(self, "ask_agent") and self.ask_agent is not None:
            self.ask_agent.dispose()
            self.ask_agent = None
        for obj, hid in list(self._bus_handlers):
            if hasattr(obj, "handler_is_connected") and obj.handler_is_connected(hid):
                obj.disconnect(hid)
        self._bus_handlers.clear()
        self._release_terminals()

    def _on_close_request(self, *_args):
        self._cleanup()
        return False

    def _on_destroy(self, *_args):
        self._cleanup()

    def _release_terminals(self) -> None:
        """Window closing: hang up local shells; core-backed terminals only close their socket (the core keeps the shell)."""
        for frame in list(self.canvas_widget.frames.values()):
            content = frame.content
            if is_terminal(content):
                terminate_terminal(content)
            elif getattr(content, "transport", None) is not None:
                content.dispose()

    def _on_workspace_switched(self, bus_obj, workspace_id: str):
        if not self.loaded:
            return
        # Persist the workspace we are leaving before replacing the document.
        if self.sync is not None:
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
            self._configure_notes_backend()   # notes_dir must follow the new workspace id
            self._load_payload_into_canvas()
            cw.controller.dirty = True
            bus.emit("workspace-loaded", doc.payload)
        return GLib.SOURCE_REMOVE
