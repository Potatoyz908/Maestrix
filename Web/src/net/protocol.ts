/**
 * DRAFT wire protocol between the Swift core and the web shell. The core does NOT
 * serve /api or /ws yet (only POST /cli exists); this file is the proposal.
 *
 *   POST /cli  body {"args":[...]}  header X-Terminal-ID  -> text/plain   (existing)
 *   GET  /api/workspaces/:id        -> WorkspaceDocument (schemaVersion 2)
 *   GET|PUT /api/notes/:name        -> note markdown (text lives in notes/{name}.md, not workspace.json)
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

export type ServerEvent =
  | { type: "nodeAdded"; node: CanvasNodeDTO }
  | { type: "nodeUpdated"; node: CanvasNodeDTO }
  | { type: "nodeRemoved"; id: string }
  | { type: "connectionChanged"; id: string; a: string; b: string; removed?: boolean };

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
  | { type: "addConnection"; a: string; b: string }
  | { type: "removeConnection"; id: string };

/** Text frames the server may send on /ws/pty (PROPOSED, needs Sentinela ack). */
export type ServerPtyControl = { type: "exit"; code: number };

export type ClientPtyControl = { type: "resize"; cols: number; rows: number };
