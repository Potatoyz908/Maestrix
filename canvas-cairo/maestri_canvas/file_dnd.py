"""Native file drag-and-drop between canvas nodes (and from external file managers).

Source side (FileTreeView rows): `file_content_provider(files)` offers, in order of preference, a Gdk.FileList (text/uri-list on the
wire: what terminals and file managers accept), a single Gio.File (what StickyNoteView's DropTarget asks for) and plain text paths.

Drop side (terminals): `install_terminal_drop(widget, write)` accepts Gdk.FileList / Gio.File (internal drags and Nautilus & co.) or
plain text, and pastes the result into the shell WITHOUT pressing Enter, like GNOME Terminal: each path POSIX-shell-quoted, separated
by spaces, with a trailing space so the user can keep typing.
"""
from __future__ import annotations

import shlex
from typing import Callable, Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk


def file_arg(f: Gio.File | str) -> str:
    """Local path when there is one (file://, FUSE-backed mounts), else the URI (sftp://...)."""
    if isinstance(f, str):
        return f
    return f.get_path() or f.get_uri()


def shell_quote(arg: str) -> str:
    """POSIX quoting via shlex.quote: safe names stay bare (src/main.py), anything else is single-quoted with embedded quotes
    escaped ('it'"'"'s'); valid for sh/bash/zsh/fish. Names holding control characters (newline, tab, ESC...) use ANSI-C $'...'
    quoting (bash/zsh/ksh) instead, so no raw control byte ever reaches the line editor."""
    if not any(ord(c) < 32 or ord(c) == 127 for c in arg):
        return shlex.quote(arg)
    out = []
    for c in arg:
        if c in "\\'":
            out.append("\\" + c)
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 32 or ord(c) == 127:
            out.append(f"\\x{ord(c):02x}")
        else:
            out.append(c)
    return "$'" + "".join(out) + "'"


def shell_quote_paths(files: Iterable[Gio.File | str]) -> str:
    """['/a/b c', '/d'] -> "'/a/b c' /d " (space-separated, trailing space so the user keeps typing). Empty input -> ""."""
    args = [shell_quote(file_arg(f)) for f in files]
    return " ".join(args) + " " if args else ""


def file_content_provider(files: list[Gio.File]) -> Gdk.ContentProvider:
    providers = [Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, Gdk.FileList.new_from_list(files)))]
    if len(files) == 1:
        providers.append(Gdk.ContentProvider.new_for_value(GObject.Value(Gio.File, files[0])))
    text = "\n".join(file_arg(f) for f in files)
    providers.append(Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
    return Gdk.ContentProvider.new_union(providers)


def dropped_text(value) -> str:
    """What a drop types into the shell: quoted paths for files, the text itself for a text drag."""
    if isinstance(value, Gdk.FileList):
        return shell_quote_paths(value.get_files())
    if isinstance(value, Gio.File):
        return shell_quote_paths([value])
    if isinstance(value, str):
        return value
    return ""


def install_terminal_drop(widget: Gtk.Widget, write: Callable[[str], None], focus: Gtk.Widget | None = None) -> Gtk.DropTarget:
    """Make `widget` accept file/text drops; `write(text)` sends the text to the shell, `focus` gets the keyboard afterwards.
    Pass a Vte.Terminal's `paste_text` as `write`: VTE then wraps the text in bracketed-paste markers when the shell enabled them
    (readline/zle insert it literally, no TAB completion or premature execution) and emits `commit`, which reaches the PTY.
    The `.maestri-drop-hover` class is set while a drag hovers so the node can be highlighted."""
    target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
    target.set_gtypes([Gdk.FileList, Gio.File, GObject.TYPE_STRING])   # order = preference

    def on_drop(_t, value, _x, _y) -> bool:
        text = dropped_text(value)
        if not text:
            return False
        write(text)
        (focus or widget).grab_focus()
        return True

    target.connect("drop", on_drop)
    target.connect("enter", lambda *_: (widget.add_css_class("maestri-drop-hover"), Gdk.DragAction.COPY)[1])
    target.connect("leave", lambda *_: widget.remove_css_class("maestri-drop-hover"))
    widget.add_controller(target)
    return target
