"""
Maestrix GTK 4 Native Application Entry Point.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.client.api import MaestriClient
from ui.window import MaestrixWindow


APP_ID = "org.maestrix.desktop"   # also the .desktop StartupWMClass (Wayland app_id / X11 WM_CLASS)


class MaestrixApp(Gtk.Application):
    def __init__(self, port: int = 0, token: str = ""):
        super().__init__(
            application_id=APP_ID,
            flags=0
        )
        self.port = port
        self.token = token
        self.window = None

    def do_activate(self):
        if not self.window:
            Gtk.Window.set_default_icon_name("maestrix")
            client = MaestriClient(port=self.port, token=self.token)
            self.window = MaestrixWindow(self, client)
            self.window.set_icon_name("maestrix")
        self.window.present()


def main():
    parser = argparse.ArgumentParser(description="Maestrix GTK 4 Native Linux")
    parser.add_argument("--port", type=int, default=0, help="Backend TCP port")
    parser.add_argument("--token", type=str, default="", help="Backend auth token")
    # maestri://... (x-scheme-handler/maestri from the .desktop %U) and other positionals are accepted and ignored
    args, _ignored = parser.parse_known_args()
    GLib.set_prgname(APP_ID)   # X11 WM_CLASS matches StartupWMClass, so the dock groups the window with the launcher

    # Harmonize MAESTRI_SERVER_PORT and MAESTRI_PORT
    port_env = os.environ.get("MAESTRI_SERVER_PORT") or os.environ.get("MAESTRI_PORT") or "0"
    port = args.port or int(port_env)
    token = args.token or os.environ.get("MAESTRI_AUTH_TOKEN", "")

    app = MaestrixApp(port=port, token=token)
    return app.run(sys.argv[:1])   # GApplication without HANDLES_OPEN would refuse URI arguments


if __name__ == "__main__":
    sys.exit(main())
