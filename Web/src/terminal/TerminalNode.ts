import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";
import { RESIZE_CURSOR, RESIZE_EDGES, resizeFrame, type ResizeEdge } from "../geometry/resize";
import type { Rect } from "../geometry/types";
import type { PtyStatus, PtyTransport } from "../net/ptySocket";
import { nodeBox } from "../canvas/nodeBox";
import type { Viewport } from "../canvas/viewport";

export interface TerminalNodeHost {
  viewport: Viewport;
  /** Proposed new frame while dragging/resizing; the canvas applies snapping and returns nothing. */
  onFrameChange(id: string, frame: Rect, kind: "move" | "resize"): void;
  onGestureEnd(id: string): void;
  onClose(id: string): void;
  onFocus(id: string): void;
  /** ctrl/meta + wheel over a terminal should still zoom the canvas. */
  onZoomWheel(e: WheelEvent): void;
}

const STATUS_COLOR: Record<PtyStatus, string> = { connecting: "#e0a800", open: "#2ea043", closed: "#8b949e", error: "#f85149" };
const STATUS_LABEL: Record<PtyStatus, string> = { connecting: "connecting", open: "live", closed: "closed", error: "error" };

/** DOM terminal node: header (title/status/close) + xterm.js body + 8 resize handles. */
export class TerminalNode {
  readonly el = document.createElement("div");
  readonly term = new Terminal({ fontFamily: "monospace", fontSize: 13, cursorBlink: true, scrollback: 5000, theme: { background: "#1e1e1e" } });
  private fit = new FitAddon();
  private titleEl = document.createElement("span");
  private dot = document.createElement("span");
  private statusEl = document.createElement("span");
  private body = document.createElement("div");
  private ro?: ResizeObserver;
  private lastBox = "";
  private disposed = false;

  constructor(readonly id: string, title: string, private pty: PtyTransport, private host: TerminalNodeHost, private getFrame: () => Rect) {
    this.el.className = "tnode";
    this.el.dataset.nodeId = id;

    const header = document.createElement("div");
    header.className = "tnode-header";
    this.titleEl.className = "tnode-title";
    this.titleEl.textContent = title;
    this.dot.className = "tnode-dot";
    this.statusEl.className = "tnode-status";
    const close = document.createElement("button");
    close.className = "tnode-close";
    close.title = "Close";
    close.textContent = "×";
    close.addEventListener("pointerdown", (e) => e.stopPropagation());
    close.addEventListener("click", () => host.onClose(id));
    header.append(this.dot, this.titleEl, this.statusEl, close);

    this.body.className = "tnode-body";
    this.el.append(header, this.body);
    for (const edge of RESIZE_EDGES) this.el.append(this.makeHandle(edge));

    this.bindHeaderDrag(header);
    this.el.addEventListener("pointerdown", () => host.onFocus(id), true);
    // xterm owns plain wheel (scrollback); only ctrl/meta+wheel zooms the canvas.
    this.el.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); host.onZoomWheel(e); } else e.stopPropagation();
    }, { passive: false });

    this.setStatus("connecting");
    this.pty.onStatus = (s, info) => {
      this.setStatus(s, info.exitCode);
      if (s === "open" && info.reconnected) this.term.reset(); // server replays scrollback
    };
    this.pty.onData = (b) => this.term.write(b);
    this.term.onData((d) => this.pty.write(d));
    this.term.onResize(({ cols, rows }) => this.pty.resize(cols, rows));
  }

  /** Call after `el` is attached to the DOM (xterm needs layout to measure cells). */
  mount(): void {
    this.term.loadAddon(this.fit);
    this.term.open(this.body);
    try {
      const gl = new WebglAddon();
      gl.onContextLoss(() => gl.dispose());
      this.term.loadAddon(gl);
    } catch { /* keep the default renderer */ }
    // Layout size only changes on resize gestures (zoom is a CSS transform), so fit
    // here is independent of zoom.
    this.ro = new ResizeObserver(() => { try { this.fit.fit(); } catch { /* not laid out yet */ } });
    this.ro.observe(this.body);
    this.fit.fit();
    this.pty.resize(this.term.cols, this.term.rows);
  }

  private setStatus(s: PtyStatus, exitCode?: number): void {
    this.dot.style.background = STATUS_COLOR[s];
    this.statusEl.textContent = s === "closed" && exitCode !== undefined ? `exited (${exitCode})` : STATUS_LABEL[s];
  }

  setTitle(t: string): void { this.titleEl.textContent = t; }
  setSelected(on: boolean, z: number): void { this.el.classList.toggle("selected", on); this.el.style.zIndex = String(z); }
  focus(): void { this.term.focus(); }

  /** Position/scale to the current viewport; skips DOM writes when nothing changed. */
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

  private bindHeaderDrag(header: HTMLElement): void {
    header.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      header.setPointerCapture(e.pointerId);
      const sx = e.clientX, sy = e.clientY;
      const start = { ...this.getFrame() };
      const move = (ev: PointerEvent) => {
        const z = this.host.viewport.zoom;
        this.host.onFrameChange(this.id, { ...start, x: start.x + (ev.clientX - sx) / z, y: start.y + (ev.clientY - sy) / z }, "move");
      };
      const up = (ev: PointerEvent) => {
        header.releasePointerCapture(ev.pointerId);
        header.removeEventListener("pointermove", move);
        header.removeEventListener("pointerup", up);
        this.host.onGestureEnd(this.id);
      };
      header.addEventListener("pointermove", move);
      header.addEventListener("pointerup", up);
    });
  }

  private makeHandle(edge: ResizeEdge): HTMLElement {
    const h = document.createElement("div");
    h.className = `tnode-handle h-${edge}`;
    h.style.cursor = RESIZE_CURSOR[edge];
    h.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      h.setPointerCapture(e.pointerId);
      const sx = e.clientX, sy = e.clientY;
      const start = { ...this.getFrame() };
      const move = (ev: PointerEvent) => {
        const z = this.host.viewport.zoom;
        this.host.onFrameChange(this.id, resizeFrame(start, edge, (ev.clientX - sx) / z, (ev.clientY - sy) / z), "resize");
      };
      const up = (ev: PointerEvent) => {
        h.releasePointerCapture(ev.pointerId);
        h.removeEventListener("pointermove", move);
        h.removeEventListener("pointerup", up);
        this.host.onGestureEnd(this.id);
      };
      h.addEventListener("pointermove", move);
      h.addEventListener("pointerup", up);
    });
    return h;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.ro?.disconnect();
    this.pty.close();
    this.term.dispose();
    this.el.remove();
  }
}
