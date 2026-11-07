"""Markdown formatting actions for the sticky-note toolbar. Pure: (text, sel_start, sel_end) -> Edit. No GTK.

Offsets are character offsets (Python str indices == GtkTextIter offsets). Mirrors NoteTextViewRegistry.insertWrapping /
insertLinePrefix on macOS, plus toggling: applying bold to already-bold text removes it, a heading replaces another heading
level, a list item replaces a task item (and vice versa), applying the same line marker again removes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WRAPS = {"bold": ("**", "**"), "italic": ("*", "*"), "strike": ("~~", "~~"), "code": ("`", "`")}
LINE_PREFIXES = {"h1": "# ", "h2": "## ", "h3": "### ", "bullet": "- ", "task": "- [ ] ", "quote": "> "}
ACTIONS = tuple(WRAPS) + tuple(LINE_PREFIXES)

_HEADING = re.compile(r"^#{1,6} ")
_LIST = re.compile(r"^(?:- \[[ xX]\] |[-*+] )")


@dataclass(frozen=True)
class Edit:
    """Replace text[start:end] with `insert`; afterwards select [sel_start, sel_end] (offsets in the NEW text)."""
    start: int
    end: int
    insert: str
    sel_start: int
    sel_end: int

    def apply(self, text: str) -> str:
        return text[:self.start] + self.insert + text[self.end:]


def wrap(text: str, start: int, end: int, prefix: str, suffix: str) -> Edit:
    start, end = sorted((max(0, start), min(len(text), end)))
    inner = text[start:end]
    # toggle off: the selection is surrounded by the markers, or includes them
    if text[max(0, start - len(prefix)):start] == prefix and text[end:end + len(suffix)] == suffix and \
            not (prefix == "*" and text[max(0, start - 2):start] == "**" and text[end:end + 2] == "**"):   # italic inside bold
        s = start - len(prefix)
        return Edit(s, end + len(suffix), inner, s, s + len(inner))
    if len(inner) >= len(prefix) + len(suffix) and inner.startswith(prefix) and inner.endswith(suffix) and inner:
        core = inner[len(prefix):len(inner) - len(suffix)]
        return Edit(start, end, core, start, start + len(core))
    if not inner:   # empty selection: insert the pair and put the caret between them (macOS behaviour)
        caret = start + len(prefix)
        return Edit(start, end, prefix + suffix, caret, caret)
    return Edit(start, end, prefix + inner + suffix, start + len(prefix), start + len(prefix) + len(inner))


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    first = text.rfind("\n", 0, start) + 1
    if end > start and end > 0 and text[end - 1] == "\n":
        end -= 1   # a selection ending right after a newline does not touch the next line
    nl = text.find("\n", end)
    return first, (len(text) if nl == -1 else nl)


def _marker_re(prefix: str) -> re.Pattern:
    return _HEADING if prefix.startswith("#") else _LIST if prefix.startswith("- ") else re.compile("^" + re.escape(prefix))


def line_prefix(text: str, start: int, end: int, prefix: str) -> Edit:
    """Apply `prefix` to every line touched by the selection. If every line already has exactly it, remove it; a different
    marker of the same family (another heading level, list <-> task) is replaced."""
    start, end = sorted((max(0, start), min(len(text), end)))
    a, b = _line_bounds(text, start, end)
    lines = text[a:b].split("\n")
    family = _marker_re(prefix)
    def marker(line: str) -> str:
        m = family.match(line)
        return m.group(0) if m else ""

    remove = all(marker(line) == prefix for line in lines)
    out, delta_first = [], 0
    for i, line in enumerate(lines):
        m = family.match(line)
        bare = line[m.end():] if m else line
        new = bare if remove else prefix + bare
        if i == 0:
            delta_first = len(new) - len(line)
        out.append(new)
    block = "\n".join(out)
    # keep the caret on the same character of the first line (never before the line start)
    if start == end:
        caret = max(a, start + delta_first)
        return Edit(a, b, block, caret, caret)
    return Edit(a, b, block, a, a + len(block))


def format_edit(action: str, text: str, start: int, end: int) -> Edit:
    if action in WRAPS:
        return wrap(text, start, end, *WRAPS[action])
    if action in LINE_PREFIXES:
        return line_prefix(text, start, end, LINE_PREFIXES[action])
    raise ValueError(f"unknown format action: {action!r}")
