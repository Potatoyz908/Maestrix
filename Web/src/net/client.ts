import type { ClientPtyControl, ServerEvent } from "./protocol";

/** Core base URL; the port is dynamic, injected via ?port= or same-origin (vite proxy). */
const params = new URLSearchParams(location.search);
const base = params.get("port") ? `127.0.0.1:${params.get("port")}` : location.host;

export const httpUrl = (path: string) => `http://${base}${path}`;
export const wsUrl = (path: string) => `ws://${base}${path}`;

export function openEvents(workspaceId: string, onEvent: (e: ServerEvent) => void): WebSocket {
  const ws = new WebSocket(wsUrl(`/ws/events?workspace=${encodeURIComponent(workspaceId)}`));
  ws.onmessage = (m) => typeof m.data === "string" && onEvent(JSON.parse(m.data) as ServerEvent);
  return ws;
}

export function openPty(terminalId: string): { ws: WebSocket; resize: (cols: number, rows: number) => void } {
  const ws = new WebSocket(wsUrl(`/ws/pty/${terminalId}`));
  ws.binaryType = "arraybuffer";
  const resize = (cols: number, rows: number) => {
    const msg: ClientPtyControl = { type: "resize", cols, rows };
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  };
  return { ws, resize };
}
