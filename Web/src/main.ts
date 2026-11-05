import { CanvasView } from "./canvas/CanvasView";
import { CANVAS_INITIAL_ORIGIN } from "./geometry/constants";

// Prototype: two demo nodes + one rope until the core's /api/workspaces endpoint exists.
const view = new CanvasView(document.getElementById("canvas")!);
const o = CANVAS_INITIAL_ORIGIN;
view.setGraph(
  [
    { id: "aaaaaaaa-demo-terminal-1", frame: { x: o.x + 80, y: o.y + 80, w: 420, h: 260 }, zIndex: 1, title: "Terminal A" },
    { id: "bbbbbbbb-demo-terminal-2", frame: { x: o.x + 640, y: o.y + 200, w: 420, h: 260 }, zIndex: 2, title: "Terminal B" },
  ],
  [{ id: "conn-1", a: "aaaaaaaa-demo-terminal-1", b: "bbbbbbbb-demo-terminal-2" }],
);
