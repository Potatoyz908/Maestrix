"""'Ask agent' from a FileTree diff -> the active terminal (spec: docs/reference/maestri-reference-index.md §7, Diff 视图).

Flow:
    DiffView popover --ask-requested(text)--> FileTreeView --> CanvasWidget "ask-requested"(fileTree id, text)
      --> bus "ask-agent"(source id, text) --> AskAgentRouter.deliver --> bus "ask-agent-done"(source, target, ok, message)
      --> the source FileTree's DiffView.set_ask_status(message, ok)

Going through the bus keeps the canvas decoupled from the delivery: any other emitter of "ask-agent" (a sticky note, the
CLI bridge…) reaches the same terminal the same way.

Target = `CanvasWidget.active_terminal_id` (the last terminal that held the keyboard, or the one selected alone); when
there is none, the only terminal on the canvas. The text is pasted with VTE's paste_text (bracketed paste when the program
enabled it, so a multi-line quote stays one message for an agent TUI).

Enter is pressed (separately, after ENTER_DELAY_MS) ONLY when an agent is verifiably still running in the terminal:
  * `agentType` generic_shell -> never: the text is only pasted, for the user to review;
  * agent terminal -> the terminal's foreground job is checked first (`foreground_job`: tpgid of the shell's controlling
    TTY from /proc). A foreground job other than the shell = the agent TUI is alive -> paste + Enter. The shell itself in
    the foreground (the agent exited and left a plain prompt) or a state that cannot be read -> paste only, so a pasted
    diff can never be executed as shell commands by an automatic Enter (Opus-Reviewer L4).
The shell PID comes from the local VTE (`maestri_pid`) or, for a core-owned PTY, from `pid_lookup(terminal_id)` (REST
`GET /api/terminals/:id`), which runs on a worker thread together with the /proc read: nothing blocks the main loop.
"""
from __future__ import annotations

import os
import threading
import weakref
from typing import Callable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from maestri_canvas.widget import terminal_of

ENTER_DELAY_MS = 120          # TUIs treat paste+CR in one write as part of the paste
PLAIN_SHELL = "generic_shell"

# foreground_job() states
AGENT_RUNNING = "agent"       # a job other than the shell owns the terminal
SHELL_PROMPT = "shell"        # the shell itself is in the foreground: the agent exited
UNKNOWN = "unknown"           # no PID, process gone, no controlling TTY, unreadable /proc


def foreground_job(shell_pid: Optional[int], proc: str = "/proc") -> Tuple[str, str]:
    """(state, foreground command name) of the TTY whose session `shell_pid` leads.

    /proc/<pid>/stat fields after the parenthesised comm: state ppid pgrp session tty_nr tpgid ... `tpgid` is the
    foreground process group of the controlling terminal. It equals the shell's own pgrp while the shell waits at its
    prompt; it is another group while a program (the agent TUI) runs in the foreground."""
    if not shell_pid or shell_pid <= 0:
        return UNKNOWN, ""
    try:
        with open(os.path.join(proc, str(shell_pid), "stat"), "rb") as f:
            stat = f.read().decode("utf-8", "replace")
        fields = stat[stat.rindex(")") + 2:].split()
        pgrp, tty_nr, tpgid = int(fields[2]), int(fields[4]), int(fields[5])
    except (OSError, ValueError, IndexError):
        return UNKNOWN, ""
    if tty_nr == 0 or tpgid <= 0:
        return UNKNOWN, ""
    if tpgid == pgrp:
        return SHELL_PROMPT, _comm(shell_pid, proc)
    return AGENT_RUNNING, _comm(tpgid, proc)


def _comm(pid: int, proc: str) -> str:
    try:
        with open(os.path.join(proc, str(pid), "comm"), "rb") as f:
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


