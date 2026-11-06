"""
User preferences for Open Maestri GTK 4 Native.
Reads/writes ~/.open-maestri/preferences.json (schemaVersion 1, camelCase keys, shared with the macOS app).
Only the fields the Linux UI edits are modelled; every other key (agentPresets, shortcuts, ...) is preserved on save.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

TERMINAL_THEMES = ("system", "dark", "light", "dracula", "solarized-dark", "solarized-light", "nord", "one-dark", "tokyo-night")
CANVAS_BACKGROUNDS = ("dotGrid", "solid", "transparent")
LANGUAGES = ("en", "zh-Hans")
IDES = ("cursor", "vscode", "xcode")
AUTOSAVE_RANGE = (5, 600)
FONT_SIZE_RANGE = (8.0, 32.0)


def _pick(value: Any, allowed, default):
    return value if value in allowed else default


@dataclass
class Preferences:
    terminal_font_family: str = "Monospace"
    terminal_font_size: float = 13.0
    terminal_theme: str = "dark"
    canvas_background: str = "dotGrid"
    preferred_ide: str = "cursor"
    language: str = "en"
    autosave_interval_seconds: int = 30
    scrollback_memory_limit: bool = False
    option_as_meta: bool = False
    canvas_snapping: bool = True   # smart alignment guides while dragging nodes (Linux port; unknown keys are ignored by Swift)
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = dict(self._extra)
        d.update({
            "schemaVersion": 1,
            "terminalFontFamily": self.terminal_font_family,
            "terminalFontSize": float(self.terminal_font_size),
            "terminalTheme": self.terminal_theme,
            "canvasBackground": self.canvas_background,
            "preferredIDE": self.preferred_ide,
            "language": self.language,
            "autosaveIntervalSeconds": int(self.autosave_interval_seconds),
            "scrollbackMemoryLimit": bool(self.scrollback_memory_limit),
            "optionAsMeta": bool(self.option_as_meta),
            "canvasSnapping": bool(self.canvas_snapping),
        })
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Preferences":
        """Tolerant: missing/invalid values fall back to defaults (older files, hand edits)."""
        base = cls()
        known = {"schemaVersion", "terminalFontFamily", "terminalFontSize", "terminalTheme", "canvasBackground",
                 "preferredIDE", "language", "autosaveIntervalSeconds", "scrollbackMemoryLimit", "optionAsMeta",
                 "canvasSnapping"}
        try:
            size = float(d.get("terminalFontSize", base.terminal_font_size))
        except (TypeError, ValueError):
            size = base.terminal_font_size
        try:
            interval = int(d.get("autosaveIntervalSeconds", base.autosave_interval_seconds))
        except (TypeError, ValueError):
            interval = base.autosave_interval_seconds
        family = d.get("terminalFontFamily")
        return cls(
            terminal_font_family=family if isinstance(family, str) and family else base.terminal_font_family,
            terminal_font_size=min(FONT_SIZE_RANGE[1], max(FONT_SIZE_RANGE[0], size)),
            terminal_theme=_pick(d.get("terminalTheme"), TERMINAL_THEMES, base.terminal_theme),
            canvas_background=_pick(d.get("canvasBackground"), CANVAS_BACKGROUNDS, base.canvas_background),
            preferred_ide=_pick(d.get("preferredIDE"), IDES, base.preferred_ide),
            language=_pick(d.get("language"), LANGUAGES, base.language),
            autosave_interval_seconds=min(AUTOSAVE_RANGE[1], max(AUTOSAVE_RANGE[0], interval)),
            scrollback_memory_limit=bool(d.get("scrollbackMemoryLimit", False)),
            option_as_meta=bool(d.get("optionAsMeta", False)),
            canvas_snapping=bool(d.get("canvasSnapping", True)),
            _extra={k: v for k, v in d.items() if k not in known},
        )


class PreferencesStore:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else Path.home() / ".open-maestri"

    @property
    def path(self) -> Path:
        return self.base_dir / "preferences.json"

    def load(self) -> Preferences:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return Preferences.from_dict(data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            return Preferences()

    def save(self, prefs: Preferences) -> Path:
        """Atomic write (tmp file + os.replace), like PersistenceManager."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=str(self.base_dir), delete=False, encoding="utf-8") as tf:
                tmp = tf.name
                json.dump(prefs.to_dict(), tf, indent=2, ensure_ascii=False)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        return self.path
