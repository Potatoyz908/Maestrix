"""
Real-time workspace synchronisation for Open Maestri GTK 4 Native.

Listens to SignalBus events (node moved / resized / added / removed,
connections, selection z-order, viewport) and persists the active
WorkspaceDocument (schemaVersion: 2) through MaestriClient -> REST ->
PersistenceManager.

Concurrency model (mirrors the Swift snapshot pattern):
  * the document is serialised to an immutable dict ON THE GTK MAIN THREAD;
  * the HTTP POST runs on a single background worker thread, so dragging a
    node never blocks on network I/O;
  * only the newest snapshot is kept (latest wins), saves are serialised.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from gi.repository import GLib

from .models.node import current_iso8601
from .models.workspace import WorkspaceDocument
from .signals.bus import SignalBus, bus as default_bus

DEBOUNCE_MS = 400

# Bus signals that mean "the persisted document is now dirty".
DIRTY_SIGNALS = (
    "node-moved",
    "node-resized",
    "node-added",
    "node-updated",
    "node-removed",
    "connection-created",
    "connection-changed",
    "connection-removed",
    "viewport-changed",
)


class WorkspaceSync:
    def __init__(
        self,
        client,
        get_document: Callable[[], WorkspaceDocument],
        get_viewport: Optional[Callable[[], tuple]] = None,
        signal_bus: SignalBus = default_bus,
        debounce_ms: int = DEBOUNCE_MS,
    ) -> None:
        self.client = client
        self._get_document = get_document
        self._get_viewport = get_viewport
        self._bus = signal_bus
        self._debounce_ms = debounce_ms

        self._timer_id: Optional[int] = None
        self._lock = threading.Lock()
        self._pending: Optional[tuple] = None  # (workspace_id, data)
        self._worker: Optional[threading.Thread] = None
        self._handler_ids = []

        for name in DIRTY_SIGNALS:
            self._handler_ids.append(self._bus.connect(name, self._on_dirty))
        self._handler_ids.append(self._bus.connect("node-selected", self._on_selected))

    # -- signal handlers ----------------------------------------------------
    def _on_dirty(self, *_args) -> None:
        self.schedule()

    def _on_selected(self, _bus, node_id: str) -> None:
        # Selecting raises z_index; deselecting ("") changes nothing.
        if node_id:
            self.schedule()

    # -- scheduling ---------------------------------------------------------
    def schedule(self) -> None:
        """(Re)start the debounce timer. Main thread only."""
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(self._debounce_ms, self._on_timeout)

    def _on_timeout(self) -> bool:
        self._timer_id = None
        self._dispatch()
        return GLib.SOURCE_REMOVE

    def flush(self) -> None:
        """Save immediately (cancels the debounce timer). Main thread only."""
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._dispatch()

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending is not None or (
                self._worker is not None and self._worker.is_alive()
            )

    def wait_idle(self, timeout: float = 5.0) -> None:
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def close(self) -> None:
        """Flush outstanding changes and disconnect from the bus."""
        self.flush()
        self.wait_idle()
        for hid in self._handler_ids:
            self._bus.disconnect(hid)
        self._handler_ids.clear()

    # -- snapshot + background I/O -----------------------------------------
    def snapshot(self) -> tuple:
        """Build an immutable (workspace_id, dict) snapshot on the main thread."""
        doc = self._get_document()
        doc.schema_version = 2
        doc.type = "workspace"
        if self._get_viewport is not None:
            ox, oy, zoom = self._get_viewport()
            doc.payload.canvas_origin = [float(ox), float(oy)]
            doc.payload.canvas_zoom = float(zoom)
        doc.payload.last_modified_at = current_iso8601()
        return doc.payload.id, doc.to_dict()

    def _dispatch(self) -> None:
        workspace_id, data = self.snapshot()
        with self._lock:
            self._pending = (workspace_id, data)
            if self._worker is not None and self._worker.is_alive():
                return  # running worker will pick up the newest snapshot
            self._worker = threading.Thread(
                target=self._run, name="maestri-workspace-sync", daemon=True
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                item, self._pending = self._pending, None
            if item is None:
                return
            workspace_id, data = item
            ok = False
            try:
                ok = bool(self.client.save_workspace_dict(data, workspace_id))
            except Exception:
                ok = False
            if ok:
                GLib.idle_add(self._emit_saved, workspace_id)
            else:
                GLib.idle_add(self._emit_failed)

    def _emit_saved(self, workspace_id: str) -> bool:
        self._bus.emit("workspace-saved", workspace_id)
        return GLib.SOURCE_REMOVE

    def _emit_failed(self) -> bool:
        self._bus.emit("status-changed", "save-failed", False)
        return GLib.SOURCE_REMOVE
