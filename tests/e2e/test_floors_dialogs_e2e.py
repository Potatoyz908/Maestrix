"""
Sprint 5 E2E (Xvfb): Floors UI and Dialogs (`FloorButton`, `NewFloorDialog`, `LandFloorDialog`, `DeleteFloorDialog`).

Covers:
1. Floating FloorButton overlay on the canvas and popover list (Ground + floors) with R1 document sync.
2. NewFloorDialog sensitivity, auto-slug branch generation, New vs Existing branch modes, ref validation, and API error recovery.
3. LandFloorDialog preview states (fast-forward, conflicts, dirty worktree, sameBranch, preview/land error recovery) and Merge sensitivity.
4. DeleteFloorDialog 3-button contract (Cancel, Keep Branch, Delete Branch) and branch deletion flag propagation.
5. Visual snapshots in both `artifacts/sprint5/` and `artifacts/e2e-screenshots/`.
"""

import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.demo import screenshot  # noqa: E402
from maestri_core.client.api import MaestriAPIError, MaestriClient  # noqa: E402
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload  # noqa: E402
from ui.floors import DeleteFloorDialog, FloorButton, LandFloorDialog, NewFloorDialog  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def wait_until(cond, timeout: float = 8.0, interval: float = 0.01) -> bool:
    """Pump the GLib MainContext until `cond()` is truthy or `timeout` elapses."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(interval)
    return bool(cond())


def pump(seconds: float = 0.15) -> None:
    wait_until(lambda: False, timeout=seconds)


def save_dual_screenshot(widget: Gtk.Widget, sprint5_name: str, e2e_name: str) -> None:
    """Save visual regression snapshot to both `artifacts/sprint5/` and `artifacts/e2e-screenshots/`."""
    sprint5_dir = ROOT_DIR / "artifacts" / "sprint5"
    e2e_dir = ROOT_DIR / "artifacts" / "e2e-screenshots"
    sprint5_dir.mkdir(parents=True, exist_ok=True)
    e2e_dir.mkdir(parents=True, exist_ok=True)
    p1 = sprint5_dir / sprint5_name
    p2 = e2e_dir / e2e_name
    screenshot(widget, str(p1))
    shutil.copyfile(p1, p2)
    assert p1.stat().st_size > 1000
    assert p2.stat().st_size > 1000


class StubFloorsBackend(MaestriClient):
    """Deterministic in-memory Floors backend exercising all preview and mutation branches."""

    def __init__(self) -> None:
        super().__init__(port=1, auth_token="sprint5-token")
        self.doc = WorkspaceDocument(
            payload=WorkspacePayload(name="Sprint5Repo", working_directory="/tmp/sprint5-repo")
        )
        self.floors = [
            {
                "id": "F1111111-0000-0000-0000-000000000001",
                "name": "Auth Refactor",
                "branchName": "floor/auth-refactor",
                "worktreePath": "/tmp/sprint5-repo/.open-maestri/floors/auth-refactor",
                "hooks": {"setup": ["npm ci"], "run": ["npm test"], "teardown": []},
            },
            {
                "id": "F2222222-0000-0000-0000-000000000002",
                "name": "UI Polish",
                "branchName": "floor/ui-polish",
                "worktreePath": "/tmp/sprint5-repo/.open-maestri/floors/ui-polish",
                "hooks": {},
            },
        ]
        self.calls: list[tuple] = []
        self.fail_next_land = False
        self.lock = threading.Lock()

    def check_connection_status(self) -> str:
        return "connected"

    def get_workspace(self, workspace_id=None):
        return self.doc

    def save_workspace_dict(self, data, workspace_id=None) -> bool:
        return True

    def get_floors(self, workspace_id=None):
        with self.lock:
            self.calls.append(("get_floors", workspace_id))
            return [dict(f) for f in self.floors]

    def create_floor(
        self,
        name,
        branch_name=None,
        workspace_id=None,
        working_directory=None,
        base_branch=None,
        existing_branch=False,
    ):
        with self.lock:
            self.calls.append(("create_floor", name, branch_name, base_branch, existing_branch))
            if branch_name == "floor/duplicate":
                raise MaestriAPIError(
                    "HTTP 409",
                    status_code=409,
                    response_body="error: branch 'floor/duplicate' already exists",
                )
            created = {
                "id": f"F3333333-0000-0000-0000-{len(self.floors) + 1:012d}",
                "name": name,
                "branchName": branch_name,
                "worktreePath": f"/tmp/sprint5-repo/.open-maestri/floors/{name.lower().replace(' ', '-')}",
                "hooks": {},
            }
            self.floors.append(created)
            return created

    def delete_floor(self, floor_id, workspace_id=None, remove_worktree=True, delete_branch=False) -> bool:
        with self.lock:
            self.calls.append(("delete_floor", floor_id, delete_branch))
            self.floors = [f for f in self.floors if f["id"] != floor_id]
            return True

    def floor_land_preview(self, floor_id, target=None, workspace_id=None):
        with self.lock:
            self.calls.append(("land_preview", floor_id, target))
        target = target or "main"
        branches = ["main", "release", "dirty-target", "floor/auth-refactor", "broken-target"]
        if target == "broken-target":
            raise MaestriAPIError("HTTP 500", status_code=500, response_body="error: git merge-tree failed")
        if target == "floor/auth-refactor":
            return {
                "floorBranch": "floor/auth-refactor",
                "target": target,
                "branches": branches,
                "dirty": False,
                "dirtyFiles": [],
                "sameBranch": True,
                "ahead": 0,
                "fastForward": False,
                "stat": "",
                "conflicts": [],
                "canLand": False,
            }
        if target == "dirty-target":
            return {
                "floorBranch": "floor/auth-refactor",
                "target": target,
                "branches": branches,
                "dirty": True,
                "dirtyFiles": ["src/auth.py", "tests/test_auth.py"],
                "sameBranch": False,
                "ahead": 3,
                "fastForward": True,
                "stat": "2 files changed, 45 insertions(+), 8 deletions(-)",
                "conflicts": [],
                "canLand": False,
            }
        if target == "release":
            return {
                "floorBranch": "floor/auth-refactor",
                "target": target,
                "branches": branches,
                "dirty": False,
                "dirtyFiles": [],
                "sameBranch": False,
                "ahead": 3,
                "fastForward": False,
                "stat": "3 files changed, 60 insertions(+), 12 deletions(-)",
                "conflicts": ["src/auth.py", "src/config.py"],
                "canLand": False,
            }
        return {
            "floorBranch": "floor/auth-refactor",
            "target": target,
            "branches": branches,
            "dirty": False,
            "dirtyFiles": [],
            "sameBranch": False,
            "ahead": 3,
            "fastForward": True,
            "stat": "2 files changed, 45 insertions(+), 8 deletions(-)",
            "conflicts": [],
            "canLand": True,
        }

    def land_floor(self, floor_id, target, workspace_id=None):
        with self.lock:
            self.calls.append(("land_floor", floor_id, target))
            if self.fail_next_land:
                self.fail_next_land = False
                raise MaestriAPIError(
                    "HTTP 409",
                    status_code=409,
                    response_body="error: target branch is currently checked out in Ground",
                )
        return {"status": "ok", "commit": "a1b2c3d4e5f60718", "target": target}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    (tmp_path / ".zshrc").touch()
    return tmp_path


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.floors.dialogs", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def window_and_backend(app):
    backend = StubFloorsBackend()
    win = OpenMaestriWindow(app, backend)
    win.present()
    assert win.wait_loaded(), "initial workspace load did not complete"
    assert wait_until(lambda: len(win.floors.floors) == 2), "floors list was not populated"
    pump(0.2)
    yield win, backend
    win.close()
    pump(0.05)


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestFloorsDialogsE2E:
    def test_floor_button_overlay_and_popover_visual(self, window_and_backend):
        win, backend = window_and_backend
        btn = win.floor_button
        assert isinstance(btn, FloorButton)
        assert btn.get_parent() is win.canvas_stack
        assert btn.get_sensitive() is True
        assert btn.get_label() == "▤ Ground · 2 floors"
        assert win.document.payload.floors == backend.floors

        rows = []
        child = btn.list_box.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        assert len(rows) == 3  # Ground + 2 floors
        assert getattr(rows[0], "floor_id", None) is None
        assert getattr(rows[1], "floor_id", None) == backend.floors[0]["id"]
        assert getattr(rows[2], "floor_id", None) == backend.floors[1]["id"]

        save_dual_screenshot(win, "floor_01_button_overlay.png", "08_floors_button_overlay.png")

    def test_new_floor_dialog_validation_autofill_modes_and_error_recovery(self, window_and_backend):
        win, backend = window_and_backend
        dlg = win.floor_button.open_new()
        try:
            assert isinstance(dlg, NewFloorDialog)
            pump(0.2)
            assert not dlg.create_btn.get_sensitive()
            assert not dlg.error_label.get_visible()

            # 1. Typing name auto-fills slugified branch and enables Create.
            dlg.name_entry.set_text("OAuth2 Callback!")
            assert dlg.branch_entry.get_text() == "floor/oauth2-callback"
            assert dlg.create_btn.get_sensitive() is True

            save_dual_screenshot(dlg, "floor_02_new_floor_dialog.png", "09_new_floor_dialog.png")

            # 2. Invalid git branch characters disable Create and show error label.
            for invalid_ref in ("bad space", "feat..bar", "-leading-dash", "branch.lock", ".hidden/part"):
                dlg.branch_entry.set_text(invalid_ref)
                assert not dlg.create_btn.get_sensitive(), f"expected {invalid_ref!r} to be rejected"
                assert dlg.error_label.get_visible()

            # 3. Valid manual branch + invalid base branch disables Create.
            dlg.base_entry.set_text("bad..base")
            dlg.branch_entry.set_text("floor/custom-oauth")
            assert not dlg.create_btn.get_sensitive()
            assert "Base branch:" in dlg.error_label.get_label()
            dlg.base_entry.set_text("main")
            dlg._validate()
            assert dlg.create_btn.get_sensitive()

            # 4. Switching to "Existing branch" hides base_entry; invalid empty branch disables Create.
            dlg.mode.set_selected(1)
            assert dlg.existing is True
            assert not dlg.base_entry.get_visible() and not dlg.base_label.get_visible()
            dlg.branch_entry.set_text("")
            assert not dlg.create_btn.get_sensitive()

            # Switch back to "New branch"
            dlg.mode.set_selected(0)
            assert dlg.existing is False
            assert dlg.base_entry.get_visible()

            # 5. Server 409 error keeps dialog open, shows error message, and re-enables Create.
            dlg.branch_entry.set_text("floor/duplicate")
            dlg.submit()
            assert wait_until(
                lambda: dlg.error_label.get_visible() and "already exists" in dlg.error_label.get_label()
            )
            assert dlg.create_btn.get_sensitive() is True

            # 6. Retry with valid branch succeeds, closes dialog, and updates floor list.
            dlg.branch_entry.set_text("floor/oauth2-ready")
            dlg.submit()
            assert wait_until(lambda: len(win.floors.floors) == 3)
            assert ("create_floor", "OAuth2 Callback!", "floor/oauth2-ready", "main", False) in backend.calls
            assert win.floor_button.get_label() == "▤ Ground · 3 floors"
        finally:
            if dlg.get_visible():
                dlg.close()
                pump(0.05)

    def test_land_floor_dialog_states_dirty_same_branch_conflicts_and_fast_forward(self, window_and_backend):
        win, backend = window_and_backend
        floor = win.floors.floors[0]
        dlg = win.floor_button.open_land(floor)
        try:
            assert isinstance(dlg, LandFloorDialog)
            assert wait_until(lambda: dlg.preview_data is not None)
            pump(0.15)

            # 1. Initial target ("main"): clean fast-forward, Merge enabled.
            assert dlg.selected_target == "main"
            assert dlg.merge_btn.get_sensitive() is True
            assert "3 commits → main (fast-forward)" in dlg.summary.get_label()
            assert not dlg.conflicts.get_visible()
            save_dual_screenshot(dlg, "floor_03_land_dialog_clean.png", "10_land_floor_dialog.png")

            # 2. Target with merge conflicts ("release"): Merge disabled, red conflicts list visible.
            dlg.target.set_selected(dlg._branches.index("release"))
            assert wait_until(lambda: dlg.preview_data.get("target") == "release")
            pump(0.15)
            assert not dlg.merge_btn.get_sensitive()
            assert dlg.conflicts.get_visible() is True
            assert "✗ src/auth.py" in dlg.conflicts.get_label()
            assert "✗ src/config.py" in dlg.conflicts.get_label()
            save_dual_screenshot(dlg, "floor_04_land_dialog_conflicts.png", "10b_land_floor_conflicts.png")

            # 3. Target with uncommitted changes ("dirty-target"): Merge disabled, dirty warning shown.
            dlg.target.set_selected(dlg._branches.index("dirty-target"))
            assert wait_until(lambda: dlg.preview_data.get("target") == "dirty-target")
            assert not dlg.merge_btn.get_sensitive()
            assert "Uncommitted changes in the floor: src/auth.py, tests/test_auth.py" in dlg.summary.get_label()

            # 4. Target equal to floor's own branch ("floor/auth-refactor"): Merge disabled.
            dlg.target.set_selected(dlg._branches.index("floor/auth-refactor"))
            assert wait_until(lambda: dlg.preview_data.get("target") == "floor/auth-refactor")
            assert not dlg.merge_btn.get_sensitive()
            assert "is the floor's own branch" in dlg.summary.get_label()

            # 5. Preview failure ("broken-target"): shows error, Merge disabled.
            dlg.target.set_selected(dlg._branches.index("broken-target"))
            assert wait_until(lambda: dlg.error_label.get_visible() and "merge-tree failed" in dlg.error_label.get_label())
            assert not dlg.merge_btn.get_sensitive()

            # 6. Switch back to "main"; simulate a 409 land error first, then land cleanly.
            dlg.target.set_selected(dlg._branches.index("main"))
            assert wait_until(lambda: dlg.preview_data.get("target") == "main" and dlg.merge_btn.get_sensitive())
            backend.fail_next_land = True
            dlg.submit()
            assert wait_until(lambda: ("land_floor", floor["id"], "main") in backend.calls)
            assert wait_until(lambda: dlg.merge_btn.get_sensitive())  # re-previewed after land error

            dlg.submit()
            assert wait_until(lambda: "Landed" in win.floor_button.status.get_label())
            assert "a1b2c3d4" in win.floor_button.status.get_label()
        finally:
            if dlg.get_visible():
                dlg.close()
                pump(0.05)

    def test_delete_floor_dialog_three_buttons_cancel_keep_and_delete_branch(self, window_and_backend):
        win, backend = window_and_backend
        f1, f2 = win.floors.floors[0], win.floors.floors[1]

        dlg1 = win.floor_button.open_delete(f1)
        assert isinstance(dlg1, DeleteFloorDialog)
        assert list(dlg1.alert.get_buttons()) == ["Cancel", "Keep Branch", "Delete Branch"]
        assert dlg1.alert.get_cancel_button() == DeleteFloorDialog.CANCEL
        assert dlg1.alert.get_default_button() == DeleteFloorDialog.KEEP_BRANCH

        # 1. Cancel does nothing.
        dlg1.choose(DeleteFloorDialog.CANCEL)
        pump(0.1)
        assert not any(c[0] == "delete_floor" for c in backend.calls)
        assert len(win.floors.floors) == 2

        # 2. Keep Branch deletes floor with delete_branch=False.
        dlg1.choose(DeleteFloorDialog.KEEP_BRANCH)
        assert wait_until(lambda: len(win.floors.floors) == 1)
        assert ("delete_floor", f1["id"], False) in backend.calls
        assert "deleted" in win.floor_button.status.get_label()
        assert "branch" not in win.floor_button.status.get_label()

        # 3. Delete Branch deletes floor with delete_branch=True.
        dlg2 = win.floor_button.open_delete(f2)
        dlg2.choose(DeleteFloorDialog.DELETE_BRANCH)
        assert wait_until(lambda: win.floors.floors == [])
        assert ("delete_floor", f2["id"], True) in backend.calls
        assert "and its branch deleted" in win.floor_button.status.get_label()
        assert win.floor_button.get_label() == "▤ Ground"
        assert win.document.payload.floors == []
