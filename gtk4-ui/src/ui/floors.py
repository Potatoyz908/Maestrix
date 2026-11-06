"""Floors UI for Open Maestri GTK 4 (spec: docs/linux-gtk-sprint5-spec.md §2 T2.3-T2.6, base sprint4 §1; reference
docs/reference/official/09-floors.md).

A floor is a git worktree of the workspace's repository with its own branch. The core owns them (`/api/floors`); this module is
only a client:

* `FloorsController`: floor list, per-floor status (dirty ●, +ins −del), the active floor (None = Ground) and what new
  terminals inherit from it (`terminal_cwd`, `terminal_env`). Reloads on `workspace-loaded` and on the core's `/ws/events`
  floor events. Every HTTP call runs on a worker thread; results come back through GLib.idle_add. The server's floor list is
  mirrored into `window.document.payload.floors` on the main thread, so a sync snapshot never resends a stale list (R1).
* `FloorButton`: floating `Gtk.MenuButton` "🏢 <active floor | Ground>" in the canvas's bottom-right corner. Its popover lists
  Ground + the floors (click = switch, ✕ = delete), "＋ Novo floor…" and "Land…" (only when a floor is active).
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
.floor-button { border-radius: 18px; padding: 4px 12px; background-color: alpha(#1f1f22, 0.92); }
.floor-popover-title { font-weight: bold; margin: 4px 8px; }
.floor-row { padding: 4px 6px; }
.floor-row.active { background-color: alpha(#3b82f6, 0.18); border-radius: 6px; }
.floor-branch { font-family: monospace; font-size: 11px; opacity: 0.7; }
.floor-badge { font-family: monospace; font-size: 11px; }
.floor-dirty { color: #e5c07b; }
.floor-error { color: #f14c4c; }
.floor-ok { color: #23d18b; }
.floor-conflict { color: #f14c4c; font-family: monospace; }
.floor-plane { font-size: 20px; }
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
        return "Informe o nome da branch"
    if b == "@" or not _valid_ref(b):
        return "Nome de branch inválido (sem espaços, '..', ~ ^ : ? * [ \\ nem '.' no início de um componente)"
    return None


def badge_text(status: Optional[Dict[str, Any]]) -> str:
    """"+ins −del" (empty when unknown or no difference)."""
    if not status:
        return ""
    ins, dels = int(status.get("insertions") or 0), int(status.get("deletions") or 0)
    return f"+{ins} −{dels}" if ins or dels else ""


def describe_preview(p: Dict[str, Any]) -> str:
    """Summary of a land-preview response (the conflicts list is rendered separately)."""
    floor_branch, target = p.get("floorBranch", "?"), p.get("target", "?")
    if p.get("sameBranch"):
        return (f"{floor_branch} é a própria branch do floor: os commits já estão no repositório (o worktree compartilha "
                f"as refs com o Ground). Escolha outra branch de destino para fazer merge.")
    lines = []
    ahead = int(p.get("ahead") or 0)
    if ahead == 0:
        lines.append(f"Nada para pousar: {floor_branch} não tem commits que {target} não tenha.")
    else:
        kind = "fast-forward" if p.get("fastForward") else "commit de merge"
        lines.append(f"{ahead} commit{'s' if ahead != 1 else ''} → {target} ({kind})")
    ins, dels = int(p.get("insertions") or 0), int(p.get("deletions") or 0)
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
        return "Faça commit antes de pousar" + (f": {shown}" if shown else "")
    if p.get("conflicts"):
        return "Resolva os conflitos no terminal/IDE"
    if p.get("targetCheckedOut"):
        return f"{p.get('target')} está em uso no Ground: faça checkout de outra branch ou use o terminal"
    return None


def delete_warning(floor: Floor, status: Optional[Dict[str, Any]] = None) -> str:
    """DeleteFloorDialog body (L3): what is always lost, and the explicit warning that "Apagar branch" discards the commits
    that were never landed (with their count when the floor status is known)."""
    branch = floor.get("branchName", "")
    ahead = int((status or {}).get("ahead") or 0)
    unlanded = (f"{ahead} commit{'s' if ahead != 1 else ''} ainda não pousado{'s' if ahead != 1 else ''}" if ahead
                else "todos os commits que ainda não foram pousados (Land)")
    return (f"O diretório {floor.get('worktreePath', '')} é sempre removido, inclusive mudanças sem commit.\n\n"
            f"⚠ “Apagar branch” apaga também a branch {branch} e DESCARTA {unlanded}: eles não poderão ser recuperados "
            f"por esta interface. Escolha “Manter branch” para voltar a esses commits depois.")


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
                 lambda exc: self._status(f"Não foi possível listar os floors: {error_text(exc)}", False))

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
    def switch_to(self, floor_id: Optional[str]) -> None:
        """None = Ground. Terminals created afterwards start in the floor's worktree (terminal_cwd / terminal_env)."""
        floor = self.find(floor_id)
        self.active_floor_id = floor.get("id") if floor else None
        if floor:
            self._status(f"Floor ativo: {floor.get('name')}. Novos terminais abrem em {floor.get('worktreePath')}", True)
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
            self._status(f"Floor “{floor.get('name', name)}” criado em {floor.get('branchName', branch)}", True)
            self.refresh()
            done(floor)

        self.run(lambda: self.client.create_floor(name, branch_name=branch, workspace_id=ws, base_branch=base or None,
                                                  existing_branch=existing),
                 ok, lambda exc: failed(error_text(exc)))

    def delete(self, floor: Floor, delete_branch: bool) -> None:
        ws = self.workspace_id

        def ok(_res) -> None:
            what = " e a branch" if delete_branch else ""
            self._status(f"Floor “{floor.get('name')}”{what} apagado", True)
            if self.active_floor_id and self.active_floor_id.upper() == str(floor.get("id", "")).upper():
                self.switch_to(None)   # T2.6: deleting the active floor returns to Ground
            self.refresh()

        self.run(lambda: self.client.delete_floor(floor["id"], workspace_id=ws, delete_branch=delete_branch),
                 ok, lambda exc: self._status(f"Falha ao apagar: {error_text(exc)}", False))

    def preview(self, floor: Floor, target: Optional[str], done: Callable[[Dict[str, Any]], None],
                failed: Callable[[str], None]) -> None:
        ws = self.workspace_id
        self.run(lambda: self.client.land_preview(floor["id"], target=target, workspace_id=ws),
                 done, lambda exc: failed(error_text(exc)))

    def land(self, floor: Floor, target: str, done: Callable[[Dict[str, Any]], None], failed: Callable[[str], None]) -> None:
        ws = self.workspace_id

        def ok(res: Dict[str, Any]) -> None:
            self._status(f"Floor pousado em {target}", True)
            self.refresh()
            done(res)

        self.run(lambda: self.client.land_floor(floor["id"], target, workspace_id=ws), ok, lambda exc: failed(error_text(exc)))

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
    """T2.4 — modal "Novo floor": nome, nova branch (nome pré-preenchido floor/<slug> + base) ou branch existente."""
    MODES = ("Nova branch", "Branch existente")
    HEAD_LABEL = "(branch atual do Ground)"

    def __init__(self, parent: Gtk.Window, controller: FloorsController,
                 on_created: Optional[Callable[[Floor], None]] = None) -> None:
        super().__init__(title="Novo floor", transient_for=parent, modal=True, resizable=False)
        self.set_default_size(460, -1)
        self.controller, self.on_created = controller, on_created
        self.branches: List[str] = []
        self._branch_edited = False
        self._auto_branch = ""

        grid = _form_grid()
        self.name_entry = Gtk.Entry(placeholder_text="ex.: Corrigir login", hexpand=True, activates_default=True)
        self.mode = Gtk.DropDown(model=Gtk.StringList.new(list(self.MODES)))
        self.branch_entry = Gtk.Entry(placeholder_text="floor/corrigir-login", activates_default=True)
        self.base_model = Gtk.StringList.new([self.HEAD_LABEL])
        self.base_dropdown = Gtk.DropDown(model=self.base_model)
        self.existing_model = Gtk.StringList.new([])
        self.existing_dropdown = Gtk.DropDown(model=self.existing_model)
        self.branch_label, self.base_label, self.existing_label = _label("Nome da branch"), _label("Base"), _label("Branch")
        self.error_label = _label("", wrap=True, css_classes=["floor-error"])
        self.error_label.set_visible(False)
        rows = ((_label("Nome"), self.name_entry), (_label("Tipo"), self.mode), (self.branch_label, self.branch_entry),
                (self.base_label, self.base_dropdown), (self.existing_label, self.existing_dropdown))
        for i, (lbl, widget) in enumerate(rows):
            grid.attach(lbl, 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)
        grid.attach(self.error_label, 0, len(rows), 2, 1)

        self.spinner = Gtk.Spinner(visible=False)
        self.cancel_btn = Gtk.Button(label="Cancelar")
        self.create_btn = Gtk.Button(label="Criar", css_classes=["suggested-action"], sensitive=False)
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
            self.branch_entry.set_text(self._auto_branch)   # not a user edit (see _on_branch_changed)
        self._validate()

    def _on_branch_changed(self, _e) -> None:
        if self.branch_entry.get_text() != self._auto_branch:
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
            return "Informe o nome do floor"
        if self.existing:
            return None if self.existing_branch else "Nenhuma branch existente encontrada"
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
        super().close()

    def _failed(self, message: str) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._validate()
        self._show_error(message)   # the dialog stays open (T2.4)


