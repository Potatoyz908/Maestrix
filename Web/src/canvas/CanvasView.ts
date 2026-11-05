import { Viewport } from "./viewport";
import { RopeSimulation, computeStaticCatenary } from "../geometry/rope";
import { snap, type GuideLine } from "../geometry/tileSnapping";
import { frameFromRect, intersects, type Point, type Rect } from "../geometry/types";
import { canConnect, facingSides, pinPoint, type PinSide } from "../geometry/pins";
import { CANVAS_GRID_SPACING } from "../geometry/constants";
import { linkFor, type NodeContent, type StickyNoteContent } from "../net/protocol";
import { MemoryServices, type Services } from "../net/api";
import type { PtyTransport } from "../net/ptySocket";
import { NodeOverlay } from "./NodeOverlay";
import type { DomNode, DomNodeHost } from "./DomNode";
import { DEFAULT_NOTE, isDomKind, modelFromDTO, type NodeModel } from "./model";
import { TerminalNode } from "../terminal/TerminalNode";
import { NoteNode } from "../note/NoteNode";

export type { NodeModel } from "./model";
export interface ConnectionModel { id: string; a: string; b: string; sideA?: PinSide; sideB?: PinSide; pending?: boolean }

const NOTE_SIZE = { w: 320, h: 240 };

/** 2D-canvas renderer (grid, ropes, plain nodes, guides) + DOM overlay for terminals/notes. */
export class CanvasView {
  readonly viewport = new Viewport();
  readonly ropes = new RopeSimulation();
  nodes: NodeModel[] = [];
  connections: ConnectionModel[] = [];

  services: Services = new MemoryServices();
  ptyFactory: (id: string) => PtyTransport = () => { throw new Error("ptyFactory not set"); };
  onNodeClosed?: (id: string) => void;
  onFrameCommit?: (id: string, frame: Rect) => void;
  /** Note color/preview changed: persist {stickyNote:{_0:meta}} via the core (updateContent). */
  onContentChange?: (id: string, content: NodeContent) => void;
  /** Rope settled: persist its 21 points ({type:"persistRope"}). */
  onRopeSettled?: (id: string, points: Point[]) => void;
  onError?: (msg: string) => void;

  private guides: GuideLine[] = [];
  private ctx: CanvasRenderingContext2D;
  private canvas = document.createElement("canvas");
  private dragging: { id: string; grab: Point } | null = null;
  private panning: Point | null = null;
  private pending: { id: string; side: PinSide; to: Point } | null = null;
  private selected = new Set<string>();
  private overlay: NodeOverlay;
  private nodeHost: DomNodeHost;
  private tmpSeq = 0;
  private raf = 0;

  constructor(private host: HTMLElement) {
    host.style.position = "relative";
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    this.overlay = new NodeOverlay(host);
    this.nodeHost = {
      viewport: this.viewport,
      onFrameChange: (id, frame, kind) => this.applyFrame(id, frame, kind === "move"),
      onGestureEnd: (id) => { this.guides = []; const n = this.find(id); if (n) this.onFrameCommit?.(id, n.frame); },
      onClose: (id) => this.closeNode(id),
      onFocus: (id) => this.select(id),
      onZoomWheel: (e) => this.viewport.zoomAt(this.pt(e), Math.exp(-e.deltaY * 0.01)),
      onPinDown: (id, side, e) => this.beginConnect(id, side, e),
    };
    this.ropes.onSleep = (m) => { for (const [id, pts] of m) if (!id.startsWith("pending-")) this.onRopeSettled?.(id, pts); };
    new ResizeObserver(() => this.resize()).observe(host);
    this.canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    this.canvas.addEventListener("pointerdown", (e) => this.onDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.onMove(e));
    this.canvas.addEventListener("pointerup", () => this.onUp());
    this.resize();
    this.loop();
  }

  // ---- graph -------------------------------------------------------------------------------

  find(id: string): NodeModel | undefined { return this.nodes.find((n) => n.id === id); }

  setGraph(nodes: NodeModel[], connections: ConnectionModel[]): void {
    this.nodes = [];
    for (const n of nodes) this.addNodeModel(n);
    this.connections = connections;
    for (const c of connections) {
      const p = this.anchors(c);
      if (p && !this.ropes.pointsFor(c.id)) this.ropes.addRope(c.id, p[0], p[1]);
    }
  }

