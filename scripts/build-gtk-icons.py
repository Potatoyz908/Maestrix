#!/usr/bin/env python3
"""Build the GTK4 UI's monochrome outline icon set (gtk4-ui/src/ui/icons/hicolor/scalable/actions/maestri-*-symbolic.svg).

Each glyph is authored here as 16x16 stroked outlines (1.4 px, round caps/joins). GTK recolors a symbolic icon by forcing
`fill` on its shapes, so stroked outlines would render as solid blobs: Inkscape converts every stroke to a filled path first.

    python3 scripts/build-gtk-icons.py          # needs inkscape >= 1.0 on PATH
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

OUT = os.path.join(os.path.dirname(__file__), "..", "gtk4-ui", "src", "ui", "icons", "hicolor", "scalable", "actions")

# name -> SVG body (stroked; converted to fills by Inkscape). Coordinates on a 16x16 grid.
GLYPHS: dict[str, str] = {
    "pointer": '<path d="M3.5 2.25 V12.75 L6.4 10 L8.3 14 L10.1 13.2 L8.25 9.3 L12 9.1 Z"/>',
    "terminal": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M4.75 6.25 L6.75 8.1 L4.75 10"/>'
                '<path d="M8.5 10 H11.25"/>',
    "note": '<path d="M3.25 2.25 H12.75 Q13.75 2.25 13.75 3.25 V9.75 L9.75 13.75 H3.25 Q2.25 13.75 2.25 12.75 V3.25 '
            'Q2.25 2.25 3.25 2.25 Z"/><path d="M13.75 9.75 H10.75 Q9.75 9.75 9.75 10.75 V13.75"/>',
    "paperclip": '<path d="M13.25 7.4 L8.2 12.45 A3.05 3.05 0 0 1 3.9 8.15 L9.05 3 A2 2 0 0 1 11.9 5.85 L6.9 10.85 '
                 'A0.98 0.98 0 0 1 5.5 9.45 L9.95 5"/>',
    "file-tree": '<path d="M3.25 2 V11.25 Q3.25 12.25 4.25 12.25 H6.5"/><path d="M3.25 5.5 H6.5"/>'
                 '<rect x="6.75" y="3.75" width="7" height="3.5" rx="1"/><rect x="6.75" y="10.5" width="7" height="3.5" rx="1"/>',
    "globe": '<circle cx="8" cy="8" r="6.25"/><path d="M8 1.75 C5.1 5.2 5.1 10.8 8 14.25 C10.9 10.8 10.9 5.2 8 1.75 Z"/>'
             '<path d="M1.75 8 H14.25"/>',
    "text": '<path d="M3 4 V2.75 H13 V4"/><path d="M8 2.75 V13.25"/><path d="M6 13.25 H10"/>',
    "pencil": '<path d="M10.6 2.4 Q11.35 1.65 12.1 2.4 L13.6 3.9 Q14.35 4.65 13.6 5.4 L5.6 13.4 L2.25 13.75 L2.6 10.4 Z"/>'
              '<path d="M9.25 3.75 L12.25 6.75"/>',
    "pencil-line": '<path d="M10.6 2.4 Q11.35 1.65 12.1 2.4 L13.6 3.9 Q14.35 4.65 13.6 5.4 L6.25 12.75 L2.75 13.25 '
                   'L3.25 9.75 Z"/><path d="M9.25 3.75 L12.25 6.75"/><path d="M9 14 H14"/>',
    "highlighter": '<path d="M10.5 2 L14 5.5 L8.25 11.25 L4.75 7.75 Z"/><path d="M4.75 7.75 L3.25 11.25 L4.75 12.75 '
                   'L8.25 11.25"/><path d="M3.25 12.75 L2 14"/>',
    "square": '<rect x="2.5" y="3" width="11" height="10" rx="1.5"/>',
    "circle": '<circle cx="8" cy="8" r="5.75"/>',
    "diamond": '<path d="M8 1.75 L14.25 8 L8 14.25 L1.75 8 Z"/>',
    "shapes": '<rect x="1.75" y="8" width="6.25" height="6.25" rx="1"/><circle cx="11" cy="5" r="3.25"/>',
    "link": '<path d="M6.75 9.25 A3 3 0 0 0 11 9.25 L12.85 7.4 A3 3 0 0 0 8.6 3.15 L7.75 4"/>'
            '<path d="M9.25 6.75 A3 3 0 0 0 5 6.75 L3.15 8.6 A3 3 0 0 0 7.4 12.85 L8.25 12"/>',
    "copy": '<rect x="5.5" y="5.5" width="8.5" height="8.5" rx="1.5"/><path d="M2.25 10.5 V3.75 Q2.25 2.25 3.75 2.25 H10.5"/>',
    "duplicate": '<rect x="5.5" y="5.5" width="8.5" height="8.5" rx="1.5"/><path d="M2.25 10.5 V3.75 Q2.25 2.25 3.75 2.25 H10.5"/>'
                 '<path d="M9.75 7.75 V11.75 M7.75 9.75 H11.75"/>',
    "paste": '<rect x="5.5" y="1.5" width="5" height="2.75" rx="0.75"/><path d="M10.5 2.75 H11.75 Q13 2.75 13 4 V13 '
             'Q13 14.25 11.75 14.25 H4.25 Q3 14.25 3 13 V4 Q3 2.75 4.25 2.75 H5.5"/>',
    "select-all": '<path d="M2.25 4.5 V3.5 Q2.25 2.25 3.5 2.25 H4.5 M7 2.25 H9 M11.5 2.25 H12.5 Q13.75 2.25 13.75 3.5 V4.5 '
                  'M13.75 7 V9 M13.75 11.5 V12.5 Q13.75 13.75 12.5 13.75 H11.5 M9 13.75 H7 M4.5 13.75 H3.5 Q2.25 13.75 '
                  '2.25 12.5 V11.5 M2.25 9 V7"/><rect x="5.5" y="5.5" width="5" height="5" rx="0.75"/>',
    "trash": '<path d="M2.5 4 H13.5"/><path d="M6 4 V2.75 Q6 2 6.75 2 H9.25 Q10 2 10 2.75 V4"/>'
             '<path d="M3.75 4 L4.45 13.1 Q4.55 14 5.45 14 H10.55 Q11.45 14 11.55 13.1 L12.25 4"/>'
             '<path d="M6.5 6.75 V11.25 M9.5 6.75 V11.25"/>',
    "layers": '<path d="M8 1.75 L14.25 5 L8 8.25 L1.75 5 Z"/><path d="M1.75 8 L8 11.25 L14.25 8"/>'
              '<path d="M1.75 11 L8 14.25 L14.25 11"/>',
    "map": '<path d="M1.75 3.75 L5.75 2 L10.25 4 L14.25 2.25 V12.25 L10.25 14 L5.75 12 L1.75 13.75 Z"/>'
           '<path d="M5.75 2 V12 M10.25 4 V14"/>',
    "minus": '<path d="M3 8 H13"/>',
    "plus": '<path d="M8 3 V13 M3 8 H13"/>',
    "book-open": '<path d="M8 4.25 C6.6 2.9 4.6 2.6 1.75 2.6 V12.4 C4.6 12.4 6.6 12.7 8 14 C9.4 12.7 11.4 12.4 14.25 12.4 '
                 'V2.6 C11.4 2.6 9.4 2.9 8 4.25 Z"/><path d="M8 4.25 V14"/>',
    "chevron-right": '<path d="M6 3.5 L10.5 8 L6 12.5"/>',
    "check": '<path d="M3 8.5 L6.5 12 L13 4.5"/>',
    "sliders": '<path d="M2 4.5 H8.5 M12.5 4.5 H14 M2 11.5 H3.5 M7.5 11.5 H14"/><circle cx="10.5" cy="4.5" r="1.75"/>'
               '<circle cx="5.5" cy="11.5" r="1.75"/>',
    "toggles": '<path d="M9 4.5 H14 M2 11.5 H7"/><circle cx="5.25" cy="4.5" r="2.25"/><circle cx="10.75" cy="11.5" r="2.25"/>',
    "eye": '<path d="M1.75 8 C3.25 5 5.4 3.6 8 3.6 C10.6 3.6 12.75 5 14.25 8 C12.75 11 10.6 12.4 8 12.4 C5.4 12.4 '
           '3.25 11 1.75 8 Z"/><circle cx="8" cy="8" r="2"/>',
    "eye-off": '<path d="M6.3 3.85 Q7.1 3.6 8 3.6 C10.6 3.6 12.75 5 14.25 8 Q13.6 9.3 12.75 10.25 M10.5 11.85 Q9.3 12.4 8 '
               '12.4 C5.4 12.4 3.25 11 1.75 8 Q2.6 6.3 3.9 5.2"/><path d="M6.6 6.6 A2 2 0 0 0 9.4 9.4"/>'
               '<path d="M2 2 L14 14"/>',
    "reload": '<path d="M13.25 8 A5.25 5.25 0 1 1 11.5 4.1"/><path d="M12.25 1.75 V4.6 H9.4"/>',
    "moon": '<path d="M13.5 9.6 A5.75 5.75 0 1 1 6.4 2.5 A4.5 4.5 0 0 0 13.5 9.6 Z"/>',
    "lock": '<rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5.25 7 V5 A2.75 2.75 0 0 1 10.75 5 V7"/>',
    "lock-open": '<rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5.25 7 V5 A2.75 2.75 0 0 1 10.6 4.1"/>',
    "send": '<path d="M14.25 1.75 L9.75 14.25 L7.1 8.9 L1.75 6.25 Z"/><path d="M14.25 1.75 L7.1 8.9"/>',
    "folder": '<path d="M1.75 4 Q1.75 2.75 3 2.75 H6 L7.5 4.5 H13 Q14.25 4.5 14.25 5.75 V12 Q14.25 13.25 13 13.25 H3 '
              'Q1.75 13.25 1.75 12 Z"/>',
    "list": '<path d="M6 4 H14 M6 8 H14 M6 12 H14"/><path d="M2.5 4 H2.6 M2.5 8 H2.6 M2.5 12 H2.6"/>',
    "list-checks": '<path d="M1.75 4.25 L3 5.5 L5.25 3"/><path d="M1.75 11.25 L3 12.5 L5.25 10"/>'
                   '<path d="M8 4.25 H14 M8 11.25 H14"/>',
    "settings": '<circle cx="8" cy="8" r="2.1"/><path d="M6.85 1.75 H9.15 L9.5 3.5 L10.85 4.3 L12.55 3.7 L13.7 5.7 '
                'L12.35 6.85 V9.15 L13.7 10.3 L12.55 12.3 L10.85 11.7 L9.5 12.5 L9.15 14.25 H6.85 L6.5 12.5 L5.15 11.7 '
                'L3.45 12.3 L2.3 10.3 L3.65 9.15 V6.85 L2.3 5.7 L3.45 3.7 L5.15 4.3 L6.5 3.5 Z"/>',
    "sidebar": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M6 2.75 V13.25"/>',
    "play": '<path d="M4.5 2.75 L12.75 8 L4.5 13.25 Z"/>',
    "stop": '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/>',
    "close": '<path d="M4 4 L12 12 M12 4 L4 12"/>',
    # bottom capsules (Sprint 8 D5): floor = macOS "square.stack.3d.up", land = airplane, map viewport, zoom +/-
    "floor": '<path d="M8 1.75 L14 4.75 L8 7.75 L2 4.75 Z"/><path d="M2 8 L8 11 L14 8"/><path d="M2 11.25 L8 14.25 L14 11.25"/>',
    "floor-new": '<path d="M7 2 L12.5 4.75 L7 7.5 L1.5 4.75 Z"/><path d="M1.5 8 L7 10.75 L9 9.75"/>'
                 '<path d="M1.5 11.25 L7 14 L8.5 13.25"/><path d="M12.25 9 V14.5 M9.5 11.75 H15"/>',
    "floor-land": '<path d="M2 14 H14"/><path d="M2.5 10.25 L13.25 6.4 Q14.6 6 14.3 7.1 Q14.05 7.9 12.9 8.3 L4.9 11.15 '
                  'L2.5 10.25 Z"/><path d="M6.4 8.6 L4.8 4.25 L6.15 3.8 L9.15 7.6"/>',
    "map-viewport": '<path d="M1.75 3.75 L5.75 2 L10.25 4 L14.25 2.25 V12.25 L10.25 14 L5.75 12 L1.75 13.75 Z"/>'
                    '<rect x="6.25" y="6" width="4" height="3.5" rx="0.6"/>',
    "zoom-in": '<path d="M8 3.5 V12.5 M3.5 8 H12.5"/>',
    "zoom-out": '<path d="M3.5 8 H12.5"/>',
}

TEMPLATE = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            '<g fill="none" stroke="#2e3436" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>')


def main() -> int:
    if shutil.which("inkscape") is None:
        print("inkscape not found", file=sys.stderr)
        return 1
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in GLYPHS.items():
            src = os.path.join(tmp, f"{name}.svg")
            dst = os.path.join(OUT, f"maestri-{name}-symbolic.svg")
            with open(src, "w") as fh:
                fh.write(TEMPLATE.format(body=body))
            subprocess.run(["inkscape", src, "--actions=select-all:all;object-stroke-to-path;select-all:all;object-to-path",
                            "--export-plain-svg", f"--export-filename={dst}"], check=True, capture_output=True)
            with open(dst) as fh:
                svg = fh.read()
            # keep it tiny and deterministic: no metadata, one fill colour GTK will replace
            svg = re.sub(r"<metadata.*?</metadata>|<defs[^>]*/>|<defs.*?</defs>", "", svg, flags=re.S)
            svg = re.sub(r'\s(id|inkscape:[a-z-]+|sodipodi:[a-z-]+)="[^"]*"', "", svg)
            with open(dst, "w") as fh:
                fh.write(svg)
            print("built", os.path.relpath(dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
