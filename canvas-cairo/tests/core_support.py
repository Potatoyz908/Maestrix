"""Shared by the canvas tests: the REAL open-maestri-server on a throwaway data dir, plus the guard that keeps the whole
canvas-cairo session away from the user's real ~/.open-maestri. Both live in tests/data_dir_isolation.py (one copy for the
root suite and this one): OPEN_MAESTRI_DATA_DIR when the core honours it, else a private user+mount namespace."""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests import data_dir_isolation as isolation  # noqa: E402

_REAL_BEFORE: dict = {}
_TMP_ROOT: list[Path] = []


def guard_start() -> None:
    """pytest_configure: every test of the session sees OPEN_MAESTRI_DATA_DIR = a throwaway dir."""
    root = Path(tempfile.mkdtemp(prefix="open-maestri-canvas-tests-"))
    _TMP_ROOT.append(root)
    isolation.isolate_session(root)
    _REAL_BEFORE.clear()
    _REAL_BEFORE.update(isolation.snapshot())


def guard_finish(session) -> None:
    """pytest_sessionfinish: FAIL the run when the real ~/.open-maestri changed."""
    changes = isolation.diff(_REAL_BEFORE, isolation.snapshot())
    if changes:
        listing = "\n  ".join(changes[:50])
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        (reporter.write_line if reporter else print)(
            f"\nERROR: the test run touched the REAL {isolation.REAL_DATA_DIR} ({len(changes)} change(s)); tests must use "
            f"{isolation.DATA_DIR_ENV} (if you were using Maestrix meanwhile, re-run to confirm):\n  {listing}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    while _TMP_ROOT:
        shutil.rmtree(_TMP_ROOT.pop(), ignore_errors=True)


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    """{"port", "token", "home", "data_dir"}: data_dir is <home>/.open-maestri, never the real one."""
    server = isolation.start_isolated_server(tmp_path_factory.mktemp("home"))
    try:
        yield {k: server[k] for k in ("port", "token", "home", "data_dir")}
    finally:   # runs on every path: never leave a server behind
        isolation.stop_server(server["proc"])
