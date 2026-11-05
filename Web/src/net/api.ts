import { authHeaders, httpUrl } from "./client";
import {
  endpointsOf, toSwiftDate,
  type AgentDTO, type CanvasNodeDTO, type ConnectionLink, type FileEntry, type FloorDTO, type GitChange, type NodeContent, type NodeType,
  type SidebarLayout, type WorkspaceEntryDTO,
} from "./protocol";
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
export interface FilesApi { list(root: string, relPath: string): Promise<FileEntry[]> }
export interface GitApi { status(root: string): Promise<GitChange[]> }
export interface SidebarApi {
  workspaces(): Promise<WorkspaceEntryDTO[]>;
  layout(): Promise<SidebarLayout | undefined>;
  saveLayout(l: SidebarLayout): Promise<void>;
  floors(workspaceId?: string): Promise<FloorDTO[]>;
  agents(): Promise<AgentDTO[]>;
}
export interface Services { notes: NotesApi; connections: ConnectionsApi; nodes: NodesApi; files: FilesApi; git: GitApi; sidebar: SidebarApi }

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
        method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify({ ...link, a: endpointsOf(link)[0], b: endpointsOf(link)[1] }), // today's server reads a/b and infers the type from the nodes
      }));
      return (await r.json()) as { id: string };
    },
  };

  private async getJson<T>(path: string): Promise<T> {
    return (await (await this.ok(await this.f(httpUrl(path), { headers: authHeaders() }))).json()) as T;
  }
  files: FilesApi = {
    list: (root, rel) => this.getJson(`/api/files?root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel)}`),
  };
  git: GitApi = { status: (root) => this.getJson(`/api/git/status?root=${encodeURIComponent(root)}`) };
  sidebar: SidebarApi = {
    workspaces: () => this.getJson("/api/workspaces"),
    layout: async () => { try { return await this.getJson<SidebarLayout>("/api/sidebar-layout"); } catch { return undefined; } },
    saveLayout: async (l) => {
      await this.ok(await this.f(httpUrl("/api/sidebar-layout"), { method: "PUT", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(l) }));
    },
    floors: (ws) => this.getJson(`/api/floors${ws ? `?workspace=${encodeURIComponent(ws)}` : ""}`),
    agents: () => this.getJson("/api/agents"),
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
  files: FilesApi = {
    list: async (_root, rel) => (rel ? [{ name: "main.swift", path: `${rel}/main.swift`, isDir: false }] : [
      { name: "Sources", path: "Sources", isDir: true }, { name: "docs", path: "docs", isDir: true },
      { name: "README.md", path: "README.md", isDir: false }, { name: "Package.swift", path: "Package.swift", isDir: false },
    ]),
  };
  git: GitApi = { status: async () => [{ path: "README.md", status: "M" }, { path: "Sources/main.swift", status: "A" }, { path: "docs/notes.txt", status: "?" }] };
  sidebar: SidebarApi = {
    workspaces: async () => [
      { id: "11111111-0000-0000-0000-00000000000A", name: "Demo", workingDirectory: "/", icon: "folder", color: "blue", isPinned: true, locationType: "local", createdAt: "2026-01-01T00:00:00Z" },
      { id: "11111111-0000-0000-0000-00000000000B", name: "Scratch", workingDirectory: "/", icon: "folder", color: "green", isPinned: false, locationType: "local", createdAt: "2026-01-01T00:00:00Z" },
    ],
    layout: async () => undefined,
    saveLayout: async () => {},
    floors: async () => [{ id: "F1", name: "feat-x", branchName: "feat/x", worktreePath: "/tmp/x", createdAt: "2026-01-01T00:00:00Z" }],
    agents: async () => [{ name: "Pixel" }, { name: "Sentinela" }],
  };
  nodes: NodesApi = {
    create: async (req) => {
      const now = toSwiftDate(new Date());
      return { id: uuid(), frame: req.frame, content: req.content ?? {}, zIndex: 0, isLocked: false, createdAt: now, lastModifiedAt: now };
    },
  };
}
