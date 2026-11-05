"""
FileTree backend model for Open Maestri GTK 4 Native.

Connects a `FileTreeContent` node (rootPath) to the REST endpoints
`GET /api/files` (directory listing, folders-first, with git badges) and
`GET /api/git/status` (porcelain changes). Pure Python / no GTK: blocking
calls are meant to run on a worker thread (see `refresh_async`).

Badges: M modified, A added, D deleted, R renamed, ? untracked.
Directories inherit the highest-priority badge of any changed descendant.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

from .client.api import MaestriAPIError
from .models.node import FileTreeContent

# Priority when folding descendants' badges onto a folder.
_BADGE_PRIORITY = {"D": 5, "M": 4, "R": 3, "A": 2, "?": 1}


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str            # relative to the tree root, "/"-separated
    is_dir: bool = False
    git_status: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FileEntry":
        return cls(
            name=str(d.get("name", "")),
            path=str(d.get("path", "")),
            is_dir=bool(d.get("isDir", False)),
            git_status=d.get("gitStatus"),
        )


@dataclass(frozen=True)
class GitChange:
    path: str
    status: str

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GitChange":
        return cls(path=str(d.get("path", "")), status=str(d.get("status", "")))


class FileTreeModel:
    """Lazy directory tree rooted at `content.root_path`."""

    def __init__(self, client, content: FileTreeContent) -> None:
        self.client = client
        self.content = content
        self.children: Dict[str, List[FileEntry]] = {}   # rel dir ("" = root) -> entries
        self.changes: Dict[str, str] = {}                # rel file path -> badge
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def root(self) -> str:
        return self.content.root_path

    # -- loading (blocking; call from a worker thread) ----------------------
    def load_dir(self, rel: str = "") -> List[FileEntry]:
        try:
            raw = self.client.list_files(root=self.root, path=rel)
            entries = [FileEntry.from_dict(d) for d in raw]
            self.error = None
        except (MaestriAPIError, requests.RequestException, ValueError, TypeError) as exc:
            self.error = str(exc) or exc.__class__.__name__
            entries = []
        with self._lock:
            self.children[rel] = entries
        return entries

    def load_git_status(self) -> Dict[str, str]:
        try:
            raw = self.client.get_git_status(root=self.root)
            changes = {c.path.rstrip("/"): c.status for c in map(GitChange.from_dict, raw)}
        except (MaestriAPIError, requests.RequestException, ValueError, TypeError):
            changes = {}
        with self._lock:
            self.changes = changes
        return changes

    def refresh(self) -> None:
        """Reload git status and every directory loaded so far (root always)."""
        self.load_git_status()
        for rel in sorted(set(self.children) | {""}):
            self.load_dir(rel)

    def refresh_async(self, on_done: Optional[Callable[[], None]] = None) -> threading.Thread:
        def run() -> None:
            self.refresh()
            if on_done:
                on_done()
        t = threading.Thread(target=run, name="maestri-filetree", daemon=True)
        t.start()
        return t

    # -- queries ------------------------------------------------------------
    def entries(self, rel: str = "") -> List[FileEntry]:
        with self._lock:
            return list(self.children.get(rel, []))

    def badge_for(self, entry: FileEntry) -> Optional[str]:
        """Badge for a file, or the strongest badge among a folder's changed descendants."""
        if entry.git_status:
            return entry.git_status
        with self._lock:
            changes = dict(self.changes)
        if not entry.is_dir:
            return changes.get(entry.path)
        prefix = entry.path.rstrip("/") + "/"
        found = [b for p, b in changes.items() if p == entry.path or p.startswith(prefix)]
        return max(found, key=lambda b: _BADGE_PRIORITY.get(b, 0)) if found else None
