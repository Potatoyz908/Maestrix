import { sameId } from "../net/protocol";
import type { Point, Rect } from "./types";

/** Connection pins sit at the vertical middle of a node's left/right edge. */
export type PinSide = "left" | "right";

export const pinPoint = (f: Rect, side: PinSide): Point => ({ x: side === "left" ? f.x : f.x + f.w, y: f.y + f.h / 2 });

/** Default sides for a connection: the two pins that face each other horizontally. */
export function facingSides(a: Rect, b: Rect): [PinSide, PinSide] {
  return a.x + a.w / 2 <= b.x + b.w / 2 ? ["right", "left"] : ["left", "right"];
}

/** No self-links and no duplicates (undirected, ids compared case-insensitively). */
export function canConnect(existing: readonly { a: string; b: string }[], a: string, b: string): boolean {
  if (sameId(a, b)) return false;
  return !existing.some((c) => (sameId(c.a, a) && sameId(c.b, b)) || (sameId(c.a, b) && sameId(c.b, a)));
}
