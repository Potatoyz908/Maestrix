import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch

from maestri_core.persistence.manager import PersistenceManager
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload, SidebarLayout, SidebarGroup


def test_persistence_directories_creation(tmp_path):
    pm = PersistenceManager(base_dir=tmp_path / ".open-maestri")
    pm.ensure_directories()
    assert (tmp_path / ".open-maestri" / "workspaces").is_dir()
    assert (tmp_path / ".open-maestri" / "notes").is_dir()
    assert (tmp_path / ".open-maestri" / "run").is_dir()


def test_persistence_workspace_path_traversal_protection(tmp_path):
    pm = PersistenceManager(base_dir=tmp_path / ".open-maestri")
    pm.ensure_directories()

    # Valid ID works
    p = pm.workspace_path("A1B2C3D4")
    assert p == tmp_path / ".open-maestri" / "workspaces" / "A1B2C3D4" / "workspace.json"

    # Directory traversal attempts should raise ValueError
    with pytest.raises(ValueError):
        pm.workspace_path("../etc/passwd")

    with pytest.raises(ValueError):
        pm.workspace_path("..")

    with pytest.raises(ValueError):
        pm.workspace_path(".")


def test_persistence_save_and_load_workspace_atomic(tmp_path):
    pm = PersistenceManager(base_dir=tmp_path / ".open-maestri")
    payload = WorkspacePayload(name="Persistence Test", id="TEST-PERSIST-1")
    doc = WorkspaceDocument(payload=payload)

    target_path = pm.save_workspace(doc)
    assert target_path.is_file()
    assert target_path.name == "workspace.json"

    loaded = pm.load_workspace("TEST-PERSIST-1")
    assert loaded is not None
    assert loaded.payload.id == "TEST-PERSIST-1"
    assert loaded.payload.name == "Persistence Test"
    assert loaded.schema_version == 2


def test_persistence_save_workspace_cleanup_on_error(tmp_path):
    pm = PersistenceManager(base_dir=tmp_path / ".open-maestri")
    payload = WorkspacePayload(name="Error Test", id="TEST-ERR-1")
    doc = WorkspaceDocument(payload=payload)

    # Simulate an error during json.dump
    with patch("json.dump", side_effect=IOError("Simulated disk error")):
        with pytest.raises(IOError):
            pm.save_workspace(doc)

    # Check that no temporary .tmp files are left behind in target directory
    target_dir = tmp_path / ".open-maestri" / "workspaces" / "TEST-ERR-1"
    if target_dir.exists():
        tmp_files = [f for f in target_dir.iterdir() if f.name != "workspace.json"]
        assert len(tmp_files) == 0


def test_persistence_sidebar_layout_round_trip(tmp_path):
    pm = PersistenceManager(base_dir=tmp_path / ".open-maestri")
    layout = SidebarLayout(
        schema_version=2,
        top_level_items=["WS-1", "WS-2"],
        groups=[
            SidebarGroup(name="Frontend", items=["WS-3", "WS-4"])
        ]
    )

    path = pm.save_sidebar_layout(layout)
    assert path.is_file()

    loaded = pm.load_sidebar_layout()
    assert loaded.schema_version == 2
    assert loaded.top_level_items == ["WS-1", "WS-2"]
    assert len(loaded.groups) == 1
    assert loaded.groups[0].name == "Frontend"
    assert loaded.groups[0].items == ["WS-3", "WS-4"]
