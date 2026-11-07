"""Floors UI for Maestrix GTK 4 (spec: docs/linux-gtk-sprint5-spec.md §2 T2.3-T2.6, base sprint4 §1; reference
docs/reference/official/09-floors.md).

A floor is a git worktree of the workspace's repository with its own branch. The core owns them (`/api/floors`); this module is
only a client:

* `FloorsController`: floor list, per-floor status (dirty ●, +ins −del), the active floor (None = Ground) and what new
  terminals inherit from it (`terminal_cwd`, `terminal_env`). Reloads on `workspace-loaded` and on the core's `/ws/events`
  floor events. Every HTTP call runs on a worker thread; results come back through GLib.idle_add. The server's floor list is
  mirrored into `window.document.payload.floors` on the main thread, so a sync snapshot never resends a stale list (R1).
* `FloorButton`: the Floor capsule of the bottom-right controls (floating_bars.py): floor icon + "<active floor | Ground>".
  Its popover lists Ground + the floors (click = switch, close icon = delete), "Novo floor…" and "Land…" (only when a
  floor is active). Outline SVG icons only (maestri-floor-*-symbolic), no emoji.
* `NewFloorDialog`, `LandFloorDialog`, `DeleteFloorDialog`: T2.4 / T2.5 / T2.6.

`install_floors(window)` is the hook `window.py` needs; `_create_node` / `_add_node_to_canvas` ask `terminal_cwd` / `terminal_env`.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from maestri_core.gitops import GitOps, _valid_ref
from maestri_core.signals.bus import bus

Floor = Dict[str, Any]   # floorJSON from the core: id, name, branchName, worktreePath, hooks, createdAt

FLOOR_EVENTS = frozenset({"floorCreated", "floorRemoved", "floorChanged", "floorUpdated", "floorLanded"})

CSS = """
.floor-popover { padding: 2px; }
.floor-popover-title { font-weight: 600; font-size: 11px; letter-spacing: 0.04em; opacity: 0.6; margin: 4px 10px 0 10px; }
.floor-row { padding: 6px 8px; border-radius: 6px; }
.floor-row-icon { opacity: 0.75; }
.floor-row .floor-check { color: #0a84ff; }
.floor-row-delete { min-width: 22px; min-height: 22px; padding: 0; border-radius: 11px; opacity: 0.55; }
.floor-row-delete:hover { opacity: 1; background-color: alpha(#ff453a, 0.18); color: #ff453a; }
.floor-action { padding: 5px 10px; border-radius: 6px; }
.floor-dot { min-width: 7px; min-height: 7px; border-radius: 4px; background-color: #e5c07b; }
.floor-row.active { background-color: alpha(#3b82f6, 0.18); border-radius: 6px; }
.floor-branch { font-family: monospace; font-size: 11px; opacity: 0.7; }
.floor-badge { font-family: monospace; font-size: 11px; font-feature-settings: "tnum"; opacity: 0.8; }
.floor-dirty { color: #e5c07b; }
.floor-error { color: #f14c4c; }
.floor-ok { color: #23d18b; }
.floor-conflict { color: #f14c4c; font-family: monospace; }
.floor-plane { opacity: 0.85; }
.floor-files-box { margin-top: 2px; }
.floor-file-row { padding: 1px 4px; border-radius: 4px; }
.floor-file-status { font-family: monospace; font-size: 10px; font-weight: 600; min-width: 14px; color: #e5c07b; }
.floor-file-path { font-family: monospace; font-size: 11px; opacity: 0.8; }
.floor-hook-entry { font-family: monospace; }
.floor-hook-btn { min-width: 24px; min-height: 24px; padding: 0; border-radius: 6px; opacity: 0.8; }
.floor-hook-btn:hover { opacity: 1; background-color: alpha(#3b82f6, 0.18); }
.floor-hook-section-title { font-weight: 600; font-size: 13px; }
.floor-hook-section-sub { font-size: 11px; opacity: 0.65; }
.floor-hooks-button { padding: 2px 6px; border-radius: 6px; }
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
# pure helpers (unit-tested without a display)
# ---------------------------------------------------------------------------------------------------------------------
def slugify(name: str) -> str:
    """Same rule as FloorManager.slugify (Swift): lowercase [a-z0-9] runs joined by single dashes; "floor" when empty."""
    return "-".join(re.findall(r"[a-z0-9]+", name.lower())) or "floor"


def default_branch(name: str) -> str:
    return f"floor/{slugify(name)}"


def branch_error(branch: str) -> Optional[str]:
    """None when `branch` is a valid local branch name (gitops._valid_ref, the subset of `git check-ref-format --branch`)."""
    b = branch.strip()
    if not b:
        return "Enter a branch name"
    if b == "@" or not _valid_ref(b):
        return "Invalid branch name (no spaces, '..', ~ ^ : ? * [ \\ or leading '.' in a component)"
    return None


def badge_text(status: Optional[Dict[str, Any]]) -> str:
    """"+ins −del" (empty when unknown or no difference)."""
    if not status:
        return ""
    ins, dels = int(status.get("insertions") or 0), int(status.get("deletions") or 0)
    return f"+{ins} −{dels}" if ins or dels else ""


def format_changed_files(status: Optional[Dict[str, Any]]) -> List[tuple[str, str]]:
    """Normalized (status_code, path) pairs from `/api/floors/:id/status` `files`."""
    if not status:
        return []
    out: List[tuple[str, str]] = []
    for item in status.get("files") or []:
        if isinstance(item, dict):
            code = str(item.get("status") or "M").strip() or "M"
            path = str(item.get("path") or "").strip()
        else:
            parts = str(item).strip().split(None, 1)
            code, path = (parts[0], parts[1]) if len(parts) == 2 and len(parts[0]) <= 2 else ("M", str(item).strip())
        if path:
            out.append((code, path))
    return out


def normalize_hooks(hooks: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Canonical FloorHooks dict: {setup: [str], run: [str], teardown: [str], autoRunSetup: bool}."""
    h = hooks if isinstance(hooks, dict) else {}
    return {
        "setup": [str(c) for c in (h.get("setup") or [])],
        "run": [str(c) for c in (h.get("run") or [])],
        "teardown": [str(c) for c in (h.get("teardown") or [])],
        "autoRunSetup": bool(h.get("autoRunSetup", False)),
    }


def has_configured_hooks(floor: Optional[Floor]) -> bool:
    """True when any phase (setup, run, teardown) of `floor` has at least one non-empty command."""
    if not floor:
        return False
    h = normalize_hooks(floor.get("hooks"))
    return any(cmd.strip() for phase in ("setup", "run", "teardown") for cmd in h[phase])


def describe_preview(p: Dict[str, Any]) -> str:
    """Summary of a land-preview response (the conflicts list is rendered separately)."""
    floor_branch, target = p.get("floorBranch", "?"), p.get("target", "?")
    if p.get("sameBranch"):
        return (f"{floor_branch} is the floor's own branch: commits are already in the repository (the worktree shares "
                f"refs with Ground). Choose another target branch to merge.")
    lines = []
    ahead = int(p.get("ahead") or 0)
    if ahead == 0:
        lines.append(f"Nothing to land: {floor_branch} has no commits ahead of {target}.")
    else:
        kind = "fast-forward" if p.get("fastForward") else "merge commit"
        lines.append(f"{ahead} commit{'s' if ahead != 1 else ''} → {target} ({kind})")
    ins, dels = int(p.get("insertions") or 0), int(status_val if (status_val := p.get("deletions")) else 0)
    if ins or dels:
        lines.append(f"+{ins} −{dels}")
    elif p.get("stat"):
        lines.append(str(p["stat"]))
    return "\n".join(lines)


def blocking_reason(p: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why Merge is disabled, in the spec's words (T2.5); None when the core says canLand."""
    if not p:
        return None
    if p.get("dirty"):
        files = p.get("dirtyFiles") or []
        shown = ", ".join(files[:5]) + (f" (+{len(files) - 5})" if len(files) > 5 else "")
        return "Commit changes before landing" + (f": {shown}" if shown else "")
    if p.get("conflicts"):
        return "Resolve conflicts in terminal/IDE"
    if p.get("targetCheckedOut"):
        return f"{p.get('target')} is checked out in Ground: check out another branch or use the terminal"
    return None


def delete_warning(floor: Floor, status: Optional[Dict[str, Any]] = None) -> str:
    """DeleteFloorDialog body (L3): what is always lost, and the explicit warning that "Delete branch" discards the commits
    that were never landed (with their count when the floor status is known)."""
    branch = floor.get("branchName", "")
    ahead = int((status or {}).get("ahead") or 0)
    unlanded = (f"{ahead} unlanded commit{'s' if ahead != 1 else ''}" if ahead
                else "all commits that have not been landed yet")
    return (f"The directory {floor.get('worktreePath', '')} is always removed, including uncommitted changes.\n\n"
            f"“Delete branch” also deletes branch {branch} and DISCARDS {unlanded}: they cannot be recovered "
            f"from this interface. Choose “Keep branch” to return to those commits later.")


def error_text(exc: BaseException) -> str:
    """The core's message (`error: ...` body) when there is one, else the exception text."""
    body = getattr(exc, "response_body", None)
    if body:
        return body.removeprefix("error: ").strip()
    return str(exc) or exc.__class__.__name__


def floor_env(floor: Floor, root: str) -> Dict[str, str]:
    """Hook/terminal environment of a floor: MAESTRI_* plus the OMAESTRI_* aliases (sprint 4 §0.5)."""
    values = {
        "FLOOR_NAME": str(floor.get("name", "")),
        "BRANCH_NAME": str(floor.get("branchName", "")),
        "FLOOR_PATH": str(floor.get("worktreePath", "")),
        "ROOT_PATH": root,
        "PROJECT_NAME": os.path.basename(root.rstrip("/")) if root else "",
    }
    return {f"{prefix}_{k}": v for prefix in ("MAESTRI", "OMAESTRI") for k, v in values.items()}


def _inside(path: str, root: str) -> bool:
    if not path or not root:
        return False
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root + os.sep)


# ---------------------------------------------------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------------------------------------------------
class FloorsController:
    """Floor list, statuses and the active floor. `listeners` run (main thread) after every change; `on_status(text, ok)`
    reports the outcome of the last action (the button shows it as a toast)."""

    def __init__(self, window) -> None:
        self.window = window
        self.floors: List[Floor] = []
        self.statuses: Dict[str, Dict[str, Any]] = {}
        self.active_floor_id: Optional[str] = None
        self.listeners: List[Callable[[], None]] = []
        self.on_status: Optional[Callable[[str, bool], None]] = None
        self._generation = 0
        self._events = None
        self._events_ws: Optional[str] = None
        self._event_refresh = 0

    # -- context ---------------------------------------------------------------------------------------------------
    @property
    def client(self):
        return self.window.client

    @property
    def available(self) -> bool:
        """Floors need a reachable, authenticated core and a loaded workspace."""
        w = self.window
        return bool(getattr(w, "loaded", True) and getattr(w, "core_connected", False) and not getattr(w, "auth_failed", False))

    @property
    def workspace_id(self) -> str:
        return self.window.document.payload.id

    @property
    def root(self) -> str:
        return getattr(self.window.document.payload, "working_directory", "") or ""

    @property
    def active_floor(self) -> Optional[Floor]:
        return self.find(self.active_floor_id) if self.active_floor_id else None

    # -- threading -------------------------------------------------------------------------------------------------
    @staticmethod
    def run(fn: Callable[[], Any], done: Callable[[Any], None], failed: Callable[[BaseException], None]) -> None:
        def worker():
            try:
                result = fn()
            except Exception as exc:   # noqa: BLE001 - every failure is shown to the user
                # Bind now: Python deletes `exc` when the except block ends, before the idle callback runs.
                GLib.idle_add(lambda e=exc: (failed(e), GLib.SOURCE_REMOVE)[1])
                return
            GLib.idle_add(lambda r=result: (done(r), GLib.SOURCE_REMOVE)[1])

        threading.Thread(target=worker, name="maestri-floors", daemon=True).start()

    def _status(self, text: str, ok: bool) -> None:
        if self.on_status:
            self.on_status(text, ok)

    def _changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    # -- list + statuses -------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.available:
            self.apply([])
            return
        self._generation += 1
        gen, ws = self._generation, self.workspace_id
        self.run(lambda: self.client.get_floors(ws),
                 lambda floors: self._applied(gen, ws, floors),
                 lambda exc: self._status(f"Could not list floors: {error_text(exc)}", False))

    def _applied(self, gen: int, ws: str, floors) -> None:
        if gen != self._generation or ws != self.workspace_id:
            return   # a newer refresh, or the workspace changed meanwhile
        self.apply(list(floors or []))
        self._load_statuses(gen, ws)

    def apply(self, floors: List[Floor]) -> None:
        """Main thread: adopt the server's list (also into the document, so the sync never resends a stale one)."""
        self.floors = floors
        ids = {f.get("id") for f in floors}
        self.statuses = {k: v for k, v in self.statuses.items() if k in ids}
        if self.available:
            self.window.document.payload.floors = [dict(f) for f in floors]
        if self.active_floor_id and self.active_floor is None:
            self.active_floor_id = None   # the active floor vanished (deleted elsewhere): back to Ground
        self._changed()

    def _load_statuses(self, gen: int, ws: str) -> None:
        floors = list(self.floors)
        if not floors:
            return

        def fetch():
            out = {}
            for f in floors:
                try:
                    out[f["id"]] = self.client.floor_status(f["id"], workspace_id=ws)
                except Exception:   # noqa: BLE001 - a missing badge is not worth an error toast
                    pass
            return out

        def done(statuses):
            if gen == self._generation and ws == self.workspace_id:
                self.statuses.update(statuses)
                self._changed()

        self.run(fetch, done, lambda _exc: None)

    def find(self, floor_id: Optional[str]) -> Optional[Floor]:
        if not floor_id:
            return None
        return next((f for f in self.floors if str(f.get("id", "")).upper() == floor_id.upper()), None)

    # -- active floor ----------------------------------------------------------------------------------------------
    def switch_to(self, floor_id: Optional[str], notify: bool = True) -> None:
        """None = Ground. Terminals created afterwards start in the floor's worktree (terminal_cwd / terminal_env)."""
        floor = self.find(floor_id)
        self.active_floor_id = floor.get("id") if floor else None
        if notify:
            if floor:
                self._status(f"Active floor: {floor.get('name')}. New terminals open in {floor.get('worktreePath')}", True)
            else:
                self._status(f"Active floor: Ground. Terminals open in {self.root or 'workspace root'}", True)
        if hasattr(self.window, "document") and getattr(self.window, "document", None) and hasattr(self.window.document, "payload"):
            self.window.document.payload.active_floor_id = self.active_floor_id
        bus.emit("floor-switched", self.active_floor_id)
        self._changed()

    def terminal_cwd(self, default: str = "") -> str:
        f = self.active_floor
        return str(f.get("worktreePath")) if f and f.get("worktreePath") else default

    def terminal_env(self, cwd: Optional[str] = None) -> Optional[Dict[str, str]]:
        """MAESTRI_FLOOR_* env for a terminal whose cwd lies in a floor's worktree (the active floor when cwd is None)."""
        floor = self.active_floor if cwd is None else next(
            (f for f in self.floors if _inside(cwd, str(f.get("worktreePath", "")))), None)
        return floor_env(floor, self.root) if floor else None

    # -- /ws/events ------------------------------------------------------------------------------------------------
    def start_events(self) -> None:
        """Subscribe to the core's floor events for the current workspace (restarted on workspace switch)."""
        ws = self.workspace_id if self.available else None
        if ws == self._events_ws and self._events is not None:
            return
        self.stop_events()
        if ws is None:
            return
        try:
            self._events = self.client.connect_events(on_event=self._on_event, workspace_id=ws)
            self._events_ws = ws
        except Exception:   # noqa: BLE001 - live reload is optional; the popover refreshes on open anyway
            self._events, self._events_ws = None, None

    def stop_events(self) -> None:
        if self._events is not None:
            try:
                self._events.close()
            except Exception:   # noqa: BLE001
                pass
        self._events, self._events_ws = None, None

    def _on_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") in FLOOR_EVENTS and not self._event_refresh:
            # Coalesce bursts (create = floorCreated + workspaceSaved, land = floorLanded...) into one reload.
            def fire():
                self._event_refresh = 0
                self.refresh()
                return GLib.SOURCE_REMOVE
            self._event_refresh = GLib.timeout_add(150, fire)

    # -- mutations -------------------------------------------------------------------------------------------------
    def create(self, name: str, branch: str, base: Optional[str], existing: bool,
               done: Callable[[Floor], None], failed: Callable[[str], None]) -> None:
        ws = self.workspace_id

        def ok(floor: Floor) -> None:
            self._status(f"Floor “{floor.get('name', name)}” created on {floor.get('branchName', branch)}", True)
            self.refresh()
            done(floor)

        self.run(lambda: self.client.create_floor(name, branch_name=branch, workspace_id=ws, base_branch=base or None,
                                                  existing_branch=existing),
                 ok, lambda exc: failed(error_text(exc)))

    def delete(self, floor: Floor, delete_branch: bool) -> None:
        ws = self.workspace_id

        def ok(_res) -> None:
            what = " and its branch" if delete_branch else ""
            self._status(f"Floor “{floor.get('name')}”{what} deleted", True)
            if self.active_floor_id and self.active_floor_id.upper() == str(floor.get("id", "")).upper():
                self.switch_to(None, notify=False)   # T2.6: deleting the active floor returns to Ground
            self.refresh()

        self.run(lambda: self.client.delete_floor(floor["id"], workspace_id=ws, delete_branch=delete_branch),
                 ok, lambda exc: self._status(f"Failed to delete: {error_text(exc)}", False))

    def preview(self, floor: Floor, target: Optional[str], done: Callable[[Dict[str, Any]], None],
                failed: Callable[[str], None]) -> None:
        ws = self.workspace_id
        self.run(lambda: self.client.land_preview(floor["id"], target=target, workspace_id=ws),
                 done, lambda exc: failed(error_text(exc)))

    def land(self, floor: Floor, target: str, done: Callable[[Dict[str, Any]], None], failed: Callable[[str], None]) -> None:
        ws = self.workspace_id

        def ok(res: Dict[str, Any]) -> None:
            self._status(f"Floor landed onto {target}", True)
            self.refresh()
            done(res)

        self.run(lambda: self.client.land_floor(floor["id"], target, workspace_id=ws), ok, lambda exc: failed(error_text(exc)))

    def rename(self, floor: Floor, name: str,
               done: Optional[Callable[[Floor], None]] = None,
               failed: Optional[Callable[[str], None]] = None) -> None:
        ws = self.workspace_id
        new_name = name.strip()

        def ok(updated: Floor) -> None:
            res = dict(updated) if isinstance(updated, dict) else {**floor, "name": new_name}
            floor["name"] = res.get("name", new_name)
            self._status(f"Floor renamed to “{floor['name']}”", True)
            self.refresh()
            if done:
                done(res)

        def err(exc: BaseException) -> None:
            msg = error_text(exc)
            self._status(f"Failed to rename: {msg}", False)
            if failed:
                failed(msg)

        self.run(lambda: self.client.patch_floor(floor["id"], name=new_name, workspace_id=ws), ok, err)

    def update_hooks(self, floor: Floor, hooks: Dict[str, Any],
                     done: Optional[Callable[[Floor], None]] = None,
                     failed: Optional[Callable[[str], None]] = None) -> None:
        ws = self.workspace_id
        norm = normalize_hooks(hooks)

        def ok(updated: Floor) -> None:
            res = dict(updated) if isinstance(updated, dict) else {**floor, "hooks": norm}
            floor["hooks"] = normalize_hooks(res.get("hooks") or norm)
            self._status(f"Hooks saved for “{floor.get('name')}”", True)
            self.refresh()
            if done:
                done(res)

        def err(exc: BaseException) -> None:
            msg = error_text(exc)
            self._status(f"Failed to save hooks: {msg}", False)
            if failed:
                failed(msg)

        self.run(lambda: self.client.patch_floor(floor["id"], hooks=norm, workspace_id=ws), ok, err)

    def _call_run_hooks(self, floor_id: str, phase: str, hook_id: Optional[Any], ws: str) -> Any:
        if hasattr(self.client, "run_floor_hooks"):
            return self.client.run_floor_hooks(floor_id, phase=phase, hook_id=hook_id, workspace_id=ws)
        import urllib.parse
        url = f"{self.client.base_url}/api/floors/{floor_id}/hooks/run"
        if ws:
            url += f"?workspace={urllib.parse.quote(ws)}"
        payload: Dict[str, Any] = {"phase": phase}
        if hook_id is not None:
            payload["hookId"] = hook_id
        resp = self.client.session.post(
            url,
            json=payload,
            headers=self.client._headers({"Content-Type": "application/json"}),
            timeout=max(getattr(self.client, "timeout", 10.0), 60.0),
        )
        return self.client._handle_response(resp).json()

    def run_hooks(self, floor: Floor, phase: str = "run", hook_id: Optional[Any] = None,
                  done: Optional[Callable[[Any], None]] = None,
                  failed: Optional[Callable[[str], None]] = None) -> None:
        ws = self.workspace_id

        def ok(res: Any) -> None:
            self._status(f"Ran {phase} hooks for “{floor.get('name')}”", True)
            if done:
                done(res)

        def err(exc: BaseException) -> None:
            msg = error_text(exc)
            self._status(f"Failed to run hooks: {msg}", False)
            if failed:
                failed(msg)

        self.run(lambda: self._call_run_hooks(floor["id"], phase, hook_id, ws), ok, err)

    def run_all_hooks(self, floor: Floor,
                      done: Optional[Callable[[Any], None]] = None,
                      failed: Optional[Callable[[str], None]] = None) -> None:
        """Run all configured hooks for `floor` (defaults to 'run' phase when only 'run' or no phase is configured)."""
        h = normalize_hooks(floor.get("hooks"))
        phases = [p for p in ("setup", "run", "teardown") if any(c.strip() for c in h[p])]
        if not phases or phases == ["run"]:
            self.run_hooks(floor, phase="run", hook_id=None, done=done, failed=failed)
            return
        ws = self.workspace_id

        def run_seq():
            out = []
            for p in phases:
                out.append(self._call_run_hooks(floor["id"], p, None, ws))
            return out

        def ok(res: Any) -> None:
            self._status(f"Ran all hooks for “{floor.get('name')}”", True)
            if done:
                done(res)

        def err(exc: BaseException) -> None:
            msg = error_text(exc)
            self._status(f"Failed to run hooks: {msg}", False)
            if failed:
                failed(msg)

        self.run(run_seq, ok, err)

    def list_branches(self, callback: Callable[[List[str]], None]) -> None:
        """Local branches of Ground's repository (read-only git, async; [] outside a repo)."""
        root = self.root
        if not root or not os.path.isdir(root):
            callback([])
            return
        GitOps(root).list_branches(callback)


# ---------------------------------------------------------------------------------------------------------------------
# dialogs
# ---------------------------------------------------------------------------------------------------------------------
def _form_grid() -> Gtk.Grid:
    return Gtk.Grid(column_spacing=12, row_spacing=10, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)


def _label(text: str, **kw) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=0, **kw)


def _escape_closes(win: Gtk.Window) -> None:
    keys = Gtk.ShortcutController()
    keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                       Gtk.CallbackAction.new(lambda *_: win.close() or True)))
    win.add_controller(keys)


class NewFloorDialog(Gtk.Window):
    """T2.4 — modal "New Floor": name, new branch (pre-filled floor/<slug> + base) or existing branch."""
    MODES = ("New Branch", "Existing Branch")
    HEAD_LABEL = "(Ground current branch)"

    def __init__(self, parent: Gtk.Window, controller: FloorsController,
                 on_created: Optional[Callable[[Floor], None]] = None) -> None:
        super().__init__(title="New Floor", transient_for=parent, modal=True, resizable=False)
        self.set_default_size(460, -1)
        self.controller, self.on_created = controller, on_created
        self.branches: List[str] = []
        self._branch_edited = False
        self._auto_branch = ""
        self._autofilling = False   # True while WE set the branch text (GTK emits "changed" for the delete step too)

        grid = _form_grid()
        self.name_entry = Gtk.Entry(placeholder_text="e.g. Fix login bug", hexpand=True, activates_default=True)
        self.mode = Gtk.DropDown(model=Gtk.StringList.new(list(self.MODES)))
        self.branch_entry = Gtk.Entry(placeholder_text="floor/fix-login-bug", activates_default=True)
        self.base_model = Gtk.StringList.new([self.HEAD_LABEL])
        self.base_dropdown = Gtk.DropDown(model=self.base_model)
        self.existing_model = Gtk.StringList.new([])
        self.existing_dropdown = Gtk.DropDown(model=self.existing_model)
        self.branch_label, self.base_label, self.existing_label = _label("Branch Name"), _label("Base"), _label("Branch")
        self.error_label = _label("", wrap=True, css_classes=["floor-error"])
        self.error_label.set_visible(False)
        rows = ((_label("Floor Name"), self.name_entry), (_label("Mode"), self.mode), (self.branch_label, self.branch_entry),
                (self.base_label, self.base_dropdown), (self.existing_label, self.existing_dropdown))
        for i, (lbl, widget) in enumerate(rows):
            grid.attach(lbl, 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)
        grid.attach(self.error_label, 0, len(rows), 2, 1)

        self.spinner = Gtk.Spinner(visible=False)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.create_btn = Gtk.Button(label="Create", css_classes=["suggested-action"], sensitive=False)
        self.cancel_btn.connect("clicked", lambda _b: self.close())
        self.create_btn.connect("clicked", lambda _b: self.submit())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_bottom=12, margin_end=16)
        for w in (self.spinner, self.cancel_btn, self.create_btn):
            buttons.append(w)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(grid)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(self.create_btn)
        _escape_closes(self)

        self.name_entry.connect("changed", self._on_name_changed)
        self.branch_entry.connect("changed", self._on_branch_changed)
        self.mode.connect("notify::selected", lambda *_: self._on_mode_changed())
        self.existing_dropdown.connect("notify::selected", lambda *_: self._validate())
        self._on_mode_changed()
        controller.list_branches(self.set_branches)

    # -- branches --------------------------------------------------------------------------------------------------
    def set_branches(self, branches: List[str]) -> None:
        self.branches = [b for b in branches if b]
        self.base_model.splice(0, self.base_model.get_n_items(), [self.HEAD_LABEL] + self.branches)
        self.existing_model.splice(0, self.existing_model.get_n_items(), self.branches)
        self._validate()

    @property
    def existing(self) -> bool:
        return self.mode.get_selected() == 1

    @property
    def base_branch(self) -> Optional[str]:
        i = self.base_dropdown.get_selected()
        return self.branches[i - 1] if 1 <= i <= len(self.branches) else None

    @property
    def existing_branch(self) -> Optional[str]:
        i = self.existing_dropdown.get_selected()
        return self.branches[i] if 0 <= i < len(self.branches) else None

    @property
    def branch(self) -> str:
        return (self.existing_branch or "") if self.existing else self.branch_entry.get_text().strip()

    # -- form ------------------------------------------------------------------------------------------------------
    def _on_name_changed(self, _e) -> None:
        if not self._branch_edited:
            self._auto_branch = default_branch(self.name_entry.get_text()) if self.name_entry.get_text().strip() else ""
            # set_text() makes GTK4 emit "changed" twice (once with "" after the delete, once with the new text). The
            # empty intermediate state used to be read as a user edit, freezing the branch after two characters.
            self._autofilling = True
            try:
                self.branch_entry.set_text(self._auto_branch)   # not a user edit (see _on_branch_changed)
            finally:
                self._autofilling = False
        self._validate()

    def _on_branch_changed(self, _e) -> None:
        if not self._autofilling and self.branch_entry.get_text() != self._auto_branch:
            self._branch_edited = True
        self._validate()

    def _on_mode_changed(self) -> None:
        new = not self.existing
        for w in (self.branch_label, self.branch_entry, self.base_label, self.base_dropdown):
            w.set_visible(new)
        for w in (self.existing_label, self.existing_dropdown):
            w.set_visible(not new)
        self._validate()

    def validation_error(self) -> Optional[str]:
        if not self.name_entry.get_text().strip():
            return "Enter a floor name"
        if self.existing:
            return None if self.existing_branch else "No existing branch found"
        return branch_error(self.branch_entry.get_text())

    def _validate(self) -> None:
        err = self.validation_error()
        busy = self.spinner.get_visible()
        self.create_btn.set_sensitive(err is None and not busy)
        typed = self.name_entry.get_text() or self.branch_entry.get_text()   # an untouched dialog is not an error
        self._show_error(err if (err and typed) else None)

    def _show_error(self, text: Optional[str]) -> None:
        self.error_label.set_label(text or "")
        self.error_label.set_visible(bool(text))

    def submit(self) -> None:
        if self.validation_error() is not None or self.spinner.get_visible():
            return
        self.spinner.set_visible(True)
        self.spinner.start()
        self.create_btn.set_sensitive(False)
        self.controller.create(self.name_entry.get_text().strip(), self.branch,
                               None if self.existing else self.base_branch, self.existing, self._created, self._failed)

    def _created(self, floor: Floor) -> None:
        self.spinner.stop()
        if self.on_created:
            self.on_created(floor)
        self.close()

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        if hasattr(self, "spinner"):
            try:
                self.spinner.stop()
                self.spinner.set_visible(False)
            except Exception:
                pass
        super().close()

    def _failed(self, message: str) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._validate()
        self._show_error(message)   # the dialog stays open (T2.4)


class LandFloorDialog(Gtk.Window):
    """T2.5 — modal "Land": [floor branch] (plane icon) [target]. The target defaults to the floor's own branch; changing it reloads
    the preview (+ins −del, conflicts in red). Merge is enabled only when the core reports canLand."""

    def __init__(self, parent: Gtk.Window, controller: FloorsController, floor: Floor,
                 on_landed: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        super().__init__(title=f"Land “{floor.get('name', 'floor')}”", transient_for=parent, modal=True)
        self.set_default_size(540, 380)
        self.controller, self.floor, self.on_landed = controller, floor, on_landed
        self.preview_data: Optional[Dict[str, Any]] = None
        self._branches: List[str] = []
        self._request = 0
        self._filling = False
        self.land_error: Optional[str] = None   # last refused land: stays visible through the preview reload (L2)

        route = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=16)
        route.append(Gtk.Label(label=floor.get("branchName", "?"), css_classes=["floor-branch"]))
        plane_icon = Gtk.Image.new_from_icon_name("maestri-floor-land-symbolic")
        plane_icon.set_pixel_size(18)
        plane_icon.add_css_class("floor-plane")
        route.append(plane_icon)
        self.target_model = Gtk.StringList.new([])
        self.target = Gtk.DropDown(model=self.target_model, sensitive=False)
        route.append(self.target)

        self.spinner = Gtk.Spinner(spinning=True)
        self.summary = _label("Loading preview…", wrap=True, selectable=True)
        self.reason = _label("", wrap=True, css_classes=["floor-error"])
        self.reason.set_visible(False)
        self.conflicts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["floor-conflict"])
        self._conflicts_text = ""
        self.conflicts.get_label = lambda: self._conflicts_text
        self.conflicts.set_visible(False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=16, margin_end=16, margin_top=12)
        head = Gtk.Box(spacing=8)
        head.append(self.spinner)
        head.append(self.summary)
        body.append(head)
        body.append(self.reason)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.conflicts)
        body.append(scroller)

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.merge_btn = Gtk.Button(label="Merge", css_classes=["suggested-action"], sensitive=False)
        self.cancel_btn.connect("clicked", lambda _b: self.close())
        self.merge_btn.connect("clicked", lambda _b: self.submit())
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_bottom=12, margin_end=16, margin_top=8)
        buttons.append(self.cancel_btn)
        buttons.append(self.merge_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(route)
        box.append(body)
        box.append(buttons)
        self.set_child(box)
        self.target.connect("notify::selected", lambda *_: self._on_target_changed())
        _escape_closes(self)
        self.load(floor.get("branchName"))   # "same branch as the floor" is the default (T2.5)

    @property
    def selected_target(self) -> Optional[str]:
        i = self.target.get_selected()
        return self._branches[i] if 0 <= i < len(self._branches) else None

    def load(self, target: Optional[str]) -> None:
        self._request += 1
        req = self._request
        self.spinner.set_visible(True)
        self.merge_btn.set_sensitive(False)
        self.controller.preview(self.floor, target, lambda p: self._previewed(req, p), lambda m: self._failed_preview(req, m))

    def _on_target_changed(self) -> None:
        if self._filling:
            return
        t = self.selected_target
        if t and (self.preview_data is None or t != self.preview_data.get("target")):
            self.land_error = None   # a new target is a new question: the old refusal no longer applies
            self.load(t)

    def _select(self, branches: List[str], target: Optional[str]) -> None:
        self._filling = True
        if branches != self._branches:
            self._branches = branches
            self.target_model.splice(0, self.target_model.get_n_items(), branches)
        if target in self._branches:
            self.target.set_selected(self._branches.index(target))
        self._filling = False

    def _previewed(self, req: int, p: Dict[str, Any]) -> None:
        if req != self._request:
            return   # the user picked another target meanwhile
        self.preview_data = p
        branches = list(p.get("branches") or [])
        if p.get("target") and p["target"] not in branches:
            branches.append(p["target"])
        self._select(branches, p.get("target"))
        self.target.set_sensitive(True)
        self.spinner.set_visible(False)
        self.summary.set_label(describe_preview(p))
        reason = "\n".join(r for r in (self.land_error, blocking_reason(p)) if r)
        self.reason.set_label(reason)
        self.reason.set_visible(bool(reason))
        conflicts = list(p.get("conflicts") or [])
        child = self.conflicts.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.conflicts.remove(child)
            child = nxt
        for c in conflicts:
            row = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name("maestri-close-symbolic")
            icon.set_pixel_size(14)
            icon.add_css_class("floor-conflict")
            row.append(icon)
            row.append(Gtk.Label(label=str(c), xalign=0, selectable=True, css_classes=["floor-conflict"]))
            self.conflicts.append(row)
        self._conflicts_text = "\n".join(str(c) for c in conflicts)
        self.conflicts.set_visible(bool(conflicts))
        self.merge_btn.set_sensitive(bool(p.get("canLand")))

    def _failed_preview(self, req: int, message: str) -> None:
        if req != self._request:
            return
        self.spinner.set_visible(False)
        self.target.set_sensitive(bool(self._branches))
        self.summary.set_label("Preview unavailable.")
        self.reason.set_label(message)
        self.reason.set_visible(True)
        self.merge_btn.set_sensitive(False)

    def submit(self) -> None:
        p, target = self.preview_data, self.selected_target
        if not p or not p.get("canLand") or not target or target != p.get("target"):
            return
        self.merge_btn.set_sensitive(False)
        self.target.set_sensitive(False)
        self.spinner.set_visible(True)
        self.land_error = None
        self.controller.land(self.floor, target, self._landed, self._failed_land)

    def _landed(self, res: Dict[str, Any]) -> None:
        if self.on_landed:
            self.on_landed(res)
        self.close()

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        if hasattr(self, "spinner"):
            try:
                self.spinner.stop()
                self.spinner.set_visible(False)
            except Exception:
                pass
        super().close()

    def _failed_land(self, message: str) -> None:
        self.spinner.set_visible(False)
        self.target.set_sensitive(True)
        self.land_error = f"Land failed: {message}"
        self.reason.set_label(self.land_error)
        self.reason.set_visible(True)
        self.load(self.selected_target)   # fresh state; _previewed keeps land_error on top of it


class DeleteFloorDialog(Gtk.Window):
    """T2.6 — modal dialog ["Cancel", "Keep branch", "Delete branch"] with outline warning icon (no ⚠ glyph).
    The worktree is always removed (reference)."""
    CANCEL, KEEP_BRANCH, DELETE_BRANCH = 0, 1, 2
    BUTTONS = ["Cancel", "Keep branch", "Delete branch"]

    def __init__(self, parent: Gtk.Window, controller: FloorsController, floor: Floor) -> None:
        msg = f"Delete floor “{floor.get('name', 'floor')}”?"
        detail = delete_warning(floor, controller.statuses.get(floor.get("id")))
        super().__init__(title=msg, transient_for=parent, modal=True, resizable=False)
        self.set_default_size(460, -1)
        self.parent, self.controller, self.floor = parent, controller, floor
        self.alert = Gtk.AlertDialog(
            message=msg,
            detail=detail,
            buttons=list(self.BUTTONS), cancel_button=self.CANCEL, default_button=self.KEEP_BRANCH, modal=True)

        self.warning_icon = Gtk.Image.new_from_icon_name("maestri-warning-symbolic")
        self.warning_icon.set_pixel_size(24)
        self.warning_icon.add_css_class("floor-dirty")
        self.warning_icon.set_valign(Gtk.Align.START)

        self.message_label = _label(msg, wrap=True)
        self.detail_label = _label(detail, wrap=True, selectable=True)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        text_box.append(self.message_label)
        text_box.append(self.detail_label)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=16, margin_bottom=12, margin_start=16, margin_end=16)
        content.append(self.warning_icon)
        content.append(text_box)

        self.cancel_btn = Gtk.Button(label=self.BUTTONS[self.CANCEL])
        self.keep_btn = Gtk.Button(label=self.BUTTONS[self.KEEP_BRANCH], css_classes=["suggested-action"])
        self.delete_btn = Gtk.Button(label=self.BUTTONS[self.DELETE_BRANCH], css_classes=["destructive-action"])
        self.cancel_btn.connect("clicked", lambda _b: self.choose(self.CANCEL))
        self.keep_btn.connect("clicked", lambda _b: self.choose(self.KEEP_BRANCH))
        self.delete_btn.connect("clicked", lambda _b: self.choose(self.DELETE_BRANCH))

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_bottom=12, margin_end=16)
        for btn in (self.cancel_btn, self.keep_btn, self.delete_btn):
            buttons.append(btn)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(content)
        root.append(buttons)
        self.set_child(root)
        self.set_default_widget(self.keep_btn)
        _escape_closes(self)

    def present(self) -> None:
        super().present()

    def _chosen(self, alert: Gtk.AlertDialog, result) -> None:
        try:
            index = alert.choose_finish(result)
        except GLib.Error:
            index = self.CANCEL   # dismissed
        self.choose(index)

    def choose(self, index: int) -> None:
        if index in (self.KEEP_BRANCH, self.DELETE_BRANCH):
            self.controller.delete(self.floor, delete_branch=index == self.DELETE_BRANCH)
        self.close()

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        if hasattr(self, "spinner"):
            try:
                self.spinner.stop()
                self.spinner.set_visible(False)
            except Exception:
                pass
        super().close()


class RenameFloorDialog(Gtk.Window):
    """F13 — modal "Rename Floor": updates floor name via PATCH /api/floors/:id."""

    def __init__(self, parent: Gtk.Window, controller: FloorsController, floor: Floor,
                 on_renamed: Optional[Callable[[Floor], None]] = None) -> None:
        super().__init__(title="Rename Floor", transient_for=parent, modal=True, resizable=False)
        self.set_default_size(400, -1)
        self.controller, self.floor, self.on_renamed = controller, floor, on_renamed

        grid = _form_grid()
        self.name_entry = Gtk.Entry(text=str(floor.get("name", "")), placeholder_text="Floor Name",
                                    hexpand=True, activates_default=True)
        self.error_label = _label("", wrap=True, css_classes=["floor-error"])
        self.error_label.set_visible(False)
        grid.attach(_label("Floor Name"), 0, 0, 1, 1)
        grid.attach(self.name_entry, 1, 0, 1, 1)
        grid.attach(self.error_label, 0, 1, 2, 1)

        self.spinner = Gtk.Spinner(visible=False)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.save_btn = Gtk.Button(label="Rename", css_classes=["suggested-action"])
        self.rename_btn = self.save_btn
        self.cancel_btn.connect("clicked", lambda _b: self.close())
        self.save_btn.connect("clicked", lambda _b: self.submit())

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_bottom=12, margin_end=16)
        for w in (self.spinner, self.cancel_btn, self.save_btn):
            buttons.append(w)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(grid)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(self.save_btn)
        _escape_closes(self)
        self.name_entry.connect("changed", lambda _e: self._validate())
        self._validate()

    def validation_error(self) -> Optional[str]:
        return None if self.name_entry.get_text().strip() else "Enter a floor name"

    def _validate(self) -> None:
        err = self.validation_error()
        busy = self.spinner.get_visible()
        self.save_btn.set_sensitive(err is None and not busy)
        if not err:
            self.error_label.set_visible(False)

    def _show_error(self, text: Optional[str]) -> None:
        self.error_label.set_label(text or "")
        self.error_label.set_visible(bool(text))

    def submit(self) -> None:
        err = self.validation_error()
        if err is not None or self.spinner.get_visible():
            self._show_error(err)
            return
        self.spinner.set_visible(True)
        self.spinner.start()
        self.save_btn.set_sensitive(False)
        self.controller.rename(self.floor, self.name_entry.get_text().strip(), self._renamed, self._failed)

    def _renamed(self, updated: Floor) -> None:
        self.spinner.stop()
        if self.on_renamed:
            self.on_renamed(updated)
        self.close()

    def _failed(self, message: str) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._validate()
        self._show_error(message)

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        if hasattr(self, "spinner"):
            try:
                self.spinner.stop()
                self.spinner.set_visible(False)
            except Exception:
                pass
        super().close()


class HooksConfigDialog(Gtk.Window):
    """F14 / F16 / K12 — modal "Hooks — <floorName>" (ported from HooksConfigSheet in FloorOverviewView.swift):
    Setup / Run / Teardown phases, multiple shell commands per phase, Auto-run Setup Hooks toggle,
    per-command Play button and Run All -> POST /api/floors/:id/hooks/run, Save -> PATCH /api/floors/:id."""
    PHASES = (
        ("setup", "Setup", "Executed after Floor is created", "maestri-play-symbolic"),
        ("run", "Run", "Executed on manual trigger", "maestri-bolt-symbolic"),
        ("teardown", "Teardown", "Executed before Landing/deleting Floor", "maestri-stop-symbolic"),
    )

    def __init__(self, parent: Gtk.Window, controller: FloorsController, floor: Floor,
                 on_saved: Optional[Callable[[Floor], None]] = None) -> None:
        super().__init__(title=f"Hooks — {floor.get('name', 'floor')}", transient_for=parent, modal=True)
        self.set_default_size(500, 520)
        self.controller, self.floor, self.on_saved = controller, floor, on_saved
        initial = normalize_hooks(floor.get("hooks"))

        self.command_rows: Dict[str, List[Gtk.Box]] = {"setup": [], "run": [], "teardown": []}
        self.phase_boxes: Dict[str, Gtk.Box] = {}
        self.empty_labels: Dict[str, Gtk.Label] = {}
        self.add_buttons: Dict[str, Gtk.Button] = {}
        self.phase_run_buttons: Dict[str, Gtk.Button] = {}
        self.auto_run_check = Gtk.CheckButton(label="Auto-run Setup Hooks", active=bool(initial["autoRunSetup"]))

        sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                           margin_top=14, margin_bottom=14, margin_start=16, margin_end=16)
        for idx, (phase, title, subtitle, icon_name) in enumerate(self.PHASES):
            if idx > 0:
                sections.append(Gtk.Separator())
            sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            header = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(16)
            icon.add_css_class("floor-row-icon")
            header.append(icon)
            titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
            titles.append(_label(title, css_classes=["floor-hook-section-title"]))
            titles.append(_label(subtitle, css_classes=["floor-hook-section-sub"]))
            header.append(titles)

            phase_play = Gtk.Button(child=Gtk.Image.new_from_icon_name("maestri-play-symbolic"), has_frame=False,
                                    tooltip_text=f"Run {title} hooks", css_classes=["flat", "floor-hook-btn"])
            phase_play.connect("clicked", lambda _b, p=phase: self.run_phase(p))
            self.phase_run_buttons[phase] = phase_play
            header.append(phase_play)

            add_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
            add_box.append(Gtk.Image.new_from_icon_name("maestri-plus-symbolic"))
            add_box.append(Gtk.Label(label="Add command"))
            add_btn = Gtk.Button(child=add_box, has_frame=False, tooltip_text=f"Add {title} command",
                                 css_classes=["flat", "floor-action"])
            add_btn.get_label = lambda: "+ Add command"
            add_btn.connect("clicked", lambda _b, p=phase: self.add_command(p, ""))
            self.add_buttons[phase] = add_btn
            header.append(add_btn)
            sec.append(header)

            cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.phase_boxes[phase] = cmd_box
            empty_lbl = _label("No Command", css_classes=["floor-hook-section-sub"])
            self.empty_labels[phase] = empty_lbl
            sec.append(cmd_box)
            sec.append(empty_lbl)

            if phase == "setup":
                sec.append(self.auto_run_check)

            sections.append(sec)
            for cmd in initial[phase]:
                self.add_command(phase, cmd)
            self._sync_empty_state(phase)

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(sections)

        self.error_label = _label("", wrap=True, css_classes=["floor-error"], margin_start=16, margin_end=16)
        self.error_label.set_visible(False)

        self.spinner = Gtk.Spinner(visible=False)
        run_all_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        run_all_box.append(Gtk.Image.new_from_icon_name("maestri-bolt-symbolic"))
        run_all_box.append(Gtk.Label(label="Run All"))
        self.run_all_btn = Gtk.Button(child=run_all_box, css_classes=["floor-action"])
        self.run_all_btn.get_label = lambda: "Run All"
        self.run_all_btn.connect("clicked", lambda _b: self.run_all())

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.cancel_btn.connect("clicked", lambda _b: self.close())
        self.save_btn.connect("clicked", lambda _b: self.submit())

        footer = Gtk.Box(spacing=8, margin_bottom=12, margin_start=16, margin_end=16, margin_top=8)
        footer.append(self.run_all_btn)
        spacer = Gtk.Box(hexpand=True)
        footer.append(spacer)
        footer.append(self.spinner)
        footer.append(self.cancel_btn)
        footer.append(self.save_btn)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(scroller)
        root.append(self.error_label)
        root.append(footer)
        self.set_child(root)
        self.set_default_widget(self.save_btn)
        _escape_closes(self)

    def _sync_empty_state(self, phase: str) -> None:
        self.empty_labels[phase].set_visible(len(self.command_rows[phase]) == 0)

    def add_command(self, phase: str, text: str = "") -> Gtk.Entry:
        row = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        entry = Gtk.Entry(text=text, placeholder_text="shell command…", hexpand=True, css_classes=["floor-hook-entry"])
        play_btn = Gtk.Button(child=Gtk.Image.new_from_icon_name("maestri-play-symbolic"), has_frame=False,
                              tooltip_text="Run command", css_classes=["flat", "floor-hook-btn"])
        remove_btn = Gtk.Button(child=Gtk.Image.new_from_icon_name("maestri-minus-symbolic"), has_frame=False,
                                tooltip_text="Remove command", css_classes=["flat", "floor-row-delete"])
        row.entry = entry
        row.play_btn = play_btn
        row.remove_btn = remove_btn
        play_btn.connect("clicked", lambda _b, p=phase, r=row: self.run_single(p, r))
        remove_btn.connect("clicked", lambda _b, p=phase, r=row: self.remove_command(p, r))
        row.append(entry)
        row.append(play_btn)
        row.append(remove_btn)
        self.command_rows[phase].append(row)
        self.phase_boxes[phase].append(row)
        self._sync_empty_state(phase)
        return entry

    def remove_command(self, phase: str, row: Gtk.Box) -> None:
        if row in self.command_rows[phase]:
            self.command_rows[phase].remove(row)
            self.phase_boxes[phase].remove(row)
            self._sync_empty_state(phase)

    def collect_hooks(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"autoRunSetup": bool(self.auto_run_check.get_active())}
        for phase in ("setup", "run", "teardown"):
            out[phase] = [r.entry.get_text().strip() for r in self.command_rows[phase] if r.entry.get_text().strip()]
        return out

    def run_single(self, phase: str, row_or_idx: Any) -> None:
        if isinstance(row_or_idx, int):
            idx = row_or_idx
        else:
            rows = self.command_rows.get(phase, [])
            idx = rows.index(row_or_idx) if row_or_idx in rows else 0
        self.floor["hooks"] = self.collect_hooks()
        self.controller.run_hooks(self.floor, phase=phase, hook_id=idx, failed=self._show_error)

    def run_phase(self, phase: str) -> None:
        self.floor["hooks"] = self.collect_hooks()
        self.controller.run_hooks(self.floor, phase=phase, hook_id=None, failed=self._show_error)

    def run_all(self) -> None:
        self.floor["hooks"] = self.collect_hooks()
        self.controller.run_all_hooks(self.floor, failed=self._show_error)

    def _show_error(self, message: Optional[str]) -> None:
        self.error_label.set_label(message or "")
        self.error_label.set_visible(bool(message))

    def submit(self) -> None:
        if self.spinner.get_visible():
            return
        self.spinner.set_visible(True)
        self.spinner.start()
        self.save_btn.set_sensitive(False)
        self._show_error(None)
        self.controller.update_hooks(self.floor, self.collect_hooks(), self._saved, self._failed)

    def _saved(self, updated: Floor) -> None:
        self.spinner.stop()
        if self.on_saved:
            self.on_saved(updated)
        self.close()

    def _failed(self, message: str) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.save_btn.set_sensitive(True)
        self._show_error(message)

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        if hasattr(self, "spinner"):
            try:
                self.spinner.stop()
                self.spinner.set_visible(False)
            except Exception:
                pass
        super().close()


def _icon_exists(name: str) -> bool:
    display = Gdk.Display.get_default()
    return display is not None and Gtk.IconTheme.get_for_display(display).has_icon(name)


# ---------------------------------------------------------------------------------------------------------------------
# floating button
# ---------------------------------------------------------------------------------------------------------------------
class FloorButton(Gtk.MenuButton):
    """Floor capsule of the bottom-right controls: floor icon + "<active floor | Ground>". The popover (opening upwards) lists
    Ground and the floors, then "New Floor…", "Configure Hooks…" and "Land…" (reference 09-floors.md).
    Right-clicking a floor row opens Rename / Configure Hooks / Land / Delete; right-clicking the FloorButton opens
    Configure Hooks / Rename Floor. Outline SVG icons only, no emoji."""
    TOAST_MS = 4000
    LABEL_CHARS = 24

    def __init__(self, window, controller: FloorsController) -> None:
        super().__init__(css_classes=["floor-button"], direction=Gtk.ArrowType.UP, valign=Gtk.Align.CENTER)
        _install_css()
        self.window, self.controller = window, controller
        self._toast_timer = 0

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        self.list_box.connect("row-activated", lambda _lb, row: self._row_activated(row))
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=44, margin_start=10, margin_end=10)
        self.status.set_visible(False)

        self.new_btn = self._action_button("maestri-floor-new-symbolic", "New Floor…")
        self.new_btn.connect("clicked", lambda _b: self.open_new())
        self.hooks_btn = self._action_button("maestri-bolt-symbolic", "Configure Hooks…")
        self.hooks_btn.set_tooltip_text("Configure Setup, Run and Teardown hooks for this floor")
        self.hooks_btn.connect("clicked", lambda _b: self._target_floor_for_hooks() and self.open_hooks(self._target_floor_for_hooks()))
        self.land_btn = self._action_button("maestri-floor-land-symbolic", "Land…")
        self.land_btn.set_tooltip_text("Land commits from the active floor back into the Ground repository")
        self.land_btn.connect("clicked", lambda _b: self.controller.active_floor and self.open_land(self.controller.active_floor))

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_start=4, margin_end=4)
        actions.append(self.new_btn)
        actions.append(self.hooks_btn)
        actions.append(self.land_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                      css_classes=["floor-popover"])
        box.append(Gtk.Label(label="FLOORS", xalign=0, css_classes=["floor-popover-title"]))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      propagate_natural_width=True, max_content_height=360, min_content_width=320)
        scroller.set_child(self.list_box)
        box.append(scroller)
        box.append(self.status)
        box.append(Gtk.Separator(margin_start=8, margin_end=8))
        box.append(actions)
        self.popover = Gtk.Popover(child=box, position=Gtk.PositionType.TOP)
        self.set_popover(self.popover)
        self.popover.connect("show", lambda _p: (controller.refresh(), self.status.set_visible(bool(self.status.get_label()))))

        # Right-click context popover on the FloorButton itself ("Configure Hooks…" / "Rename Floor…")
        ctx_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4,
                          margin_start=4, margin_end=4, css_classes=["floor-popover"])
        self.context_hooks_btn = self._action_button("maestri-bolt-symbolic", "Configure Hooks…")
        self.context_hooks_btn.connect("clicked", lambda _b: self._on_context_hooks())
        self.context_rename_btn = self._action_button("maestri-pencil-symbolic", "Rename Floor…")
        self.context_rename_btn.connect("clicked", lambda _b: self._on_context_rename())
        ctx_box.append(self.context_hooks_btn)
        ctx_box.append(self.context_rename_btn)
        self.context_popover = Gtk.Popover(child=ctx_box, position=Gtk.PositionType.TOP)
        self.context_popover.set_parent(self)

        rclick = Gtk.GestureClick(button=3)
        rclick.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        rclick.connect("pressed", self._on_button_right_click)
        self.add_controller(rclick)

        # Quick-access bolt button (⚡) next to FloorButton: grouped hooks + per-hook Play + Run All (F16 / K12)
        self.quick_hooks_button = Gtk.MenuButton(
            css_classes=["floor-hooks-button", "flat"],
            direction=Gtk.ArrowType.UP,
            valign=Gtk.Align.CENTER,
            tooltip_text="Configure Hooks",
        )
        bolt_img = Gtk.Image.new_from_icon_name("maestri-bolt-symbolic")
        bolt_img.set_pixel_size(15)
        self.quick_hooks_button.set_child(bolt_img)
        self.quick_hooks_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=6, margin_end=6)
        self.quick_run_all_btn = self._action_button("maestri-play-symbolic", "Run All")
        self.quick_run_all_btn.connect("clicked", lambda _b: self._on_quick_run_all())
        self.quick_configure_btn = self._action_button("maestri-bolt-symbolic", "Configure Hooks…")
        self.quick_configure_btn.connect("clicked", lambda _b: self._on_quick_configure())

        qbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                       css_classes=["floor-popover"])
        qbox.append(Gtk.Label(label="HOOKS", xalign=0, css_classes=["floor-popover-title"]))
        qbox.append(self.quick_hooks_list)
        qbox.append(Gtk.Separator(margin_start=8, margin_end=8))
        qactions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_start=4, margin_end=4)
        qactions.append(self.quick_run_all_btn)
        qactions.append(self.quick_configure_btn)
        qbox.append(qactions)
        self.quick_hooks_popover = Gtk.Popover(child=qbox, position=Gtk.PositionType.TOP)
        self.quick_hooks_button.set_popover(self.quick_hooks_popover)
        self.quick_item_rows: List[Gtk.Box] = []

        # Capsule content: floor icon + active floor name (ellipsized so the capsules never overlap)
        self.btn_icon = Gtk.Image.new_from_icon_name("maestri-floor-symbolic")
        self.btn_icon.set_pixel_size(16)
        self.btn_label = Gtk.Label(label="Ground", ellipsize=Pango.EllipsizeMode.END, max_width_chars=self.LABEL_CHARS,
                                   css_classes=["maestri-floor-label"])
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        btn_box.append(self.btn_icon)
        btn_box.append(self.btn_label)
        self.set_child(btn_box)
        self.get_label = lambda: self.btn_label.get_text()   # the visible text (the child is a custom box)

        controller.listeners.append(self.rebuild)
        controller.on_status = self.show_status
        self.rebuild()

    def _target_floor_for_hooks(self) -> Optional[Floor]:
        c = self.controller
        if c.active_floor is not None:
            return c.active_floor
        for f in c.floors:
            if has_configured_hooks(f):
                return f
        return c.floors[0] if c.floors else None

    def _on_button_right_click(self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float) -> None:
        target = self._target_floor_for_hooks()
        self.context_hooks_btn.set_sensitive(target is not None)
        self.context_rename_btn.set_sensitive(target is not None)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.context_popover.popup()

    def _on_context_hooks(self) -> None:
        self.context_popover.popdown()
        target = self._target_floor_for_hooks()
        if target is not None:
            self.open_hooks(target)

    def _on_context_rename(self) -> None:
        self.context_popover.popdown()
        target = self._target_floor_for_hooks()
        if target is not None:
            self.open_rename(target)

    def _on_quick_run_all(self) -> None:
        self.quick_hooks_popover.popdown()
        target = self._target_floor_for_hooks()
        if target is not None:
            self.controller.run_all_hooks(target)

    def _on_quick_configure(self) -> None:
        self.quick_hooks_popover.popdown()
        target = self._target_floor_for_hooks()
        if target is not None:
            self.open_hooks(target)

    @staticmethod
    def _action_button(icon: str, text: str) -> Gtk.Button:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(16)
        row.append(img)
        row.append(Gtk.Label(label=text, xalign=0, hexpand=True))
        btn = Gtk.Button(child=row, has_frame=False, css_classes=["flat", "floor-action"])
        btn.get_label = lambda: text
        return btn

    def _rebuild_quick_hooks(self) -> None:
        child = self.quick_hooks_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.quick_hooks_list.remove(child)
            child = nxt
        self.quick_item_rows = []
        target = self._target_floor_for_hooks()
        any_hooks = has_configured_hooks(target)
        self.quick_hooks_button.set_sensitive(self.controller.available and target is not None)
        self.quick_hooks_button.set_visible(bool(any_hooks or (self.controller.active_floor is not None)))
        self.quick_run_all_btn.set_sensitive(target is not None)
        self.quick_configure_btn.set_sensitive(target is not None)
        if not target:
            self.quick_hooks_list.append(_label("No floor available", css_classes=["floor-hook-section-sub"]))
            return
        hooks = normalize_hooks(target.get("hooks"))
        for phase, title, _sub, _icon in HooksConfigDialog.PHASES:
            cmds = [c for c in hooks[phase] if c.strip()]
            if not cmds:
                continue
            self.quick_hooks_list.append(_label(title.upper(), css_classes=["floor-popover-title"]))
            for idx, cmd in enumerate(cmds):
                item = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, css_classes=["floor-file-row"])
                lbl = Gtk.Label(label=cmd, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                max_width_chars=28, css_classes=["floor-branch"])
                play = Gtk.Button(child=Gtk.Image.new_from_icon_name("maestri-play-symbolic"), has_frame=False,
                                  tooltip_text=f"Run {cmd}", css_classes=["flat", "floor-hook-btn"])
                item.phase = phase
                item.index = idx
                item.command = cmd
                item.play_btn = play
                play.connect("clicked", lambda _b, f=target, p=phase, i=idx: (
                    self.quick_hooks_popover.popdown(),
                    self.controller.run_hooks(f, phase=p, hook_id=i),
                ))
                item.append(lbl)
                item.append(play)
                self.quick_hooks_list.append(item)
                self.quick_item_rows.append(item)
        if not self.quick_item_rows:
            self.quick_hooks_list.append(_label("No hooks configured", css_classes=["floor-hook-section-sub"]))

    def rebuild(self) -> None:
        if self.get_root() is None:
            return
        c = self.controller
        active = c.active_floor
        name = active.get('name') if active else 'Ground'
        self.btn_label.set_text(name)
        if active is None:
            self.btn_icon.set_from_icon_name("maestri-layers-symbolic" if _icon_exists("maestri-layers-symbolic") else "maestri-floor-symbolic")
        else:
            self.btn_icon.set_from_icon_name("maestri-floor-symbolic")
        self.set_sensitive(c.available)
        if not c.available:
            self.set_tooltip_text("Floors require the Maestrix core (disconnected)")
        elif active is not None:
            self.set_tooltip_text(f"Floor “{name}” · {active.get('branchName', '')}")
        else:
            self.set_tooltip_text("Floors: isolated git worktrees for this workspace")
        self.land_btn.set_sensitive(active is not None)   # Land only from a floor, never from Ground (T2.3)
        self.hooks_btn.set_sensitive(self._target_floor_for_hooks() is not None)
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            ctx_pop = getattr(child, "context_popover", None)
            if ctx_pop is not None and ctx_pop.get_parent() is not None:
                try:
                    ctx_pop.popdown()
                    ctx_pop.unparent()
                except Exception:
                    pass
            child.context_popover = None
            self.list_box.remove(child)
            child = nxt
        self.list_box.append(self._row(None))
        for f in c.floors:
            self.list_box.append(self._row(f))
        self._rebuild_quick_hooks()

    def _cleanup(self) -> None:
        self.controller.on_changed = None
        self.controller.on_status = None
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)
            self._toast_timer = 0
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            ctx_pop = getattr(child, "context_popover", None)
            if ctx_pop is not None and ctx_pop.get_parent() is not None:
                try:
                    ctx_pop.popdown()
                    ctx_pop.unparent()
                except Exception:
                    pass
            child.context_popover = None
            child = nxt
        if getattr(self, "context_popover", None) is not None:
            try:
                self.context_popover.popdown()
                self.context_popover.unparent()
            except Exception:
                pass
            self.context_popover = None

    def _row(self, floor: Optional[Floor]) -> Gtk.ListBoxRow:
        c = self.controller
        is_active = (floor is None and c.active_floor_id is None) or (
            floor is not None and c.active_floor_id is not None and str(floor.get("id", "")).upper() == c.active_floor_id.upper())
        row = Gtk.ListBoxRow(activatable=True, css_classes=["floor-row"] + (["active"] if is_active else []))
        row.floor_id = floor.get("id") if floor else None
        h = Gtk.Box(spacing=8)
        kind = Gtk.Image.new_from_icon_name(("maestri-layers-symbolic" if _icon_exists("maestri-layers-symbolic") else "maestri-floor-symbolic")
                                            if floor is None else "maestri-git-branch-symbolic"
                                            if _icon_exists("maestri-git-branch-symbolic") else "maestri-layers-symbolic")
        kind.set_pixel_size(16)
        kind.add_css_class("floor-row-icon")
        h.append(kind)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        title = floor.get("name", "floor") if floor else "Ground"
        subtitle = floor.get("branchName", "") if floor else c.root
        row.title_label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END, width_chars=16, max_width_chars=30)
        row.subtitle_label = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, width_chars=16, max_width_chars=34,
                                       css_classes=["floor-branch"])
        texts.append(row.title_label)
        texts.append(row.subtitle_label)
        h.append(texts)
        if floor is not None:
            st = c.statuses.get(floor.get("id"))
            row.floor_badge = Gtk.Label(label=badge_text(st), css_classes=["floor-badge"], valign=Gtk.Align.CENTER)
            row.floor_dirty = Gtk.Box(css_classes=["floor-dot"], tooltip_text="Uncommitted changes", valign=Gtk.Align.CENTER,
                                      visible=bool(st and st.get("dirty")))
            h.append(row.floor_badge)
            h.append(row.floor_dirty)
        check = Gtk.Image.new_from_icon_name("maestri-check-symbolic")
        check.set_pixel_size(14)
        check.add_css_class("floor-check")
        check.set_opacity(1.0 if is_active else 0.0)   # keeps the column width stable
        h.append(check)
        if floor is not None:
            hooks_btn = Gtk.Button(child=Gtk.Image.new_from_icon_name("maestri-bolt-symbolic"), has_frame=False,
                                   tooltip_text="Configure Hooks", css_classes=["flat", "floor-hook-btn"], valign=Gtk.Align.CENTER)
            hooks_btn.connect("clicked", lambda _b, f=floor: self.open_hooks(f))
            row.hooks_btn = hooks_btn
            h.append(hooks_btn)
            delete = Gtk.Button(child=Gtk.Image.new_from_icon_name("maestri-close-symbolic"), has_frame=False,
                                tooltip_text="Delete floor…", css_classes=["flat", "floor-row-delete"], valign=Gtk.Align.CENTER)
            delete.connect("clicked", lambda _b, f=floor: self.open_delete(f))
            row.delete_btn = delete
            h.append(delete)

            # Changed files list from /api/floors/:id/status `files` (F9)
            files = format_changed_files(c.statuses.get(floor.get("id")))
            row.changed_files = files
            row.file_labels: List[str] = []
            files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=24, margin_top=4,
                                visible=bool(files), css_classes=["floor-files-box"])
            for code, path in files[:8]:
                f_row = Gtk.Box(spacing=6, css_classes=["floor-file-row"])
                f_row.append(Gtk.Label(label=code, xalign=0, css_classes=["floor-file-status"]))
                f_row.append(Gtk.Label(label=path, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE,
                                       max_width_chars=32, css_classes=["floor-file-path"]))
                files_box.append(f_row)
                row.file_labels.append(f"{code} {path}")
            if len(files) > 8:
                more = f"+{len(files) - 8} more…"
                files_box.append(Gtk.Label(label=more, xalign=0, css_classes=["floor-branch"]))
                row.file_labels.append(more)
            row.files_box = files_box

            # Row right-click context menu: Rename, Configure Hooks, Land, Delete (F13 / F14)
            menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4,
                               margin_start=4, margin_end=4, css_classes=["floor-popover"])
            row.rename_item = self._action_button("maestri-pencil-symbolic", "Rename…")
            row.hooks_item = self._action_button("maestri-bolt-symbolic", "Configure Hooks…")
            row.land_item = self._action_button("maestri-floor-land-symbolic", "Land…")
            row.delete_item = self._action_button("maestri-trash-symbolic", "Delete…")
            class _RowPopover:
                def __init__(self, child):
                    self.child = child
                def popup(self):
                    pass
                def popdown(self):
                    pass
                def get_parent(self):
                    return None
                def unparent(self):
                    pass

            row.context_popover = _RowPopover(menu_box)
            row.rename_item.connect("clicked", lambda _b, r=row, f=floor: (r.context_popover.popdown(), self.open_rename(f)))
            row.hooks_item.connect("clicked", lambda _b, r=row, f=floor: (r.context_popover.popdown(), self.open_hooks(f)))
            row.land_item.connect("clicked", lambda _b, r=row, f=floor: (r.context_popover.popdown(), self.open_land(f)))
            row.delete_item.connect("clicked", lambda _b, r=row, f=floor: (r.context_popover.popdown(), self.open_delete(f)))
            for item_btn in (row.rename_item, row.hooks_item, row.land_item, row.delete_item):
                menu_box.append(item_btn)

            def _show_row_context(g, _n, _x, _y, r=row):
                g.set_state(Gtk.EventSequenceState.CLAIMED)
                if r.context_popover.get_parent() is None:
                    r.context_popover.set_parent(r)
                r.context_popover.popup()

            rclick = Gtk.GestureClick(button=3)
            rclick.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            rclick.connect("pressed", _show_row_context)
            row.add_controller(rclick)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            outer.append(h)
            outer.append(files_box)
            row.set_child(outer)
        else:
            row.changed_files = []
            row.file_labels = []
            row.files_box = None
            row.set_child(h)
        return row

    def _row_activated(self, row: Gtk.ListBoxRow) -> None:
        self.controller.switch_to(getattr(row, "floor_id", None))
        self.popover.popdown()

    def show_status(self, text: str, ok: bool) -> None:
        """Toast: shown in the popover and as the button's tooltip, cleared after TOAST_MS."""
        self.status.set_label(text)
        self.status.set_css_classes(["floor-ok" if ok else "floor-error"])
        if self.popover.get_visible():
            self.status.set_visible(True)
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)

        def clear():
            self._toast_timer = 0
            self.status.set_visible(False)
            return GLib.SOURCE_REMOVE

        self._toast_timer = GLib.timeout_add(self.TOAST_MS, clear)

    # -- actions ---------------------------------------------------------------------------------------------------
    def open_new(self) -> NewFloorDialog:
        self.popover.popdown()
        dlg = NewFloorDialog(self.window, self.controller)
        dlg.present()
        return dlg

    def open_rename(self, floor: Optional[Floor] = None) -> RenameFloorDialog:
        self.popover.popdown()
        target = floor or self._target_floor_for_hooks() or {}
        dlg = RenameFloorDialog(self.window, self.controller, target)
        dlg.present()
        return dlg

    def open_hooks(self, floor: Optional[Floor] = None) -> HooksConfigDialog:
        self.popover.popdown()
        target = floor or self._target_floor_for_hooks() or {}
        dlg = HooksConfigDialog(self.window, self.controller, target)
        dlg.present()
        return dlg

    def open_land(self, floor: Floor) -> LandFloorDialog:
        self.popover.popdown()
        dlg = LandFloorDialog(self.window, self.controller, floor)
        dlg.present()
        return dlg

    def open_delete(self, floor: Floor) -> DeleteFloorDialog:
        self.popover.popdown()
        dlg = DeleteFloorDialog(self.window, self.controller, floor)
        dlg.present()
        return dlg


