import { CANVAS_GRID_SPACING, MIN_NODE_HEIGHT, MIN_NODE_WIDTH } from "./constants";
import type { Rect } from "./types";

/** Port of ResizeEdge.swift + CanvasResizeHandler.applyResizeOnCanvas (done in canvas space). */
export type ResizeEdge = "right" | "left" | "top" | "bottom" | "topLeft" | "topRight" | "bottomLeft" | "bottomRight";
export const RESIZE_EDGES: readonly ResizeEdge[] = ["right", "left", "top", "bottom", "topLeft", "topRight", "bottomLeft", "bottomRight"];

export const RESIZE_CURSOR: Record<ResizeEdge, string> = {
  left: "ew-resize", right: "ew-resize", top: "ns-resize", bottom: "ns-resize",
  topLeft: "nwse-resize", bottomRight: "nwse-resize", topRight: "nesw-resize", bottomLeft: "nesw-resize",
};

const snapGrid = (v: number) => Math.round(v / CANVAS_GRID_SPACING) * CANVAS_GRID_SPACING;

/**
 * @param start  frame when the drag began (canvas units)
 * @param dx,dy  pointer delta in canvas units (screen delta / zoom); +y is down
 * The active edge snaps to the 16-unit grid; the opposite edge stays fixed.
 */
export function resizeFrame(start: Rect, edge: ResizeEdge, dx: number, dy: number, grid = true): Rect {
  const movesLeft = edge === "left" || edge === "topLeft" || edge === "bottomLeft";
  const movesRight = edge === "right" || edge === "topRight" || edge === "bottomRight";
  const movesTop = edge === "top" || edge === "topLeft" || edge === "topRight";
  const movesBottom = edge === "bottom" || edge === "bottomLeft" || edge === "bottomRight";
  const s = grid ? snapGrid : (v: number) => v;

  let left = start.x, right = start.x + start.w, top = start.y, bottom = start.y + start.h;
  if (movesLeft) left = Math.min(s(start.x + dx), right - MIN_NODE_WIDTH);
  if (movesRight) right = Math.max(s(right + dx), left + MIN_NODE_WIDTH);
  if (movesTop) top = Math.min(s(start.y + dy), bottom - MIN_NODE_HEIGHT);
  if (movesBottom) bottom = Math.max(s(bottom + dy), top + MIN_NODE_HEIGHT);
  return { x: left, y: top, w: right - left, h: bottom - top };
}
