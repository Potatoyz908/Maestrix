"""Markdown formatting actions of the note context toolbar (pure)."""
import pytest

from maestri_canvas.markdown_format import ACTIONS, Edit, format_edit, line_prefix, wrap


def run(action, text, start, end=None):
    e = format_edit(action, text, start, start if end is None else end)
    new = e.apply(text)
    return new, new[e.sel_start:e.sel_end], (e.sel_start, e.sel_end)


def test_wrap_selection_and_keep_it_selected():
    assert run("bold", "say hello now", 4, 9)[:2] == ("say **hello** now", "hello")
    assert run("italic", "say hello now", 4, 9)[:2] == ("say *hello* now", "hello")
    assert run("strike", "x", 0, 1)[:2] == ("~~x~~", "x")
    assert run("code", "a b", 2, 3)[:2] == ("a `b`", "b")


def test_empty_selection_inserts_pair_with_caret_inside():
    new, _, sel = run("bold", "ab", 1)
    assert new == "a****b" and sel == (3, 3)


def test_wrap_toggles_off_inside_or_including_markers():
    assert run("bold", "say **hello** now", 6, 11)[:2] == ("say hello now", "hello")
    assert run("bold", "say **hello** now", 4, 13)[:2] == ("say hello now", "hello")


def test_italic_inside_bold_is_added_not_mistaken_for_toggle():
    assert run("italic", "**hi**", 2, 4)[0] == "***hi***"


def test_heading_on_caret_line_and_level_replacement():
    text = "first\nsecond line\nthird"
    new, _, sel = run("h1", text, 9)
    assert new == "first\n# second line\nthird" and sel == (11, 11)   # caret stays on the same character
    assert run("h2", new, 12)[0] == "first\n## second line\nthird"     # another level replaces, not stacks
    assert run("h1", new, 12)[0] == text                               # same level again removes it


def test_list_and_task_on_every_selected_line():
    text = "a\nb\nc"
    new, selected, _ = run("bullet", text, 0, 3)                       # touches lines a, b
    assert new == "- a\n- b\nc" and selected == "- a\n- b"
    assert run("task", new, 0, 7)[0] == "- [ ] a\n- [ ] b\nc"          # list <-> task replace each other
    assert run("bullet", new, 0, 7)[0] == text                         # all already bullets: removed


def test_selection_ending_after_newline_does_not_touch_next_line():
    assert run("bullet", "a\nb", 0, 2)[0] == "- a\nb"


def test_mixed_lines_get_the_marker_added_everywhere():
    assert run("bullet", "- a\nb", 0, 5)[0] == "- a\n- b"


def test_unicode_offsets_are_characters():
    new, selected, _ = run("bold", "ação é boa", 5, 6)
    assert new == "ação **é** boa" and selected == "é"


def test_unknown_action_and_action_list():
    assert set(ACTIONS) >= {"bold", "italic", "bullet", "h1", "h2"}
    with pytest.raises(ValueError):
        format_edit("underline", "x", 0, 1)
    assert isinstance(wrap("x", 0, 1, "*", "*"), Edit) and isinstance(line_prefix("x", 0, 0, "- "), Edit)