  addNodeModel(n: NodeModel): void {
    this.nodes.push(n);
    const dom = this.createDom(n);
    if (dom) this.overlay.add(dom);
  }

  private createDom(n: NodeModel): DomNode | null {
    const frame = () => this.find(n.id)?.frame ?? n.frame;
    if (n.kind === "terminal") return new TerminalNode(n.id, n.title ?? n.id.slice(0, 8), this.ptyFactory(n.id), this.nodeHost, frame);
    if (n.kind === "note" && n.note) {
      return new NoteNode(n.id, n.note, this.services.notes, this.nodeHost, frame,
        (meta: StickyNoteContent) => { n.note = meta; this.onContentChange?.(n.id, { stickyNote: { _0: meta } }); });
    }
    return null;
  }

  /** ➕ Note: the server creates the node (POST /api/nodes); we place it at the viewport center. */
  async addNote(): Promise<void> {
    const g = CANVAS_GRID_SPACING;
    const c = this.viewport.toCanvas({ x: this.host.clientWidth / 2, y: this.host.clientHeight / 2 });
    const frame: Rect = { x: Math.round((c.x - NOTE_SIZE.w / 2) / g) * g, y: Math.round((c.y - NOTE_SIZE.h / 2) / g) * g, ...NOTE_SIZE };
    while (this.nodes.some((n) => n.frame.x === frame.x && n.frame.y === frame.y)) { frame.x += 2 * g; frame.y += 2 * g; } // don't stack exactly
    const fileName = this.nextNoteFileName();
    try {
      const dto = await this.services.nodes.create({ nodeType: "stickyNote", frame: frameFromRect(frame), content: { stickyNote: { _0: { ...DEFAULT_NOTE, fileName } } } });
      const m = modelFromDTO(dto);
      m.zIndex = Math.max(0, ...this.nodes.map((n) => n.zIndex)) + 1;
      this.addNodeModel(m);
      this.select(m.id);
      this.overlay.focus(m.id);
    } catch (err) {
      this.fail(`Could not create note: ${(err as Error).message}`);
    }
  }

  private nextNoteFileName(): string {
    const used = new Set(this.nodes.map((n) => n.note?.fileName));
    for (let i = 1; ; i++) if (!used.has(`Note ${i}.md`)) return `Note ${i}.md`;
  }

  closeNode(id: string): void {
    this.overlay.remove(id);
    this.nodes = this.nodes.filter((n) => n.id !== id);
    for (const c of this.connections.filter((c) => c.a === id || c.b === id)) this.ropes.removeRope(c.id);
    this.connections = this.connections.filter((c) => c.a !== id && c.b !== id);
    this.selected.delete(id);
    this.onNodeClosed?.(id);
  }

  private fail(msg: string): void { console.warn(msg); this.onError?.(msg); }

  // ---- connections (pins + ropes) ------------------------------------------------------------

  /** Rope end points: explicit pin sides if the user chose them, else the facing pair (recomputed as nodes move). */
  private anchors(c: ConnectionModel): [Point, Point] | undefined {
    const a = this.find(c.a), b = this.find(c.b);
    if (!a || !b) return undefined;
    const [fa, fb] = facingSides(a.frame, b.frame);
    return [pinPoint(a.frame, c.sideA ?? fa), pinPoint(b.frame, c.sideB ?? fb)];
  }

  private beginConnect(id: string, side: PinSide, e: PointerEvent): void {
    if (!this.find(id)) return;
    this.pending = { id, side, to: this.viewport.toCanvas(this.pt(e)) };
    this.overlay.setConnecting(true);
    const move = (ev: PointerEvent) => { if (this.pending) this.pending.to = this.viewport.toCanvas(this.pt(ev)); };
    const finish = (target: HTMLElement | null) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      const p = this.pending;
      this.pending = null;
      this.overlay.setConnecting(false);
      if (p && target?.dataset.nodeId) void this.connect(p.id, p.side, target.dataset.nodeId, target.dataset.side as PinSide);
    };
    const up = (ev: PointerEvent) => finish((document.elementFromPoint(ev.clientX, ev.clientY) as HTMLElement | null)?.closest<HTMLElement>(".pin") ?? null);
    const cancel = () => finish(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  }

