"""Regression: the New Floor dialog froze its branch after two typed characters.

GTK4's Entry.set_text() emits "changed" twice (delete, then insert). The empty intermediate state was read as a
user edit, so floor/<slug> stopped following the name ("floor/la" for "layer-one") and a second floor with a similar
name collided with the first branch, so several floors could not be created.

Needs a display: run under Xvfb (DISPLAY=:99). The controller is a stub because the dialog only calls
list_branches() on construction; the unit under test is the dialog's own form logic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "canvas-cairo", REPO / "gtk4-ui" / "src", REPO):
    sys.path.insert(0, str(p))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs a display (run under Xvfb)")


def _dialog():
    from ui.floors import NewFloorDialog
    controller = MagicMock()
    controller.list_branches = lambda cb: cb(["main"])
    return NewFloorDialog(Gtk.Window(), controller)


def _type_slowly(dialog, text: str) -> None:
    """One character at a time through Editable.insert_text, like a keyboard.

    Entry.set_text(prefix) per character does NOT reproduce the bug (the old code also passes it); only real
    incremental inserts do: the old code ends with branch 'floor/la' and _branch_edited True.
    """
    for ch in text:
        dialog.name_entry.insert_text(ch, len(dialog.name_entry.get_text()))


def test_branch_follows_every_typed_character() -> None:
    dialog = _dialog()
    _type_slowly(dialog, "layer-one")
    assert dialog.branch_entry.get_text() == "floor/layer-one"
    assert dialog._branch_edited is False


def test_second_floor_with_a_similar_name_gets_its_own_branch() -> None:
    first, second = _dialog(), _dialog()
    _type_slowly(first, "layer-one")
    _type_slowly(second, "layer-two")
    assert first.branch_entry.get_text() != second.branch_entry.get_text()
    assert second.branch_entry.get_text() == "floor/layer-two"


def test_a_real_manual_edit_of_the_branch_still_sticks() -> None:
    dialog = _dialog()
    _type_slowly(dialog, "layer-one")
    dialog.branch_entry.set_text("feature/mine")
    _type_slowly(dialog, "layer-one-renamed")
    assert dialog.branch_entry.get_text() == "feature/mine"
    assert dialog._branch_edited is True
