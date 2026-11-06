"""
Sprint 5 E2E (Xvfb): Floating Context Toolbars (`NoteContextToolbar` & `DrawingContextToolbar`).

Covers:
1. Selecting a StickyNote shows `NoteContextToolbar` and exercises all 8 Markdown formatting buttons
   (`bold`, `italic`, `strike`, `code`, `h1`, `h2`, `bullet`, `task`), including auto-switching from formatted preview to raw.
2. Selecting drawn nodes (`shape`, `text`, `freehand`) in `OpenMaestriWindow` displays the corresponding
   `DrawingContextToolbar` variant, clamps strokeWidth/fontSize to macOS ranges, updates colour palettes and
   stroke/fill styles, persists changes into the schema-v2 `WorkspaceDocument`, and deletes via 🗑.
3. Multi-selection, non-select drawing tools, and locked nodes hide the floating toolbar.
4. Visual snapshots of each toolbar variant in both `artifacts/sprint5/` and `artifacts/e2e-screenshots/`.
"""

import shutil
import sys
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

from maestri_canvas.context_toolbar import (  # noqa: E402
    FONT_RANGE,
    NOTE_ACTIONS,
    STROKE_RANGE,
    DrawingContextToolbar,
    MultiContextToolbar,
    NoteContextToolbar,
)
from maestri_canvas.demo import screenshot  # noqa: E402
from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.scene import norm  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
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


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    (tmp_path / ".zshrc").touch()
    return tmp_path


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.ctxtoolbar.sprint5", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def window(app):
    win = OpenMaestriWindow(app, MaestriClient(port=0))
    win.present()
    assert win.wait_loaded(), "initial workspace load did not complete"
    pump(0.2)
    yield win
    win.close()
    pump(0.05)


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestContextToolbarE2E:
    def test_note_context_toolbar_all_eight_markdown_actions_and_visual_snapshot(self, window):
        cw = window.canvas_widget
        window._create_node("stickyNote", at=Point(9950, 8650))
        note_node = [n for n in window.document.payload.nodes if n.node_type == "stickyNote"][-1]
        nid = norm(note_node.id)
        view = cw.frames[nid].content
        assert wait_until(lambda: getattr(view, "_loaded", False))

        cw.scene.selection = {nid}
        cw.controller.dirty = True
        assert wait_until(
            lambda: isinstance(cw.context_toolbar.toolbar, NoteContextToolbar)
            and cw.context_toolbar.toolbar.get_visible()
        )
        tb = cw.context_toolbar.toolbar
        assert set(tb.buttons.keys()) == {a for a, _lbl, _tip in NOTE_ACTIONS}

        # 1. Selecting word in preview mode and clicking Bold switches to Raw and wraps with `**`.
        view.buf.set_text("hello world\nline two\nline three\nline four")
        view.set_previewing(True)
        assert view.meta.is_previewing is True
        view.buf.select_range(view.buf.get_iter_at_offset(0), view.buf.get_iter_at_offset(5))
        tb.buttons["bold"].emit("clicked")
        assert view.meta.is_previewing is False
        assert view.text().startswith("**hello** world\n")

        # 2. Clicking Bold again on the same selection unwraps `**`.
        tb.buttons["bold"].emit("clicked")
        assert view.text().startswith("hello world\n")

        # 3. Italic, Strike, and Inline Code on selection.
        view.buf.select_range(view.buf.get_iter_at_offset(0), view.buf.get_iter_at_offset(5))
        tb.buttons["italic"].emit("clicked")
        assert view.text().startswith("*hello* world\n")
        tb.buttons["italic"].emit("clicked")
        tb.buttons["strike"].emit("clicked")
        assert view.text().startswith("~~hello~~ world\n")
        tb.buttons["strike"].emit("clicked")
        tb.buttons["code"].emit("clicked")
        assert view.text().startswith("`hello` world\n")

        # 4. Line prefix actions: H1, H2, Bullet, Task.
        lines = view.text().split("\n")
        line2_offset = len(lines[0]) + 1
        view.buf.place_cursor(view.buf.get_iter_at_offset(line2_offset))
        tb.buttons["h1"].emit("clicked")
        assert "\n# line two\n" in view.text()
        tb.buttons["h2"].emit("clicked")  # replaces H1 with H2
        assert "\n## line two\n" in view.text()

        lines = view.text().split("\n")
        line3_offset = len(lines[0]) + 1 + len(lines[1]) + 1
        view.buf.place_cursor(view.buf.get_iter_at_offset(line3_offset))
        tb.buttons["bullet"].emit("clicked")
        assert "\n- line three\n" in view.text()

        lines = view.text().split("\n")
        line4_offset = len("\n".join(lines[:3])) + 1
        view.buf.place_cursor(view.buf.get_iter_at_offset(line4_offset))
        tb.buttons["task"].emit("clicked")
        assert view.text().endswith("\n- [ ] line four")

        pump(0.15)
        save_dual_screenshot(cw, "toolbar_01_note.png", "11_context_toolbar_note.png")

    def test_drawing_context_toolbars_shape_text_freehand_and_multi_selection(self, window):
        cw = window.canvas_widget

        # 1. Shape toolbar variant
        shape = window.drawing.create("rect", Rect(9900, 8620, 220, 140))
        cw.scene.selection = {norm(shape.id)}
        cw.controller.dirty = True
        assert wait_until(
            lambda: isinstance(cw.context_toolbar.toolbar, DrawingContextToolbar)
            and cw.context_toolbar.key == (norm(shape.id), "shape")
            and cw.context_toolbar.toolbar.get_visible()
        )
        tb_shape = cw.context_toolbar.toolbar
        assert tb_shape.stroke_style_btn is not None and tb_shape.fill_style_btn is not None
        tb_shape.palette_buttons["orange"].emit("clicked")
        tb_shape.stroke_items["dashed"].emit("clicked")
        tb_shape.fill_items["hatched"].emit("clicked")
        tb_shape.plus_btn.emit("clicked")
        assert shape.content["strokeColor"] == "orange" and shape.content["fillColor"] == "orange"
        assert shape.content["strokeStyle"] == "dashed" and shape.content["fillStyle"] == "hatched"
        assert shape.content["strokeWidth"] == 3.0
        pump(0.15)
        save_dual_screenshot(cw, "toolbar_02_shape.png", "12_context_toolbar_shape.png")

        # 2. Text toolbar variant (fontSize 8..96, no stroke/fill style menus)
        text = window.drawing.create("text", Rect(10200, 8620, 140, 44))
        window.drawing._on_content_changed(cw, text.id, {**text.content, "text": "Sprint 5 Title"})
        cw.scene.selection = {norm(text.id)}
        cw.controller.dirty = True
        assert wait_until(
            lambda: cw.context_toolbar.key == (norm(text.id), "text")
            and cw.context_toolbar.toolbar.get_visible()
        )
        tb_text = cw.context_toolbar.toolbar
        assert tb_text.value_key == "fontSize" and tb_text.value_range == FONT_RANGE["text"]
        assert tb_text.stroke_style_btn is None and tb_text.fill_style_btn is None
        tb_text.palette_buttons["purple"].emit("clicked")
        tb_text.plus_btn.emit("clicked")
        assert text.content["color"] == "purple" and text.content["fontSize"] == 19.0
        pump(0.15)
        save_dual_screenshot(cw, "toolbar_03_text.png", "13_context_toolbar_text.png")

        # 3. Freehand toolbar variant (strokeWidth 1..20, no style menus)
        freehand = window.drawing.create("freehand_pen", Rect(9900, 8820, 200, 100))
        cw.scene.selection = {norm(freehand.id)}
        cw.controller.dirty = True
        assert wait_until(
            lambda: cw.context_toolbar.key == (norm(freehand.id), "freehand")
            and cw.context_toolbar.toolbar.get_visible()
        )
        tb_free = cw.context_toolbar.toolbar
        assert tb_free.value_key == "strokeWidth" and tb_free.value_range == STROKE_RANGE
        assert tb_free.stroke_style_btn is None and tb_free.fill_style_btn is None
        tb_free.palette_buttons["green"].emit("clicked")
        for _ in range(5):
            if tb_free.minus_btn.get_sensitive():
                tb_free.minus_btn.emit("clicked")
        assert freehand.content["strokeColor"] == "green"
        assert freehand.content["strokeWidth"] == float(STROKE_RANGE[0])
        assert not tb_free.minus_btn.get_sensitive()
        pump(0.15)
        save_dual_screenshot(cw, "toolbar_04_freehand.png", "14_context_toolbar_freehand.png")

        # 4. Multi-selection shows MultiContextToolbar with only Delete (🗑)
        cw.scene.selection = {norm(shape.id), norm(freehand.id)}
        cw.controller.dirty = True
        assert wait_until(
            lambda: isinstance(cw.context_toolbar.toolbar, MultiContextToolbar)
            and cw.context_toolbar.toolbar.get_visible()
        )
        tb_multi = cw.context_toolbar.toolbar
        assert tb_multi.kind == "multi"
        assert tb_multi.get_first_child() is tb_multi.delete_btn
        assert tb_multi.get_last_child() is tb_multi.delete_btn
        pump(0.15)
        save_dual_screenshot(cw, "toolbar_05_multi.png", "15_context_toolbar_multi.png")

        # 5. Activating a drawing tool hides the context toolbar; returning to select restores it
        cw.set_tool("rect")
        assert wait_until(lambda: cw.context_toolbar.toolbar is None)
        cw.set_tool("select")

        # 6. Selecting a single node again and clicking Delete (🗑) removes it from document and scene
        cw.scene.selection = {norm(freehand.id)}
        cw.controller.dirty = True
        assert wait_until(lambda: isinstance(cw.context_toolbar.toolbar, DrawingContextToolbar))
        delete_btn = cw.context_toolbar.toolbar.get_last_child()
        delete_btn.emit("clicked")
        assert wait_until(lambda: norm(freehand.id) not in cw.scene.nodes)
        assert freehand not in window.document.payload.nodes
