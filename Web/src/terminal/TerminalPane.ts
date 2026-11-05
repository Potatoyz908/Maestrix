import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";
import { openPty } from "../net/client";

/** One xterm.js instance bound to a PTY websocket on the Swift core. */
export class TerminalPane {
  readonly term = new Terminal({ fontFamily: "monospace", fontSize: 13, cursorBlink: true, scrollback: 5000 });
  private fit = new FitAddon();

  constructor(readonly terminalId: string, host: HTMLElement) {
    this.term.loadAddon(this.fit);
    this.term.open(host);
    try {
      const gl = new WebglAddon();
      gl.onContextLoss(() => gl.dispose()); // falls back to the DOM/canvas renderer
      this.term.loadAddon(gl);
    } catch { /* WebGL unavailable: keep default renderer */ }
    this.fit.fit();

    const { ws, resize } = openPty(terminalId);
    const enc = new TextEncoder();
    ws.onopen = () => resize(this.term.cols, this.term.rows);
    ws.onmessage = (m) => this.term.write(new Uint8Array(m.data as ArrayBuffer));
    this.term.onData((d) => ws.readyState === WebSocket.OPEN && ws.send(enc.encode(d)));
    this.term.onResize(({ cols, rows }) => resize(cols, rows));
    new ResizeObserver(() => this.fit.fit()).observe(host);
  }
}
