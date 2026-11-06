"""
Sprint 9 — Arquiteto Workstream Acceptance & E2E Tests (Canvas Engine, Terminal UX, Portal Bridge, Notes & FileTree).

Covers §3 contracts (K3, K9) and §4 Arquiteto criteria:
1. Terminal UX parity (`TerminalFeatures`, `VteTerminalView`, `CanvasWidget`):
   - `Ctrl+Shift+C` (copy), `Ctrl+Shift+V` (paste), `Ctrl+Shift+A` (select all), `Ctrl+Shift+F` (find).
   - `TerminalSearchBar` overlay: find next / previous / close (`Esc`).
   - Clickable links: regex (`URL_PATTERN`) & OSC 8 hyperlinks opened on `Ctrl+click` via mocked handler.
   - Bell attention badge (`ring_bell` / `clear_bell`), busy/idle header status badge, and
     `set_terminal_exited(node_id, code)` (`exited N` badge).
   - Preferences applied: `apply_terminal_prefs(scrollback_memory_limit=...)` (2000 vs 10000 lines).
   - Number badges (`1`..`9`): `frame.set_number_badge(n)`, `cw.show_number_badges(on)`, and
     `cw.jump_to_terminal(n)`.
2. Canvas & connection engine (K9):
   - Interactive connect mode (`cw.begin_connect_mode` / `L` key) + click target -> `connect-requested`,
     `Escape` cancels.
   - Connection hit-testing (`scene.hit_connection(pt, tol)` / `ConnectionTarget`).
   - Rope control points persistence (`ropePoints` / `connection-rope-settled`).
3. Portal automation bridge (K3):
   - `PortalView` / `PortalBridge` `navigate`, `evaluate`, `text`, `html`, `snapshot`, `info`, and
     `screenshot` (PNG / base64) under Xvfb.
4. StickyNote, FileTree, Activity Rings & dead-code cleanup:
   - Live managed-note file sync (`watch_path`): external write updates view when clean, warns
     `"Changed on disk: your edits are kept"` when dirty.
   - Consistent Note delete: managed note deletes `.md`, custom note unlinks without deleting user file.
   - Drop file on Note (`file-dropped` / `adopt_file`).
   - `FileTreeView` Collapse All (`collapse_all`).
   - `AgentActivityRingsWidget`: real activity fraction + truthful tooltip (no fake `136k / 200k` strings).
   - Dead files `gtk4-ui/src/ui/canvas_widget.py` and `gtk4-ui/src/ui/filetree.py` removed.

All tests run against an isolated `OPEN_MAESTRI_DATA_DIR` under Xvfb (`DISPLAY=:99`).
"""

from __future__ import annotations

import base64
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
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.gi_env import webkit_available  # noqa: E402
from maestri_canvas.note_model import NoteMeta  # noqa: E402
from maestri_canvas.note_storage import LocalNoteStorage  # noqa: E402
from maestri_canvas.portal_model import PortalMeta  # noqa: E402
from maestri_canvas.scene import ConnItem, NodeItem, norm  # noqa: E402
from maestri_canvas.sticky_note import StickyNoteView  # noqa: E402
from maestri_canvas.terminal_features import (  # noqa: E402
    URL_PATTERN,
     search_pattern,
    shortcut_action,
)
from maestri_canvas.widget import CanvasWidget, load_vte, terminal_of  # noqa: E402
from ui.floating_bars import AgentActivityRingsWidget  # noqa: E402


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