  /** Optimistic: the rope appears at once with a temp id; POST /api/connections confirms (or we roll back). */
  async connect(aId: string, sideA: PinSide, bId: string, sideB: PinSide): Promise<void> {
    const a = this.find(aId), b = this.find(bId);
    if (!a || !b) return;
    const link = linkFor({ id: a.id, kind: a.kind }, { id: b.id, kind: b.kind });
    if (!link) return this.fail(`Can't connect ${a.kind} to ${b.kind}`);
    if (!canConnect(this.connections, a.id, b.id)) return this.fail("Already connected");

    const conn: ConnectionModel = { id: `pending-${++this.tmpSeq}`, a: a.id, b: b.id, sideA, sideB, pending: true };
    this.connections.push(conn);
    const p = this.anchors(conn)!;
    this.ropes.addRope(conn.id, p[0], p[1]);
    try {
      const { id } = await this.services.connections.create(link);
      if (!this.connections.includes(conn)) return; // a node was closed meanwhile
      const pts = this.ropes.pointsFor(conn.id);
      this.ropes.removeRope(conn.id);
      conn.id = id;
      conn.pending = false;
      const q = this.anchors(conn)!;
      this.ropes.addRope(id, q[0], q[1], pts);
      this.ropes.wake();
    } catch (err) {
      this.ropes.removeRope(conn.id);
      this.connections = this.connections.filter((c) => c !== conn);
      this.fail(`Connection failed: ${(err as Error).message}`);
    }
  }

  // ---- input / frames --------------------------------------------------------------------------

  private resize(): void {
    const dpr = devicePixelRatio || 1;
    this.canvas.width = this.host.clientWidth * dpr;
    this.canvas.height = this.host.clientHeight * dpr;
    this.canvas.style.cssText = "width:100%;height:100%;display:block";
  }

  /** Z-ordered hit test over canvas-drawn nodes (DOM nodes handle their own events). */
  hitTest(p: Point): NodeModel | undefined {
    return [...this.nodes].filter((n) => !isDomKind(n.kind)).sort((a, b) => b.zIndex - a.zIndex)
      .find((n) => p.x >= n.frame.x && p.x <= n.frame.x + n.frame.w && p.y >= n.frame.y && p.y <= n.frame.y + n.frame.h);
  }

