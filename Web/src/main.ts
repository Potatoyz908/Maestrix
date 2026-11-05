import { CanvasView, type NodeModel } from "./canvas/CanvasView";
import { Toolbar } from "./canvas/Toolbar";
import { CANVAS_INITIAL_ORIGIN } from "./geometry/constants";
import { FakePty } from "./net/fakePty";
import { PtySocket } from "./net/ptySocket";
import { httpUrl, authHeaders } from "./net/client";

const params = new URLSearchParams(location.search);
const live = params.has("port") || params.has("corePort");

const container = document.getElementById("canvas")!;
const view = new CanvasView(container);

// Helper to spawn backend PTY session on Swift Core
async function spawnBackendTerminal(id: string, name: string): Promise<boolean> {
  if (!live) return false;
  try {
    const res = await fetch(httpUrl("/api/terminals"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ id, displayName: name }),
    });
    return res.ok;
  } catch (err) {
    console.warn(`[Open Maestri] Could not spawn backend terminal ${id}:`, err);
    return false;
  }
}

// Helper to close terminal on backend
async function closeBackendTerminal(id: string): Promise<void> {
  if (!live) return;
  try {
    await fetch(httpUrl(`/api/terminals/${encodeURIComponent(id)}`), {
      method: "DELETE",
      headers: authHeaders(),
    });
  } catch (err) {
    console.warn(`[Open Maestri] Could not delete terminal ${id}:`, err);
  }
}

view.ptyFactory = (id) => (live ? new PtySocket(id) : new FakePty(id.slice(0, 8)));

view.onNodeClosed = (id) => {
  console.info("[Open Maestri] Node closed:", id);
  closeBackendTerminal(id);
};

// Initial nodes
const o = CANVAS_INITIAL_ORIGIN;
const nodeA: NodeModel = {
  id: "38430c4d-0001-4000-8000-000000000001",
  kind: "terminal",
  frame: { x: o.x + 80, y: o.y + 80, w: 480, h: 288 },
  zIndex: 1,
  title: "Terminal A",
};

const nodeB: NodeModel = {
  id: "38430c4d-0002-4000-8000-000000000002",
  kind: "terminal",
  frame: { x: o.x + 640, y: o.y + 160, w: 480, h: 288 },
  zIndex: 2,
  title: "Terminal B",
};

const initialNodes: NodeModel[] = [nodeA, nodeB];
const initialConnections = [
  { id: "conn-1", a: nodeA.id, b: nodeB.id },
];

// Mount floating top toolbar
const toolbar = new Toolbar({
  onAddTerminal: async () => {
    const newId = crypto.randomUUID ? crypto.randomUUID() : `term-${Date.now()}`;
    const count = view.nodes.length + 1;
    const title = `Terminal ${String.fromCharCode(64 + count)}`;

    const screenCenter = {
      x: Math.max(80, window.innerWidth / 2 - 240),
      y: Math.max(80, window.innerHeight / 2 - 144),
    };
    const canvasPt = view.viewport.toCanvas(screenCenter);

    const newNode: NodeModel = {
      id: newId,
      kind: "terminal",
      frame: { x: Math.round(canvasPt.x), y: Math.round(canvasPt.y), w: 480, h: 288 },
      zIndex: count,
      title,
    };

    if (live) {
      await spawnBackendTerminal(newId, title);
    }

    view.setGraph([...view.nodes, newNode], view.connections);
  },
  onAddNote: () => {
    // Placeholder for future Note node creation
  },
  onResetZoom: () => {
    view.viewport.zoom = 1;
    view.viewport.origin = { ...CANVAS_INITIAL_ORIGIN };
  },
});

document.body.appendChild(toolbar.element);

// Bootstrapping: start backend terminals and set initial graph
async function bootstrap() {
  if (live) {
    toolbar.setStatus(true, "Core Conectado");
    for (const node of initialNodes) {
      await spawnBackendTerminal(node.id, node.title ?? "Terminal");
    }
  } else {
    toolbar.setStatus(false, "Modo Demo Offline");
  }

  view.setGraph(initialNodes, initialConnections);
}

bootstrap();
