"""Markdown -> styled runs for a Gtk.TextBuffer (pure, no GTK). The widget maps each tag name to a Gtk.TextTag.

Block tags : h1 h2 h3 (h4-h6 -> h3), quote, code-block, li + indent0..4, hr, table
Inline tags: bold italic strike code link
Every block ends with "\\n". Links keep their target only for http/https/mailto (anything else, e.g. javascript:, renders as plain text).
Images become a run with `image=<src>` (the widget decides how to load it). Soft line breaks are kept: a note is shown as typed."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ALLOWED_LINK = re.compile(r"^(https?://|mailto:)", re.I)


@dataclass(frozen=True, slots=True)
class Run:
    text: str
    tags: tuple[str, ...] = ()
    link: str | None = None
    image: str | None = None


_INLINE = re.compile(
    r"(?P<esc>\\[\\`*_{}\[\]()#+\-.!~>|])"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<img>!\[(?P<alt>[^\]]*)\]\((?P<isrc><[^>\n]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<link>\[(?P<label>[^\]]+)\]\((?P<lurl><[^>\n]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<bold>\*\*(?=\S)(?P<b1>.+?)(?<=\S)\*\*|(?<![\w_])__(?=\S)(?P<b2>.+?)(?<=\S)__(?![\w_]))"
    r"|(?P<strike>~~(?=\S)(?P<s1>.+?)(?<=\S)~~)"
    r"|(?P<ital>(?<![\w*])\*(?=[^\s*])(?P<i1>[^*\n]+?)(?<=[^\s*])\*(?![\w*])|(?<![\w_])_(?=[^\s_])(?P<i2>[^_\n]+?)(?<=[^\s_])_(?![\w_]))"
    r"|(?P<burl>https?://[^\s<>)\]]+)"
)
_TRAIL = ".,;:!?'\""


def _dest(d: str) -> str:
    return d[1:-1] if d.startswith("<") and d.endswith(">") else d


def _inline(text: str, tags: tuple[str, ...], link: str | None = None) -> list[Run]:
    out: list[Run] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(Run(text[pos:m.start()], tags, link))
        kind = m.lastgroup
        # lastgroup is the last *named group that matched*; map nested groups back to the construct
        for k in ("esc", "code", "img", "link", "bold", "strike", "ital", "burl"):
            if m.group(k) is not None:
                kind = k
                break
        if kind == "esc":
            out.append(Run(m.group("esc")[1], tags, link))
        elif kind == "code":
            out.append(Run(m.group("code")[1:-1], tags + ("code",), link))
        elif kind == "img":
            out.append(Run(m.group("alt") or "image", tags, link, image=_dest(m.group("isrc"))))
        elif kind == "link":
            url = _dest(m.group("lurl"))
            ok = bool(ALLOWED_LINK.match(url))
            out.extend(_inline(m.group("label"), tags + (("link",) if ok else ()), url if ok else link))
        elif kind == "bold":
            out.extend(_inline(m.group("b1") or m.group("b2"), tags + ("bold",), link))
        elif kind == "strike":
            out.extend(_inline(m.group("s1"), tags + ("strike",), link))
        elif kind == "ital":
            out.extend(_inline(m.group("i1") or m.group("i2"), tags + ("italic",), link))
        elif kind == "burl":
            raw = m.group("burl")
            url = raw.rstrip(_TRAIL)
            out.append(Run(url, tags + ("link",), url))
            if len(url) < len(raw):
                out.append(Run(raw[len(url):], tags, link))
        pos = m.end()
    if pos < len(text):
        out.append(Run(text[pos:], tags, link))
    return out


def plain(text: str) -> str:
    """Text with inline markers resolved (used for table cells, which are aligned in monospace)."""
    return "".join(r.text for r in _inline(text, ()))


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * (w - _width(s))


_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_LI = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TASK = re.compile(r"^\[( |x|X)\]\s+(.*)$")
_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _table(rows: list[str]) -> list[Run]:
    header, body = _cells(rows[0]), [_cells(r) for r in rows[2:]]
    n = len(header)
    grid = [[plain(c) for c in header]] + [[plain(c) for c in (r + [""] * n)[:n]] for r in body]
    widths = [max(_width(row[i]) for row in grid) for i in range(n)]
    def fmt(row: list[str]) -> str:
        return "│ " + " │ ".join(_pad(c, widths[i]) for i, c in enumerate(row)) + " │\n"
    out = [Run(fmt(grid[0]), ("table", "bold")),
           Run("├─" + "─┼─".join("─" * w for w in widths) + "─┤\n", ("table",))]
    out += [Run(fmt(r), ("table",)) for r in grid[1:]]
    return out


def parse_markdown(src: str) -> list[Run]:
    lines = src.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[Run] = []
    i, blank = 0, True   # `blank`: the previous output was a blank separator (collapse repeated blank lines)
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            if not blank and out:
                out.append(Run("\n", ("blank",)))
            blank = True
            i += 1
            continue
        blank = False
        if line.lstrip().startswith("```"):
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                out.append(Run(lines[i] + "\n", ("code-block",)))
                i += 1
            i += 1   # closing fence (or EOF)
            continue
        m = _HEAD.match(line)
        if m:
            out.extend(_inline(m.group(2), (f"h{min(len(m.group(1)), 3)}",)))
            out.append(Run("\n", (f"h{min(len(m.group(1)), 3)}",)))
            i += 1
            continue
        if _HR.match(line):
            out.append(Run("─" * 24 + "\n", ("hr",)))
            i += 1
            continue
        if line.lstrip().startswith(">"):
            body = re.sub(r"^\s*>\s?", "", line)
            out.extend(_inline(body, ("quote",)))
            out.append(Run("\n", ("quote",)))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            out.extend(_table(lines[i:j]))
            i = j
            continue
        m = _LI.match(line)
        if m:
            level = min(len(m.group(1).replace("\t", "    ")) // 2, 4)
            marker, rest = m.group(2), m.group(3)
            t = _TASK.match(rest)
            if t:
                prefix, rest = ("☑ " if t.group(1) in "xX" else "☐ "), t.group(2)
            elif marker[0].isdigit():
                prefix = marker + " "
            else:
                prefix = "• " if level % 2 == 0 else "◦ "
            tags = ("li", f"indent{level}")
            out.append(Run(prefix, tags))
            out.extend(_inline(rest, tags))
            out.append(Run("\n", tags))
            i += 1
            continue
        out.extend(_inline(line, ()))
        out.append(Run("\n"))
        i += 1
    while out and out[-1].tags == ("blank",):   # a trailing newline in the source is not an extra empty line
        out.pop()
    return out


def runs_text(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)
