import json

from maestri_core.preferences import Preferences, PreferencesStore


def test_defaults_when_missing(tmp_path):
    p = PreferencesStore(tmp_path).load()
    assert p.terminal_theme == "dark" and p.autosave_interval_seconds == 30


def test_roundtrip_preserves_unknown_keys(tmp_path):
    store = PreferencesStore(tmp_path)
    (tmp_path / "preferences.json").write_text(json.dumps({"schemaVersion": 1, "agentPresets": [{"x": 1}], "shortcuts": {"a": 1}, "language": "zh-Hans"}))
    p = store.load()
    assert p.language == "zh-Hans"
    p.terminal_font_size = 15
    store.save(p)
    d = json.loads((tmp_path / "preferences.json").read_text())
    assert d["agentPresets"] == [{"x": 1}] and d["shortcuts"] == {"a": 1}
    assert d["terminalFontSize"] == 15.0 and d["language"] == "zh-Hans"
    assert not [f for f in tmp_path.iterdir() if f.name != "preferences.json"]


def test_invalid_values_fall_back_and_clamp(tmp_path):
    p = Preferences.from_dict({"terminalTheme": "nope", "terminalFontSize": 999, "autosaveIntervalSeconds": "x", "language": 3})
    assert p.terminal_theme == "dark" and p.terminal_font_size == 32.0
    assert p.autosave_interval_seconds == 30 and p.language == "en"


def test_corrupt_file_gives_defaults(tmp_path):
    (tmp_path / "preferences.json").write_text("{not json")
    assert PreferencesStore(tmp_path).load() == Preferences()


def test_canvas_snapping_defaults_on_and_roundtrips(tmp_path):
    assert Preferences().canvas_snapping is True
    assert Preferences.from_dict({}).canvas_snapping is True               # older files without the key
    store = PreferencesStore(tmp_path)
    p = store.load()
    p.canvas_snapping = False
    store.save(p)
    assert json.loads((tmp_path / "preferences.json").read_text())["canvasSnapping"] is False
    assert store.load().canvas_snapping is False


def test_agent_economy_mode_round_trip_default_off():
    from maestri_core.preferences import Preferences
    assert Preferences().agent_economy_mode is False
    d = Preferences(agent_economy_mode=True).to_dict()
    assert d["agentEconomyMode"] is True and Preferences.from_dict(d).agent_economy_mode is True


def test_agent_compact_language_round_trip_default_off():
    from maestri_core.preferences import Preferences
    assert Preferences().agent_compact_language is False
    d = Preferences(agent_economy_mode=True, agent_compact_language=True).to_dict()
    assert d["agentCompactLanguage"] is True and Preferences.from_dict(d).agent_compact_language is True


def test_agent_language_dropdown_maps_to_compact_flag():
    from maestri_core.preferences import (AGENT_LANGUAGE_HELP, AGENT_LANGUAGE_LABELS, AGENT_LANGUAGES, agent_language,
                                          compact_for)
    assert [AGENT_LANGUAGE_LABELS[v] for v in AGENT_LANGUAGES] == ["Short replies", "MX telegrams"]
    assert agent_language(False) == "short" and agent_language(True) == "mx"
    assert compact_for("short") is False and compact_for("mx") is True
    assert "5 lines" in AGENT_LANGUAGE_HELP["short"] and "ok:/err:/q:" in AGENT_LANGUAGE_HELP["mx"]


def test_settings_agent_language_hidden_until_economy_on(tmp_path):
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "gtk4-ui" / "src"), str(root / "canvas-cairo")]
    import gi
    gi.require_version("Gtk", "4.0")
    from ui.settings_window import SettingsWindow
    store = PreferencesStore(tmp_path)
    win = SettingsWindow(None, store, lambda _p: None)
    assert not win.agent_lang_dropdown.get_visible() and not win.agent_lang_help.get_visible()
    assert "40 lines" in win.economy_help.get_label()
    win.economy_switch.set_active(True)
    assert win.agent_lang_dropdown.get_visible() and win.agent_lang_label.get_visible()
    assert win.agent_lang_dropdown.get_selected() == 0 and "5 lines" in win.agent_lang_help.get_label()
    win.agent_lang_dropdown.set_selected(1)
    assert store.load().agent_compact_language is True and "ok:/err:/q:" in win.agent_lang_help.get_label()
    assert json.loads((tmp_path / "preferences.json").read_text())["agentCompactLanguage"] is True
    win.agent_lang_dropdown.set_selected(0)
    assert store.load().agent_compact_language is False
    win.economy_switch.set_active(False)
    assert not win.agent_lang_dropdown.get_visible()
    win.destroy()
