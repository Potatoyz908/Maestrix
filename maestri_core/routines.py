"""
Routines for Open Maestri GTK 4 (spec: docs/linux-gtk-sprint4-spec.md §2, reference docs/reference/official/11-routines.md).

* `Routine` mirrors the Swift model field for field (`id, name, prompts[], intervalSeconds, targetTerminalId, isActive,
  createdAt`), and `dump_container` / `load_container` match `RoutinesContainer` (writes `payload`, reads `payload` or the
  older `routines` key), so `routines.json` round-trips with the Swift core.
* `parse_prompts`: `&&` chains prompts only when it stands on its own line (`make && make test` is ONE prompt).
* `RoutineScheduler`: pure, clock-driven state machine (`tick(now)`), so it is tested with a fake clock. Rules:
  interval >= 60 s; the next chained prompt is sent only once the target is idle (3 s grace, 300 s timeout, like Swift);
  no overlap (a tick that fires while the previous run is still going is skipped and logged); a routine whose target
  terminal no longer exists is paused and `routineFailed` is emitted. `GLibDriver` ticks it from the GTK main loop.
* `RoutinesClient`: REST client for `/api/routines` (the core owns the persisted state).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

log = logging.getLogger("maestri.routines")

MIN_INTERVAL_SECONDS = 60.0
IDLE_GRACE_SECONDS = 3.0
IDLE_TIMEOUT_SECONDS = 300.0
SEPARATOR = "&&"


# ---------------------------------------------------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------------------------------------------------
def parse_prompts(text: str) -> List[str]:
    prompts: List[str] = []
    current: List[str] = []

    def flush() -> None:
        joined = "\n".join(current).strip()
        if joined:
            prompts.append(joined)
        current.clear()

    for line in text.splitlines():
        if line.strip() == SEPARATOR:
            flush()
        else:
            current.append(line)
    flush()
    return prompts


def join_prompts(prompts: Iterable[str]) -> str:
    """Inverse of parse_prompts, for the editor."""
    return f"\n{SEPARATOR}\n".join(prompts)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: Any) -> datetime:
    if isinstance(s, (int, float)):        # Swift's default Date strategy: seconds since 2001-01-01
        return datetime.fromtimestamp(978307200 + float(s), timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


@dataclass
class Routine:
    name: str
    prompts: List[str]
    interval_seconds: float
    target_terminal_id: str
    is_active: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))

    @classmethod
    def create(cls, name: str, prompt: str, interval_seconds: float, target_terminal_id: str) -> "Routine":
        return cls(name=name, prompts=parse_prompts(prompt), interval_seconds=float(interval_seconds),
                   target_terminal_id=str(target_terminal_id).upper())

    @property
    def prompt_text(self) -> str:
        return join_prompts(self.prompts)

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("routine name is empty")
        if not self.prompts:
            raise ValueError("routine prompt is empty")
        if self.interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval must be at least {int(MIN_INTERVAL_SECONDS)} s")
        uuid.UUID(self.target_terminal_id)   # raises ValueError

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompts": list(self.prompts),
            "intervalSeconds": self.interval_seconds,
            "targetTerminalId": self.target_terminal_id,
            "isActive": self.is_active,
            "createdAt": _iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Routine":
        prompts = d.get("prompts")
        if prompts is None and "prompt" in d:
            prompts = parse_prompts(str(d["prompt"]))
        return cls(
            id=str(d.get("id") or uuid.uuid4()).upper(),
            name=str(d.get("name", "")),
            prompts=[str(p) for p in (prompts or [])],
            interval_seconds=float(d.get("intervalSeconds", MIN_INTERVAL_SECONDS)),
            target_terminal_id=str(d.get("targetTerminalId", "")).upper(),
            is_active=bool(d.get("isActive", True)),
            created_at=_parse_iso(d.get("createdAt", _iso(datetime.now(timezone.utc)))),
        )


def dump_container(routines: Iterable[Routine]) -> Dict[str, Any]:
    return {"payload": [r.to_dict() for r in routines]}


def load_container(data: Any) -> List[Routine]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("routines")
        if not isinstance(items, list):
            items = data.get("payload")
        if not isinstance(items, list):
            items = []
    else:
        items = []
    return [Routine.from_dict(d) for d in items if isinstance(d, dict)]


def describe_interval(seconds: float) -> str:
    s = int(seconds)
    if s % 3600 == 0:
        return f"every {s // 3600}h"
    if s % 60 == 0:
        return f"every {s // 60}m"
    return f"every {s}s"


# ---------------------------------------------------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class _Run:
    step: int = 0                  # index of the next prompt to send
    wait_since: float = 0.0        # when the previous prompt went out


class RoutineScheduler:
    """`send(terminal_id, text) -> bool` (False: no such terminal), `is_idle(terminal_id) -> bool`,
    `on_event(kind, routine_id, info)` with kind in routineStarted / routineStep / routineFinished / routineFailed /
    routineSkipped / routinePaused."""

    def __init__(self, send: Callable[[str, str], bool], is_idle: Callable[[str], bool],
                 on_event: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
                 clock: Callable[[], float] = time.monotonic, grace: float = IDLE_GRACE_SECONDS,
                 idle_timeout: float = IDLE_TIMEOUT_SECONDS) -> None:
        self.send = send
        self.is_idle = is_idle
        self.on_event = on_event or (lambda *_: None)
        self.clock = clock
        self.grace = grace
        self.idle_timeout = idle_timeout
        self.routines: Dict[str, Routine] = {}
        self.next_due: Dict[str, float] = {}
        self.runs: Dict[str, _Run] = {}
        self.last_run: Dict[str, float] = {}

    # ---- set management ------------------------------------------------------------------------------------------
    def set_routines(self, routines: Iterable[Routine]) -> None:
        """Replace the set (e.g. after a REST refresh). Timers of unchanged active routines keep their phase."""
        now = self.clock()
        new = {r.id: r for r in routines}
        for rid in list(self.routines):
            if rid not in new:
                self._forget(rid)
        for rid, r in new.items():
            old = self.routines.get(rid)
            self.routines[rid] = r
            if not r.is_active:
                self.next_due.pop(rid, None)
            elif old is None or not old.is_active or old.interval_seconds != r.interval_seconds or rid not in self.next_due:
                self.next_due[rid] = now + max(r.interval_seconds, MIN_INTERVAL_SECONDS)

    def upsert(self, routine: Routine) -> None:
        others = [r for rid, r in self.routines.items() if rid != routine.id]
        self.set_routines(others + [routine])

    def remove(self, routine_id: str) -> None:
        self._forget(routine_id)

    def pause(self, routine_id: str) -> None:
        r = self.routines.get(routine_id)
        if r is not None:
            self.routines[routine_id] = replace(r, is_active=False)
            self.next_due.pop(routine_id, None)

    def resume(self, routine_id: str) -> None:
        r = self.routines.get(routine_id)
        if r is not None and not r.is_active:
            self.upsert(replace(r, is_active=True))

    def is_running(self, routine_id: str) -> bool:
        return routine_id in self.runs

    def run_now(self, routine_id: str) -> bool:
        """Start a run immediately (does not move the periodic schedule). False when already running or unknown."""
        r = self.routines.get(routine_id)
        if r is None or routine_id in self.runs:
            return False
        self._start(r, self.clock())
        return True

    def _forget(self, rid: str) -> None:
        self.routines.pop(rid, None)
        self.next_due.pop(rid, None)
        self.runs.pop(rid, None)

    # ---- clock ------------------------------------------------------------------------------------------------------
    def tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        for rid, due in list(self.next_due.items()):
            r = self.routines.get(rid)
            if r is None or now < due:
                continue
            interval = max(r.interval_seconds, MIN_INTERVAL_SECONDS)
            # catch up without a burst: the next slot is the first one after `now`
            missed = int((now - due) // interval)
            self.next_due[rid] = due + (missed + 1) * interval
            if rid in self.runs:
                log.info("routine %s (%s): previous run still in progress, tick skipped", r.name, rid)
                self.on_event("routineSkipped", rid, {"reason": "overlap"})
                continue
            self._start(r, now)
        for rid in list(self.runs):
            self._advance(rid, now)

    def _start(self, r: Routine, now: float) -> None:
        self.runs[r.id] = _Run()
        self.last_run[r.id] = now
        self.on_event("routineStarted", r.id, {"n": len(r.prompts)})
        self._advance(r.id, now)

    def _advance(self, rid: str, now: float) -> None:
        run = self.runs.get(rid)
        r = self.routines.get(rid)
        if run is None or r is None:
            self.runs.pop(rid, None)
            return
        while run.step < len(r.prompts):
            if run.step > 0:
                waited = now - run.wait_since
                if waited < self.grace:
                    return
                if not self.is_idle(r.target_terminal_id):
                    if waited < self.grace + self.idle_timeout:
                        return
                    log.warning("routine %s: agent not idle after %ss, sending next prompt anyway", r.name, self.idle_timeout)
            if not self.send(r.target_terminal_id, r.prompts[run.step]):
                self._fail(r, "target terminal not found")
                return
            run.step += 1
            run.wait_since = now
            self.on_event("routineStep", rid, {"i": run.step, "n": len(r.prompts)})
        del self.runs[rid]
        self.on_event("routineFinished", rid, {})

    def _fail(self, r: Routine, reason: str) -> None:
        self.runs.pop(r.id, None)
        self.pause(r.id)
        log.warning("routine %s paused: %s", r.name, reason)
        self.on_event("routineFailed", r.id, {"reason": reason})
        self.on_event("routinePaused", r.id, {"reason": reason})


class GLibDriver:
    """Ticks a scheduler from the GLib main loop (send/is_idle then run on the GTK thread, where terminals live)."""

    def __init__(self, scheduler: RoutineScheduler, period_ms: int = 500) -> None:
        from gi.repository import GLib
        self._GLib = GLib
        self.scheduler = scheduler
        self._id = GLib.timeout_add(period_ms, self._tick)

    def _tick(self) -> bool:
        try:
            self.scheduler.tick()
        except Exception:   # a bad routine must never kill the timer
            log.exception("routine tick failed")
        return True

    def stop(self) -> None:
        if self._id:
            self._GLib.source_remove(self._id)
            self._id = 0


# ---------------------------------------------------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------------------------------------------------
class RoutinesClient:
    """`/api/routines` on the core, through an existing MaestriClient (auth headers, base URL, error mapping).
    Blocking: call from a worker thread."""

    def __init__(self, client) -> None:
        self.client = client

    def _req(self, method: str, path: str, json: Optional[Dict[str, Any]] = None):
        c = self.client
        headers = c._headers({"Content-Type": "application/json"} if json is not None else None)
        resp = c.session.request(method, f"{c.base_url}{path}", json=json, headers=headers, timeout=c.timeout)
        return c._handle_response(resp)

    def list(self) -> List[Routine]:
        return load_container(self._req("GET", "/api/routines").json())

    def create(self, r: Routine) -> Routine:
        body = {"name": r.name, "prompt": r.prompt_text, "intervalSeconds": r.interval_seconds,
                "targetTerminalId": r.target_terminal_id}
        created = Routine.from_dict(self._req("POST", "/api/routines", body).json())
        if not r.is_active:
            created = self.set_active(created.id, False)
        return created

    def update(self, r: Routine) -> Routine:
        """Full edit. Sends every field; a core that only patches name/isActive is detected and the routine is
        re-created (keeping it consistent with what the user saved)."""
        body = {"name": r.name, "prompt": r.prompt_text, "prompts": r.prompts, "intervalSeconds": r.interval_seconds,
                "targetTerminalId": r.target_terminal_id, "isActive": r.is_active}
        got = Routine.from_dict(self._req("PATCH", f"/api/routines/{r.id}", body).json())
        if (got.prompts, got.interval_seconds, got.target_terminal_id) != (r.prompts, r.interval_seconds, r.target_terminal_id):
            self.delete(r.id)
            got = self.create(r)
        return got

    def run_now(self, routine_id: str) -> None:
        """The server owns PTYs in every workspace and waits for idle between prompts."""
        self._req("POST", f"/api/routines/{routine_id}/run-now")

    def set_active(self, routine_id: str, active: bool) -> Routine:
        return Routine.from_dict(self._req("PATCH", f"/api/routines/{routine_id}", {"isActive": active}).json())

    def delete(self, routine_id: str) -> None:
        self._req("DELETE", f"/api/routines/{routine_id}")
