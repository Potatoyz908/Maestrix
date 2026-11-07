"""Sprint 9 Vertice: K1 ids, K4 agentType, dialog fields -> TerminalContent (pure; isolated data dir)."""
import json

import pytest

from maestri_core.models.node import CanvasNode, TerminalContent, agent_type_for_command
from maestri_core.terminal_fields import apply_fields, resolve_role, role_name, terminal_fields_from_dialog


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_MAESTRI_DATA_DIR", str(tmp_path))
    (tmp_path / "preferences.json").write_text(json.dumps({"rolePresets": [
        {"id": "aaaaaaaa-0000-0000-0000-000000000001", "name": "Reviewer"}]}))
    return tmp_path


@pytest.mark.parametrize("cmd, kind", [
    ("claude", "claude_code"), ("claude --resume", "claude_code"), ("/usr/local/bin/codex", "codex"),
    ("npx gemini", "gemini_cli"), ("opencode", "open_code"), ("bash", "generic_shell"), ("", "generic_shell"),
    ("htop 'unterminated", "generic_shell"),
])
def test_agent_type_for_command(cmd, kind):
    assert agent_type_for_command(cmd) == kind


def test_dialog_fields_map_to_serialized_content():
    f = terminal_fields_from_dialog({"name": "A", "command": "claude", "cwd": "/tmp", "icon": "asterisk", "color": "#D97706",
                                     "theme": "dracula", "font": "JetBrains Mono", "font_size": "13", "monitor": True,
                                     "maestro": True, "role": "reviewer"})
    assert f == {"agent_type": "claude_code", "icon": "asterisk", "color": "#D97706", "theme_id": "dracula",
                 "font_family": "JetBrains Mono", "font_size": 13.0, "monitor_with_ombro": True, "is_manager": True,
                 "assigned_role_id": "AAAAAAAA-0000-0000-0000-000000000001"}
    nid = "11111111-2222-3333-4444-555555555555"
    node = CanvasNode(id=nid, node_type="terminal",
                      content=TerminalContent(id=nid, terminal_id=nid, name="A", command="claude", **f))
    d = CanvasNode.from_dict(node.to_dict()).to_dict()["content"]["terminal"]["_0"]   # round trip keeps every field
    assert d["id"] == nid and d["agentType"] == "claude_code" and d["isManager"] is True and d["monitorWithOmbro"] is True
    assert d["themeId"] == "dracula" and d["fontFamily"] == "JetBrains Mono" and d["fontSize"] == 13.0
    assert d["assignedRoleId"] == "AAAAAAAA-0000-0000-0000-000000000001" and d["icon"] == "asterisk"


def test_unknown_theme_and_role_are_dropped_and_preset_sets_agent_type():
    f = terminal_fields_from_dialog({"preset": "codex", "theme": "Hacker Matrix", "role": "Nobody", "font_size": 0})
    assert f == {"agent_type": "codex", "assigned_role_id": None}
    assert terminal_fields_from_dialog({"role": "None"})["assigned_role_id"] is None


def test_apply_fields_reports_changes_and_role_name():
    c = TerminalContent(name="T")
    changed = apply_fields(c, terminal_fields_from_dialog({"maestro": True, "role": "Reviewer", "command": "bash"}))
    assert set(changed) == {"is_manager", "assigned_role_id"} and c.is_manager
    assert role_name(c) == "Reviewer" and resolve_role("aaaaaaaa-0000-0000-0000-000000000001")["name"] == "Reviewer"
