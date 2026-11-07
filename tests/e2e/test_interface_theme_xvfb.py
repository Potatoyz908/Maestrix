"""Interface theme (Light / Dark · Maestrix): the Settings dropdown persists it, applies it live, and the palette is valid GTK CSS.
Needs a display (Xvfb)."""
import os, sys, tempfile
from pathlib import Path
import pytest
REPO = Path(__file__).resolve().parents[2]
for p in (REPO/"canvas-cairo", REPO/"gtk4-ui"/"src", REPO): sys.path.insert(0, str(p))
pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs a display (run under Xvfb)")
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def test_palette_is_valid_css_and_light_restores_the_base_theme():
    Gtk.init()
    from ui.theme import DARK_CSS, InterfaceTheme
    errors = []
    theme = InterfaceTheme()
    theme.provider.connect("parsing-error", lambda *a: errors.append(str(a[-1])))
    theme.apply("dark")
    assert not errors, errors
    assert "#168bf1" in DARK_CSS and "#9354e8" in DARK_CSS      # the Maestrix icon blue -> purple gradient
    assert ".maestri-top-toolbar" in DARK_CSS and "button.maestri-island-btn.maestri-island-btn.maestri-island-btn.active" in DARK_CSS
    theme.apply("light")
    assert theme.provider.to_string().strip() == ""


def test_settings_dropdown_persists_and_applies_live():
    Gtk.init()
    from maestri_core.preferences import Preferences, PreferencesStore
    from ui.settings_window import SettingsWindow
    from ui.theme import InterfaceTheme
    with tempfile.TemporaryDirectory() as t:
        store = PreferencesStore(Path(t))
        store.save(Preferences.from_dict({"agentPresets": [{"name": "Keep"}]}))
        theme, seen = InterfaceTheme(), []
        parent = Gtk.Window()
        w = SettingsWindow(parent, store, lambda p: (seen.append(p.interface_theme), theme.apply(p.interface_theme)))
        w.theme_dropdown.set_selected(1)
        assert store.load().interface_theme == "dark" and seen == ["dark"]
        assert store.load()._extra["agentPresets"] == [{"name": "Keep"}]
        w.destroy()
        w = SettingsWindow(parent, store, lambda p: None)
        assert w.theme_dropdown.get_selected() == 1, "the choice must survive reopening"
        w.destroy(); parent.destroy()
