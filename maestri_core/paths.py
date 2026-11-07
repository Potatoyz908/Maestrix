"""Where Maestrix keeps its data (shared contract with the Swift core).

`OPEN_MAESTRI_DATA_DIR` (an absolute path) replaces ~/.open-maestri for EVERY read and write: manifest.json, workspaces/,
run/ (agent.sock, auth.token), preferences.json, routines.json, sidebar-layout.json, notes/, roles/. Without it the data
lives in ~/.open-maestri as before. HOME is never the knob: GTK, GI_TYPELIB_PATH and site-packages depend on ~/.local.

Both functions resolve at call time (never cache), so tests can point the variable at a temporary directory.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_ENV = "OPEN_MAESTRI_DATA_DIR"
DATA_DIR_NAME = ".open-maestri"


def data_dir() -> Path:
    override = (os.environ.get("MAESTRIX_DATA_DIR") or os.environ.get(DATA_DIR_ENV, "")).strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DATA_DIR_NAME


def run_dir() -> Path:
    return data_dir() / "run"
