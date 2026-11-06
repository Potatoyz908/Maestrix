"""CanvasWidget: header badges (NodeFrame.set_badge / set_node_badge), the active-terminal tracking used by 'Ask agent', and
the fileTree 'ask-requested' re-emission. Needs a display and Vte 3.91; skipped otherwise."""
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.geometry import Rect  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.widget import CanvasWidget, load_vte  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)
Vte = load_vte()
if Vte is None:
    pytest.skip("Vte 3.91 not available", allow_module_level=True)

T1 = "AAAAAAAA-0000-4000-8000-000000000001"
T2 = "AAAAAAAA-0000-4000-8000-000000000002"
FT = "FFFFFFFF-0000-4000-8000-000000000003"


def pump(cond=lambda: False, timeout=3.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.003)
    return cond()


@pytest.fixture
def canvas(tmp_path):
    cw = CanvasWidget()
    win = Gtk.Window(default_width=900, default_height=700)
    win.set_child(cw)
    for i, nid in enumerate((T1, T2)):
        cw.add_node(NodeItem(nid, Rect(9800 + 450 * i, 8500, 420, 300), i, "terminal", f"T{i}"), Vte.Terminal())
    win.present()
    pump(timeout=0.3)
    yield cw
    win.destroy()
    pump(timeout=0.3)


def test_badge_add_update_remove(canvas):
    frame = canvas.frames[T1]
    assert canvas.set_node_badge(T1, "routine", "⏱", "Routines:\nTests", ("routine-running",))
    lbl = frame.badge("routine")
    assert lbl.get_label() == "⏱" and lbl.get_tooltip_text() == "Routines:\nTests" and lbl.has_css_class("routine-running")
    assert lbl.get_parent() is frame.badges
    canvas.set_node_badge(T1, "routine", "⏱ 2")               # update in place, css replaced
    assert frame.badge("routine") is lbl and lbl.get_label() == "⏱ 2" and not lbl.has_css_class("routine-running")
    canvas.set_node_badge(T1, "other", "🔗")
    assert [c.get_label() for c in (frame.badges.get_first_child(), frame.badges.get_last_child())] == ["⏱ 2", "🔗"]
    canvas.set_node_badge(T1, "routine", None)
    assert frame.badge("routine") is None and frame.badges.get_first_child().get_label() == "🔗"
    assert not canvas.set_node_badge("BBBBBBBB-0000-4000-8000-000000000009", "routine", "⏱")


def test_active_terminal_follows_focus_selection_and_removal(canvas):
    assert canvas.active_terminal_id is None
    canvas.frames[T2].content.grab_focus()
    assert pump(lambda: canvas.active_terminal_id == T2)
    canvas.frames[T1].content.grab_focus()
    assert pump(lambda: canvas.active_terminal_id == T1)
    canvas.controller._set_selection({T2})                    # selecting one terminal alone also activates it
    assert canvas.active_terminal_id == T2
    canvas.controller._set_selection({T1, T2})                # multi-selection keeps the previous target
    assert canvas.active_terminal_id == T2
    canvas.active_terminal_id = T1
    canvas.remove_node(T1)
    assert canvas.active_terminal_id is None


def test_file_tree_ask_is_reemitted_with_node_id(canvas, tmp_path):
    canvas.add_node(NodeItem(FT, Rect(9800, 8900, 420, 300), 3, "fileTree", "Files"), cwd=str(tmp_path))
    got = []
    canvas.connect("ask-requested", lambda _c, nid, text: got.append((nid, text)))
    canvas.frames[FT].content.emit("ask-requested", "Explain this change:\n\n```diff\n+x\n```")
    assert got == [(FT, "Explain this change:\n\n```diff\n+x\n```")]
