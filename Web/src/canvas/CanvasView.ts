import { Viewport } from "./viewport";
import { RopeSimulation, computeStaticCatenary } from "../geometry/rope";
import { snap, type GuideLine } from "../geometry/tileSnapping";
import { intersects, type Point, type Rect } from "../geometry/types";
import type { PtyTransport } from "../net/ptySocket";
import { NodeOverlay } from "./NodeOverlay";
import type { TerminalNodeHost } from "../terminal/TerminalNode";

export interface NodeModel { id: string; frame: Rect; zIndex: number; title?: string; kind?: "terminal" }
export interface ConnectionModel { id: string; a: string; b: string }

/** 2D-canvas renderer: grid, nodes (culled), ropes, snap guides; pan (drag) + zoom (wheel). */
export class CanvasView {
  readonly viewport = new Viewport();
  readonly ropes = new RopeSimulation();
  nodes: NodeModel[] = [];
  connections: ConnectionModel[] = [];
  private guides: GuideLine[] = [];
  private ctx: CanvasRenderingContext2D;
  private canvas = document.createElement("canvas");
  private dragging: { id: string; grab: Point } | null = null;
  private panning: Point | null = null;
  private raf = 0;
  private selected = new Set<string>();
  private overlay: NodeOverlay;
  /** Called after a node is closed from its titlebar (send `closeNode` to the core here). */
  onNodeClosed?: (id: string) => void;
  /** Called when a move/resize gesture ends (persist the frame via the core here). */
  onFrameCommit?: (id: string, frame: Rect) => void;
  /** Creates the PTY transport for a terminal node (PtySocket, or FakePty offline). */
  ptyFactory: (id: string) => PtyTransport = () => { throw new Error("ptyFactory not set"); };

  constructor(private host: HTMLElement) {
    host.style.position = "relative";
    host.appendChild(this.canvas);
    const self = this;
    const nodeHost: TerminalNodeHost = {
      viewport: this.viewport,
      onFrameChange: (id, frame, kind) => self.applyFrame(id, frame, kind === "move"),
      onGestureEnd: (id) => { self.guides = []; const n = self.nodes.find((n) => n.id === id); if (n) self.onFrameCommit?.(id, n.frame); },
      onClose: (id) => self.closeNode(id),
      onFocus: (id) => self.select(id),
      onZoomWheel: (e) => self.viewport.zoomAt(self.pt(e), Math.exp(-e.deltaY * 0.01)),
    };
    this.overlay = new NodeOverlay(host, nodeHost);
    this.ctx = this.canvas.getContext("2d")!;
    new ResizeObserver(() => this.resize()).observe(host);
    this.canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    this.canvas.addEventListener("pointerdown", (e) => this.onDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.onMove(e));
    this.canvas.addEventListener("pointerup", () => this.onUp());
    this.resize();
    this.loop();
  }

  setGraph(nodes: NodeModel[], connections: ConnectionModel[]): void {
    this.nodes = nodes;
    this.connections = connections;
    for (const n of nodes) {
      if (n.kind === "terminal" && !this.overlay.has(n.id)) {
        this.overlay.add(n, this.ptyFactory(n.id), () => this.nodes.find((m) => m.id === n.id)!.frame);
      }
    }
    for (const c of connections) {
      const [a, b] = this.anchors(c);
      if (a && b && !this.ropes.pointsFor(c.id)) this.ropes.addRope(c.id, a, b);
    }
  }

  private anchors(c: ConnectionModel): [Point?, Point?] {
    const center = (id: string) => {
      const n = this.nodes.find((n) => n.id === id);
      return n && { x: n.frame.x + n.frame.w / 2, y: n.frame.y + n.frame.h / 2 };
    };
    return [center(c.a), center(c.b)];
  }

  private resize(): void {
    const dpr = devicePixelRatio || 1;
    this.canvas.width = this.host.clientWidth * dpr;
    this.canvas.height = this.host.clientHeight * dpr;
    this.canvas.style.cssText = "width:100%;height:100%;display:block";
  }

  /** Z-ordered hit test: highest zIndex first (descending), like the Swift dual-cache. */
  hitTest(canvasPt: Point): NodeModel | undefined {
    return [...this.nodes].filter((n) => n.kind !== "terminal").sort((a, b) => b.zIndex - a.zIndex)
      .find((n) => canvasPt.x >= n.frame.x && canvasPt.x <= n.frame.x + n.frame.w && canvasPt.y >= n.frame.y && canvasPt.y <= n.frame.y + n.frame.h);
  }

