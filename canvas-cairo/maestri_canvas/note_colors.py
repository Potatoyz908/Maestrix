"""Sticky-note colours (pure). The persisted `color` is a hex string OR a Maestri colour name ("yellow"); the original string is
preserved on save and only RESOLVED for rendering."""
from __future__ import annotations

import re

DEFAULT_COLOR = "#FEFDE8"   # Constants.noteDefaultColor

NAMED: dict[str, str] = {
    "yellow": "#FCF7B8", "pink": "#F5BFCC", "blue": "#BAD6F5", "green": "#BAEDCC",
    "orange": "#FCD6AB", "purple": "#DEC4F5", "white": "#F7F7F7",
    "black": "#2E2E2E", "darkgray": "#405261", "darkblue": "#1A2E73",
}
PRESETS: list[tuple[str, str]] = [(name, name.title()) for name in NAMED]
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def resolve_hex(color: str | None) -> str:
    """'#RRGGBB' (uppercase) for any persisted value; unknown strings fall back to the default instead of failing."""
    if not color:
        return DEFAULT_COLOR
    c = color.strip()
    named = NAMED.get(c.lower())
    if named:
        return named
    m = _HEX.match(c)
    if not m:
        return DEFAULT_COLOR
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h[:6].upper()   # alpha (8 digits) is ignored: a note card is opaque


def to_rgb(color: str | None) -> tuple[float, float, float]:
    h = resolve_hex(color)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def shade(color: str | None, factor: float) -> str:
    """Multiply the channels (factor < 1 darkens). Used for the header/border tints derived from the card colour."""
    return _hex(tuple(c * factor for c in to_rgb(color)))  # type: ignore[arg-type]


def luminance(color: str | None) -> float:
    r, g, b = to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def text_color(color: str | None) -> str:
    """Readable text colour on the card (custom colours may be dark)."""
    return "#1F1F1F" if luminance(color) > 0.5 else "#F2F2F2"


def css_class(color: str | None) -> str:
    return "note-c-" + resolve_hex(color)[1:].lower()


def css_rule(color: str | None) -> str:
    """Per-colour rule. Only the card, its header/footer tint and the text colour depend on the note colour."""
    hx, cls = resolve_hex(color), css_class(color)
    # NodeShellView overlays themeColor on the neutral shell (5%) and header (18%).
    def tint(base, alpha):
        return _hex(tuple(b * (1 - alpha) + c * alpha for b, c in zip(to_rgb(base), to_rgb(hx))))
    body, head = tint("#ECECEC", .05), tint("#E6E8EA", .18)
    return (f".maestri-node.maestri-note.{cls} {{ background: {body}; color: #1F1F1F; }}\n"
            f".maestri-node.maestri-note.{cls}:not(.selected) {{ border-color: rgba(0,0,0,.06); }}\n"
            f".maestri-note.{cls} .note-bar {{ background: {body}; }}\n"
            f".maestri-note.{cls} .maestri-node-header {{ background: {head}; }}\n")



def css_font_size_rule(sizes: range = range(10, 33)) -> str:
    return "".join(f".maestri-note.note-fs-{s} textview {{ font-size: {s}px; }}\n" for s in sizes)


def clamp_font_size(n: int) -> int:
    return max(10, min(32, int(n)))