class AskAgentRouter:
    """One per window. The bus is process-wide, so a router answers only the asks that belong to it: those whose source
    node is on its canvas, and (for a source-less ask, e.g. from a note bridge or the CLI) only the router of the active
    window, else of the most recently created visible one. Without this, two windows would both paste the same question.

    `nodes()` returns the workspace's CanvasNode list (to name terminals and read their agentType).
    `pid_lookup(terminal_id) -> pid | None` resolves the shell of a core-owned PTY (blocking; called off the main thread).
    `probe(pid) -> (state, comm)` reads the foreground job (default: foreground_job on /proc)."""

    def __init__(self, canvas, nodes: Callable[[], list], bus,
                 pid_lookup: Optional[Callable[[str], Optional[int]]] = None,
                 probe: Callable[[Optional[int]], Tuple[str, str]] = foreground_job) -> None:
        self.canvas = canvas
        self.nodes = nodes
        self.bus = bus
        self.pid_lookup = pid_lookup
        self.probe = probe
        AskAgentRouter._live.append(weakref.ref(self))
        self.last: Optional[Tuple[str, str, bool, str]] = None
        self._handlers: List[Tuple[object, int]] = [
            (canvas, canvas.connect("ask-requested", lambda _c, sid, text: bus.emit("ask-agent", sid, text))),
            (bus, bus.connect("ask-agent", self._on_ask)),
            (bus, bus.connect("ask-agent-done", self._on_done)),
        ]

    _live: List["weakref.ref[AskAgentRouter]"] = []

    def _visible(self) -> bool:
        return bool(self._handlers) and self.canvas.get_mapped()

    def owns(self, source_id: str) -> bool:
        """Whether this router should answer an ask from `source_id`."""
        if source_id:
            return source_id.upper() in self.canvas.frames
        live = [r for r in (ref() for ref in AskAgentRouter._live) if r is not None and r._visible()]
        if not live:
            return False
        active = [r for r in live if getattr(r.canvas.get_root(), "is_active", lambda: False)()]
        return (active or live)[-1] is self

    def dispose(self) -> None:
        AskAgentRouter._live = [r for r in AskAgentRouter._live if r() is not None and r() is not self]
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
    def _shell_pid(self, content) -> Tuple[Optional[int], Optional[str]]:
        """(local shell pid, None) for a VTE-spawned shell, (None, core terminal id) for a core-owned PTY."""
        vte = terminal_of(content)
        for w in (vte, content):
            pid = getattr(w, "maestri_pid", -1)
            if isinstance(pid, int) and pid > 0:
                return pid, None
        transport = getattr(content, "transport", None)
        core_tid = getattr(transport, "terminal_id", None)
        return None, (str(core_tid) if core_tid else None)

    def deliver(self, text: str, done: Callable[[str, bool, str], None]) -> None:
        """Paste `text` into the target terminal; `done(target node id or "", ok, human message)` runs on the main loop
        once the text is in (Enter, when pressed, follows ENTER_DELAY_MS later)."""
        tid = self.target()
        if tid is None:
            several = sum(1 for i in self.canvas.frames if self._is_live_terminal(i)) > 1
            done("", False, ("Click the terminal that should answer, then ask again." if several
                             else "No terminal on the canvas to ask."))
            return
        node = self._node(tid)
        name = self._name(tid)
        if node is not None and getattr(node.content, "agent_type", PLAIN_SHELL) == PLAIN_SHELL:
            self._paste(tid, text, enter=False)
            done(tid, True, f"Pasted into {name}: review and press Enter.")
            return

        local_pid, core_tid = self._shell_pid(self.canvas.frames[tid].content)

        def check() -> None:     # worker thread: REST + /proc
            try:
                pid = local_pid if local_pid is not None else (self.pid_lookup(core_tid) if self.pid_lookup and core_tid else None)
                state, comm = self.probe(pid)
            except Exception:     # a failed lookup must never press Enter
                state, comm = UNKNOWN, ""
            GLib.idle_add(lambda: finish(state, comm) and False)

        def finish(state: str, comm: str) -> None:
            if tid.upper() not in self.canvas.frames:          # removed meanwhile
                done("", False, f"{name} was closed before the question could be sent.")
                return
            running = state == AGENT_RUNNING
            self._paste(tid, text, enter=running)
            if running:
                done(tid, True, f"Sent to {name}.")
            elif state == SHELL_PROMPT:
                done(tid, True, f"The agent in {name} is no longer running (shell prompt): pasted without Enter, "
                                f"review before running anything.")
            else:
                done(tid, True, f"Could not confirm an agent is running in {name}: pasted without Enter, review and "
                                f"press Enter.")

        threading.Thread(target=check, name="maestri-ask-agent", daemon=True).start()

    def _paste(self, tid: str, text: str, enter: bool) -> None:
        vte = terminal_of(self.canvas.frames[tid].content)
        vte.paste_text(text)
        if enter:
            def press():
                vte.feed_child(b"\r")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(ENTER_DELAY_MS, press)
        vte.grab_focus()
        self.canvas.active_terminal_id = tid

    def _on_ask(self, _bus, source_id: str, text: str) -> None:
        if not self._handlers or not self.owns(source_id):
            return

        def done(target: str, ok: bool, message: str) -> None:
            self.last = (source_id, target, ok, message)     # this router's own deliveries only
            if self._handlers:
                self.bus.emit("ask-agent-done", source_id, target, ok, message)
        self.deliver(text, done)

    def _on_done(self, _bus, source_id: str, target: str, ok: bool, message: str) -> None:
        frame = self.canvas.frames.get(source_id.upper()) if source_id else None
        diff = getattr(getattr(frame, "content", None), "diff_view", None)
        if diff is not None and hasattr(diff, "set_ask_status"):
            diff.set_ask_status(message, ok)


def install_ask_agent(window) -> AskAgentRouter:
    """Wire 'Ask agent' for MaestrixWindow (uses window.canvas_widget and window.document)."""
    from maestri_core.signals.bus import bus

    def pid_lookup(terminal_id: str) -> Optional[int]:
        meta = window.client.get_terminal(terminal_id) if getattr(window, "client", None) is not None else None
        if not meta or meta.get("running") is False:
            return None
        pid = meta.get("pid")
        return int(pid) if isinstance(pid, (int, float)) and pid > 0 else None

    router = AskAgentRouter(window.canvas_widget, lambda: window.document.payload.nodes, bus, pid_lookup=pid_lookup)
    window.connect("close-request", lambda *_: router.dispose() or False)
    window.connect("destroy", lambda *_: router.dispose())
    return router
