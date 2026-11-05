#!/usr/bin/env python3
"""
Open Maestri - Linux Desktop Application Runner
Launches the Open Maestri Core backend and displays the Canvas in a native GTK3 + WebKit2 window.
"""

import os
import sys
import time
import signal
import socket
import shutil
import threading
import subprocess
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, Gdk, WebKit2, GLib, GdkPixbuf

# Locate repository root and installation paths
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DEFAULT_SHARE_DIR = Path.home() / ".local" / "share" / "open-maestri"


def find_server_binary() -> Path:
    candidates = [
        REPO_DIR / ".build" / "release" / "open-maestri-server",
        REPO_DIR / ".build" / "debug" / "open-maestri-server",
        DEFAULT_SHARE_DIR / "bin" / "open-maestri-server",
        Path.home() / ".local" / "bin" / "open-maestri-server",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    which_bin = shutil.which("open-maestri-server")
    if which_bin:
        return Path(which_bin)
    raise FileNotFoundError("Could not find 'open-maestri-server' binary. Run 'swift build' first.")


def find_web_dist_dir() -> Path:
    candidates = [
        REPO_DIR / "Web" / "dist",
        DEFAULT_SHARE_DIR / "Web" / "dist",
    ]
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    raise FileNotFoundError("Could not find 'Web/dist/index.html'. Run 'cd Web && npm run build' first.")


def find_app_icon() -> Path:
    candidates = [
        REPO_DIR / "Sources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512.png",
        REPO_DIR / "Sources" / "Assets.xcassets" / "AppIcon.appiconset" / "icon_512x512@2x.png",
        Path.home() / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps" / "open-maestri.png",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy GET logs
        pass


class LocalWebServer:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        # Bind to 127.0.0.1 on port 0 for random free ephemeral port
        handler = lambda *args, **kwargs: QuietHTTPRequestHandler(*args, directory=str(root_dir), **kwargs)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class OpenMaestriApp:
    def __init__(self):
        self.server_proc = None
        self.web_server = None
        self.server_port = None
        self.socket_path = None

        self.server_bin = find_server_binary()
        self.web_dist = find_web_dist_dir()
        self.icon_path = find_app_icon()

    def start_backend(self):
        print(f"[Open Maestri] Starting Core Server: {self.server_bin}")
        env = os.environ.copy()
        self.server_proc = subprocess.Popen(
            [str(self.server_bin)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Monitor output to detect port
        for line in iter(self.server_proc.stdout.readline, ""):
            print(f"[Core] {line.strip()}")
            if "Open Maestri Server active on port" in line:
                try:
                    self.server_port = int(line.strip().split()[-1])
                except ValueError:
                    pass
            if "Unix Domain Socket:" in line:
                self.socket_path = line.strip().split()[-1]
            if self.server_port and self.socket_path:
                break

        print(f"[Open Maestri] Backend ready (HTTP port: {self.server_port}, Socket: {self.socket_path})")

    def start_web_server(self):
        self.web_server = LocalWebServer(self.web_dist)
        self.web_server.start()
        print(f"[Open Maestri] Web UI server listening at http://127.0.0.1:{self.web_server.port}")

    def create_window(self):
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title("Open Maestri")
        window.set_default_size(1440, 900)
        window.set_position(Gtk.WindowPosition.CENTER)

        if self.icon_path and self.icon_path.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(self.icon_path))
                window.set_icon(pixbuf)
            except Exception as e:
                print(f"[Open Maestri] Note: Could not set window icon: {e}")

        # Configure WebKit Settings
        settings = WebKit2.Settings()
        settings.set_enable_javascript(True)
        settings.set_enable_webgl(True)
        settings.set_enable_developer_extras(True)
        settings.set_javascript_can_access_clipboard(True)
        settings.set_enable_write_console_messages_to_stdout(True)

        self.webview = WebKit2.WebView.new_with_settings(settings)
        self.inspector = self.webview.get_inspector()

        # Keyboard shortcuts (F12 for Developer Tools)
        def on_key_press(widget, event):
            if event.keyval == Gdk.KEY_F12:
                if self.inspector.is_attached():
                    self.inspector.close()
                else:
                    self.inspector.show()
                return True
            return False

        window.connect("key-press-event", on_key_press)

        # Scrolled container for webview
        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.webview)
        window.add(scrolled)

        # Window cleanup on close
        window.connect("destroy", self.on_window_destroy)

        # URL to load
        query_params = []
        if self.server_port:
            query_params.append(f"corePort={self.server_port}")
        if self.socket_path:
            query_params.append(f"socket={self.socket_path}")

        url = f"http://127.0.0.1:{self.web_server.port}/"
        if query_params:
            url += "?" + "&".join(query_params)

        print(f"[Open Maestri] Loading Canvas: {url}")
        self.webview.load_uri(url)

        window.show_all()
        return window

    def on_window_destroy(self, *args):
        print("[Open Maestri] Closing application...")
        self.cleanup()
        Gtk.main_quit()

    def cleanup(self):
        if self.server_proc and self.server_proc.poll() is None:
            print("[Open Maestri] Terminating Core Server...")
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
        if self.web_server:
            print("[Open Maestri] Stopping Web UI server...")
            self.web_server.stop()

    def run(self):
        signal.signal(signal.SIGINT, lambda s, f: self.on_window_destroy())
        signal.signal(signal.SIGTERM, lambda s, f: self.on_window_destroy())

        self.start_backend()
        self.start_web_server()
        self.create_window()
        Gtk.main()


if __name__ == "__main__":
    app = OpenMaestriApp()
    app.run()
