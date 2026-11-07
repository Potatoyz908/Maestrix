"""Pure (no GTK) file operations behind the FileTree context menu, and the "what preview node does a dropped file become" rule.

Every mutating helper validates first and raises `FileOpError` with a human message (shown in the node's status line), so the
view never half-applies an operation. Delete goes to the freedesktop Trash through Gio (see `FileTreeView.trash`), never `rm -rf`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Text-ish files open as an editable sticky note bound to the file (storageMode custom, same as macOS Maestri's Finder drop);
# everything else (images, PDF, video, archives...) opens read-only in a portal on its file:// URI.
_TEXT_EXTS = frozenset("""
md markdown txt text rst adoc org log csv tsv json jsonl yaml yml toml ini cfg conf env properties xml html htm css scss less
py pyi js mjs cjs ts tsx jsx java kt kts swift c h cc cpp hpp hh m mm rs go rb php pl lua sh bash zsh fish ps1 sql r scala dart
ex exs erl hs ml clj vim el lisp nix gradle make mk cmake dockerfile gitignore editorconfig
""".split())
_TEXT_NAMES = frozenset({"makefile", "dockerfile", "readme", "license", "notice", "changelog", "authors", "contributing"})

PREVIEW_W, PREVIEW_H, PREVIEW_STEP = 320.0, 240.0, 260.0   # Swift WorkspaceCanvasView+FileDrop: 320x240, stacked every 260


class FileOpError(Exception):
    pass


def validate_name(name: str) -> str:
    """A single path component the user typed: no separators, no traversal, not empty, no NUL / control chars."""
    n = name.strip()
    if not n:
        raise FileOpError("Name can't be empty")
    if n in (".", ".."):
        raise FileOpError(f"'{n}' is not a valid name")
    if "/" in n or "\x00" in n:
        raise FileOpError("Name can't contain '/'")
    if any(ord(c) < 32 for c in n):
        raise FileOpError("Name can't contain control characters")
    if len(n.encode()) > 255:
        raise FileOpError("Name is too long")
    return n


def unique_child(folder: str, base: str) -> str:
    """'untitled' -> 'untitled 2' -> ... ; the extension is kept ('notes.txt' -> 'notes 2.txt')."""
    if not os.path.lexists(os.path.join(folder, base)):
        return base
    stem, ext = os.path.splitext(base)
    if not stem:   # dotfile like ".env": no extension split
        stem, ext = base, ""
    i = 2
    while os.path.lexists(os.path.join(folder, f"{stem} {i}{ext}")):
        i += 1
    return f"{stem} {i}{ext}"


def _target(folder: str, name: str) -> str:
    path = os.path.join(folder, validate_name(name))
    if os.path.lexists(path):
        raise FileOpError(f"'{os.path.basename(path)}' already exists")
    return path


def create_file(folder: str, name: str) -> str:
    path = _target(folder, name)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)   # O_EXCL: never truncate a file that appeared meanwhile
    except OSError as e:
        raise FileOpError(e.strerror or str(e)) from e
    os.close(fd)
    return path


def create_folder(folder: str, name: str) -> str:
    path = _target(folder, name)
    try:
        os.mkdir(path)
    except OSError as e:
        raise FileOpError(e.strerror or str(e)) from e
    return path


def rename(path: str, new_name: str) -> str:
    new_name = validate_name(new_name)
    if new_name == os.path.basename(path):
        return path
    dest = _target(os.path.dirname(path), new_name)
    try:
        os.rename(path, dest)   # same folder: atomic, and the lexists check above keeps it from clobbering
    except OSError as e:
        raise FileOpError(e.strerror or str(e)) from e
    return dest


def check_move(path: str, dest_dir: str) -> str:
    """Destination path of moving `path` into `dest_dir`, or FileOpError (into itself / a descendant, onto an existing name)."""
    src = os.path.realpath(path)
    dst_dir = os.path.realpath(dest_dir)
    if not os.path.isdir(dst_dir):
        raise FileOpError("Destination is not a folder")
    if os.path.dirname(src) == dst_dir:
        raise FileOpError("Already in that folder")
    if os.path.isdir(src) and (dst_dir == src or dst_dir.startswith(src.rstrip(os.sep) + os.sep)):
        raise FileOpError("Can't move a folder into itself")
    dest = os.path.join(dst_dir, os.path.basename(path))
    if os.path.lexists(dest):
        raise FileOpError(f"'{os.path.basename(path)}' already exists there")
    return dest


def is_text_file(path: str, content_type: str | None = None) -> bool:
    if content_type:
        ct = content_type.lower()
        if ct.startswith("text/") or ct in ("application/json", "application/xml", "application/x-shellscript",
                                            "application/javascript", "application/x-yaml", "application/toml"):
            return True
        if ct.startswith(("image/", "video/", "audio/")) or ct in ("application/pdf", "application/zip"):
            return False
    base = os.path.basename(path).lower()
    ext = base.rsplit(".", 1)[-1] if "." in base.lstrip(".") else base.lstrip(".")
    return ext in _TEXT_EXTS or base in _TEXT_NAMES


@dataclass(frozen=True)
class PreviewSpec:
    kind: str     # "stickyNote" | "portal" | "fileTree"
    path: str
    title: str


def preview_spec(path: str, content_type: str | None = None) -> PreviewSpec:
    """The node a file dropped on the empty canvas becomes: a folder -> a File Tree rooted there; a text file -> a sticky note
    editing that file (storageMode custom); anything else -> a portal on its file:// URI (WebKit renders images, video, PDF)."""
    name = os.path.basename(path.rstrip(os.sep)) or path
    if os.path.isdir(path):
        return PreviewSpec("fileTree", path, name)
    if is_text_file(path, content_type):
        return PreviewSpec("stickyNote", path, name)
    return PreviewSpec("portal", path, name)


def preview_frames(n: int, x: float, y: float) -> list[tuple[float, float, float, float]]:
    """One frame per dropped file, top-left at the drop point, stacked downwards."""
    return [(x, y + i * PREVIEW_STEP, PREVIEW_W, PREVIEW_H) for i in range(n)]
