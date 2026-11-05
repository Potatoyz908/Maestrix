import type { Rect } from "../geometry/types";
import { DomNode, type DomNodeHost } from "../canvas/DomNode";
import type { PortalContent } from "../net/protocol";
import { PortalHistory, navigatePortal, normalizeUrl } from "./url";

/**
 * Browser node: nav bar (back / forward / reload / URL / open externally) + sandboxed iframe.
 * Limits (browser-imposed, cannot be worked around from here):
 *  - sites that send X-Frame-Options / CSP frame-ancestors render blank: use the ↗ button;
 *  - back/forward only track URL-bar navigations, not link clicks inside the frame;
 *  - storageScope (isolated/shared) has no effect on an iframe.
 */
export class PortalNode extends DomNode {
  private frame = document.createElement("iframe");
  private input = document.createElement("input");
  private back = document.createElement("button");
  private fwd = document.createElement("button");
  private history = new PortalHistory();
  private bar = document.createElement("div");

  constructor(id: string, private content: PortalContent, host: DomNodeHost, getFrame: () => Rect, private onChange: (c: PortalContent) => void) {
    super(id, content.name, host, getFrame, "pnode");
    const btn = (label: string, title: string, fn: () => void) => {
      const b = document.createElement("button");
      b.textContent = label; b.title = title; b.className = "pnode-btn";
      b.addEventListener("click", fn);
      return b;
    };
    this.back = btn("◀", "Back", () => this.go(this.history.back()));
    this.fwd = btn("▶", "Forward", () => this.go(this.history.forward()));
    const reload = btn("⟳", "Reload", () => this.reload());
    const ext = btn("↗", "Open in a new tab (for sites that refuse to be embedded)", () => {
      const u = this.history.current;
      if (u) window.open(u, "_blank", "noopener,noreferrer");
    });
    this.input.className = "pnode-url";
    this.input.placeholder = "Enter a URL…";
    this.input.spellcheck = false;
    this.input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") this.navigate(this.input.value); });
    this.bar.className = "pnode-bar";
    this.bar.append(this.back, this.fwd, reload, this.input, ext);

    this.frame.className = "pnode-frame";
    // allow-same-origin is safe ONLY because normalizeUrl refuses the shell's own origin.
    this.frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox");
    this.frame.referrerPolicy = "no-referrer"; // the shell URL carries ?token=: never leak it in Referer
    this.frame.addEventListener("load", () => this.setStatus("idle"));
    this.body.append(this.bar, this.frame);
    this.applyChrome();
    this.input.value = content.currentURL;
  }

  mount(): void {
    if (this.content.currentURL) {
      const r = normalizeUrl(this.content.currentURL, location.origin);
      if (r.ok) { this.history.visit(r.url); this.load(r.url); }
    }
    this.updateButtons();
  }

  private applyChrome(): void { this.bar.style.display = this.content.chromeHidden ? "none" : ""; }

  private navigate(raw: string): void {
    const r = normalizeUrl(raw, location.origin);
    this.input.classList.toggle("bad", !r.ok);
    this.input.title = r.ok ? "" : r.reason;
    if (!r.ok) return;
    this.history.visit(r.url);
    this.load(r.url);
    // currentURL and source.url._0 move together (both are Maestri fields).
    this.content = navigatePortal(this.content, r.url);
    this.onChange({ ...this.content, status: "idle" });
  }

  private go(url: string | undefined): void {
    if (!url) return;
    this.load(url);
    this.content = navigatePortal(this.content, url);
    this.onChange({ ...this.content, status: "idle" });
  }

  private reload(): void { const u = this.history.current; if (u) this.load(u); }

  private load(url: string): void {
    this.input.value = url;
    this.setStatus("loading");
    this.frame.src = url;
    this.updateButtons();
  }

  private setStatus(s: "idle" | "loading"): void {
    this.content = { ...this.content, status: s };
    this.el.classList.toggle("loading", s === "loading");
  }

  private updateButtons(): void { (this.back as HTMLButtonElement).disabled = !this.history.canBack; (this.fwd as HTMLButtonElement).disabled = !this.history.canForward; }

  override focus(): void { this.input.focus(); }
  protected onDispose(): void { this.frame.src = "about:blank"; }
}