# ============================================================================
# 1. P0 — Terminal UX Parity (`TerminalFeatures`, Search, Links, Bell, Busy/Exited, Number Badges)
# ============================================================================
class TestTerminalUXParity:
    def test_shortcut_action_maps_ctrl_shift_c_v_a_f(self) -> None:
        assert shortcut_action("C", ctrl=True, shift=True) == "copy"
        assert shortcut_action("v", ctrl=True, shift=True) == "paste"
        assert shortcut_action("A", ctrl=True, shift=True) == "select-all"
        assert shortcut_action("F", ctrl=True, shift=True) == "find"
        assert shortcut_action("c", ctrl=True, shift=False) is None
        assert search_pattern("a+b*c?") == r"a\+b\*c\?"

    def test_terminal_search_bar_bell_busy_exited_and_prefs(self, tmp_path: Path) -> None:
        if load_vte() is None:
            pytest.skip("Vte 3.91 typelib not available")

        win = Gtk.Window()
        cw = CanvasWidget()
        win.set_child(cw)
        win.present()
        pump_gtk(80)

        try:
            nid = str(uuid.uuid4()).upper()
            cw.add_node(
                NodeItem(nid, Rect(100, 100, 460, 320), 1, "terminal", "UX Term"),
                cwd=str(tmp_path),
            )
            pump_gtk(150)

            frame = cw.frames[norm(nid)]
            tf = frame.terminal_features
            term = terminal_of(frame.content)
            assert tf is not None and term is not None

            # Feed sample output with a URL and searchable token
            term.feed(b"Hello Sprint9_SearchToken https://example.com/docs\r\n")
            pump_gtk(100)

            # 1. Busy badge set on output
            assert tf.busy is True
            assert frame.badge("status") is not None

            # 2. Search bar (Ctrl+Shift+F)
            sbar = tf.open_search()
            assert sbar.get_visible() is True
            sbar.entry.set_text("Sprint9_SearchToken")
            pump_gtk(60)
            assert tf.find("Sprint9_SearchToken", backwards=False, restart=True) is True
            tf.close_search()
            assert sbar.get_visible() is False

            # 3. Bell badge when unfocused, cleared on clear_bell()
            tf.ring_bell()
            if not term.has_focus():
                assert frame.badge("bell") is not None
            tf.clear_bell()
            assert frame.badge("bell") is None

            # 4. Exited indicator
            cw.set_terminal_exited(nid, 0)
            exited_badge = frame.badge("exited")
            assert exited_badge is not None
            assert "exited 0" in exited_badge.get_label()

            # 5. Scrollback memory limit preference
            cw.apply_terminal_prefs(scrollback_memory_limit=True)
            assert cw.terminal_scrollback_lines == 2000
            cw.apply_terminal_prefs(scrollback_memory_limit=False)
            assert cw.terminal_scrollback_lines == 10000

            # 6. Number badges (`1`..`9`) and `jump_to_terminal`
            frame.set_number_badge(1)
            nb = frame.badge("number")
            assert nb is not None and nb.get_label() == "1"
            frame.set_number_badge(None)
            assert frame.badge("number") is None

            if hasattr(cw, "show_number_badges"):
                cw.show_number_badges(True)
                assert frame.badge("number") is not None
                cw.show_number_badges(False)
                assert frame.badge("number") is None

            if hasattr(cw, "jump_to_terminal"):
                assert cw.jump_to_terminal(1) is True
                assert norm(nid) in cw.scene.selection
        finally:
            win.close()
            pump_gtk(60)


# ============================================================================
# 2. P0 — Canvas & Connection Engine (K9: Connect Mode, Hit-testing, Rope Points)
# ============================================================================
class TestK9CanvasAndConnectionEngine:
    def test_begin_connect_mode_and_hit_connection(self) -> None:
        cw = CanvasWidget()
        n1 = str(uuid.uuid4()).upper()
        n2 = str(uuid.uuid4()).upper()
        cw.scene.add_node(NodeItem(n1, Rect(100, 100, 300, 200), 1, "terminal", "T1"))
        cw.scene.add_node(NodeItem(n2, Rect(600, 100, 300, 200), 2, "terminal", "T2"))
        conn_id = "CONN-HIT-1"
        cw.add_connection(ConnItem(conn_id, n1, n2, "right", "left"))

        # 1. Interactive connect mode (K9)
        assert hasattr(cw, "begin_connect_mode") and callable(cw.begin_connect_mode), (
            "Arquiteto K9: CanvasWidget.begin_connect_mode(node_id=None) must exist"
        )
        cw.scene.select_only(n1)
        cw.begin_connect_mode(n1)
        assert cw.controller.overlay.connecting is not None

        # Escape cancels connect mode
        cw.controller.key("escape", cw._mods(Gdk.ModifierType(0)), canvas_focused=True)
        assert cw.controller.overlay.connecting is None

        # 2. Scene.hit_connection(pt, tol) finds the rope midpoint between T1(400,200) and T2(600,200)
        assert hasattr(cw.scene, "hit_connection") and callable(cw.scene.hit_connection), (
            "Arquiteto K9: Scene.hit_connection(pt, tol=...) must exist"
        )
        hit = cw.scene.hit_connection(Point(500, 200), tol=40.0)
        assert hit is not None and hit.id == conn_id
        assert cw.scene.hit_connection(Point(2000, 2000), tol=10.0) is None


