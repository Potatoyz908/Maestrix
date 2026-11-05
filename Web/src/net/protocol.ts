/**
 * DRAFT wire protocol between the Swift core and the web shell.
 * To be agreed with Sentinela (IPC). Existing Swift contract kept as-is:
 *   POST /cli  body {"args": [...]}  header X-Terminal-ID  -> text/plain
 * New (proposed):
 *   GET  /api/workspaces/:id            -> WorkspaceDocument JSON (Maestri-compatible)
 *   WS   /ws/events?workspace=:id       -> ServerEvent (JSON text frames)
 *   WS   /ws/pty/:terminalId            -> binary frames = raw PTY bytes;
 *                                          text frames = ClientPtyControl (resize)
 */
import type { FrameTuple } from "../geometry/types";

/** NodeContent keeps Maestri's { type: { _0: payload } } wrapper. */
export type NodeContent = Record<string, { _0: unknown }>;

export interface CanvasNodeDTO {
  id: string;
  frame: FrameTuple;
  content: NodeContent;
  zIndex: number;
  isLocked: boolean;
}

export type ServerEvent =
  | { type: "nodeAdded"; node: CanvasNodeDTO }
  | { type: "nodeUpdated"; node: CanvasNodeDTO }
  | { type: "nodeRemoved"; id: string }
  | { type: "connectionChanged"; id: string; a: string; b: string; removed?: boolean };

export type ClientCommand =
  | { type: "moveNode"; id: string; frame: FrameTuple }
  | { type: "resizeNode"; id: string; frame: FrameTuple }
  | { type: "selectNodes"; ids: string[] }
  | { type: "persistRope"; id: string; points: [number, number][] };

export type ClientPtyControl = { type: "resize"; cols: number; rows: number };
