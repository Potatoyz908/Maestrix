"""
Connection models for Maestrix GTK 4 Native.
Compatible with schemaVersion: 2 connections.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


def current_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Point2D:
    x: float
    y: float

    def to_list(self) -> List[float]:
        return [self.x, self.y]

    @classmethod
    def from_list(cls, data: List[float]) -> "Point2D":
        return cls(x=data[0], y=data[1])


@dataclass
class Connection:
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    a_id: str = ""
    b_id: str = ""
    kind: str = "terminal"  # "terminal", "terminalToNote", "portalToPortal", "terminalToPortal", "noteToNote"
    rope_points: List[List[float]] = field(default_factory=list)
    created_at: str = field(default_factory=current_iso8601)
    _extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        try:
            self.id = str(uuid.UUID(str(self.id))).upper()
        except Exception:
            self.id = str(self.id).upper()
        if self.a_id:
            try:
                self.a_id = str(uuid.UUID(str(self.a_id))).upper()
            except Exception:
                self.a_id = str(self.a_id).upper()
        if self.b_id:
            try:
                self.b_id = str(uuid.UUID(str(self.b_id))).upper()
            except Exception:
                self.b_id = str(self.b_id).upper()
        if not self.created_at:
            self.created_at = current_iso8601()

    def to_dict(self) -> dict:
        data = dict(self._extra)
        data.update({
            "id": self.id,
            "ropePoints": self.rope_points,
            "createdAt": self.created_at or current_iso8601(),
        })
        if self.kind == "terminal":
            data["terminalIdA"] = self.a_id
            data["terminalIdB"] = self.b_id
        elif self.kind == "terminalToNote":
            data["terminalId"] = self.a_id
            data["noteNodeId"] = self.b_id
        elif self.kind == "terminalToPortal":
            data["terminalId"] = self.a_id
            data["portalNodeId"] = self.b_id
        elif self.kind == "portalToPortal":
            data["portalIdA"] = self.a_id
            data["portalIdB"] = self.b_id
        elif self.kind == "noteToNote":
            data["noteNodeIdA"] = self.a_id
            data["noteNodeIdB"] = self.b_id
        else:
            data["a"] = self.a_id
            data["b"] = self.b_id
            data["type"] = self.kind
        return data

    @classmethod
    def from_dict(cls, data: dict, default_kind: str = "terminal") -> "Connection":
        cid = data.get("id", str(uuid.uuid4()).upper())
        pts = data.get("ropePoints", [])
        created = data.get("createdAt") or current_iso8601()

        a_id = ""
        b_id = ""
        kind = default_kind

        if "terminalIdA" in data:
            a_id = data["terminalIdA"]
            b_id = data.get("terminalIdB", "")
            kind = "terminal"
        elif "noteNodeId" in data and "terminalId" in data:
            a_id = data["terminalId"]
            b_id = data["noteNodeId"]
            kind = "terminalToNote"
        elif "portalNodeId" in data and "terminalId" in data:
            a_id = data["terminalId"]
            b_id = data["portalNodeId"]
            kind = "terminalToPortal"
        elif "portalIdA" in data:
            a_id = data["portalIdA"]
            b_id = data.get("portalIdB", "")
            kind = "portalToPortal"
        elif "noteNodeIdA" in data:
            a_id = data["noteNodeIdA"]
            b_id = data.get("noteNodeIdB", "")
            kind = "noteToNote"
        elif "a" in data and "b" in data:
            a_id = data["a"]
            b_id = data["b"]
            kind = data.get("type", default_kind)

        known = {
            "id", "ropePoints", "createdAt", "terminalIdA", "terminalIdB",
            "terminalId", "noteNodeId", "portalNodeId", "portalIdA", "portalIdB",
            "noteNodeIdA", "noteNodeIdB", "a", "b", "type"
        }
        extra = {k: v for k, v in data.items() if k not in known}

        return cls(id=cid, a_id=a_id, b_id=b_id, kind=kind, rope_points=pts, created_at=created, _extra=extra)
