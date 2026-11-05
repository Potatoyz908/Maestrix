import { describe, expect, it } from "vitest";
import { PortalHistory, isLoading, navigatePortal, newPortalContent, normalizeUrl } from "../src/portal/url";
import { ExpandedStore, TreeState, baseName, gitIndex, joinRel, safeRelPath, sortEntries } from "../src/filetree/tree";
import { resolveSidebar, toggleGroup, validateLayout } from "../src/sidebar/layout";
import { linkFor, type SidebarLayout, type WorkspaceEntryDTO } from "../src/net/protocol";
import { HttpServices, MemoryServices } from "../src/net/api";
import { modelFromDTO, isDomKind } from "../src/canvas/model";

const url = (s: string, origin?: string) => normalizeUrl(s, origin);

describe("normalizeUrl", () => {
  it("adds https for hosts, http for local/private hosts", () => {
    expect(url("example.com")).toEqual({ ok: true, url: "https://example.com/" });
    expect(url("localhost:3000/app")).toEqual({ ok: true, url: "http://localhost:3000/app" });
    expect(url("192.168.0.10:8080")).toEqual({ ok: true, url: "http://192.168.0.10:8080/" });
    expect(url("https://a.b/c?d=1#e")).toEqual({ ok: true, url: "https://a.b/c?d=1#e" });
  });
  it("rejects non-http(s) schemes and non-URLs", () => {
    for (const bad of ["javascript:alert(1)", "data:text/html,<h1>x</h1>", "file:///etc/passwd", "blob:x", "ftp://x.y", "hello world", "", "   ", "justaword"]) {
      expect(url(bad).ok, bad).toBe(false);
    }
  });
  it("refuses the shell's own origin (same-origin iframe could reach the token)", () => {
    expect(url("http://127.0.0.1:5000/x", "http://127.0.0.1:5000").ok).toBe(false);
    expect(url("http://127.0.0.1:5001/x", "http://127.0.0.1:5000").ok).toBe(true);
  });
});

describe("PortalHistory & content", () => {
  it("back/forward and truncating forward on a new visit", () => {
    const h = new PortalHistory();
    h.visit("a"); h.visit("b"); h.visit("c");
    expect(h.back()).toBe("b");
    expect(h.back()).toBe("a");
    expect(h.back()).toBeUndefined();
    expect(h.forward()).toBe("b");
    h.visit("d");
    expect(h.canForward).toBe(false);
    expect(h.current).toBe("d");
  });
  it("navigating keeps currentURL and source.url._0 coherent and adds no url/isLoading key", () => {
    const c = navigatePortal(newPortalContent("Portal 1", "ID"), "https://x.y/");
    expect(c.currentURL).toBe("https://x.y/");
    expect(c.source).toEqual({ url: { _0: "https://x.y/" } });
    expect(Object.keys(c).sort()).toEqual(["chromeHidden", "currentURL", "id", "name", "source", "status", "storageScope"]);
    expect(isLoading({ ...c, status: "loading" })).toBe(true);
    expect(isLoading(c)).toBe(false);
  });
  it("new portal: source none is {}", () => {
    expect(newPortalContent("P").source).toEqual({});
  });
});

describe("file tree", () => {
  const e = (name: string, isDir: boolean, parent = "") => ({ name, path: joinRel(parent, name), isDir });
  it("sorts folders first, case-insensitive and numeric-aware", () => {
    expect(sortEntries([e("b.txt", false), e("Zdir", true), e("a10", false), e("a2", false), e("adir", true)]).map((x) => x.name))
      .toEqual(["adir", "Zdir", "a2", "a10", "b.txt"]);
  });
  it("rows follow expansion and depth", () => {
    const t = new TreeState();
    t.setChildren("", [e("src", true), e("README.md", false)]);
    t.setChildren("src", [e("main.swift", false, "src")]);
    expect(t.rows().map((r) => r.entry.path)).toEqual(["src", "README.md"]);
    t.toggle("src");
    expect(t.rows().map((r) => [r.entry.path, r.depth])).toEqual([["src", 0], ["src/main.swift", 1], ["README.md", 0]]);
    expect(t.rows(1)).toHaveLength(1);
  });
  it("git index: file badges + ancestor dirs flagged", () => {
    const g = gitIndex([{ path: "a/b/c.ts", status: "M" }, { path: "README.md", status: "?" }]);
    expect(g.files.get("a/b/c.ts")).toBe("M");
    expect([...g.dirtyDirs].sort()).toEqual(["a", "a/b"]);
  });
  it("rejects path traversal", () => {
    for (const bad of ["../x", "a/../b", "/etc", "a\\b", "a\u0000"]) expect(() => safeRelPath(bad)).toThrow();
    expect(safeRelPath("a/b")).toBe("a/b");
    expect(baseName("/home/u/proj/")).toBe("proj");
  });
  it("expanded paths persist outside workspace.json, tolerate bad storage", () => {
    const mem = new Map<string, string>();
    const st = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) };
    new ExpandedStore("abc", st).save(["x", "y/z"]);
    expect(new ExpandedStore("ABC", st).load()).toEqual(["x", "y/z"]); // id case-insensitive
    mem.set([...mem.keys()][0]!, "{not json");
    expect(new ExpandedStore("abc", st).load()).toEqual([]);
    expect(new ExpandedStore("abc", { getItem: () => { throw new Error("x"); }, setItem: () => { throw new Error("x"); } }).load()).toEqual([]);
  });
});

