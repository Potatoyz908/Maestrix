"""Keep CLAUDE.md and AGENTS.md in sync (workspace option "syncConfigFiles", Edit Workspace).

macOS only has the model field; here the GUI does the work while the workspace is open:
  * Gio.FileMonitor on <working_directory>/CLAUDE.md and AGENTS.md;
  * a change to one is copied into the other after a short debounce (400 ms), atomically (temp file + os.replace);
  * when only one exists, the pair is created; when both exist and differ at attach time, the newer one wins;
  * no echo: the copy we just wrote is recognised by its content hash and never copied back;
  * remote (SSH) workspaces are ignored: their files are not on this machine.

`sync_pair()` is the pure part (no GTK) so it can be tested with a temporary directory.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from gi.repository import Gio, GLib

FILES = ("CLAUDE.md", "AGENTS.md")
DEBOUNCE_MS = 400


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.maestri-sync.{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
    except OSError:
        pass
    os.replace(tmp, path)


def sync_pair(directory: str | os.PathLike, source: Optional[str] = None) -> Optional[str]:
    """Copy `source` (one of FILES) onto the other file; with source=None pick the only/newer existing one.
    Returns the hash of the content now in both files, or None when there was nothing to copy."""
    d = Path(directory)
    a, b = (d / FILES[0]), (d / FILES[1])
    if source is None:
        existing = [p for p in (a, b) if p.is_file()]
        if not existing:
            return None
        src = max(existing, key=lambda p: p.stat().st_mtime_ns)
    else:
        src = d / source
    dst = b if src.name == FILES[0] else a
    data = _read(src)
    if data is None:
        return None
    if _read(dst) != data:
        _write_atomic(dst, data)
    return _digest(data)


class ConfigFileSync:
    """One per window; `attach()` on every workspace load, `detach()` on switch/close."""

    def __init__(self) -> None:
        self.directory: Optional[Path] = None
        self._monitors: list[Gio.FileMonitor] = []
        self._timer = 0
        self._pending: Optional[str] = None
        self._last_hash: Optional[str] = None
        self.copies = 0   # how many times a file was copied over the other (tests / diagnostics)

    @property
    def active(self) -> bool:
        return self.directory is not None

    def attach(self, payload) -> bool:
        """Start syncing for this workspace payload when its syncConfigFiles option is on (and it is local)."""
        self.detach()
        extra = getattr(payload, "_extra", {}) or {}
        enabled = bool(getattr(payload, "sync_config_files", extra.get("syncConfigFiles", False)))
        location = str(getattr(payload, "location_type", extra.get("locationType", "local")) or "local")
        wd = os.path.expanduser(getattr(payload, "working_directory", "") or "")
        if not enabled or location != "local" or not wd or not os.path.isdir(wd):
            return False
        self.directory = Path(wd)
        try:
            self._last_hash = sync_pair(self.directory)   # create the missing one / reconcile at open
        except OSError as exc:
            print(f"[config-sync] initial sync failed: {exc!r}")
        for name in FILES:
            mon = Gio.File.new_for_path(str(self.directory / name)).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
            mon.connect("changed", self._on_changed, name)
            self._monitors.append(mon)
        return True

    def detach(self) -> None:
        for mon in self._monitors:
            mon.cancel()
        self._monitors.clear()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.directory, self._pending, self._last_hash = None, None, None

    def _on_changed(self, _mon, _file, _other, event, name: str) -> None:
        if event not in (Gio.FileMonitorEvent.CHANGED, Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                         Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.RENAMED):
            return
        self._pending = name
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(DEBOUNCE_MS, self._flush)

    def _flush(self) -> bool:
        self._timer = 0
        name, self._pending = self._pending, None
        if self.directory is None or name is None:
            return GLib.SOURCE_REMOVE
        data = _read(self.directory / name)
        if data is None or _digest(data) == self._last_hash:
            return GLib.SOURCE_REMOVE   # deleted, or our own copy coming back (no echo)
        try:
            self._last_hash = sync_pair(self.directory, name)
            self.copies += 1
        except OSError as exc:
            print(f"[config-sync] copy {name} failed: {exc!r}")
        return GLib.SOURCE_REMOVE
