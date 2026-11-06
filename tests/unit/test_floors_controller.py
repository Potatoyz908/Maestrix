"""Floors (Sprint 5 T2.3): pure helpers of gtk4-ui/src/ui/floors.py and FloorsController state with a fake client
(floor list mirrored into the document, statuses, switching floors, terminal_cwd / terminal_env, events)."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

gi = pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402

from maestri_core.client.api import MaestriAPIError  # noqa: E402
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload  # noqa: E402
from ui.floors import (FloorsController, badge_text, blocking_reason, branch_error, default_branch,  # noqa: E402
                       describe_preview, error_text, floor_env, slugify)


def pump_until(cond, timeout=3.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# ---- pure ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name, slug", [("Fix login bug", "fix-login-bug"), ("My Feature ../Floor 1!", "my-feature-floor-1"),
                                        ("../../../", "floor"), ("", "floor"), ("Ação Nº2", "a-o-n-2")])
def test_slugify_matches_swift_rule(name, slug):
    assert slugify(name) == slug


def test_default_branch():
    assert default_branch("Fix login bug") == "floor/fix-login-bug"


@pytest.mark.parametrize("branch", ["floor/x", "feature/login-2", "release-1.2"])
def test_valid_branches(branch):
    assert branch_error(branch) is None


@pytest.mark.parametrize("branch", ["", "  ", "has space", "a..b", "-x", "x/", "x.lock", "a//b", "a@{1", "x~1", "a:b",
                                    "a/.hidden", "@"])
def test_invalid_branches(branch):
    assert branch_error(branch)


def test_badge_and_preview_texts():
    assert badge_text({"insertions": 3, "deletions": 1}) == "+3 −1"
    assert badge_text({"insertions": 0, "deletions": 0}) == "" and badge_text(None) == ""
    base = {"floorBranch": "floor/x", "target": "main", "insertions": 2, "deletions": 0}
    assert "1 commit → main (fast-forward)" in describe_preview({**base, "ahead": 1, "fastForward": True})
    assert "3 commits → main (merge commit)" in describe_preview({**base, "ahead": 3})
    assert "+2 −0" in describe_preview({**base, "ahead": 3})
    assert "Nothing to land" in describe_preview({**base, "ahead": 0})
    assert "own branch" in describe_preview({**base, "sameBranch": True})


def test_blocking_reasons_use_spec_wording():
    assert blocking_reason({"dirty": True, "dirtyFiles": ["a"]}).startswith("Commit changes before landing")
    assert blocking_reason({"conflicts": ["a"]}) == "Resolve conflicts in terminal/IDE"
    assert "check out another branch" in blocking_reason({"targetCheckedOut": True, "target": "main"})
    assert blocking_reason({"canLand": True}) is None and blocking_reason(None) is None


def test_error_text_prefers_core_body():
    assert error_text(MaestriAPIError("HTTP 409", status_code=409, response_body="error: merge conflicts: a")) == "merge conflicts: a"
    assert error_text(RuntimeError("boom")) == "boom"


def test_floor_env_has_both_prefixes():
    env = floor_env({"name": "Fix", "branchName": "floor/fix", "worktreePath": "/r/.open-maestri/floors/fix"}, "/r/proj")
    for p in ("MAESTRI", "OMAESTRI"):
        assert env[f"{p}_FLOOR_NAME"] == "Fix" and env[f"{p}_BRANCH_NAME"] == "floor/fix"
        assert env[f"{p}_FLOOR_PATH"] == "/r/.open-maestri/floors/fix"
        assert env[f"{p}_ROOT_PATH"] == "/r/proj" and env[f"{p}_PROJECT_NAME"] == "proj"


# ---- controller ----------------------------------------------------------------------------------------------------
FLOOR_A = {"id": "AAAAAAAA-0000-0000-0000-000000000001", "name": "Fix", "branchName": "floor/fix",
           "worktreePath": "/r/proj/.open-maestri/floors/fix"}
FLOOR_B = {"id": "BBBBBBBB-0000-0000-0000-000000000002", "name": "Exp", "branchName": "floor/exp",
           "worktreePath": "/r/proj/.open-maestri/floors/exp"}


class FakeClient:
    def __init__(self):
        self.floors = [dict(FLOOR_A), dict(FLOOR_B)]
        self.calls = []

    def get_floors(self, ws):
        self.calls.append(("get", ws))
        return [dict(f) for f in self.floors]

    def floor_status(self, fid, workspace_id=None):
        if fid == FLOOR_B["id"]:
            raise MaestriAPIError("boom", status_code=409)
        return {"dirty": True, "insertions": 4, "deletions": 2}

    def delete_floor(self, fid, workspace_id=None, delete_branch=False):
        self.calls.append(("delete", fid, delete_branch))
        self.floors = [f for f in self.floors if f["id"] != fid]
        return True

    def connect_events(self, on_event=None, workspace_id=None):
        self.calls.append(("events", workspace_id))
        return SimpleNamespace(close=lambda: self.calls.append(("events-closed",)))


@pytest.fixture
def ctl():
    doc = WorkspaceDocument(payload=WorkspacePayload(name="W", working_directory="/r/proj"))
    window = SimpleNamespace(client=FakeClient(), document=doc, loaded=True, core_connected=True, auth_failed=False)
    c = FloorsController(window)
    statuses = []
    c.on_status = lambda text, ok: statuses.append((text, ok))
    c.toasts = statuses
    return c


def test_refresh_mirrors_list_into_document_and_loads_statuses(ctl):
    ctl.refresh()
    assert pump_until(lambda: len(ctl.floors) == 2 and FLOOR_A["id"] in ctl.statuses)
    assert ctl.window.document.payload.floors == [FLOOR_A, FLOOR_B], "server list mirrored for the sync (R1)"
    assert ctl.statuses[FLOOR_A["id"]]["dirty"] and FLOOR_B["id"] not in ctl.statuses   # a failed status = no badge


def test_unavailable_core_never_calls_the_api(ctl):
    ctl.window.core_connected = False
    ctl.refresh()
    pump_until(lambda: False, 0.05)
    assert ctl.floors == [] and ctl.window.client.calls == []


def test_switch_floor_terminal_cwd_and_env(ctl):
    ctl.refresh()
    assert pump_until(lambda: len(ctl.floors) == 2)
    assert ctl.terminal_cwd("/default") == "/default" and ctl.terminal_env() is None   # Ground
    ctl.switch_to(FLOOR_A["id"].lower())
    assert ctl.active_floor["name"] == "Fix"
    assert ctl.terminal_cwd("/default") == FLOOR_A["worktreePath"]
    assert ctl.terminal_env()["MAESTRI_FLOOR_NAME"] == "Fix"
    # env follows the terminal's own cwd (reloaded terminals), not the active floor
    assert ctl.terminal_env(FLOOR_B["worktreePath"] + "/src")["MAESTRI_BRANCH_NAME"] == "floor/exp"
    assert ctl.terminal_env("/r/proj") is None
    assert ctl.terminal_env("/r/proj/.open-maestri/floors/fixture") is None, "prefix of another path is not inside"
    ctl.switch_to(None)
    assert ctl.active_floor is None and ctl.terminal_cwd("/d") == "/d"


def test_deleting_active_floor_returns_to_ground(ctl):
    ctl.refresh()
    assert pump_until(lambda: len(ctl.floors) == 2)
    ctl.switch_to(FLOOR_A["id"])
    ctl.delete(FLOOR_A, delete_branch=True)
    assert pump_until(lambda: len(ctl.floors) == 1)
    assert ctl.active_floor_id is None
    assert ("delete", FLOOR_A["id"], True) in ctl.window.client.calls
    assert any("deleted" in t for t, ok in ctl.toasts if ok)


def test_floor_vanishing_elsewhere_resets_active(ctl):
    ctl.refresh()
    assert pump_until(lambda: len(ctl.floors) == 2)
    ctl.switch_to(FLOOR_B["id"])
    ctl.apply([dict(FLOOR_A)])
    assert ctl.active_floor_id is None


def test_events_subscription_and_coalesced_reload(ctl):
    ctl.start_events()
    ctl.start_events()   # same workspace: no second socket
    assert ctl.window.client.calls.count(("events", ctl.workspace_id)) == 1
    before = sum(1 for c in ctl.window.client.calls if c[0] == "get")
    for t in ("floorCreated", "workspaceSaved", "floorChanged"):
        ctl._on_event({"type": t})
    assert pump_until(lambda: sum(1 for c in ctl.window.client.calls if c[0] == "get") == before + 1)
    pump_until(lambda: False, 0.3)
    assert sum(1 for c in ctl.window.client.calls if c[0] == "get") == before + 1, "a burst reloads once"
    ctl.stop_events()
    assert ("events-closed",) in ctl.window.client.calls


# ---- Sprint 6 L3 ---------------------------------------------------------------------------------------------------
def test_delete_warning_says_unlanded_commits_are_discarded():
    from ui.floors import delete_warning
    w = delete_warning(FLOOR_A, {"ahead": 3})
    assert "Delete branch" in w and "DISCARDS 3 unlanded commits" in w and FLOOR_A["branchName"] in w
    assert "DISCARDS 1 unlanded commit:" in delete_warning(FLOOR_A, {"ahead": 1})
    assert "all commits that have not been landed yet" in delete_warning(FLOOR_A, None)
    assert "Keep branch" in w and FLOOR_A["worktreePath"] in w
