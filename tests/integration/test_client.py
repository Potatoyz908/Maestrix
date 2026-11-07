"""
Integration tests for MaestriClient.
"""

import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from maestri_core.client.api import MaestriClient
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload


def test_client_initialization_and_discovery():
    client = MaestriClient(port=43129, token="test_token_123")
    assert client.port == 43129
    assert client.token == "test_token_123"
    assert client.base_url == "http://127.0.0.1:43129"
    assert client.headers["Authorization"] == "Bearer test_token_123"


def test_client_offline_graceful_handling():
    # An unused high port
    client = MaestriClient(port=59999, token="dummy")
    assert not client.is_connected()
    assert client.get_workspace() is None
    assert client.get_workspaces() == []
    assert client.get_note("nonexistent") == ""
    assert not client.save_workspace(WorkspaceDocument(payload=WorkspacePayload()))
