import type { Rect } from "../geometry/types";
import type { Viewport } from "./viewport";

/**
 * CSS box for a DOM node overlaying the canvas. The element keeps its *logical*
 * size (frame.w x frame.h, so xterm's fit works in layout pixels) and is moved and
 * scaled with a transform: screen = (canvas - origin) * zoom.
 */
export function nodeBox(frame: Rect, vp: Pick<Viewport, "origin" | "zoom">): { transform: string; width: string; height: string } {
  const sx = (frame.x - vp.origin.x) * vp.zoom;
  const sy = (frame.y - vp.origin.y) * vp.zoom;
  return {
    transform: `translate(${sx}px, ${sy}px) scale(${vp.zoom})`,
    width: `${frame.w}px`,
    height: `${frame.h}px`,
  };
}
