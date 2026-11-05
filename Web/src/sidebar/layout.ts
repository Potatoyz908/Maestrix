import { sameId, type SidebarGroup, type SidebarLayout, type WorkspaceEntryDTO } from "../net/protocol";

export type LayoutCheck = { ok: true; layout: SidebarLayout } | { ok: false; error: string };

/** Mirrors what the core must enforce on PUT: schemaVersion 2, known workspace ids, no duplicate placement. */
export function validateLayout(l: unknown, knownWorkspaceIds: Iterable<string>): LayoutCheck {
  const o = l as Partial<SidebarLayout> | null;
  if (!o || typeof o !== "object") return { ok: false, error: "not an object" };
  if (o.schemaVersion !== 2) return { ok: false, error: `unsupported schemaVersion ${String(o.schemaVersion)}` };
  if (!Array.isArray(o.topLevelItems) || !Array.isArray(o.groups)) return { ok: false, error: "topLevelItems/groups must be arrays" };
  const known = new Set([...knownWorkspaceIds].map((x) => x.toUpperCase()));
  const seen = new Set<string>();
  const place = (id: unknown): string | null => {
    if (typeof id !== "string") return "item is not a string";
    const u = id.toUpperCase();
    if (!known.has(u)) return `unknown workspace ${id}`;
    if (seen.has(u)) return `workspace ${id} placed twice`;
    seen.add(u);
    return null;
  };
  for (const id of o.topLevelItems) { const e = place(id); if (e) return { ok: false, error: e }; }
  for (const g of o.groups) {
    if (!g || typeof g.id !== "string" || typeof g.name !== "string" || typeof g.isCollapsed !== "boolean" || !Array.isArray(g.items)) return { ok: false, error: "malformed group" };
    for (const id of g.items) { const e = place(id); if (e) return { ok: false, error: e }; }
  }
  return { ok: true, layout: o as SidebarLayout };
}

export interface ResolvedSidebar { top: WorkspaceEntryDTO[]; groups: { group: SidebarGroup; items: WorkspaceEntryDTO[] }[] }

/** Ordered view of the layout. Unknown ids are dropped; workspaces the layout doesn't mention are appended so none is hidden. */
export function resolveSidebar(layout: SidebarLayout | undefined, workspaces: readonly WorkspaceEntryDTO[]): ResolvedSidebar {
  const byId = new Map(workspaces.map((w) => [w.id.toUpperCase(), w]));
  const used = new Set<string>();
  const take = (ids: readonly string[]) => ids.flatMap((id) => {
    const w = byId.get(id.toUpperCase());
    if (!w || used.has(w.id.toUpperCase())) return [];
    used.add(w.id.toUpperCase());
    return [w];
  });
  const top = take(layout?.topLevelItems ?? []);
  const groups = (layout?.groups ?? []).map((group) => ({ group, items: take(group.items) }));
  top.push(...workspaces.filter((w) => !used.has(w.id.toUpperCase())));
  return { top, groups };
}

export function toggleGroup(layout: SidebarLayout, groupId: string): SidebarLayout {
  return { ...layout, groups: layout.groups.map((g) => (sameId(g.id, groupId) ? { ...g, isCollapsed: !g.isCollapsed } : g)) };
}
