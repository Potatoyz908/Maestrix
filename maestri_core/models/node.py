"""
Canvas node models for Open Maestri GTK 4 Native.
Strictly compatible with Maestri schemaVersion: 2 format:
  frame: [[x, y], [w, h]]
  content: { "<type>": { "_0": <payload> } }
  UUIDs in UPPERCASE
  ISO8601 UTC dates without fractional seconds (strftime("%Y-%m-%dT%H:%M:%SZ"))
  Preserves unknown fields on round-trip.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


def current_iso8601() -> str:
    """Returns ISO8601 timestamp without fractional seconds per schema spec."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_iso8601(val: Any) -> str:
    """Normalizes any ISO8601 date string to strict UTC '%Y-%m-%dT%H:%M:%SZ' format."""
    if not val or not isinstance(val, str):
        return current_iso8601()
    try:
        clean = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return str(val)


@dataclass
class NodeFrame:
    x: float = 100.0
    y: float = 100.0
    width: float = 400.0
    height: float = 300.0

    def to_list(self) -> List[List[float]]:
        return [[float(self.x), float(self.y)], [float(self.width), float(self.height)]]

    @classmethod
    def from_list(cls, data: List[List[float]]) -> "NodeFrame":
        if len(data) >= 2 and len(data[0]) >= 2 and len(data[1]) >= 2:
            return cls(
                x=float(data[0][0]),
                y=float(data[0][1]),
                width=float(data[1][0]),
                height=float(data[1][1]),
            )
        return cls()

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= (self.x + self.width) and self.y <= py <= (self.y + self.height)


@dataclass
class TerminalContent:
    name: str = "Terminal"
    agent_type: str = "generic_shell"
    command: str = "bash"
    working_directory: str = ""
    color: str = "#000000"
    icon: str = "terminal"
    terminal_id: str = ""
    id: str = ""
    shell_path: str = "/bin/bash"
    status: str = "idle"
    is_manager: bool = False
    monitor_with_ombro: bool = False
    auto_scroll_locked: bool = False
    shortcut_mode: Dict[str, Any] = field(default_factory=lambda: {"kind": "none"})
    scrollback_line_count: int = 0
    font_size: Optional[float] = None
    assigned_role_id: Optional[str] = None
    scrollback_file: Optional[str] = None
    last_active_at: Optional[str] = None
    theme_id: Optional[str] = None
    font_family: Optional[str] = None
    _extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id and not self.terminal_id:
            uid = str(uuid.uuid4()).upper()
            self.id = uid
            self.terminal_id = uid
        elif self.id and not self.terminal_id:
            self.terminal_id = str(self.id).upper()
            self.id = self.terminal_id
        elif self.terminal_id and not self.id:
            self.terminal_id = str(self.terminal_id).upper()
            self.id = self.terminal_id

    def to_dict(self) -> dict:
        d = dict(self._extra)
        # Required non-optional fields in Swift Codable TerminalContent
        d.update({
            "name": self.name,
            "agentType": self.agent_type,
            "command": self.command,
            "workingDirectory": self.working_directory,
            "color": self.color,
            "icon": self.icon,
            "id": str(self.terminal_id).upper(),
            "shellPath": self.shell_path,
            "status": self.status,
            "isManager": bool(self.is_manager),
            "monitorWithOmbro": bool(self.monitor_with_ombro),
            "autoScrollLocked": bool(self.auto_scroll_locked),
            "shortcutMode": self.shortcut_mode if self.shortcut_mode else {"kind": "none"},
            "scrollbackLineCount": int(self.scrollback_line_count),
        })
        # Optional fields: omit if None to match Swift Optional encoding
        if self.font_size is not None:
            d["fontSize"] = float(self.font_size)
        if self.assigned_role_id is not None:
            d["assignedRoleId"] = str(self.assigned_role_id).upper()
        if self.scrollback_file is not None:
            d["scrollbackFile"] = str(self.scrollback_file)
        if self.last_active_at is not None:
            d["lastActiveAt"] = str(self.last_active_at)
        if self.theme_id is not None:
            d["themeId"] = str(self.theme_id)
        if self.font_family is not None:
            d["fontFamily"] = str(self.font_family)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TerminalContent":
        known = {
            "name", "agentType", "command", "workingDirectory", "color",
            "icon", "id", "shellPath", "status", "isManager", "fontSize",
            "monitorWithOmbro", "autoScrollLocked", "shortcutMode", "scrollbackLineCount",
            "assignedRoleId", "scrollbackFile", "lastActiveAt", "themeId", "fontFamily"
        }
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            name=d.get("name", "Terminal"),
            agent_type=d.get("agentType", "generic_shell"),
            command=d.get("command", "bash"),
            working_directory=d.get("workingDirectory", ""),
            color=d.get("color", "#000000"),
            icon=d.get("icon", "terminal"),
            terminal_id=str(d.get("id", uuid.uuid4())).upper(),
            shell_path=d.get("shellPath", "/bin/bash"),
            status=d.get("status", "idle"),
            is_manager=d.get("isManager", False),
            monitor_with_ombro=d.get("monitorWithOmbro", False),
            auto_scroll_locked=d.get("autoScrollLocked", False),
            shortcut_mode=d.get("shortcutMode", {"kind": "none"}),
            scrollback_line_count=int(d.get("scrollbackLineCount", 0)),
            font_size=d.get("fontSize"),
            assigned_role_id=d.get("assignedRoleId"),
            scrollback_file=d.get("scrollbackFile"),
            last_active_at=d.get("lastActiveAt"),
            theme_id=d.get("themeId"),
            font_family=d.get("fontFamily"),
            _extra=extra,
        )


