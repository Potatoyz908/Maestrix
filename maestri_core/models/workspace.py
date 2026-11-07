"""
Workspace models for Maestrix GTK 4 Native.
Strictly compatible with Maestri schemaVersion: 2.
Preserves unknown fields on round-trip for lossless compatibility.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid

from .node import CanvasNode, current_iso8601
from .connection import Connection


@dataclass
class WorkspaceEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    name: str = "Workspace"
    working_directory: str = ""
    icon: str = "folder"
    color: str = "blue"
    is_pinned: bool = False
    location_type: str = "local"
    created_at: str = field(default_factory=current_iso8601)
    last_opened_at: Optional[str] = None
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "id": str(self.id).upper(),
            "name": self.name,
            "workingDirectory": self.working_directory,
            "icon": self.icon,
            "color": self.color,
            "isPinned": self.is_pinned,
            "locationType": self.location_type,
            "createdAt": self.created_at,
        })
        if self.last_opened_at:
            d["lastOpenedAt"] = self.last_opened_at
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WorkspaceEntry":
        known = {"id", "name", "workingDirectory", "icon", "color", "isPinned", "locationType", "createdAt", "lastOpenedAt"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            id=str(d.get("id", uuid.uuid4())).upper(),
            name=d.get("name", "Workspace"),
            working_directory=d.get("workingDirectory", ""),
            icon=d.get("icon", "folder"),
            color=d.get("color", "blue"),
            is_pinned=d.get("isPinned", False),
            location_type=d.get("locationType", "local"),
            created_at=d.get("createdAt", current_iso8601()),
            last_opened_at=d.get("lastOpenedAt"),
            _extra=extra,
        )


@dataclass
class WorkspacePayload:
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    name: str = "Default"
    working_directory: str = ""
    icon: str = "folder"
    is_pinned: bool = False
    canvas_origin: List[float] = field(default_factory=lambda: [9800.0, 8500.0])
    canvas_zoom: float = 1.0
    nodes: List[CanvasNode] = field(default_factory=list)
    connections: List[Connection] = field(default_factory=list)
    note_connections: List[Connection] = field(default_factory=list)
    portal_connections: List[Connection] = field(default_factory=list)
    portal_to_portal_connections: List[Connection] = field(default_factory=list)
    note_to_note_connections: List[Connection] = field(default_factory=list)
    cross_floor_connections: List[Any] = field(default_factory=list)
    floors: List[Any] = field(default_factory=list)
    drawings: List[Any] = field(default_factory=list)
    created_at: str = field(default_factory=current_iso8601)
    last_modified_at: str = field(default_factory=current_iso8601)
    last_opened_at: Optional[str] = None
    _extra: Dict[str, Any] = field(default_factory=dict)

    def all_connections(self) -> List[Connection]:
        return (
            self.connections
            + self.note_connections
            + self.portal_connections
            + self.portal_to_portal_connections
            + self.note_to_note_connections
        )

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "id": str(self.id).upper(),
            "name": self.name,
            "workingDirectory": self.working_directory,
            "icon": self.icon,
            "isPinned": self.is_pinned,
            "locationType": d.get("locationType", "local"),
            "preferredIDE": d.get("preferredIDE", "cursor"),
            "syncConfigFiles": d.get("syncConfigFiles", False),
            "canvasOrigin": [float(self.canvas_origin[0]), float(self.canvas_origin[1])],
            "canvasZoom": float(self.canvas_zoom),
            "nodes": [n.to_dict() for n in self.nodes],
            "connections": [c.to_dict() for c in self.connections],
            "noteConnections": [c.to_dict() for c in self.note_connections],
            "portalConnections": [c.to_dict() for c in self.portal_connections],
            "portalToPortalConnections": [c.to_dict() for c in self.portal_to_portal_connections],
            "noteToNoteConnections": [c.to_dict() for c in self.note_to_note_connections],
            "crossFloorConnections": self.cross_floor_connections,
            "floors": self.floors,
            "drawings": self.drawings,
            "createdAt": self.created_at,
            "lastModifiedAt": self.last_modified_at,
        })
        if self.last_opened_at:
            d["lastOpenedAt"] = self.last_opened_at
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WorkspacePayload":
        raw_nodes = d.get("nodes", [])
        nodes = [CanvasNode.from_dict(n) for n in raw_nodes]

        conns = [Connection.from_dict(c, "terminal") for c in d.get("connections", [])]
        note_conns = [Connection.from_dict(c, "terminalToNote") for c in d.get("noteConnections", [])]
        portal_conns = [Connection.from_dict(c, "terminalToPortal") for c in d.get("portalConnections", [])]
        p2p_conns = [Connection.from_dict(c, "portalToPortal") for c in d.get("portalToPortalConnections", [])]
        n2n_conns = [Connection.from_dict(c, "noteToNote") for c in d.get("noteToNoteConnections", [])]

        origin = d.get("canvasOrigin", [9800.0, 8500.0])
        if isinstance(origin, dict) and "x" in origin and "y" in origin:
            origin = [float(origin["x"]), float(origin["y"])]
        elif not isinstance(origin, list) or len(origin) < 2:
            origin = [9800.0, 8500.0]

        known = {
            "id", "name", "workingDirectory", "icon", "isPinned",
            "canvasOrigin", "canvasZoom", "nodes", "connections", "noteConnections",
            "portalConnections", "portalToPortalConnections", "noteToNoteConnections",
            "crossFloorConnections", "floors", "drawings", "createdAt", "lastModifiedAt", "lastOpenedAt"
        }
        extra = {k: v for k, v in d.items() if k not in known}

        return cls(
            id=str(d.get("id", uuid.uuid4())).upper(),
            name=d.get("name", "Default"),
            working_directory=d.get("workingDirectory", ""),
            icon=d.get("icon", "folder"),
            is_pinned=d.get("isPinned", False),
            canvas_origin=[float(origin[0]), float(origin[1])],
            canvas_zoom=float(d.get("canvasZoom", 1.0)),
            nodes=nodes,
            connections=conns,
            note_connections=note_conns,
            portal_connections=portal_conns,
            portal_to_portal_connections=p2p_conns,
            note_to_note_connections=n2n_conns,
            cross_floor_connections=d.get("crossFloorConnections", []),
            floors=d.get("floors", []),
            drawings=d.get("drawings", []),
            created_at=d.get("createdAt", current_iso8601()),
            last_modified_at=d.get("lastModifiedAt", current_iso8601()),
            last_opened_at=d.get("lastOpenedAt"),
            _extra=extra,
        )


@dataclass
class WorkspaceDocument:
    schema_version: int = 2
    type: str = "workspace"
    payload: WorkspacePayload = field(default_factory=WorkspacePayload)
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "schemaVersion": int(self.schema_version),
            "type": self.type,
            "payload": self.payload.to_dict(),
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WorkspaceDocument":
        schema_version = int(d.get("schemaVersion", 2))
        doc_type = d.get("type", "workspace")
        payload = WorkspacePayload.from_dict(d.get("payload", {}))
        known = {"schemaVersion", "type", "payload"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(schema_version=schema_version, type=doc_type, payload=payload, _extra=extra)


@dataclass
class SidebarGroup:
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    name: str = "Group"
    is_collapsed: bool = False
    items: List[str] = field(default_factory=list)
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "id": str(self.id).upper(),
            "name": self.name,
            "isCollapsed": bool(self.is_collapsed),
            "items": [str(it).upper() for it in self.items],
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SidebarGroup":
        known = {"id", "name", "isCollapsed", "items"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            id=str(d.get("id", uuid.uuid4())).upper(),
            name=d.get("name", "Group"),
            is_collapsed=bool(d.get("isCollapsed", False)),
            items=[str(it).upper() for it in d.get("items", [])],
            _extra=extra,
        )


@dataclass
class SidebarLayout:
    schema_version: int = 2
    top_level_items: List[str] = field(default_factory=list)
    groups: List[SidebarGroup] = field(default_factory=list)
    _extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "schemaVersion": int(self.schema_version),
            "topLevelItems": [str(it).upper() for it in self.top_level_items],
            "groups": [g.to_dict() for g in self.groups],
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SidebarLayout":
        known = {"schemaVersion", "topLevelItems", "groups"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            schema_version=int(d.get("schemaVersion", 2)),
            top_level_items=[str(it).upper() for it in d.get("topLevelItems", [])],
            groups=[SidebarGroup.from_dict(g) for g in d.get("groups", [])],
            _extra=extra,
        )
