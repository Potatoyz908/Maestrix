import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ui.workspace_dialogs import create_workspace_record
from ui.sidebar import SidebarView


def test_create_workspace_record_offline(isolated_data_dir, tmp_path):
    # Client that fails (offline)
    mock_client = MagicMock()
    mock_client.create_workspace.side_effect = ConnectionError("Offline")

    ws_id = create_workspace_record(mock_client, name="Test Offline WS", wd=str(tmp_path), icon="folder")
    assert ws_id is not None
    assert len(ws_id) == 36

    # Verify manifest.json
    manifest_path = isolated_data_dir / "manifest.json"
    assert manifest_path.exists()
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert any(ws["id"] == ws_id and ws["name"] == "Test Offline WS" for ws in data["workspaces"])

    # Verify workspace.json
    ws_json_path = isolated_data_dir / "workspaces" / ws_id / "workspace.json"
    assert ws_json_path.exists()
    with open(ws_json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["schemaVersion"] == 2
    assert doc["payload"]["id"] == ws_id
    assert doc["payload"]["name"] == "Test Offline WS"
    assert doc["payload"]["workingDirectory"] == str(tmp_path)


def test_sidebar_add_header_menu_items(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    mock_client = MagicMock()
    mock_client.get_workspaces.return_value = []
    mock_client.get_sidebar_layout.return_value = MagicMock(groups=[])

    win = Gtk.Window()
    sidebar = SidebarView(mock_client)
    win.set_child(sidebar)
    win.present()
    btn = Gtk.Button()
    sidebar.append(btn)
    sidebar._on_add_header_clicked(btn)
    win.destroy()
