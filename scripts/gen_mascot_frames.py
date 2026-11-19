#!/usr/bin/env python3
"""Draws the Maestrix mascot (a little conductor in the app colours) as transparent PNG frames:
idle_00..11.png (breathing + blink) and alert_00..11.png (jump + waving baton). Replace the PNGs with your own art if you like:
any number of idle_*.png / alert_*.png frames of the same size is picked up by ui/mascot.py."""
import math
import sys
from pathlib import Path

import cairo

SIZE = 128
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "gtk4-ui/src/ui/assets/mascot"
BLUE, PURPLE, CYAN = (0x16 / 255, 0x8b / 255, 0xf1 / 255), (0x93 / 255, 0x54 / 255, 0xe8 / 255), (0x71 / 255, 0xdc / 255, 0xec / 255)


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def frame(path: Path, bob: float, squash: float, blink: bool, baton: float, arm: float, sparkle: float) -> None:
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    cr = cairo.Context(s)
    cx, ground = SIZE / 2, SIZE - 10
    # shadow (shrinks while jumping)
    cr.save()
    cr.translate(cx, ground + 2)
    cr.scale(1.0, 0.28)
    cr.arc(0, 0, 26 - min(bob, 18) * 0.5, 0, 2 * math.pi)
    cr.restore()
    cr.set_source_rgba(0, 0, 0, 0.22)
    cr.fill()

    w, h = 64 * (1 + squash), 76 * (1 - squash)
    x, y = cx - w / 2, ground - h - bob
    # body: rounded capsule with the app gradient
    g = cairo.LinearGradient(x, y, x + w, y + h)
    g.add_color_stop_rgb(0, *BLUE)
    g.add_color_stop_rgb(1, *PURPLE)
    rounded(cr, x, y, w, h, 26)
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.25)
    cr.set_line_width(2)
    cr.stroke()
    # visor
    vx, vy, vw, vh = x + 10, y + 16, w - 20, 26
    rounded(cr, vx, vy, vw, vh, 12)
    cr.set_source_rgb(0.05, 0.07, 0.13)
    cr.fill()
    # eyes
    cr.set_source_rgb(*CYAN)
    for ex in (vx + vw * 0.32, vx + vw * 0.68):
        if blink:
            cr.rectangle(ex - 6, vy + vh / 2 - 1.5, 12, 3)
        else:
            cr.save()
            cr.translate(ex, vy + vh / 2)
            cr.scale(1.0, 1.25)
            cr.arc(0, 0, 5, 0, 2 * math.pi)
            cr.restore()
        cr.fill()
    # bow tie (conductor)
    by = y + 52
    cr.set_source_rgb(0.05, 0.07, 0.13)
    cr.move_to(cx, by)
    cr.line_to(cx - 11, by - 6)
    cr.line_to(cx - 11, by + 6)
    cr.close_path()
    cr.move_to(cx, by)
    cr.line_to(cx + 11, by - 6)
    cr.line_to(cx + 11, by + 6)
    cr.close_path()
    cr.fill()
    cr.arc(cx, by, 3, 0, 2 * math.pi)
    cr.fill()
    # right arm + baton
    sx, sy = x + w - 4, y + 48
    hx, hy = sx + 14 * math.cos(arm), sy - 14 * math.sin(arm)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(7)
    cr.set_source(g)
    cr.move_to(sx, sy)
    cr.line_to(hx, hy)
    cr.stroke()
    cr.set_line_width(3)
    cr.set_source_rgb(0.95, 0.96, 1.0)
    cr.move_to(hx, hy)
    cr.line_to(hx + 22 * math.cos(baton), hy - 22 * math.sin(baton))
    cr.stroke()
    # left arm resting
    cr.set_line_width(7)
    cr.set_source(g)
    cr.move_to(x + 4, y + 48)
    cr.line_to(x - 6, y + 60)
    cr.stroke()
    # music sparkles while alerting
    if sparkle > 0:
        cr.set_source_rgba(*CYAN, sparkle)
        for i, (dx, dy) in enumerate(((-38, -30), (36, -42), (-30, -56))):
            px, py = cx + dx, y + 20 + dy * 0.5 - 6 * sparkle
            cr.arc(px, py, 3.5, 0, 2 * math.pi)
            cr.fill()
            cr.set_line_width(2)
            cr.move_to(px + 3.2, py)
            cr.line_to(px + 3.2, py - 11)
            cr.stroke()
    s.write_to_png(str(path))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    n = 12
    for i in range(n):
        t = i / n
        breathe = math.sin(2 * math.pi * t)
        frame(OUT / f"idle_{i:02d}.png", bob=2 + 2 * breathe, squash=0.02 * breathe, blink=i in (9, 10),
              baton=math.radians(60), arm=math.radians(20), sparkle=0)
        jump = max(0.0, math.sin(2 * math.pi * t))
        frame(OUT / f"alert_{i:02d}.png", bob=22 * jump, squash=-0.06 * jump + (0.08 if jump == 0 else 0),
              blink=False, baton=math.radians(60 + 50 * math.sin(4 * math.pi * t)),
              arm=math.radians(45 + 25 * math.sin(4 * math.pi * t)), sparkle=0.4 + 0.6 * jump)


if __name__ == "__main__":
    main()
