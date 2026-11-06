# CanvasWidget (GTK 4 + Cairo): design and status

Package: `canvas-cairo/maestri_canvas/`. Run the tests with `python3 -m pytest canvas-cairo` (142 tests; `test_widget.py`, `test_terminal.py` and `test_sticky_note.py` need a display, the
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
Other kinds are drawn by Cairo: `text`, `shape`, `freehand` for real (`drawn.py`, see "Drawn nodes"); `stroke` is still a placeholder rectangle.

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

## Sticky note (`sticky_note.py`, `note_*.py`, `markdown_spans.py`)

`canvas.add_node(item, note={...StickyNoteContent...})` builds a `StickyNoteView`; the card is styled through the node frame, the header
widgets come from a small protocol (`header_left` / `header_title` / `header_right` / `frame_attached`) that `NodeFrame` honours for any content.

| Piece | How |
|---|---|
| Header | `Raw|Formatted` toggles <-> `isPreviewing`; title (first line, or the custom name); `⋯` menu: Rename, Move to…, Larger/Smaller text, Delete. Double-click on the header starts renaming (`CanvasWidget._header_double_click`: 400 ms / 6 px). While the title is edited it is retagged `content` so the caret works; otherwise it drags the node. |
| Body | `Gtk.Stack`: `raw` = monospace `TextView`; `formatted` = read-only `TextView` with `Gtk.TextTag`s from `parse_markdown` (h1-h3, bold/italic/strike, code, fenced code, lists/tasks, quotes, tables in monospace with display-width alignment, links, inline images). No GtkSource. |
| Colours | 7 preset swatches + `Gtk.ColorDialogButton`, in a footer. The persisted `color` string is kept VERBATIM ("yellow" stays "yellow"); it is resolved only to render. One CSS rule per resolved colour (two spellings share it); text colour flips on dark colours. |
| Persistence | debounce 500 ms (`NoteSaver`: latest text only, one write in flight, error keeps the text for retry, `cancel()` for delete) -> `NoteStorage`: `CoreNoteStorage` (REST `/api/notes/{name WITHOUT .md}`, libsoup async, Bearer header) or `LocalNoteStorage` (custom path; Gio `replace_contents`: temp file + rename, verified by a new inode and no leftovers). |
| Metadata | `NoteMeta` round-trips unknown keys; `meta-changed` fires only when the persisted dict really changes (`bind()` does not echo). |
| External edits | `Gio.FileMonitor` on the file; applied only when the buffer is clean, as a minimal diff so the cursor follows its text (tested: append keeps offset 7, prepend moves it to 11); with unsaved edits the user's text wins and the status says so. |
| Rename / Move / Delete | rename = flush, then core `get` + `put` new + `delete` old (no rename endpoint), or `set_display_name` for a custom file; Move = atomic write to the folder, old copy deleted only after; Delete = cancel pending save, wait for an in-flight write, delete, then `delete-requested`. `request_delete()` shows the `Gtk.AlertDialog`; `delete_file()` does not. |
| Images | paste (Ctrl+V with an image and no text) saves `<name>-<ms>.png` NEXT TO the .md and inserts `![](<…>)` (angle brackets when the name has a space); rendering never fetches the network. Needs a folder: `notes_dir` for managed notes, else the custom file's folder. |

Findings that cost time and are worth knowing:

| Finding | Handling |
|---|---|
| The user theme (`WhiteSur-Dark-purple`) is installed as `~/.config/gtk-4.0/gtk.css`, which loads at **USER** priority (800) and beats a provider at APPLICATION+1 whatever the selector specificity; the note body stayed dark and the swatches grey | the note CSS provider is added at USER priority (only note-specific classes are styled, so nothing else is affected) |
| The expected "text goes blurry above 1.5x zoom" does not happen: GSK re-renders text at the transform's scale | no re-render at font-size x zoom; checked at 1.6 on a screenshot |
| Footer controls (font +/-, status) pushed the card wider than its frame (340) and it slid under the neighbour | font size moved to the menu; status is ellipsized; swatches 14 px |
| `bind()` re-emitted `meta-changed` (flipping the toggle buttons fired `toggled`) | `_mode_sync` guard; an echo would loop app -> core -> bind -> app |
| Delete with a pending debounced save recreated the file | `NoteSaver.cancel()` + wait for the in-flight write; a failed delete re-queues the cancelled text |
| The core's note name must not contain `.md` (it appends it: `x.md` -> `x.md.md`) | `NoteMeta.name` strips it; e2e asserts `Ideas.md` exists and `Ideas.md.md` does not |
| `Gtk.EditableLabel` has no `set_xalign`; a screenshot helper intermittently got no render node before the first frame | removed; `demo.screenshot` now retries while the main loop runs |

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

- Sticky note: no real pointer or keyboard input was injected either: clicks on swatches/toggles are `emit("clicked")`/`set_active`, double-click is fed to `_header_double_click`, Ctrl+V image paste is exercised through `paste_texture` (the key handler and `Gdk.Clipboard.read_texture_async` path are not). `Gtk.FileDialog` (Move to…) and `Gtk.AlertDialog` (Delete) were not driven; their callbacks (`move_to`, `delete_file`) are tested.
- Sticky note: dropping a file emits `file-dropped` and the app must call `adopt_file` (the spec is ambiguous about replacing an existing note's storage); a managed note is watched only if the app passes `watch_path` (the core's notes folder path is not known to the widget); managed-note Rename is copy + delete, not atomic across the two requests.
- No real pointer or touchpad input was injected (not possible on Wayland from a test): the engine is unit-tested with resolved targets,
  and `target_at` and placement are tested against real GTK, but `GestureDrag`/`GestureZoom` wiring is untested by input.
- HiDPI (scale factor 2) and X11 are untested; only scale 1 on Wayland was run.
- Node content: the terminal and the sticky note are done (the file tree is another agent's `file_tree_node.py`); the portal is not.
- Canvas-drawn `stroke` (line/arrow) is still a placeholder; drawn nodes have no context toolbar (colour, stroke style, font) yet, so they keep the macOS defaults. No minimap or hold-Ctrl terminal numbering.
- The `L` shortcut (start a connection from the selected terminal) is not implemented.
- Mouse text selection and caret placement inside VTE/TextView while the zoom is not 1 are untested (no pointer injection). Rendering at zoom 1.6 was checked on a screenshot only.
- VTE shows the replay as plain text: colours/attributes of the history are lost on reconnect (the core only keeps plain text), and the prompt line may need a keypress to redraw.

## Portal (Sprint 3)

`portal_model.py` (pure: lossless PortalContent, `normalize_url` = http/https/file/about only), `portal_node.py` (`PortalView`: WebKit 6.0 WebView + back/forward/reload, URL bar, progress, hide-chrome; automation `evaluate/text/html/snapshot/screenshot/info`), `connection_rules.py` (the 5 schema-v2 pairs, terminal first, ids uppercase, `purge_node` over the 6 lists).

- WebKit-6.0 + JavaScriptCore-6.0 typelibs live in `~/.local/lib/girepository-1.0` (from `gir1.2-webkit-6.0` / `gir1.2-javascriptcoregtk-6.0`, extracted without root). Without them the node is a Gtk.Label placeholder. The system WebKit2-4.1 is never imported.
- `currentURL` is persisted on load FINISHED only (never per redirect, never `about:`); `source` and unknown keys are preserved verbatim; `meta-changed` fires only on a real change; `bind()` does not echo.
- isolated = own ephemeral NetworkSession; portal<->portal connected group = ONE shared session (WebView rebuilt + current page reloaded, since a session is construct-only); disconnect/delete gives a fresh isolated one.
- Refused connection pairs: toast + red flash on both nodes + `connection-refused` signal. Optimistic `local-…` ropes: `replace_connection_id` on success, `remove_connection` + error on POST failure.
- Ctrl+scroll: the canvas' capture-phase scroll controller is an ancestor of the WebView, so it runs first. Verified with REAL wheel events (xdotool XTEST under Xvfb, `tests/e2e/test_gtk4_canvas_input_xvfb.py`): Ctrl+wheel over the page zooms the canvas and WebKit's page zoom stays 1.0; a plain wheel leaves the canvas alone and scrolls the page.

## Context toolbars (Sprint 5: `context_toolbar.py`, `markdown_format.py`)

A floating pill over the ONE selected, unlocked node: centred just above it in screen space, below it when there is no room,
clamped to the canvas; hidden during a gesture, while the drawn-text editor is open and while the node is off screen. Hosted
by `CanvasWidget.context_toolbar` (`ContextToolbarHost.sync` runs in the frame tick; re-created only when the target changes).
It is an overlay above every layer, tagged with the node's "content" role, so the capture-phase drag gesture DENIES presses on it
(no marquee, no deselect) and they reach the buttons. Buttons have `focus_on_click=False`: Bold must not steal the note's caret.
(macOS docks these toolbars at the bottom of the canvas; here they sit over the node, as asked for this sprint.)

| Toolbar | Content |
|---|---|
| `NoteContextToolbar` | B / I / S / `</>` / H1 / H2 / list / task on the RAW Markdown: `markdown_format.format_edit` (pure) -> one buffer user action (undo + the debounced save path). Toggles: same marker again removes it; H1<->H2 and list<->task replace each other; a formatted note switches to Raw first. |
| `DrawingContextToolbar` | palette (the 10 `colorFromString` names; shape = stroke AND fill, text = `color`), stroke width 1-20 pt (shape/freehand) or font size 8-96 (text), stroke style + fill style menus (shape), delete. Emits `drawn-content-changed` with the merged dict; the app persists and calls `set_node_content`, which refreshes the toolbar. A text label re-measures (bigger font -> bigger frame). |

## Drawn nodes (Sprint 4: `drawn.py`, `drawn_editor.py`, `gtk4-ui/src/ui/drawing.py`)

| Piece | How |
|---|---|
| Model | content = the schema-v2 `_0` dict, kept verbatim on `NodeItem.content` (unknown keys survive). Defaults = the Swift initialisers; `tests/test_drawn.py` parses `NodeContent.swift` and requires exactly its keys (Swift decodes them with synthesized Codable: one missing key fails the whole workspace on macOS). |
| Tools | macOS names: `text`, `rect`, `ellipse`, `diamond`, `freehand_pen`, `freehand_highlighter` (header button "✏️ Desenho"). A drag draws a grid-snapped frame; a click makes a 45x35 label (text) or a 200x160 shape. Freehand samples every 2 screen px; frame = bbox + 8 padding, points normalised 0..1 (CanvasInteractionHandler). With a tool active, pressing on a drawn node draws over it; a widget node's header still drags. |
| Paint | `CairoRenderer` -> `drawn.draw_node`: Swift palette (`colorFromString`, pastel "blue" etc.), shape fill 30% (hatched 10% + 8 px hatch at 60%), dashed `[8,4]`/dotted `[2,4]`, caption = stroke colour 35% darker; freehand = Catmull-Rom like `FreehandNodeSwiftUIView`; text via PangoCairo (toy-API fallback). Selected drawn nodes get the dashed outline here (they have no NodeFrame); an empty label shows a faint box. |
| Input | no widget, so `CanvasWidget._drawn_target` hit-tests them in canvas space when GTK picking finds nothing: body = drag (header role), a selected node exposes 8 edges (6 px). Resize floor 16x16 (widget nodes keep 160x80). |
| Text edit | double-click (or a new label) -> `Gtk.Popover` + `TextView` over the node; Enter commits, Shift+Enter newline, Esc cancels, click-away commits -> `drawn-content-changed`. The app merges the dict and grows a label to `measure_text`. |
| Signals | `draw-requested` (tool, frame; widget tools such as P = portal now create the node at the drawn frame), `freehand-drawn` (tool, frame, points), `drawn-content-changed` (id, dict). API: `set_node_content`, `edit_drawn_text`. |
