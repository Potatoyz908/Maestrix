#!/usr/bin/env python3
"""Static site generator for the Maestrix documentation (GitHub Pages).

    pip install markdown pygments
    python3 site/build.py            # writes site/_site/
    python3 -m http.server -d site/_site 8000

Content lives in site/content/*.md with a small front matter:

    ---
    title: Page title
    section: Guide            # sidebar group
    order: 10                 # position inside the group
    description: One sentence for <meta> and the search index.
    ---
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path

import markdown
from pygments.formatters import HtmlFormatter

HERE = Path(__file__).resolve().parent
CONTENT = HERE / "content"
ASSETS = HERE / "assets"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "_site"

SITE_NAME = "Maestrix"
REPO = "https://github.com/Potatoyz908/Maestrix"
SECTION_ORDER = ["Start", "Guide", "Reference", "Project"]


def parse(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---\n"):
        head, _, body = raw[4:].partition("\n---\n")
        for line in head.splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    slug = path.stem
    md = markdown.Markdown(
        extensions=["fenced_code", "codehilite", "tables", "toc", "admonition", "attr_list", "md_in_html", "sane_lists"],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "hl", "pygments_style": "one-dark"},
            "toc": {"permalink": "#", "toc_depth": "2-3"},
        },
    )
    content = md.convert(body)
    toc = [(t["level"], t["id"], t["name"]) for t in flatten(md.toc_tokens)]
    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content))).strip()
    return {
        "slug": slug,
        "file": f"{slug}.html",
        "title": meta.get("title", slug.replace("-", " ").title()),
        "section": meta.get("section", "Guide"),
        "order": int(meta.get("order", 50)),
        "description": meta.get("description", ""),
        "layout": meta.get("layout", "doc"),
        "content": content,
        "toc": toc,
        "plain": plain,
    }


def flatten(tokens):
    for t in tokens:
        yield t
        yield from flatten(t.get("children", []))


def nav_html(pages: list[dict], current: str) -> str:
    groups: dict[str, list[dict]] = {}
    for p in pages:
        groups.setdefault(p["section"], []).append(p)
    out = []
    for section in sorted(groups, key=lambda s: SECTION_ORDER.index(s) if s in SECTION_ORDER else 99):
        out.append(f'<div class="nav-group"><div class="nav-title">{html.escape(section)}</div>')
        for p in sorted(groups[section], key=lambda p: (p["order"], p["title"])):
            cls = ' class="active"' if p["slug"] == current else ""
            out.append(f'<a{cls} href="{p["file"]}">{html.escape(p["title"])}</a>')
        out.append("</div>")
    return "\n".join(out)


def toc_html(toc) -> str:
    items = [(lvl, i, n) for lvl, i, n in toc if lvl in (2, 3)]
    if len(items) < 2:
        return ""
    rows = "".join(
        f'<a class="toc-l{lvl}" href="#{i}">{html.escape(re.sub(r"<[^>]+>", "", n))}</a>' for lvl, i, n in items
    )
    return f'<aside class="toc"><div class="toc-title">On this page</div>{rows}</aside>'


def page_html(page: dict, pages: list[dict], neighbours: tuple) -> str:
    prev_p, next_p = neighbours
    pager = ""
    if page["layout"] == "doc":
        pager = '<nav class="pager">'
        pager += f'<a class="prev" href="{prev_p["file"]}"><span>Previous</span>{html.escape(prev_p["title"])}</a>' if prev_p else "<span></span>"
        pager += f'<a class="next" href="{next_p["file"]}"><span>Next</span>{html.escape(next_p["title"])}</a>' if next_p else "<span></span>"
        pager += "</nav>"
    body_class = "landing" if page["layout"] == "landing" else "is-doc"
    desc = html.escape(page["description"] or "Maestrix documentation")
    title = SITE_NAME if page["layout"] == "landing" else f'{page["title"]} · {SITE_NAME}'
    if page["layout"] == "landing":
        main = f'<main class="landing-main">{page["content"]}</main>'
    else:
        main = (
            f'<div class="doc-shell"><main class="doc"><h1 class="doc-title">{html.escape(page["title"])}</h1>'
            f'<p class="doc-sub">{html.escape(page["description"])}</p>'
            f'<article>{page["content"]}</article>{pager}</main>{toc_html(page["toc"])}</div>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{desc}">
<meta name="theme-color" content="#0c0f1b">
<link rel="icon" href="assets/icon.png">
<link rel="stylesheet" href="assets/style.css">
</head>
<body class="{body_class}">
<header class="topbar">
  <button class="menu-btn" aria-label="Toggle navigation" id="menu-btn">☰</button>
  <a class="brand" href="index.html"><img src="assets/icon.png" alt="" width="28" height="28"><span>Maestrix</span></a>
  <div class="search"><input id="search" type="search" placeholder="Search the docs…" autocomplete="off" aria-label="Search"><div id="results" class="results" hidden></div></div>
  <nav class="top-links"><a href="getting-started.html">Docs</a><a href="cli-reference.html">CLI</a><a href="{REPO}">GitHub</a></nav>
  <a class="gh-stars" href="{REPO}" aria-label="Star Maestrix on GitHub"><span class="star">★</span> Star <b id="gh-stars">–</b></a>
</header>
<div class="layout">
<aside class="sidebar" id="sidebar">{nav_html(pages, page["slug"])}</aside>
{main}
</div>
<footer class="footer">Maestrix is a derivative of OpenMaestri, distributed under the GPL-3.0-or-later. <a href="{REPO}">Source on GitHub</a>.
<div class="counters"><a href="{REPO}/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Potatoyz908/Maestrix?style=flat-square&label=stars&color=168bf1&labelColor=151c2e"></a> <img alt="Visits" src="https://hits.sh/potatoyz908.github.io/Maestrix.svg?style=flat-square&label=visits&color=9354e8&labelColor=151c2e"></div></footer>
<script src="assets/site.js"></script>
</body>
</html>
"""


def main() -> None:
    pages = [parse(p) for p in sorted(CONTENT.glob("*.md"))]
    docs = [p for p in pages if p["layout"] == "doc"]
    docs_sorted = sorted(
        docs,
        key=lambda p: (SECTION_ORDER.index(p["section"]) if p["section"] in SECTION_ORDER else 99, p["order"], p["title"]),
    )
    nav_pages = [p for p in pages if p["layout"] == "doc"]
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    for f in ASSETS.rglob("*"):
        if f.is_file():
            dest = OUT / "assets" / f.relative_to(ASSETS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    css = (ASSETS / "style.css").read_text(encoding="utf-8")
    css += "\n/* pygments */\n" + HtmlFormatter(style="one-dark", cssclass="hl").get_style_defs(".hl")
    (OUT / "assets" / "style.css").write_text(css, encoding="utf-8")
    index = []
    for page in pages:
        pos = docs_sorted.index(page) if page in docs_sorted else -1
        prev_p = docs_sorted[pos - 1] if pos > 0 else None
        next_p = docs_sorted[pos + 1] if 0 <= pos < len(docs_sorted) - 1 else None
        (OUT / page["file"]).write_text(page_html(page, nav_pages, (prev_p, next_p)), encoding="utf-8")
        index.append({
            "t": page["title"], "u": page["file"], "d": page["description"],
            "h": [n for _, _, n in page["toc"]], "x": page["plain"][:1800],
        })
    (OUT / "search-index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built {len(pages)} pages -> {OUT}")


if __name__ == "__main__":
    main()
