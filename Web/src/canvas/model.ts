import { nodeType, type CanvasNodeDTO, type StickyNoteContent } from "../net/protocol";
import { rectFromFrame, type Rect } from "../geometry/types";

export type NodeKind = "terminal" | "note" | "other";
export interface NodeModel {
  id: string; frame: Rect; zIndex: number; title?: string; kind?: NodeKind;
  /** Present when kind === "note". */
  note?: StickyNoteContent;
}
export const isDomKind = (k?: NodeKind): boolean => k === "terminal" || k === "note";

export const DEFAULT_NOTE: Omit<StickyNoteContent, "fileName"> = {
  color: "#FFF3A3", fontSize: 14, hasCustomName: false, isPreviewing: false, storageMode: { managed: {} },
};

/** Server DTO -> view model (the {type:{_0:payload}} wrapper is unwrapped here). */
export function modelFromDTO(dto: CanvasNodeDTO): NodeModel {
  const t = nodeType(dto.content);
  const base = { id: dto.id, frame: rectFromFrame(dto.frame), zIndex: dto.zIndex };
  if (t === "terminal") return { ...base, kind: "terminal" };
  if (t === "stickyNote") {
    const note = dto.content.stickyNote!._0 as StickyNoteContent;
    return { ...base, kind: "note", note, title: note.fileName.replace(/\.md$/i, "") };
  }
  return { ...base, kind: "other", title: t };
}
