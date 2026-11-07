"""
Generate high-resolution real screenshots of Maestrix in Dark and Light themes.
Captures genuine GTK 4 + Cairo canvas windows with real VTE terminals,
Markdown notes, FileTree nodes, and inter-agent Verlet rope connections.
"""

import json
import os
import sys
import time
import shutil
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, Gio, GLib

from maestri_canvas.demo import screenshot
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import ConnItem
from maestri_core.client.api import MaestriClient
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, FileTreeContent
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload, WorkspaceEntry
from ui.window import OpenMaestriWindow
from ui.theme import InterfaceTheme
from ui.terminal_dialog import NewTerminalDialog
from tests.data_dir_isolation import start_isolated_server

def pump_events(ms=100):
    ctx = GLib.MainContext.default()
    end = time.time() + (ms / 1000.0)
    while time.time() < end:
        while ctx.iteration(False):
            pass
        time.sleep(0.005)

def run():
    print("🎬 Generating real Maestrix screenshots under GTK4...")
    tmp_dir = Path("/tmp/maestrix-screenshots-data")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    
    # Pre-populate preferences so the app starts in complete Dark mode with solid dark background
    data_dir = tmp_dir / ".open-maestri"
    data_dir.mkdir(parents=True)
    prefs_payload = {
        "schemaVersion": 1,
        "interfaceTheme": "dark",
        "canvasBackground": "solid",
        "terminalTheme": "dark"
    }
    (data_dir / "preferences.json").write_text(json.dumps(prefs_payload))
    os.environ["OPEN_MAESTRI_DATA_DIR"] = str(data_dir)

    docs_images = ROOT_DIR / "docs" / "images"
    docs_images.mkdir(parents=True, exist_ok=True)

    with start_isolated_server(tmp_dir) as srv:
        client = MaestriClient(port=srv["port"], auth_token=srv["token"])
        app = Gtk.Application(
            application_id="org.maestrix.screenshot.gen",
            flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        app.register(None)

        win = OpenMaestriWindow(app, client)
        win.set_default_size(1440, 900)
        win.present()
        win.wait_loaded(timeout=10.0)
        pump_events(200)

        # 1. Setup rich realistic workspace
        ws = client.create_workspace("Maestrix Autonomous Squad", working_directory=str(ROOT_DIR))
        win._on_workspace_switched(None, ws.id)
        pump_events(300)

        # Terminals: Claude Code & Codex CLI
        t1 = win.new_terminal_node(
            NodeFrame(x=9800, y=8550, width=460, height=330),
            name="Architect (Claude Code)",
            command="bash",
            cwd=str(ROOT_DIR)
        )
        t2 = win.new_terminal_node(
            NodeFrame(x=10450, y=8550, width=460, height=330),
            name="Reviewer (Codex CLI)",
            command="bash",
            cwd=str(ROOT_DIR)
        )
        win._insert_node(t1)
        win._insert_node(t2)
        pump_events(200)

        # Inter-agent connection cable
        conn = client.create_connection(t1.id, t2.id, kind="terminal", workspace_id=ws.id)
        if conn:
            win.document.payload.connections.append(conn)
            win.canvas_widget.add_connection(ConnItem(conn.id, t1.id, t2.id, "right", "left"))
        pump_events(150)

        # Create Note node via window API
        win._create_node("stickyNote", at=Point(9800, 8980), size=(440, 310))
        pump_events(150)

        # Create FileTree node via window API
        win._create_node("fileTree", at=Point(10350, 8980), size=(440, 310))
        pump_events(200)

        cw = win.canvas_widget

        # Find the note widget and fill in nice roadmap text and format it
        for n_id, n_frame in cw.frames.items():
            content_w = n_frame.content
            if hasattr(content_w, "_set_text"):
                content_w._set_text("""# Maestrix Linux Roadmap
- [x] Native GTK4 + Cairo Canvas
- [x] POSIX PTY Multi-Agent Terminals
- [x] Inter-Agent Communication (omaestri ask)
- [x] Dark & Light Interface Themes
- [x] Standalone .deb Package

> *"The spatial AI canvas for autonomous agents."*
""")
            if hasattr(content_w, "set_previewing"):
                content_w.set_previewing(True)
            if hasattr(content_w, "set_color"):
                content_w.set_color("blue")

        # Center viewport around nodes
        cw.viewport.origin = Point(9650, 8430)
        cw.viewport.zoom = 1.0
        cw.controller.dirty = True
        pump_events(300)

        # Write sample text to terminals to make them look authentic
        try:
            from maestri_canvas.widget import terminal_of
            if t1.id in cw.frames:
                term1_widget = terminal_of(cw.frames[t1.id].content)
                if term1_widget and hasattr(term1_widget, "feed_child"):
                    term1_widget.feed_child(b"\x1b[1;36m[Architect]\x1b[0m omaestri ask \"Reviewer\" \"Please verify the dark theme PR\"\r\n")
            if t2.id in cw.frames:
                term2_widget = terminal_of(cw.frames[t2.id].content)
                if term2_widget and hasattr(term2_widget, "feed_child"):
                    term2_widget.feed_child(b"\x1b[1;32m[Reviewer]\x1b[0m Checking diff and test coverage... All 146 tests passed!\r\n")
        except Exception as e:
            print("Note on terminal feeds:", e)
        pump_events(400)

        theme = InterfaceTheme()

        # =====================================================================
        # 1. DARK THEME SCREENSHOTS (100% Full Dark Canvas Background)
        # =====================================================================
        print("📸 Capturing Dark Theme screenshots...")
        theme.apply("dark")
        win.preferences.interface_theme = "dark"
        win.preferences.canvas_background = "solid"
        cw.renderer.dark_mode = True
        cw.renderer.background = "solid"
        cw.controller.dirty = True
        cw.layer.queue_draw()
        cw.top_layer.queue_draw()
        pump_events(400)

        # Full Window (Home Dark)
        dark_home = docs_images / "maestrix-dark-home.png"
        screenshot(win, str(dark_home))
        shutil.copyfile(dark_home, docs_images / "home.png")
        print(f"  ✓ {dark_home}")

        # Canvas detail (Agents Dark)
        dark_agents = docs_images / "maestrix-dark-agents.png"
        screenshot(cw, str(dark_agents))
        shutil.copyfile(dark_agents, docs_images / "agents.png")
        print(f"  ✓ {dark_agents}")

        # Files detail (Files Dark)
        dark_files = docs_images / "maestrix-dark-files.png"
        cw.viewport.origin = Point(10200, 8850)
        cw.controller.dirty = True
        cw.layer.queue_draw()
        pump_events(200)
        screenshot(cw, str(dark_files))
        shutil.copyfile(dark_files, docs_images / "file.png")
        print(f"  ✓ {dark_files}")

        # Dialog screenshot (Terminal Dialog Dark)
        print("📸 Capturing New Terminal Dialog...")
        cw.viewport.origin = Point(9650, 8430)
        cw.controller.dirty = True
        cw.layer.queue_draw()
        pump_events(100)
        dlg = NewTerminalDialog(win, on_create=lambda res: None, default_cwd=str(ROOT_DIR))
        dlg.set_default_size(520, 520)
        dlg.present()
        pump_events(300)
        dark_dlg = docs_images / "create-terminal.png"
        screenshot(dlg, str(dark_dlg))
        dlg.destroy()
        pump_events(100)
        print(f"  ✓ {dark_dlg}")

        # =====================================================================
        # 2. LIGHT THEME SCREENSHOTS
        # =====================================================================
        print("📸 Capturing Light Theme screenshots...")
        theme.apply("light")
        win.preferences.interface_theme = "light"
        win.preferences.canvas_background = "dotGrid"
        cw.renderer.dark_mode = False
        cw.renderer.background = "dotGrid"
        cw.viewport.origin = Point(9650, 8430)
        cw.controller.dirty = True
        cw.layer.queue_draw()
        cw.top_layer.queue_draw()
        pump_events(400)

        # Full Window (Home Light)
        light_home = docs_images / "maestrix-light-home.png"
        screenshot(win, str(light_home))
        print(f"  ✓ {light_home}")

        # Canvas detail (Agents Light)
        light_agents = docs_images / "maestrix-light-agents.png"
        screenshot(cw, str(light_agents))
        print(f"  ✓ {light_agents}")

        win.destroy()
        pump_events(100)

    print("🎉 All real screenshots generated successfully!")

if __name__ == "__main__":
    run()
