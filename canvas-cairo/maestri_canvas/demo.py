"""Interactive demo / screenshot harness:  python -m maestri_canvas.demo [--screenshot out.png --seconds 1.5]

The terminal node is a real VTE shell; other content widgets are stand-ins (a TextView) for the app's node factories."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from .geometry import Point, Rect
from .note_model import NoteMeta
from .note_storage import MemoryNoteStorage
from .scene import ConnItem, NodeItem
from .sticky_note import StickyNoteView
from .viewport import Viewport
from .widget import CanvasWidget

DEMO_PAGE = Path(__file__).with_name("demo_page.html")
DEMO_NOTE = """# Sprint notes

Plain **bold**, *italic*, ~~strike~~ and `inline code`.

## Todo
- [x] markdown cards
- [ ] colour toolbar
  - nested item
1. first
2. second

> a quote with a [link](https://example.com)

| Name | Qty |
|------|----:|
| apple | 3 |
| fig | 12 |

```
code block **not** parsed
```
"""


def build_canvas() -> CanvasWidget:
    canvas = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    canvas.add_node(NodeItem("AAAAAAAA-0000-0000-0000-000000000001", Rect(60, 80, 360, 220), 1, "terminal", "Terminal A"), focus=True)   # native VTE login shell
    # native sticky note, seeded in memory (a real app sets canvas.notes_client so notes live in the core's notes/ folder)
    note = NoteMeta(file_name="Note 1.md", is_previewing=True)
    canvas.add_node(NodeItem("BBBBBBBB-0000-0000-0000-000000000002", Rect(560, 140, 340, 300), 2, "stickyNote", "Note 1"),
                    StickyNoteView(note, MemoryNoteStorage(DEMO_NOTE)))
    canvas.add_node(NodeItem("EEEEEEEE-0000-0000-0000-000000000005", Rect(940, 60, 300, 420), 4, "fileTree", "Files"),
                    cwd=str(Path(__file__).resolve().parents[1]))   # native file tree rooted at canvas-cairo/
    canvas.add_node(NodeItem("DDDDDDDD-0000-0000-0000-000000000004", Rect(60, 560, 420, 260), 5, "portal", "Portal"),
                    portal={"id": "DDDDDDDD-0000-0000-0000-000000000004", "name": "Portal", "currentURL": DEMO_PAGE.as_uri(), "source": {},
                            "status": "idle", "chromeHidden": False, "storageScope": "isolated"})
    canvas.add_node(NodeItem("CCCCCCCC-0000-0000-0000-000000000003", Rect(300, 400, 220, 120), 3, "shape", "shape"))
    canvas.add_connection(ConnItem("11111111-0000-0000-0000-000000000001", "AAAAAAAA-0000-0000-0000-000000000001", "BBBBBBBB-0000-0000-0000-000000000002"))
    return canvas


def build_window(app: Gtk.Application) -> tuple[Gtk.ApplicationWindow, CanvasWidget]:
    win = Gtk.ApplicationWindow(application=app, title="Maestri canvas demo", default_width=1280, default_height=860)
    canvas = build_canvas()
    win.set_child(canvas)
    return win, canvas


def screenshot(widget: Gtk.Widget, path: str) -> None:
    paint = Gtk.WidgetPaintable.new(widget)
    ctx = GLib.MainContext.default()
    node = None
    for _ in range(40):   # to_node() is None until the widget has produced a frame: let the main loop run and retry (was flaky)
        snap = Gtk.Snapshot()
        paint.snapshot(snap, widget.get_width(), widget.get_height())
        node = snap.to_node()
        if node is not None:
            break
        for _i in range(20):
            ctx.iteration(False)
        GLib.usleep(10_000)
    if node is None:
        raise RuntimeError("widget produced no render node (not realized or zero-sized)")
    widget.get_native().get_renderer().render_texture(node, None).save_to_png(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screenshot")
    ap.add_argument("--seconds", type=float, default=1.5)
    args = ap.parse_args(argv)
    app = Gtk.Application(application_id="dev.maestri.canvasdemo", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(a: Gtk.Application) -> None:
        win, canvas = build_window(a)
        win.present()
        if args.screenshot:
            def shot() -> bool:
                screenshot(canvas, args.screenshot)
                a.quit()
                return False
            GLib.timeout_add(int(args.seconds * 1000), shot)

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
