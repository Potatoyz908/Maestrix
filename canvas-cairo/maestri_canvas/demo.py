"""Interactive demo / screenshot harness:  python -m maestri_canvas.demo [--screenshot out.png --seconds 1.5]

The terminal node is a real VTE shell; other content widgets are stand-ins (a TextView) for the app's node factories."""
from __future__ import annotations

import argparse
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from .geometry import Point, Rect
from .scene import ConnItem, NodeItem
from .viewport import Viewport
from .widget import CanvasWidget


def build_canvas() -> CanvasWidget:
    canvas = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    tv = Gtk.TextView(); tv.get_buffer().set_text("# a note\nplain text editor stand-in")
    canvas.add_node(NodeItem("AAAAAAAA-0000-0000-0000-000000000001", Rect(60, 80, 360, 220), 1, "terminal", "Terminal A"), focus=True)   # native VTE login shell
    canvas.add_node(NodeItem("BBBBBBBB-0000-0000-0000-000000000002", Rect(560, 140, 320, 240), 2, "stickyNote", "Note 1"), tv)
    canvas.add_node(NodeItem("CCCCCCCC-0000-0000-0000-000000000003", Rect(300, 400, 220, 120), 3, "shape", "shape"))
    canvas.add_connection(ConnItem("11111111-0000-0000-0000-000000000001", "AAAAAAAA-0000-0000-0000-000000000001", "BBBBBBBB-0000-0000-0000-000000000002"))
    return canvas


def build_window(app: Gtk.Application) -> tuple[Gtk.ApplicationWindow, CanvasWidget]:
    win = Gtk.ApplicationWindow(application=app, title="Maestri canvas demo", default_width=1000, default_height=620)
    canvas = build_canvas()
    win.set_child(canvas)
    return win, canvas


def screenshot(widget: Gtk.Widget, path: str) -> None:
    paint = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paint.snapshot(snap, widget.get_width(), widget.get_height())
    node = snap.to_node()
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
