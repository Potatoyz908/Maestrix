"""
Tests for B7: Authentication error handling and graceful degradation in GTK 4 OpenMaestriWindow.
Validates that a 401 Unauthorized prevents silent demo creation and disables sync (no POSTs).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio

from maestri_core.client.api import MaestriClient, AuthenticationError
from ui.window import OpenMaestriWindow


@pytest.fixture(scope="module")
def maestri_app():
    app = Gtk.Application(
        application_id="org.openmaestri.authtest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    yield app


def test_auth_failure_disables_sync_and_does_not_save(maestri_app):
    """When the server responds 401, OpenMaestriWindow flags auth_failed, disables sync, and performs 0 POSTs."""
    client = MaestriClient(port=12345, token="bad_token")

    err_401 = MagicMock()
    err_401.ok = False
    err_401.status_code = 401
    err_401.text = "Unauthorized"

    with patch.object(client.session, "get", return_value=err_401):
        with patch.object(client.session, "post") as mock_post:
            win = OpenMaestriWindow(maestri_app, client)

            # 1. Auth failure flag must be True
            assert win.auth_failed is True

            # 2. Sync must be disabled (None) so no debounced/periodic saves occur
            assert win.sync is None

            # 3. Status label must reflect invalid token
            assert "Token Inválido" in win.lbl_status.get_label()

            # 4. Trigger manual save action
            win._save_to_backend()

            # 5. Verify zero POSTs were made to the server
            assert mock_post.call_count == 0

            win.close()
