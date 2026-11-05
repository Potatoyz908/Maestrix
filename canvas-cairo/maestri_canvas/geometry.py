"""Canvas-space primitives. Y grows downward (CoreGraphics flipped semantics), like the macOS canvas."""
from __future__ import annotations

import math
from dataclasses import dataclass


def round_half_away(v: float) -> int:
    """Swift's Double.rounded() (toNearestOrAwayFromZero). Python's round() is banker's rounding, which would
    snap exact ties (e.g. 200/16 = 12.5) to a different grid line than the macOS app does."""
    return int(math.copysign(math.floor(abs(v) + 0.5), v))


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def min_x(self) -> float: return self.x
    @property
    def max_x(self) -> float: return self.x + self.w
    @property
    def min_y(self) -> float: return self.y
    @property
    def max_y(self) -> float: return self.y + self.h
    @property
    def center(self) -> Point: return Point(self.x + self.w / 2, self.y + self.h / 2)

    def contains(self, p: Point) -> bool:
        return self.x <= p.x <= self.max_x and self.y <= p.y <= self.max_y

    def intersects(self, o: "Rect") -> bool:
        """CGRect.intersects: true only when the overlap has non-zero area."""
        return self.x < o.max_x and self.max_x > o.x and self.y < o.max_y and self.max_y > o.y

    def inset(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    def offset(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def union(self, o: "Rect") -> "Rect":
        x0, y0 = min(self.x, o.x), min(self.y, o.y)
        return Rect(x0, y0, max(self.max_x, o.max_x) - x0, max(self.max_y, o.max_y) - y0)

    @staticmethod
    def from_points(a: Point, b: Point) -> "Rect":
        """Normalized rect spanning two corners (marquee / draw-to-create)."""
        return Rect(min(a.x, b.x), min(a.y, b.y), abs(a.x - b.x), abs(a.y - b.y))

    # Maestri on-disk frame: [[x, y], [w, h]] (see CGRect+Frame.swift)
    @staticmethod
    def from_frame(f: "list[list[float]] | tuple") -> "Rect":
        return Rect(float(f[0][0]), float(f[0][1]), float(f[1][0]), float(f[1][1]))

    def to_frame(self) -> list[list[float]]:
        return [[self.x, self.y], [self.w, self.h]]
