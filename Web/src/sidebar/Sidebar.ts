import type { SidebarApi } from "../net/api";
import type { AgentDTO, FloorDTO, SidebarLayout, WorkspaceEntryDTO } from "../net/protocol";
import { resolveSidebar, toggleGroup, validateLayout } from "./layout";

export interface SidebarHooks { onOpenWorkspace(id: string): void; onError(msg: string): void }

const COLLAPSED_KEY = "openMaestri.sidebar.collapsed";
const lsGet = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };

/** Retractable left sidebar: Workspaces (with sidebar-layout groups), Floors, connected Agents. All text via textContent. */
export class Sidebar {
  readonly el = document.createElement("aside");
  private content = document.createElement("div");
  private toggleBtn = document.createElement("button");
  private workspaces: WorkspaceEntryDTO[] = [];
  private layout: SidebarLayout | undefined;
  private floors: FloorDTO[] = [];
  private agents: AgentDTO[] = [];
  private activeId: string | undefined;
  private timer: ReturnType<typeof setInterval> | undefined;

  constructor(host: HTMLElement, private api: SidebarApi, private hooks: SidebarHooks) {
    this.el.className = "sidebar";
    this.content.className = "sidebar-content";
    this.toggleBtn.className = "sidebar-toggle";
    this.toggleBtn.addEventListener("click", () => this.setCollapsed(!this.el.classList.contains("collapsed")));
    this.el.addEventListener("pointerdown", (e) => e.stopPropagation()); // never reaches the canvas
    this.el.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });
    this.el.append(this.toggleBtn, this.content);
    host.append(this.el);
    this.setCollapsed(lsGet(COLLAPSED_KEY) === "1");
    void this.refresh();
    this.timer = setInterval(() => void this.refreshAgents(), 5000);
  }

  setCollapsed(c: boolean): void {
    this.el.classList.toggle("collapsed", c);
    this.toggleBtn.textContent = c ? "☰" : "‹";
    this.toggleBtn.title = c ? "Show sidebar" : "Hide sidebar";
    lsSet(COLLAPSED_KEY, c ? "1" : "0");
  }

  setActiveWorkspace(id: string | undefined): void { this.activeId = id; this.render(); }

  async refresh(): Promise<void> {
    const [ws, layout, floors, agents] = await Promise.allSettled([this.api.workspaces(), this.api.layout(), this.api.floors(this.activeId), this.api.agents()]);
    if (ws.status === "fulfilled") this.workspaces = ws.value; else this.hooks.onError(`Workspaces: ${(ws.reason as Error).message}`);
    if (layout.status === "fulfilled") {
      const check = layout.value ? validateLayout(layout.value, this.workspaces.map((w) => w.id)) : undefined;
      this.layout = check?.ok ? check.layout : undefined; // invalid layout: fall back to the plain list
    }
    if (floors.status === "fulfilled") this.floors = floors.value;
    if (agents.status === "fulfilled") this.agents = agents.value;
    this.render();
  }

  private async refreshAgents(): Promise<void> {
    try { this.agents = await this.api.agents(); this.render(); } catch { /* transient */ }
  }

  private async toggleGroupCollapse(id: string): Promise<void> {
    if (!this.layout) return;
    const next = toggleGroup(this.layout, id);
    this.layout = next; // optimistic
    this.render();
    try { await this.api.saveLayout(next); } catch (e) { this.hooks.onError(`Could not save sidebar layout: ${(e as Error).message}`); }
  }

  private el_(tag: string, cls: string, text?: string): HTMLElement {
    const e = document.createElement(tag);
    e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  private wsRow(w: WorkspaceEntryDTO, indent = false): HTMLElement {
    const r = this.el_("div", "sb-item" + (indent ? " indent" : "") + (w.id === this.activeId ? " active" : ""), `${w.isPinned ? "📌 " : ""}${w.name}`);
    r.title = w.workingDirectory;
    r.addEventListener("click", () => this.hooks.onOpenWorkspace(w.id));
    return r;
  }

  private render(): void {
    const frag = document.createDocumentFragment();
    frag.append(this.el_("div", "sb-title", "Workspaces"));
    const { top, groups } = resolveSidebar(this.layout, this.workspaces);
    for (const w of top) frag.append(this.wsRow(w));
    for (const { group, items } of groups) {
      const h = this.el_("div", "sb-group", `${group.isCollapsed ? "▸" : "▾"} ${group.name}`);
      h.addEventListener("click", () => void this.toggleGroupCollapse(group.id));
      frag.append(h);
      if (!group.isCollapsed) for (const w of items) frag.append(this.wsRow(w, true));
    }
    if (!this.workspaces.length) frag.append(this.el_("div", "sb-empty", "No workspaces"));

    frag.append(this.el_("div", "sb-title", "Floors"));
    for (const f of this.floors) { const r = this.el_("div", "sb-item", `🌿 ${f.name}`); r.title = f.branchName; frag.append(r); }
    if (!this.floors.length) frag.append(this.el_("div", "sb-empty", "No floors"));

    frag.append(this.el_("div", "sb-title", "Agents"));
    for (const a of this.agents) frag.append(this.el_("div", "sb-item", `● ${a.name}`));
    if (!this.agents.length) frag.append(this.el_("div", "sb-empty", "No agents connected"));
    this.content.replaceChildren(frag);
  }

  destroy(): void { clearInterval(this.timer); this.el.remove(); }
}
