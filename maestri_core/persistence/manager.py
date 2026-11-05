"""
Persistence manager for Open Maestri GTK 4 Native.
Atomic reading and writing of workspace.json conforming to schemaVersion: 2.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..models.workspace import SidebarLayout, WorkspaceDocument


class PersistenceManager:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (Path.home() / ".open-maestri")
        self.workspaces_dir = self.base_dir / "workspaces"

    def ensure_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "run").mkdir(parents=True, exist_ok=True)

    def workspace_path(self, workspace_id: str) -> Path:
        if not workspace_id or ".." in workspace_id or "/" in workspace_id or "\\" in workspace_id:
            raise ValueError(f"Invalid workspace ID: {workspace_id!r}")
        safe_id = Path(workspace_id).name
        if not safe_id or safe_id in (".", ".."):
            raise ValueError(f"Invalid workspace ID: {workspace_id!r}")
        target = (self.workspaces_dir / safe_id / "workspace.json").resolve()
        if not str(target).startswith(str(self.workspaces_dir.resolve())):
            raise ValueError(f"Directory traversal detected: {workspace_id!r}")
        return target

    def load_workspace(self, workspace_id: str) -> Optional[WorkspaceDocument]:
        path = self.workspace_path(workspace_id)
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkspaceDocument.from_dict(data)

    def save_workspace(self, doc: WorkspaceDocument) -> Path:
        self.ensure_directories()
        safe_id = Path(doc.payload.id).name
        if not safe_id or safe_id in (".", ".."):
            raise ValueError(f"Invalid workspace ID: {doc.payload.id!r}")
        target_dir = self.workspaces_dir / safe_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "workspace.json"

        data = doc.to_dict()
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=str(target_dir), delete=False, encoding="utf-8") as tf:
                temp_name = tf.name
                json.dump(data, tf, indent=2, ensure_ascii=False)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(temp_name, target_path)
            temp_name = None
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

        return target_path

    @property
    def sidebar_layout_path(self) -> Path:
        return self.base_dir / "sidebar-layout.json"

    def load_sidebar_layout(self) -> SidebarLayout:
        path = self.sidebar_layout_path
        if not path.is_file():
            return SidebarLayout()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SidebarLayout.from_dict(data)

    def save_sidebar_layout(self, layout: SidebarLayout) -> Path:
        self.ensure_directories()
        path = self.sidebar_layout_path
        data = layout.to_dict()
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=str(self.base_dir), delete=False, encoding="utf-8") as tf:
                temp_name = tf.name
                json.dump(data, tf, indent=2, ensure_ascii=False)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(temp_name, path)
            temp_name = None
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
        return path
