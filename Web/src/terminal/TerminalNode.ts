import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";
import type { Rect } from "../geometry/types";
import type { PtyStatus, PtyTransport } from "../net/ptySocket";
import { DomNode, type DomNodeHost } from "../canvas/DomNode";

const STATUS_COLOR: Record<PtyStatus, string> = { connecting: "#e0a800", open: "#2ea043", closed: "#8b949e", error: "#f85149" };
const STATUS_LABEL: Record<PtyStatus, string> = { connecting: "connecting", open: "live", closed: "closed", error: "error" };

/** Terminal node: xterm.js body bound to a PTY transport, with a status dot in the titlebar. */
export class TerminalNode extends DomNode {
  readonly term = new Terminal({ fontFamily: "monospace", fontSize: 13, cursorBlink: true, scrollback: 5000, theme: { background: "#1e1e1e" } });
  private fit = new FitAddon();
  private dot = document.createElement("span");
  private statusEl = document.createElement("span");
  private ro?: ResizeObserver;

  constructor(id: string, title: string, private pty: PtyTransport, host: DomNodeHost, getFrame: () => Rect) {
    super(id, title, host, getFrame, "tnode");
    this.dot.className = "tnode-dot";
    this.statusEl.className = "tnode-status";
    this.headerLeft.append(this.dot);
    this.headerRight.append(this.statusEl);
    this.body.classList.add("tnode-body");
    this.setStatus("connecting");

    this.pty.onStatus = (s, info) => {
      this.setStatus(s, info.exitCode);
      if (s === "open" && info.reconnected) this.term.reset(); // server replays scrollback
    };
    this.pty.onData = (b) => this.term.write(b);
    this.term.onData((d) => this.pty.write(d));
    this.term.onResize(({ cols, rows }) => this.pty.resize(cols, rows));
  }

  mount(): void {
    this.term.loadAddon(this.fit);
    this.term.open(this.body);
    try {
      const gl = new WebglAddon();
      gl.onContextLoss(() => gl.dispose());
      this.term.loadAddon(gl);
    } catch { /* keep the default renderer */ }
    // Layout size only changes on resize gestures (zoom is a CSS transform), so fit is zoom-independent.
    this.ro = new ResizeObserver(() => { try { this.fit.fit(); } catch { /* not laid out yet */ } });
    this.ro.observe(this.body);
    this.fit.fit();
    this.pty.resize(this.term.cols, this.term.rows);
  }

  private setStatus(s: PtyStatus, exitCode?: number): void {
    this.dot.style.background = STATUS_COLOR[s];
    this.statusEl.textContent = s === "closed" && exitCode !== undefined ? `exited (${exitCode})` : STATUS_LABEL[s];
  }

  override focus(): void { this.term.focus(); }

  protected onDispose(): void {
    this.ro?.disconnect();
    this.pty.close();
    this.term.dispose();
  }
}
