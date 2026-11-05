# CanvasWidget (GTK 4 + Cairo): design and status

Package: `canvas-cairo/maestri_canvas/`. Run the tests with `python3 -m pytest canvas-cairo` (53 tests; the 10 in
`test_widget.py` need a display and skip without one). Demo: `cd canvas-cairo && python3 -m maestri_canvas.demo`
(`--screenshot out.png --seconds 2` renders the real GTK widget tree to a PNG).

## Layers

```
CanvasWidget (Gtk.Overlay)
 ├─ CairoLayer(top=False)   grid, ropes, canvas-drawn nodes     (pycairo ImageSurface -> Gdk.MemoryTexture)
 ├─ Gtk.Fixed               NodeFrame per node: header, content widget, 8 resize handles, 2 pins (GskTransform = pan/zoom)
 └─ CairoLayer(top=True)    gesture visuals: dragged rope, snap guides, marquee, Alt-drag ghosts (paints nothing when idle)

engine (no GTK):  geometry, viewport, snapping, resize, rope, scene (z caches, hit-test, pins), interaction (state machine)
renderer (no GTK): pycairo, draws on ANY cairo.Context
widget.py: the only file that imports Gtk
```

Widget kinds (`terminal`, `stickyNote`, `portal`, `fileTree`) are real GTK widgets so VTE/WebView/TextView work and stack by z.
Other kinds (`shape`, `stroke`, `freehand`, `text`) are drawn by Cairo (currently placeholder rectangles).

## Verified on this host (GTK 4.22.4, PyGObject 3.56.2, Python 3.14, Wayland)

| Finding | Consequence |
|---|---|
| `gi._gi_cairo` is missing (`gi.require_foreign("cairo")` raises ImportError) | `Gtk.DrawingArea.set_draw_func` cannot be used from Python: the callback fails with "Couldn't find foreign struct converter for 'cairo.Context'". We render into our own `cairo.ImageSurface` and upload it as a texture. If `python3-gi-cairo` appears, only `CairoLayer` changes. |
| Surface + upload cost at 1280x688 | about 1.0 ms Cairo + 1.4 ms upload per frame; the frame-clock tick averaged 17.9 ms (about 56 fps, startup included) in a 1.5 s run |
| Renderer, 100 nodes + 30 ropes, zoom 0.4 | median 3.3 ms (budget 16 ms); rope tick 0.9 ms for 30 ropes |
| `Widget.pick()` honours per-child `GskTransform` | pointer roles resolve correctly at any zoom (tested at 1.0 and 1.5) |
| `Gtk.Fixed` is itself pickable; `set_can_target(False)` on it disables its children too | do not rely on the background layer for events: one capture-phase gesture on `CanvasWidget` picks and routes |
| Overhanging children (negative margins) are pickable | resize handles and pins straddle the border |
| Python `round()` is banker's rounding, Swift `.rounded()` rounds half away from zero | `round_half_away()`; otherwise a right edge at 200 snaps to 192 instead of 208 (the grid is 16) |
| `Vte` typelib present is 2.91 (GTK 3); there is no `Vte 3.91` | **VTE for GTK 4 is not usable from PyGObject here** (needs `gir1.2-vte-3.91`). The terminal node is the open risk of the native path. |

## Input routing

One `Gtk.GestureDrag` (capture phase, any button) resolves `target_at(x, y)` and calls the engine; chrome roles are claimed,
content roles are DENIED so the child widget keeps the event. Child widgets carry only a role tag, no gestures.

| Input | Action |
|---|---|
| left drag on header | move node (selection moves as a group; Ctrl = magnetic snapping; Alt = duplicate ghost; < 3 px = click) |
| left drag on edge/corner | resize, active edge snaps to the 16 grid, minimum 160x80 |
| left drag from a pin | rope preview; release on another pin emits `connect-requested` (never mutates the scene by itself) |
| left drag on empty space | marquee (Shift adds); a plain click deselects; with a draw tool active, draws a grid-snapped frame |
| middle drag, Space + left drag | pan |
| scroll | pan (Shift swaps the axis); Ctrl + scroll zooms at the pointer; over node content only Ctrl is taken |
| pinch | zoom at the pointer |
| Ctrl `=` `-` `0`, Ctrl `W`, Ctrl `A` | zoom step 0.25 / reset / delete selection / select all (Ctrl chords also work with focus inside a node) |
| Delete, arrows (Shift = x10), `\`, `P`, Esc | only when focus is on the canvas, never stolen from a terminal or editor |

macOS Command maps to Ctrl, per `docs/reference/maestri-reference-index.md`.

## API for the rest of the app

`CanvasWidget.add_node(NodeItem, content_widget)`, `remove_node`, `update_node_frame`, `add_connection`, `set_tool`.
Ids are normalized to uppercase. GObject signals: `node-moved`, `node-resized` (id, x, y, w, h, final), `selection-changed`,
`connect-requested` (a, side_a, b, side_b), `delete-requested`, `duplicate-requested`, `draw-requested`, `tool-changed`, `viewport-changed`.
The widget never creates, deletes or persists anything: the application confirms with the core and then calls `add_node` /
`add_connection` (the rope the user dragged is only a request). Call everything from the GTK main thread (`GLib.idle_add` from
PTY/WebSocket threads).

Mapping to `maestri_core.signals.bus` (not wired: that package does not import yet): `node-moved`, `node-resized`,
`selection-changed` -> `node-moved`, `node-resized`, `node-selected`; `viewport-changed` -> `viewport-changed`;
`connect-requested` -> POST connection, then `connection-created`.

## Not done / not verified

- No real pointer or touchpad input was injected (not possible on Wayland from a test): the engine is unit-tested with resolved targets,
  and `target_at` and placement are tested against real GTK, but `GestureDrag`/`GestureZoom` wiring is untested by input.
- HiDPI (scale factor 2) and X11 are untested; only scale 1 on Wayland was run.
- Node content: no terminal (see the VTE row), note editor, portal, or file tree; the demo uses labels and a TextView.
- Canvas-drawn kinds (shape, stroke, freehand, text) are placeholders; no minimap, context menu, toolbar, or hold-Ctrl terminal numbering.
- The `L` shortcut (start a connection from the selected terminal) is not implemented.
- Text selection and caret placement inside a real content widget (TextView, VTE) while the zoom is not 1 have not been exercised: only labels were used.
