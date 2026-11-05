"""
Open Maestri GTK 4.0 Native - REST & WebSocket API Client
Consumes InterAgentServer with Bearer authentication and full PTY stream management.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import urllib.parse
import uuid

import requests

from ..models.connection import Connection
from ..models.node import CanvasNode
from ..models.workspace import WorkspaceDocument, WorkspaceEntry, WorkspacePayload


SCHEMA_VERSION = 2


class MaestriAPIError(requests.RequestException):
    """Base exception for Maestri API client errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AuthenticationError(MaestriAPIError):
    """Raised when authentication fails (HTTP 401/403)."""


class NotFoundError(MaestriAPIError):
    """Raised when a requested resource is not found (HTTP 404)."""


# ============================================================================
# RFC 6455 Lightweight WebSocket Client (PTY Stream)
# ============================================================================

class PTYWebSocketClient:
    """
    Pure Python RFC 6455 WebSocket client for bidirectional PTY streaming
    and terminal window resizing (TIOCSWINSZ).
    """

    OPCODE_CONTINUATION = 0x0
    OPCODE_TEXT = 0x1
    OPCODE_BINARY = 0x2
    OPCODE_CLOSE = 0x8
    OPCODE_PING = 0x9
    OPCODE_PONG = 0xA

    def __init__(
        self,
        host: str,
        port: int,
        terminal_id: str,
        auth_token: str,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.terminal_id = terminal_id
        self.auth_token = auth_token
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self._is_open = False
        self._lock = threading.Lock()
        self._rx_buffer = bytearray()
        self._reader_thread: Optional[threading.Thread] = None
        self._on_data: Optional[Callable[[bytes], None]] = None
        self._on_close: Optional[Callable[[], None]] = None
        self._on_error: Optional[Callable[[Exception], None]] = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    def connect(self) -> None:
        """Performs TCP connection and RFC 6455 WebSocket upgrade handshake."""
        with self._lock:
            if self._is_open:
                return

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            # Generate random 16-byte base64 Sec-WebSocket-Key
            raw_nonce = os.urandom(16)
            sec_key = base64.b64encode(raw_nonce).decode("ascii")

            # Calculate expected accept key
            ws_guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            expected_accept = base64.b64encode(
                hashlib.sha1((sec_key + ws_guid).encode("utf-8")).digest()
            ).decode("ascii")

            path = f"/ws/pty/{self.terminal_id}?token={urllib.parse.quote(self.auth_token)}"
            headers = [
                f"GET {path} HTTP/1.1",
                f"Host: {self.host}:{self.port}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {sec_key}",
                "Sec-WebSocket-Version: 13",
                f"Origin: http://127.0.0.1:{self.port}",
                f"Authorization: Bearer {self.auth_token}",
                "",
                "",
            ]
            handshake_req = "\r\n".join(headers).encode("utf-8")
            sock.sendall(handshake_req)

            # Read handshake response
            resp_data = bytearray()
            while b"\r\n\r\n" not in resp_data:
                chunk = sock.recv(4096)
                if not chunk:
                    sock.close()
                    raise ConnectionError("Server closed connection during WebSocket handshake")
                resp_data.extend(chunk)

            header_part, trailing = resp_data.split(b"\r\n\r\n", 1)
            self._rx_buffer = bytearray(trailing)

            header_text = header_part.decode("utf-8", errors="replace")
            status_line = header_text.splitlines()[0] if header_text else ""
            if " 101 " not in status_line:
                sock.close()
                if " 401 " in status_line:
                    raise AuthenticationError(f"WebSocket upgrade rejected: 401 Unauthorized ({status_line})", 401)
                raise ConnectionError(f"WebSocket handshake failed: {status_line}")

            # Verify Sec-WebSocket-Accept
            accept_header = None
            for line in header_text.splitlines()[1:]:
                if line.lower().startswith("sec-websocket-accept:"):
                    accept_header = line.split(":", 1)[1].strip()
                    break

            if accept_header != expected_accept:
                sock.close()
                raise ConnectionError(
                    f"Sec-WebSocket-Accept mismatch: expected {expected_accept}, got {accept_header}"
                )

            self.sock = sock
            self._is_open = True

    def send_raw(self, data: bytes) -> None:
        """Sends binary payload directly to the PTY."""
        self._send_frame(self.OPCODE_BINARY, data)

    def send_text(self, text: str) -> None:
        """Sends text payload to the PTY."""
        self._send_frame(self.OPCODE_TEXT, text.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        """Sends JSON resize control command to update PTY window size via TIOCSWINSZ."""
        cmd = json.dumps({"type": "resize", "cols": int(cols), "rows": int(rows)})
        self._send_frame(self.OPCODE_TEXT, cmd.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        with self._lock:
            if not self._is_open or not self.sock:
                raise ConnectionError("WebSocket connection is not open")

            fin = 0x80
            b1 = fin | (opcode & 0x0F)
            length = len(payload)
            mask_bit = 0x80

            frame = bytearray([b1])
            if length <= 125:
                frame.append(mask_bit | length)
            elif length <= 65535:
                frame.append(mask_bit | 126)
                frame.extend(struct.pack("!H", length))
            else:
                frame.append(mask_bit | 127)
                frame.extend(struct.pack("!Q", length))

            # Client frames MUST be masked per RFC 6455
            mask_key = os.urandom(4)
            frame.extend(mask_key)

            masked_payload = bytearray(length)
            for i in range(length):
                masked_payload[i] = payload[i] ^ mask_key[i % 4]
            frame.extend(masked_payload)

            self.sock.sendall(frame)

    def receive_frame(self, timeout: Optional[float] = None) -> Optional[Tuple[int, bytes]]:
        """
        Receives the next complete WebSocket frame.
        Returns (opcode, payload) or None on timeout/close.
        """
        start_time = time.time()
        while self._is_open:
            sock = self.sock
            if not sock or not self._is_open:
                return None

            # Try to decode from buffer
            frame = self._decode_frame()
            if frame is not None:
                opcode, payload = frame
                if opcode == self.OPCODE_PING:
                    self._send_frame(self.OPCODE_PONG, payload)
                    continue
                if opcode == self.OPCODE_CLOSE:
                    self.close()
                    return (opcode, payload)
                return (opcode, payload)

            # Wait for data
            rem_timeout = None
            if timeout is not None:
                elapsed = time.time() - start_time
                rem_timeout = max(0.0, timeout - elapsed)
                if rem_timeout <= 0.0:
                    return None

            try:
                if not self._is_open or not self.sock:
                    return None
                r, _, _ = select.select([sock], [], [], rem_timeout)
                if not r or not self._is_open or not self.sock:
                    return None
                chunk = sock.recv(4096)
                if not chunk:
                    self.close()
                    return None
                self._rx_buffer.extend(chunk)
            except (socket.error, OSError, ValueError) as e:
                self.close()
                if not self._is_open:
                    return None
                raise ConnectionError(f"Socket error while receiving frame: {e}") from e

        return None

    def _decode_frame(self) -> Optional[Tuple[int, bytes]]:
        buf_len = len(self._rx_buffer)
        if buf_len < 2:
            return None

        b1 = self._rx_buffer[0]
        b2 = self._rx_buffer[1]
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        payload_len = b2 & 0x7F

        header_len = 2
        if payload_len == 126:
            if buf_len < 4:
                return None
            payload_len = struct.unpack("!H", self._rx_buffer[2:4])[0]
            header_len = 4
        elif payload_len == 127:
            if buf_len < 10:
                return None
            payload_len = struct.unpack("!Q", self._rx_buffer[2:10])[0]
            header_len = 10

        mask_len = 4 if masked else 0
        total_len = header_len + mask_len + payload_len
        if buf_len < total_len:
            return None

        payload_start = header_len + mask_len
        payload = self._rx_buffer[payload_start:total_len]

        if masked:
            mask_key = self._rx_buffer[header_len:payload_start]
            unmasked = bytearray(payload_len)
            for i in range(payload_len):
                unmasked[i] = payload[i] ^ mask_key[i % 4]
            payload = bytes(unmasked)
        else:
            payload = bytes(payload)

        # Slice consumed frame from buffer
        del self._rx_buffer[:total_len]
        return (opcode, payload)

    def start_reader_thread(
        self,
        on_data: Callable[[bytes], None],
        on_close: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """Starts a background reader thread for asynchronous PTY output dispatch."""
        self._on_data = on_data
        self._on_close = on_close
        self._on_error = on_error

        def _worker() -> None:
            try:
                while self._is_open:
                    frame = self.receive_frame(timeout=0.5)
                    if frame:
                        opcode, payload = frame
                        if opcode in (self.OPCODE_BINARY, self.OPCODE_TEXT):
                            if self._on_data:
                                self._on_data(payload)
                        elif opcode == self.OPCODE_CLOSE:
                            break
            except Exception as e:
                if self._on_error:
                    self._on_error(e)
            finally:
                if self._on_close:
                    self._on_close()

        self._reader_thread = threading.Thread(target=_worker, daemon=True)
        self._reader_thread.start()

    def close(self) -> None:
        """Closes the WebSocket connection."""
        with self._lock:
            if not self._is_open:
                return
            self._is_open = False
            sock = self.sock
            self.sock = None
            if sock:
                try:
                    # Try sending close frame
                    try:
                        fin = 0x80 | self.OPCODE_CLOSE
                        mask = 0x80
                        mask_key = os.urandom(4)
                        close_pkt = bytearray([fin, mask, 0, 0, 0, 0])
                        close_pkt[2:6] = mask_key
                        sock.sendall(close_pkt)
                    except Exception:
                        pass
                    sock.close()
                except Exception:
                    pass


# ============================================================================
# Main Open Maestri REST & PTY Client
# ============================================================================

class MaestriClient:
    """
    High-level REST client consuming the Open Maestri InterAgentServer.
    Handles Bearer token authentication, CORS / Origin headers, workspace lifecycle,
    PTY creation and stream handling.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
        auth_token: Optional[str] = None,
        timeout: float = 10.0,
        token: Optional[str] = None,
    ) -> None:
        self.host = host
        self.timeout = timeout
        self.auth_token = self._resolve_auth_token(auth_token or token)
        self.port = self._resolve_server_port(port)
        self.session = requests.Session()

    @property
    def token(self) -> str:
        return self.auth_token

    @property
    def headers(self) -> Dict[str, str]:
        return self._headers()

    def is_connected(self) -> bool:
        return self.check_connection_status() == "connected"

    def check_connection_status(self) -> str:
        """Checks server reachability and credentials:
        Returns:
            'connected': Server reachable, token valid (200 OK)
            'unauthorized': Server reachable, but rejected credentials (401 Unauthorized)
            'offline': Server unreachable or timed out
        """
        try:
            r = self.session.get(f"{self.base_url}/api/workspace", headers=self._headers(), timeout=1.5)
            if r.status_code == 200:
                return "connected"
            if r.status_code == 401:
                return "unauthorized"
            return "offline"
        except Exception:
            return "offline"

    def _resolve_auth_token(self, provided_token: Optional[str]) -> str:
        if provided_token:
            return provided_token.strip()

        # Check environment variable
        env_token = os.environ.get("MAESTRI_AUTH_TOKEN")
        if env_token:
            return env_token.strip()

        # Read ~/.open-maestri/run/auth.token
        token_path = Path.home() / ".open-maestri" / "run" / "auth.token"
        if token_path.is_file():
            try:
                token = token_path.read_text(encoding="utf-8").strip()
                if token:
                    return token
            except Exception:
                pass

        return ""

    def _resolve_server_port(self, provided_port: Optional[int]) -> int:
        if provided_port is not None and provided_port > 0:
            return provided_port

        # Check environment variable
        env_port = os.environ.get("MAESTRI_SERVER_PORT")
        if env_port and env_port.isdigit():
            return int(env_port)

        return 39345  # Default fallback if port is unconfigured

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _headers(self, custom: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Host": f"{self.host}:{self.port}",
            "Origin": f"http://{self.host}:{self.port}",
            "Accept": "application/json, text/plain, */*",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if custom:
            headers.update(custom)
        return headers

    def _handle_response(self, response: requests.Response) -> requests.Response:
        if response.status_code == 401:
            raise AuthenticationError(
                f"Unauthorized (401): {response.text}",
                status_code=401,
                response_body=response.text,
            )
        if response.status_code == 404:
            raise NotFoundError(
                f"Not Found (404): {response.text}",
                status_code=404,
                response_body=response.text,
            )
        if not response.ok:
            raise MaestriAPIError(
                f"HTTP {response.status_code} Error: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response

    # ------------------------------------------------------------------------
    # CLI Command Dispatch (/cli)
    # ------------------------------------------------------------------------

    def execute_cli(self, args: List[str], terminal_id: Optional[str] = None) -> str:
        """Dispatches an omaestri CLI command through the backend server."""
        headers = self._headers({"Content-Type": "application/json"})
        if terminal_id:
            headers["X-Terminal-ID"] = terminal_id

        resp = self.session.post(
            f"{self.base_url}/cli",
            json={"args": args},
            headers=headers,
            timeout=self.timeout,
        )
        return self._handle_response(resp).text

    # ------------------------------------------------------------------------
    # Workspace Operations (/api/workspaces, /api/workspace)
    # ------------------------------------------------------------------------

    def get_workspaces(self) -> List[WorkspaceEntry]:
        """Lists all workspaces from the server manifest and workspace directories."""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/workspaces",
                headers=self._headers(),
                timeout=self.timeout,
            )
            data = self._handle_response(resp).json()
            return [WorkspaceEntry.from_dict(item) for item in data]
        except AuthenticationError:
            raise
        except (requests.RequestException, ValueError):
            return []

    def create_workspace(
        self,
        name: str,
        working_directory: str = "",
        icon: str = "folder",
        color: str = "blue",
        is_pinned: bool = False,
        location_type: str = "local",
        workspace_id: Optional[str] = None,
    ) -> WorkspaceEntry:
        """Creates a new workspace on the server."""
        payload: Dict[str, Any] = {
            "name": name,
            "workingDirectory": working_directory,
            "icon": icon,
            "color": color,
            "isPinned": is_pinned,
            "locationType": location_type,
        }
        if workspace_id:
            payload["id"] = workspace_id

        resp = self.session.post(
            f"{self.base_url}/api/workspaces",
            json=payload,
            headers=self._headers({"Content-Type": "application/json"}),
            timeout=self.timeout,
        )
        data = self._handle_response(resp).json()
        return WorkspaceEntry.from_dict(data)

    def get_workspace(self, workspace_id: Optional[str] = None) -> Optional[WorkspaceDocument]:
        """Fetches a full WorkspaceDocument. Returns None if offline or if the
        document is not schemaVersion 2 (never silently reinterpret other schemas).
        Raises AuthenticationError on 401 Unauthorized."""
        url = f"{self.base_url}/api/workspace"
        if workspace_id:
            url += f"?id={urllib.parse.quote(workspace_id)}"

        try:
            resp = self.session.get(url, headers=self._headers(), timeout=self.timeout)
            data = self._handle_response(resp).json()
        except AuthenticationError:
            raise
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict) or int(data.get("schemaVersion", 0) or 0) != SCHEMA_VERSION:
            return None
        return WorkspaceDocument.from_dict(data)

    def save_workspace(self, document: WorkspaceDocument, workspace_id: Optional[str] = None) -> bool:
        """Atomically saves a WorkspaceDocument on the server (forces schemaVersion 2)."""
        return self.save_workspace_dict(document.to_dict(), workspace_id)

    def save_workspace_dict(self, data: Dict[str, Any], workspace_id: Optional[str] = None) -> bool:
        """Saves an already-serialized document. Safe to call from a worker thread,
        since `data` is an immutable snapshot taken on the main thread."""
        data = dict(data)
        data["schemaVersion"] = SCHEMA_VERSION
        data.setdefault("type", "workspace")
        url = f"{self.base_url}/api/workspace"
        if workspace_id:
            url += f"?id={urllib.parse.quote(workspace_id)}"

        try:
            resp = self.session.post(
                url,
                json=data,
                headers=self._headers({"Content-Type": "application/json"}),
                timeout=self.timeout,
            )
            self._handle_response(resp)
            return True
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------------
    # Terminal & PTY Management (/api/terminals, /ws/pty)
    # ------------------------------------------------------------------------

    def create_terminal(
        self,
        name: str = "Terminal",
        command: str = "bash",
        role: str = "generic_shell",
        working_directory: str = "",
        workspace_id: Optional[str] = None,
        terminal_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a headless PTY terminal session on the server. `terminal_id` pins the id (a canvas node's
        terminalId), otherwise the core generates one. NOTE: the core replaces an existing session with the same id;
        use `ensure_terminal` to attach to a node's shell without killing it."""
        payload: Dict[str, Any] = {
            "name": name,
            "command": command,
            "role": role,
            "workingDirectory": working_directory,
        }
        if workspace_id:
            payload["workspaceId"] = workspace_id
        if terminal_id:
            payload["id"] = terminal_id
        if display_name:
            payload["displayName"] = display_name

        resp = self.session.post(
            f"{self.base_url}/api/terminals",
            json=payload,
            headers=self._headers({"Content-Type": "application/json"}),
            timeout=self.timeout,
        )
        return self._handle_response(resp).json()

    def get_terminal(self, terminal_id: str) -> Optional[Dict[str, Any]]:
        """PTY metadata (pid/cols/rows/running/exitCode...) or None when the core has no such session (404)."""
        resp = self.session.get(
            f"{self.base_url}/api/terminals/{terminal_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code == 404:
            return None
        return self._handle_response(resp).json()

    def ensure_terminal(
        self,
        terminal_id: str,
        command: str = "bash",
        working_directory: str = "",
        workspace_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> bool:
        """Make sure the core runs a PTY for `terminal_id`: reuse a live one (its shell and scrollback survive a UI
        restart), create it otherwise. Returns True when a new session was created."""
        meta = self.get_terminal(terminal_id)
        if meta is not None and meta.get("running", True):
            return False
        self.create_terminal(
            name=display_name or "Terminal",
            command=command,
            working_directory=working_directory,
            workspace_id=workspace_id,
            terminal_id=terminal_id,
            display_name=display_name,
        )
        return True

    def delete_terminal(self, terminal_id: str) -> bool:
        """Terminates and removes a PTY terminal session."""
        resp = self.session.delete(
            f"{self.base_url}/api/terminals/{terminal_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._handle_response(resp)
        return True

    def get_agents(self) -> List[Dict[str, Any]]:
        """Returns the list of currently active agents and terminals."""
        resp = self.session.get(
            f"{self.base_url}/api/agents",
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._handle_response(resp).json()

    def connect_pty(self, terminal_id: str) -> PTYWebSocketClient:
        """
        Connects an RFC 6455 WebSocket client to stream live PTY I/O
        for the given terminal session.
        """
        client = PTYWebSocketClient(
            host=self.host,
            port=self.port,
            terminal_id=terminal_id,
            auth_token=self.auth_token,
            timeout=self.timeout,
        )
        client.connect()
        return client

    # ------------------------------------------------------------------------
    # Notes Management (/api/notes)
    # ------------------------------------------------------------------------

    @staticmethod
    def _sanitize_note_name(name: str) -> str:
        """Strips trailing .md extension if present to prevent .md.md duplicate paths."""
        return name[:-3] if name.lower().endswith(".md") else name

    def get_note(self, name: str) -> str:
        """Reads a Markdown sticky note's content."""
        clean = self._sanitize_note_name(name)
        safe_name = urllib.parse.quote(clean)
        try:
            resp = self.session.get(
                f"{self.base_url}/api/notes/{safe_name}",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return self._handle_response(resp).text
        except (AuthenticationError, NotFoundError, MaestriAPIError):
            raise
        except (requests.RequestException, ValueError):
            return ""

    def put_note(self, name: str, content: str) -> bool:
        """Writes Markdown content to a sticky note atomically."""
        clean = self._sanitize_note_name(name)
        safe_name = urllib.parse.quote(clean)
        resp = self.session.put(
            f"{self.base_url}/api/notes/{safe_name}",
            data=content.encode("utf-8"),
            headers=self._headers({"Content-Type": "text/markdown; charset=utf-8"}),
            timeout=self.timeout,
        )
        self._handle_response(resp)
        return True

    def delete_note(self, name: str) -> bool:
        """Deletes a sticky note file."""
        clean = self._sanitize_note_name(name)
        safe_name = urllib.parse.quote(clean)
        resp = self.session.delete(
            f"{self.base_url}/api/notes/{safe_name}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._handle_response(resp)
        return True

    # ------------------------------------------------------------------------
    # Connections (/api/connections)
    # ------------------------------------------------------------------------

    def get_connections(self) -> List[Connection]:
        """Lists all connections currently in the active workspace."""
        resp = self.session.get(
            f"{self.base_url}/api/connections",
            headers=self._headers(),
            timeout=self.timeout,
        )
        data = self._handle_response(resp).json()
        return [Connection.from_dict(item) for item in data]

    def create_connection(self, a_id: str, b_id: str, kind: str = "terminal") -> Connection:
        """Connects two canvas nodes with 21 interpolated ropePoints."""
        payload = {"a": a_id, "b": b_id, "kind": kind}
        resp = self.session.post(
            f"{self.base_url}/api/connections",
            json=payload,
            headers=self._headers({"Content-Type": "application/json"}),
            timeout=self.timeout,
        )
        data = self._handle_response(resp).json()
        return Connection.from_dict(data, default_kind=kind)

    def delete_connection(self, connection_id: str) -> bool:
        """Removes a connection between canvas nodes."""
        resp = self.session.delete(
            f"{self.base_url}/api/connections/{connection_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._handle_response(resp)
        return True

    # ------------------------------------------------------------------------
    # Floors (/api/floors)
    # ------------------------------------------------------------------------

    def get_floors(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists floors (git worktrees) for a workspace."""
        url = f"{self.base_url}/api/floors"
        if workspace_id:
            url += f"?workspace={urllib.parse.quote(workspace_id)}"

        resp = self.session.get(url, headers=self._headers(), timeout=self.timeout)
        return self._handle_response(resp).json()

    def create_floor(
        self,
        name: str,
        branch_name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        working_directory: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates an isolated floor git worktree environment."""
        payload: Dict[str, Any] = {"name": name}
        if branch_name:
            payload["branchName"] = branch_name
        if workspace_id:
            payload["workspaceId"] = workspace_id
        if working_directory:
            payload["workingDirectory"] = working_directory

        resp = self.session.post(
            f"{self.base_url}/api/floors",
            json=payload,
            headers=self._headers({"Content-Type": "application/json"}),
            timeout=self.timeout,
        )
        return self._handle_response(resp).json()

    # ------------------------------------------------------------------------
    # Files & Git Status (/api/files, /api/git/status)
    # ------------------------------------------------------------------------

    def list_files(self, root: str = "", path: str = "") -> List[Dict[str, Any]]:
        """Lists directory entries sorted folders-first with git badges."""
        params: List[str] = []
        if root:
            params.append(f"root={urllib.parse.quote(root)}")
        if path:
            params.append(f"path={urllib.parse.quote(path)}")

        query = ("?" + "&".join(params)) if params else ""
        resp = self.session.get(
            f"{self.base_url}/api/files{query}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._handle_response(resp).json()

    def get_git_status(self, root: str = "") -> List[Dict[str, Any]]:
        """Fetches git porcelain status changes for repository root."""
        query = f"?root={urllib.parse.quote(root)}" if root else ""
        resp = self.session.get(
            f"{self.base_url}/api/git/status{query}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._handle_response(resp).json()

    # ------------------------------------------------------------------------
    # Sidebar Layout (/api/sidebar-layout)
    # ------------------------------------------------------------------------

    def get_sidebar_layout(self) -> Dict[str, Any]:
        """Loads sidebar-layout.json schemaVersion: 2."""
        resp = self.session.get(
            f"{self.base_url}/api/sidebar-layout",
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._handle_response(resp).json()

    def save_sidebar_layout(self, layout: Dict[str, Any]) -> bool:
        """Saves sidebar-layout.json after server schema and UUID validation."""
        resp = self.session.put(
            f"{self.base_url}/api/sidebar-layout",
            json=layout,
            headers=self._headers({"Content-Type": "application/json"}),
            timeout=self.timeout,
        )
        self._handle_response(resp)
        return True

    # ------------------------------------------------------------------------
    # Canvas Nodes (/api/nodes)
    # ------------------------------------------------------------------------

    def create_node(
        self,
        node_type: str,
        frame: Optional[List[List[float]]] = None,
        content: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> CanvasNode:
        """Dynamically creates a new CanvasNode (terminal, stickyNote, portal, fileTree)."""
        payload: Dict[str, Any] = {"nodeType": node_type}
        if frame:
            payload["frame"] = frame
        if content:
            payload["content"] = content

        url = f"{self.base_url}/api/nodes"
        if workspace_id:
            url += f"?workspace={urllib.parse.quote(workspace_id)}"

        resp = self.session.post(
            url,
            json=payload,
            headers=self._headers({"Content-Type": "application/json"}),
            timeout=self.timeout,
        )
        data = self._handle_response(resp).json()
        return CanvasNode.from_dict(data)
