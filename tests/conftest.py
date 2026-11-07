"""
Pytest configuration and fixtures for Open Maestri tests.
Provides mocking setup for GTK, WebKit2, and system dependencies.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import pytest


# Add project directories to path for imports
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "canvas-cairo"))
sys.path.insert(0, str(REPO_ROOT / "canvas-cairo" / "src"))
sys.path.insert(0, str(REPO_ROOT / "gtk4-ui" / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from tests import data_dir_isolation as _isolation  # noqa: E402

_REAL_DATA_DIR_BEFORE: dict = {}


def pytest_configure(config):
    """Before any test module is imported: point OPEN_MAESTRI_DATA_DIR at a throwaway dir and record the real one."""
    root = Path(tempfile.mkdtemp(prefix="open-maestri-tests-"))
    config._open_maestri_tmp_root = root
    _isolation.isolate_session(root)
    _REAL_DATA_DIR_BEFORE.clear()
    _REAL_DATA_DIR_BEFORE.update(_isolation.snapshot())


def pytest_sessionfinish(session, exitstatus):
    """FAIL the run when anything under the real ~/.open-maestri changed while the tests ran."""
    changes = _isolation.diff(_REAL_DATA_DIR_BEFORE, _isolation.snapshot())
    if changes:
        listing = "\n  ".join(changes[:50])
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        (reporter.write_line if reporter else print)(
            f"\nERROR: the test run touched the REAL {_isolation.REAL_DATA_DIR} ({len(changes)} change(s)); tests must use "
            f"{_isolation.DATA_DIR_ENV} (if you were using Open Maestri meanwhile, re-run to confirm):\n  {listing}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    root = getattr(session.config, "_open_maestri_tmp_root", None)
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory, monkeypatch):
    """Every test gets its own empty data dir through the shared OPEN_MAESTRI_DATA_DIR contract. It lives OUTSIDE the
    test's own tmp_path, so tests that list tmp_path see only what they wrote."""
    data = tmp_path_factory.mktemp("open-maestri-data")
    monkeypatch.setenv(_isolation.DATA_DIR_ENV, str(data))
    return data


@pytest.fixture
def mock_gtk():
    """Mock GTK3 imports for headless testing."""
    with patch('gi.require_version'), \
         patch('gi.repository.Gtk'), \
         patch('gi.repository.Gdk'), \
         patch('gi.repository.WebKit2'), \
         patch('gi.repository.GLib'), \
         patch('gi.repository.GdkPixbuf'):
        yield


@pytest.fixture
def mock_subprocess():
    """Mock subprocess for testing server startup."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.side_effect = [
        "Open Maestri Server active on port 12345\n",
        "Unix Domain Socket: /tmp/maestri.sock\n",
        "",
    ]

    with patch('subprocess.Popen', return_value=mock_proc) as mock_popen:
        yield mock_popen


@pytest.fixture
def temp_workspace():
    """Create temporary workspace directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_dir = Path(tmpdir) / "workspace"
        workspace_dir.mkdir()
        yield workspace_dir


@pytest.fixture
def mock_server_binary(tmp_path):
    """Create mock server binary in temp directory."""
    server_dir = tmp_path / "bin"
    server_dir.mkdir()
    server_bin = server_dir / "open-maestri-server"
    server_bin.touch()
    server_bin.chmod(0o755)
    return server_bin


@pytest.fixture
def mock_web_dist(tmp_path):
    """Create mock Web distribution directory."""
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>Test</body></html>")
    return web_dir


@pytest.fixture
def mock_app_icon(tmp_path):
    """Create mock app icon file."""
    icon_dir = tmp_path / "icons"
    icon_dir.mkdir()
    icon_file = icon_dir / "icon_512x512.png"
    icon_file.write_bytes(b"PNG mock data")
    return icon_file


@pytest.fixture
def mock_environ():
    """Capture and restore environment variables."""
    original_env = os.environ.copy()
    yield os.environ
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def performance_baseline():
    """Performance baseline thresholds."""
    return {
        "coordinate_conversion_ms": 1.0,  # < 1ms
        "cairo_100_shapes_ms": 16.0,      # < 16ms for 60fps
        "server_startup_s": 2.0,          # < 2s
        "workspace_load_s": 3.0,          # < 3s
    }


@pytest.fixture(autouse=True)
def reset_modules():
    """Reset imported modules between tests to avoid state leakage."""
    yield
    # Clean up module imports to prevent cross-test contamination
    modules_to_remove = [m for m in sys.modules.keys() if "open_maestri" in m]
    for mod in modules_to_remove:
        del sys.modules[mod]
