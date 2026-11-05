"""Routines UI for Open Maestri GTK 4 (spec: docs/linux-gtk-sprint4-spec.md §2; reference docs/reference/official/11-routines.md).

File → Routines… (`win.routines`, Ctrl+Shift+R) opens `RoutinesWindow` (non-modal): one row per routine with a live
indicator (● pulsing while a run is in progress), name, target and interval, a Switch (pause / resume), Run now, Edit and
Delete. "New Routine" / Edit open `RoutineEditor`: name, prompt (`&&` on its own line chains prompts), interval
(SpinButton + min/h DropDown, minimum 1 min) and the target terminal (DropDown of the workspace's terminals).

State lives in the core (`/api/routines`, through `RoutinesClient` on worker threads); `RoutinesController` mirrors it into
a `RoutineScheduler` ticked by the GLib main loop. Prompts reach the target terminal through `TerminalBridge`: it types the
text into the node's terminal (core PTY transport or local VTE) and presses Enter, and reports the agent idle once the
terminal has produced no output for IDLE_QUIET_SECONDS.

`install_routines(window)` is the only hook `window.py` needs (plus the menu entry).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from maestri_core.routines import (MIN_INTERVAL_SECONDS, GLibDriver, Routine, RoutineScheduler, RoutinesClient,
                                   describe_interval, parse_prompts)

IDLE_QUIET_SECONDS = 2.0
ENTER_DELAY_MS = 120      # TUIs (Claude Code, Codex…) treat text+CR in one write as a paste: press Enter separately

CSS = """
.routine-dot { color: alpha(currentColor, 0.35); font-size: 14px; }
.routine-dot.active { color: #23d18b; }
.routine-dot.running { color: #23d18b; animation: routine-pulse 1s ease-in-out infinite alternate; }
@keyframes routine-pulse { from { opacity: 1; } to { opacity: 0.25; } }
.routine-row { padding: 6px 8px; }
"""
_css_installed = False


def _install_css() -> None:
    global _css_installed
    display = Gdk.Display.get_default()
    if _css_installed or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


# ---------------------------------------------------------------------------------------------------------------------
# terminals
# ---------------------------------------------------------------------------------------------------------------------
TerminalInfo = Tuple[str, str, str]     # (terminal_id, display name, canvas node id)


class TerminalBridge:
    """Delivers routine prompts to canvas terminals. `terminals()` lists (terminal_id, name, node_id) of the workspace;
    `content_for(node_id)` returns the node's content widget (or None)."""

    def __init__(self, terminals: Callable[[], List[TerminalInfo]], content_for: Callable[[str], Optional[Gtk.Widget]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.terminals = terminals
        self.content_for = content_for
        self.clock = clock
        self._last_output: Dict[str, float] = {}
        self._watched: Dict[str, object] = {}

    def _content(self, terminal_id: str) -> Optional[Gtk.Widget]:
        tid = terminal_id.upper()
        for t_id, _name, node_id in self.terminals():
            if t_id.upper() == tid or node_id.upper() == tid:
                return self.content_for(node_id)
        return None

    @staticmethod
    def _vte(content) -> Optional[Gtk.Widget]:
        if content is None:
            return None
        if hasattr(content, "feed_child") and hasattr(content, "get_text_range_format"):
            return content
        term = getattr(content, "term", None)
        return term if term is not None and hasattr(term, "feed_child") else None

    def _write_fn(self, content) -> Optional[Callable[[str], None]]:
        transport = getattr(content, "transport", None)
        if transport is not None and hasattr(transport, "write"):
            return transport.write                       # core-owned PTY (VteTerminalView)
        vte = self._vte(content)
        if vte is not None:
            return lambda s: vte.feed_child(s.encode("utf-8"))   # local shell
        return None

    def _watch(self, terminal_id: str, content) -> None:
        vte = self._vte(content)
        key = terminal_id.upper()
        if vte is None or self._watched.get(key) is vte:
            return
        self._watched[key] = vte
        vte.connect("contents-changed", lambda *_: self._last_output.__setitem__(key, self.clock()))

    def send(self, terminal_id: str, text: str) -> bool:
        content = self._content(terminal_id)
        write = self._write_fn(content)
        if write is None:
            return False
        self._watch(terminal_id, content)
        self._last_output[terminal_id.upper()] = self.clock()   # busy from now until output settles
        write(text)

        def enter():
            write("\r")
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(ENTER_DELAY_MS, enter)
        return True

    def is_idle(self, terminal_id: str) -> bool:
        last = self._last_output.get(terminal_id.upper())
        return last is None or self.clock() - last >= IDLE_QUIET_SECONDS


# ---------------------------------------------------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------------------------------------------------
class RoutinesController:
    """REST <-> scheduler <-> UI. All public methods are called on the main thread; REST runs on worker threads and
    results come back through GLib.idle_add. `listeners` are called with no argument after every change."""

    def __init__(self, store: Optional[RoutinesClient], bridge: TerminalBridge, start_driver: bool = True) -> None:
        self.store = store
        self.bridge = bridge
        self.routines: List[Routine] = []
        self.error: str = ""
        self.listeners: List[Callable[[], None]] = []
        self.events: List[Tuple[str, str, dict]] = []
        self.scheduler = RoutineScheduler(bridge.send, bridge.is_idle, self._on_event)
        self.driver = GLibDriver(self.scheduler) if start_driver else None

    # ---- REST plumbing --------------------------------------------------------------------------------------------
    def _async(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        if self.store is None:
            self.error = "Core not connected: routines are unavailable offline."
            self._changed()
            return

        def run():
            try:
                result, err = work(), ""
            except Exception as exc:   # surfaced in the window, never raised into GTK
                result, err = None, str(exc) or exc.__class__.__name__

            def apply():
                self.error = err
                if not err:
                    done(result)
                self._changed()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(apply)
        threading.Thread(target=run, name="maestri-routines", daemon=True).start()

    def _set(self, routines: List[Routine]) -> None:
        self.routines = sorted(routines, key=lambda r: r.created_at)
        self.scheduler.set_routines(self.routines)

    def _replace(self, old_id: str, r: Routine) -> None:
        self._set([x for x in self.routines if x.id not in (old_id, r.id)] + [r])

    def _changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    # ---- public API -----------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self._async(self.store.list if self.store else (lambda: []), self._set)

    def save(self, r: Routine, is_new: bool) -> None:
        r.validate()
        if is_new:
            self._async(lambda: self.store.create(r), lambda got: self._replace(r.id, got))
        else:
            self._async(lambda: self.store.update(r), lambda got: self._replace(r.id, got))

    def set_active(self, routine_id: str, active: bool) -> None:
        (self.scheduler.resume if active else self.scheduler.pause)(routine_id)   # immediate, REST confirms
        self._async(lambda: self.store.set_active(routine_id, active), lambda got: self._replace(routine_id, got))

    def delete(self, routine_id: str) -> None:
        self.scheduler.remove(routine_id)
        self._async(lambda: self.store.delete(routine_id),
                    lambda _r: self._set([x for x in self.routines if x.id != routine_id]))

    def run_now(self, routine_id: str) -> bool:
        ok = self.scheduler.run_now(routine_id)
        self._changed()
        return ok

    def is_running(self, routine_id: str) -> bool:
        return self.scheduler.is_running(routine_id)

    def stop(self) -> None:
        if self.driver is not None:
            self.driver.stop()

    def _on_event(self, kind: str, routine_id: str, info: dict) -> None:
        self.events.append((kind, routine_id, info))
        del self.events[:-200]
        if kind == "routinePaused":          # target vanished: persist the pause in the core
            self._async(lambda: self.store.set_active(routine_id, False), lambda got: self._replace(routine_id, got))
        if kind in ("routineStarted", "routineFinished", "routineFailed", "routinePaused"):
            self._changed()


# ---------------------------------------------------------------------------------------------------------------------
# editor
# ---------------------------------------------------------------------------------------------------------------------
UNITS = (("minutes", 60), ("hours", 3600))


class RoutineEditor(Gtk.Window):
    def __init__(self, parent: Optional[Gtk.Window], terminals: List[TerminalInfo], routine: Optional[Routine],
                 on_save: Callable[[Routine, bool], None]) -> None:
        super().__init__(title="Edit Routine" if routine else "New Routine", modal=True, transient_for=parent,
                         default_width=460, default_height=420)
        self.routine = routine
        self.terminals = terminals
        self.on_save = on_save

        grid = Gtk.Grid(row_spacing=8, column_spacing=10, margin_start=14, margin_end=14, margin_top=14, margin_bottom=14)
        self.name_entry = Gtk.Entry(hexpand=True, placeholder_text="e.g. Continuous testing")
        self.prompt_buffer = Gtk.TextBuffer()
        prompt_view = Gtk.TextView(buffer=self.prompt_buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
                                   top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        prompt_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_height=140)
        prompt_scroll.set_child(prompt_view)
        prompt_frame = Gtk.Frame()
        prompt_frame.set_child(prompt_scroll)
        hint = Gtk.Label(xalign=0, wrap=True, label="Chain prompts with && on its own line; each waits for the agent to finish.")
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")

        self.interval_spin = Gtk.SpinButton.new_with_range(1, 999, 1)
        self.unit_dropdown = Gtk.DropDown.new_from_strings([u for u, _ in UNITS])
        interval_box = Gtk.Box(spacing=6)
        interval_box.append(Gtk.Label(label="every"))
        interval_box.append(self.interval_spin)
        interval_box.append(self.unit_dropdown)

        names = [name for _tid, name, _nid in terminals] or ["(no terminals in this workspace)"]
        self.target_dropdown = Gtk.DropDown.new_from_strings(names)
        self.target_dropdown.set_sensitive(bool(terminals))

        self.error_label = Gtk.Label(xalign=0, wrap=True)
        self.error_label.add_css_class("error")
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda _b: self._save())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons.append(cancel)
        buttons.append(self.save_button)

        rows = (("Name", self.name_entry), ("Prompt", prompt_frame), (None, hint), ("Interval", interval_box),
                ("Target agent", self.target_dropdown))
        for i, (label, widget) in enumerate(rows):
            if label:
                grid.attach(Gtk.Label(label=label, xalign=1, yalign=0.0 if label == "Prompt" else 0.5), 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)
        grid.attach(self.error_label, 0, len(rows), 2, 1)
        grid.attach(buttons, 0, len(rows) + 1, 2, 1)
        self.set_child(grid)

        if routine is not None:
            self.name_entry.set_text(routine.name)
            self.prompt_buffer.set_text(routine.prompt_text, -1)
            secs = int(routine.interval_seconds)
            if secs % 3600 == 0:
                self.unit_dropdown.set_selected(1)
                self.interval_spin.set_value(secs // 3600)
            else:
                self.interval_spin.set_value(max(1, round(secs / 60)))
            for i, (tid, _n, nid) in enumerate(terminals):
                if routine.target_terminal_id.upper() in (tid.upper(), nid.upper()):
                    self.target_dropdown.set_selected(i)
        else:
            self.interval_spin.set_value(5)

        for sig_src, sig in ((self.name_entry, "changed"), (self.prompt_buffer, "changed")):
            sig_src.connect(sig, lambda *_: self._validate())
        self._validate()

    def prompt_text(self) -> str:
        b = self.prompt_buffer
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def build(self) -> Routine:
        seconds = float(self.interval_spin.get_value_as_int() * UNITS[self.unit_dropdown.get_selected()][1])
        idx = self.target_dropdown.get_selected()
        tid = self.terminals[idx][0] if self.terminals and idx < len(self.terminals) else ""
        prompts = parse_prompts(self.prompt_text())
        if self.routine is None:
            r = Routine.create(self.name_entry.get_text().strip(), self.prompt_text(), seconds, tid)
        else:
            r = Routine(name=self.name_entry.get_text().strip(), prompts=prompts, interval_seconds=seconds,
                        target_terminal_id=tid.upper(), is_active=self.routine.is_active, id=self.routine.id,
                        created_at=self.routine.created_at)
        return r

    def _validate(self) -> bool:
        try:
            self.build().validate()
            msg = ""
        except ValueError as exc:
            msg = str(exc)
        if not self.terminals:
            msg = "Add a terminal to the canvas first."
        self.error_label.set_label(msg if (self.name_entry.get_text() or self.prompt_text()) else "")
        self.save_button.set_sensitive(not msg)
        return not msg

    def _save(self) -> None:
        if not self._validate():
            return
        self.on_save(self.build(), self.routine is None)
        self.close()


# ---------------------------------------------------------------------------------------------------------------------
# list window
# ---------------------------------------------------------------------------------------------------------------------
class RoutinesWindow(Gtk.Window):
    def __init__(self, parent: Optional[Gtk.Window], controller: RoutinesController,
                 terminals: Callable[[], List[TerminalInfo]]) -> None:
        super().__init__(title="Routines", transient_for=parent, default_width=560, default_height=420)
        _install_css()
        self.controller = controller
        self.terminals = terminals
        self.editor: Optional[RoutineEditor] = None

        header = Gtk.HeaderBar()
        self.new_button = Gtk.Button(label="New Routine", icon_name="list-add-symbolic", tooltip_text="New Routine")
        self.new_button.connect("clicked", lambda _b: self.open_editor(None))
        header.pack_start(self.new_button)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload from core")
        refresh.connect("clicked", lambda _b: controller.refresh())
        header.pack_end(refresh)
        self.set_titlebar(header)

        self.error_label = Gtk.Label(xalign=0, wrap=True, visible=False, margin_start=10, margin_end=10, margin_top=6)
        self.error_label.add_css_class("error")
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.empty_label = Gtk.Label(label="No routines yet. Schedule a prompt for an agent with New Routine.",
                                     wrap=True, margin_top=40, margin_start=20, margin_end=20)
        self.empty_label.add_css_class("dim-label")
        self.listbox.set_placeholder(self.empty_label)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.listbox)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.error_label)
        box.append(scroll)
        self.set_child(box)

        self._listener = self.rebuild
        controller.listeners.append(self._listener)
        self.connect("close-request", self._on_close)
        self.rebuild()
        controller.refresh()

    def _on_close(self, *_a) -> bool:
        if self._listener in self.controller.listeners:
            self.controller.listeners.remove(self._listener)
        return False

    def _terminal_name(self, tid: str) -> str:
        for t_id, name, nid in self.terminals():
            if tid.upper() in (t_id.upper(), nid.upper()):
                return name
        return "missing terminal"

    def rows(self) -> List[Gtk.Widget]:
        out, child = [], self.listbox.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ListBoxRow):
                out.append(child)
            child = child.get_next_sibling()
        return out

    def rebuild(self) -> None:
        c = self.controller
        self.error_label.set_label(c.error)
        self.error_label.set_visible(bool(c.error))
        self.listbox.remove_all()
        for r in c.routines:
            self.listbox.append(self._row(r))

    def _row(self, r: Routine) -> Gtk.Widget:
        c = self.controller
        row = Gtk.ListBoxRow(activatable=False)
        row.routine_id = r.id
        box = Gtk.Box(spacing=10)
        box.add_css_class("routine-row")
        dot = Gtk.Label(label="●")
        dot.add_css_class("routine-dot")
        if c.is_running(r.id):
            dot.add_css_class("running")
            dot.set_tooltip_text("Running")
        elif r.is_active:
            dot.add_css_class("active")
            dot.set_tooltip_text("Active")
        else:
            dot.set_tooltip_text("Paused")
        row.dot = dot
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = Gtk.Label(label=r.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("heading")
        steps = f" · {len(r.prompts)} prompts" if len(r.prompts) > 1 else ""
        sub = Gtk.Label(label=f"→ {self._terminal_name(r.target_terminal_id)} · {describe_interval(r.interval_seconds)}{steps}",
                        xalign=0, ellipsize=Pango.EllipsizeMode.END)
        sub.add_css_class("dim-label")
        sub.set_tooltip_text(r.prompt_text)
        texts.append(title)
        texts.append(sub)
        switch = Gtk.Switch(active=r.is_active, valign=Gtk.Align.CENTER, tooltip_text="Pause / resume")
        switch.connect("notify::active", lambda s, _p, rid=r.id: c.set_active(rid, s.get_active()))
        row.switch = switch
        run = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Run now", valign=Gtk.Align.CENTER)
        run.add_css_class("flat")
        run.connect("clicked", lambda _b, rid=r.id: c.run_now(rid))
        edit = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit", valign=Gtk.Align.CENTER)
        edit.add_css_class("flat")
        edit.connect("clicked", lambda _b, rr=r: self.open_editor(rr))
        delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete", valign=Gtk.Align.CENTER)
        delete.add_css_class("flat")
        delete.connect("clicked", lambda _b, rr=r: self.confirm_delete(rr))
        for w in (dot, texts, switch, run, edit, delete):
            box.append(w)
        row.set_child(box)
        return row

    def open_editor(self, routine: Optional[Routine]) -> RoutineEditor:
        self.editor = RoutineEditor(self, self.terminals(), routine, self.controller.save)
        self.editor.present()
        return self.editor

    def confirm_delete(self, r: Routine) -> None:
        dialog = Gtk.AlertDialog(message=f"Delete routine “{r.name}”?", detail="This cannot be undone.",
                                 buttons=["Cancel", "Delete"], cancel_button=0, default_button=0)

        def done(d, res):
            try:
                if d.choose_finish(res) == 1:
                    self.controller.delete(r.id)
            except GLib.Error:
                pass
        dialog.choose(self, None, done)


# ---------------------------------------------------------------------------------------------------------------------
# window hook
# ---------------------------------------------------------------------------------------------------------------------
def install_routines(window) -> RoutinesController:
    """Wire routines into OpenMaestriWindow: scheduler + `win.routines` / `win.new-routine` actions (Ctrl+Shift+R).
    Uses window.client, window.document and window.canvas_widget; stops the scheduler when the window closes."""

    def terminals() -> List[TerminalInfo]:
        out = []
        for n in window.document.payload.nodes:
            if n.node_type == "terminal":
                tid = (getattr(n.content, "terminal_id", "") or n.id).upper()
                out.append((tid, getattr(n.content, "name", "") or n.title or "Terminal", n.id))
        return out

    def content_for(node_id: str):
        frame = window.canvas_widget.frames.get(node_id.upper())
        return getattr(frame, "content", None)

    store = RoutinesClient(window.client) if not getattr(window, "auth_failed", False) else None
    controller = RoutinesController(store, TerminalBridge(terminals, content_for))
    state = {"win": None}

    def show(new: bool = False) -> None:
        if state["win"] is None:
            state["win"] = RoutinesWindow(window, controller, terminals)
            state["win"].connect("close-request", lambda *_: state.__setitem__("win", None) or False)
        state["win"].present()
        if new:
            state["win"].open_editor(None)

    for name, fn in (("routines", lambda: show()), ("new-routine", lambda: show(True))):
        act = Gio.SimpleAction.new(name, None)
        act.connect("activate", lambda _a, _p, f=fn: f())
        window.add_action(act)
    app = window.get_application()
    if app is not None:
        app.set_accels_for_action("win.routines", ["<Control><Shift>r"])
    window.connect("close-request", lambda *_: controller.stop() or False)
    if store is not None:
        controller.refresh()     # start the active routines without waiting for the window to open
    return controller
