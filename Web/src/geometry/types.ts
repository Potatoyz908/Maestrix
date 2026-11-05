/** Canvas-space primitives. Mirrors CoreGraphics semantics (Y grows downward). */
export interface Point { x: number; y: number }
export interface Size { w: number; h: number }
export interface Rect { x: number; y: number; w: number; h: number }

/** Maestri on-disk frame format: [[x, y], [w, h]] (see CGRect+Frame.swift). */
export type FrameTuple = [[number, number], [number, number]];

export const rectFromFrame = (f: FrameTuple): Rect => ({ x: f[0][0], y: f[0][1], w: f[1][0], h: f[1][1] });
export const frameFromRect = (r: Rect): FrameTuple => [[r.x, r.y], [r.w, r.h]];

export const minX = (r: Rect) => r.x;
export const maxX = (r: Rect) => r.x + r.w;
export const minY = (r: Rect) => r.y;
export const maxY = (r: Rect) => r.y + r.h;

export function insetBy(r: Rect, dx: number, dy: number): Rect {
  return { x: r.x + dx, y: r.y + dy, w: r.w - 2 * dx, h: r.h - 2 * dy };
}

/** CGRect.intersects: true only when the overlap has non-zero area. */
export function intersects(a: Rect, b: Rect): boolean {
  return a.x < maxX(b) && maxX(a) > b.x && a.y < maxY(b) && maxY(a) > b.y;
}
