import { authHeaders, httpUrl } from "./client";
import { toSwiftDate, type CanvasNodeDTO, type ConnectionLink, type NodeContent, type NodeType } from "./protocol";
import type { FrameTuple } from "../geometry/types";

/** Reject anything that could escape notes/ (the core must ALSO enforce this server-side). */
export function safeNoteName(name: string): string {
  if (!name || name.startsWith(".") || /[\/\\\u0000]/.test(name) || name.includes("..")) throw new Error(`invalid note name: ${JSON.stringify(name)}`);
  return name;
}

/** Note "name" = fileName without the .md extension. */
export const noteNameFromFile = (fileName: string): string => fileName.replace(/\.md$/i, "");

export interface NotesApi {
  get(name: string): Promise<string>;
  put(name: string, text: string): Promise<void>;
}
export interface ConnectionsApi {
  create(link: ConnectionLink): Promise<{ id: string }>;
}
export interface CreateNodeRequest { nodeType: NodeType; frame: FrameTuple; content?: NodeContent }
export interface NodesApi {
  create(req: CreateNodeRequest): Promise<CanvasNodeDTO>;
}
export interface Services { notes: NotesApi; connections: ConnectionsApi; nodes: NodesApi }

type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/** REST client for the core. Endpoints are PROPOSED (not yet served): see protocol.ts. */
export class HttpServices implements Services {
  constructor(private f: Fetch = (u, i) => fetch(u, i)) {}

  private async ok(r: Response): Promise<Response> {
    if (!r.ok) throw new Error(`HTTP ${r.status} ${r.url}`);
    return r;
  }

  notes: NotesApi = {
    get: async (name) => {
      const r = await this.f(httpUrl(`/api/notes/${encodeURIComponent(safeNoteName(name))}`), { headers: authHeaders() });
      if (r.status === 404) return "";
      return (await this.ok(r)).text();
    },
    put: async (name, text) => {
      await this.ok(await this.f(httpUrl(`/api/notes/${encodeURIComponent(safeNoteName(name))}`), {
        method: "PUT", headers: { ...authHeaders(), "Content-Type": "text/markdown; charset=utf-8" }, body: text,
      }));
    },
  };

  connections: ConnectionsApi = {
    create: async (link) => {
      const r = await this.ok(await this.f(httpUrl("/api/connections"), {
        method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(link),
      }));
      return (await r.json()) as { id: string };
    },
  };

  nodes: NodesApi = {
    create: async (req) => {
      const r = await this.ok(await this.f(httpUrl("/api/nodes"), {
        method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(req),
      }));
      return (await r.json()) as CanvasNodeDTO;
    },
  };
}

const uuid = () => crypto.randomUUID().toUpperCase(); // the core emits uppercase UUIDs

/** Offline stand-in (no ?port=): keeps everything in memory so the UI works standalone. */
export class MemoryServices implements Services {
  private store = new Map<string, string>();
  notes: NotesApi = {
    get: async (n) => this.store.get(safeNoteName(n)) ?? "",
    put: async (n, t) => { this.store.set(safeNoteName(n), t); },
  };
  connections: ConnectionsApi = { create: async () => ({ id: uuid() }) };
  nodes: NodesApi = {
    create: async (req) => {
      const now = toSwiftDate(new Date());
      return { id: uuid(), frame: req.frame, content: req.content ?? {}, zIndex: 0, isLocked: false, createdAt: now, lastModifiedAt: now };
    },
  };
}