class LandFloorDialog(Gtk.Window):
    """T2.5 — modal "Land": [branch do floor] ✈ [destino]. The target defaults to the floor's own branch; changing it reloads
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
        plane_icon = Gtk.Image.new_from_icon_name("maestri-send-symbolic")
        plane_icon.set_pixel_size(16)
        plane_icon.add_css_class("floor-plane")
        route.append(plane_icon)
        self.target_model = Gtk.StringList.new([])
        self.target = Gtk.DropDown(model=self.target_model, sensitive=False)
        route.append(self.target)

        self.spinner = Gtk.Spinner(spinning=True)
        self.summary = _label("Carregando prévia…", wrap=True, selectable=True)
        self.reason = _label("", wrap=True, css_classes=["floor-error"])
        self.reason.set_visible(False)
        self.conflicts = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True, css_classes=["floor-conflict"])
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

        self.cancel_btn = Gtk.Button(label="Cancelar")
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
        self.load(floor.get("branchName"))   # "mesma branch do floor" is the default (T2.5)

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
        conflicts = p.get("conflicts") or []
        self.conflicts.set_label("\n".join(f"✗ {c}" for c in conflicts))
        self.conflicts.set_visible(bool(conflicts))
        self.merge_btn.set_sensitive(bool(p.get("canLand")))

    def _failed_preview(self, req: int, message: str) -> None:
        if req != self._request:
            return
        self.spinner.set_visible(False)
        self.target.set_sensitive(bool(self._branches))
        self.summary.set_label("Prévia indisponível.")
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
        super().close()

    def _failed_land(self, message: str) -> None:
        self.spinner.set_visible(False)
        self.target.set_sensitive(True)
        self.land_error = f"Land falhou: {message}"
        self.reason.set_label(self.land_error)
        self.reason.set_visible(True)
        self.load(self.selected_target)   # fresh state; _previewed keeps land_error on top of it


class DeleteFloorDialog:
    """T2.6 — `Gtk.AlertDialog` ["Cancelar", "Manter branch", "Apagar branch"]. The worktree is always removed (reference)."""
    CANCEL, KEEP_BRANCH, DELETE_BRANCH = 0, 1, 2
    BUTTONS = ["Cancelar", "Manter branch", "Apagar branch"]

    def __init__(self, parent: Gtk.Window, controller: FloorsController, floor: Floor) -> None:
        self.parent, self.controller, self.floor = parent, controller, floor
        # GtkAlertDialog has no per-button style: "destructive" is conveyed by the wording and by not being the default.
        self.alert = Gtk.AlertDialog(
            message=f"Apagar o floor “{floor.get('name', 'floor')}”?",
            detail=delete_warning(floor, controller.statuses.get(floor.get("id"))),
            buttons=list(self.BUTTONS), cancel_button=self.CANCEL, default_button=self.KEEP_BRANCH, modal=True)

    def present(self) -> None:
        self.alert.choose(self.parent, None, self._chosen)

    def _chosen(self, alert: Gtk.AlertDialog, result) -> None:
        try:
            index = alert.choose_finish(result)
        except GLib.Error:
            index = self.CANCEL   # dismissed
        self.choose(index)

    def choose(self, index: int) -> None:
        if index in (self.KEEP_BRANCH, self.DELETE_BRANCH):
            self.controller.delete(self.floor, delete_branch=index == self.DELETE_BRANCH)


# ---------------------------------------------------------------------------------------------------------------------
# floating button
# ---------------------------------------------------------------------------------------------------------------------
class FloorButton(Gtk.MenuButton):
    """Bottom-right canvas overlay "🏢 <floor ativo | Ground>"; the popover holds the list and the actions."""
    TOAST_MS = 4000

    def __init__(self, window, controller: FloorsController) -> None:
        super().__init__(halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=244, margin_bottom=16,
                         css_classes=["floor-button", "maestri-bottom-pill"], direction=Gtk.ArrowType.UP)
        _install_css()
        self.window, self.controller = window, controller
        self._toast_timer = 0

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.connect("row-activated", lambda _lb, row: self._row_activated(row))
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=44, margin_start=8, margin_end=8)
        self.status.set_visible(False)

        new_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_icon = Gtk.Image.new_from_icon_name("maestri-plus-symbolic")
        new_icon.set_pixel_size(14)
        new_box.append(new_icon)
        new_box.append(Gtk.Label(label="Novo floor…"))
        self.new_btn = Gtk.Button(child=new_box, has_frame=False)
        self.new_btn.get_label = lambda: "＋ Novo floor…"
        self.new_btn.connect("clicked", lambda _b: self.open_new())

        land_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        land_icon = Gtk.Image.new_from_icon_name("maestri-send-symbolic")
        land_icon.set_pixel_size(14)
        land_box.append(land_icon)
        land_box.append(Gtk.Label(label="Land…"))
        self.land_btn = Gtk.Button(child=land_box, has_frame=False)
        self.land_btn.get_label = lambda: "✈ Land…"
        self.land_btn.connect("clicked", lambda _b: self.controller.active_floor and self.open_land(self.controller.active_floor))

        actions = Gtk.Box(spacing=4)
        actions.append(self.new_btn)
        actions.append(self.land_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        box.append(Gtk.Label(label="Floors", xalign=0, css_classes=["floor-popover-title"]))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      max_content_height=360, min_content_width=340)
        scroller.set_child(self.list_box)
        box.append(scroller)
        box.append(self.status)
        box.append(Gtk.Separator())
        box.append(actions)
        self.popover = Gtk.Popover(child=box)
        self.set_popover(self.popover)
        self.popover.connect("show", lambda _p: controller.refresh())

        # Main button content: symbolic icon + floor title
        self.btn_icon = Gtk.Image.new_from_icon_name("maestri-layers-symbolic")
        self.btn_icon.set_pixel_size(15)
        self.btn_label = Gtk.Label(label="Ground")
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.append(self.btn_icon)
        btn_box.append(self.btn_label)
        self.set_child(btn_box)
        self.get_label = lambda: f"🏢 {self.btn_label.get_text()}"

        controller.listeners.append(self.rebuild)
        controller.on_status = self.show_status
        self.rebuild()

    def rebuild(self) -> None:
        c = self.controller
        active = c.active_floor
        name = active.get('name') if active else 'Ground'
        self.btn_label.set_text(name)
        self.set_sensitive(c.available)
        self.set_tooltip_text("Floors: worktrees git isolados deste workspace" if c.available
                              else "Floors precisam do core do Open Maestri (desconectado)")
        self.land_btn.set_sensitive(active is not None)   # Land only from a floor, never from Ground (T2.3)
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        self.list_box.append(self._row(None))
        for f in c.floors:
            self.list_box.append(self._row(f))

    def _row(self, floor: Optional[Floor]) -> Gtk.ListBoxRow:
        c = self.controller
        is_active = (floor is None and c.active_floor_id is None) or (
            floor is not None and c.active_floor_id is not None and str(floor.get("id", "")).upper() == c.active_floor_id.upper())
        row = Gtk.ListBoxRow(activatable=True, css_classes=["floor-row"] + (["active"] if is_active else []))
        row.floor_id = floor.get("id") if floor else None
        h = Gtk.Box(spacing=8)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = floor.get("name", "floor") if floor else "Ground"
        subtitle = floor.get("branchName", "") if floor else c.root
        texts.append(Gtk.Label(label=("✓ " if is_active else "") + title, xalign=0, ellipsize=Pango.EllipsizeMode.END))
        texts.append(Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["floor-branch"]))
        h.append(texts)
        if floor is not None:
            st = c.statuses.get(floor.get("id"))
            row.floor_badge = Gtk.Label(label=badge_text(st), css_classes=["floor-badge"])
            row.floor_dirty = Gtk.Label(label="●", css_classes=["floor-dirty"], tooltip_text="Mudanças sem commit",
                                  visible=bool(st and st.get("dirty")))
            delete = Gtk.Button(label="✕", has_frame=False, tooltip_text="Apagar floor…")
            delete.connect("clicked", lambda _b, f=floor: self.open_delete(f))
            for w in (row.floor_badge, row.floor_dirty, delete):
                h.append(w)
        row.set_child(h)
        return row

    def _row_activated(self, row: Gtk.ListBoxRow) -> None:
        self.controller.switch_to(getattr(row, "floor_id", None))
        self.popover.popdown()

    def show_status(self, text: str, ok: bool) -> None:
        """Toast: shown in the popover and as the button's tooltip, cleared after TOAST_MS."""
        self.status.set_label(text)
        self.status.set_css_classes(["floor-ok" if ok else "floor-error"])
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
    """Wire floors into OpenMaestriWindow: the floating button on the overlay that holds the canvas, a reload on every
    workspace load (initial load and switch) and the /ws/events subscription; both stop when the window closes."""
    controller = FloorsController(window)
    window.floor_button = FloorButton(window, controller)
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
        return False

    hid = bus.connect("workspace-loaded", loaded)
    window.connect("close-request", closed)
    window.connect("destroy", closed)
    return controller
