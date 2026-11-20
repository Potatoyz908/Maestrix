import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "gtk4-ui" / "src"), str(ROOT / "canvas-cairo")]

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from maestri_core.preferences import Preferences  # noqa: E402
from ui.eco_button import EcoButton  # noqa: E402


def test_leaf_turns_economy_and_language_on_and_follows_settings():
    seen = []
    b = EcoButton(Preferences(), seen.append)
    assert not b.has_css_class("on") and not b.lang_box.get_visible()   # language only with economy on
    b.switch.set_active(True)
    assert seen[-1].agent_economy_mode and not seen[-1].agent_compact_language
    assert b.has_css_class("on") and b.lang_box.get_visible()
    b.lang.set_selected(1)                                                 # MX telegrams
    assert seen[-1].agent_compact_language and "MX" in b.get_tooltip_text()
    assert "telegrams" in b.lang_help.get_label()
    n = len(seen)
    b.sync(Preferences(agent_economy_mode=False))                          # a change made in Settings
    assert len(seen) == n and not b.switch.get_active() and not b.has_css_class("on")
