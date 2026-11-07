"""Client for the core's PTY websocket (`WS /ws/pty/:terminalId`), on libsoup 3 so it runs on the GLib main loop (no threads, no marshalling).

Wire protocol (verified against Sources/InterAgent/InterAgentServer.swift + WebSocketServer.swift):
  server -> client  binary frames: the saved buffer ONCE right after the handshake (also on every reconnect), then live output
  client -> server  binary frame = raw keystrokes; text frame {"type":"resize","cols":N,"rows":M}
  auth              `Authorization: Bearer <token>` (or ?token=), Host/Origin must be loopback; the core has no "exit" frame: a finished
                    shell shows up as the socket closing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Protocol

import gi

gi.require_version("Soup", "3.0")
from gi.repository import GLib, Soup


class PtyTransport(Protocol):
    on_data: Callable[[bytes], None] | None
    on_status: Callable[[str, bool], None] | None   # (connecting|open|closed|error, reconnected)
    def write(self, data: bytes | str) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def close(self) -> None: ...


def read_auth_token(home: str | os.PathLike | None = None) -> str:
    """MAESTRI_AUTH_TOKEN, else <data_dir>/run/auth.token (the file the core writes with mode 0600). `home` (tests) pins the
    data dir to <home>/.open-maestri; otherwise maestri_core.paths decides (OPEN_MAESTRI_DATA_DIR, else ~/.open-maestri)."""
    env = os.environ.get("MAESTRI_AUTH_TOKEN")
    if env:
        return env
    from maestri_core.paths import DATA_DIR_NAME, run_dir
    path = (Path(home) / DATA_DIR_NAME / "run" if home else run_dir()) / "auth.token"
    try:
        return path.read_text().strip()
    except OSError:
        return ""


class SoupPtyTransport:
    def __init__(self, terminal_id: str, port: int, token: str = "", host: str = "127.0.0.1", reconnect: bool = True,
                 base_backoff_s: float = 0.5, max_backoff_s: float = 5.0) -> None:
        self.terminal_id = terminal_id
        self.url = f"ws://{host}:{port}/ws/pty/{terminal_id}"
        self.token = token
        self.on_data: Callable[[bytes], None] | None = None
        self.on_status: Callable[[str, bool], None] | None = None
        self._reconnect, self._base, self._max = reconnect, base_backoff_s, max_backoff_s
        self._session = Soup.Session()
        self._conn: Soup.WebsocketConnection | None = None
        self._attempts = 0
        self._ever_open = False
        self._closed_by_user = False
        self._size: tuple[int, int] | None = None
        self._timer = 0

    # ---- lifecycle ----------------------------------------------------------------------------
    def connect(self) -> "SoupPtyTransport":
        self._status("connecting")
        msg = Soup.Message.new("GET", self.url)
        if self.token:
            msg.get_request_headers().append("Authorization", f"Bearer {self.token}")  # header, not ?token=, so it never lands in logs
        self._session.websocket_connect_async(msg, None, None, GLib.PRIORITY_DEFAULT, None, self._connected)
        return self

    def _connected(self, session: Soup.Session, result) -> None:
        try:
            conn = session.websocket_connect_finish(result)
        except GLib.Error:
            self._status("error")
            self._maybe_reconnect()
            return
        if self._closed_by_user:
            conn.close(1000, None)
            return
        self._conn = conn
        conn.set_max_incoming_payload_size(64 * 1024 * 1024)
        conn.connect("message", self._on_message)
        conn.connect("closed", self._on_closed)
        conn.connect("error", lambda *_: self._status("error"))
        reconnected = self._ever_open
        self._ever_open = True
        self._attempts = 0
        self._status("open", reconnected)
        if self._size:
            self.resize(*self._size)

    def _on_message(self, _conn, msg_type, data: GLib.Bytes) -> None:
        if msg_type == Soup.WebsocketDataType.BINARY and self.on_data:
            self.on_data(data.get_data())

    def _on_closed(self, conn) -> None:
        if self._conn is conn:
            self._conn = None
        self._status("closed")
        self._maybe_reconnect()

    def _maybe_reconnect(self) -> None:
        if self._closed_by_user or not self._reconnect:
            return
        delay = min(self._base * 2 ** self._attempts, self._max)
        self._attempts += 1
        self._timer = GLib.timeout_add(int(delay * 1000), self._retry)

    def _retry(self) -> bool:
        if not self._closed_by_user:
            self.connect()
        return GLib.SOURCE_REMOVE

    def _status(self, s: str, reconnected: bool | None = None) -> None:
        if self.on_status:
            self.on_status(s, self._ever_open if reconnected is None else reconnected)

    # ---- I/O ----------------------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._conn is not None and self._conn.get_state() == Soup.WebsocketState.OPEN

    def write(self, data: bytes | str) -> None:
        if not self.is_open:
            return  # keystrokes typed while disconnected are dropped on purpose (replaying them later would surprise the user)
        raw = data.encode() if isinstance(data, str) else data
        self._conn.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(raw))

    def resize(self, cols: int, rows: int) -> None:
        self._size = (cols, rows)   # re-sent after every (re)connect
        if self.is_open:
            self._conn.send_text(json.dumps({"type": "resize", "cols": cols, "rows": rows}))

    def close(self) -> None:
        self._closed_by_user = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._conn is not None and self._conn.get_state() == Soup.WebsocketState.OPEN:
            self._conn.close(1000, None)
