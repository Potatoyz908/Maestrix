"""GI typelib search path. Call ensure_typelib_paths() BEFORE gi.require_version("Vte", "3.91").

The GTK 4 VTE typelib (Vte-3.91.typelib) is not shipped by the distro package on this host (only the GTK 3 Vte-2.91); it lives in a
user directory that is not on the default search path. Extra directories come from MAESTRI_TYPELIB_PATH (os.pathsep-separated) plus
~/.local/lib/girepository-1.0 when it exists. Idempotent; works without GI_TYPELIB_PATH being exported."""
from __future__ import annotations

import os
from pathlib import Path

import gi

_done = False


def candidate_dirs() -> list[str]:
    dirs = [d for d in os.environ.get("MAESTRI_TYPELIB_PATH", "").split(os.pathsep) if d]
    dirs.append(str(Path.home() / ".local/lib/girepository-1.0"))
    return [d for d in dict.fromkeys(dirs) if Path(d).is_dir()]


def ensure_typelib_paths() -> list[str]:
    global _done
    dirs = candidate_dirs()
    if not _done:
        repo = gi.Repository.get_default()
        for d in dirs:
            repo.prepend_search_path(d)
        _done = True
    return dirs


def vte_available() -> bool:
    """True when Vte 3.91 (GTK 4) can be imported."""
    ensure_typelib_paths()
    try:
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte  # noqa: F401
        return True
    except (ValueError, ImportError):
        return False
