"""Quick Look for the FileTree: Space on a selected file opens a floating preview window, Space / Escape closes it.

What it shows (`preview_mode`, pure):
    image/*            Gtk.Picture (scaled to fit, keeps aspect)
    video/*, audio/*   Gtk.Video (GStreamer media backend, autoplay)
    text-ish           read-only monospace Gtk.TextView with the first MAX_TEXT_BYTES
    PDF / HTML         WebKit 6.0 WebView on the file:// URI, when the typelib is present
    anything else      an info card: icon, type, size, modification date and "Open" (default app)
Arrow keys in the window ask the owner for the previous / next file (`navigate(+1|-1)` returns the new path or None).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .file_ops import is_text_file

MAX_TEXT_BYTES = 512 * 1024


def preview_mode(path: str, content_type: str | None, webkit: bool = False) -> str:
    if os.path.isdir(path):
        return "info"
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith(("video/", "audio/")):
        return "media"
    if is_text_file(path, content_type):
        return "text"
    if webkit and ct in ("application/pdf", "text/html", "application/xhtml+xml"):
        return "web"
    return "info"


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{n} bytes"


class QuickLookWindow(Gtk.Window):
    __gtype_name__ = "MaestriQuickLookWindow"

    def __init__(self, parent: Gtk.Window | None = None, navigate: Callable[[int], str | None] | None = None) -> None:
        super().__init__(title="Quick Look", default_width=760, default_height=560, modal=False, hide_on_close=False)
        self.add_css_class("maestri-quick-look")
        if parent is not None:
            self.set_transient_for(parent)
        self._navigate = navigate
        self.path = ""
        self.mode = ""
        self._web = None
        header = Gtk.HeaderBar()
        self._open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Open with default application")
        self._open_btn.connect("clicked", lambda _b: self.open_external())
        header.pack_end(self._open_btn)
        self.set_titlebar(header)
        self._holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.set_child(self._holder)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    # ---- public -------------------------------------------------------------------------------------
    def show_path(self, path: str) -> None:
        self.path = path
        self.set_title(os.path.basename(path) or path)
        try:
            info = Gio.File.new_for_path(path).query_info("standard::*,time::modified", Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error as e:
            self._set_body(self._message(f"Can't read {os.path.basename(path)}: {e.message}"))
            self.mode = "error"
            return
        from .gi_env import webkit_available
        self.mode = preview_mode(path, info.get_content_type(), webkit_available())
        self._set_body(getattr(self, f"_build_{self.mode}")(path, info))

    def dispose(self) -> None:
        self._set_body(None)
        self.destroy()

    def open_external(self) -> None:
        if self.path:
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(self.path).get_uri(), None)
            except GLib.Error:
                pass

    # ---- bodies ----------------------------------------------------------------------------------------
    def _set_body(self, w: Gtk.Widget | None) -> None:
        old = self._holder.get_first_child()
        if old is not None:
            if isinstance(old, Gtk.Video) and old.get_media_stream() is not None:
                old.get_media_stream().pause()
            if self._web is not None and old is self._web:
                self._web.stop_loading()
                self._web = None
            self._holder.remove(old)
        if w is not None:
            w.set_hexpand(True)
            w.set_vexpand(True)
            self._holder.append(w)

    def _build_image(self, path: str, _info) -> Gtk.Widget:
        pic = Gtk.Picture.new_for_filename(path)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_can_shrink(True)
        return pic

    def _build_media(self, path: str, _info) -> Gtk.Widget:
        v = Gtk.Video.new_for_filename(path)
        v.set_autoplay(True)
        return v

    def _build_text(self, path: str, info) -> Gtk.Widget:
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_TEXT_BYTES + 1)
        except OSError as e:
            return self._message(f"Can't read: {e.strerror}")
        text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_TEXT_BYTES:
            text += f"\n\n… (preview truncated at {human_size(MAX_TEXT_BYTES)}; file is {human_size(info.get_size())})"
        tv = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        tv.get_buffer().set_text(text)
        self.text_view = tv
        sc = Gtk.ScrolledWindow()
        sc.set_child(tv)
        return sc

    def _build_web(self, path: str, _info) -> Gtk.Widget:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        web = WebKit.WebView()
        web.load_uri(Gio.File.new_for_path(path).get_uri())
        self._web = web
        return web

    def _build_info(self, path: str, info: Gio.FileInfo) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        img = Gtk.Image(pixel_size=128)
        icon = info.get_icon()
        if icon is not None:
            img.set_from_gicon(icon)
        box.append(img)
        name = Gtk.Label(label=info.get_display_name(), wrap=True, justify=Gtk.Justification.CENTER)
        name.add_css_class("title-2")
        box.append(name)
        ct = info.get_content_type() or ""
        lines = [Gio.content_type_get_description(ct) if ct else "Unknown type"]
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            try:
                lines.append(f"{len(os.listdir(path))} items")
            except OSError:
                pass
        else:
            lines.append(human_size(info.get_size()))
        dt = info.get_modification_date_time()
        if dt is not None:
            lines.append("Modified " + datetime.fromtimestamp(dt.to_unix()).strftime("%Y-%m-%d %H:%M"))
        details = Gtk.Label(label="\n".join(lines), justify=Gtk.Justification.CENTER, ellipsize=Pango.EllipsizeMode.MIDDLE)
        details.add_css_class("dim-label")
        box.append(details)
        self.info_label = details
        return box

    @staticmethod
    def _message(text: str) -> Gtk.Widget:
        return Gtk.Label(label=text, wrap=True)

    # ---- keys ---------------------------------------------------------------------------------------------
    def _key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        name = (Gdk.keyval_name(keyval) or "").lower()
        if name in ("space", "escape") and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            self.close()
            return True
        if name in ("up", "down", "left", "right") and self._navigate is not None:
            nxt = self._navigate(-1 if name in ("up", "left") else 1)
            if nxt:
                self.show_path(nxt)
            return True
        return False
