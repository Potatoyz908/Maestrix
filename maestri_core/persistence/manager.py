"""
Persistence manager for Open Maestri GTK 4 Native.
Atomic reading and writing of workspace.json conforming to schemaVersion: 2.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..models.workspace import WorkspaceDocument


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
        return self.workspaces_dir / workspace_id / "workspace.json"

    def load_workspace(self, workspace_id: str) -> Optional[WorkspaceDocument]:
        path = self.workspace_path(workspace_id)
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkspaceDocument.from_dict(data)

    def save_workspace(self, doc: WorkspaceDocument) -> Path:
        self.ensure_directories()
        target_dir = self.workspaces_dir / doc.payload.id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "workspace.json"

        data = doc.to_dict()
        # Atomic write via temp file + replace
        with tempfile.NamedTemporaryFile("w", dir=str(target_dir), delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            temp_name = tf.name

        os.replace(temp_name, target_path)
        return target_path
