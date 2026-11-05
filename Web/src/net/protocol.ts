/**
 * DRAFT wire protocol between the Swift core and the web shell. The core does NOT
 * serve /api or /ws yet (only POST /cli exists); this file is the proposal.
 *
 *   POST /cli  body {"args":[...]}  header X-Terminal-ID  -> text/plain   (existing)
 *   GET  /api/workspaces/:id        -> WorkspaceDocument (schemaVersion 2)
 *   GET|PUT /api/notes/:name        -> note markdown. :name = fileName WITHOUT ".md" (the core appends it),
 *                                      percent-encoded; text lives in notes/{name}.md, not workspace.json
 *   POST /api/nodes                 -> create a node (body CreateNodeRequest) -> CanvasNodeDTO
 *   POST /api/connections           -> body ConnectionLink (kind-discriminated) -> {id}
 *
 * SECURITY (blocking, core side): every /api and TCP /cli request needs
 * "Authorization: Bearer <token>" (constant-time compare), Origin/Host validation
 * (anti DNS-rebinding) and NO "Access-Control-Allow-Origin: *". The web shell already
 * sends the Bearer header on every fetch.
 *   WS   /ws/events?workspace=:id   -> ServerEvent (text JSON frames)
 *   WS   /ws/pty/:terminalId        -> binary = raw PTY bytes; text = ClientPtyControl / ServerPtyControl
 *
 * Contract rules verified by the Architect against a real Swift encode (schemaVersion 2):
 *  - UUIDs are UPPERCASE on the wire: compare with sameId(), never ===.
 *  - Dates are ISO8601 WITHOUT fractional seconds: use toSwiftDate().
 *  - canvasOrigin is an ARRAY [x, y], not {x, y}.
 *  - frame is [[x,y],[w,h]]; content is {type:{_0:payload}}.
 */
import type { FrameTuple } from "../geometry/types";

export const NODE_TYPES = ["terminal", "stickyNote", "portal", "fileTree", "text", "shape", "stroke", "freehand"] as const;
export type NodeType = (typeof NODE_TYPES)[number];

/** content: exactly one key (a NodeType) whose value is {_0: payload}. */
export type NodeContent = Partial<Record<NodeType, { _0: unknown }>>;

export interface CanvasNodeDTO {
  id: string;
  frame: FrameTuple;
  content: NodeContent;
  zIndex: number;
  isLocked: boolean;
  /** ISO8601 without fractions, e.g. 2026-11-05T15:27:28Z. Kept for lossless round-trips. */
  createdAt: string;
  lastModifiedAt: string;
}

/** StickyNoteContent as encoded by Swift. The note TEXT is not here (see /api/notes/:name). */
export interface StickyNoteContent {
  color: string; // hex or a named color
  fileName: string; // no path
  fontSize: number;
  hasCustomName: boolean;
  isPreviewing: boolean;
  storageMode: { managed: Record<string, never> } | { custom: { _0: string } };
}

/** The Swift decoder uses .iso8601, which rejects Date.toISOString()'s ".000Z". */
export const toSwiftDate = (d: Date | string): string => new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z");

/** Server UUIDs are uppercase; the browser may hold either case. */
export const normId = (id: string): string => id.toUpperCase();
export const sameId = (a: string, b: string): boolean => normId(a) === normId(b);

/** The node's type key, e.g. "terminal". */
export const nodeType = (c: NodeContent): NodeType | undefined => NODE_TYPES.find((t) => c[t] !== undefined);

/**
 * Connections are NOT [a,b]: workspace.json keeps 6 typed arrays, each item
 * {id, createdAt, <id pair>, ropePoints:[[x,y]*21]}. A/B order is preserved by the core.
 */
export type ConnectionLink =
  | { kind: "terminal"; terminalIdA: string; terminalIdB: string }
  | { kind: "note"; terminalId: string; noteNodeId: string }
  | { kind: "portal"; terminalId: string; portalNodeId: string }
  | { kind: "portalToPortal"; portalIdA: string; portalIdB: string }
  | { kind: "noteToNote"; noteNodeIdA: string; noteNodeIdB: string }
  | { kind: "crossFloor"; nodeIdA: string; floorIdA?: string; nodeIdB: string; floorIdB?: string };

export type ConnectionDTO = ConnectionLink & { id: string; createdAt: string; ropePoints: [number, number][] };

/** The two node ids a link joins, in the order stored by the core. */
export function endpointsOf(l: ConnectionLink): [string, string] {
  switch (l.kind) {
    case "terminal": return [l.terminalIdA, l.terminalIdB];
    case "note": return [l.terminalId, l.noteNodeId];
    case "portal": return [l.terminalId, l.portalNodeId];
    case "portalToPortal": return [l.portalIdA, l.portalIdB];
    case "noteToNote": return [l.noteNodeIdA, l.noteNodeIdB];
    case "crossFloor": return [l.nodeIdA, l.nodeIdB];
  }
}

