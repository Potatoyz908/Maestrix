"""Open Maestri native canvas: pure engine (no GTK imports) + pycairo renderer + GTK 4 widget (maestri_canvas.widget)."""
from .geometry import Point, Rect
from .viewport import Viewport

__all__ = ["Point", "Rect", "Viewport"]
