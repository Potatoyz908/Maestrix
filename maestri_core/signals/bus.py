"""
GObject signal bus for Open Maestri GTK 4 Native.
Provides decoupled event communication across UI, Canvas, and Backend.
Ensures thread-safe emission on the GTK main thread via GLib.idle_add.
"""

import gi
gi.require_version("GObject", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GObject, GLib


class SignalBus(GObject.Object):
    """
    Central event dispatcher for Open Maestri Native GTK.
    """
    __gsignals__ = {
        # Workspace lifecycle
        "workspace-loaded": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "workspace-saved": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "workspace-switched": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Node events
        "node-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "node-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "node-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "node-moved": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float)),
        "node-resized": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float)),
        "node-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Connection events
        "connection-created": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "connection-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "connection-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Viewport events
        "viewport-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float, float)),

        # Backend status
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
    }

    _instance = None

    @classmethod
    def get_default(cls) -> "SignalBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def emit_main_thread(self, signal_name: str, *args):
        """Emit signal safely on GTK Main Thread."""
        def _cb():
            self.emit(signal_name, *args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(_cb)


# Global shared bus
bus = SignalBus.get_default()
