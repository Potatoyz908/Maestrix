import { describe, expect, it } from "vitest";
import { PtySocket, type WebSocketLike } from "../src/net/ptySocket";
import { nodeType, normId, sameId, toSwiftDate } from "../src/net/protocol";
import { resizeFrame } from "../src/geometry/resize";
import { nodeBox } from "../src/canvas/nodeBox";

class MockWs implements WebSocketLike {
  binaryType = ""; readyState = 0; sent: unknown[] = [];
  onopen: ((e: unknown) => void) | null = null; onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: unknown) => void) | null = null; onerror: ((e: unknown) => void) | null = null;
  constructor(readonly url: string) {}
  send(d: unknown) { this.sent.push(d); }
  close() { this.readyState = 3; this.onclose?.({}); }
  open() { this.readyState = 1; this.onopen?.({}); }
}

function setup() {
  const sockets: MockWs[] = [];
  const timers: Array<{ fn: () => void; ms: number }> = [];
  const pty = new PtySocket("abc", {
    url: "ws://x/ws/pty/abc",
    factory: (u) => { const w = new MockWs(u); sockets.push(w); return w; },
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return 0; },
  });
  const statuses: string[] = [];
  pty.onStatus = (s, i) => statuses.push(`${s}${i.reconnected ? "+r" : ""}`);
  return { pty, sockets, timers, statuses };
}

describe("PtySocket", () => {
  it("receives binary bytes, sends keystrokes as binary and resize as JSON text", () => {
    const { pty, sockets } = setup();
    const ws = sockets[0]!;
    expect(ws.binaryType).toBe("arraybuffer");
    const got: number[][] = [];
    pty.onData = (b) => got.push([...b]);
    ws.open();
    ws.onmessage?.({ data: new Uint8Array([104, 105]).buffer });
    expect(got).toEqual([[104, 105]]);
    pty.write("ls\r");
    expect([...(ws.sent[0] as Uint8Array)]).toEqual([108, 115, 13]);
    pty.resize(120, 30);
    expect(JSON.parse(ws.sent[1] as string)).toEqual({ type: "resize", cols: 120, rows: 30 });
  });

  it("queues the last size and re-sends it on open; drops keystrokes while closed", () => {
    const { pty, sockets } = setup();
    pty.resize(80, 24); // before open: not sent yet
    pty.write("x");
    expect(sockets[0]!.sent).toEqual([]);
    sockets[0]!.open();
    expect(JSON.parse(sockets[0]!.sent[0] as string)).toEqual({ type: "resize", cols: 80, rows: 24 });
  });

  it("reconnects with backoff and flags the reconnect so the UI can reset before the replay", () => {
    const { sockets, timers, statuses } = setup();
    sockets[0]!.open();
    sockets[0]!.close(); // network drop
    expect(timers).toHaveLength(1);
    expect(timers[0]!.ms).toBe(500);
    timers[0]!.fn();
    expect(sockets).toHaveLength(2);
    sockets[1]!.open();
    expect(statuses).toContain("open+r");
  });

  it("does not reconnect after an exit frame or a user close", () => {
    const a = setup();
    a.sockets[0]!.open();
    a.sockets[0]!.onmessage?.({ data: JSON.stringify({ type: "exit", code: 0 }) });
    a.sockets[0]!.close();
    expect(a.timers).toHaveLength(0);
    const b = setup();
    b.pty.close();
    expect(b.timers).toHaveLength(0);
  });
});

describe("protocol helpers", () => {
  it("toSwiftDate drops fractional seconds", () => {
    expect(toSwiftDate("2026-11-05T15:27:28.123Z")).toBe("2026-11-05T15:27:28Z");
    expect(toSwiftDate(new Date(Date.UTC(2026, 10, 5, 15, 27, 28)))).toBe("2026-11-05T15:27:28Z");
  });
  it("ids compare case-insensitively", () => {
    expect(sameId("abcd-ef", "ABCD-EF")).toBe(true);
    expect(normId("abc")).toBe("ABC");
  });
  it("nodeType finds the wrapper key", () => {
    expect(nodeType({ stickyNote: { _0: {} } })).toBe("stickyNote");
  });
});

describe("resizeFrame", () => {
  const f = { x: 100, y: 100, w: 400, h: 300 };
  it("right/bottom grow and snap to the 16 grid", () => {
    // the active EDGE snaps (right 500+21 -> 528, bottom 400+5 -> 400), as in applyResizeOnCanvas
    expect(resizeFrame(f, "bottomRight", 21, 5)).toEqual({ x: 100, y: 100, w: 428, h: 300 });
  });
  it("left keeps the right edge fixed", () => {
    const r = resizeFrame(f, "left", -30, 0, false);
    expect(r.x + r.w).toBe(500);
    expect(r.w).toBe(430);
  });
  it("clamps to the 160x80 minimum", () => {
    const r = resizeFrame(f, "topLeft", 999, 999);
    expect(r.w).toBe(160);
    expect(r.h).toBe(80);
    expect(r.x + r.w).toBe(500);
    expect(r.y + r.h).toBe(400);
  });
});

describe("nodeBox", () => {
  it("keeps logical size and moves/scales via transform", () => {
    const b = nodeBox({ x: 150, y: 250, w: 480, h: 288 }, { origin: { x: 100, y: 200 }, zoom: 2 });
    expect(b).toEqual({ transform: "translate(100px, 100px) scale(2)", width: "480px", height: "288px" });
  });
});
