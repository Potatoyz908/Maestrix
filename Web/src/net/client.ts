import type { ServerEvent } from "./protocol";

/** Evaluated lazily so this module can be imported outside a browser (tests). */
const params = () => new URLSearchParams(typeof location === "undefined" ? "" : location.search);
/** Core address: ?port= or ?corePort= (dynamic port, loopback only) or same-origin (vite dev proxy). */
const base = () => {
  const p = params();
  const port = p.get("port") ?? p.get("corePort");
  return port ? `127.0.0.1:${port}` : typeof location === "undefined" ? "127.0.0.1" : location.host;
};

/**
 * Session token (core: ~/.open-maestri/run/auth.token, env MAESTRI_AUTH_TOKEN).
 * Browsers can't set headers on a WebSocket, so it travels as ?token= (the Architect
 * notes this can leak into logs; the core must not log query strings, and a one-shot
 * ticket obtained via fetch + Authorization is the better long-term option).
 * The launcher (WebKitGTK/Electron) opens the page as /?port=NNNN&token=XXXX.
 */
const token = () => params().get("token") ?? "";
const withToken = (path: string) => (token() ? `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token())}` : path);

export const httpUrl = (path: string) => `http://${base()}${path}`; // fetch(): use authHeaders(), not the query
export const wsUrl = (path: string) => `ws://${base()}${withToken(path)}`;
export const authHeaders = (): Record<string, string> => (token() ? { Authorization: `Bearer ${token()}` } : {});

export function openEvents(workspaceId: string, onEvent: (e: ServerEvent) => void): WebSocket {
  const ws = new WebSocket(wsUrl(`/ws/events?workspace=${encodeURIComponent(workspaceId)}`));
  ws.onmessage = (m) => typeof m.data === "string" && onEvent(JSON.parse(m.data) as ServerEvent);
  return ws;
}
