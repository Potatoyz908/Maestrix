"""Core reachability probe for the GTK window (Sprint 6 R1) and the sidebar's online/offline workspace store (Sprint 8).

The window must not give up on the core when it was offline at startup or drops later: `CoreReconnector` probes
`client.check_connection_status()` on a worker thread every `interval_s` seconds while started, and hands the result
("connected" | "unauthorized" | "offline") to `on_status` on the GTK main loop. `probe(callback)` runs a single check (used
to tell "the core is gone" from "the core refused this save"). After `dispose()` no callback fires anymore.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from gi.repository import GLib

from maestri_core.models.workspace import SidebarGroup, SidebarLayout, WorkspaceEntry

RECONNECT_INTERVAL_S = 5


class CoreReconnector:
    def __init__(self, client, on_status: Callable[[str], None], interval_s: int = RECONNECT_INTERVAL_S) -> None:
        self.client = client
        self.on_status = on_status
        self.interval_s = max(1, int(interval_s))
        self._timer = 0
        self._inflight = False
        self._disposed = False

    @property
    def running(self) -> bool:
        return bool(self._timer)

    def probe(self, callback: Callable[[str], None]) -> None:
        """One check off the main thread; `callback(status)` runs on the main loop (never after dispose)."""
        client = self.client

        def worker() -> None:
            try:
                status = client.check_connection_status()
            except Exception:   # noqa: BLE001 - any failure to reach the core means offline
                status = "offline"

            def deliver() -> bool:
                if not self._disposed:
                    callback(status)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(deliver)

        threading.Thread(target=worker, name="maestri-core-probe", daemon=True).start()

    def start(self) -> None:
        """Probe every `interval_s` until stop(); idempotent."""
        if self._timer or self._disposed:
            return
        self._timer = GLib.timeout_add_seconds(self.interval_s, self._tick)

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def dispose(self) -> None:
        self.stop()
        self._disposed = True

    def _tick(self) -> bool:
        if not self._inflight:   # a slow probe (1.5 s timeout) never stacks up
            self._inflight = True
            self.probe(self._probed)
        return GLib.SOURCE_CONTINUE

    def _probed(self, status: str) -> None:
        self._inflight = False
        if self._timer:
            self.on_status(status)


# ---- Sidebar data (Sprint 8 C1-C3): online first, ~/.open-maestri on disk when the core is unreachable ---------------------------
#
# Every mutation tries the core's REST API first (the core then writes manifest.json / sidebar-layout.json / workspace.json itself)
# and, when that fails (offline, or the core does not know the workspace), applies the same change to the files the core would
# have written, atomically and with the same rules as Sources/InterAgent/InterAgentServer.swift. Pure functions and plain I/O:
# safe to call from worker threads, no GTK.

def data_root() -> str:
    from maestri_core.paths import data_dir   # OPEN_MAESTRI_DATA_DIR, else ~/.open-maestri
    return str(data_dir())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def count_terminals(nodes: Iterable[Any]) -> int:
    """Terminal nodes in a payload (`CanvasNode` objects or schema-v2 dicts `{"content": {"terminal": {...}}}`), like
    WorkspaceManager.terminalCount on macOS."""
    n = 0
    for node in nodes or ():
        if isinstance(node, dict):
            content = node.get("content")
            n += isinstance(content, dict) and "terminal" in content
        else:
            n += getattr(node, "node_type", None) == "terminal"
    return n


class WorkspaceStore:
    """Workspaces, sidebar layout and per-workspace terminal counts for the sidebar."""

    def __init__(self, client, root: Optional[str] = None) -> None:
        self.client = client
        self.root = root or data_root()
        self._count_cache: Dict[str, tuple[float, int]] = {}   # ws id -> (workspace.json mtime, terminal count)
        self._lock = threading.Lock()

    # ---- paths -------------------------------------------------------------------------------------------------------------
    @property
    def manifest_path(self) -> str:
        return os.path.join(self.root, "manifest.json")

    @property
    def layout_path(self) -> str:
        return os.path.join(self.root, "sidebar-layout.json")

    def workspace_dir(self, ws_id: str) -> str:
        return os.path.join(self.root, "workspaces", ws_id.upper())

    def workspace_file(self, ws_id: str) -> str:
        return os.path.join(self.workspace_dir(ws_id), "workspace.json")

    # ---- manifest ------------------------------------------------------------------------------------------------------------
    def _load_manifest(self) -> dict:
        data = _read_json(self.manifest_path)
        if not isinstance(data, dict) or not isinstance(data.get("workspaces"), list):
            data = {"app": "open-maestri", "appVersion": "1.0.0", "dataFormat": 2, "files": {}, "schemaVersion": 1,
                    "type": "appState", "workspaces": []}
        return data

    def local_workspaces(self) -> List[WorkspaceEntry]:
        return [WorkspaceEntry.from_dict(w) for w in self._load_manifest()["workspaces"] if isinstance(w, dict) and w.get("id")]

    def list_workspaces(self) -> List[WorkspaceEntry]:
        try:
            online = self.client.get_workspaces()
        except Exception:   # noqa: BLE001 - auth failures included: the local manifest still lists them
            online = []
        return list(online) or self.local_workspaces()

    def _edit_manifest(self, fn) -> Any:
        with self._lock:
            m = self._load_manifest()
            result = fn(m["workspaces"])
            _write_json(self.manifest_path, m)
            return result

    # ---- workspace mutations -------------------------------------------------------------------------------------------------
    def update_workspace(self, ws_id: str, **fields: Any) -> bool:
        """fields: name, working_directory, icon, color, is_pinned (None = unchanged). True when persisted somewhere."""
        fields = {k: v for k, v in fields.items() if v is not None}
        try:
            if self.client.update_workspace(ws_id, **fields) is not None:
                return True
        except Exception:   # noqa: BLE001
            pass
        keys = {"name": "name", "working_directory": "workingDirectory", "icon": "icon", "color": "color", "is_pinned": "isPinned"}
        patch = {keys[k]: v for k, v in fields.items() if k in keys and not (k == "name" and not v)}

        def apply(items: list) -> bool:
            for w in items:
                if str(w.get("id", "")).upper() == ws_id.upper():
                    w.update(patch)
                    break
            else:
                return False
            if "isPinned" in patch:   # macOS keeps pinned workspaces first in the manifest
                items.sort(key=lambda w: not w.get("isPinned", False))
            return True

        if not self._edit_manifest(apply):
            return False
        doc = _read_json(self.workspace_file(ws_id))   # the core mirrors name/directory/icon into the document as well
        if isinstance(doc, dict) and isinstance(doc.get("payload"), dict):
            doc["payload"].update({k: v for k, v in patch.items() if k in ("name", "workingDirectory", "icon", "color", "isPinned")})
            _write_json(self.workspace_file(ws_id), doc)
        return True

    def duplicate_workspace(self, ws_id: str) -> Optional[str]:
        """New workspace id ("<name> Copy", unpinned, document cloned with a new payload id), or None."""
        try:
            res = self.client.duplicate_workspace(ws_id)
            if res is not None and getattr(res, "id", None):
                return str(res.id).upper()
        except Exception:   # noqa: BLE001
            pass
        new_id = str(uuid.uuid4()).upper()

        def apply(items: list) -> Optional[dict]:
            src = next((w for w in items if str(w.get("id", "")).upper() == ws_id.upper()), None)
            if src is None:
                return None
            entry = dict(src, id=new_id, name=f"{src.get('name', 'Workspace')} Copy", isPinned=False, createdAt=_now_iso())
            entry.pop("lastOpenedAt", None)
            items.append(entry)
            return entry

        entry = self._edit_manifest(apply)
        if entry is None:
            return None
        doc = _read_json(self.workspace_file(ws_id))
        if not (isinstance(doc, dict) and isinstance(doc.get("payload"), dict)):
            doc = {"schemaVersion": 2, "type": "workspace", "payload": {
                "workingDirectory": entry.get("workingDirectory", ""), "icon": entry.get("icon", "folder"),
                "canvasOrigin": [9800, 8500], "canvasZoom": 1, "nodes": [], "connections": [], "createdAt": _now_iso()}}
        doc["payload"].update(id=new_id, name=entry["name"], isPinned=False, lastModifiedAt=_now_iso())
        _write_json(self.workspace_file(new_id), doc)
        os.makedirs(os.path.join(self.workspace_dir(new_id), "notes"), exist_ok=True)
        layout = self.load_layout()
        if new_id not in {i.upper() for i in layout.top_level_items}:
            layout.top_level_items.append(new_id)
            self._write_layout(layout)
        return new_id

    def delete_workspace(self, ws_id: str) -> bool:
        """Removes it from the manifest and the sidebar layout and deletes its directory (what the core's DELETE does)."""
        try:
            if self.client.delete_workspace(ws_id):
                self._count_cache.pop(ws_id.upper(), None)
                return True
        except Exception:   # noqa: BLE001
            pass

        def apply(items: list) -> bool:
            before = len(items)
            items[:] = [w for w in items if str(w.get("id", "")).upper() != ws_id.upper()]
            return len(items) != before

        removed = self._edit_manifest(apply)
        layout = self.load_layout()
        if remove_from_layout(layout, ws_id):
            self._write_layout(layout)
        d = self.workspace_dir(ws_id)
        if os.path.isdir(d) and os.path.dirname(d) == os.path.join(self.root, "workspaces"):
            shutil.rmtree(d, ignore_errors=True)
        self._count_cache.pop(ws_id.upper(), None)
        return removed

    # ---- sidebar layout ------------------------------------------------------------------------------------------------------
    def load_layout(self) -> SidebarLayout:
        try:
            data = self.client.get_sidebar_layout()   # SidebarLayout or a raw dict, depending on the client version
            if isinstance(data, SidebarLayout):
                if data.groups or data.top_level_items or not os.path.isfile(self.layout_path):
                    return data
            elif isinstance(data, dict) and data:
                return SidebarLayout.from_dict(data)
        except Exception:   # noqa: BLE001 - offline / unauthorized: the file on disk
            pass
        data = _read_json(self.layout_path)
        return SidebarLayout.from_dict(data) if isinstance(data, dict) else SidebarLayout()

    def _write_layout(self, layout: SidebarLayout) -> None:
        with self._lock:
            _write_json(self.layout_path, layout.to_dict())

    def save_layout(self, layout: SidebarLayout) -> bool:
        data = layout.to_dict()
        try:
            if self.client.save_sidebar_layout(data):   # the core validates and writes sidebar-layout.json
                return True
        except Exception:   # noqa: BLE001 - offline, or an older client that wants a SidebarLayout object
            try:
                if self.client.save_sidebar_layout(layout):
                    return True
            except Exception:   # noqa: BLE001
                pass
        self._write_layout(layout)
        return True

    # ---- terminal counts -----------------------------------------------------------------------------------------------------
    def terminal_count(self, ws_id: str) -> int:
        """Terminal nodes saved in workspaces/<id>/workspace.json (re-parsed only when the file changed)."""
        path = self.workspace_file(ws_id)
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return 0
        key = ws_id.upper()
        cached = self._count_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        doc = _read_json(path)
        payload = doc.get("payload") if isinstance(doc, dict) else None
        count = count_terminals(payload.get("nodes", [])) if isinstance(payload, dict) else 0
        self._count_cache[key] = (mtime, count)
        return count

    def terminal_counts(self, ids: Iterable[str]) -> Dict[str, int]:
        return {i.upper(): self.terminal_count(i) for i in ids}

    def counts_for(self, entries: Iterable[WorkspaceEntry]) -> Dict[str, int]:
        """The core's "terminalCount" when the entry carries it (GET /api/workspaces on newer cores), else workspace.json."""
        out: Dict[str, int] = {}
        for e in entries:
            reported = entry_terminal_count(e)
            out[e.id.upper()] = reported if reported is not None else self.terminal_count(e.id)
        return out


def entry_terminal_count(entry: Any) -> Optional[int]:
    """"terminalCount" from a core response (WorkspaceEntry keeps unknown keys in _extra); None when the core did not send it."""
    extra = getattr(entry, "_extra", None)
    value = extra.get("terminalCount") if isinstance(extra, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---- layout edits (pure; mirror WorkspaceSidebarView.swift) ------------------------------------------------------------------------
def remove_from_layout(layout: SidebarLayout, ws_id: str) -> bool:
    key, changed = ws_id.upper(), False
    if any(i.upper() == key for i in layout.top_level_items):
        layout.top_level_items = [i for i in layout.top_level_items if i.upper() != key]
        changed = True
    for g in layout.groups:
        if any(i.upper() == key for i in g.items):
            g.items = [i for i in g.items if i.upper() != key]
            changed = True
    return changed


def move_to_group(layout: SidebarLayout, ws_id: str, group_id: str) -> bool:
    target = next((g for g in layout.groups if g.id.upper() == group_id.upper()), None)
    if target is None:
        return False
    remove_from_layout(layout, ws_id)
    target.items.append(ws_id.upper())
    return True


def remove_from_group(layout: SidebarLayout, ws_id: str) -> None:
    remove_from_layout(layout, ws_id)
    layout.top_level_items.append(ws_id.upper())


def new_group(layout: SidebarLayout, name: str, ws_id: Optional[str] = None) -> SidebarGroup:
    g = SidebarGroup(name=name, items=[])
    layout.groups.append(g)
    if ws_id:
        move_to_group(layout, ws_id, g.id)
    return g


def delete_group(layout: SidebarLayout, group_id: str) -> None:
    """The group's workspaces go back to the top level (they are never deleted with it)."""
    for g in [g for g in layout.groups if g.id.upper() == group_id.upper()]:
        layout.top_level_items.extend(i.upper() for i in g.items if i.upper() not in {t.upper() for t in layout.top_level_items})
    layout.groups = [g for g in layout.groups if g.id.upper() != group_id.upper()]
