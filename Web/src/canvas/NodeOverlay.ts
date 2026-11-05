import "./overlay.css";
import { intersects, type Rect } from "../geometry/types";
import type { PtyTransport } from "../net/ptySocket";
import { TerminalNode, type TerminalNodeHost } from "../terminal/TerminalNode";

export interface OverlayNode { id: string; frame: Rect; zIndex: number; title?: string }

/** DOM layer above the <canvas>: one TerminalNode per terminal, kept in sync with the viewport. */
export class NodeOverlay {
  readonly el = document.createElement("div");
  private nodes = new Map<string, TerminalNode>();

  constructor(host: HTMLElement, private nodeHost: TerminalNodeHost) {
    this.el.className = "overlay";
    host.appendChild(this.el);
  }

  has(id: string): boolean { return this.nodes.has(id); }

  add(n: OverlayNode, pty: PtyTransport, getFrame: () => Rect): void {
    if (this.nodes.has(n.id)) return;
    const t = new TerminalNode(n.id, n.title ?? n.id.slice(0, 8), pty, this.nodeHost, getFrame);
    this.nodes.set(n.id, t);
    this.el.appendChild(t.el);
    t.mount();
  }

  remove(id: string): void {
    this.nodes.get(id)?.dispose();
    this.nodes.delete(id);
  }

  focus(id: string): void { this.nodes.get(id)?.focus(); }

  /** Per-frame: position every node; hide those outside the (margin-extended) view. */
  sync(nodes: readonly OverlayNode[], view: Rect, selected: ReadonlySet<string>): void {
    for (const n of nodes) {
      const t = this.nodes.get(n.id);
      if (!t) continue;
      t.sync(n.frame, intersects(view, n.frame));
      t.setSelected(selected.has(n.id), n.zIndex);
    }
  }

  destroy(): void {
    for (const t of this.nodes.values()) t.dispose();
    this.nodes.clear();
    this.el.remove();
  }
}
