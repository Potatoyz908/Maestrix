"""'Ask agent' from a FileTree diff -> the active terminal (spec: docs/reference/maestri-reference-index.md §7, Diff 视图).

Flow:
    DiffView popover --ask-requested(text)--> FileTreeView --> CanvasWidget "ask-requested"(fileTree id, text)
      --> bus "ask-agent"(source id, text) --> AskAgentRouter.deliver --> bus "ask-agent-done"(source, target, ok, message)
      --> the source FileTree's DiffView.set_ask_status(message, ok)

Going through the bus keeps the canvas decoupled from the delivery: any other emitter of "ask-agent" (a sticky note, the
CLI bridge…) reaches the same terminal the same way.

Target = `CanvasWidget.active_terminal_id` (the last terminal that held the keyboard, or the one selected alone); when
there is none, the only terminal on the canvas. The text is pasted with VTE's paste_text (bracketed paste when the program
enabled it, so a multi-line quote stays one message for an agent TUI) and Enter is pressed separately, unless the terminal
is a plain shell (`agentType` generic_shell): there the text is only pasted, for the user to review, since a shell would run
each line as a command.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from maestri_canvas.widget import terminal_of

ENTER_DELAY_MS = 120          # TUIs treat paste+CR in one write as part of the paste
PLAIN_SHELL = "generic_shell"


class AskAgentRouter:
    """`nodes()` returns the workspace's CanvasNode list (to name terminals and read their agentType)."""

    def __init__(self, canvas, nodes: Callable[[], list], bus) -> None:
        self.canvas = canvas
        self.nodes = nodes
        self.bus = bus
        self.last: Optional[Tuple[str, str, bool, str]] = None
        self._handlers: List[Tuple[object, int]] = [
            (canvas, canvas.connect("ask-requested", lambda _c, sid, text: bus.emit("ask-agent", sid, text))),
            (bus, bus.connect("ask-agent", self._on_ask)),
            (bus, bus.connect("ask-agent-done", self._on_done)),
        ]

    def dispose(self) -> None:
        for obj, hid in self._handlers:
            if obj.handler_is_connected(hid):
                obj.disconnect(hid)
        self._handlers.clear()

    # ---- target -----------------------------------------------------------------------------------
    def _node(self, node_id: str):
        nid = node_id.upper()
        return next((n for n in self.nodes() if n.id.upper() == nid), None)

    def _is_live_terminal(self, node_id: Optional[str]) -> bool:
        frame = self.canvas.frames.get(node_id.upper()) if node_id else None
        return frame is not None and terminal_of(frame.content) is not None

    def target(self) -> Optional[str]:
        active = self.canvas.active_terminal_id
        if self._is_live_terminal(active):
            return active.upper()
        live = [i for i in self.canvas.frames if self._is_live_terminal(i)]
        return live[0] if len(live) == 1 else None

    def _name(self, node_id: str) -> str:
        n = self._node(node_id)
        return (getattr(n.content, "name", "") or n.title or "terminal") if n is not None else "terminal"

    # ---- delivery ---------------------------------------------------------------------------------
    def deliver(self, text: str) -> Tuple[str, bool, str]:
        """Paste `text` into the target terminal. Returns (target node id or "", ok, human message)."""
        tid = self.target()
        if tid is None:
            several = sum(1 for i in self.canvas.frames if self._is_live_terminal(i)) > 1
            return "", False, ("Click the terminal that should answer, then ask again." if several
                               else "No terminal on the canvas to ask.")
        vte = terminal_of(self.canvas.frames[tid].content)
        node = self._node(tid)
        plain_shell = node is not None and getattr(node.content, "agent_type", PLAIN_SHELL) == PLAIN_SHELL
        vte.paste_text(text)
        if not plain_shell:
            def enter():
                vte.feed_child(b"\r")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(ENTER_DELAY_MS, enter)
        vte.grab_focus()
        self.canvas.active_terminal_id = tid
        name = self._name(tid)
        return tid, True, (f"Pasted into {name}: review and press Enter." if plain_shell else f"Sent to {name}.")

    def _on_ask(self, _bus, source_id: str, text: str) -> None:
        if not self._handlers:
            return
        target, ok, message = self.deliver(text)
        self.bus.emit("ask-agent-done", source_id, target, ok, message)

    def _on_done(self, _bus, source_id: str, target: str, ok: bool, message: str) -> None:
        self.last = (source_id, target, ok, message)
        frame = self.canvas.frames.get(source_id.upper()) if source_id else None
        diff = getattr(getattr(frame, "content", None), "diff_view", None)
        if diff is not None and hasattr(diff, "set_ask_status"):
            diff.set_ask_status(message, ok)


def install_ask_agent(window) -> AskAgentRouter:
    """Wire 'Ask agent' for OpenMaestriWindow (uses window.canvas_widget and window.document)."""
    from maestri_core.signals.bus import bus

    router = AskAgentRouter(window.canvas_widget, lambda: window.document.payload.nodes, bus)
    window.connect("close-request", lambda *_: router.dispose() or False)
    window.connect("destroy", lambda *_: router.dispose())
    return router
