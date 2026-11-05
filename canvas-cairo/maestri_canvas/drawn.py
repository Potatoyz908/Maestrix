"""Canvas-drawn node kinds (text, shape, freehand): content defaults, freehand normalisation and Cairo drawing. Pure: no GTK.

Content dicts are the schema-v2 `_0` payloads, byte-compatible with the Swift `TextContent` / `ShapeContent` / `FreehandContent`
(CGPoint encodes as `[x, y]`). Unknown keys are preserved by callers because they edit the dict they were given.

Tool names follow the macOS app (`WorkspaceCanvasView.handleNodeDrawn`): text, rect, ellipse, diamond, freehand_pen,
freehand_highlighter. `kind_for_tool` maps them to the persisted node kind.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

import cairo

from .geometry import Point, Rect

DRAWN_KINDS = frozenset({"text", "shape", "freehand"})
SHAPE_TOOLS = ("rect", "ellipse", "diamond")
FREEHAND_TOOLS = ("freehand_pen", "freehand_highlighter")
DRAW_TOOLS = ("text",) + SHAPE_TOOLS + FREEHAND_TOOLS
FREEHAND_PADDING = 8.0            # CanvasInteractionHandler: bounding rect = points bbox + 8 on every side
TEXT_DEFAULT_SIZE = (45.0, 35.0)  # defaultNodeSize("text"): a click (no drag) creates a label this big, then it grows to fit
TEXT_PADDING = 4.0

# NoteColorPickerPopover.colorFromString: the palette shapes and drawings resolve their colour names with.
_NAMED = {
    "yellow": (0.99, 0.97, 0.72), "pink": (0.96, 0.75, 0.80), "blue": (0.73, 0.84, 0.96), "green": (0.73, 0.93, 0.80),
    "orange": (0.99, 0.84, 0.67), "purple": (0.87, 0.77, 0.96), "white": (0.97, 0.97, 0.97), "black": (0.18, 0.18, 0.18),
    "darkgray": (0.25, 0.32, 0.38), "darkblue": (0.10, 0.18, 0.45),
}
_FALLBACK = _NAMED["yellow"]
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def kind_for_tool(tool: str) -> str | None:
    if tool == "text":
        return "text"
    if tool in SHAPE_TOOLS or tool == "shape":
        return "shape"
    if tool in FREEHAND_TOOLS:
        return "freehand"
    return None


def resolve_rgb(color: str | None) -> tuple[float, float, float]:
    """Swift's colorFromString: a palette name, else hex (`#RRGGBB`, alpha ignored), else yellow."""
    s = (color or "").strip()
    if s in _NAMED:
        return _NAMED[s]
    m = _HEX.match(s)
    if m:
        h = m.group(1)[:6]
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    return _FALLBACK


# ---- content defaults (Swift initialisers) -----------------------------------------------------------
def default_text(text: str = "") -> dict:
    return {"text": text, "fontSize": 18.0, "fontWeight": "regular", "color": "#1a1a1a", "alignment": "left", "fontFamily": "sans"}


def default_shape(shape_type: str = "rect", color: str = "blue") -> dict:
    return {"shapeType": shape_type if shape_type in SHAPE_TOOLS else "rect", "fillColor": color, "strokeColor": color,
            "strokeWidth": 2.0, "strokeStyle": "solid", "fillStyle": "solid", "text": "", "fontSize": 16.0, "rotation": 0.0}


def default_freehand(freehand_type: str = "pen", color: str = "blue") -> dict:
    hl = freehand_type == "highlighter"
    return {"freehandType": "highlighter" if hl else "pen", "points": [], "strokeColor": color,
            "strokeWidth": 12.0 if hl else 3.0, "opacity": 0.4 if hl else 1.0, "rotation": 0.0}


def content_for_tool(tool: str, color: str = "blue") -> dict:
    if tool == "text":
        return default_text()
    if tool in SHAPE_TOOLS or tool == "shape":
        return default_shape(tool if tool in SHAPE_TOOLS else "rect", color)
    if tool in FREEHAND_TOOLS:
        return default_freehand("highlighter" if tool == "freehand_highlighter" else "pen", color)
    raise ValueError(f"not a drawing tool: {tool!r}")


def normalize_freehand(points: Sequence[Point], padding: float = FREEHAND_PADDING) -> tuple[Rect, list[list[float]]] | None:
    """Canvas points -> (bounding frame, points normalised 0..1 in that frame), exactly like the macOS handler. None if < 2 points."""
    if len(points) < 2:
        return None
    min_x = min(p.x for p in points); max_x = max(p.x for p in points)
    min_y = min(p.y for p in points); max_y = max(p.y for p in points)
    frame = Rect(min_x - padding, min_y - padding, (max_x - min_x) + padding * 2, (max_y - min_y) + padding * 2)
    norm = [[(p.x - frame.x) / frame.w if frame.w > 0 else 0.5, (p.y - frame.y) / frame.h if frame.h > 0 else 0.5] for p in points]
    return frame, norm


