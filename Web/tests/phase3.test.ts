import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../src/note/markdown";
import { NoteSaver, type SaveState } from "../src/note/NoteSaver";
import { canConnect, facingSides, pinPoint } from "../src/geometry/pins";
import { endpointsOf, linkFor } from "../src/net/protocol";
import { HttpServices, MemoryServices, noteNameFromFile, safeNoteName, type NotesApi } from "../src/net/api";
import { modelFromDTO } from "../src/canvas/model";

describe("markdown", () => {
  it("renders headings, emphasis, code, lists, quotes", () => {
    const html = renderMarkdown("# Title\n\nhello **bold** and *it* and `x<y`\n\n- a\n- b\n\n> q");
    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<em>it</em>");
    expect(html).toContain("<code>x&lt;y</code>");
    expect(html).toContain("<ul><li>a</li><li>b</li></ul>");
    expect(html).toContain("<blockquote>q</blockquote>");
  });
  it("escapes raw HTML (no script/handler injection)", () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)> <script>alert(1)</script>');
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;script&gt;");
  });
  it("only allows http(s)/mailto links", () => {
    expect(renderMarkdown("[x](javascript:alert(1))")).not.toContain("<a ");
    expect(renderMarkdown("[x](https://a.b/c)")).toContain('<a href="https://a.b/c" target="_blank" rel="noopener noreferrer">x</a>');
  });
  it("renders fenced code verbatim", () => {
    expect(renderMarkdown("```\n**no**\n```")).toBe("<pre><code>**no**</code></pre>");
  });
});

describe("NoteSaver", () => {
  function harness(put: NotesApi["put"]) {
    const timers: Array<() => void> = [];
    const states: SaveState[] = [];
    const calls: string[] = [];
    const api: NotesApi = { get: async () => "", put: async (n, t) => { calls.push(`${n}:${t}`); await put(n, t); } };
    const saver = new NoteSaver("Note 1", api, {
      onState: (s) => states.push(s),
      setTimer: (fn) => { timers.push(fn); return timers.length - 1; },
      clearTimer: (h) => { timers[h as number] = () => {}; },
    });
    return { saver, timers, states, calls };
  }
  it("debounces: only the last text is PUT", async () => {
    const h = harness(async () => {});
    h.saver.schedule("a"); h.saver.schedule("ab"); h.saver.schedule("abc");
    expect(h.calls).toEqual([]);
    h.timers.at(-1)!();
    await h.saver.flush();
    expect(h.calls).toEqual(["Note 1:abc"]);
    expect(h.states.at(-1)).toBe("saved");
  });
  it("flush saves immediately without waiting for the timer", async () => {
    const h = harness(async () => {});
    h.saver.schedule("x");
    await h.saver.flush();
    expect(h.calls).toEqual(["Note 1:x"]);
  });
  it("reports error and retries the same text on the next flush", async () => {
    let fail = true;
    const h = harness(async () => { if (fail) throw new Error("boom"); });
    h.saver.schedule("keep");
    await h.saver.flush();
    expect(h.states.at(-1)).toBe("error");
    fail = false;
    await h.saver.flush();
    expect(h.calls).toEqual(["Note 1:keep", "Note 1:keep"]);
    expect(h.states.at(-1)).toBe("saved");
  });
  it("edits made during an in-flight PUT are sent afterwards, in order", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let first = true;
    const h = harness(async () => { if (first) { first = false; await gate; } });
    h.saver.schedule("one");
    const p = h.saver.flush();
    await Promise.resolve();
    h.saver.schedule("two");
    release();
    await p;
    await h.saver.flush();
    expect(h.calls).toEqual(["Note 1:one", "Note 1:two"]);
  });
});

describe("pins", () => {
  const f = { x: 100, y: 100, w: 200, h: 100 };
  it("pins are at the vertical middle of the side edges", () => {
    expect(pinPoint(f, "left")).toEqual({ x: 100, y: 150 });
    expect(pinPoint(f, "right")).toEqual({ x: 300, y: 150 });
  });
  it("facing sides follow relative position", () => {
    expect(facingSides(f, { ...f, x: 600 })).toEqual(["right", "left"]);
    expect(facingSides({ ...f, x: 600 }, f)).toEqual(["left", "right"]);
  });
  it("rejects self links and duplicates (either direction, any id case)", () => {
    expect(canConnect([], "a", "A")).toBe(false);
    expect(canConnect([{ a: "AA", b: "BB" }], "bb", "aa")).toBe(false);
    expect(canConnect([{ a: "AA", b: "BB" }], "AA", "CC")).toBe(true);
  });
});