/**
 * Link for a gesture between two nodes the web shell knows how to draw.
 * terminal<->note is normalized to (terminal, note) whichever pin the user started from.
 * Returns null for pairs the core has no array for (e.g. note<->portal) or kinds not built yet.
 */
export function linkFor(a: { id: string; kind?: string }, b: { id: string; kind?: string }): ConnectionLink | null {
  const k = `${a.kind}|${b.kind}`;
  if (k === "terminal|terminal") return { kind: "terminal", terminalIdA: a.id, terminalIdB: b.id };
  if (k === "note|note") return { kind: "noteToNote", noteNodeIdA: a.id, noteNodeIdB: b.id };
  if (k === "terminal|note") return { kind: "note", terminalId: a.id, noteNodeId: b.id };
  if (k === "note|terminal") return { kind: "note", terminalId: b.id, noteNodeId: a.id };
  if (k === "terminal|portal") return { kind: "portal", terminalId: a.id, portalNodeId: b.id };
  if (k === "portal|terminal") return { kind: "portal", terminalId: b.id, portalNodeId: a.id };
  if (k === "portal|portal") return { kind: "portalToPortal", portalIdA: a.id, portalIdB: b.id };
  return null;
}

export type ServerEvent =
  | { type: "nodeAdded"; node: CanvasNodeDTO }
  | { type: "nodeUpdated"; node: CanvasNodeDTO }
  | { type: "nodeRemoved"; id: string }
  | { type: "connectionAdded"; connection: ConnectionDTO }
  | { type: "connectionRemoved"; id: string };

/**
 * Nodes are created/removed/edited by the server only (via WorkspaceManager, so
 * isDirty/snapshot/PersistenceManager rules hold); the client just asks.
 * add/remove/updateContent are PROPOSED — the core does not implement them yet.
 */
export type ClientCommand =
  | { type: "moveNode"; id: string; frame: FrameTuple }
  | { type: "resizeNode"; id: string; frame: FrameTuple }
  | { type: "selectNodes"; ids: string[] }
  | { type: "persistRope"; id: string; points: [number, number][] }
  | { type: "addNode"; nodeType: NodeType; frame: FrameTuple; content?: NodeContent }
  | { type: "removeNode"; id: string }
  | { type: "updateContent"; id: string; content: NodeContent }
  | { type: "addConnection"; link: ConnectionLink }
  | { type: "removeConnection"; id: string };

/** Text frames the server may send on /ws/pty (PROPOSED, needs Sentinela ack). */
export type ServerPtyControl = { type: "exit"; code: number };

export type ClientPtyControl = { type: "resize"; cols: number; rows: number };

// ---- Phase 4 DTOs (verified against Sources/Workspace/Models/NodeContent.swift) ------------------

/** Portal: NO url/isLoading keys. isLoading == (status === "loading"); URL is currentURL, kept coherent with source.url._0. */
export interface PortalContent {
  id: string;
  name: string;
  currentURL: string;
  source: { url: { _0: string } } | Record<string, never>; // {} == none
  status: "idle" | "loading";
  chromeHidden: boolean;
  storageScope: "isolated" | "shared";
}

/** FileTree persists ONLY these 3 keys. expandedPaths is runtime state: never add it here. */
export interface FileTreeContent { name: string; rootPath: string; viewMode: "list" | "grid" }

/** ~/.open-maestri/sidebar-layout.json (NOT in workspace.json). GET/PUT /api/sidebar-layout. */
export interface SidebarGroup { id: string; name: string; isCollapsed: boolean; items: string[] }
export interface SidebarLayout { schemaVersion: 2; topLevelItems: string[]; groups: SidebarGroup[] }

/** manifest.json WorkspaceEntry (GET /api/workspaces, PROPOSED). */
export interface WorkspaceEntryDTO {
  id: string; name: string; workingDirectory: string; icon: string; color: string;
  isPinned: boolean; locationType: "local" | "ssh" | string; createdAt: string; lastOpenedAt?: string;
}
/** Floor (GET /api/floors?workspace=, PROPOSED). */
export interface FloorDTO { id: string; name: string; branchName: string; worktreePath: string; createdAt: string }
/** Connected agents (GET /api/agents, PROPOSED; same data as `maestri list`). */
export interface AgentDTO { id?: string; name: string; kind?: string }

/** GET /api/files?root=<abs>&path=<rel> (PROPOSED). `path` is relative to root, posix separators. */
export interface FileEntry { name: string; path: string; isDir: boolean }
/** GET /api/git/status?root=<abs> (PROPOSED): paths RELATIVE TO root. Mirrors GitFileStatus.swift. */
export type GitBadge = "M" | "A" | "?" | "D" | "R";
export interface GitChange { path: string; status: GitBadge }
