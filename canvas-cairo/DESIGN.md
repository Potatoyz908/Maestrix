# CanvasWidget (GTK 4 + Cairo): design and status

Package: `canvas-cairo/maestri_canvas/`. Run the tests with `python3 -m pytest canvas-cairo` (64 tests; `test_widget.py` and `test_terminal.py` need a display, the
Vte 3.91 typelib and, for the end-to-end ones, the built `open-maestri-server`; each skips with a reason when its prerequisite is missing). Demo: `cd canvas-cairo && python3 -m maestri_canvas.demo`
(`--screenshot out.png --seconds 2` renders the real GTK widget tree to a PNG).

## Layers

```
CanvasWidget (Gtk.Overlay)
 ├─ CairoLayer(top=False)   grid, ropes, canvas-drawn nodes     (Gtk.DrawingArea draw_func; texture fallback)
 ├─ Gtk.Fixed               NodeFrame per node: header, content widget, 8 resize handles, 2 pins (GskTransform = pan/zoom)
 └─ CairoLayer(top=True)    gesture visuals: dragged rope, snap guides, marquee, Alt-drag ghosts (paints nothing when idle)

engine (no GTK):  geometry, viewport, snapping, resize, rope, scene (z caches, hit-test, pins), interaction (state machine)
renderer (no GTK): pycairo, draws on ANY cairo.Context
widget.py, terminal_node.py: the only files that import Gtk / Vte;  pty_transport.py: libsoup websocket client
```

Widget kinds (`terminal`, `stickyNote`, `portal`, `fileTree`) are real GTK widgets so VTE/WebView/TextView work and stack by z
(the terminal is implemented: see "Terminal node").
Other kinds (`shape`, `stroke`, `freehand`, `text`) are drawn by Cairo (currently placeholder rectangles).

## Verified on this host (GTK 4.22.4, PyGObject 3.56.2, Python 3.14, Wayland)

| Finding | Consequence |
|---|---|
| `gi._gi_cairo` is now available (it was missing at first) | `CairoLayer` is a `Gtk.DrawingArea` with `set_draw_func`. Same scene (100 nodes + 30 ropes) on both paths, whole-process CPU per frame: DrawingArea 5.8-6.6 ms vs texture 7.5-8.1 ms (about 15-25% less), HiDPI automatic. The texture path (own `ImageSurface` -> `Gdk.MemoryTexture`) stays as a fallback for hosts without the bridge; `tests/test_widget.py` renders both and requires near-identical pixels (0.13% of bytes differ, antialiasing). The callback time alone is misleading (DrawingArea only records; GSK rasterizes later), so measure whole-process CPU. |
| Renderer, 100 nodes + 30 ropes, zoom 0.4 | median 3.3 ms (budget 16 ms); rope tick 0.9 ms for 30 ropes |
| `Widget.pick()` honours per-child `GskTransform` | pointer roles resolve correctly at any zoom (tested at 1.0 and 1.5) |
| `Gtk.Fixed` is itself pickable; `set_can_target(False)` on it disables its children too | do not rely on the background layer for events: one capture-phase gesture on `CanvasWidget` picks and routes |
| Overhanging children (negative margins) are pickable | resize handles and pins straddle the border |
| Python `round()` is banker's rounding, Swift `.rounded()` rounds half away from zero | `round_half_away()`; otherwise a right edge at 200 snaps to 192 instead of 208 (the grid is 16) |
| `Vte-3.91.typelib` exists in `~/.local/lib/girepository-1.0`, which is NOT on the default search path | `gi.require_version("Vte", "3.91")` fails until the directory is added. `gi_env.ensure_typelib_paths()` does it from code (`gi.Repository.get_default().prepend_search_path`, or set `MAESTRI_TYPELIB_PATH`); no env var needed. The distro `gir1.2-vte-2.91` is the GTK 3 one and pulls Gtk 3 if imported. |

## Terminal node (`terminal_node.py`, `pty_transport.py`, `replay.py`)

The core owns the PTY and the shell (so `omaestri ask/check`, scrollback and agent features keep working). `Vte.Terminal` is only a
display: `feed()` for the bytes the core streams, the `commit` signal (typed keys and pastes) back to the transport. VTE never spawns
a process. `SoupPtyTransport` speaks the core's real protocol on libsoup 3 (GLib main loop, no threads): `Authorization: Bearer`
header (never `?token=`, so it stays out of logs), binary frames for output/keystrokes, text `{"type":"resize","cols","rows"}`.
Verified end to end against the real `open-maestri-server`: typed command and its output, `stty size` equal to the VTE grid (the resize
reached the PTY), reconnect, wrong token rejected. Findings from that run:

| Finding | Handling |
|---|---|
| The first frame of EVERY connection is a plain-text snapshot of the core's headless terminal: rows joined by bare `\n` (no `\r`), padded with one `\n` per empty row. Fed raw, VTE shows a staircase and the prompt lands at the bottom, far from the shell's cursor. | `replay.normalize_replay`: first frame only, if it has `\n` but no `\r`/ESC, strip trailing blank rows and convert to CRLF. A heuristic: a marker on the replay frame (core side) would remove the guess. |
| The core replays on every (re)connect | `open` with `reconnected=True` resets VTE first, so history is shown once, not twice |
| The core has no "exit" frame (that was a Web Shell proposal): a finished shell is just a closed socket | do not wait for one; the transport reconnects with backoff unless closed by the user |
| After `reset()` VTE keeps counting rows (cursor at row 27, then 52) while `vadjustment` bounds lag a frame | `text()` uses an over-wide range and lets VTE clamp it (reading by adjustment bounds returned an empty screen) |
| The terminal grid does not change with zoom (57x14 at zoom 1.0 and 1.6) | zoom is purely visual: no SIGWINCH storm while zooming; text is vector-crisp at 1.6 |
| `Ctrl+W` is both "delete node" (macOS Cmd+W) and readline's word-rubout | with focus inside a node only `Ctrl+Shift+W` deletes; `Ctrl+W` deletes only from the canvas |

Test isolation: the core resolves its data dir with `getpwuid`, NOT `$HOME`, so `HOME=...` does not isolate it, and its fixed
`~/.open-maestri/run/agent.sock` would replace the real app's socket. The e2e fixture runs it in a private user+mount namespace
(`unshare -Urm`, bind-mounting a temp dir over the account home) after a harmless pre-flight that proves the isolation, and always
terminates it.

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
| Ctrl `=` `-` `0` | zoom step 0.25 / reset (work with focus inside a node) |
| Ctrl `W` (canvas focus), Ctrl+Shift `W` (anywhere), Ctrl `A` (canvas focus) | delete selection / select all |
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
- Node content: the terminal is done; the note editor, portal and file tree are not (the demo uses labels and a TextView).
- Canvas-drawn kinds (shape, stroke, freehand, text) are placeholders; no minimap, context menu, toolbar, or hold-Ctrl terminal numbering.
- The `L` shortcut (start a connection from the selected terminal) is not implemented.
- Mouse text selection and caret placement inside VTE/TextView while the zoom is not 1 are untested (no pointer injection). Rendering at zoom 1.6 was checked on a screenshot only.
- VTE shows the replay as plain text: colours/attributes of the history are lost on reconnect (the core only keeps plain text), and the prompt line may need a keypress to redraw.
