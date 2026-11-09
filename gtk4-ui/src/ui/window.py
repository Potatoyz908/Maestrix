"""
Main ApplicationWindow for Maestrix GTK 4 Native.
Integrates HeaderBar, Sidebar, CanvasWidget, and REST synchronization.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio

import json
import os
import sys
import threading
import uuid
from pathlib import Path

# Add paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))  # provides the canonical `maestri_canvas` package
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.models.workspace import SidebarGroup, WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import (CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent,
                                      agent_type_for_command, current_iso8601)
from maestri_core.client.api import MaestriClient, AuthenticationError
from maestri_core.agent_resume import resume_command
from maestri_core.unique_names import unique_name, dedupe_terminal_names
from maestri_core.preferences import PreferencesStore
from maestri_core.element_defaults import apply_element_defaults
from maestri_core.signals.bus import bus
from maestri_core.terminal_fields import apply_fields, role_name, terminal_fields_from_dialog
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
from .live_events import LiveEvents
from .config_sync import ConfigFileSync
from .workspace_pill import WorkspacePill
from .window_actions import WindowActionsMixin
from .settings_window import SettingsWindow
from .sidebar import SidebarView
from .terminal_dialog import EditTerminalDialog, NewTerminalDialog, PRESETS, agent_presets
from .ssh_dialog import open_connect_to_host
from .workspace_dialogs import CreateWorkspaceDialog, GroupDialog, create_workspace_record
from .floating_bars import CanvasTopToolbar, CanvasBottomRightControls
from .iconography import StatusIndicator, gicon, icon_label, icon_name, install_css as install_icon_css


class MaestrixWindow(WindowActionsMixin, Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, client: MaestriClient):
        super().__init__(application=app, title="Maestrix")
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
        self._core_misses = 0
        GLib.timeout_add_seconds(5, self._core_heartbeat)   # notices a core that dies while nothing is being saved
        self.document = WorkspaceDocument(payload=WorkspacePayload(name="Conectando...", working_directory=os.path.expanduser("~")))
        load_vte()   # so is_terminal()/terminal_of() recognise VTE widgets before the first terminal exists

        # 2. Setup HeaderBar
        self._setup_header_bar()

        # 3. Main Paned Layout (Sidebar + Canvas)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._sidebar_open = True
        self._sidebar_tick = 0
        self.paned.set_position(260)
        self.paned.set_resize_start_child(False)
        self.paned.set_shrink_start_child(False)
        self.paned.set_resize_end_child(True)
        self.paned.set_shrink_end_child(False)
        self.paned.connect("notify::position", self._on_paned_position_changed)

        self.sidebar = SidebarView(self.client, menu_model=self._build_menu_model())
        self.popover_menubar = self.sidebar.menubar
        self.canvas_widget = CanvasWidget(viewport=self._viewport_from_payload())
        self.canvas_widget.set_hexpand(True)
        self.canvas_widget.set_vexpand(True)
        self.canvas_widget.renderer.background = self.preferences.canvas_background
        self.canvas_widget.set_snapping(self.preferences.canvas_snapping)
        self.canvas_widget.connect("node-moved", self._on_canvas_frame_changed)
        self.canvas_widget.connect("node-resized", self._on_canvas_frame_changed)
        self.canvas_widget.connect("viewport-changed", lambda _c, x, y, z: bus.emit("viewport-changed", x, y, z))
        self.canvas_widget.connect("delete-requested", self._on_canvas_delete_requested)
        self.canvas_widget.connect("duplicate-requested", self._on_canvas_duplicate_requested)
        self.canvas_widget.connect("connect-requested", self._on_canvas_connect_requested)
        self.canvas_widget.connect("connection-rope-settled", self._on_rope_settled)
        self.canvas_widget.connect("note-meta-changed", self._on_note_meta_changed)
        self.canvas_widget.connect("portal-meta-changed", self._on_portal_meta_changed)
        self.canvas_widget.connect("file-activated", self._on_file_activated)
        self.canvas_widget.connect("file-tree-changed", self._on_file_tree_changed)
        self.canvas_widget.connect("files-dropped", self._on_files_dropped)
        self.canvas_widget.connect("node-title-changed", self._on_node_title_changed)
        self._connect_k9_signals()                 # node-lock-changed / node-z-changed -> isLocked / zIndex (K9)
        self.live_events = LiveEvents(self)        # K2: server-created nodes/connections on the open canvas
        self.config_sync = ConfigFileSync()        # CLAUDE.md <-> AGENTS.md while the workspace's syncConfigFiles is on
        self._events_stream = None
        self.portal_bridge = None                  # K3 executor (canvas-cairo portal_bridge.PortalBridge), when installed
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

        # Discreet floating button to reopen sidebar when hidden
        self.btn_floating_sidebar = Gtk.Button()
        floating_sidebar_img = Gtk.Image.new_from_icon_name("maestri-sidebar-symbolic")
        floating_sidebar_img.set_pixel_size(15)
        self.btn_floating_sidebar.set_child(floating_sidebar_img)
        self.btn_floating_sidebar.set_tooltip_text("Open Sidebar")
        self.btn_floating_sidebar.set_halign(Gtk.Align.START)
        self.btn_floating_sidebar.set_valign(Gtk.Align.START)
        self.btn_floating_sidebar.set_margin_start(16)
        self.btn_floating_sidebar.set_margin_top(16)
        self.btn_floating_sidebar.add_css_class("maestri-floating-sidebar-btn")
        self.btn_floating_sidebar.connect("clicked", self._on_toggle_sidebar)
        self.btn_floating_sidebar.set_visible(False)
        canvas_stack.add_overlay(self.btn_floating_sidebar)

        self.paned.set_end_child(canvas_stack)
        self.paned.set_vexpand(True)
        self.set_child(self.paned)

        # 4. Setup Shortcuts
        self._bus_handlers: list[tuple[object, int]] = []
        self._bus_handlers.append((bus, bus.connect("node-changed", self._on_server_node_changed)))
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
        self._bus_handlers.append((bus, bus.connect("workspace-loaded", lambda _b, p: self.config_sync.attach(p))))
        self._bus_handlers.append((bus, bus.connect("workspace-loaded", lambda *_: self.update_workspace_pill())))
        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

        # Apply dark theme styling
        self._setup_theme()
        from .theme import InterfaceTheme
        self.interface_theme = InterfaceTheme()
        self.interface_theme.apply(self.preferences.interface_theme)
        self.canvas_widget.renderer.dark_mode = self.preferences.interface_theme == "dark"
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
            # An empty workspace opens empty: no demo terminals.
            self.document = WorkspaceDocument(payload=payload)
            self._is_offline_placeholder = not self.core_connected

        self._offline_mutated = False

        if self.auth_failed:
            self._update_status_label("Token Inválido", "error")
            self.lbl_status.set_tooltip_text("Falha de autenticação: token inválido ou ausente.")
        elif self.core_connected:
            self._update_status_label(f"Porta {self.client.port}", "connected")
        else:
            self._update_status_label("Standalone", "offline")

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
        self._start_event_stream()

    # ---- K2 / K3: /ws/events -------------------------------------------------------------------------------------
    def _start_event_stream(self) -> None:
        """One subscription to /ws/events for all workspaces (reconnects by itself): canvas events of the open document,
        terminal counts of the others, portal commands (K3). Not given the bus: the window applies events without echo."""
        if self._events_stream is not None or self._cleaned_up:
            return
        try:
            from maestri_canvas.portal_bridge import PortalBridge   # Arquiteto (K3); optional until it lands
            self.portal_bridge = PortalBridge(self.canvas_widget, self.client)
        except Exception:   # noqa: BLE001 - ImportError or an older bridge signature
            self.portal_bridge = None
        try:
            self._events_stream = self.client.connect_events(on_event=self._on_ws_event, workspace_id=None)
        except Exception as exc:   # noqa: BLE001 - live updates are best effort; reloads still show everything
            print(f"[window] /ws/events unavailable: {exc!r}")
            self._events_stream = None

    def _on_ws_event(self, etype, payload=None) -> bool:
        """`_on_ws_event(event_dict)` from the stream, or `_on_ws_event("nodeAdded", {...})`. Main loop only."""
        if isinstance(etype, dict):
            payload, etype = etype, etype.get("type", "")
        if self._cleaned_up or not isinstance(payload, dict):
            return False
        if etype == "nodeChanged":
            bus.emit("node-changed", payload)
            return False
        if etype == "portalCommand":
            if self.portal_bridge is not None:
                self.portal_bridge.handle_event(payload)
            return False
        return self.live_events.handle(str(etype), payload)

    def _on_server_node_changed(self, _bus, event) -> None:
        """Apply server metadata without node-updated, REST writes or restarting the PTY."""
        if self._cleaned_up or not self.loaded or not isinstance(event, dict):
            return
        if str(event.get("workspaceId", "")).upper() != self.document.payload.id.upper():
            return
        raw = event.get("node")
        if not isinstance(raw, dict) or not raw.get("id"):
            return
        node = self._find_node(str(event.get("nodeId", "")))
        if node is None or str(raw["id"]).upper() != node.id.upper():
            return
        updated = CanvasNode.from_dict(raw)
        if updated.node_type != node.node_type:
            return
        node.__dict__.update(updated.__dict__)
        cw = self.canvas_widget
        item = cw.scene.get(node.id)
        if item is not None:
            item.title = node.title
        frame = cw.frames.get(norm(node.id))
        if frame is not None:
            frame.title.set_label(node.title)
            if node.node_type == "terminal":
                from .iconography import sf_symbol_icon
                icon = getattr(frame, "_server_header_icon", None)
                if icon is None:
                    icon = Gtk.Image()
                    frame.title.get_parent().prepend(icon)
                    frame._server_header_icon = icon
                    frame._server_header_css = Gtk.CssProvider()
                    icon.get_style_context().add_provider(frame._server_header_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                icon.set_from_icon_name(sf_symbol_icon(node.content.icon))
                color = Gdk.RGBA()
                css = f"image {{ color: {color.to_string()}; }}" if color.parse(node.content.color or "") else ""
                frame._server_header_css.load_from_string(css)
        cw.controller.dirty = True
        cw.queue_draw()

    def _maybe_restart_core(self) -> None:
        """The launcher exports OPEN_MAESTRI_SERVER_BIN: if the core died while the app runs (nothing answers, Floors stay
        disabled, terminals become local shells), start it again, at most once a minute. Never in tests (no env var)."""
        import socket, subprocess, time
        binary = os.environ.get("OPEN_MAESTRI_SERVER_BIN")
        if not binary or not os.access(binary, os.X_OK):
            return
        now = time.monotonic()
        if now - getattr(self, "_core_restart_at", -1e9) < 60:
            return
        self._core_restart_at = now
        from maestri_core.paths import run_dir
        sock_path = run_dir() / "agent.sock"
        probe = socket.socket(socket.AF_UNIX)
        probe.settimeout(1.0)
        try:
            probe.connect(str(sock_path))
            return   # something listens there: it is just slow, not dead
        except OSError:
            pass
        finally:
            probe.close()
        try:
            sock_path.unlink()
        except OSError:
            pass
        try:
            log = open(run_dir() / "server.log", "ab")
            subprocess.Popen([binary], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        except OSError:
            pass

    def _on_core_probe(self, status: str) -> None:
        """Periodic probe result while the core is unreachable."""
        if self._cleaned_up or self.auth_failed:
            return
        if status != "connected":
            self._maybe_restart_core()
        if status == "connected" and not self.core_connected:
            self._core_link.stop()
            self.core_connected = True
            self._update_status_label(f"Porta {self.client.port}", "connected")
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
            self._update_status_label("Token Inválido", "error")
            self.lbl_status.set_tooltip_text("Falha de autenticação: token inválido ou ausente.")

    def _apply_reconnected_workspace(self, doc) -> bool:
        if self._cleaned_up:
            return GLib.SOURCE_REMOVE
        if not doc or not doc.payload:
            # B1: GET failed (core still starting or transient error): keep offline placeholder, no sync, keep probing
            self.core_connected = False
            self._update_status_label("Standalone", "offline")
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
            self._update_status_label("Standalone", "offline")
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

    def _core_heartbeat(self) -> bool:
        """While connected, check the core every few seconds; two misses in a row = lost (the reconnector then takes over and,
        under the launcher, _maybe_restart_core starts it again). Otherwise a dead core went unnoticed until some save failed."""
        if self._cleaned_up:
            return GLib.SOURCE_REMOVE
        if not self.core_connected or self.auth_failed:
            self._core_misses = 0
            return GLib.SOURCE_CONTINUE

        def worker():
            status = self.client.check_connection_status()

            def apply():
                if status == "offline":
                    self._core_misses += 1
                    if self._core_misses >= 2:
                        self._core_misses = 0
                        self._on_core_lost()
                else:
                    self._core_misses = 0
                return GLib.SOURCE_REMOVE
            GLib.idle_add(apply)
        threading.Thread(target=worker, name="maestri-core-heartbeat", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _on_core_lost(self) -> None:
        """A save failed because the core went away: back to Standalone (not 'Falha ao Salvar') and keep probing."""
        if self._cleaned_up or not self.core_connected:
            return
        self.core_connected = False
        self._update_status_label("Standalone", "offline")
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
        self.set_icon_name("maestrix")
        Gtk.Window.set_default_icon_name("maestrix")
        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            icons_dir = os.path.join(os.path.dirname(__file__), "icons")
            if os.path.isdir(icons_dir):
                theme.add_search_path(icons_dir)
            install_icon_css(display)   # status dot + icon sizes (ui/iconography.py)
        css_provider = Gtk.CssProvider()
        css = """
        window {
            background-color: #f4f4f6;
            color: #1d1d1f;
        }
        headerbar {
            background-color: transparent;
            background-image: none;
            border: none;
            box-shadow: none;
            color: #1d1d1f;
            min-height: 44px;
            padding: 0 12px 0 0;
            margin: 0;
        }
        .maestri-sidebar-top-bar {
            background-color: transparent;
            border-right: 1px solid rgba(128, 128, 128, 0.25);
            min-height: 44px;
            padding: 0 10px 0 14px;
            margin: 0;
        }
        .maestri-header-menubar {
            background-color: transparent;
            background-image: none;
            border: none;
            box-shadow: none;
            margin-left: 8px;
            padding: 0;
        }
        .maestri-header-menubar item {
            min-height: 24px;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 500;
        }
        .maestri-header-menubar item:hover {
            background-color: rgba(128, 128, 128, 0.2);
        }
        .maestri-sidebar-menubar {
            background-color: transparent;
            background-image: none;
            border: none;
            box-shadow: none;
            margin: 0 0 2px 0;
            padding: 0;
        }
        .maestri-sidebar-menubar item {
            min-height: 24px;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
            color: rgba(255, 255, 255, 0.88);
        }
        .maestri-sidebar-menubar item:hover {
            background-color: rgba(255, 255, 255, 0.16);
            color: #ffffff;
        }
        .maestri-sidebar-menubar item:active,
        .maestri-sidebar-menubar item:checked {
            background-color: rgba(255, 255, 255, 0.24);
            color: #ffffff;
        }
        .maestri-traffic-lights {
            margin-right: 6px;
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
            background-image: none;
            border: none;
            border-radius: 6px;
            padding: 4px 6px;
            font-size: 13px;
            font-weight: 500;
            color: #48484a;
            margin: 0 1px;
            box-shadow: none;
        }
        .maestri-header-btn:hover {
            background-color: rgba(0, 0, 0, 0.06);
            color: #1d1d1f;
        }
        .maestri-header-btn:active {
            background-color: rgba(0, 0, 0, 0.12);
        }
        .maestri-add-split-btn {
            padding: 4px 4px;
        }
        .maestri-add-split-btn image {
            color: #48484a;
        }
        .maestri-add-split-btn:hover image {
            color: #1d1d1f;
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
            background-color: rgba(0, 0, 0, 0.06);
            color: #1c1c1e;
            border: 1px solid rgba(0, 0, 0, 0.04);
            padding: 6px 10px;
        }
        .maestri-search-entry text {
            color: #1c1c1e;
        }
        .maestri-search-entry placeholder {
            color: #8e8e93;
        }
        .maestri-search-entry image {
            color: #8e8e93;
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
        .maestri-workspace-list {
            background: transparent;
        }
        .maestri-workspace-list row {
            background: transparent;
            padding: 2px 1px;
            border: none;
            margin: 0;
        }
        .maestri-workspace-list row:hover {
            background: transparent;
        }
        .maestri-workspace-list row:selected {
            background: transparent;
        }
        row:selected .maestri-workspace-card {
            background-color: #eef5ff;
            border-color: #007AFF;
            box-shadow: inset 4px 0 0 #007AFF;
        }
        .maestri-workspace-card {
            background-color: #ffffff;
            border: 1px solid rgba(0, 0, 0, 0.08);
            border-radius: 8px;
            padding: 8px 12px;
            margin: 1px 0px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
            transition: all 150ms ease;
        }
        .maestri-workspace-card:hover {
            border-color: #007AFF;
            box-shadow: 0 2px 8px rgba(0, 122, 255, 0.08);
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
            background-color: rgba(0, 0, 0, 0.05);
            padding: 2px 8px;
            border-radius: 10px;
        }
        .maestri-card-badge image {
            color: #636366;
        }
        .maestri-act-dot { background: #0a84ff; border-radius: 5px; }
        .maestri-card-badge.unread { background-color: rgba(10, 132, 255, 0.16); }
        .maestri-act-dot.busy { background: #23d18b; animation: maestri-badge-pulse 0.9s ease-in-out infinite alternate; }
        .maestri-card-agents-btn { min-height: 20px; min-width: 20px; padding: 0; opacity: .7; background: transparent; box-shadow: none; border: none; }
        .maestri-card-agents-btn:hover { opacity: 1; background: alpha(currentColor, .08); }
        .maestri-agent-row { padding: 4px 8px; }
        .maestri-agent-state { font-size: 11px; }
        .maestri-agent-dot { border-radius: 5px; background: alpha(currentColor, .35); }
        .maestri-agent-dot.busy { background: #23d18b; }
        .maestri-agent-dot.unread { background: #0a84ff; }
        .maestri-agent-dot.exited { background: #ff453a; }
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
        .maestri-floating-sidebar-btn {
            background-color: rgba(255, 255, 255, 0.94);
            border: 1px solid rgba(0, 0, 0, 0.12);
            border-radius: 8px;
            padding: 6px 10px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
            color: #333336;
        }
        .maestri-floating-sidebar-btn:hover {
            background-color: #ffffff;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
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
            color: #f2f2f5;
            padding: 0 6px;
        }
        .maestri-bottom-pill {
            background-color: rgba(30, 30, 32, 0.88);
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 18px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
            padding: 2px 8px;
            color: #f2f2f5;
        }
        .floor-button {
            background-color: rgba(30, 30, 32, 0.88);
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 18px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
            padding: 4px 12px;
            color: #f2f2f5;
        }
        .floor-button:hover, .maestri-bottom-pill:hover {
            background-color: rgba(45, 45, 48, 0.95);
        }
        popover.maestri-context-popover {
            background: transparent;
            border: none;
            box-shadow: none;
            padding: 0;
        }
        popover.maestri-context-popover > contents {
            background-color: rgba(36, 36, 40, 0.96);
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
            padding: 4px;
            color: #f2f2f5;
        }
        .maestri-menu-item-btn {
            background: transparent;
            border: none;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
            font-weight: 450;
            color: #e5e5ea;
        }
        .maestri-menu-item-btn:hover {
            background-color: #0A84FF;
            color: #ffffff;
        }
        .maestri-menu-item-btn.danger {
            color: #FF453A;
        }
        .maestri-menu-item-btn.danger:hover {
            background-color: #FF453A;
            color: #ffffff;
        }
        .maestri-menu-icon {
            opacity: 0.85;
        }
        .maestri-menu-chevron {
            font-size: 14px;
            opacity: 0.6;
            margin-left: 8px;
        }
        .maestri-menu-check {
            font-size: 13px;
            color: #0A84FF;
            font-weight: bold;
            margin-right: 2px;
        }
        .maestri-node-blurred {
            filter: blur(5px);
            opacity: 0.7;
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

        # High priority CSS provider to guarantee traffic dots and transparent header are not overridden by user themes (e.g. WhiteSur)
        traffic_provider = Gtk.CssProvider()
        traffic_css = """
        headerbar {
            background-color: transparent;
            background-image: none;
            border: none;
            box-shadow: none;
        }
        .maestri-sidebar-top-bar {
            background-color: transparent;
            border-right: 1px solid rgba(128, 128, 128, 0.25);
        }
        headerbar button.maestri-traffic-dot,
        button.maestri-traffic-dot {
            min-width: 12px;
            min-height: 12px;
            border-radius: 9999px;
            border: 0.5px solid rgba(0, 0, 0, 0.2);
            padding: 0;
            margin: 0 3px;
            opacity: 1;
            box-shadow: none;
        }
        headerbar button.dot-close,
        button.dot-close {
            background-color: #FF5F56;
            background-image: none;
            border-color: #E0443E;
        }
        headerbar button.dot-close:hover,
        button.dot-close:hover {
            background-color: #E0443E;
        }
        headerbar button.dot-close:active,
        button.dot-close:active {
            background-color: #BF360C;
        }
        headerbar button.dot-min,
        button.dot-min {
            background-color: #FFBD2E;
            background-image: none;
            border-color: #DEA123;
        }
        headerbar button.dot-min:hover,
        button.dot-min:hover {
            background-color: #DEA123;
        }
        headerbar button.dot-min:active,
        button.dot-min:active {
            background-color: #C47D00;
        }
        headerbar button.dot-max,
        button.dot-max {
            background-color: #27C93F;
            background-image: none;
            border-color: #1AAB29;
        }
        headerbar button.dot-max:hover,
        button.dot-max:hover {
            background-color: #1AAB29;
        }
        headerbar button.dot-max:active,
        button.dot-max:active {
            background-color: #1B5E20;
        }
        .maestri-sidebar-menubar item {
            min-height: 24px;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
            color: rgba(255, 255, 255, 0.88);
        }
        .maestri-sidebar-menubar item:hover {
            background-color: rgba(255, 255, 255, 0.16);
            color: #ffffff;
        }
        .maestri-sidebar-menubar item:active,
        .maestri-sidebar-menubar item:checked {
            background-color: rgba(255, 255, 255, 0.24);
            color: #ffffff;
        }
        """
        traffic_provider.load_from_string(traffic_css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            traffic_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
        )

    def _setup_header_bar(self):
        header = Gtk.HeaderBar()
        header.set_show_title_buttons(False)
        header.set_title_widget(Gtk.Box())   # empty widget suppresses default centered window title
        self.set_titlebar(header)
        header.set_decoration_layout("")
        self._title_controls_timer = GLib.timeout_add(100, self._suppress_title_controls, header, None)

        # Sidebar Header Container (matches sidebar width when open)
        sidebar_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sidebar_header_box.add_css_class("maestri-sidebar-top-bar")
        sidebar_header_box.set_size_request(260, 44)
        sidebar_header_box.set_halign(Gtk.Align.START)
        sidebar_header_box.set_hexpand(False)
        self.sidebar_header_box = sidebar_header_box

        # macOS Window Traffic Lights (Red, Yellow, Green)
        traffic_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        traffic_box.add_css_class("maestri-traffic-lights")
        traffic_box.set_valign(Gtk.Align.CENTER)
        traffic_box.set_halign(Gtk.Align.CENTER)
        traffic_box.set_margin_start(2)
        traffic_box.set_margin_end(6)
        self.traffic_box = traffic_box

        dot_close = Gtk.Button()
        dot_close.add_css_class("maestri-traffic-dot")
        dot_close.add_css_class("dot-close")
        dot_close.set_valign(Gtk.Align.CENTER)
        dot_close.set_halign(Gtk.Align.CENTER)
        dot_close.set_size_request(12, 12)
        dot_close.set_tooltip_text("Close")
        dot_close.connect("clicked", lambda _b: self.close())
        traffic_box.append(dot_close)

        dot_min = Gtk.Button()
        dot_min.add_css_class("maestri-traffic-dot")
        dot_min.add_css_class("dot-min")
        dot_min.set_valign(Gtk.Align.CENTER)
        dot_min.set_halign(Gtk.Align.CENTER)
        dot_min.set_size_request(12, 12)
        dot_min.set_tooltip_text("Minimize")
        dot_min.connect("clicked", lambda _b: self.minimize())
        traffic_box.append(dot_min)

        dot_max = Gtk.Button()
        dot_max.add_css_class("maestri-traffic-dot")
        dot_max.add_css_class("dot-max")
        dot_max.set_valign(Gtk.Align.CENTER)
        dot_max.set_halign(Gtk.Align.CENTER)
        dot_max.set_size_request(12, 12)
        dot_max.set_tooltip_text("Zoom / Fullscreen")
        dot_max.connect("clicked", lambda _b: self.unmaximize() if self.is_maximized() else self.maximize())
        traffic_box.append(dot_max)

        sidebar_header_box.append(traffic_box)

        # Flexible spacer to push + v and [|] to the right edge of the sidebar
        sidebar_header_spacer = Gtk.Box()
        sidebar_header_spacer.set_hexpand(True)
        sidebar_header_box.append(sidebar_header_spacer)
        self.sidebar_header_spacer = sidebar_header_spacer

        # Quick Add (+ v) MenuButton
        add_child = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        img_plus = Gtk.Image.new_from_icon_name("list-add-symbolic")
        img_plus.set_pixel_size(14)
        img_chev = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        img_chev.set_pixel_size(10)
        add_child.append(img_plus)
        add_child.append(img_chev)
        btn_quick_add = Gtk.MenuButton(child=add_child)
        btn_quick_add.set_tooltip_text("Quick Create Node")
        btn_quick_add.add_css_class("maestri-header-btn")
        btn_quick_add.add_css_class("maestri-add-split-btn")
        btn_quick_add.set_valign(Gtk.Align.CENTER)
        quick_menu = Gio.Menu()
        quick_menu.append("New Workspace…", "win.new-workspace")
        quick_menu.append("New Group…", "win.new-group")
        quick_menu.append("Connect to Host…", "win.connect-ssh")   # Remote SSH workspace (ui/ssh_dialog.py)
        term_submenu = Gio.Menu()
        for preset in agent_presets(self):
            preset_item = Gio.MenuItem.new(preset["name"], None)
            preset_item.set_action_and_target_value("win.terminal-preset", GLib.Variant.new_string(preset["id"]))
            term_submenu.append_item(preset_item)
        term_submenu.append("New Terminal…", "win.new-terminal")
        quick_menu.append_submenu("Terminal", term_submenu)
        quick_menu.append("New Sticky Note", "win.new-note")
        quick_menu.append("New Web Portal", "win.new-portal")
        quick_menu.append("New File Tree", "win.new-file-tree")
        btn_quick_add.set_menu_model(quick_menu)
        self.btn_quick_add = btn_quick_add
        self.quick_menu = quick_menu
        self.terminal_presets_menu = term_submenu
        sidebar_header_box.append(btn_quick_add)

        # Toggle Sidebar button (macOS [|] style with maestri-sidebar-symbolic icon)
        btn_sidebar = Gtk.Button()
        img_sidebar = Gtk.Image.new_from_icon_name("maestri-sidebar-symbolic")
        img_sidebar.set_pixel_size(16)
        btn_sidebar.set_child(img_sidebar)
        btn_sidebar.set_tooltip_text("Toggle Sidebar")
        btn_sidebar.add_css_class("maestri-header-btn")
        btn_sidebar.set_valign(Gtk.Align.CENTER)
        btn_sidebar.connect("clicked", self._on_toggle_sidebar)
        btn_sidebar.get_label = lambda: "[|]"
        sidebar_header_box.append(btn_sidebar)
        self.btn_sidebar = btn_sidebar

        # Sidebar collapsed: [icon] Workspace name capsule next to the toggle (follows switches and renames)
        self.workspace_pill = WorkspacePill()
        self.workspace_pill.set_valign(Gtk.Align.CENTER)
        self.workspace_pill.set_visible(False)
        sidebar_header_box.append(self.workspace_pill)

        header.pack_start(sidebar_header_box)
        self._header = header

        # Right: Settings button
        btn_settings = Gtk.Button(icon_name=icon_name("settings"))
        btn_settings.set_tooltip_text("Preferences")
        btn_settings.add_css_class("maestri-header-btn")
        btn_settings.connect("clicked", lambda _b: self.show_preferences())
        header.pack_end(btn_settings)

        # Open in Editor (macOS View > Open in Editor, ⇧⌘E): the workspace folder in the preferred IDE
        btn_editor = Gtk.Button(icon_name=icon_name("code"))
        btn_editor.set_tooltip_text("Open in Editor (Ctrl+Shift+E)")
        btn_editor.add_css_class("maestri-header-btn")
        btn_editor.connect("clicked", lambda _b: self.open_in_editor())
        header.pack_end(btn_editor)
        self.btn_open_editor = btn_editor

        # Backend Connection status indicator
        self.lbl_status = StatusIndicator("pending", "Conectando...")   # CSS dot in the semantic colour + text (no emoji)
        self.lbl_status.set_margin_end(8)
        header.pack_end(self.lbl_status)

    def _check_connection_async(self):
        """Re-probe the core off the main thread and refresh the header label (the initial load sets it the first time)."""
        def worker():
            status = self.client.check_connection_status()
            if status == "connected":
                status_text, state = f"Porta {self.client.port}", "connected"
            elif status == "unauthorized":
                status_text, state = "Token Inválido", "error"
            else:
                status_text, state = "Standalone", "offline"
            GLib.idle_add(self._update_status_label, status_text, state)

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
                self.lbl_status.set_status("connected", f"Porta {self.client.port}")
                self.lbl_status.set_tooltip_text("Conectado e sincronizado.")

    def _on_save_failed_probe(self, status: str) -> None:
        if self._cleaned_up or not self.core_connected:
            return
        if status == "connected":
            self.lbl_status.set_status("warning", "Falha ao Salvar")
            self.lbl_status.set_tooltip_text("O último salvamento para o servidor falhou.")
        elif status == "unauthorized":
            self._on_core_probe(status)
        else:
            self._on_core_lost()

    def _update_status_label(self, status_text: str, state: str = "pending"):
        self.lbl_status.set_status(state, status_text)
        return GLib.SOURCE_REMOVE

    def _build_menu_model(self) -> Gio.Menu:
        """The macOS menu bar (Sources/MaestrixApp.swift + Localizable.xcstrings English strings), Cmd mapped to Ctrl:
        File: New Workspace (Ctrl+Shift+N) / new nodes / Routines… (Ctrl+Alt+R); View: Zoom In/Out/Reset Zoom, Open in Editor
        (Ctrl+Shift+E), Snap; Window: Next/Previous Workspace (Ctrl+Down/Up), Next/Previous Terminal (Ctrl+Tab)."""
        def section(items):
            m = Gio.Menu()
            for label, action in items:
                m.append(label, action)
            return m

        menubar = Gio.Menu()
        file_menu = Gio.Menu()
        file_menu.append_section(None, section([("New Workspace", "win.new-workspace"), ("New Group…", "win.new-group"),
                                                ("Connect to Host…", "win.connect-ssh")]))
        file_menu.append_section(None, section([
            ("New Terminal…", "win.new-terminal"), ("New Note", "win.new-note"),
            ("New Portal", "win.new-portal"), ("New File Tree", "win.new-file-tree"),
        ]))
        file_menu.append_section(None, section([("Routines…", "win.routines")]))
        file_menu.append_section(None, section([("Save", "win.save")]))
        file_menu.append_section(None, section([("Settings…", "win.preferences"), ("Quit Maestrix", "win.quit")]))
        menubar.append_submenu("_File", file_menu)

        edit_menu = Gio.Menu()
        edit_menu.append_section(None, section([("Copy Element", "win.copy-element"), ("Paste", "win.paste-element")]))
        menubar.append_submenu("_Edit", edit_menu)

        view_menu = Gio.Menu()
        view_menu.append_section(None, section([("Toggle Sidebar", "win.toggle-sidebar")]))
        view_menu.append_section(None, section([("Zoom In", "win.zoom-in"), ("Zoom Out", "win.zoom-out"),
                                                ("Reset Zoom", "win.reset-zoom")]))
        view_menu.append_section(None, section([("Snap to Alignment Guides", "win.snapping")]))   # check item (stateful action)
        view_menu.append_section(None, section([("Open in Editor", "win.open-in-editor")]))
        menubar.append_submenu("_View", view_menu)

        window_menu = Gio.Menu()
        window_menu.append_section(None, section([("Next Workspace", "win.next-workspace"),
                                                  ("Previous Workspace", "win.prev-workspace")]))
        window_menu.append_section(None, section([("Next Terminal", "win.next-terminal"),
                                                  ("Previous Terminal", "win.prev-terminal")]))
        menubar.append_submenu("_Window", window_menu)

        help_menu = Gio.Menu()
        help_menu.append_section(None, section([("About Maestrix", "win.about")]))
        menubar.append_submenu("_Help", help_menu)
        return menubar

    def _install_menubar(self) -> None:
        """The menu model is displayed directly in the header bar as a PopoverMenuBar;
        register the model on the app and keep the extra menubar row hidden."""
        app = self.get_application()
        if app is not None:
            try:
                app.set_menubar(self._build_menu_model())
            except Exception as exc:   # noqa: BLE001 - headless/unregistered app: the actions and accels still work
                print(f"[window] menubar not installed: {exc!r}")
        self.set_show_menubar(False)

    def _zoom_step(self, direction: int) -> None:
        cw = self.canvas_widget
        cw.viewport.zoom_step(Point((cw.get_width() or 1200) / 2.0, (cw.get_height() or 800) / 2.0), direction)
        cw.controller.dirty = True
        cw.queue_draw()
        bus.emit("viewport-changed", cw.viewport.origin.x, cw.viewport.origin.y, cw.viewport.zoom)

    def _setup_shortcuts(self):
        """Menu actions + accelerators: Ctrl+N new terminal, Ctrl+S save, Ctrl+0 reset zoom, Ctrl+Q quit,
        Ctrl+, preferences, Ctrl+B sidebar. Application accels run before focused widgets, so they work inside terminals."""
        handlers = {
            "new-workspace": lambda: self._on_new_workspace(),
            "connect-ssh": lambda: open_connect_to_host(self),
            "new-group": lambda: self._on_new_group(),
            "new-terminal": lambda: self.open_new_terminal_dialog(),
            "new-terminal-claude": lambda: self.create_terminal_preset("claude"),
            "new-terminal-codex": lambda: self.create_terminal_preset("codex"),
            "new-terminal-gemini": lambda: self.create_terminal_preset("gemini"),
            "new-terminal-antigravity": lambda: self.create_terminal_preset("antigravity"),
            "new-terminal-opencode": lambda: self.create_terminal_preset("opencode"),
            "new-terminal-shell": lambda: self.create_terminal_preset("shell"),
            "new-note": lambda: self._create_node("stickyNote"),
            "new-portal": lambda: self.open_new_portal_dialog(),
            "new-file-tree": lambda: self._create_node("fileTree"),
            "save": lambda: self._save_to_backend(),
            "preferences": lambda: self.show_preferences(),
            "quit": lambda: self.close(),
            "toggle-sidebar": lambda: self._on_toggle_sidebar(None),
            "reset-zoom": lambda: self.reset_zoom(),
            "zoom-in": lambda: self._zoom_step(+1),
            "zoom-out": lambda: self._zoom_step(-1),
            "about": lambda: self._show_about(),
            "open-in-editor": lambda: self.open_in_editor(),
            "next-workspace": lambda: self.next_workspace(),
            "prev-workspace": lambda: self.prev_workspace(),
            "next-terminal": lambda: self.step_terminal(+1),
            "prev-terminal": lambda: self.step_terminal(-1),
            "copy-element": lambda: self.copy_selected_nodes(),
            "paste-element": lambda: self.paste_nodes(),
        }
        for n in range(1, 10):   # Ctrl+1..9: jump to the n-th terminal (creation order), K9
            handlers[f"jump-terminal-{n}"] = (lambda k=n: self.jump_to_terminal(k))
        self._actions = handlers
        for name, fn in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, f=fn: f())
            self.add_action(action)

        preset_action = Gio.SimpleAction.new("terminal-preset", GLib.VariantType.new("s"))
        preset_action.connect("activate", lambda _a, p: self.create_terminal_preset(p.get_string()))
        self.add_action(preset_action)

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
                ("win.new-workspace", "<Control><Shift>n"), ("win.open-in-editor", "<Control><Shift>e"),
                ("win.next-workspace", "<Control>Down"), ("win.prev-workspace", "<Control>Up"),
                ("win.zoom-in", "<Control>equal"), ("win.zoom-out", "<Control>minus"),
            ):
                app.set_accels_for_action(action, [accel])
            for n in range(1, 10):
                app.set_accels_for_action(f"win.jump-terminal-{n}", [f"<Control>{n}"])
        self._install_menubar()

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
        self.interface_theme.apply(prefs.interface_theme)
        self.canvas_widget.renderer.dark_mode = prefs.interface_theme == "dark"
        self.terminal_presets_menu.remove_all()
        for preset in agent_presets(self):
            item = Gio.MenuItem.new(preset["name"], None)
            item.set_action_and_target_value("win.terminal-preset", GLib.Variant.new_string(preset["id"]))
            self.terminal_presets_menu.append_item(item)
        self.terminal_presets_menu.append("New Terminal…", "win.new-terminal")
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
            term.set_scrollback_lines(2000 if self.preferences.scrollback_memory_limit else 10000)
            frame.terminal_features.option_as_meta = self.preferences.option_as_meta

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
        agent_type = getattr(c, "agent_type", "") or agent_type_for_command(command)
        floor_id = self._floor_id_for(cwd)
        restoring = bool(getattr(self, "_restoring_nodes", False))   # a saved node being reopened, not a new one

        def worker():
            cmd = command
            try:
                if restoring:
                    meta = self.client.get_terminal(tid)
                    if meta is None or not meta.get("running", True):
                        cmd = resume_command(command)   # the shell is gone: reopen the agent on its last conversation
                self.client.ensure_terminal(tid, command=cmd, working_directory=cwd, workspace_id=workspace_id,
                                            display_name=getattr(c, "name", None), agent_type=agent_type, floor_id=floor_id,
                                            role_name=role_name(c))
            except Exception:
                pass   # the socket still tries (and reconnects with backoff) - the core may come back
            GLib.idle_add(lambda: (None if view.disposed else transport.connect(), GLib.SOURCE_REMOVE)[1])

        threading.Thread(target=worker, name=f"maestri-pty-{tid[:8]}", daemon=True).start()
        return view

    def update_workspace_pill(self) -> None:
        """Name / icon / colour of the open workspace in the header capsule; visible only while the sidebar is collapsed."""
        pill = getattr(self, "workspace_pill", None)
        if pill is None:
            return
        p = self.document.payload
        entry = None
        sb = getattr(self, "sidebar", None)
        if sb is not None and p.id:
            entry = next((w for w in getattr(sb, "_raw_workspaces", []) if w.id.upper() == str(p.id).upper()), None)
        from .sidebar import workspace_icon_name
        name = (entry.name if entry else p.name) or "Workspace"
        icon = (entry.icon if entry else p.icon) or "folder"
        color = (getattr(entry, "color", None) if entry else (getattr(p, "_extra", {}) or {}).get("color")) or "blue"
        pill.update(name, workspace_icon_name(icon), color)
        pill.set_visible(not getattr(self, "_sidebar_open", True) and bool(p.id) and not getattr(self, "_no_workspace", False))

    def _floor_id_for(self, cwd: str):
        """Id of the floor whose worktree contains `cwd` (K12 floorId), None on Ground."""
        floors = getattr(getattr(self, "floors", None), "floors", None) or []
        real = os.path.realpath(cwd or "")
        for f in floors:
            root = os.path.realpath(str(f.get("worktreePath") or "")) if f.get("worktreePath") else ""
            if root and (real == root or real.startswith(root.rstrip(os.sep) + os.sep)):
                return f.get("id")
        return None

    def _terminal_names(self, exclude_id: str | None = None) -> list[str]:
        return [n.content.name for n in self.document.payload.nodes
                if n.node_type == "terminal" and hasattr(n.content, "name") and n.id != exclude_id]

    def new_terminal_node(self, frame: NodeFrame, name: str = "Terminal", command: str = "bash", cwd: str = "",
                          **content_fields) -> CanvasNode:
        """A terminal CanvasNode whose node id IS its terminal id (K1, like macOS), upper-case, with agentType from the command.
        Not added to the document: callers use `_insert_node`."""
        nid = str(uuid.uuid4()).upper()
        name = unique_name(name, self._terminal_names())
        content_fields.setdefault("agent_type", agent_type_for_command(command))
        content = TerminalContent(id=nid, terminal_id=nid, name=name, command=command, working_directory=cwd, **content_fields)
        z = max((n.z_index for n in self.document.payload.nodes), default=0) + 1
        node = CanvasNode(id=nid, frame=frame, node_type="terminal", content=content, z_index=z)
        apply_element_defaults(node, self.preferences)
        apply_fields(content, content_fields)  # explicit dialog/preset choices win
        return node

    def _insert_node(self, node: CanvasNode, focus: bool = False, flush: bool = True) -> CanvasNode:
        """Add a node created by the UI to the document + canvas and persist it."""
        if getattr(self, "_no_workspace", False):
            return node   # the last workspace was deleted: nothing to add it to
        self.document.payload.nodes.append(node)
        self._add_node_to_canvas(node, focus=focus)
        self.canvas_widget.controller.dirty = True
        bus.emit("node-added", node)
        if flush and self.sync is not None:
            self.sync.flush()
        return node

    def _show_about(self):
        about = Gtk.AboutDialog(transient_for=self, modal=True, program_name="Maestrix",
                                comments="Software Linux desenvolvido por Euller. Baseado no OpenMaestri para macOS; interface e conceito inspirados no Maestri original.",
                                authors=["Euller — versão Linux Maestrix", "OpenMaestri contributors — projeto base"],
                                license_type=Gtk.License.GPL_3_0,
                                website="https://github.com/Potatoyz908/open-maestri-linux",
                                logo_icon_name="maestrix")
        about.present()

    def _sync_sidebar_header_width(self, pos: int) -> None:
        if not hasattr(self, "sidebar_header_box") or not getattr(self, "_sidebar_open", True):
            return
        self.sidebar_header_box.set_size_request(max(pos, 0), 44)

    def _on_paned_position_changed(self, paned, _param):
        if getattr(self, "_sidebar_open", True) and hasattr(self, "sidebar_header_box"):
            pos = paned.get_position()
            if pos > 0:
                self._sync_sidebar_header_width(pos)

    def _suppress_title_controls(self, header, _clock):
        if self._cleaned_up:
            return False
        # Suppress ghost titlebutton widgets injected by extensions (except inside sidebar_header_box / traffic_box)
        def visit(parent):
            child = parent.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                if (
                    child is getattr(self, "sidebar_header_box", None)
                    or child is getattr(self, "traffic_box", None)
                    or child is getattr(self, "popover_menubar", None)
                ):
                    pass
                elif isinstance(child, Gtk.WindowControls) or child.has_css_class("titlebutton"):
                    child.set_visible(False)
                    child.set_can_target(False)
                else:
                    visit(child)
                child = following
        visit(header)
        return True

    def _on_toggle_sidebar(self, btn=None):
        self._sidebar_open = not getattr(self, "_sidebar_open", True)
        if getattr(self, "_sidebar_tick", 0):
            self.paned.remove_tick_callback(self._sidebar_tick)
            self._sidebar_tick = 0
        opening = self._sidebar_open
        self.update_workspace_pill()
        start = self.paned.get_position() if self.sidebar.get_visible() else 0
        target = 260 if opening else 0
        self.sidebar.set_visible(True)
        if hasattr(self, "btn_floating_sidebar"):
            self.btn_floating_sidebar.set_visible(not opening)
        if hasattr(self, "btn_sidebar"):
            self.btn_sidebar.set_tooltip_text("Hide Sidebar" if opening else "Open Sidebar")
            if opening:
                self.btn_sidebar.add_css_class("active")
            else:
                self.btn_sidebar.remove_css_class("active")
        if hasattr(self, "sidebar_header_box"):
            if opening:
                self._sync_sidebar_header_width(target)
                self.sidebar_header_spacer.set_visible(True)
                self.btn_quick_add.set_visible(True)
                self.sidebar_header_box.add_css_class("sidebar-open")
            else:
                self.sidebar_header_box.set_size_request(-1, 44)
                self.sidebar_header_spacer.set_visible(False)
                self.btn_quick_add.set_visible(False)
                self.sidebar_header_box.remove_css_class("sidebar-open")
        settings = self.get_settings()
        animate = settings.get_property("gtk-enable-animations") if settings else False
        started = GLib.get_monotonic_time()

        def step(_widget, clock):
            elapsed = (clock.get_frame_time() - started) / 180000 if animate else 1.0
            progress = max(0.0, min(1.0, elapsed))
            eased = 1 - (1 - progress) ** 3
            current_pos = round(start + (target - start) * eased)
            self.paned.set_position(current_pos)
            if hasattr(self, "sidebar_header_box") and opening:
                self._sync_sidebar_header_width(current_pos)
            self.sidebar.set_opacity(eased if opening else 1 - eased)
            if progress >= 1:
                self.sidebar.set_visible(opening)
                self.sidebar.set_opacity(1)
                self._sidebar_tick = 0
                if not opening and hasattr(self, "sidebar_header_box"):
                    self.sidebar_header_box.set_size_request(-1, 44)
                return False
            return True
        self._sidebar_tick = self.paned.add_tick_callback(step)

    # ---- canvas <-> document -------------------------------------------------------------------
    def _viewport_from_payload(self) -> Viewport:
        p = self.document.payload
        ox = p.canvas_origin[0] if len(p.canvas_origin) > 0 else 9800.0
        oy = p.canvas_origin[1] if len(p.canvas_origin) > 1 else 8500.0
        return Viewport(Point(ox, oy), p.canvas_zoom or 1.0)

    def _configure_notes_backend(self) -> None:
        """Managed notes go through the core REST API when it is reachable; offline they live in memory."""
        cw = self.canvas_widget
        cw.connections_client = self.client if self.core_connected else None
        cw.connections_workspace_id = self.document.payload.id
        if self.core_connected:
            cw.notes_client = CoreNotesClient(self.client.port, self.client.token, workspace_id=self.document.payload.id)
        from maestri_core.paths import data_dir
        ws_dir = data_dir() / "workspaces" / self.document.payload.id
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
        item = NodeItem(node.id, Rect(f.x, f.y, f.width, f.height), node.z_index, node.node_type, node.title,
                        locked=bool(node.is_locked), content=drawn)   # K9: isLocked / zIndex come back on load
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
            cmd = getattr(node.content, "command", None) if not isinstance(node.content, dict) else node.content.get("command")
            if cmd:
                kwargs["command"] = cmd
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

    def _push_terminal_meta(self, node: CanvasNode) -> None:
        """Item 10: a renamed terminal (or new role) reaches the core's session (displayName / roleName), off the main loop.
        The name is also in workspace.json after the flush; PATCH is best effort (older cores answer 404)."""
        if node.node_type != "terminal" or not self.core_connected:
            return
        tid, name, role = self._terminal_id(node), node.content.name, role_name(node.content)
        threading.Thread(target=lambda: self.client.update_terminal(tid, display_name=name, roleName=role),
                         name="maestri-terminal-meta", daemon=True).start()

    # ---- K13: reload / unload a terminal ----------------------------------------------------------------------------
    def reload_terminal(self, node_id: str) -> bool:
        """Restart the node's shell with the same parameters (core: POST /api/terminals/:id/restart; local VTE: respawn)."""
        node = self._find_node(node_id)
        if node is None or node.node_type != "terminal":
            return False
        if self.core_connected:
            tid = self._terminal_id(node)

            def worker():
                try:
                    self.client.restart_terminal(tid)
                    ok = True
                except Exception as exc:   # noqa: BLE001 - older cores have no restart route: recreate the session instead
                    print(f"[window] restart {tid[:8]} failed ({exc!r}); recreating the session")
                    try:
                        self.client.delete_terminal(tid)
                        ok = True
                    except Exception:   # noqa: BLE001
                        ok = False
                GLib.idle_add(lambda: (self._rebuild_terminal(node) if ok else None, GLib.SOURCE_REMOVE)[1])
            threading.Thread(target=worker, name="maestri-terminal-restart", daemon=True).start()
        else:
            self._rebuild_terminal(node)
        return True

    def unload_terminal(self, node_id: str) -> bool:
        """Stop the shell but keep the node (core: POST /api/terminals/:id/unload). Reload brings it back."""
        node = self._find_node(node_id)
        if node is None or node.node_type != "terminal":
            return False
        frame = self.canvas_widget.frames.get(norm(node.id))
        if frame is not None:
            if is_terminal(frame.content):
                terminate_terminal(frame.content)
            elif getattr(frame.content, "transport", None) is not None:
                frame.content.dispose()
        if self.core_connected:
            tid = self._terminal_id(node)

            def worker():
                try:
                    self.client.unload_terminal(tid)
                except Exception:   # noqa: BLE001 - older cores: ending the session is the closest equivalent
                    try:
                        self.client.delete_terminal(tid)
                    except Exception:   # noqa: BLE001
                        pass
            threading.Thread(target=worker, name="maestri-terminal-unload", daemon=True).start()
        return True

    def _rebuild_terminal(self, node: CanvasNode) -> None:
        """Replace the node's widget (same frame/selection) so it attaches to the fresh PTY."""
        if self._find_node(node.id) is None:
            return
        frame = self.canvas_widget.frames.get(norm(node.id))
        if frame is not None:
            if is_terminal(frame.content):
                terminate_terminal(frame.content)
            elif getattr(frame.content, "transport", None) is not None:
                frame.content.dispose()
        self.canvas_widget.remove_node(node.id)
        self._add_node_to_canvas(node)
        self.canvas_widget.controller.dirty = True

    def _on_node_title_changed(self, _canvas, node_id, new_title):
        """Title renamed directly on node header via inline entry."""
        node = self._find_node(node_id)
        if node is None:
            return
        if hasattr(node.content, "name"):
            if node.node_type == "terminal":
                new_title = unique_name(new_title, self._terminal_names(node.id))
            node.content.name = new_title
        elif isinstance(node.content, dict):
            node.content["name"] = new_title
        node.last_modified_at = current_iso8601()
        bus.emit("node-updated", node.id)
        if self.sync is not None:
            self.sync.flush()
        self._push_terminal_meta(node)

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
        """One dispatcher (context_menus.open_context_menu): canvas / connection / node menus; denied over the content
        of notes, portals and file trees so their own menus keep working."""
        from .context_menus import open_context_menu
        open_context_menu(self, gesture, x, y)

    def _load_payload_into_canvas(self) -> None:
        if dedupe_terminal_names(self.document.payload.nodes):
            self._dedupe_renamed = True   # persisted: _apply_workspace_switched marks the canvas dirty right after loading
        self._restoring_nodes = True
        try:
            for node in self.document.payload.nodes:
                self._add_node_to_canvas(node)
        finally:
            self._restoring_nodes = False
        for conn in self.document.payload.all_connections():
            if conn.a_id and conn.b_id:
                self.canvas_widget.add_connection(ConnItem(conn.id, conn.a_id, conn.b_id), conn.rope_points)

    def _on_rope_settled(self, _canvas, conn_id, points):
        import json
        conn = next((c for c in self.document.payload.all_connections() if c.id.upper() == conn_id.upper()), None)
        if conn is not None and conn.rope_points != points:
            conn.rope_points = points
            bus.emit("connection-changed", conn.id, json.dumps(points))

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
            if node is not None and node.is_locked:
                continue   # locked nodes cannot be deleted (Unlock first), like macOS
            conn_ids = [c.id for c in self.document.payload.all_connections()
                        if nid.upper() in (c.a_id.upper(), c.b_id.upper())]
            if conn_ids and self.core_connected:   # revoke them in the core too (K6/K2), not only in the saved document
                ws = self.document.payload.id
                for cid in conn_ids:
                    threading.Thread(target=self._delete_core_connection, args=(cid, ws), daemon=True).start()
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

    def _on_canvas_duplicate_requested(self, _canvas, mapping: dict):
        for src_id, new_frame in mapping.items():
            src_node = self._find_node(src_id)
            if src_node is not None:
                self._paste_payload(self._element_json([src_node]), Point(new_frame.x, new_frame.y))

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
            GLib.idle_add(self._apply_connection_created, cid, conn, target_list, a, b, ws_id, kind)

        threading.Thread(target=create_worker, daemon=True).start()

    def _apply_connection_created(self, temp_cid: str, conn, target_list, a: str = "", b: str = "", ws_id: str = "", kind: str = "terminal"):
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
            from maestri_core.models.connection import Connection
            local_id = str(uuid.uuid4()).upper()
            local_conn = Connection(id=local_id, a_id=a, b_id=b, kind=kind)
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
        preset = next((p for p in agent_presets(self) if p["id"] == preset_id), None)
        if preset is None and preset_id == "antigravity":
            preset = next(p for p in PRESETS if p["id"] == "antigravity")
        if preset is None:
            return None
        cwd = self.floors.terminal_cwd() or self.document.payload.working_directory or os.path.expanduser("~")
        spawn_at = at or Point(
            self.canvas_widget.viewport.origin.x + (self.canvas_widget.get_width() or 1000) / 2 / self.canvas_widget.viewport.zoom - 210,
            self.canvas_widget.viewport.origin.y + (self.canvas_widget.get_height() or 700) / 2 / self.canvas_widget.viewport.zoom - 150,
        )
        frame = NodeFrame(x=spawn_at.x, y=spawn_at.y, width=420, height=300)
        node = self.new_terminal_node(frame, preset["name"], preset["command"], cwd,
                                      **{k: v for k, v in (("icon", preset.get("sf_icon", preset.get("icon"))), ("color", preset.get("color"))) if v})
        return self._insert_node(node)

    def open_new_portal_dialog(self):
        from .portal_prompt import ask_portal_url
        return ask_portal_url(self, lambda name, url: self._create_node(
            "portal", content=PortalContent(name=name or "Portal", current_url=url)))

    def open_new_terminal_dialog(self, at: Point | None = None, size: tuple[float, float] | None = None, default_preset: str = "claude") -> NewTerminalDialog:
        """Opens the macOS-matching 'New Terminal' modal dialog with Quick Start presets."""
        def on_create(data: dict):
            cwd = data.get("cwd") or self.floors.terminal_cwd() or os.path.expanduser("~")
            name = data.get("name") or "Terminal"
            cmd = data.get("command") or "bash"
            term_w = size[0] if size else 560
            term_h = size[1] if size else 360
            spawn_at = at or Point(
                self.canvas_widget.viewport.origin.x + (self.canvas_widget.get_width() or 1000) / 2 / self.canvas_widget.viewport.zoom - term_w / 2,
                self.canvas_widget.viewport.origin.y + (self.canvas_widget.get_height() or 700) / 2 / self.canvas_widget.viewport.zoom - term_h / 2,
            )
            frame = NodeFrame(x=spawn_at.x, y=spawn_at.y, width=term_w, height=term_h)
            node = self.new_terminal_node(frame, name, cmd, cwd, **terminal_fields_from_dialog(data))
            if size is not None:
                node.frame.width, node.frame.height = size
            self._insert_node(node)

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

    def edit_selected_node(self, target_node_id: str | None = None) -> None:
        """Edits the node (rename, settings, etc.). Uses target_node_id if given, else selection."""
        node_id = target_node_id
        if not node_id:
            sel = list(self.canvas_widget.scene.selection)
            if not sel:
                return
            node_id = sel[0]
        node = self._find_node(node_id)
        if node is None:
            return
        if node.node_type == "terminal":
            def on_save(data: dict):
                c = node.content
                renamed = data.get("name") and data["name"] != c.name
                c.name = unique_name(data.get("name") or c.name, self._terminal_names(node.id))
                c.command = data.get("command") or c.command
                c.working_directory = data.get("cwd", c.working_directory)
                changed = apply_fields(c, terminal_fields_from_dialog(data))   # icon, color, theme, font, monitor, maestro, role
                node.last_modified_at = current_iso8601()
                frame = self.canvas_widget.frames.get(norm(node.id))
                if frame and hasattr(frame, "title"):
                    frame.title.set_label(c.name)
                if {"theme_id", "font_family", "font_size"} & set(changed):
                    self._style_terminal(node)
                self.canvas_widget.controller.dirty = True
                self.canvas_widget.queue_draw()
                bus.emit("node-updated", node.id)
                if self.sync is not None:
                    self.sync.flush()
                if renamed or "assigned_role_id" in changed:
                    self._push_terminal_meta(node)

            dlg = EditTerminalDialog(self, node, on_save)
            dlg.present()
        elif node.node_type == "stickyNote":
            frame = self.canvas_widget.frames.get(norm(node.id))
            if frame:
                frame.content.begin_rename()
        elif node.node_type == "portal":
            from .portal_prompt import NewPortalDialog
            def save_portal(name, url):
                node.content.name = name or "Portal"
                frame = self.canvas_widget.frames.get(norm(node.id))
                if frame:
                    frame.content.meta.name = node.content.name
                    frame.content.navigate(url)
                    self._on_portal_meta_changed(None, node.id, frame.content.meta.to_dict())
            dlg = NewPortalDialog(self, save_portal, title="Edit Portal", submit_label="Save")
            dlg.name_entry.set_text(node.content.name)
            dlg.url_entry.set_text(node.content.current_url)
            dlg.present()
        elif node.node_type == "fileTree":
            chooser = Gtk.FileDialog(title="Choose Root Folder")
            def selected(d, result):
                try:
                    folder = d.select_folder_finish(result)
                    frame = self.canvas_widget.frames.get(norm(node.id))
                    if folder and frame:
                        frame.content.set_root(folder.get_path())
                except GLib.Error:
                    pass
            chooser.select_folder(self, None, selected)

    def _on_new_workspace(self) -> None:
        """Opens macOS-matching 'New Workspace' modal sheet."""
        def handle_created(name: str, wd: str, icon: str, color: str = "blue"):
            def worker():
                new_id = create_workspace_record(self.client, name, wd, icon, color)
                def on_done():
                    self.sidebar.refresh()
                    self._on_workspace_switched(None, new_id)
                    return GLib.SOURCE_REMOVE
                GLib.idle_add(on_done)
            threading.Thread(target=worker, daemon=True).start()

        dlg = CreateWorkspaceDialog(self, handle_created)
        dlg.present()

    def _on_new_group(self) -> None:
        """Opens macOS-matching 'New Group' modal dialog."""
        def handle_ok(name: str):
            self.sidebar.add_group(name)   # updates the sidebar now, persists online or to sidebar-layout.json offline

        dlg = GroupDialog(self, "New Group", on_ok=handle_ok)
        dlg.present()

    def _create_node(self, node_type: str, at: Point = None, size: tuple = None, content=None):
        """Add a node of a valid kind (terminal | stickyNote | portal | fileTree).
        `at` = canvas point for the node's top-left corner (context menu); default: centered in the viewport.
        `size` = (w, h) of a frame drawn with a tool (default 420x300). `content` overrides the default content (file drops).
        Ignored until the initial workspace is loaded (the placeholder document would be replaced)."""
        if node_type not in WIDGET_KINDS:
            raise ValueError(f"invalid node type: {node_type!r}")
        if not self.loaded or getattr(self, "_no_workspace", False):
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

        supplied_content = content is not None
        if content is None:
            if node_type == "terminal":
                content = TerminalContent(name=f"Terminal {n}", command="bash", working_directory=self.floors.terminal_cwd())
            elif node_type == "stickyNote":
                taken = {x.content.file_name[:-3] if x.content.file_name.endswith(".md") else x.content.file_name
                         for x in self.document.payload.nodes if x.node_type == "stickyNote"}
                base = unique_name("Note", taken)
                content = StickyNoteContent(name=base, file_name=f"{base}.md")
            elif node_type == "portal":
                content = PortalContent(name="Portal", current_url="https://google.com")
            else:
                content = FileTreeContent(name="Files", root_path=self.floors.terminal_cwd()
                                          or self.document.payload.working_directory or os.path.expanduser("~"))
        if node_type == "terminal":   # K1: node id == terminal id, including supplied content
            tid = str(content.terminal_id or uuid.uuid4()).upper()
            content.id = content.terminal_id = tid
            if content.agent_type in ("", "generic_shell"):
                content.agent_type = agent_type_for_command(content.command)

        new_node = CanvasNode(
            **({"id": content.terminal_id} if node_type == "terminal" else {}),
            frame=NodeFrame(x=cx, y=cy, width=size[0] if size else 420, height=size[1] if size else 300),
            node_type=node_type,
            content=content,
            z_index=max_z + 1,
        )
        apply_element_defaults(new_node, self.preferences, size=size is None, appearance=not supplied_content)
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
        if getattr(self, "_title_controls_timer", 0):
            GLib.source_remove(self._title_controls_timer)
            self._title_controls_timer = 0
        if getattr(self, "_sidebar_tick", 0):
            try:
                self.paned.remove_tick_callback(self._sidebar_tick)
            except Exception:
                pass
            self._sidebar_tick = 0
        self._core_link.dispose()
        self.config_sync.detach()
        if self._events_stream is not None:
            try:
                self._events_stream.close()
            except Exception:   # noqa: BLE001
                pass
            self._events_stream = None
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = 0
        if self.sync is not None:
            self.sync.close()
            self.sync = None
        if getattr(self, "attention", None) is not None:
            self.attention.stop()
        if hasattr(self, "sidebar") and self.sidebar is not None:
            self.sidebar.dispose()   # count poll timer + bus handlers (also closes its popovers)
        if hasattr(self, "floor_button") and self.floor_button is not None:
            try:
                self.floor_button.popover.popdown()
            except Exception:
                pass
        if hasattr(self, "ask_agent") and self.ask_agent is not None:
            self.ask_agent.dispose()
            self.ask_agent = None
        for obj, hid in list(self._bus_handlers):
            if hasattr(obj, "handler_is_connected") and obj.handler_is_connected(hid):
                obj.disconnect(hid)
        self._bus_handlers.clear()
        self._release_terminals()

    def _on_close_request(self, *_args):
        if self.sync is None:
            self._persist_offline_document()
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

    def _persist_offline_document(self) -> None:
        """Write the open workspace to disk when there is no core.

        Without a server there is no WorkspaceSync, so nothing else saves the document: leaving the workspace or closing
        the window used to discard every node created since it was loaded (the canvas came back empty and workspace.json
        still had zero nodes). Uses the same atomic writer as the rest of the offline path. The offline demo document
        ("Default") is not a workspace and is never written.
        """
        doc = getattr(self, "document", None)
        if (doc is None or getattr(doc, "payload", None) is None or not doc.payload.id
                or getattr(self, "_is_offline_placeholder", False) or not getattr(self, "_offline_mutated", False)):
            return
        try:
            cw = self.canvas_widget
            doc.schema_version = 2
            doc.type = "workspace"
            doc.payload.canvas_origin = [float(cw.viewport.origin.x), float(cw.viewport.origin.y)]
            doc.payload.canvas_zoom = float(cw.viewport.zoom)
            from maestri_core.persistence.manager import PersistenceManager
            PersistenceManager().save_workspace(doc)
            self._offline_mutated = False
        except Exception:
            import traceback
            traceback.print_exc()

    def _on_workspace_switched(self, bus_obj, workspace_id: str):
        if not self.loaded:
            return
        # Persist the workspace we are leaving before replacing the document.
        if self.sync is not None:
            self.sync.flush()
        else:
            self._persist_offline_document()   # no core -> no WorkspaceSync: nothing else would write it

        def switch_worker():
            doc = None
            try:
                doc = self.client.get_workspace(workspace_id)
            except Exception:
                doc = None
            if doc is None:
                from maestri_core.paths import data_dir
                local_path = str(data_dir() / "workspaces" / workspace_id / "workspace.json")
                if os.path.isfile(local_path):
                    try:
                        with open(local_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict) and data.get("schemaVersion") == 2:
                            doc = WorkspaceDocument.from_dict(data)
                    except Exception:
                        pass
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
            self._is_offline_placeholder = False   # a real workspace now, not the offline demo document
            self._offline_mutated = False          # loading is not an edit; only later changes need saving
            cw.controller.dirty = True
            self._no_workspace = False
            if self.sync is None and self.core_connected and not self.auth_failed:
                self._enable_sync()   # after close_workspace() (last workspace deleted) the next one syncs again
            bus.emit("workspace-loaded", doc.payload)
        return GLib.SOURCE_REMOVE


OpenMaestriWindow = MaestrixWindow   # pre-rebrand name: existing tests and callers still import it
