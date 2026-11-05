import { type Rect, type Point, insetBy, intersects, minX, maxX, minY, maxY } from "./types";

/** Port of Sources/Canvas/Visual/TileSnapping.swift. */
export const SNAP_THRESHOLD = 12;
const PROXIMITY_RADIUS = 600;

export type GuideAxis = "horizontal" | "vertical";
export interface GuideLine {
  axis: GuideAxis;
  /** x for vertical lines, y for horizontal lines. */
  position: number;
  start: number;
  end: number;
}

export function snap(draggingFrame: Rect, otherFrames: readonly Rect[]): { snappedOrigin: Point; guidelines: GuideLine[] } {
  let x = draggingFrame.x;
  let y = draggingFrame.y;
  let guidelines: GuideLine[] = [];

  const dMinX = minX(draggingFrame), dMaxX = maxX(draggingFrame);
  const dMinY = minY(draggingFrame), dMaxY = maxY(draggingFrame);
  let bestDX = SNAP_THRESHOLD + 1;
  let bestDY = SNAP_THRESHOLD + 1;
  const expanded = insetBy(draggingFrame, -PROXIMITY_RADIUS, -PROXIMITY_RADIUS);

  for (const other of otherFrames) {
    if (!intersects(expanded, other)) continue;
    const oMinX = minX(other), oMaxX = maxX(other);
    const oMinY = minY(other), oMaxY = maxY(other);

    // Wall matching X: left/right edges (guideline is a vertical line).
    for (const [da, oa] of [[dMinX, oMinX], [dMinX, oMaxX], [dMaxX, oMinX], [dMaxX, oMaxX]] as const) {
      const dist = Math.abs(da - oa);
      if (dist < SNAP_THRESHOLD && dist < bestDX) {
        bestDX = dist;
        x = dMinX + (oa - da);
        guidelines = guidelines.filter((g) => g.axis !== "vertical");
        guidelines.push({ axis: "vertical", position: oa, start: Math.min(dMinY, oMinY) - 20, end: Math.max(dMaxY, oMaxY) + 20 });
      }
    }

    // Wall matching Y: top/bottom edges (guideline is a horizontal line).
    for (const [da, oa] of [[dMinY, oMinY], [dMinY, oMaxY], [dMaxY, oMinY], [dMaxY, oMaxY]] as const) {
      const dist = Math.abs(da - oa);
      if (dist < SNAP_THRESHOLD && dist < bestDY) {
        bestDY = dist;
        y = dMinY + (oa - da);
        guidelines = guidelines.filter((g) => g.axis !== "horizontal");
        guidelines.push({ axis: "horizontal", position: oa, start: Math.min(dMinX, oMinX) - 20, end: Math.max(dMaxX, oMaxX) + 20 });
      }
    }
  }
  return { snappedOrigin: { x, y }, guidelines };
}
