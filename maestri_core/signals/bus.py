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
        "floor-switched": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # (workspace id, terminal nodes persisted in it): from the core's nodeAdded / nodeRemoved / workspaceSaved events
        "terminal-count-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),

        # Node events
        "node-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "node-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),  # server snapshot; never schedules a save
        "node-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "node-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "node-moved": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float)),
        "node-resized": (GObject.SignalFlags.RUN_FIRST, None, (str, float, float, float, float)),
        "node-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Connection events
        "connection-created": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "connection-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # (connection id, status) from the core's connectionChanged {status}: presentation only, NOT a document change
        "connection-status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "connection-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Viewport events
        "viewport-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float, float)),

        # Agent collaboration: "Ask agent" from a FileTree diff -> (source node id, question + quoted diff)
        "ask-agent": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # Delivery result of an ask-agent request -> (source node id, target terminal node id or "", ok, message)
        "ask-agent-done": (GObject.SignalFlags.RUN_FIRST, None, (str, str, bool, str)),

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
