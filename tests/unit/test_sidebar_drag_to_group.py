import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "gtk4-ui" / "src"), str(ROOT / "canvas-cairo")]

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.workspace import SidebarGroup, SidebarLayout  # noqa: E402
from ui.sidebar import WS_DRAG_PREFIX, SidebarView  # noqa: E402


def make():
    sb = SidebarView(MaestriClient(port=9, token="x", timeout=0.3))   # offline client, isolated data dir
    group = SidebarGroup(name="Pentest", items=[], is_collapsed=True)
    sb._sidebar_layout = SidebarLayout(top_level_items=["A"], groups=[group])
    sb._save_sidebar_layout = lambda: None
    return sb, group


def test_dropping_a_workspace_on_a_group_moves_it_there_and_expands_the_group():
    sb, group = make()
    assert sb._on_ws_dropped(f"{WS_DRAG_PREFIX}a", group.id)
    assert [i.upper() for i in group.items] == ["A"] and not group.is_collapsed
    assert "A" not in [i.upper() for i in sb._sidebar_layout.top_level_items]


def test_dropping_on_the_main_list_takes_it_out_of_the_group_and_same_place_is_ignored():
    sb, group = make()
    sb._on_ws_dropped(f"{WS_DRAG_PREFIX}A", group.id)
    assert not sb._on_ws_dropped(f"{WS_DRAG_PREFIX}A", group.id)   # already there
    assert sb._on_ws_dropped(f"{WS_DRAG_PREFIX}A", None)
    assert group.items == [] and "A" in sb._sidebar_layout.top_level_items


def test_foreign_text_is_refused_and_lists_accept_drops():
    sb, group = make()
    assert not sb._on_ws_dropped("hello", group.id)
    kinds = [type(c).__name__ for c in sb.ws_list_box.observe_controllers()]
    assert "DropTarget" in kinds
