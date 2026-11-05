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
