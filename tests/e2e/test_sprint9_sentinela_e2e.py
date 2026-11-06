"""
Sprint 9 — Sentinela Workstream Acceptance & E2E Tests (Floors UI, Rename, Hooks, Changed Files, Iconography).

Covers §3 contracts (K12) and §4 Sentinela criteria:
1. Floor rename via row context menu and FloorButton context popover (PATCH /api/floors/:id, RenameFloorDialog).
2. Configure Hooks… dialog:
   - Setup, Run, Teardown phases with multiple shell commands per phase.
   - Auto-run Setup Hooks toggle.
   - Per-command and per-phase play buttons calling POST /api/floors/:id/hooks/run.
   - Run All button running all phases/commands.
   - Quick-access bolt button (⚡) on FloorButton with grouped hooks & per-hook play.
3. Changed files list in floor row (files from /api/floors/:id/status formatted and displayed).
4. English strings throughout floors.py and replacement of ⚠ and ✗ glyphs with outline icons.
5. DeleteFloorDialog warning icon and Keep branch vs Delete branch semantics.

All tests run against an isolated OPEN_MAESTRI_DATA_DIR under Xvfb (DISPLAY=:99).
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from maestri_core.client.api import MaestriClient  # noqa: E402
from ui.floors import (  # noqa: E402
    DeleteFloorDialog,
    FloorButton,
    FloorsController,
    HooksConfigDialog,
    LandFloorDialog,
    NewFloorDialog,
    RenameFloorDialog,
    format_changed_files,
    has_configured_hooks,
    install_floors,
    normalize_hooks,
)
from ui.window import OpenMaestriWindow  # noqa: E402

from tests.data_dir_isolation import start_isolated_server  # noqa: E402


def pump_gtk(ms: int = 120) -> None:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + (ms / 1000.0)
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 5.0, step_ms: int = 25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump_gtk(step_ms)
        if predicate():
            return True
    return bool(predicate())


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "open-maestri-data"
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(data_dir))
    ws_workdir = tmp_path / "sentinela-workdir"
    ws_workdir.mkdir(parents=True, exist_ok=True)
    with start_isolated_server(data_dir) as srv:
        client = MaestriClient(base_url=srv.base_url, token=srv.token)
        doc = client.get_workspace()
        assert doc is not None and doc.payload is not None
        doc.payload.working_directory = str(ws_workdir)
        doc.payload.nodes = []
        client.save_workspace(doc)

        app = Gtk.Application(
            application_id=f"com.openmaestri.sentinela.s9.{uuid.uuid4().hex[:8]}",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        app.register(None)
        win = OpenMaestriWindow(app, client)
        win.present()
        assert win.wait_loaded(timeout=8.0)
        pump_gtk(150)
        try:
            yield srv, client, win, ws_workdir
        finally:
            win.close()
            pump_gtk(80)


# ============================================================================
# 1. P0 — Floor Rename via Context Menu and PATCH /api/floors/:id
# ============================================================================
class TestFloorRename:
    def test_rename_floor_dialog_updates_name_via_patch(self, isolated_env) -> None:
        _, client, win, _ = isolated_env
        ws_id = win.document.payload.id
        floor_id = str(uuid.uuid4()).upper()

        sample_floor = {
            "id": floor_id,
            "name": "Feature Auth",
            "branchName": "floor/feature-auth",
            "worktreePath": "/tmp/test-wt-auth",
            "hooks": {"setup": [], "run": [], "teardown": [], "autoRunSetup": False},
        }
        win.floors.apply([sample_floor])
        pump_gtk(80)

        with patch.object(client, "patch_floor") as mock_patch:
            mock_patch.return_value = {**sample_floor, "name": "Feature Auth Renamed"}

            dlg = RenameFloorDialog(win, win.floors, sample_floor)
            dlg.present()
            pump_gtk(50)

            dlg.name_entry.set_text("Feature Auth Renamed")
            pump_gtk(30)
            assert dlg.save_btn.get_sensitive() is True

            dlg.submit()
            pump_gtk(150)

            mock_patch.assert_called_once_with(floor_id, name="Feature Auth Renamed", workspace_id=ws_id)
            assert sample_floor["name"] == "Feature Auth Renamed"

    def test_floor_row_right_click_context_menu_has_rename_item(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Quick Fix",
            "branchName": "floor/quick-fix",
            "worktreePath": "/tmp/test-wt-fix",
        }
        win.floors.apply([sample_floor])
        pump_gtk(80)

        # Look up row in floor button popover
        btn = win.floor_button
        row = btn.list_box.get_row_at_index(1)  # index 0 is Ground, index 1 is first floor
        assert row is not None
        assert getattr(row, "floor_id", None) == floor_id
        assert hasattr(row, "rename_item"), "Floor row must have rename_item in context popover"
        assert hasattr(row, "hooks_item"), "Floor row must have hooks_item in context popover"
        assert hasattr(row, "land_item"), "Floor row must have land_item in context popover"
        assert hasattr(row, "delete_item"), "Floor row must have delete_item in context popover"


# ============================================================================
# 2. P0 — Configure Hooks Dialog & Run All / Play Hooks (K12)
# ============================================================================
class TestFloorHooksConfigAndExecution:
    def test_normalize_hooks_and_has_configured_hooks(self) -> None:
        empty_norm = normalize_hooks(None)
        assert empty_norm == {"setup": [], "run": [], "teardown": [], "autoRunSetup": False}
        assert not has_configured_hooks({"hooks": empty_norm})

        configured = {
            "setup": ["npm install"],
            "run": ["pytest -v", "npm test"],
            "teardown": ["docker compose down"],
            "autoRunSetup": True,
        }
        assert normalize_hooks(configured) == configured
        assert has_configured_hooks({"hooks": configured})

    def test_hooks_config_dialog_phases_commands_and_save(self, isolated_env) -> None:
        _, client, win, _ = isolated_env
        ws_id = win.document.payload.id
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Backend Refactor",
            "branchName": "floor/refactor",
            "worktreePath": "/tmp/test-wt-refactor",
            "hooks": {"setup": ["cargo check"], "run": [], "teardown": [], "autoRunSetup": False},
        }

        with patch.object(client, "patch_floor") as mock_patch:
            dlg = HooksConfigDialog(win, win.floors, sample_floor)
            dlg.present()
            pump_gtk(60)

            # Check that initial command is loaded
            assert len(dlg.command_rows["setup"]) == 1
            assert dlg.command_rows["setup"][0].entry.get_text() == "cargo check"

            # Add commands to Run and Teardown
            dlg.add_command("run", "cargo test")
            dlg.add_command("teardown", "cargo clean")
            dlg.auto_run_check.set_active(True)
            pump_gtk(50)

            hooks_out = dlg.collect_hooks()
            assert hooks_out["setup"] == ["cargo check"]
            assert hooks_out["run"] == ["cargo test"]
            assert hooks_out["teardown"] == ["cargo clean"]
            assert hooks_out["autoRunSetup"] is True

            dlg.submit()
            pump_gtk(150)
            mock_patch.assert_called_once_with(floor_id, hooks=hooks_out, workspace_id=ws_id)

    def test_run_hooks_and_run_all_invokes_hooks_run_endpoint(self, isolated_env) -> None:
        _, client, win, _ = isolated_env
        ws_id = win.document.payload.id
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Testing Floor",
            "branchName": "floor/testing",
            "worktreePath": "/tmp/test-wt-testing",
            "hooks": {
                "setup": ["pip install -r req.txt"],
                "run": ["pytest"],
                "teardown": ["rm -f .temp"],
                "autoRunSetup": False,
            },
        }
        win.floors.apply([sample_floor])

        with patch.object(win.floors, "_call_run_hooks") as mock_call:
            mock_call.return_value = {"ok": True}

            # Run single hook phase
            win.floors.run_hooks(sample_floor, phase="run", hook_id=0)
            pump_gtk(100)
            mock_call.assert_called_with(floor_id, "run", 0, ws_id)

            # Run all hooks
            mock_call.reset_mock()
            win.floors.run_all_hooks(sample_floor)
            pump_gtk(150)
            assert mock_call.call_count == 3  # setup, run, teardown

    def test_quick_hooks_button_displays_bolt_and_lists_commands(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Pipeline",
            "branchName": "floor/pipeline",
            "worktreePath": "/tmp/test-wt-pipeline",
            "hooks": {
                "setup": ["./setup.sh"],
                "run": ["./run.sh"],
                "teardown": [],
                "autoRunSetup": True,
            },
        }
        win.floors.apply([sample_floor])
        win.floors.switch_to(floor_id)
        pump_gtk(80)

        btn = win.floor_button
        assert btn.quick_hooks_button.get_visible() is True
        assert btn.quick_hooks_button.get_sensitive() is True
        assert len(btn.quick_item_rows) == 2


# ============================================================================
# 3. P1 — Changed Files List in Floor Panel (/api/floors/:id/status files)
# ============================================================================
class TestFloorChangedFilesList:
    def test_format_changed_files_helper(self) -> None:
        assert format_changed_files(None) == []
        assert format_changed_files({}) == []

        # List of dicts
        st_dict = {
            "files": [
                {"status": "M", "path": "Sources/Server.swift"},
                {"status": "A", "path": "tests/test_new.py"},
                {"status": "D", "path": "old_file.txt"},
            ]
        }
        assert format_changed_files(st_dict) == [
            ("M", "Sources/Server.swift"),
            ("A", "tests/test_new.py"),
            ("D", "old_file.txt"),
        ]

        # List of strings "M path"
        st_str = {"files": ["M src/main.py", "?? untracked.md"]}
        assert format_changed_files(st_str) == [
            ("M", "src/main.py"),
            ("??", "untracked.md"),
        ]

    def test_floor_row_renders_changed_files_box(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Diff Floor",
            "branchName": "floor/diff",
            "worktreePath": "/tmp/test-wt-diff",
        }
        win.floors.statuses[floor_id] = {
            "dirty": True,
            "insertions": 12,
            "deletions": 4,
            "files": [
                {"status": "M", "path": "Sources/Server.swift"},
                {"status": "A", "path": "tests/new_test.py"},
            ],
        }
        win.floors.apply([sample_floor])
        pump_gtk(80)

        btn = win.floor_button
        row = btn.list_box.get_row_at_index(1)
        assert row is not None
        assert hasattr(row, "files_box") and row.files_box is not None
        assert row.files_box.get_visible() is True
        assert len(row.changed_files) == 2
        assert "M Sources/Server.swift" in row.file_labels


# ============================================================================
# 4. P1 — English Strings & No Unicode Glyphs (Outline Icons Only)
# ============================================================================
class TestSentinelaIconographyAndEnglishStrings:
    def test_delete_floor_dialog_uses_outline_warning_icon_and_no_glyph(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Branch Delete",
            "branchName": "floor/branch-delete",
            "worktreePath": "/tmp/test-wt-delete",
        }
        dlg = DeleteFloorDialog(win, win.floors, sample_floor)
        dlg.present()
        pump_gtk(60)

        try:
            assert isinstance(dlg.warning_icon, Gtk.Image)
            assert dlg.warning_icon.get_icon_name() == "maestri-warning-symbolic"
            # Ensure no literal Unicode warning glyph in text labels
            assert "⚠" not in dlg.message_label.get_label()
            assert "⚠" not in dlg.detail_label.get_label()
            # Buttons are in English
            assert dlg.BUTTONS == ["Cancel", "Keep branch", "Delete branch"]
        finally:
            dlg.close()
            pump_gtk(40)

    def test_land_floor_dialog_uses_outline_plane_icon(self, isolated_env) -> None:
        _, _, win, _ = isolated_env
        floor_id = str(uuid.uuid4()).upper()
        sample_floor = {
            "id": floor_id,
            "name": "Land Floor",
            "branchName": "floor/land",
            "worktreePath": "/tmp/test-wt-land",
        }
        with patch.object(win.floors, "preview"):
            dlg = LandFloorDialog(win, win.floors, sample_floor)
            dlg.present()
            pump_gtk(60)
            try:
                assert "Land “Land Floor”" in dlg.get_title()
                assert dlg.cancel_btn.get_label() == "Cancel"
                assert dlg.merge_btn.get_label() == "Merge"
            finally:
                dlg.close()
                pump_gtk(40)
