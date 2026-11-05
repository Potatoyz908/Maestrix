import type { ClientPtyControl, ServerEvent } from "./protocol";

/** Core base URL; the port is dynamic, injected via ?port= or same-origin (vite proxy). */
const params = new URLSearchParams(location.search);
const base = params.get("port") ? `127.0.0.1:${params.get("port")}` : location.host;

/**
 * Session token (core writes ~/.open-maestri/run/auth.token, env MAESTRI_AUTH_TOKEN).
 * Browsers can't set headers on WebSocket, so it travels as ?token=. The launcher
 * (WebKitGTK/Electron shell) must open the page as /?port=NNNN&token=XXXX.
 */
const token = params.get("token") ?? "";
const withToken = (path: string) => (token ? `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : path);

export const httpUrl = (path: string) => `http://${base}${withToken(path)}`;
export const wsUrl = (path: string) => `ws://${base}${withToken(path)}`;
/** For fetch(): prefer the Authorization header over the query string. */
export const authHeaders = (): Record<string, string> => (token ? { Authorization: `Bearer ${token}` } : {});

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
