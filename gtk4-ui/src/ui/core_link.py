"""Core reachability probe for the GTK window (Sprint 6 R1).

The window must not give up on the core when it was offline at startup or drops later: `CoreReconnector` probes
`client.check_connection_status()` on a worker thread every `interval_s` seconds while started, and hands the result
("connected" | "unauthorized" | "offline") to `on_status` on the GTK main loop. `probe(callback)` runs a single check (used
to tell "the core is gone" from "the core refused this save"). After `dispose()` no callback fires anymore.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from gi.repository import GLib

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
