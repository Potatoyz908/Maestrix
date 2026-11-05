"""
Fixture and Live Workspace round-trip validation tests for schemaVersion: 2 compliance.
"""

import json
from pathlib import Path
import pytest
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode


def test_testworkspace_fixture_roundtrip():
    fixture_path = Path("Tests/OpenMaestriTests/Fixtures/TestWorkspace.json")
    assert fixture_path.exists(), "TestWorkspace.json fixture must exist"

    with open(fixture_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    doc = WorkspaceDocument.from_dict(raw_data)
    assert doc.schema_version == 2
    assert doc.type == "workspace"
    assert doc.payload.id == "00000000-0000-0000-0000-000000000001"
    assert doc.payload.name == "Test Workspace"
    assert doc.payload.canvas_origin == [9800.0, 8500.0]

    # Dump back to dict
    dumped = doc.to_dict()
    assert dumped["schemaVersion"] == 2
    assert dumped["type"] == "workspace"
    assert dumped["payload"]["id"] == "00000000-0000-0000-0000-000000000001"
    assert dumped["payload"]["name"] == "Test Workspace"
    assert dumped["payload"]["canvasOrigin"] == [9800.0, 8500.0]


def test_live_workspaces_roundtrip():
    workspaces_dir = Path.home() / ".open-maestri" / "workspaces"
    if not workspaces_dir.exists():
        pytest.skip("No live ~/.open-maestri/workspaces directory")

    ws_files = list(workspaces_dir.glob("*/*.json"))
    if not ws_files:
        pytest.skip("No live workspace files found")

    for ws_file in ws_files:
        with open(ws_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        doc = WorkspaceDocument.from_dict(raw_data)
        assert doc.schema_version == 2
        assert doc.type == "workspace"

        dumped = doc.to_dict()
        assert dumped["schemaVersion"] == 2
        assert dumped["type"] == "workspace"
        assert dumped["payload"]["id"] == raw_data["payload"]["id"].upper()

        # Check that canvasOrigin is an array [x, y]
        assert isinstance(dumped["payload"]["canvasOrigin"], list)
        assert len(dumped["payload"]["canvasOrigin"]) == 2

        # Roundtrip node count
        assert len(dumped["payload"]["nodes"]) == len(raw_data["payload"].get("nodes", []))
