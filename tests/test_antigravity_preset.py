"""Antigravity preset: launched as `agy --dangerously-skip-permissions` (the user's explicit choice; `agy --permissions`
is not a real flag: agy answers "flags provided but not defined: -permissions")."""
from maestri_core.models.node import agent_type_for_command
from ui.agents_settings import default_agents
from ui.iconography import sf_symbol_icon
from ui.terminal_dialog import PRESETS


def test_antigravity_launches_with_skip_permissions():
    preset = next(p for p in PRESETS if p['id'] == 'antigravity')
    assert preset['name'] == 'Antigravity'
    assert preset['command'] == 'agy --dangerously-skip-permissions'
    assert preset['agent_type'] == 'antigravity_cli'
    assert preset['sf_icon'] == 'sparkle'
    assert sf_symbol_icon(preset['sf_icon']) == 'maestri-sparkle-symbolic'


def test_antigravity_preferences_default_matches_launch_preset():
    preset = next(p for p in default_agents() if p['name'] == 'Antigravity')
    assert preset['command'] == 'agy --dangerously-skip-permissions'
    assert preset['agentType'] == 'antigravity_cli'
    assert preset['icon'] == 'sparkle'
    assert preset['isActive'] and preset['isBuiltIn']


def test_antigravity_command_detection():
    assert agent_type_for_command('agy') == 'antigravity_cli'
    assert agent_type_for_command('agy --dangerously-skip-permissions') == 'antigravity_cli'
    assert agent_type_for_command('/opt/bin/agy') == 'antigravity_cli'
    assert agent_type_for_command('bash') == 'generic_shell'
