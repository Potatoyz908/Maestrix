"""Which terminals are working / have an answer the user has not read, for EVERY workspace (not only the open one).

The core keeps every PTY alive and reports `lastOutputAt` (GET /api/terminals), so a workspace whose canvas is closed can still
show "an agent finished". `AttentionTracker` is the pure logic; `AttentionMonitor` polls the core on a worker thread.

  working = the PTY printed within WORKING_S
  unread  = it printed after the user last looked at it, and it has been quiet for SETTLE_S (the answer is complete)
  looked at = the terminal has the keyboard (focused) or the user marked it read; first sight of a terminal is a baseline.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Optional

from gi.repository import GLib

WORKING_S = 2.0
SETTLE_S = 1.5
POLL_S = 1.5


class AttentionTracker:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}     # terminal id (upper) -> latest core row
        self.seen: dict[str, float] = {}    # terminal id -> last output time the user has seen

    def update(self, rows: Iterable[dict], now: float, focused: Iterable[str] = ()) -> None:
        current: dict[str, dict] = {}
        for r in rows:
            tid = str(r.get("terminalId") or "").upper()
            if tid:
                current[tid] = r
        self.rows = current
        for tid, r in current.items():
            last = float(r.get("lastOutputAt") or 0.0)
            if tid not in self.seen:
                self.seen[tid] = last          # baseline: what is already there is not news
        for tid in focused:
            tid = tid.upper()
            if tid in current:
                self.seen[tid] = max(self.seen.get(tid, 0.0), now, float(current[tid].get("lastOutputAt") or 0.0))
        for tid in [t for t in self.seen if t not in current]:
            del self.seen[tid]

    def mark_seen(self, tid: str, now: Optional[float] = None) -> None:
        tid = tid.upper()
        row = self.rows.get(tid)
        self.seen[tid] = max(now if now is not None else time.time(), float((row or {}).get("lastOutputAt") or 0.0))

    def state(self, tid: str, now: float) -> str:
        """busy / unread / exited / idle."""
        r = self.rows.get(tid.upper())
        if r is None:
            return "idle"
        if r.get("running") is False:
            return "exited"
        last = float(r.get("lastOutputAt") or 0.0)
        if last and now - last < WORKING_S:
            return "busy"
        if last > self.seen.get(tid.upper(), last) and now - last >= SETTLE_S:
            return "unread"
        return "idle"

    def summary(self, ws_id: str, now: float) -> tuple[int, int]:
        """(working, unread) terminals of a workspace."""
        busy = unread = 0
        for tid, r in self.rows.items():
            if str(r.get("workspaceId") or "").upper() != ws_id.upper():
                continue
            st = self.state(tid, now)
            busy += st == "busy"
            unread += st == "unread"
        return busy, unread

    def terminals(self, ws_id: str, now: float) -> list[dict]:
        out = []
        for tid, r in self.rows.items():
            if str(r.get("workspaceId") or "").upper() == ws_id.upper():
                out.append({"id": tid, "name": r.get("displayName") or "Terminal", "state": self.state(tid, now),
                            "unread": self.state(tid, now) == "unread"})
        return sorted(out, key=lambda a: a["name"].lower())


class AttentionMonitor:
    def __init__(self, client, focused: Callable[[], Iterable[str]], on_update: Callable[[], None]) -> None:
        self.client, self.focused, self.on_update = client, focused, on_update
        self.tracker = AttentionTracker()
        self._lock = threading.Lock()
        self._timer = 0
        self._inflight = False

    def start(self) -> None:
        if not self._timer:
            self._timer = GLib.timeout_add(int(POLL_S * 1000), self._tick)
            self._tick()

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self) -> bool:
        if self._inflight:
            return GLib.SOURCE_CONTINUE
        self._inflight = True
        threading.Thread(target=self._fetch, name="maestri-attention", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _fetch(self) -> None:
        try:
            rows = self.client.list_terminals()
        except Exception:   # noqa: BLE001 - core down / auth: keep the last picture
            rows = None
        GLib.idle_add(self._apply, rows)

    def _apply(self, rows) -> bool:
        self._inflight = False
        if rows is not None and self._timer:
            self.tracker.update(rows, time.time(), self.focused())
            self.on_update()
        return GLib.SOURCE_REMOVE

    # thread-free helpers for the UI
    def summary(self, ws_id: str) -> tuple[int, int]:
        return self.tracker.summary(ws_id, time.time())

    def terminals(self, ws_id: str) -> list[dict]:
        return self.tracker.terminals(ws_id, time.time())

    def mark_seen(self, tid: str) -> None:
        self.tracker.mark_seen(tid)
