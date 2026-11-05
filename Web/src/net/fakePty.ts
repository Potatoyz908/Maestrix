import type { PtyStatus, PtyTransport, StatusInfo } from "./ptySocket";

/** Offline stand-in: a tiny line-echo "shell" so the UI is testable without the core. */
export class FakePty implements PtyTransport {
  onData: ((b: Uint8Array) => void) | null = null;
  onStatus: ((s: PtyStatus, i: StatusInfo) => void) | null = null;
  private line = "";
  private enc = new TextEncoder();

  constructor(private name: string) {
    setTimeout(() => {
      this.onStatus?.("open", { reconnected: false });
      this.out(`\x1b[2m[fake pty: ${name} - core /ws/pty not connected]\x1b[0m\r\n$ `);
    }, 0);
  }
  private out(s: string) { this.onData?.(this.enc.encode(s)); }
  write(d: string | Uint8Array): void {
    for (const ch of typeof d === "string" ? d : new TextDecoder().decode(d)) {
      if (ch === "\r") { this.out(`\r\n${this.line ? `echo: ${this.line}\r\n` : ""}$ `); this.line = ""; }
      else if (ch === "\x7f") { if (this.line) { this.line = this.line.slice(0, -1); this.out("\b \b"); } }
      else if (ch >= " ") { this.line += ch; this.out(ch); }
    }
  }
  resize(): void {}
  close(): void { this.onStatus?.("closed", { reconnected: false }); }
}
