import { CanvasView } from "./canvas/CanvasView";
import { DEFAULT_NOTE } from "./canvas/model";
import { CANVAS_INITIAL_ORIGIN } from "./geometry/constants";
import { HttpServices, MemoryServices } from "./net/api";
import { FakePty } from "./net/fakePty";
import { PtySocket } from "./net/ptySocket";
import { Toolbar } from "./ui/Toolbar";

const live = new URLSearchParams(location.search).has("port"); // ?port= => talk to the core; else offline demo
const host = document.getElementById("canvas")!;

const view = new CanvasView(host);
view.services = live ? new HttpServices() : new MemoryServices();
view.ptyFactory = (id) => (live ? new PtySocket(id) : new FakePty(id.slice(0, 8)));
// TODO(core): wire these to WS commands once /ws/events accepts them.
view.onNodeClosed = (id) => console.info("closeNode", id);
view.onContentChange = (id, c) => console.info("updateContent", id, c);
view.onRopeSettled = (id, pts) => console.debug("persistRope", id, pts.length);

const toolbar = new Toolbar(host, { onAddNote: () => { toolbar.setBusy(true); void view.addNote().finally(() => toolbar.setBusy(false)); } });
view.onError = (m) => toolbar.showError(m);

const o = CANVAS_INITIAL_ORIGIN;
view.setGraph(
  [
    { id: "AAAAAAAA-0000-0000-0000-000000000001", kind: "terminal", frame: { x: o.x + 80, y: o.y + 80, w: 480, h: 288 }, zIndex: 1, title: "Terminal A" },
    { id: "BBBBBBBB-0000-0000-0000-000000000002", kind: "terminal", frame: { x: o.x + 720, y: o.y + 160, w: 480, h: 288 }, zIndex: 2, title: "Terminal B" },
    { id: "CCCCCCCC-0000-0000-0000-000000000003", kind: "note", frame: { x: o.x + 400, y: o.y + 480, w: 320, h: 240 }, zIndex: 3, title: "Note 1",
      note: { ...DEFAULT_NOTE, fileName: "Note 1.md" } },
  ],
  [{ id: "11111111-0000-0000-0000-000000000001", a: "AAAAAAAA-0000-0000-0000-000000000001", b: "BBBBBBBB-0000-0000-0000-000000000002" }],
);