  private pt(e: PointerEvent | WheelEvent): Point {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) this.viewport.zoomAt(this.pt(e), Math.exp(-e.deltaY * 0.01)); // pinch / ctrl+wheel
    else this.viewport.panByScreen(-e.deltaX, -e.deltaY);
  }

  private onDown(e: PointerEvent): void {
    this.canvas.setPointerCapture(e.pointerId);
    const s = this.pt(e);
    const hit = this.hitTest(this.viewport.toCanvas(s));
    if (hit) this.dragging = { id: hit.id, grab: { x: this.viewport.toCanvas(s).x - hit.frame.x, y: this.viewport.toCanvas(s).y - hit.frame.y } };
    else this.panning = s;
  }

  private onMove(e: PointerEvent): void {
    const s = this.pt(e);
    if (this.panning) {
      this.viewport.panByScreen(s.x - this.panning.x, s.y - this.panning.y);
      this.panning = s;
    } else if (this.dragging) {
      const n = this.nodes.find((n) => n.id === this.dragging!.id)!;
      const c = this.viewport.toCanvas(s);
      this.applyFrame(n.id, { ...n.frame, x: c.x - this.dragging.grab.x, y: c.y - this.dragging.grab.y }, true);
    }
  }

  /** Single place where a node's frame changes (drag, header-drag, resize): snap, then re-anchor ropes. */
  private applyFrame(id: string, proposed: Rect, snapMove: boolean): void {
    const n = this.nodes.find((n) => n.id === id);
    if (!n) return;
    if (snapMove) {
      const res = snap(proposed, this.nodes.filter((o) => o.id !== id).map((o) => o.frame));
      n.frame = { ...proposed, x: res.snappedOrigin.x, y: res.snappedOrigin.y };
      this.guides = res.guidelines;
    } else {
      n.frame = proposed; // resize already snapped to the 16-unit grid
      this.guides = [];
    }
    for (const conn of this.connections) {
      if (conn.a === id || conn.b === id) {
        const [a, b] = this.anchors(conn);
        if (a && b) this.ropes.updateAnchors(conn.id, a, b);
      }
    }
  }

  private select(id: string): void {
    this.selected = new Set([id]);
    const top = Math.max(0, ...this.nodes.map((n) => n.zIndex));
    const n = this.nodes.find((n) => n.id === id);
    if (n && n.zIndex < top) n.zIndex = top + 1; // raise to front
  }

  closeNode(id: string): void {
    this.overlay.remove(id);
    this.nodes = this.nodes.filter((n) => n.id !== id);
    for (const c of this.connections.filter((c) => c.a === id || c.b === id)) this.ropes.removeRope(c.id);
    this.connections = this.connections.filter((c) => c.a !== id && c.b !== id);
    this.selected.delete(id);
    this.onNodeClosed?.(id);
  }

  private onUp(): void { this.dragging = null; this.panning = null; this.guides = []; }

  private loop = (): void => {
    this.ropes.tick();
    this.draw();
    this.overlay.sync(this.nodes.filter((n) => n.kind === "terminal"), this.viewport.visibleRect(this.host.clientWidth, this.host.clientHeight), this.selected);
    this.raf = requestAnimationFrame(this.loop);
  };

  destroy(): void { cancelAnimationFrame(this.raf); this.overlay.destroy(); }

  private draw(): void {
    const { ctx, canvas, viewport: vp } = this;
    const dpr = devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = canvas.width / dpr, h = canvas.height / dpr;
    ctx.fillStyle = "#f2f2f2";
    ctx.fillRect(0, 0, w, h);

    // Everything below is drawn in canvas space.
    ctx.setTransform(dpr * vp.zoom, 0, 0, dpr * vp.zoom, -vp.origin.x * vp.zoom * dpr, -vp.origin.y * vp.zoom * dpr);
    const view = vp.visibleRect(w, h);

    // Dot grid (every 40 units).
    ctx.fillStyle = "#e6e6e6";
    const step = 40;
    for (let x = Math.floor(view.x / step) * step; x < view.x + view.w; x += step)
      for (let y = Math.floor(view.y / step) * step; y < view.y + view.h; y += step) ctx.fillRect(x, y, 2, 2);

    // Ropes (animated points, static catenary as fallback).
    ctx.strokeStyle = "#888"; ctx.lineWidth = 2; ctx.lineJoin = "round";
    for (const c of this.connections) {
      const pts = this.ropes.pointsFor(c.id) ?? (() => { const [a, b] = this.anchors(c); return a && b ? computeStaticCatenary(a, b) : []; })();
      if (pts.length < 2) continue;
      ctx.beginPath(); ctx.moveTo(pts[0]!.x, pts[0]!.y);
      for (const p of pts.slice(1)) ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }

    // Nodes: viewport culling (+200 margin) and ascending z-order.
    for (const n of [...this.nodes].sort((a, b) => a.zIndex - b.zIndex)) {
      if (n.kind === "terminal" || !intersects(view, n.frame)) continue; // terminals are DOM
      const f = n.frame;
      ctx.fillStyle = "#1e1e1e"; ctx.fillRect(f.x, f.y, f.w, f.h);
      ctx.strokeStyle = "#555"; ctx.lineWidth = 1; ctx.strokeRect(f.x, f.y, f.w, f.h);
      ctx.fillStyle = "#ddd"; ctx.font = "13px system-ui"; ctx.fillText(n.title ?? n.id.slice(0, 8), f.x + 10, f.y + 20);
    }

    // Snap guides.
    ctx.strokeStyle = "#3b82f6"; ctx.lineWidth = 1 / vp.zoom;
    for (const g of this.guides) {
      ctx.beginPath();
      if (g.axis === "vertical") { ctx.moveTo(g.position, g.start); ctx.lineTo(g.position, g.end); }
      else { ctx.moveTo(g.start, g.position); ctx.lineTo(g.end, g.position); }
      ctx.stroke();
    }
  }
}
