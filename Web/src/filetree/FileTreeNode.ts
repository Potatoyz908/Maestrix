import type { Rect } from "../geometry/types";
import { DomNode, type DomNodeHost } from "../canvas/DomNode";
import type { FilesApi, GitApi } from "../net/api";
import type { FileTreeContent, GitBadge } from "../net/protocol";
import { BADGE_TITLE, ExpandedStore, TreeState, gitIndex } from "./tree";

const MAX_ROWS = 2000;
const GIT_POLL_MS = 10_000;
const BADGE_CLASS: Record<GitBadge, string> = { M: "gm", A: "ga", "?": "gu", D: "gd", R: "gr" };

/** Folder explorer: lazy directory loading, expandable folders, Git badges (M/A/?/D/R; dot on folders with changes). */
export class FileTreeNode extends DomNode {
  private list = document.createElement("div");
  private msg = document.createElement("div");
  private tree: TreeState;
  private store: ExpandedStore;
  private git: ReturnType<typeof gitIndex> = { files: new Map(), dirtyDirs: new Set() };
  private selected = "";
  private timer: ReturnType<typeof setInterval> | undefined;
  private dead = false;

  constructor(id: string, private content: FileTreeContent, private files: FilesApi, private gitApi: GitApi, host: DomNodeHost, getFrame: () => Rect) {
    super(id, content.name, host, getFrame, "fnode");
    this.store = new ExpandedStore(id);
    this.tree = new TreeState(this.store.load());
    const bar = document.createElement("div");
    bar.className = "fnode-bar";
    const root = document.createElement("span");
    root.className = "fnode-root";
    root.textContent = content.rootPath;
    root.title = content.rootPath;
    const refresh = document.createElement("button");
    refresh.className = "fnode-refresh dnode-nodrag";
    refresh.textContent = "⟳";
    refresh.title = "Refresh";
    refresh.addEventListener("click", () => void this.refresh());
    bar.append(root, refresh);
    this.list.className = "fnode-list";
    this.msg.className = "fnode-msg";
    this.body.append(bar, this.msg, this.list);
  }

  mount(): void {
    void this.refresh();
    this.timer = setInterval(() => void this.refreshGit(), GIT_POLL_MS);
  }

  /** Reload root + every expanded directory, then the Git status. */
  async refresh(): Promise<void> {
    const dirs = ["", ...this.tree.expanded];
    await Promise.all(dirs.map((d) => this.loadDir(d)));
    await this.refreshGit();
    this.render();
  }

  private async loadDir(dir: string): Promise<void> {
    try {
      const entries = await this.files.list(this.content.rootPath, dir);
      if (this.dead) return;
      this.tree.setChildren(dir, entries);
      if (dir === "") this.msg.textContent = "";
    } catch (e) {
      if (dir === "") this.msg.textContent = `Cannot read folder: ${(e as Error).message}`;
      else this.tree.expanded.delete(dir); // folder vanished or became unreadable
    }
  }

  private async refreshGit(): Promise<void> {
    try { this.git = gitIndex(await this.gitApi.status(this.content.rootPath)); } catch { this.git = { files: new Map(), dirtyDirs: new Set() }; } // not a repo / no access: no badges
    if (!this.dead) this.render();
  }

  private async toggle(path: string): Promise<void> {
    const open = this.tree.toggle(path);
    this.store.save(this.tree.expanded);
    if (open && !this.tree.isLoaded(path)) await this.loadDir(path);
    this.render();
  }

  private render(): void {
    const rows = this.tree.rows(MAX_ROWS);
    const frag = document.createDocumentFragment();
    for (const { entry, depth, expanded } of rows) {
      const row = document.createElement("div");
      row.className = "fnode-row" + (entry.path === this.selected ? " sel" : "");
      row.style.paddingLeft = `${6 + depth * 14}px`;
      const chev = document.createElement("span");
      chev.className = "fnode-chev";
      chev.textContent = entry.isDir ? (expanded ? "▾" : "▸") : "";
      const icon = document.createElement("span");
      icon.textContent = entry.isDir ? "📁" : "📄";
      const name = document.createElement("span");
      name.className = "fnode-name";
      name.textContent = entry.name; // textContent: file names are untrusted
      name.title = entry.path;
      row.append(chev, icon, name);

      const badge = this.git.files.get(entry.path);
      if (badge) {
        const b = document.createElement("span");
        b.className = `fnode-badge ${BADGE_CLASS[badge]}`;
        b.textContent = badge;
        b.title = BADGE_TITLE[badge];
        row.append(b);
      } else if (entry.isDir && this.git.dirtyDirs.has(entry.path)) {
        const d = document.createElement("span");
        d.className = "fnode-badge gdot";
        d.textContent = "•";
        d.title = "Contains changes";
        row.append(d);
      }
      row.addEventListener("click", () => { this.selected = entry.path; if (entry.isDir) void this.toggle(entry.path); else this.render(); });
      frag.append(row);
    }
    if (rows.length >= MAX_ROWS) {
      const more = document.createElement("div");
      more.className = "fnode-msg";
      more.textContent = `Showing first ${MAX_ROWS} rows - collapse some folders`;
      frag.append(more);
    }
    this.list.replaceChildren(frag);
  }

  protected onDispose(): void { this.dead = true; clearInterval(this.timer); }
}
