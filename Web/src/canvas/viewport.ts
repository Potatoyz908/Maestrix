import { CANVAS_INITIAL_ORIGIN, CANVAS_MAX_ZOOM, CANVAS_MIN_ZOOM } from "../geometry/constants";
import type { Point, Rect } from "../geometry/types";

/**
 * Pure pan/zoom math (no DOM). `origin` is the canvas-space point shown at the
 * top-left of the screen, matching CanvasState.origin in Swift.
 * screen = (canvas - origin) * zoom
 */
export class Viewport {
  origin: Point = { ...CANVAS_INITIAL_ORIGIN };
  zoom = 1;

  toScreen(p: Point): Point {
    return { x: (p.x - this.origin.x) * this.zoom, y: (p.y - this.origin.y) * this.zoom };
  }
  toCanvas(p: Point): Point {
    return { x: p.x / this.zoom + this.origin.x, y: p.y / this.zoom + this.origin.y };
  }

  panByScreen(dx: number, dy: number): void {
    this.origin = { x: this.origin.x - dx / this.zoom, y: this.origin.y - dy / this.zoom };
  }

  /** Zoom keeping the canvas point under `anchorScreen` fixed. */
  zoomAt(anchorScreen: Point, factor: number): void {
    const before = this.toCanvas(anchorScreen);
    this.zoom = Math.min(CANVAS_MAX_ZOOM, Math.max(CANVAS_MIN_ZOOM, this.zoom * factor));
    this.origin = { x: before.x - anchorScreen.x / this.zoom, y: before.y - anchorScreen.y / this.zoom };
  }

  /** Visible canvas rect (+margin in canvas units) for viewport culling. */
  visibleRect(screenW: number, screenH: number, margin = 200): Rect {
    return {
      x: this.origin.x - margin,
      y: this.origin.y - margin,
      w: screenW / this.zoom + 2 * margin,
      h: screenH / this.zoom + 2 * margin,
    };
  }
}
