"""tests/data_dir_isolation.py: start_isolated_server's result works as a dict AND as a context manager with attributes."""
import subprocess
import sys

import pytest

from tests.data_dir_isolation import IsolatedServer, start_isolated_server, stop_server


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def test_dict_and_attribute_access_and_idempotent_stop(tmp_path):
    proc = _sleeper()
    srv = IsolatedServer(proc=proc, port=4321, token="t", home=tmp_path, data_dir=tmp_path / ".open-maestri")
    assert srv["port"] == srv.port == 4321 and srv.token == "t" and srv.base_url == "http://127.0.0.1:4321"
    with pytest.raises(AttributeError):
        _ = srv.missing
    stop_server(srv["proc"])          # the dict style used by codice/nexus/sentinela/integration tests
    assert proc.poll() is not None
    with srv as same:                 # the context-manager style; __exit__ after a manual stop must not fail
        assert same is srv
    stop_server(srv)                  # also accepts the server object, again idempotent


def test_context_manager_stops_the_process(tmp_path):
    proc = _sleeper()
    with IsolatedServer(proc=proc, port=1, token="", home=tmp_path, data_dir=tmp_path) as srv:
        assert srv.proc.poll() is None
    assert proc.poll() is not None


def test_live_server_both_styles(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path / "unused"))
    with start_isolated_server(tmp_path) as srv:   # skips with a message when the binary is not built
        assert srv["port"] == srv.port > 0 and srv.base_url.endswith(str(srv.port))
        assert srv.data_dir == tmp_path / ".open-maestri"
    assert srv.proc.poll() is not None
