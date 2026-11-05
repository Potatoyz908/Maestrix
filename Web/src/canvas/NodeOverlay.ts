import "./overlay.css";
import { intersects, type Rect } from "../geometry/types";
import type { DomNode } from "./DomNode";
import type { NodeModel } from "./model";

/** DOM layer above the <canvas>: one DomNode per terminal/note, synced to the viewport each frame. */
export class NodeOverlay {
  readonly el = document.createElement("div");
  private nodes = new Map<string, DomNode>();

  constructor(host: HTMLElement) {
    this.el.className = "overlay";
    host.appendChild(this.el);
  }

  has(id: string): boolean { return this.nodes.has(id); }
  get(id: string): DomNode | undefined { return this.nodes.get(id); }

  add(node: DomNode): void {
    if (this.nodes.has(node.id)) return;
    this.nodes.set(node.id, node);
    this.el.appendChild(node.el);
    node.mount();
  }

  remove(id: string): void { this.nodes.get(id)?.dispose(); this.nodes.delete(id); }
  focus(id: string): void { this.nodes.get(id)?.focus(); }
  setConnecting(on: boolean): void { this.el.classList.toggle("connecting", on); }

  sync(nodes: readonly NodeModel[], view: Rect, selected: ReadonlySet<string>): void {
    for (const n of nodes) {
      const t = this.nodes.get(n.id);
      if (!t) continue;
      t.sync(n.frame, intersects(view, n.frame));
      t.setSelected(selected.has(n.id), n.zIndex);
    }
  }

  destroy(): void { for (const t of this.nodes.values()) t.dispose(); this.nodes.clear(); this.el.remove(); }
}
