"""Verlet + Jakobsen rope simulation (port of RopeSimulation.swift). Timer-free: the widget calls tick() once per frame clock tick."""
from __future__ import annotations

import math
from typing import Callable

from .constants import ROPE_BEND_RATIO_MAX, ROPE_BEND_RATIO_MIN, ROPE_CONTROL_POINT_COUNT
from .geometry import Point

GRAVITY = 0.8           # canvas units / frame^2, +Y is down
DAMPING = 0.98
ITERATIONS = 5
SLEEP_THRESHOLD = 0.1
WAKE_THRESHOLD = 0.5
MIN_ROPE_LENGTH = 20.0
_BEND = (ROPE_BEND_RATIO_MIN + ROPE_BEND_RATIO_MAX) / 2


def static_catenary(a: Point, b: Point, count: int = ROPE_CONTROL_POINT_COUNT) -> list[Point]:
    """Parabolic droop, no animation (drag preview / snapshots)."""
    dx, dy = b.x - a.x, b.y - a.y
    d = math.hypot(dx, dy)
    if d <= 1:
        return [a] * count
    sag = d * (_BEND - 1.0) * 1.5
    out = []
    for i in range(count):
        t = i / (count - 1)
        out.append(Point(a.x + dx * t, a.y + dy * t + 4 * sag * t * (1 - t)))
    return out


class Rope:
    """Coordinates live in two flat float lists (xs, ys): far cheaper in CPython than a list of Point objects."""
    __slots__ = ("id", "ax", "ay", "bx", "by", "xs", "ys", "pxs", "pys", "seg")

    def __init__(self, rope_id: str, a: Point, b: Point, n: int = ROPE_CONTROL_POINT_COUNT) -> None:
        self.id = rope_id
        self.ax, self.ay, self.bx, self.by = a.x, a.y, b.x, b.y
        self.xs = [a.x + (b.x - a.x) * i / (n - 1) for i in range(n)]
        self.ys = [a.y + (b.y - a.y) * i / (n - 1) for i in range(n)]
        self.pxs, self.pys = self.xs[:], self.ys[:]
        self.seg = 0.0
        self.update_segment()

    def update_segment(self) -> None:
        length = max(math.hypot(self.bx - self.ax, self.by - self.ay) * _BEND, MIN_ROPE_LENGTH)
        self.seg = length / (len(self.xs) - 1)

    def pin_ends(self) -> None:
        last = len(self.xs) - 1
        self.xs[0] = self.pxs[0] = self.ax; self.ys[0] = self.pys[0] = self.ay
        self.xs[last] = self.pxs[last] = self.bx; self.ys[last] = self.pys[last] = self.by

    @property
    def points(self) -> list[Point]:
        return [Point(x, y) for x, y in zip(self.xs, self.ys)]


class RopeSimulation:
    def __init__(self) -> None:
        self._ropes: dict[str, Rope] = {}
        self._sleeping = True
        self.on_sleep: Callable[[dict[str, list[Point]]], None] | None = None

    @property
    def is_sleeping(self) -> bool: return self._sleeping
    @property
    def ids(self) -> list[str]: return list(self._ropes)
    def get(self, rope_id: str) -> Rope | None: return self._ropes.get(rope_id)
    def __contains__(self, rope_id: str) -> bool: return rope_id in self._ropes
    def points(self, rope_id: str) -> list[Point] | None:
        r = self._ropes.get(rope_id)
        return r.points if r else None

    def add(self, rope_id: str, a: Point, b: Point, existing: list[Point] | None = None) -> None:
        rope = Rope(rope_id, a, b)
        if existing is not None and len(existing) == len(rope.xs):
            rope.xs = [p.x for p in existing]; rope.ys = [p.y for p in existing]  # restored ropes are assumed settled
            rope.pxs, rope.pys = rope.xs[:], rope.ys[:]
        else:
            self.wake()
        self._ropes[rope_id] = rope

    def remove(self, rope_id: str) -> None:
        self._ropes.pop(rope_id, None)
        if not self._ropes:
            self._sleep()

    def clear(self) -> None:
        self._ropes.clear()
        self._sleeping = True

    def update_anchors(self, rope_id: str, a: Point, b: Point) -> None:
        r = self._ropes.get(rope_id)
        if r is None:
            return
        moved = max(math.hypot(r.ax - a.x, r.ay - a.y), math.hypot(r.bx - b.x, r.by - b.y))
        r.ax, r.ay, r.bx, r.by = a.x, a.y, b.x, b.y
        r.pin_ends()
        r.update_segment()
        if moved > WAKE_THRESHOLD:
            self.wake()

    def wake(self) -> None:
        self._sleeping = False

    def _sleep(self) -> None:
        if self._sleeping:
            return
        self._sleeping = True
        if self.on_sleep:
            self.on_sleep({i: r.points for i, r in self._ropes.items()})

    def tick(self) -> bool:
        """Advance one frame. Returns True while anything is still moving (caller should redraw)."""
        if self._sleeping:
            return False
        movement = 0.0
        for rope in self._ropes.values():
            movement += self._simulate(rope)
        if movement < SLEEP_THRESHOLD:
            self._sleep()
        return True  # this frame changed positions, so redraw even if we just fell asleep

    @staticmethod
    def _simulate(r: Rope) -> float:
        n = len(r.xs)
        xs, ys, pxs, pys = r.xs, r.ys, r.pxs, r.pys
        before_x, before_y = xs[:], ys[:]
        for i in range(1, n - 1):
            cx, cy = xs[i], ys[i]
            xs[i] = cx + (cx - pxs[i]) * DAMPING
            ys[i] = cy + (cy - pys[i]) * DAMPING + GRAVITY
            pxs[i], pys[i] = cx, cy
        r.pin_ends()
        last = n - 1
        seg = r.seg
        for _ in range(ITERATIONS):
            for i in range(n - 1):
                dx = xs[i + 1] - xs[i]
                dy = ys[i + 1] - ys[i]
                d = math.hypot(dx, dy)
                if d <= 0.001:
                    continue
                k = (seg - d) / d * 0.5
                ox, oy = dx * k, dy * k
                if i != 0:
                    xs[i] -= ox; ys[i] -= oy
                if i + 1 != last:
                    xs[i + 1] += ox; ys[i + 1] += oy
            xs[0], ys[0], xs[last], ys[last] = r.ax, r.ay, r.bx, r.by
        return sum(abs(xs[i] - before_x[i]) + abs(ys[i] - before_y[i]) for i in range(1, n - 1))