def _points(content: dict) -> list[tuple[float, float]]:
    out = []
    for p in content.get("points") or ():
        try:
            out.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError, KeyError):
            continue   # tolerate a malformed point instead of failing the whole frame
    return out


def _num(content: dict, key: str, default: float) -> float:
    try:
        return float(content.get(key, default))
    except (TypeError, ValueError):
        return default


# ---- text layout (PangoCairo when present, cairo toy API otherwise) -----------------------------------
try:
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)))   # needs gi's cairo bridge
except Exception:   # pragma: no cover - depends on the host
    Pango = PangoCairo = None

_FAMILY = {"sans": "Sans", "serif": "Serif", "mono": "Monospace"}
_ALIGN = {"left": 0, "center": 1, "right": 2}


def _layout(cr: cairo.Context, text: str, size: float, weight: str = "regular", family: str = "sans", align: str = "left",
            width: float | None = None):
    lay = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string(_FAMILY.get(family, family or "Sans"))
    desc.set_absolute_size(size * Pango.SCALE)
    desc.set_weight({"bold": Pango.Weight.BOLD, "medium": Pango.Weight.MEDIUM}.get(weight, Pango.Weight.NORMAL))
    lay.set_font_description(desc)
    lay.set_alignment((Pango.Alignment.LEFT, Pango.Alignment.CENTER, Pango.Alignment.RIGHT)[_ALIGN.get(align, 0)])
    if width is not None:
        lay.set_width(int(max(1.0, width) * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    lay.set_text(text, -1)
    return lay


def _toy_lines(cr: cairo.Context, text: str, size: float, weight: str, family: str) -> list[tuple[str, float]]:
    cr.select_font_face({"serif": "serif", "mono": "monospace"}.get(family, "sans-serif"), cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if weight == "bold" else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    return [(line, cr.text_extents(line).x_advance) for line in text.split("\n")]


def measure_text(content: dict) -> tuple[float, float]:
    """Natural size of a text node's label, padding included (the frame grows to this after an edit)."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    cr = cairo.Context(surf)
    text = str(content.get("text") or " ")
    size = _num(content, "fontSize", 18.0)
    weight, family = str(content.get("fontWeight", "regular")), str(content.get("fontFamily", "sans"))
    if PangoCairo is not None:
        w, h = _layout(cr, text, size, weight, family).get_pixel_size()
    else:
        lines = _toy_lines(cr, text, size, weight, family)
        w, h = max(a for _, a in lines), len(lines) * size * 1.25
    return math.ceil(w + 2 * TEXT_PADDING), math.ceil(h + 2 * TEXT_PADDING)


def _draw_text(cr: cairo.Context, text: str, frame: Rect, size: float, rgb, weight="regular", family="sans", align="left",
               valign_center=False) -> None:
    if not text:
        return
    inner_w = max(1.0, frame.w - 2 * TEXT_PADDING)
    cr.save()
    cr.rectangle(frame.x, frame.y, frame.w, frame.h)
    cr.clip()
    cr.set_source_rgb(*rgb)
    if PangoCairo is not None:
        lay = _layout(cr, text, size, weight, family, align, inner_w)
        _, h = lay.get_pixel_size()
        y = frame.y + (frame.h - h) / 2 if valign_center else frame.y + TEXT_PADDING
        cr.move_to(frame.x + TEXT_PADDING, y)
        PangoCairo.show_layout(cr, lay)
    else:
        lines = _toy_lines(cr, text, size, weight, family)
        lh = size * 1.25
        y = frame.y + (frame.h - lh * len(lines)) / 2 if valign_center else frame.y + TEXT_PADDING
        for i, (line, adv) in enumerate(lines):
            x = frame.x + TEXT_PADDING + {"center": (inner_w - adv) / 2, "right": inner_w - adv}.get(align, 0.0)
            cr.move_to(x, y + size + i * lh)
            cr.show_text(line)
    cr.restore()


# ---- drawing (canvas units: the renderer already applied the viewport transform) -----------------------
def _rotate_about_center(cr: cairo.Context, f: Rect, radians: float) -> None:
    if radians:
        cx, cy = f.x + f.w / 2, f.y + f.h / 2
        cr.translate(cx, cy); cr.rotate(radians); cr.translate(-cx, -cy)


def _shape_path(cr: cairo.Context, shape_type: str, f: Rect) -> None:
    if shape_type == "ellipse":
        cr.save()
        cr.translate(f.x + f.w / 2, f.y + f.h / 2)
        cr.scale(max(f.w, 1e-6) / 2, max(f.h, 1e-6) / 2)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
    elif shape_type == "diamond":
        cr.move_to(f.x + f.w / 2, f.y); cr.line_to(f.x + f.w, f.y + f.h / 2)
        cr.line_to(f.x + f.w / 2, f.y + f.h); cr.line_to(f.x, f.y + f.h / 2); cr.close_path()
    else:
        cr.rectangle(f.x, f.y, f.w, f.h)


def _hatch(cr: cairo.Context, f: Rect, rgb, angle_deg: float) -> None:
    spacing = 8.0
    diag = math.hypot(f.w, f.h)
    count = int(diag / spacing) + 2
    cx, cy = f.x + f.w / 2, f.y + f.h / 2
    rad = math.radians(angle_deg)
    nx, ny = math.cos(rad + math.pi / 2), math.sin(rad + math.pi / 2)
    dx, dy = diag * math.cos(rad), diag * math.sin(rad)
    for i in range(-count, count + 1):
        ox, oy = cx + i * spacing * nx, cy + i * spacing * ny
        cr.move_to(ox - dx, oy - dy); cr.line_to(ox + dx, oy + dy)
    cr.set_source_rgba(*rgb, 0.6)
    cr.set_line_width(1.0)
    cr.stroke()


def draw_shape(cr: cairo.Context, c: dict, f: Rect) -> None:
    rgb = resolve_rgb(c.get("strokeColor"))
    shape_type = str(c.get("shapeType", "rect"))
    cr.save()
    _rotate_about_center(cr, f, _num(c, "rotation", 0.0))
    fill = str(c.get("fillStyle", "solid"))
    if fill != "none":
        _shape_path(cr, shape_type, f)
        cr.set_source_rgba(*rgb, 0.3 if fill == "solid" else 0.1)
        cr.fill_preserve()
        if fill in ("hatched", "crossHatched"):
            cr.save()
            cr.clip()
            _hatch(cr, f, rgb, 45)
            if fill == "crossHatched":
                _hatch(cr, f, rgb, -45)
            cr.restore()
        cr.new_path()
    width = _num(c, "strokeWidth", 2.0)
    if width > 0:
        _shape_path(cr, shape_type, f)
        cr.set_source_rgb(*rgb)
        cr.set_line_width(width)
        cr.set_line_join(cairo.LINE_JOIN_MITER)
        style = c.get("strokeStyle")
        cr.set_dash([8, 4] if style == "dashed" else [2, 4] if style == "dotted" else [])
        cr.stroke()
        cr.set_dash([])
    text = str(c.get("text") or "")
    if text:   # label colour: the stroke colour blended 35% towards black (ShapeNodeSwiftUIView.textColor)
        _draw_text(cr, text, f, _num(c, "fontSize", 16.0), tuple(v * 0.65 for v in rgb), align="center", valign_center=True)
    cr.restore()


def draw_freehand(cr: cairo.Context, c: dict, f: Rect) -> None:
    pts = [(f.x + x * f.w, f.y + y * f.h) for x, y in _points(c)]
    if len(pts) < 2:
        return
    cr.save()
    _rotate_about_center(cr, f, _num(c, "rotation", 0.0))
    cr.move_to(*pts[0])
    if len(pts) > 3:   # Catmull-Rom -> cubic Bezier, same control points as FreehandNodeSwiftUIView.catmullRomPath
        n = len(pts)
        for i in range(n - 1):
            p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, n - 1)]
            cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                        p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1])
    else:
        for p in pts[1:]:
            cr.line_to(*p)
    cr.set_source_rgba(*resolve_rgb(c.get("strokeColor")), max(0.0, min(1.0, _num(c, "opacity", 1.0))))
    cr.set_line_width(_num(c, "strokeWidth", 3.0))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke()
    cr.restore()


def draw_text_node(cr: cairo.Context, c: dict, f: Rect) -> None:
    _draw_text(cr, str(c.get("text") or ""), f, _num(c, "fontSize", 18.0), resolve_rgb(c.get("color") or "#1a1a1a"),
               str(c.get("fontWeight", "regular")), str(c.get("fontFamily", "sans")), str(c.get("alignment", "left")))


def draw_freehand_preview(cr: cairo.Context, points: Iterable[Point], tool: str, color: str = "blue") -> None:
    pts = list(points)
    if len(pts) < 2:
        return
    c = content_for_tool(tool if tool in FREEHAND_TOOLS else "freehand_pen", color)
    cr.save()
    cr.move_to(pts[0].x, pts[0].y)
    for p in pts[1:]:
        cr.line_to(p.x, p.y)
    cr.set_source_rgba(*resolve_rgb(c["strokeColor"]), c["opacity"])
    cr.set_line_width(c["strokeWidth"])
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke()
    cr.restore()


def draw_node(cr: cairo.Context, kind: str, content: dict | None, frame: Rect) -> bool:
    """Draw a canvas-drawn node. False when there is nothing to draw it with (unknown kind or no content): the caller falls back."""
    if not isinstance(content, dict):
        return False
    if kind == "shape":
        draw_shape(cr, content, frame)
    elif kind == "freehand":
        draw_freehand(cr, content, frame)
    elif kind == "text":
        draw_text_node(cr, content, frame)
    else:
        return False
    return True
