"""
Viewport and coordinate system for Open Maestri Cairo Canvas.
Handles coordinate translation, zoom scale, and canvas bounds.
Canvas origin is (9800, 8500) per Maestri standard.
"""

from typing import Tuple


class Viewport:
    def __init__(self, origin_x: float = 9800.0, origin_y: float = 8500.0, zoom: float = 1.0):
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        self.zoom = float(zoom)

        # Zoom limits
        self.min_zoom = 0.1
        self.max_zoom = 3.0

    def screen_to_canvas(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """Convert window/screen pixel coordinate to virtual canvas coordinate."""
        canvas_x = self.origin_x + (screen_x / self.zoom)
        canvas_y = self.origin_y + (screen_y / self.zoom)
        return canvas_x, canvas_y

    def canvas_to_screen(self, canvas_x: float, canvas_y: float) -> Tuple[float, float]:
        """Convert virtual canvas coordinate to window/screen pixel coordinate."""
        screen_x = (canvas_x - self.origin_x) * self.zoom
        screen_y = (canvas_y - self.origin_y) * self.zoom
        return screen_x, screen_y

    def pan(self, delta_screen_x: float, delta_screen_y: float) -> None:
        """Pan viewport by delta screen pixels."""
        self.origin_x -= delta_screen_x / self.zoom
        self.origin_y -= delta_screen_y / self.zoom

    def zoom_at(self, factor: float, screen_center_x: float, screen_center_y: float) -> None:
        """Zoom in or out anchored around screen cursor position."""
        old_zoom = self.zoom
        new_zoom = max(self.min_zoom, min(self.max_zoom, old_zoom * factor))
        if new_zoom == old_zoom:
            return

        # Anchor canvas point under screen center
        anchor_canvas_x, anchor_canvas_y = self.screen_to_canvas(screen_center_x, screen_center_y)

        self.zoom = new_zoom
        # Adjust origin so anchor point stays in same screen position
        self.origin_x = anchor_canvas_x - (screen_center_x / self.zoom)
        self.origin_y = anchor_canvas_y - (screen_center_y / self.zoom)

    def reset_zoom(self, screen_center_x: float, screen_center_y: float) -> None:
        """Reset zoom to 100% (1.0)."""
        self.zoom_at(1.0 / self.zoom, screen_center_x, screen_center_y)
