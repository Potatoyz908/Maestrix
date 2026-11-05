"""
Open Maestri GTK 4 Native Application Entry Point.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.client.api import MaestriClient
from ui.window import OpenMaestriWindow


def ensure_backend_server() -> int:
    """Ensure open-maestri-server is running on localhost, returning its port."""
    run_dir = Path.home() / ".open-maestri" / "run"
    sock_path = run_dir / "agent.sock"

    # Check if existing server process is running
    server_bin = ROOT_DIR / ".build" / "release" / "open-maestri-server"
    if not server_bin.exists():
        server_bin = ROOT_DIR / ".build" / "debug" / "open-maestri-server"

    # Start server if not running
    if server_bin.exists() and not sock_path.exists():
        proc = subprocess.Popen([str(server_bin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Wait briefly for startup
        time.sleep(0.5)

    # Read port from environment or scan running processes
    return 0


class OpenMaestriApp(Gtk.Application):
    def __init__(self, port: int = 0, token: str = ""):
        super().__init__(
            application_id="org.openmaestri.desktop.native",
            flags=0
        )
        self.port = port
        self.token = token
        self.window = None

    def do_activate(self):
        if not self.window:
            client = MaestriClient(port=self.port, token=self.token)
            self.window = OpenMaestriWindow(self, client)
        self.window.present()


def main():
    parser = argparse.ArgumentParser(description="Open Maestri GTK 4 Native Linux")
    parser.add_argument("--port", type=int, default=0, help="Backend TCP port")
    parser.add_argument("--token", type=str, default="", help="Backend auth token")
    args = parser.parse_args()

    port = args.port or int(os.environ.get("MAESTRI_PORT", "0"))
    token = args.token or os.environ.get("MAESTRI_AUTH_TOKEN", "")

    app = OpenMaestriApp(port=port, token=token)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
