"""Terminal dialog data -> TerminalContent fields (Sprint 9 Vertice item 6). Pure; no GTK.

The New/Edit Terminal dialogs (terminal_dialog.py, Pixel) return a dict. Every key is optional and tolerated under the names the
dialog has used so far:

    name, command, cwd                      -> name / command / working_directory (handled by the caller for New)
    icon, color                             -> icon / color (a preset's icon/color when the dialog sends only `preset`)
    theme | theme_id                        -> theme_id      (only ids that exist in terminal_themes.THEMES; else unset)
    font | font_family, font_size           -> font_family / font_size
    monitor                                 -> monitor_with_ombro   (the field the schema serializes: "monitorWithOmbro")
    maestro | is_manager                    -> is_manager           (contract K8)
    role | role_id                          -> assigned_role_id     (a rolePresets id; a role NAME is resolved through
                                                                     preferences.json rolePresets, unknown names are dropped)
    preset                                  -> agent_type (+ icon/color defaults) via the command it launches
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .models.node import agent_type_for_command
from .paths import data_dir

PRESET_COMMANDS = {"claude": "claude", "codex": "codex", "gemini": "gemini", "opencode": "opencode", "shell": "bash"}
NONE_ROLE = {"", "none", "no role", "default"}


def _known_themes() -> set[str]:
    try:
        from maestri_canvas.terminal_themes import THEMES   # canvas-cairo on sys.path in the app
        return set(THEMES)
    except Exception:   # noqa: BLE001 - without the canvas package, accept any non-empty id
        return set()


def role_presets() -> list[dict]:
    """prefs.rolePresets ([{id, name, ...}]) from preferences.json; [] when absent/unreadable."""
    try:
        with open(data_dir() / "preferences.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    roles = data.get("rolePresets") if isinstance(data, dict) else None
    return [r for r in roles if isinstance(r, dict) and r.get("id")] if isinstance(roles, list) else []


def resolve_role(value: Any) -> Optional[dict]:
    """The role preset for an id or a (case-insensitive) name; None for "None"/unknown."""
    if not value or str(value).strip().lower() in NONE_ROLE:
        return None
    v = str(value).strip()
    for r in role_presets():
        if str(r.get("id", "")).upper() == v.upper() or str(r.get("name", "")).strip().lower() == v.lower():
            return r
    return None


def terminal_fields_from_dialog(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keyword arguments for TerminalContent (everything except name/command/working_directory)."""
    out: Dict[str, Any] = {}
    command = data.get("command") or PRESET_COMMANDS.get(str(data.get("preset") or ""), "")
    if command:
        out["agent_type"] = agent_type_for_command(command)
    if data.get("icon"):
        out["icon"] = str(data["icon"])
    if data.get("color"):
        out["color"] = str(data["color"])

    theme = data.get("theme_id", data.get("theme"))
    if theme:
        known = _known_themes()
        if not known or theme in known:
            out["theme_id"] = str(theme)
    font = data.get("font_family", data.get("font"))
    if font:
        out["font_family"] = str(font)
    try:
        size = float(data.get("font_size") or 0)
    except (TypeError, ValueError):
        size = 0.0
    if size > 0:
        out["font_size"] = size

    if "monitor" in data:
        out["monitor_with_ombro"] = bool(data["monitor"])
    if "maestro" in data or "is_manager" in data:
        out["is_manager"] = bool(data.get("maestro", data.get("is_manager")))
    if "role" in data or "role_id" in data:
        role = resolve_role(data.get("role_id", data.get("role")))
        out["assigned_role_id"] = str(role["id"]).upper() if role else None
    return out


def apply_fields(content: Any, fields: Dict[str, Any]) -> list[str]:
    """Set `fields` on an existing TerminalContent (Edit…); returns the attribute names that changed."""
    changed = []
    for k, v in fields.items():
        if hasattr(content, k) and getattr(content, k) != v:
            setattr(content, k, v)
            changed.append(k)
    return changed


def role_name(content: Any) -> Optional[str]:
    rid = getattr(content, "assigned_role_id", None)
    if not rid:
        return None
    role = resolve_role(rid)
    return str(role.get("name")) if role else None
