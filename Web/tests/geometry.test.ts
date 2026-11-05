import { describe, expect, it } from "vitest";
import { snap } from "../src/geometry/tileSnapping";
import { RopeSimulation, computeStaticCatenary } from "../src/geometry/rope";
import { frameFromRect, rectFromFrame } from "../src/geometry/types";
import { Viewport } from "../src/canvas/viewport";

describe("frame format", () => {
  it("round-trips Maestri [[x,y],[w,h]]", () => {
    expect(frameFromRect(rectFromFrame([[1, 2], [3, 4]]))).toEqual([[1, 2], [3, 4]]);
  });
});

describe("TileSnapping", () => {
  const other = { x: 100, y: 100, w: 200, h: 100 };
  it("snaps left edge to the other's right edge within threshold", () => {
    const r = snap({ x: 305, y: 400, w: 50, h: 50 }, [other]);
    expect(r.snappedOrigin.x).toBe(300);
    expect(r.guidelines.some((g) => g.axis === "vertical" && g.position === 300)).toBe(true);
  });
  it("does not snap beyond threshold", () => {
    expect(snap({ x: 330, y: 500, w: 50, h: 50 }, [other]).snappedOrigin).toEqual({ x: 330, y: 500 });
  });
});

describe("Rope", () => {
  it("static catenary: 21 points, ends fixed, sags downward (+Y)", () => {
    const p = computeStaticCatenary({ x: 0, y: 0 }, { x: 200, y: 0 });
    expect(p).toHaveLength(21);
    expect(p[0]).toEqual({ x: 0, y: 0 });
    expect(p[10]!.y).toBeGreaterThan(0);
  });
  it("simulation settles and goes to sleep with anchors pinned", () => {
    const sim = new RopeSimulation();
    sim.addRope("r", { x: 0, y: 0 }, { x: 300, y: 0 });
    let frames = 0;
    while (sim.tick() && frames < 5000) frames++;
    expect(sim.isSleeping).toBe(true);
    const pts = sim.pointsFor("r")!;
    expect(pts[0]).toEqual({ x: 0, y: 0 });
    expect(pts[20]).toEqual({ x: 300, y: 0 });
    expect(pts[10]!.y).toBeGreaterThan(0);
  });
});

describe("Viewport", () => {
  it("zoomAt keeps the anchor's canvas point fixed", () => {
    const v = new Viewport();
    const a = { x: 300, y: 200 };
    const before = v.toCanvas(a);
    v.zoomAt(a, 1.5);
    expect(v.toCanvas(a).x).toBeCloseTo(before.x);
    expect(v.toCanvas(a).y).toBeCloseTo(before.y);
  });
});