describe("sidebar layout", () => {
  const ws = (id: string, name: string): WorkspaceEntryDTO => ({ id, name, workingDirectory: "/", icon: "folder", color: "blue", isPinned: false, locationType: "local", createdAt: "" });
  const A = ws("AAAA", "A"), B = ws("BBBB", "B"), C = ws("CCCC", "C");
  const layout: SidebarLayout = { schemaVersion: 2, topLevelItems: ["AAAA"], groups: [{ id: "G1", name: "Group", isCollapsed: false, items: ["bbbb"] }] };
  it("validates schemaVersion, known ids, duplicates", () => {
    expect(validateLayout(layout, ["AAAA", "BBBB"]).ok).toBe(true);
    expect(validateLayout({ ...layout, schemaVersion: 1 }, ["AAAA", "BBBB"]).ok).toBe(false);
    expect(validateLayout(layout, ["AAAA"]).ok).toBe(false); // BBBB unknown
    expect(validateLayout({ ...layout, topLevelItems: ["AAAA", "bbbb"] }, ["AAAA", "BBBB"]).ok).toBe(false); // placed twice
    expect(validateLayout(null, []).ok).toBe(false);
  });
  it("resolves order (ids case-insensitive) and appends unplaced workspaces", () => {
    const r = resolveSidebar(layout, [A, B, C]);
    expect(r.top.map((w) => w.name)).toEqual(["A", "C"]);
    expect(r.groups[0]!.items.map((w) => w.name)).toEqual(["B"]);
    expect(resolveSidebar(undefined, [A, B]).top.map((w) => w.name)).toEqual(["A", "B"]);
  });
  it("toggleGroup flips only that group, immutably", () => {
    const next = toggleGroup(layout, "g1");
    expect(next.groups[0]!.isCollapsed).toBe(true);
    expect(layout.groups[0]!.isCollapsed).toBe(false);
  });
});

describe("links, model and api (phase 4)", () => {
  it("portal links are normalized (terminal first) and portal<->portal exists", () => {
    const want = { kind: "portal", terminalId: "T", portalNodeId: "P" };
    expect(linkFor({ id: "T", kind: "terminal" }, { id: "P", kind: "portal" })).toEqual(want);
    expect(linkFor({ id: "P", kind: "portal" }, { id: "T", kind: "terminal" })).toEqual(want);
    expect(linkFor({ id: "P1", kind: "portal" }, { id: "P2", kind: "portal" })).toEqual({ kind: "portalToPortal", portalIdA: "P1", portalIdB: "P2" });
    expect(linkFor({ id: "N", kind: "note" }, { id: "P", kind: "portal" })).toBeNull();
    expect(linkFor({ id: "F", kind: "fileTree" }, { id: "T", kind: "terminal" })).toBeNull();
  });
  it("modelFromDTO maps portal and fileTree and they are DOM kinds", () => {
    const base = { id: "I", frame: [[0, 0], [10, 10]] as [[number, number], [number, number]], zIndex: 1, isLocked: false, createdAt: "", lastModifiedAt: "" };
    const p = modelFromDTO({ ...base, content: { portal: { _0: newPortalContent("P1", "X") } } });
    expect(p.kind).toBe("portal");
    expect(p.title).toBe("P1");
    const f = modelFromDTO({ ...base, content: { fileTree: { _0: { name: "proj", rootPath: "/p", viewMode: "list" } } } });
    expect(f.kind).toBe("fileTree");
    expect(f.fileTree?.rootPath).toBe("/p");
    expect(isDomKind(p.kind) && isDomKind(f.kind)).toBe(true);
  });
  it("HttpServices encodes root/path query params and PUTs the sidebar layout", async () => {
    const seen: Array<{ url: string; method?: string; body?: unknown }> = [];
    const api = new HttpServices(async (u, i) => { seen.push({ url: u, method: i?.method, body: i?.body }); return new Response("[]", { status: 200 }); });
    await api.files.list("/home/u/my proj", "a b/c");
    expect(seen[0]!.url).toContain("/api/files?root=%2Fhome%2Fu%2Fmy%20proj&path=a%20b%2Fc");
    await api.git.status("/p");
    expect(seen[1]!.url).toContain("/api/git/status?root=%2Fp");
    await api.sidebar.saveLayout({ schemaVersion: 2, topLevelItems: [], groups: [] });
    expect(seen[2]).toMatchObject({ method: "PUT" });
    expect(seen[2]!.url).toContain("/api/sidebar-layout");
  });
  it("offline services provide sidebar and tree data", async () => {
    const m = new MemoryServices();
    expect((await m.sidebar.workspaces()).length).toBeGreaterThan(0);
    expect((await m.files.list("/", "")).some((x) => x.isDir)).toBe(true);
    expect((await m.git.status("/")).length).toBeGreaterThan(0);
  });
});