describe("connection links (typed arrays in workspace.json)", () => {
  it("terminal<->terminal, note<->note", () => {
    expect(linkFor({ id: "T1", kind: "terminal" }, { id: "T2", kind: "terminal" })).toEqual({ kind: "terminal", terminalIdA: "T1", terminalIdB: "T2" });
    expect(linkFor({ id: "N1", kind: "note" }, { id: "N2", kind: "note" })).toEqual({ kind: "noteToNote", noteNodeIdA: "N1", noteNodeIdB: "N2" });
  });
  it("terminal<->note is normalized to (terminal, note) from either pin", () => {
    const want = { kind: "note", terminalId: "T", noteNodeId: "N" };
    expect(linkFor({ id: "T", kind: "terminal" }, { id: "N", kind: "note" })).toEqual(want);
    expect(linkFor({ id: "N", kind: "note" }, { id: "T", kind: "terminal" })).toEqual(want);
  });
  it("unsupported pairs return null; endpointsOf keeps A/B order", () => {
    expect(linkFor({ id: "N", kind: "note" }, { id: "X", kind: "other" })).toBeNull();
    expect(endpointsOf({ kind: "note", terminalId: "T", noteNodeId: "N" })).toEqual(["T", "N"]);
    expect(endpointsOf({ kind: "crossFloor", nodeIdA: "A", nodeIdB: "B" })).toEqual(["A", "B"]);
  });
});

describe("notes API", () => {
  it("strips .md (the core appends it) and rejects traversal", () => {
    expect(noteNameFromFile("nome.md")).toBe("nome");
    for (const bad of ["", "../x", "a/b", "a\\b", ".hidden", "a..b"]) expect(() => safeNoteName(bad)).toThrow();
  });
  it("PUT percent-encodes the name, sends Authorization-less offline, and the raw text body", async () => {
    const seen: Array<{ url: string; init?: RequestInit }> = [];
    const api = new HttpServices(async (url, init) => { seen.push({ url, init }); return new Response("", { status: 200 }); });
    await api.notes.put("Minha nota é ótima", "# oi");
    expect(seen[0]!.url).toContain("/api/notes/Minha%20nota%20%C3%A9%20%C3%B3tima");
    expect(seen[0]!.init?.method).toBe("PUT");
    expect(seen[0]!.init?.body).toBe("# oi");
  });
  it("GET 404 means an empty note; other errors throw", async () => {
    expect(await new HttpServices(async () => new Response("", { status: 404 })).notes.get("n")).toBe("");
    await expect(new HttpServices(async () => new Response("", { status: 500 })).notes.get("n")).rejects.toThrow();
  });
  it("POST /api/connections sends the kind-discriminated link", async () => {
    let body = "";
    const api = new HttpServices(async (_u, init) => { body = String(init?.body); return new Response(JSON.stringify({ id: "X" }), { status: 200 }); });
    const r = await api.connections.create({ kind: "note", terminalId: "T", noteNodeId: "N" });
    expect(r.id).toBe("X");
    // a/b are what today's server reads (it infers the type from the nodes); kind + ids stay for typed servers
    expect(JSON.parse(body)).toEqual({ kind: "note", terminalId: "T", noteNodeId: "N", a: "T", b: "N" });
  });
  it("offline MemoryServices round-trips notes and creates uppercase ids with fraction-less dates", async () => {
    const m = new MemoryServices();
    await m.notes.put("a", "hello");
    expect(await m.notes.get("a")).toBe("hello");
    const dto = await m.nodes.create({ nodeType: "stickyNote", frame: [[0, 0], [1, 1]] });
    expect(dto.id).toBe(dto.id.toUpperCase());
    expect(dto.createdAt).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
  });
});

describe("modelFromDTO", () => {
  it("unwraps sticky notes and keeps the fileName", () => {
    const m = modelFromDTO({
      id: "ID", frame: [[10, 20], [300, 200]], zIndex: 4, isLocked: false, createdAt: "", lastModifiedAt: "",
      content: { stickyNote: { _0: { color: "#fff", fileName: "Ideas.md", fontSize: 14, hasCustomName: false, isPreviewing: true, storageMode: { managed: {} } } } },
    });
    expect(m.kind).toBe("note");
    expect(m.frame).toEqual({ x: 10, y: 20, w: 300, h: 200 });
    expect(m.title).toBe("Ideas");
    expect(m.note?.fileName).toBe("Ideas.md");
  });
  it("terminal and unknown types", () => {
    const base = { id: "I", frame: [[0, 0], [1, 1]] as [[number, number], [number, number]], zIndex: 0, isLocked: false, createdAt: "", lastModifiedAt: "" };
    expect(modelFromDTO({ ...base, content: { terminal: { _0: {} } } }).kind).toBe("terminal");
    expect(modelFromDTO({ ...base, content: { shape: { _0: {} } } }).kind).toBe("other");
  });
});
