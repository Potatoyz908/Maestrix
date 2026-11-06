"""Floors UI (Sprint 5/6 T2.3-T2.6 + R6) under Xvfb: the floating button on the canvas, NewFloorDialog, LandFloorDialog and
DeleteFloorDialog against a fake core client (no network). Run with DISPLAY=:99 (Xvfb), never on the user's display."""
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires a display (Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas.demo import screenshot  # noqa: E402
from maestri_core.client.api import MaestriAPIError, MaestriClient  # noqa: E402
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload  # noqa: E402
from ui.floors import DeleteFloorDialog, FloorButton, LandFloorDialog, NewFloorDialog  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402

FLOOR_ID = "AAAAAAAA-0000-0000-0000-000000000001"


def pump_until(cond, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(0.005)
    return cond()


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


class FakeFloorsCore(MaestriClient):
    """Connected core with an in-memory floor list; land-preview answers depend on the target."""

    def __init__(self, root):
        super().__init__(port=1, auth_token="t")
        self.doc = WorkspaceDocument(payload=WorkspacePayload(name="Repo", working_directory=str(root)))
        self.floors = [{"id": FLOOR_ID, "name": "Fix login", "branchName": "floor/fix-login",
                        "worktreePath": f"{root}/.open-maestri/floors/fix-login", "hooks": {}}]
        self.calls = []
        self.fail_next_land = False
        self.lock = threading.Lock()

    def _log(self, *call):
        with self.lock:
            self.calls.append(call)

    def check_connection_status(self):
        return "connected"

    def get_workspace(self, workspace_id=None):
        return self.doc

    def save_workspace_dict(self, data, workspace_id=None):
        return True

    def connect_events(self, on_event=None, workspace_id=None, **_kw):
        self._log("events", workspace_id)
        return SimpleNamespace(close=lambda: None)

    def get_floors(self, workspace_id=None):
        self._log("get", workspace_id)
        return [dict(f) for f in self.floors]

    def floor_status(self, floor_id, workspace_id=None):
        return {"dirty": True, "ahead": 2, "insertions": 7, "deletions": 3, "files": [{"path": "a", "status": "M"}]}

    def create_floor(self, name, branch_name=None, workspace_id=None, working_directory=None, base_branch=None, existing_branch=False):
        self._log("create", name, branch_name, base_branch, existing_branch)
        if branch_name == "taken":
            raise MaestriAPIError("HTTP 400", status_code=400, response_body="error: failed to create floor - branch exists")
        with self.lock:
            idx = len(self.floors) + 1
            f = {"id": f"BBBBBBBB-0000-0000-0000-{idx:012d}", "name": name, "branchName": branch_name,
                 "worktreePath": f"/tmp/x/{name}", "hooks": {}}
            self.floors.append(f)
        return f

    def delete_floor(self, floor_id, workspace_id=None, remove_worktree=True, delete_branch=False):
        self._log("delete", floor_id, delete_branch)
        with self.lock:
            self.floors = [f for f in self.floors if f["id"] != floor_id]
        return True

    def land_preview(self, floor_id, target=None, workspace_id=None):
        self._log("preview", target)
        target = target or "main"
        branches = ["main", "release", "dirty", "merge-commit", "dev", "floor/fix-login"]
        if target == "dirty":
            return {"floorBranch": "floor/fix-login", "target": target, "branches": branches,
                    "dirty": True, "dirtyFiles": ["src/login.py"], "sameBranch": False,
                    "targetCheckedOut": False, "ahead": 2, "fastForward": True,
                    "stat": "1 file changed, 5 insertions(+)", "insertions": 5, "deletions": 0,
                    "conflicts": [], "canLand": False}
        if target == "merge-commit":
            return {"floorBranch": "floor/fix-login", "target": target, "branches": branches,
                    "dirty": False, "dirtyFiles": [], "sameBranch": False,
                    "targetCheckedOut": False, "ahead": 2, "fastForward": False,
                    "stat": "2 files changed, 10 insertions(+), 2 deletions(-)", "insertions": 10, "deletions": 2,
                    "conflicts": [], "canLand": True}
        conflicts = ["src/app.py", "src/auth.py"] if target == "release" else []
        return {"floorBranch": "floor/fix-login", "target": target, "branches": branches,
                "dirty": False, "dirtyFiles": [], "sameBranch": target == "floor/fix-login",
                "targetCheckedOut": target == "main", "ahead": 2, "fastForward": target == "dev",
                "stat": "2 files changed, 10 insertions(+)", "insertions": 10, "deletions": 0, "conflicts": conflicts,
                "canLand": target == "dev"}

    def land_floor(self, floor_id, target, workspace_id=None):
        self._log("land", floor_id, target)
        with self.lock:
            if self.fail_next_land:
                self.fail_next_land = False
                raise MaestriAPIError("HTTP 409", status_code=409, response_body="error: target branch updated concurrently")
        return {"status": "ok", "commit": "0123456789abcdef", "target": target}


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.floors", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    (tmp_path / ".zshrc").touch()
    repo = tmp_path / "repo"
    repo.mkdir()
    core = FakeFloorsCore(repo)
    w = OpenMaestriWindow(app, core)
    w.present()
    assert w.wait_loaded()
    assert pump_until(lambda: len(w.floors.floors) == 1 and FLOOR_ID in w.floors.statuses)
    yield w, core
    w.close()
    pump_until(lambda: False, 0.05)


def _rows(btn):
    out, child = [], btn.list_box.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestFloorsUI:
    def test_button_overlays_canvas_lists_floors_and_switches(self, win):
        w, core = win
        btn = w.floor_button
        assert isinstance(btn, FloorButton) and (btn.get_parent() is w.canvas_widget.get_parent() or btn.get_ancestor(Gtk.Overlay) is w.canvas_widget.get_parent())
        assert (btn.get_halign() == Gtk.Align.END and btn.get_valign() == Gtk.Align.END) or btn.get_parent() is getattr(w.bottom_controls, "pill_floor", None)
        assert btn.get_sensitive() and btn.get_label() == "Ground"
        assert not btn.land_btn.get_sensitive(), "Land only from a floor"
        rows = _rows(btn)
        assert [getattr(r, "floor_id", "?") for r in rows] == [None, FLOOR_ID]       # Ground on top
        assert rows[1].floor_badge.get_label() == "+7 −3" and rows[1].floor_dirty.get_visible()
        assert w.document.payload.floors == core.floors, "server list mirrored into the document (R1)"
        assert ("events", w.document.payload.id) in core.calls

        btn._row_activated(rows[1])
        assert btn.get_label() == "Fix login" and btn.land_btn.get_sensitive()
        w._create_node("terminal")
        term = w.document.payload.nodes[-1]
        assert term.content.working_directory == core.floors[0]["worktreePath"], "new terminals start in the floor"
        btn._row_activated(_rows(btn)[0])
        assert btn.get_label() == "Ground"
        save_dual_screenshot(w, "floor_01_button_overlay.png", "08_floors_button_overlay.png")

    def test_button_disabled_offline(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        w = OpenMaestriWindow(app, MaestriClient(port=0))
        try:
            assert w.wait_loaded()
            assert not w.floor_button.get_sensitive()
        finally:
            w.close()

    def test_new_floor_dialog_sensitivity_errors_and_create(self, win):
        w, core = win
        w.floors.list_branches = lambda cb: cb(["main", "dev"])   # instead of GitOps (the tmp dir is not a repo)
        dlg = w.floor_button.open_new()
        assert isinstance(dlg, NewFloorDialog) and not dlg.create_btn.get_sensitive()
        assert dlg.branches == ["main", "dev"]
        dlg.name_entry.set_text("Fix Signup Flow")
        assert dlg.branch_entry.get_text() == "floor/fix-signup-flow" and dlg.create_btn.get_sensitive()
        save_dual_screenshot(dlg, "floor_02_new_floor_dialog.png", "09_new_floor_dialog.png")
        dlg.branch_entry.set_text("bad branch")
        assert not dlg.create_btn.get_sensitive() and dlg.error_label.get_visible()
        dlg.branch_entry.set_text("taken")
        dlg.submit()
        assert pump_until(lambda: "branch exists" in dlg.error_label.get_label())
        assert dlg.get_visible() and dlg.create_btn.get_sensitive(), "a core error keeps the dialog open"
        dlg.branch_entry.set_text("floor/signup")
        dlg.base_dropdown.set_selected(2)   # "dev"
        dlg.submit()
        assert pump_until(lambda: len(w.floors.floors) == 2)
        assert ("create", "Fix Signup Flow", "floor/signup", "dev", False) in core.calls

    def test_new_floor_existing_branch_mode(self, win):
        w, core = win
        w.floors.list_branches = lambda cb: cb(["main", "feature/review"])
        dlg = w.floor_button.open_new()
        dlg.name_entry.set_text("Review")
        dlg.mode.set_selected(1)
        assert not dlg.branch_entry.get_visible() and dlg.existing_dropdown.get_visible()
        dlg.existing_dropdown.set_selected(1)
        assert dlg.create_btn.get_sensitive()
        dlg.submit()
        assert pump_until(lambda: any(c[0] == "create" for c in core.calls))
        assert ("create", "Review", "feature/review", None, True) in core.calls

    def test_land_dialog_preview_reasons_and_merge(self, win):
        w, core = win
        dlg = w.floor_button.open_land(w.floors.floors[0])
        assert isinstance(dlg, LandFloorDialog)
        assert pump_until(lambda: dlg.preview_data is not None)
        assert ("preview", "floor/fix-login") in core.calls, "default target = the floor's own branch"
        assert dlg.selected_target == "floor/fix-login" and not dlg.merge_btn.get_sensitive()
        assert "própria branch do floor" in dlg.summary.get_label()

        def pick(branch):
            dlg.target.set_selected(dlg._branches.index(branch))
            assert pump_until(lambda: dlg.preview_data.get("target") == branch)

        # 1. Merge conflicts -> Merge disabled, red conflict file list shown
        pick("release")
        assert not dlg.merge_btn.get_sensitive()
        assert dlg.reason.get_label() == "Resolva os conflitos no terminal/IDE"
        assert dlg.conflicts.get_visible()
        assert "✗ src/app.py" in dlg.conflicts.get_label() and "✗ src/auth.py" in dlg.conflicts.get_label()
        save_dual_screenshot(dlg, "floor_04_land_dialog_conflicts.png", "10b_land_floor_conflicts.png")

        # 2. Uncommitted dirty changes -> Merge disabled with commit warning
        pick("dirty")
        assert not dlg.merge_btn.get_sensitive()
        assert dlg.reason.get_label() == "Faça commit antes de pousar: src/login.py"

        # 3. Target checked out in Ground -> Merge disabled with checkout warning
        pick("main")
        assert not dlg.merge_btn.get_sensitive() and "checkout de outra branch" in dlg.reason.get_label()

        # 4. Clean non-fast-forward merge commit -> Merge enabled
        pick("merge-commit")
        assert dlg.merge_btn.get_sensitive() and not dlg.reason.get_visible()
        assert "2 commits → merge-commit (commit de merge)" in dlg.summary.get_label()
        assert "+10 −2" in dlg.summary.get_label()

        # 5. Clean fast-forward merge -> Merge enabled, error recovery (L2), then successful land
        pick("dev")
        assert dlg.merge_btn.get_sensitive() and not dlg.reason.get_visible()
        assert "2 commits → dev (fast-forward)" in dlg.summary.get_label()
        assert "+10 −0" in dlg.summary.get_label()
        save_dual_screenshot(dlg, "floor_03_land_dialog_clean.png", "10_land_floor_dialog.png")

        core.fail_next_land = True
        dlg.submit()
        assert pump_until(lambda: dlg.merge_btn.get_sensitive())
        assert dlg.reason.get_visible() and "target branch updated concurrently" in dlg.reason.get_label()

        dlg.submit()
        assert pump_until(lambda: ("land", FLOOR_ID, "dev") in core.calls)
        assert pump_until(lambda: w.floor_button.status.get_label() == "Floor pousado em dev")

    def test_delete_dialog_three_buttons_keep_vs_delete_branch_and_back_to_ground(self, win):
        w, core = win
        # Add a second floor so we can test both KEEP_BRANCH (delete_branch=False) and DELETE_BRANCH (delete_branch=True)
        second = core.create_floor("Second Floor", branch_name="floor/second")
        w.floors.refresh()
        assert pump_until(lambda: len(w.floors.floors) == 2)

        floor1 = w.floors.find(FLOOR_ID)
        floor2 = w.floors.find(second["id"])
        assert floor1 is not None and floor2 is not None

        # 1. Verify 3-button contract, warning detail (L3), and Cancel (0)
        dlg1 = w.floor_button.open_delete(floor1)
        assert isinstance(dlg1, DeleteFloorDialog)
        assert list(dlg1.alert.get_buttons()) == ["Cancelar", "Manter branch", "Apagar branch"]
        assert dlg1.alert.get_cancel_button() == DeleteFloorDialog.CANCEL
        assert dlg1.alert.get_default_button() == DeleteFloorDialog.KEEP_BRANCH
        detail = dlg1.alert.get_detail() or ""
        assert floor1["worktreePath"] in detail and floor1["branchName"] in detail
        assert "Manter branch" in detail

        dlg1.choose(DeleteFloorDialog.CANCEL)
        pump_until(lambda: False, 0.1)
        assert not any(c[0] == "delete" for c in core.calls)
        assert len(w.floors.floors) == 2

        # 2. Keep Branch (1 -> delete_branch=False): deletes worktree/floor but keeps git branch
        dlg1.choose(DeleteFloorDialog.KEEP_BRANCH)
        assert pump_until(lambda: len(w.floors.floors) == 1)
        assert ("delete", floor1["id"], False) in core.calls
        assert w.floor_button.status.get_label() == f"Floor “{floor1['name']}” apagado"
        assert [f["id"] for f in w.document.payload.floors] == [floor2["id"]]

        # 3. Delete Branch (2 -> delete_branch=True) on active floor: deletes branch and returns to Ground
        w.floors.switch_to(floor2["id"])
        assert w.floors.active_floor_id == floor2["id"] and w.floor_button.get_label() == floor2["name"]
        dlg2 = w.floor_button.open_delete(floor2)
        dlg2.choose(DeleteFloorDialog.DELETE_BRANCH)
        assert pump_until(lambda: w.floors.floors == [])
        assert ("delete", floor2["id"], True) in core.calls
        assert w.floor_button.status.get_label() == f"Floor “{floor2['name']}” e a branch apagado"
        assert w.floors.active_floor_id is None and w.floor_button.get_label() == "Ground"
        assert w.document.payload.floors == []
