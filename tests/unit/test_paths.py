"""OPEN_MAESTRI_DATA_DIR contract (shared with the Swift core): it replaces ~/.open-maestri for every read/write."""
from pathlib import Path
from unittest.mock import patch

from maestri_core.paths import data_dir, run_dir
from maestri_core.persistence.manager import PersistenceManager
from maestri_core.preferences import PreferencesStore


def test_env_var_overrides_home(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path / "data"))
    assert data_dir() == tmp_path / "data"
    assert run_dir() == tmp_path / "data" / "run"
    assert PersistenceManager().workspaces_dir == tmp_path / "data" / "workspaces"
    assert PreferencesStore().path == tmp_path / "data" / "preferences.json"


def test_without_env_var_uses_home_dot_open_maestri(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_MAESTRI_DATA_DIR", raising=False)
    with patch("pathlib.Path.home", return_value=tmp_path):
        assert data_dir() == tmp_path / ".open-maestri"
        assert run_dir() == tmp_path / ".open-maestri" / "run"


def test_empty_env_var_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", "  ")
    with patch("pathlib.Path.home", return_value=tmp_path):
        assert data_dir() == tmp_path / ".open-maestri"


def test_resolved_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path / "a"))
    first = data_dir()
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path / "b"))
    assert (first, data_dir()) == (tmp_path / "a", tmp_path / "b")


def test_session_never_points_at_the_real_dir():
    assert data_dir() != Path.home() / ".open-maestri", "tests/conftest.py must isolate every test"


def test_pty_transport_token_follows_data_dir(monkeypatch, tmp_path):
    from maestri_canvas.pty_transport import read_auth_token
    monkeypatch.delenv("MAESTRI_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path))
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "auth.token").write_text("tok-1\n")
    assert read_auth_token() == "tok-1"
