"""Debounced, serialized note writer (pure: timers and the write function are injected).

Only the latest text is ever written; at most one write is in flight (a follow-up goes out when it finishes if the user kept typing);
after an error the text stays pending and is retried on the next schedule()/flush()."""
from __future__ import annotations

from typing import Callable

WriteFn = Callable[[str, Callable[[Exception | None], None]], None]   # write(text, done(err))
STATES = ("idle", "dirty", "saving", "saved", "error")


class NoteSaver:
    def __init__(self, write: WriteFn, debounce_ms: int = 500, on_state: Callable[[str, Exception | None], None] | None = None,
                 set_timer: Callable[[Callable[[], None], int], object] | None = None, clear_timer: Callable[[object], None] | None = None) -> None:
        self._write, self._ms, self._on_state = write, debounce_ms, on_state
        self._set, self._clear = set_timer, clear_timer
        self._pending: str | None = None
        self._timer: object | None = None
        self._in_flight = False
        self._waiters: list[Callable[[], None]] = []
        self.state = "idle"
        self.last_error: Exception | None = None

    # ---- public --------------------------------------------------------------------------------
    def schedule(self, text: str) -> None:
        self._pending = text
        self._set_state("dirty")
        if self._timer is not None:
            self._clear(self._timer)
        self._timer = self._set(self._fire, self._ms)

    def flush(self, done: Callable[[], None] | None = None) -> None:
        """Write now (blur, close). `done` runs once nothing is pending or in flight (also after an error: check `state`)."""
        if done:
            self._waiters.append(done)
        if self._timer is not None:
            self._clear(self._timer)
            self._timer = None
        self._pump()

    @property
    def dirty(self) -> bool:
        return self._pending is not None or self._in_flight

    # ---- internals -----------------------------------------------------------------------------
    def _fire(self) -> None:
        self._timer = None
        self._pump()

    def _set_state(self, s: str, err: Exception | None = None) -> None:
        self.state = s
        if err is not None:
            self.last_error = err
        if self._on_state:
            self._on_state(s, err)

    def _pump(self) -> None:
        if self._in_flight:
            return
        if self._pending is None:
            self._release()
            return
        text, self._pending = self._pending, None
        self._in_flight = True
        self._set_state("saving")

        def finished(err: Exception | None) -> None:
            self._in_flight = False
            if err is not None:
                if self._pending is None:
                    self._pending = text   # keep it: the next schedule/flush retries (a newer text, if any, wins)
                self._set_state("error", err)
                self._release()
                return
            self._set_state("dirty" if self._pending is not None else "saved")
            self._pump()

        self._write(text, finished)

    def _release(self) -> None:
        waiters, self._waiters = self._waiters, []
        for w in waiters:
            w()
