"""Plan-usage ("quota") of the coding agents, read the way Maestri's shipped usage providers do.

Each provider asks the vendor CLI itself; nothing here reads a token or calls a vendor API directly, and nothing starts a
model turn:

* claude       `claude -p --input-format stream-json ...` + a `get_usage` control request  -> 5-hour / weekly windows
* codex        `codex app-server` JSON-RPC `account/rateLimits/read`                         -> 5-hour / weekly windows
* antigravity  `agy -p /usage --output-format json`                                           -> per model group windows

`UsageMonitor` polls them from a daemon thread (each provider at least 60 s apart, failures back off up to 30 min), so the
GUI only reads `snapshot()`. Unknown values stay None - never shown as 0 %.
"""
from __future__ import annotations

import json
import math
import os
import select
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

MIN_GAP_S = 60
MAX_BACKOFF_S = 1800
INTERVALS = {"claude": 300, "codex": 300, "antigravity": 600}
NAMES = {"claude": "Claude Code", "codex": "Codex", "antigravity": "Antigravity"}
EXECUTABLES = {"claude": "claude", "codex": "codex", "antigravity": "agy"}


_path_cache: Optional[str] = None


def agent_path() -> str:
    """PATH for finding and running the agent CLIs. A GUI started from the desktop has a minimal PATH without nvm, ~/.local/bin
    or npm globals, so (like Maestri) add the login shell's PATH and the common install folders."""
    global _path_cache
    if _path_cache is not None:
        return _path_cache
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        out = subprocess.run([shell, "-lic", "printf %s \"$PATH\""], capture_output=True, text=True, timeout=8,
                             stdin=subprocess.DEVNULL).stdout
        parts += [p for p in out.strip().splitlines()[-1].split(os.pathsep) if p] if out.strip() else []
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    home = os.path.expanduser("~")
    extra = [f"{home}/.local/bin", f"{home}/.npm-global/bin", f"{home}/.bun/bin", f"{home}/.cargo/bin",
             "/usr/local/bin", "/usr/bin", "/bin", "/snap/bin"]
    nvm = os.path.join(home, ".nvm", "versions", "node")
    if os.path.isdir(nvm):
        extra += sorted((os.path.join(nvm, v, "bin") for v in os.listdir(nvm)), reverse=True)
    seen, merged = set(), []
    for p in parts + extra:
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            merged.append(p)
    _path_cache = os.pathsep.join(merged)
    return _path_cache


def find_executable(name: str) -> Optional[str]:
    return shutil.which(name, path=agent_path())


def agent_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = agent_path()
    env.update(TERM="dumb", NO_COLOR="1")
    return env


@dataclass
class UsageWindow:
    label: str
    used: Optional[float]          # 0..100, consumed share of the window; None = unknown
    resets_at: Optional[float] = None   # epoch seconds
    duration_s: Optional[int] = None


@dataclass
class ProviderUsage:
    id: str
    name: str
    plan: Optional[str] = None
    windows: List[UsageWindow] = field(default_factory=list)
    error: Optional[str] = None
    fetched_at: float = 0.0
    installed: bool = True

    @property
    def peak(self) -> Optional[float]:
        """The most constrained window: what the ring shows."""
        vals = [w.used for w in self.windows if w.used is not None]
        return max(vals) if vals else None


# ---------------------------------------------------------------------------------------------------- parsing helpers
def _pct(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(min(100.0, max(0.0, value)))


def _iso_epoch(value) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def window_label(minutes: Optional[float]) -> str:
    if not minutes:
        return "Limit"
    if minutes == 300:
        return "Session (5h)"
    if minutes == 10080:
        return "Weekly"
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)}-day"
    if minutes % 60 == 0:
        return f"{int(minutes // 60)}-hour"
    return f"{int(minutes)} min"


def parse_claude(response: dict) -> ProviderUsage:
    out = ProviderUsage("claude", NAMES["claude"], fetched_at=time.time())
    plan = response.get("subscription_type")
    out.plan = plan.replace("_", " ").title() if isinstance(plan, str) and plan else None
    limits = response.get("rate_limits")
    if not response.get("rate_limits_available") or not isinstance(limits, dict):
        out.error = "Usage is not available for this account"
        return out
    wanted = (("five_hour", "Session (5h)", 18000), ("seven_day", "Weekly", 604800),
              ("seven_day_opus", "Weekly · Opus", 604800), ("seven_day_sonnet", "Weekly · Sonnet", 604800))
    for key, label, seconds in wanted:
        row = limits.get(key)
        if isinstance(row, dict) and _pct(row.get("utilization")) is not None:
            out.windows.append(UsageWindow(label, _pct(row["utilization"]), _iso_epoch(row.get("resets_at")), seconds))
    if not out.windows:
        out.error = "No usage windows returned"
    return out


