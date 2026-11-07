"""StickyNoteContent as persisted by the core (schemaVersion 2), lossless: unknown keys survive a load -> dump round trip. Pure."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .note_colors import DEFAULT_COLOR

_KNOWN = ("color", "fileName", "fontSize", "hasCustomName", "isPreviewing", "storageMode")
MANAGED = {"managed": {}}


@dataclass
class NoteMeta:
    color: str = DEFAULT_COLOR                     # hex or Maestri colour name: kept verbatim
    file_name: str | None = "Note 1.md"            # WITH the .md extension, never a path
    font_size: int = 14
    has_custom_name: bool = False
    is_previewing: bool = False
    storage_mode: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(MANAGED))
    extra: dict[str, Any] = field(default_factory=dict)   # keys we do not know: written back untouched

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NoteMeta":
        return cls(
            color=d.get("color") or DEFAULT_COLOR,
            file_name=d.get("fileName"),
            font_size=int(d.get("fontSize", 14)),
            has_custom_name=bool(d.get("hasCustomName", False)),
            is_previewing=bool(d.get("isPreviewing", False)),
            storage_mode=copy.deepcopy(d.get("storageMode") or MANAGED),
            extra={k: copy.deepcopy(v) for k, v in d.items() if k not in _KNOWN},
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "color": self.color, "fontSize": int(self.font_size), "hasCustomName": self.has_custom_name,
            "isPreviewing": self.is_previewing, "storageMode": copy.deepcopy(self.storage_mode),
        }
        if self.file_name is not None:   # Swift Optional: absent key, never null
            out["fileName"] = self.file_name
        out.update(copy.deepcopy(self.extra))
        return out

    def copy(self) -> "NoteMeta":
        return NoteMeta.from_dict(self.to_dict())

    # ---- derived ------------------------------------------------------------------------------
    @property
    def name(self) -> str:
        """The core's note name: the file name WITHOUT ".md" (it appends the extension itself: passing "x.md" creates "x.md.md")."""
        return note_name(self.file_name or "")

    @property
    def custom_path(self) -> str | None:
        c = self.storage_mode.get("custom")
        return c.get("_0") if isinstance(c, dict) else None

    @property
    def is_custom(self) -> bool:
        return self.custom_path is not None

    def set_custom_path(self, path: str | None) -> None:
        self.storage_mode = {"custom": {"_0": path}} if path else copy.deepcopy(MANAGED)


def note_name(file_name: str) -> str:
    return file_name[:-3] if file_name.lower().endswith(".md") else file_name


def valid_note_name(name: str) -> bool:
    """A name usable as a file stem: no separators, no traversal, no hidden/empty names, no NUL."""
    n = name.strip()
    return bool(n) and n == name and not n.startswith(".") and ".." not in n and not any(c in n for c in "/\\\x00")


def title_from_text(text: str, fallback: str = "Note", limit: int = 40) -> str:
    """Without a custom name the title is the first non-empty line (Markdown markers stripped)."""
    for line in text.splitlines():
        s = line.strip().lstrip("#>-*+ ").strip()
        if s:
            return s if len(s) <= limit else s[: limit - 1] + "…"
    return fallback


def unique_name(base: str, taken: set[str]) -> str:
    low = {t.lower() for t in taken}
    if base.lower() not in low:
        return base
    i = 2
    while f"{base} {i}".lower() in low:
        i += 1
    return f"{base} {i}"