  private pt(e: PointerEvent | WheelEvent): Point {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) this.viewport.zoomAt(this.pt(e), Math.exp(-e.deltaY * 0.01));
    else this.viewport.panByScreen(-e.deltaX, -e.deltaY);
  }

  private onDown(e: PointerEvent): void {
    this.canvas.setPointerCapture(e.pointerId);
    const s = this.pt(e);
    const c = this.viewport.toCanvas(s);
    const hit = this.hitTest(c);
    if (hit) { this.select(hit.id); this.dragging = { id: hit.id, grab: { x: c.x - hit.frame.x, y: c.y - hit.frame.y } }; }
    else { this.selected.clear(); this.panning = s; }
  }

  private onMove(e: PointerEvent): void {
    const s = this.pt(e);
    if (this.panning) {
      this.viewport.panByScreen(s.x - this.panning.x, s.y - this.panning.y);
      this.panning = s;
    } else if (this.dragging) {
      const n = this.find(this.dragging.id)!;
      const c = this.viewport.toCanvas(s);
      this.applyFrame(n.id, { ...n.frame, x: c.x - this.dragging.grab.x, y: c.y - this.dragging.grab.y }, true);
    }
  }

  private onUp(): void {
    if (this.dragging) { const n = this.find(this.dragging.id); if (n) this.onFrameCommit?.(n.id, n.frame); }
    this.dragging = null; this.panning = null; this.guides = [];
  }

  /** The one place a node's frame changes (drag, titlebar-drag, resize): snap, then re-anchor ropes. */
  private applyFrame(id: string, proposed: Rect, snapMove: boolean): void {
    const n = this.find(id);
    if (!n) return;
    if (snapMove) {
      const res = snap(proposed, this.nodes.filter((o) => o.id !== id).map((o) => o.frame));
      n.frame = { ...proposed, x: res.snappedOrigin.x, y: res.snappedOrigin.y };
      this.guides = res.guidelines;
    } else {
      n.frame = proposed; // resize already snapped to the 16-unit grid
      this.guides = [];
    }
    for (const c of this.connections) {
      if (c.a !== id && c.b !== id) continue;
      const p = this.anchors(c);
      if (p) this.ropes.updateAnchors(c.id, p[0], p[1]);
    }
  }

  private select(id: string): void {
    this.selected = new Set([id]);
    const n = this.find(id);
    const top = Math.max(0, ...this.nodes.map((m) => m.zIndex));
    if (n && n.zIndex < top) n.zIndex = top + 1; // raise to front
  }

  // ---- render ------------------------------------------------------------------------------------

  private loop = (): void => {
    this.ropes.tick();
    this.draw();
    const view = this.viewport.visibleRect(this.host.clientWidth, this.host.clientHeight);
    this.overlay.sync(this.nodes.filter((n) => isDomKind(n.kind)), view, this.selected);
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

    ctx.setTransform(dpr * vp.zoom, 0, 0, dpr * vp.zoom, -vp.origin.x * vp.zoom * dpr, -vp.origin.y * vp.zoom * dpr);
    const view = vp.visibleRect(w, h);

    ctx.fillStyle = "#e6e6e6";
    const step = 40;
    for (let x = Math.floor(view.x / step) * step; x < view.x + view.w; x += step)
      for (let y = Math.floor(view.y / step) * step; y < view.y + view.h; y += step) ctx.fillRect(x, y, 2, 2);

    // Ropes sit under the DOM nodes; pending ones are dashed until the core confirms them.
    ctx.lineJoin = "round";
    for (const c of this.connections) {
      const pts = this.ropes.pointsFor(c.id) ?? (() => { const p = this.anchors(c); return p ? computeStaticCatenary(p[0], p[1]) : []; })();
      if (pts.length < 2) continue;
      ctx.strokeStyle = "#888"; ctx.lineWidth = 2.5;
      ctx.setLineDash(c.pending ? [6, 6] : []);
      ctx.beginPath(); ctx.moveTo(pts[0]!.x, pts[0]!.y);
      for (const p of pts.slice(1)) ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Rope being dragged from a pin.
    if (this.pending) {
      const from = this.find(this.pending.id);
      if (from) {
        const a = pinPoint(from.frame, this.pending.side);
        const pts = computeStaticCatenary(a, this.pending.to);
        ctx.strokeStyle = "#3b82f6"; ctx.lineWidth = 2.5; ctx.setLineDash([8, 6]);
        ctx.beginPath(); ctx.moveTo(pts[0]!.x, pts[0]!.y);
        for (const p of pts.slice(1)) ctx.lineTo(p.x, p.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#3b82f6"; ctx.beginPath(); ctx.arc(this.pending.to.x, this.pending.to.y, 5, 0, Math.PI * 2); ctx.fill();
      }
    }

    for (const n of [...this.nodes].sort((a, b) => a.zIndex - b.zIndex)) {
      if (isDomKind(n.kind) || !intersects(view, n.frame)) continue;
      const f = n.frame;
      ctx.fillStyle = "#3a3a3a"; ctx.fillRect(f.x, f.y, f.w, f.h);
      ctx.strokeStyle = "#555"; ctx.lineWidth = 1; ctx.strokeRect(f.x, f.y, f.w, f.h);
      ctx.fillStyle = "#ddd"; ctx.font = "13px system-ui"; ctx.fillText(n.title ?? n.id.slice(0, 8), f.x + 10, f.y + 20);
    }

    ctx.strokeStyle = "#3b82f6"; ctx.lineWidth = 1 / vp.zoom;
    for (const g of this.guides) {
      ctx.beginPath();
      if (g.axis === "vertical") { ctx.moveTo(g.position, g.start); ctx.lineTo(g.position, g.end); }
      else { ctx.moveTo(g.start, g.position); ctx.lineTo(g.end, g.position); }
      ctx.stroke();
    }
  }
}