def parse_codex(result: dict) -> ProviderUsage:
    out = ProviderUsage("codex", NAMES["codex"], fetched_at=time.time())
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        buckets = {"codex": result.get("rateLimits") or {}}
    for bucket_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        plan = bucket.get("planType")
        if out.plan is None and isinstance(plan, str) and plan:
            out.plan = plan.replace("_", " ").title()
        prefix = "" if bucket_id == "codex" else f"{bucket.get('limitName') or bucket_id} · "
        for key in ("primary", "secondary"):
            row = bucket.get(key)
            if not isinstance(row, dict) or _pct(row.get("usedPercent")) is None:
                continue
            mins = row.get("windowDurationMins")
            resets = row.get("resetsAt")
            out.windows.append(UsageWindow(prefix + window_label(mins), _pct(row["usedPercent"]),
                                           float(resets) if isinstance(resets, (int, float)) else None,
                                           int(mins * 60) if isinstance(mins, (int, float)) else None))
    if not out.windows:
        out.error = "No usage windows returned"
    return out


def parse_antigravity(data: dict) -> ProviderUsage:
    out = ProviderUsage("antigravity", NAMES["antigravity"], fetched_at=time.time())
    for group in data.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            remaining = bucket.get("remaining_fraction")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining):
                continue
            text = f"{bucket.get('id', '')} {bucket.get('name', '')} {bucket.get('window', '')}".lower()
            if "5h" in text or "five" in text or "5-hour" in text:
                span, seconds = "Session (5h)", 18000
            elif "week" in text:
                span, seconds = "Weekly", 604800
            else:
                span, seconds = str(bucket.get("name") or bucket.get("id") or "Limit"), None
            out.windows.append(UsageWindow(f"{group.get('name', 'Models')} · {span}",
                                           _pct(100.0 * (1.0 - remaining)), _iso_epoch(bucket.get("reset_time")), seconds))
    if not out.windows:
        out.error = "No usage windows returned"
    return out


