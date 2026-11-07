from maestri_core.preferences import Preferences


def test_interface_theme_defaults_to_light_and_roundtrips():
    assert Preferences().interface_theme == "light"
    p = Preferences.from_dict({"interfaceTheme": "dark"})
    assert p.interface_theme == "dark" and p.to_dict()["interfaceTheme"] == "dark"


def test_invalid_interface_theme_falls_back_to_light_and_unknown_keys_survive():
    p = Preferences.from_dict({"interfaceTheme": "neon", "agentPresets": [{"name": "Keep"}]})
    assert p.interface_theme == "light"
    assert p.to_dict()["agentPresets"] == [{"name": "Keep"}]
