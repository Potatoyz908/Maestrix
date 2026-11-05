"""Where a note's text lives. Three implementations of one tiny async interface (callbacks run on the GLib main loop, never block):

    storage.read(cb(text | None, err))      storage.write(text, cb(err))      storage.delete(cb(err))      storage.local_path

  CoreNoteStorage   managed notes: the core's REST API (`/api/notes/{name WITHOUT .md}`; the core appends ".md", so passing "x.md"
                    would create "x.md.md"). A missing note reads as "" (404).
  LocalNoteStorage  custom-path notes (`storageMode.custom`): a plain file, written atomically by Gio (temp file + rename).
  MemoryNoteStorage tests / offline demo.
"""
from __future__ import annotations

import os
import urllib.parse
from typing import Callable, Protocol

import gi

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Soup

from .note_model import valid_note_name

ReadCb = Callable[[str | None, Exception | None], None]
DoneCb = Callable[[Exception | None], None]
TEXT_EXTENSIONS = (".md", ".markdown", ".txt")


class NoteStorage(Protocol):
    local_path: str | None
    def read(self, cb: ReadCb) -> None: ...
    def write(self, text: str, cb: DoneCb) -> None: ...
    def delete(self, cb: DoneCb) -> None: ...


class MemoryNoteStorage:
    local_path = None

    def __init__(self, text: str = "") -> None:
        self.text, self.deleted, self.writes = text, False, []

    def read(self, cb: ReadCb) -> None: cb(self.text, None)
    def write(self, text: str, cb: DoneCb) -> None:
        self.text = text; self.writes.append(text); cb(None)
    def delete(self, cb: DoneCb) -> None:
        self.deleted = True; cb(None)


class CoreNotesClient:
    """Async client for the core's note endpoints (libsoup 3, GLib main loop). Auth: `Authorization: Bearer`, never `?token=`."""

    def __init__(self, port: int, token: str = "", host: str = "127.0.0.1") -> None:
        self.base, self.token = f"http://{host}:{port}", token
        self.session = Soup.Session()

    def _url(self, name: str) -> str:
        return f"{self.base}/api/notes/{urllib.parse.quote(name, safe='')}"

    def _request(self, method: str, name: str, body: bytes | None, cb: Callable[[int, bytes, Exception | None], None]) -> None:
        if not valid_note_name(name):
            GLib.idle_add(lambda: (cb(0, b"", ValueError(f"invalid note name: {name!r}")), False)[1])
            return
        msg = Soup.Message.new(method, self._url(name))
        if self.token:
            msg.get_request_headers().append("Authorization", f"Bearer {self.token}")
        if body is not None:
            msg.set_request_body_from_bytes("text/markdown; charset=utf-8", GLib.Bytes.new(body))

        def done(session, result) -> None:
            try:
                data = session.send_and_read_finish(result).get_data()
            except GLib.Error as e:
                cb(0, b"", e)
                return
            cb(msg.get_status(), data, None)

        self.session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, None, done)

    @staticmethod
    def _fail(op: str, status: int, data: bytes) -> Exception:
        return RuntimeError(f"{op} failed: HTTP {status} {data[:120].decode(errors='replace').strip()}")

    def get(self, name: str, cb: ReadCb) -> None:
        def got(status, data, err):
            if err: cb(None, err)
            elif status == 404: cb("", None)             # not created yet: empty note
            elif 200 <= status < 300: cb(data.decode("utf-8", errors="replace"), None)
            else: cb(None, self._fail("read", status, data))
        self._request("GET", name, None, got)

    def put(self, name: str, text: str, cb: DoneCb) -> None:
        def put_done(status, data, err):
            cb(err if err else None if 200 <= status < 300 else self._fail("write", status, data))
        self._request("PUT", name, text.encode(), put_done)

    def delete(self, name: str, cb: DoneCb) -> None:
        def del_done(status, data, err):
            cb(err if err else None if 200 <= status < 300 or status == 404 else self._fail("delete", status, data))
        self._request("DELETE", name, None, del_done)

    def rename(self, old: str, new: str, cb: DoneCb) -> None:
        """No rename endpoint: create the new note from the old text, then delete the old one. If the copy fails nothing is lost; if only the
        delete fails the old file is left behind and reported."""
        def after_read(text, err):
            if err: return cb(err)
            self.put(new, text or "", lambda perr: perr and cb(perr) or self.delete(old, lambda derr: cb(derr)))
        self.get(old, after_read)


class CoreNoteStorage:
    local_path = None

    def __init__(self, client: CoreNotesClient, name: str) -> None:
        self.client, self.name = client, name

    def read(self, cb: ReadCb) -> None: self.client.get(self.name, cb)
    def write(self, text: str, cb: DoneCb) -> None: self.client.put(self.name, text, cb)
    def delete(self, cb: DoneCb) -> None: self.client.delete(self.name, cb)


class LocalNoteStorage:
    def __init__(self, path: str) -> None:
        self.local_path = path
        self.file = Gio.File.new_for_path(path)

    def read(self, cb: ReadCb) -> None:
        def done(f, res):
            try:
                ok, data, _etag = f.load_contents_finish(res)
            except GLib.Error as e:
                cb("" if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND) else None,
                   None if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND) else e)
                return
            cb(bytes(data).decode("utf-8", errors="replace"), None)
        self.file.load_contents_async(None, done)

    def write(self, text: str, cb: DoneCb) -> None:
        def done(f, res):
            try:
                f.replace_contents_finish(res)   # Gio writes a temp file in the same dir and renames it over the target: atomic
            except GLib.Error as e:
                cb(e)
                return
            cb(None)
        self.file.replace_contents_bytes_async(GLib.Bytes.new(text.encode()), None, False, Gio.FileCreateFlags.NONE, None, done)

    def delete(self, cb: DoneCb) -> None:
        def done(f, res):
            try:
                f.delete_finish(res)
            except GLib.Error as e:
                cb(None if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND) else e)
                return
            cb(None)
        self.file.delete_async(GLib.PRIORITY_DEFAULT, None, done)


def watch_file(path: str, on_change: Callable[[], None]) -> Gio.FileMonitor | None:
    """Notify when `path` changes on disk (an agent wrote the note). One event per finished write (CHANGES_DONE_HINT)."""
    try:
        mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
    except GLib.Error:
        return None
    def changed(_m, _f, _o, event):
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.MOVED_IN):
            on_change()
    mon.connect("changed", changed)
    return mon


def is_note_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in TEXT_EXTENSIONS