@dataclass
class StickyNoteContent:
    name: str = "Note"
    file_name: str = "Note.md"
    color: str = "yellow"
    font_size: int = 14
    is_previewing: bool = False
    has_custom_name: bool = False
    storage_mode: Dict[str, Any] = field(default_factory=lambda: {"managed": {}})
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        fn = self.file_name if self.file_name.endswith(".md") else f"{self.file_name}.md"
        d.update({
            "color": self.color,
            "fileName": fn,
            "fontSize": int(self.font_size),
            "hasCustomName": self.has_custom_name,
            "isPreviewing": self.is_previewing,
            "storageMode": self.storage_mode,
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StickyNoteContent":
        known = {"color", "fileName", "fontSize", "hasCustomName", "isPreviewing", "storageMode"}
        extra = {k: v for k, v in d.items() if k not in known}
        fn = d.get("fileName", "Note.md")
        name = fn[:-3] if fn.endswith(".md") else fn
        return cls(
            name=name,
            file_name=fn,
            color=d.get("color", "yellow"),
            font_size=int(d.get("fontSize", 14)),
            is_previewing=d.get("isPreviewing", False),
            has_custom_name=d.get("hasCustomName", False),
            storage_mode=d.get("storageMode", {"managed": {}}),
            _extra=extra,
        )


@dataclass
class PortalContent:
    name: str = "Portal"
    current_url: str = ""
    status: str = "idle"  # "idle" | "loading"
    chrome_hidden: bool = False
    storage_scope: str = "isolated"
    portal_id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    source: Optional[Dict[str, Any]] = None
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        # Preserve original source if present, else construct according to current_url
        if self.source is not None:
            src = self.source
        elif self.current_url:
            src = {"url": {"_0": self.current_url}}
        else:
            src = {}

        d.update({
            "id": str(self.portal_id).upper(),
            "name": self.name,
            "currentURL": self.current_url,
            "source": src,
            "status": self.status,
            "chromeHidden": self.chrome_hidden,
            "storageScope": self.storage_scope,
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PortalContent":
        known = {"id", "name", "currentURL", "source", "status", "chromeHidden", "storageScope"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            name=d.get("name", "Portal"),
            current_url=d.get("currentURL", ""),
            source=d.get("source"),
            status=d.get("status", "idle"),
            chrome_hidden=d.get("chromeHidden", False),
            storage_scope=d.get("storageScope", "isolated"),
            portal_id=str(d.get("id", uuid.uuid4())).upper(),
            _extra=extra,
        )


@dataclass
class FileTreeContent:
    name: str = "Files"
    root_path: str = ""
    view_mode: str = "list"  # "list" | "grid"
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "name": self.name,
            "rootPath": self.root_path,
            "viewMode": self.view_mode,
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FileTreeContent":
        known = {"name", "rootPath", "viewMode"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            name=d.get("name", "Files"),
            root_path=d.get("rootPath", ""),
            view_mode=d.get("viewMode", "list"),
            _extra=extra,
        )


@dataclass
class CanvasNode:
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    frame: NodeFrame = field(default_factory=NodeFrame)
    node_type: str = "terminal"  # "terminal", "stickyNote", "portal", "fileTree", "text", "shape", "stroke", "freehand"
    content: Any = field(default_factory=TerminalContent)
    z_index: int = 0
    is_locked: bool = False
    created_at: str = field(default_factory=current_iso8601)
    last_modified_at: str = field(default_factory=current_iso8601)
    _extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        try:
            self.id = str(uuid.UUID(str(self.id))).upper()
        except Exception:
            pass
        if self.node_type == "terminal":
            if isinstance(self.content, StickyNoteContent):
                self.node_type = "stickyNote"
            elif isinstance(self.content, PortalContent):
                self.node_type = "portal"
            elif isinstance(self.content, FileTreeContent):
                self.node_type = "fileTree"

    @property
    def title(self) -> str:
        if hasattr(self.content, "name"):
            return self.content.name
        return self.node_type.capitalize()

    def to_dict(self) -> dict:
        if hasattr(self.content, "to_dict"):
            inner_payload = self.content.to_dict()
        elif isinstance(self.content, dict):
            inner_payload = dict(self.content)
        else:
            inner_payload = {}
        type_obj = {
            self.node_type: {
                "_0": inner_payload
            }
        }
        d = dict(self._extra)
        # In schemaVersion: 2, only "content" is present (no duplicate "type" key)
        d.update({
            "id": self.id,
            "frame": self.frame.to_list(),
            "content": type_obj,
            "zIndex": int(self.z_index),
            "isLocked": self.is_locked,
            "createdAt": self.created_at,
            "lastModifiedAt": self.last_modified_at,
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CanvasNode":
        nid = str(d.get("id", uuid.uuid4())).upper()
        frame = NodeFrame.from_list(d.get("frame", [[100, 100], [400, 300]]))
        raw_content = d.get("content") or d.get("type", {})

        node_type = "terminal"
        content_obj: Any = TerminalContent()

        if isinstance(raw_content, dict):
            for k, val in raw_content.items():
                node_type = k
                inner = val.get("_0", {}) if isinstance(val, dict) else {}
                if k == "terminal":
                    content_obj = TerminalContent.from_dict(inner)
                elif k == "stickyNote":
                    content_obj = StickyNoteContent.from_dict(inner)
                elif k == "portal":
                    content_obj = PortalContent.from_dict(inner)
                elif k == "fileTree":
                    content_obj = FileTreeContent.from_dict(inner)
                else:
                    content_obj = inner
                break

        known = {"id", "frame", "content", "type", "zIndex", "isLocked", "createdAt", "lastModifiedAt"}
        extra = {k: v for k, v in d.items() if k not in known}

        return cls(
            id=nid,
            frame=frame,
            node_type=node_type,
            content=content_obj,
            z_index=int(d.get("zIndex", 0)),
            is_locked=bool(d.get("isLocked", False)),
            created_at=normalize_iso8601(d.get("createdAt")),
            last_modified_at=normalize_iso8601(d.get("lastModifiedAt")),
            _extra=extra,
        )
