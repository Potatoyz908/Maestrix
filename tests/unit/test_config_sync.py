"""CLAUDE.md <-> AGENTS.md sync (Sprint 10 item 5): pure pair copy + live Gio.FileMonitor, no echo, SSH ignored."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gtk4-ui" / "src"))
pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402

from ui.config_sync import ConfigFileSync, sync_pair  # noqa: E402


def pump(cond, t=4.0):
    ctx, end = GLib.MainContext.default(), time.time() + t
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def payload(wd, on=True, location="local"):
    return NS(working_directory=str(wd), _extra={"syncConfigFiles": on, "locationType": location})


def test_sync_pair_creates_missing_and_newer_wins(tmp_path):
    assert sync_pair(tmp_path) is None
    (tmp_path / "AGENTS.md").write_text("agents")
    sync_pair(tmp_path)
    assert (tmp_path / "CLAUDE.md").read_text() == "agents"
    (tmp_path / "CLAUDE.md").write_text("claude newer")
    sync_pair(tmp_path, "CLAUDE.md")
    assert (tmp_path / "AGENTS.md").read_text() == "claude newer"


def test_live_sync_both_directions_without_echo(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("v1")
    s = ConfigFileSync()
    try:
        assert s.attach(payload(tmp_path))
        assert (tmp_path / "AGENTS.md").read_text() == "v1"      # pair created at attach
        (tmp_path / "CLAUDE.md").write_text("v2 from claude")
        assert pump(lambda: (tmp_path / "AGENTS.md").read_text() == "v2 from claude")
        (tmp_path / "AGENTS.md").write_text("v3 from agents")
        assert pump(lambda: (tmp_path / "CLAUDE.md").read_text() == "v3 from agents")
        copies = s.copies
        pump(lambda: False, 1.0)                                  # the copies' own events must not bounce back
        assert s.copies == copies == 2
    finally:
        s.detach()
    (tmp_path / "CLAUDE.md").write_text("after detach")
    pump(lambda: False, 0.8)
    assert (tmp_path / "AGENTS.md").read_text() == "v3 from agents"


def test_disabled_or_ssh_workspace_is_ignored(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("x")
    s = ConfigFileSync()
    assert not s.attach(payload(tmp_path, on=False)) and not s.attach(payload(tmp_path, location="ssh"))
    assert not (tmp_path / "AGENTS.md").exists() and not s.active
