"""
Unified-diff line classifier for the FileTree diff view (pure Python, no GTK).

`classify(diff_text)` -> [(kind, text)] with kind in:
    "file"    `diff --git a/x b/x` (start of a file section)
    "meta"    index / mode / rename / similarity / `--- a/x` / `+++ b/x` / "Binary files ..." / "\\ No newline at end of file"
    "hunk"    `@@ -1,3 +1,4 @@ ...`
    "add"     added line   (`+...` inside a hunk)
    "del"     removed line (`-...` inside a hunk)
    "context" unchanged line inside a hunk

A `+++`/`---` line is only a header outside a hunk: inside one it is an added/removed line that happens to start with
"++"/"--", so hunk line counts are tracked to tell them apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


@dataclass(frozen=True)
class DiffStat:
    files: int = 0
    added: int = 0
    removed: int = 0

    def __str__(self) -> str:
        if not self.files:
            return "no changes"
        return f"{self.files} file{'s' if self.files != 1 else ''}, +{self.added} −{self.removed}"


def classify(diff_text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    old_left = new_left = 0     # lines still expected in the current hunk
    for line in diff_text.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                new_left -= 1
                out.append(("add", line))
                continue
            if line.startswith("-"):
                old_left -= 1
                out.append(("del", line))
                continue
            if line.startswith("\\"):
                out.append(("meta", line))
                continue
            if line.startswith(" ") or line == "":
                old_left -= 1
                new_left -= 1
                out.append(("context", line))
                continue
            old_left = new_left = 0   # malformed hunk: fall through to header parsing
        m = _HUNK.match(line)
        if m:
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_left = int(m.group(2)) if m.group(2) is not None else 1
            out.append(("hunk", line))
        elif line.startswith("diff --git ") or line.startswith("diff --cc "):
            out.append(("file", line))
        else:
            out.append(("meta", line))
    return out


def stat(lines: List[Tuple[str, str]]) -> DiffStat:
    files = sum(1 for k, _ in lines if k == "file")
    return DiffStat(files, sum(1 for k, _ in lines if k == "add"), sum(1 for k, _ in lines if k == "del"))
