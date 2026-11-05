import { type Point } from "./types";
import { ROPE_BEND_RATIO_MAX, ROPE_BEND_RATIO_MIN, ROPE_CONTROL_POINT_COUNT } from "./constants";

/** Port of Sources/Canvas/Physics/RopeSimulation.swift (Verlet + Jakobsen constraints). */
const GRAVITY = 0.8; // canvas units / frame^2, +Y is down
const DAMPING = 0.98;
const CONSTRAINT_ITERATIONS = 5;
const SLEEP_THRESHOLD = 0.1;
const WAKE_THRESHOLD = 0.5;
const MIN_ROPE_LENGTH = 20;

const avgBend = () => (ROPE_BEND_RATIO_MIN + ROPE_BEND_RATIO_MAX) / 2;
const dist = (a: Point, b: Point) => Math.hypot(b.x - a.x, b.y - a.y);

function lerpLine(a: Point, b: Point, count: number): Point[] {
  return Array.from({ length: count }, (_, i) => {
    const t = i / (count - 1);
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  });
}

export class Rope {
  points: Point[];
  prevPoints: Point[];
  segmentLength = 0;

  constructor(readonly id: string, public anchorA: Point, public anchorB: Point, count = ROPE_CONTROL_POINT_COUNT) {
    this.points = lerpLine(anchorA, anchorB, count);
    this.prevPoints = this.points.map((p) => ({ ...p }));
    this.updateSegmentLength();
  }

  updateSegmentLength(): void {
    const length = Math.max(dist(this.anchorA, this.anchorB) * avgBend(), MIN_ROPE_LENGTH);
    this.segmentLength = length / (this.points.length - 1);
  }

  /** Pin both end points to the anchors (they never move under simulation). */
  pinEnds(): void {
    const last = this.points.length - 1;
    this.points[0] = { ...this.anchorA };
    this.prevPoints[0] = { ...this.anchorA };
    this.points[last] = { ...this.anchorB };
    this.prevPoints[last] = { ...this.anchorB };
  }
}

/** Static parabolic catenary used for drag-preview / snapshots (no animation). */
export function computeStaticCatenary(start: Point, end: Point, count = ROPE_CONTROL_POINT_COUNT): Point[] {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const d = Math.hypot(dx, dy);
  if (d <= 1) return Array.from({ length: count }, () => ({ ...start }));
  const sag = d * (avgBend() - 1) * 1.5;
  return Array.from({ length: count }, (_, i) => {
    const t = i / (count - 1);
    return { x: start.x + dx * t, y: start.y + dy * t + 4 * sag * t * (1 - t) };
  });
}

/**
 * Timer-free simulation: the host calls `tick()` once per frame (rAF).
 * `tick()` returns true while ropes are still moving; false once asleep.
 */
export class RopeSimulation {
  private ropes = new Map<string, Rope>();
  private sleeping = true;
  onSleep?: (points: Map<string, Point[]>) => void;

  get isSleeping(): boolean { return this.sleeping; }
  get ids(): string[] { return [...this.ropes.keys()]; }
  pointsFor(id: string): Point[] | undefined { return this.ropes.get(id)?.points; }

  addRope(id: string, a: Point, b: Point, existing?: Point[]): void {
    const rope = new Rope(id, a, b);
    if (existing && existing.length === rope.points.length) {
      rope.points = existing.map((p) => ({ ...p }));
      rope.prevPoints = existing.map((p) => ({ ...p }));
    } else {
      this.wake(); // restored ropes are assumed settled
    }
    this.ropes.set(id, rope);
  }

  removeRope(id: string): void {
    this.ropes.delete(id);
    if (this.ropes.size === 0) this.fallAsleep();
  }

  updateAnchors(id: string, a: Point, b: Point): void {
    const rope = this.ropes.get(id);
    if (!rope) return;
    const moved = Math.max(dist(rope.anchorA, a), dist(rope.anchorB, b));
    rope.anchorA = a;
    rope.anchorB = b;
    rope.pinEnds();
    rope.updateSegmentLength();
    if (moved > WAKE_THRESHOLD) this.wake();
  }

  wake(): void { this.sleeping = false; }

  private fallAsleep(): void {
    if (this.sleeping) return;
    this.sleeping = true;
    this.onSleep?.(new Map([...this.ropes].map(([id, r]) => [id, r.points])));
  }

  tick(): boolean {
    if (this.sleeping) return false;
    let movement = 0;
    for (const rope of this.ropes.values()) movement += this.simulate(rope);
    if (movement < SLEEP_THRESHOLD) this.fallAsleep();
    return !this.sleeping;
  }

  private simulate(rope: Rope): number {
    const n = rope.points.length;
    if (n < 2) return 0;
    const before = rope.points.map((p) => ({ ...p }));

    // Verlet integration (end points are pinned below).
    for (let i = 1; i < n - 1; i++) {
      const cur = rope.points[i]!;
      const prev = rope.prevPoints[i]!;
      rope.prevPoints[i] = { ...cur };
      rope.points[i] = { x: cur.x + (cur.x - prev.x) * DAMPING, y: cur.y + (cur.y - prev.y) * DAMPING + GRAVITY };
    }
    rope.pinEnds();

    for (let k = 0; k < CONSTRAINT_ITERATIONS; k++) {
      this.applyConstraints(rope);
      rope.points[0] = { ...rope.anchorA };
      rope.points[n - 1] = { ...rope.anchorB };
    }

    let movement = 0;
    for (let i = 1; i < n - 1; i++) {
      movement += Math.abs(rope.points[i]!.x - before[i]!.x) + Math.abs(rope.points[i]!.y - before[i]!.y);
    }
    return movement;
  }

  /** Jakobsen distance constraints; first/last point are immovable. */
  private applyConstraints(rope: Rope): void {
    const n = rope.points.length;
    for (let i = 0; i < n - 1; i++) {
      const p1 = rope.points[i]!;
      const p2 = rope.points[i + 1]!;
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const d = Math.hypot(dx, dy);
      if (d <= 0.001) continue;
      const diff = (rope.segmentLength - d) / d;
      const ox = dx * diff * 0.5;
      const oy = dy * diff * 0.5;
      if (i !== 0) rope.points[i] = { x: p1.x - ox, y: p1.y - oy };
      if (i + 1 !== n - 1) rope.points[i + 1] = { x: p2.x + ox, y: p2.y + oy };
    }
  }
}
