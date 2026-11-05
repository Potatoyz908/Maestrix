import { RESIZE_CURSOR, RESIZE_EDGES, resizeFrame, type ResizeEdge } from "../geometry/resize";
import type { PinSide } from "../geometry/pins";
import type { Rect } from "../geometry/types";
import { nodeBox } from "./nodeBox";
import type { Viewport } from "./viewport";

export interface DomNodeHost {
  viewport: Viewport;
  /** Proposed frame during a drag/resize; the canvas applies snapping. */
  onFrameChange(id: string, frame: Rect, kind: "move" | "resize"): void;
  onGestureEnd(id: string): void;
  onClose(id: string): void;
  onFocus(id: string): void;
  /** ctrl/meta + wheel over a node still zooms the canvas. */
  onZoomWheel(e: WheelEvent): void;
  /** Pointer pressed on a connection pin: start a rope drag. */
  onPinDown(id: string, side: PinSide, e: PointerEvent): void;
}

/** Shared chrome: titlebar (drag + close), 8 resize handles, 2 connection pins. Subclasses fill `body`. */
export abstract class DomNode {
  readonly el = document.createElement("div");
  protected readonly header = document.createElement("div");
  protected readonly headerLeft = document.createElement("div");
  protected readonly headerRight = document.createElement("div");
  protected readonly titleEl = document.createElement("span");
  protected readonly body = document.createElement("div");
  private lastBox = "";
  private disposed = false;

  constructor(readonly id: string, title: string, protected host: DomNodeHost, protected getFrame: () => Rect, kindClass: string) {
    this.el.className = `dnode ${kindClass}`;
    this.el.dataset.nodeId = id;
    this.header.className = "dnode-header";
    this.headerLeft.className = "dnode-hl";
    this.headerRight.className = "dnode-hr";
    this.titleEl.className = "dnode-title";
    this.titleEl.textContent = title;
    const close = document.createElement("button");
    close.className = "dnode-close";
    close.title = "Close";
    close.textContent = "×";
    close.addEventListener("pointerdown", (e) => e.stopPropagation());
    close.addEventListener("click", () => host.onClose(id));
    this.header.append(this.headerLeft, this.titleEl, this.headerRight, close);
    this.body.className = "dnode-body";
    this.el.append(this.header, this.body);
    for (const edge of RESIZE_EDGES) this.el.append(this.makeHandle(edge));
    for (const side of ["left", "right"] as const) this.el.append(this.makePin(side));

    this.bindHeaderDrag();
    this.el.addEventListener("pointerdown", () => host.onFocus(id), true);
    this.el.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); host.onZoomWheel(e); } else e.stopPropagation();
    }, { passive: false });
  }

  /** Called once `el` is in the DOM. */
  abstract mount(): void;
  protected abstract onDispose(): void;
  focus(): void {}

  setTitle(t: string): void { this.titleEl.textContent = t; }
  setSelected(on: boolean, z: number): void { this.el.classList.toggle("selected", on); this.el.style.zIndex = String(z); }

  /** Position/scale for the current viewport; skips DOM writes when nothing changed. */
  sync(frame: Rect, visible: boolean): void {
    this.el.style.display = visible ? "" : "none";
    if (!visible) return;
    const b = nodeBox(frame, this.host.viewport);
    const key = `${b.transform}|${b.width}|${b.height}`;
    if (key === this.lastBox) return;
    this.lastBox = key;
    this.el.style.transform = b.transform;
    this.el.style.width = b.width;
    this.el.style.height = b.height;
  }

  private gesture(target: HTMLElement, e: PointerEvent, propose: (dx: number, dy: number, start: Rect) => void): void {
    target.setPointerCapture(e.pointerId);
    const sx = e.clientX, sy = e.clientY;
    const start = { ...this.getFrame() };
    const move = (ev: PointerEvent) => propose((ev.clientX - sx) / this.host.viewport.zoom, (ev.clientY - sy) / this.host.viewport.zoom, start);
    const up = (ev: PointerEvent) => {
      target.releasePointerCapture(ev.pointerId);
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      this.host.onGestureEnd(this.id);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  }

  private bindHeaderDrag(): void {
    this.header.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || (e.target as HTMLElement).closest("button, input, .dnode-nodrag")) return;
      this.gesture(this.header, e, (dx, dy, s) => this.host.onFrameChange(this.id, { ...s, x: s.x + dx, y: s.y + dy }, "move"));
    });
  }

  private makeHandle(edge: ResizeEdge): HTMLElement {
    const h = document.createElement("div");
    h.className = `dnode-handle h-${edge}`;
    h.style.cursor = RESIZE_CURSOR[edge];
    h.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      this.gesture(h, e, (dx, dy, s) => this.host.onFrameChange(this.id, resizeFrame(s, edge, dx, dy), "resize"));
    });
    return h;
  }

  private makePin(side: PinSide): HTMLElement {
    const p = document.createElement("div");
    p.className = `pin pin-${side}`;
    p.dataset.nodeId = this.id;
    p.dataset.side = side;
    p.title = "Drag to another pin to connect";
    p.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      this.host.onPinDown(this.id, side, e);
    });
    return p;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.onDispose();
    this.el.remove();
  }
}