# ---------------------------------------------------------------------------------------------------------- fetching
def _drive(argv, first: dict, handler, timeout: float, cwd=None):
    argv = [find_executable(argv[0]) or argv[0]] + list(argv[1:])
    """Run `argv`, write `first`, then call handler(message) for every JSON line it prints. The handler returns
    (result, to_send): a non-None result ends the dialogue, `to_send` (one message or a list) is written back. The child's
    whole process group is always terminated."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=cwd, env=agent_env(), start_new_session=True)

    def send(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    deadline = time.monotonic() + timeout
    buf = b""
    try:
        send(first)
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], max(0.0, min(1.0, deadline - time.monotonic())))
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                result, to_send = handler(msg)
                if result is not None:
                    return result
                for m in (to_send if isinstance(to_send, list) else [to_send] if to_send else []):
                    send(m)
        raise TimeoutError("no answer in time")
    finally:
        try:
            os.killpg(proc.pid, 15)
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


def fetch_claude() -> ProviderUsage:
    request = {"type": "control_request", "request_id": "maestrix-usage",
               "request": {"subtype": "get_usage", "skip_behaviors": True}}

    def handler(msg):
        if msg.get("type") == "control_response":
            resp = msg.get("response") or {}
            if resp.get("request_id") == "maestrix-usage":
                if resp.get("subtype") == "error":
                    raise RuntimeError(str(resp.get("error") or "usage request failed"))
                return resp.get("response") or {}, None
        return None, None

    with tempfile.TemporaryDirectory(prefix="maestrix-usage-") as cwd:   # an empty folder: no project config is picked up
        data = _drive(["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                       "--no-session-persistence"], request, handler, 30, cwd=cwd)
    return parse_claude(data)


def fetch_codex() -> ProviderUsage:
    def handler(msg):
        if msg.get("id") == 1:
            if "error" in msg:
                raise RuntimeError("Codex rejected the connection")
            return None, [{"method": "initialized"}, {"id": 2, "method": "account/rateLimits/read", "params": {}}]
        if msg.get("id") == 2:
            if "error" in msg:
                raise RuntimeError("Codex usage request failed")
            return msg.get("result") or {}, None
        return None, None

    data = _drive(["codex", "app-server", "--listen", "stdio://"],
                  {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "maestrix_usage", "version": "1"}}},
                  handler, 30)
    return parse_codex(data)


def fetch_antigravity() -> ProviderUsage:
    with tempfile.TemporaryDirectory(prefix="maestrix-usage-") as cwd:
        proc = subprocess.run([find_executable("agy") or "agy", "-p", "/usage", "--output-format", "json"], cwd=cwd,
                              capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL, env=agent_env())
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError("Antigravity did not answer with JSON")
    if payload.get("status") != "SUCCESS":
        raise RuntimeError("Antigravity usage request failed")
    data = (payload.get("command") or {}).get("data")
    if not isinstance(data, dict) or "groups" not in data:
        raise RuntimeError("Antigravity returned no usage groups")
    return parse_antigravity(data)


FETCHERS: Dict[str, Callable[[], ProviderUsage]] = {
    "claude": fetch_claude, "codex": fetch_codex, "antigravity": fetch_antigravity}


# ------------------------------------------------------------------------------------------------------------ monitor
class UsageMonitor:
    """Polls every provider in a daemon thread. `on_update()` is called from that thread after each result: the GUI
    marshals it onto its main loop. Providers whose CLI is not installed are reported once and never run."""

    def __init__(self, fetchers: Optional[Dict[str, Callable[[], ProviderUsage]]] = None,
                 on_update: Optional[Callable[[], None]] = None, intervals: Optional[Dict[str, int]] = None) -> None:
        self.fetchers = dict(FETCHERS if fetchers is None else fetchers)
        self.on_update = on_update
        self.intervals = dict(INTERVALS if intervals is None else intervals)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_installed = fetchers is None   # injected fetchers (tests) are always "installed"
        self._state: Dict[str, ProviderUsage] = {
            pid: ProviderUsage(pid, NAMES.get(pid, pid)) for pid in self.fetchers}
        self._due: Dict[str, float] = {pid: 0.0 for pid in self.fetchers}
        self._last: Dict[str, float] = {pid: 0.0 for pid in self.fetchers}
        self._failures: Dict[str, int] = {pid: 0 for pid in self.fetchers}
        self.loading = set(self.fetchers)

    def snapshot(self) -> Dict[str, ProviderUsage]:
        with self._lock:
            return dict(self._state)

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="maestrix-usage", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh_now(self) -> None:
        """A manual refresh: due immediately, still never closer than MIN_GAP_S to the previous request."""
        with self._lock:
            for pid in self._due:
                self._due[pid] = 0.0
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            wait = 30.0
            for pid, fetch in self.fetchers.items():
                if self._stop.is_set():
                    return
                with self._lock:
                    due = max(self._due[pid], self._last[pid] + MIN_GAP_S)
                if now >= due:
                    self._poll(pid, fetch)
                    with self._lock:
                        wait = min(wait, max(1.0, self._due[pid] - time.time()))
                else:
                    wait = min(wait, due - now)
            self._wake.wait(timeout=max(1.0, wait))
            self._wake.clear()

    def _poll(self, pid: str, fetch: Callable[[], ProviderUsage]) -> None:
        if self._check_installed and find_executable(EXECUTABLES.get(pid, pid)) is None:
            with self._lock:   # looked up again every poll: installing the CLI later is picked up without a restart
                self._state[pid] = ProviderUsage(pid, NAMES.get(pid, pid), installed=False, fetched_at=time.time())
                self._last[pid] = time.time()
                self._due[pid] = time.time() + self.intervals.get(pid, 300)
                self.loading.discard(pid)
            if self.on_update is not None:
                self.on_update()
            return
        try:
            result = fetch()
            failed = result.error is not None
        except Exception as exc:   # noqa: BLE001 - a failing CLI must never stop the monitor
            failed = True
            result = ProviderUsage(pid, NAMES.get(pid, pid), error=str(exc) or exc.__class__.__name__,
                                   fetched_at=time.time())
        with self._lock:
            previous = self._state[pid]
            if failed and previous.windows:   # keep showing the last good reading, flagged with the error
                result = ProviderUsage(pid, previous.name, previous.plan, previous.windows, result.error,
                                       previous.fetched_at, True)
            self._state[pid] = result
            self._last[pid] = time.time()
            self._failures[pid] = self._failures[pid] + 1 if failed else 0
            base = self.intervals.get(pid, 300)
            self._due[pid] = time.time() + (min(MAX_BACKOFF_S, base * 2 ** self._failures[pid]) if failed else base)
            self.loading.discard(pid)
        if self.on_update is not None:
            try:
                self.on_update()
            except Exception:   # noqa: BLE001
                pass


def format_reset(resets_at: Optional[float], now: Optional[float] = None) -> str:
    """'resets in 3h 12m' / '2d 4h' / 'resetting'; '' when unknown."""
    if resets_at is None:
        return ""
    seconds = int(resets_at - (time.time() if now is None else now))
    if seconds <= 0:
        return "resetting"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"resets in {days}d {hours}h"
    if hours:
        return f"resets in {hours}h {minutes}m"
    return f"resets in {max(1, minutes)}m"
