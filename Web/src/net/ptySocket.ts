import { wsUrl } from "./client";
import type { ClientPtyControl, ServerPtyControl } from "./protocol";

export type PtyStatus = "connecting" | "open" | "closed" | "error";
export interface StatusInfo { reconnected: boolean; exitCode?: number }

/** What TerminalNode needs from a PTY, so a fake can stand in until the core has /ws/pty. */
export interface PtyTransport {
  onData: ((bytes: Uint8Array) => void) | null;
  onStatus: ((status: PtyStatus, info: StatusInfo) => void) | null;
  write(data: string | Uint8Array): void;
  resize(cols: number, rows: number): void;
  close(): void;
}

/** Subset of the DOM WebSocket used here (lets tests inject a mock). */
export interface WebSocketLike {
  binaryType: string;
  readyState: number;
  send(data: string | ArrayBufferLike | ArrayBufferView): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

const OPEN = 1;

export interface PtySocketOptions {
  /** Full ws:// URL override; default is wsUrl(`/ws/pty/${id}`) (adds ?token=). */
  url?: string;
  factory?: (url: string) => WebSocketLike;
  reconnect?: boolean;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  /** For tests. */
  setTimer?: (fn: () => void, ms: number) => unknown;
}

/**
 * Client for WS /ws/pty/:terminalId.
 *  - server -> client: binary frames = raw PTY bytes (first the saved scrollback, then live);
 *    text frames = ServerPtyControl JSON (e.g. {type:"exit"}).
 *  - client -> server: binary frames = keystrokes; text frame {type:"resize",cols,rows}.
 * On reconnect the server replays scrollback again, so `status` carries `reconnected`
 * and the consumer should reset its terminal before the replay arrives.
 */
export class PtySocket implements PtyTransport {
  onData: ((bytes: Uint8Array) => void) | null = null;
  onStatus: ((status: PtyStatus, info: StatusInfo) => void) | null = null;

  private ws: WebSocketLike | null = null;
  private attempts = 0;
  private everOpened = false;
  private closedByUser = false;
  private exited = false;
  private lastSize: { cols: number; rows: number } | null = null;
  private readonly enc = new TextEncoder();
  private readonly opts: Required<Pick<PtySocketOptions, "reconnect" | "baseBackoffMs" | "maxBackoffMs">> & PtySocketOptions;

  constructor(readonly terminalId: string, opts: PtySocketOptions = {}) {
    this.opts = { reconnect: true, baseBackoffMs: 500, maxBackoffMs: 5000, ...opts };
    this.connect();
  }

  private connect(): void {
    const url = this.opts.url ?? wsUrl(`/ws/pty/${this.terminalId}`);
    const ws = (this.opts.factory ?? ((u) => new WebSocket(u) as unknown as WebSocketLike))(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.onStatus?.("connecting", { reconnected: this.everOpened });

    ws.onopen = () => {
      const reconnected = this.everOpened;
      this.everOpened = true;
      this.attempts = 0;
      this.onStatus?.("open", { reconnected });
      if (this.lastSize) this.sendControl({ type: "resize", ...this.lastSize });
    };
    ws.onmessage = (ev) => {
      const d = ev.data;
      if (typeof d === "string") return this.handleControl(d);
      if (d instanceof ArrayBuffer) this.onData?.(new Uint8Array(d));
      else if (ArrayBuffer.isView(d)) this.onData?.(new Uint8Array(d.buffer, d.byteOffset, d.byteLength));
    };
    ws.onerror = () => this.onStatus?.("error", { reconnected: this.everOpened });
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.onStatus?.("closed", { reconnected: this.everOpened });
      if (!this.closedByUser && !this.exited && this.opts.reconnect) this.scheduleReconnect();
    };
  }

  private handleControl(text: string): void {
    let msg: ServerPtyControl;
    try { msg = JSON.parse(text) as ServerPtyControl; } catch { return; }
    if (msg.type === "exit") {
      this.exited = true; // the shell ended: do not reconnect
      this.onStatus?.("closed", { reconnected: this.everOpened, exitCode: msg.code });
    }
  }

  private scheduleReconnect(): void {
    const delay = Math.min(this.opts.baseBackoffMs * 2 ** this.attempts, this.opts.maxBackoffMs);
    this.attempts++;
    (this.opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms)))(() => { if (!this.closedByUser) this.connect(); }, delay);
  }

  private sendControl(msg: ClientPtyControl): void {
    if (this.ws?.readyState === OPEN) this.ws.send(JSON.stringify(msg));
  }

  write(data: string | Uint8Array): void {
    if (this.ws?.readyState !== OPEN) return; // keystrokes while disconnected are dropped on purpose
    this.ws.send(typeof data === "string" ? this.enc.encode(data) : data);
  }

  /** Remembers the size so it is re-sent after (re)connect. */
  resize(cols: number, rows: number): void {
    this.lastSize = { cols, rows };
    this.sendControl({ type: "resize", cols, rows });
  }

  close(): void {
    this.closedByUser = true;
    this.ws?.close();
  }
}
