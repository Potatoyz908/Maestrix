import type { FileEntry, GitBadge, GitChange } from "../net/protocol";

/** Reject anything that could leave the tree root. (The core must enforce this too.) */
export function safeRelPath(p: string): string {
  if (p.includes("\u0000") || p.includes("\\") || p.startsWith("/") || p.split("/").some((s) => s === "..")) throw new Error(`invalid path: ${JSON.stringify(p)}`);
  return p;
}
export const joinRel = (parent: string, name: string): string => (parent ? `${parent}/${name}` : name);
export const baseName = (p: string): string => p.replace(/\/+$/, "").split("/").pop() || p;

/** Folders first, then names (case-insensitive, numeric-aware). */
export function sortEntries(entries: readonly FileEntry[]): FileEntry[] {
  return [...entries].sort((a, b) => Number(b.isDir) - Number(a.isDir) || a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true }));
}

export interface Row { entry: FileEntry; depth: number; expanded: boolean }

/** Pure tree state: loaded children + expanded set -> flat visible rows. */
export class TreeState {
  private children = new Map<string, FileEntry[]>();
  readonly expanded: Set<string>;
  constructor(expanded: Iterable<string> = []) { this.expanded = new Set(expanded); }

  setChildren(dir: string, entries: readonly FileEntry[]): void { this.children.set(dir, sortEntries(entries)); }
  isLoaded(dir: string): boolean { return this.children.has(dir); }
  loadedDirs(): string[] { return [...this.children.keys()]; }
  toggle(dir: string): boolean { return this.expanded.delete(dir) ? false : (this.expanded.add(dir), true); }

  rows(max = Infinity): Row[] {
    const out: Row[] = [];
    const walk = (dir: string, depth: number) => {
      for (const entry of this.children.get(dir) ?? []) {
        if (out.length >= max) return;
        const open = entry.isDir && this.expanded.has(entry.path);
        out.push({ entry, depth, expanded: open });
        if (open) walk(entry.path, depth + 1);
      }
    };
    walk("", 0);
    return out;
  }
}

/** File badges + the set of folders that contain a change (shown as a dot). */
export function gitIndex(changes: readonly GitChange[]): { files: Map<string, GitBadge>; dirtyDirs: Set<string> } {
  const files = new Map<string, GitBadge>();
  const dirtyDirs = new Set<string>();
  for (const c of changes) {
    files.set(c.path, c.status);
    const parts = c.path.split("/");
    for (let i = 1; i < parts.length; i++) dirtyDirs.add(parts.slice(0, i).join("/"));
  }
  return { files, dirtyDirs };
}

export const BADGE_TITLE: Record<GitBadge, string> = { M: "Modified", A: "Added", "?": "Untracked", D: "Deleted", R: "Renamed" };

/** expandedPaths is RUNTIME state (never in workspace.json): remembered per node in localStorage only. */
export class ExpandedStore {
  constructor(private nodeId: string, private storage: Pick<Storage, "getItem" | "setItem"> | undefined = (() => { try { return localStorage; } catch { return undefined; } })()) {}
  private key = () => `openMaestri.fileTree.expanded.${this.nodeId.toUpperCase()}`;
  load(): string[] {
    try {
      const v = JSON.parse(this.storage?.getItem(this.key()) ?? "[]") as unknown;
      return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
    } catch { return []; }
  }
  save(paths: Iterable<string>): void {
    try { this.storage?.setItem(this.key(), JSON.stringify([...paths])); } catch { /* storage unavailable: fine */ }
  }
}
