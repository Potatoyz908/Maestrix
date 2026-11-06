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

# ---- Sprint 8 E2 (emoji -> icons in dialogs, toolbars, status, git, presets and the workspace icon picker) ----
GLYPHS.update({
    "warning": '<path d="M8 2 L14.5 13.5 H1.5 Z"/><path d="M8 6.5 V9.25 M8 11.4 V11.5"/>',
    "timer": '<circle cx="8" cy="9" r="5.25"/><path d="M8 9 V6.5 M6.5 1.75 H9.5 M12.25 4.5 L13.25 3.5"/>',
    "git-branch": '<circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/><circle cx="11.5" cy="5" r="1.6"/>'
                  '<path d="M4.5 5.1 V10.9 M11.5 6.6 Q11.5 9.5 6 10.6"/>',
    "arrow-down": '<path d="M8 2.5 V13 M3.75 8.75 L8 13 L12.25 8.75"/>',
    "arrow-up": '<path d="M8 13.5 V3 M3.75 7.25 L8 3 L12.25 7.25"/>',
    "archive": '<rect x="1.75" y="2.5" width="12.5" height="3.5" rx="1"/><path d="M2.75 6 V12.5 Q2.75 13.5 3.75 13.5 H12.25 '
               'Q13.25 13.5 13.25 12.5 V6 M6.5 8.75 H9.5"/>',
    "archive-out": '<rect x="1.75" y="2.5" width="12.5" height="3.5" rx="1"/><path d="M2.75 6 V12.5 Q2.75 13.5 3.75 13.5 '
                   'H5 M11 13.5 H12.25 Q13.25 13.5 13.25 12.5 V6 M8 14 V8.25 M5.75 10.25 L8 8 L10.25 10.25"/>',
    "file": '<path d="M9.25 1.75 H4.25 Q3 1.75 3 3 V13 Q3 14.25 4.25 14.25 H11.75 Q13 14.25 13 13 V5.5 Z"/>'
            '<path d="M9.25 1.75 V5.5 H13"/>',
    "file-text": '<path d="M9.25 1.75 H4.25 Q3 1.75 3 3 V13 Q3 14.25 4.25 14.25 H11.75 Q13 14.25 13 13 V5.5 Z"/>'
                 '<path d="M9.25 1.75 V5.5 H13 M5.75 8.5 H10.25 M5.75 11 H10.25"/>',
    "folder-open": '<path d="M1.75 12 V4 Q1.75 2.75 3 2.75 H6 L7.5 4.5 H12 Q13.25 4.5 13.25 5.75 V7"/>'
                   '<path d="M1.75 12 L3.6 7.9 Q3.9 7.25 4.6 7.25 H14 Q14.6 7.25 14.35 7.85 L12.6 12.4 Q12.3 13.25 11.4 '
                   '13.25 H3 Q1.75 13.25 1.75 12 Z"/>',
    "asterisk": '<path d="M8 2 V14 M2.8 5 L13.2 11 M2.8 11 L13.2 5"/>',
    "sparkle": '<path d="M8 1.75 Q8.6 7.4 14.25 8 Q8.6 8.6 8 14.25 Q7.4 8.6 1.75 8 Q7.4 7.4 8 1.75 Z"/>',
    "sparkles": '<path d="M6.5 2.5 Q7 7 11.5 7.5 Q7 8 6.5 12.5 Q6 8 1.5 7.5 Q6 7 6.5 2.5 Z"/>'
                '<path d="M12.5 1.5 V4.5 M11 3 H14 M12.5 11 V14 M11 12.5 H14"/>',
    "brain": '<path d="M8 3.25 Q6.9 1.75 5.25 2.25 Q3.75 2.75 3.75 4.25 Q2 4.75 2 6.75 Q2 8 2.9 8.6 Q2.25 10.5 3.75 11.5 '
             'Q4 13.75 6.25 13.75 Q7.4 13.75 8 12.75 Q8.6 13.75 9.75 13.75 Q12 13.75 12.25 11.5 Q13.75 10.5 13.1 8.6 '
             'Q14 8 14 6.75 Q14 4.75 12.25 4.25 Q12.25 2.75 10.75 2.25 Q9.1 1.75 8 3.25 Z"/><path d="M8 3.25 V12.75"/>'
             '<path d="M5.25 6.25 Q6.5 6.5 8 6 M10.75 9.75 Q9.5 9.5 8 10"/>',
    "cpu": '<rect x="3.75" y="3.75" width="8.5" height="8.5" rx="1.25"/><rect x="6.25" y="6.25" width="3.5" height="3.5" '
           'rx="0.5"/><path d="M6 1.5 V3.75 M10 1.5 V3.75 M6 12.25 V14.5 M10 12.25 V14.5 M1.5 6 H3.75 M1.5 10 H3.75 '
           'M12.25 6 H14.5 M12.25 10 H14.5"/>',
    "desktop": '<rect x="1.75" y="2.25" width="12.5" height="8.75" rx="1.25"/><path d="M8 11 V13.75 M5 13.75 H11"/>',
    "laptop": '<rect x="3" y="3" width="10" height="7.25" rx="1"/><path d="M1.5 12.75 H14.5 L13.5 10.25 H2.5 Z"/>',
    "network": '<rect x="6" y="1.75" width="4" height="3.5" rx="0.75"/><rect x="1.75" y="10.75" width="4" height="3.5" '
               'rx="0.75"/><rect x="10.25" y="10.75" width="4" height="3.5" rx="0.75"/><path d="M8 5.25 V8 M3.75 10.75 '
               'V8 H12.25 V10.75"/>',
    "server": '<rect x="2" y="2" width="12" height="5" rx="1.25"/><rect x="2" y="9" width="12" height="5" rx="1.25"/>'
              '<path d="M4.75 4.5 H4.85 M4.75 11.5 H4.85"/>',
    "star": '<path d="M8 1.75 L9.85 5.5 L14 6.1 L11 9 L11.7 13.1 L8 11.15 L4.3 13.1 L5 9 L2 6.1 L6.15 5.5 Z"/>',
    "masks": '<path d="M2 2.75 Q5 4 8 2.75 V7.5 Q8 11 5 11.75 Q2 11 2 7.5 Z"/><path d="M8 5.5 Q11 6.75 14 5.5 V10 Q14 '
             '13.5 11 14.25 Q9.4 13.85 8.6 12.75"/><path d="M3.75 6 H4.25 M5.75 6 H6.25 M3.9 8.75 Q5 9.75 6.1 8.75"/>',
    "chat": '<path d="M2 3.75 Q2 2.5 3.25 2.5 H9.75 Q11 2.5 11 3.75 V7.5 Q11 8.75 9.75 8.75 H5.75 L3.25 10.75 V8.75 Q2 '
            '8.75 2 7.5 Z"/><path d="M11 5.75 H12.75 Q14 5.75 14 7 V10.75 Q14 12 12.75 12 V13.75 L10.5 12 H7.75 Q6.5 12 '
            '6.5 10.75 V10.5"/>',
    "list-box": '<rect x="1.75" y="2.25" width="12.5" height="11.5" rx="1.75"/><path d="M7 5.5 H11.5 M7 8 H11.5 M7 10.5 '
                'H11.5 M4.5 5.5 H4.6 M4.5 8 H4.6 M4.5 10.5 H4.6"/>',
    "hammer": '<path d="M9.75 6.25 L3.25 12.75 Q2.5 13.5 3.25 14.25 Q4 15 4.75 14.25 L11.25 7.75"/><path d="M7 3.5 L10 '
              '1.75 L14.25 6 L12.5 7.75 L11.5 6.75 L10.25 8 L8 5.75 L9.25 4.5 Z"/>',
    "wrench": '<path d="M10 2 Q12.5 1.5 13.75 3.25 L11.5 5.5 L12.1 6.9 L13.5 7.5 L15 5.25 Q15 8.5 11.25 8.75 L4.75 '
              '14.25 Q3.75 15 2.75 14 Q1.75 13 2.5 12 L8 5.75 Q7.5 2.5 10 2 Z"/>',
    "bolt": '<path d="M9 1.5 L3 9 H7.5 L7 14.5 L13 7 H8.5 Z"/>',
    "squares": '<rect x="5.5" y="5.5" width="8.5" height="8.5" rx="1.5"/><rect x="2" y="2" width="8.5" height="8.5" '
               'rx="1.5"/>',
    "photo": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.75"/><circle cx="5.5" cy="6.25" r="1.25"/>'
             '<path d="M14.25 10.5 L10.75 7 L3.5 13.25"/>',
    "brush": '<path d="M13.75 2.25 Q12.5 2 7.5 7.5 L8.5 8.5 Q14 3.5 13.75 2.25 Z"/><path d="M6.5 8.75 Q4.5 8.75 4 10.75 '
             'Q3.75 12.75 2 13.5 Q5.5 14.75 7.25 13 Q8.25 12 7.6 9.9"/>',
    "box": '<path d="M8 1.75 L14 4.75 V11.25 L8 14.25 L2 11.25 V4.75 Z"/><path d="M2 4.75 L8 7.75 L14 4.75 M8 7.75 V14.25 '
           'M5 3.25 L11 6.25"/>',
    "cube": '<path d="M8 1.75 L14 4.75 V11.25 L8 14.25 L2 11.25 V4.75 Z"/><path d="M2 4.75 L8 7.75 L14 4.75 M8 7.75 '
            'V14.25"/>',
    "smile": '<circle cx="8" cy="8" r="6.25"/><path d="M5.5 9.75 Q8 12 10.5 9.75 M6 6.25 H6.1 M10 6.25 H10.1"/>',
    "stroke-solid": '<path d="M2 8 H14"/>',
    "stroke-dashed": '<path d="M2 8 H4.5 M6.75 8 H9.25 M11.5 8 H14"/>',
    "stroke-dotted": '<path d="M2.25 8 H2.35 M5.05 8 H5.15 M7.95 8 H8.05 M10.85 8 H10.95 M13.65 8 H13.75"/>',
    "fill-solid": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5" fill="#2e3436" stroke="none"/>',
    "fill-none": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>',
    "fill-hatched": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M2.75 7 L7 2.75 M2.75 11.5 L11.5 '
                    '2.75 M6.25 13.25 L13.25 6.25"/>',
    "fill-crosshatched": '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M2.75 7 L7 2.75 M2.75 11.5 '
                         'L11.5 2.75 M6.25 13.25 L13.25 6.25 M9 2.75 L13.25 7 M4.5 2.75 L13.25 11.5 M2.75 6.25 L9.75 '
                         '13.25"/>',
})

# ---- Sprint 9 (Arquiteto): terminal bell / search, file tree collapse-all
GLYPHS.update({
    "bell": '<path d="M4 11.25 V7.25 A4 4 0 0 1 12 7.25 V11.25 L13.25 12.5 H2.75 Z"/><path d="M6.5 14.25 H9.5"/>',
    "search": '<circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4 L14 14"/>',
    "ellipsis": '<path d="M3.5 8 H3.6 M8 8 H8.1 M12.5 8 H12.6"/>',
    "collapse-all": '<path d="M4.5 2.5 L8 6 L11.5 2.5"/><path d="M4.5 13.5 L8 10 L11.5 13.5"/><path d="M2.5 8 H13.5"/>',
})

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