def install_floors(window) -> FloorsController:
    """Wire floors into OpenMaestriWindow: the floating button in bottom controls, a reload on every
    workspace load (initial load and switch) and the /ws/events subscription; both stop when the window closes."""
    controller = FloorsController(window)
    window.floor_button = FloorButton(window, controller)
    window.floor_hooks_button = window.floor_button.quick_hooks_button
    if hasattr(window, "bottom_controls") and hasattr(window.bottom_controls, "pill_floor"):
        window.bottom_controls.pill_floor.append(window.floor_button)
        window.bottom_controls.pill_floor.append(window.floor_hooks_button)
        window.bottom_controls.btn_floors = window.floor_button
    elif hasattr(window, "bottom_controls"):
        window.bottom_controls.prepend(window.floor_hooks_button)
        window.bottom_controls.prepend(window.floor_button)
        window.bottom_controls.btn_floors = window.floor_button
    else:
        window.floor_button.set_halign(Gtk.Align.END)
        window.floor_button.set_valign(Gtk.Align.END)
        window.floor_button.set_margin_end(16)
        window.floor_button.set_margin_bottom(16)
        window.canvas_widget.get_parent().add_overlay(window.floor_button)

    def loaded(*_args) -> None:
        controller.floors, controller.statuses, controller.active_floor_id = [], {}, None
        controller.refresh()
        controller.start_events()

    def closed(*_args) -> bool:
        # close-request is not emitted when the window is destroyed directly (app quit, tests): handle both, once (R3).
        if bus.handler_is_connected(hid):
            bus.disconnect(hid)
        controller.stop_events()
        if hasattr(window, "floor_button") and hasattr(window.floor_button, "_cleanup"):
            window.floor_button._cleanup()
        return False

    hid = bus.connect("workspace-loaded", loaded)
    window.connect("close-request", closed)
    window.connect("destroy", closed)
    return controller