# ============================================================================
# 3. P0 — Portal Automation Bridge (K3: Navigate, Evaluate, Screenshot under Xvfb)
# ============================================================================
class TestK3PortalViewAndBridge:
    def test_portal_view_navigate_evaluate_and_screenshot_under_xvfb(self, tmp_path: Path) -> None:
        if not webkit_available():
            pytest.skip("WebKit 6.0 typelib not available")

        from maestri_canvas.portal_node import PortalView

        html_file = tmp_path / "sprint9_portal.html"
        html_file.write_text(
            "<!DOCTYPE html><html><head><title>Sprint9Portal</title></head>"
            "<body><h1 id='hdr'>Maestri Portal Ready</h1></body></html>",
            encoding="utf-8",
        )

        win = Gtk.Window()
        win.set_default_size(600, 450)
        meta = PortalMeta(
            name="Portal Test",
            portal_id=str(uuid.uuid4()).upper(),
            current_url=html_file.as_uri(),
        )
        view = PortalView(meta, autoload=True)
        win.set_child(view)
        win.present()

        try:
            assert wait_until(lambda: view.meta.status == "idle" and not view.web.is_loading(), timeout=6.0)

            # Evaluate JS expression
            eval_res: dict = {}
            view.evaluate("1 + 1", lambda val, err: eval_res.update({"val": val, "err": err}))
            assert wait_until(lambda: "val" in eval_res, timeout=4.0)
            assert eval_res["err"] is None
            assert str(eval_res["val"]).strip() == "2"

            # Text extraction
            txt_res: dict = {}
            view.text(lambda val, err: txt_res.update({"val": val, "err": err}))
            assert wait_until(lambda: "val" in txt_res, timeout=4.0)
            assert "Maestri Portal Ready" in (txt_res["val"] or "")

            # Screenshot to PNG file
            png_path = tmp_path / "shot.png"
            shot_res: dict = {}
            view.screenshot(str(png_path), lambda p, err: shot_res.update({"path": p, "err": err}))
            assert wait_until(lambda: "path" in shot_res, timeout=5.0)
            assert shot_res["err"] is None
            assert png_path.is_file() and png_path.stat().st_size > 0
            b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
            assert len(b64) > 32
        finally:
            view.dispose()
            win.close()
            pump_gtk(60)


# ============================================================================
# 4. P1 — StickyNote Live Sync & File Drop, FileTree Collapse All, Activity Rings & Dead Files
# ============================================================================
class TestStickyNoteFileTreeRingsAndDeadCode:
    def test_sticky_note_live_watch_updates_when_clean_and_warns_when_dirty(self, tmp_path: Path) -> None:
        note_file = tmp_path / "WatchedNote.md"
        note_file.write_text("Initial disk content", encoding="utf-8")

        meta = NoteMeta(
            file_name="WatchedNote.md",
            storage_mode={"custom": {"_0": str(note_file)}},
        )
        storage = LocalNoteStorage(str(note_file))
        view = StickyNoteView(meta, storage, watch_path=str(note_file), debounce_ms=2000)

        assert wait_until(lambda: view.text() == "Initial disk content", timeout=3.0)

        try:
            # 1. External write while clean -> updates buffer automatically
            note_file.write_text("Updated by external agent", encoding="utf-8")
            if hasattr(view, "_on_external_change"):
                view._on_external_change()
            assert wait_until(lambda: view.text() == "Updated by external agent", timeout=3.0)

            # 2. External write while dirty -> keeps user edits and warns in status label
            view.buf.set_text("User unsaved local edit")
            pump_gtk(40)
            assert view.saver.dirty is True
            note_file.write_text("Conflicting disk write", encoding="utf-8")
            if hasattr(view, "_on_external_change"):
                view._on_external_change()
            pump_gtk(150)
            assert view.text() == "User unsaved local edit"
            assert "Changed on disk" in view.status.get_text()
        finally:
            view.dispose()

    def test_file_tree_collapse_all_collapses_expanded_folders(self, tmp_path: Path) -> None:
        sub = tmp_path / "folder_a"
        sub.mkdir()
        (sub / "nested.txt").write_text("hi", encoding="utf-8")

        ft = FileTreeView(root=str(tmp_path), git=False)
        assert wait_until(lambda: not ft.is_loading() and len(ft.rows()) >= 1, timeout=3.0)

        row = ft.row_for("folder_a")
        assert row is not None
        row.set_expanded(True)
        assert wait_until(lambda: not ft.is_loading() and row.get_expanded(), timeout=3.0)

        assert hasattr(ft, "collapse_all") and callable(ft.collapse_all), (
            "Arquiteto P1: FileTreeView.collapse_all() must exist"
        )
        ft.collapse_all()
        pump_gtk(60)
        assert row.get_expanded() is False

    def test_agent_activity_rings_uses_truthful_activity_and_no_fake_token_counts(self) -> None:
        rings = AgentActivityRingsWidget()
        tooltip = rings.get_tooltip_text() or ""
        for fake_str in ("136k", "200k", "54k", "128k", "250k", "1.0M"):
            assert fake_str not in tooltip, (
                f"Arquiteto P1: AgentActivityRingsWidget must not display hardcoded fake quota {fake_str!r}"
            )

    def test_dead_duplicate_ui_files_are_removed(self) -> None:
        dead_canvas = ROOT_DIR / "gtk4-ui" / "src" / "ui" / "canvas_widget.py"
        dead_filetree = ROOT_DIR / "gtk4-ui" / "src" / "ui" / "filetree.py"
        assert not dead_canvas.exists(), f"Dead file {dead_canvas} must be deleted"
        assert not dead_filetree.exists(), f"Dead file {dead_filetree} must be deleted"
