"""Thumbnails for the FileTree grid mode.

Lookup order for a file (all off the main thread or async; the grid shows the themed icon until a texture arrives):
  1. the freedesktop thumbnail cache (`thumbnail::path` from Gio, written by Nautilus & co.)
  2. GnomeDesktop.DesktopThumbnailFactory (optional, gnome-desktop-4): runs the system thumbnailers (video, PDF, fonts...) and
     saves the result to that same cache
  3. image/* decoded and scaled with GdkPixbuf on a worker thread
Textures are cached in memory by (path, mtime, size) with LRU eviction. At most MAX_JOBS decodes run at once.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib

THUMB_ATTRS = "thumbnail::path,thumbnail::is-valid,thumbnail::failed,time::modified"
MAX_JOBS = 2
CACHE_SIZE = 256
MAX_SOURCE_BYTES = 64 * 1024 * 1024   # never decode a gigantic image just for a 96px cell

_factory = None
_factory_tried = False


def _gnome_factory():
    global _factory, _factory_tried
    if not _factory_tried:
        _factory_tried = True
        try:
            gi.require_version("GnomeDesktop", "4.0")
            from gi.repository import GnomeDesktop
            _factory = GnomeDesktop.DesktopThumbnailFactory.new(GnomeDesktop.DesktopThumbnailSize.NORMAL)
        except (ValueError, ImportError, AttributeError):
            _factory = None
    return _factory


def texture_from_pixbuf(pb: GdkPixbuf.Pixbuf) -> Gdk.Texture:
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(), pb.get_rowstride())


def load_scaled(path: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Decode `path` to fit size x size (aspect kept, EXIF orientation applied). Blocking: call from a worker thread."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except GLib.Error:
        return None
    return pb.apply_embedded_orientation() or pb


class Thumbnailer:
    def __init__(self, size: int = 96) -> None:
        self.size = size
        self._cache: OrderedDict[tuple, Gdk.Texture] = OrderedDict()
        self._queue: list[tuple] = []
        self._running = 0
        self._pending: dict[tuple, list[Callable]] = {}

    @staticmethod
    def key(info: Gio.FileInfo, path: str, size: int) -> tuple:
        dt = info.get_modification_date_time() if info.has_attribute("time::modified") else None
        return (path, dt.to_unix() if dt else 0, size)

    def cached(self, info: Gio.FileInfo, path: str) -> Gdk.Texture | None:
        k = self.key(info, path, self.size)
        tex = self._cache.get(k)
        if tex is not None:
            self._cache.move_to_end(k)
        return tex

    def request(self, info: Gio.FileInfo, path: str, cb: Callable[[Gdk.Texture], None]) -> bool:
        """Ask for a thumbnail; `cb(texture)` runs on the main thread when one exists. Returns False when the type has none."""
        k = self.key(info, path, self.size)
        if k in self._cache:
            cb(self._cache[k])
            return True
        ctype = info.get_content_type() or ""
        thumb = info.get_attribute_byte_string("thumbnail::path") if info.has_attribute("thumbnail::path") else None
        valid = info.get_attribute_boolean("thumbnail::is-valid") if info.has_attribute("thumbnail::is-valid") else bool(thumb)
        factory = _gnome_factory()
        uri = Gio.File.new_for_path(path).get_uri()
        if thumb and valid:
            job = ("file", thumb)
        elif ctype.startswith("image/") and info.get_size() <= MAX_SOURCE_BYTES:
            job = ("file", path)
        elif factory is not None and not (info.has_attribute("thumbnail::failed") and info.get_attribute_boolean("thumbnail::failed")) \
                and factory.can_thumbnail(uri, ctype, k[1]):
            job = ("gnome", uri, ctype, k[1])
        else:
            return False
        if k in self._pending:
            self._pending[k].append(cb)
            return True
        self._pending[k] = [cb]
        self._queue.append((k, job))
        self._pump()
        return True

    def _pump(self) -> None:
        while self._running < MAX_JOBS and self._queue:
            k, job = self._queue.pop()   # LIFO: the cells the user is looking at right now were requested last
            self._running += 1
            if job[0] == "gnome":
                self._run_gnome(k, *job[1:])
            else:
                threading.Thread(target=self._decode, args=(k, job[1]), daemon=True, name="maestri-thumb").start()

    def _decode(self, k: tuple, source: str) -> None:
        pb = load_scaled(source, self.size)
        GLib.idle_add(self._finish, k, pb)

    def _run_gnome(self, k: tuple, uri: str, ctype: str, mtime: int) -> None:
        factory = _gnome_factory()

        def generated(f, res):
            try:
                pb = f.generate_thumbnail_finish(res)
            except GLib.Error:
                f.create_failed_thumbnail_async(uri, mtime, None, lambda *_: None)
                self._finish(k, None)
                return
            f.save_thumbnail_async(pb, uri, mtime, None, lambda *_: None)   # into ~/.cache/thumbnails for next time
            scaled = pb
            if max(pb.get_width(), pb.get_height()) > self.size:
                s = self.size / max(pb.get_width(), pb.get_height())
                scaled = pb.scale_simple(max(1, int(pb.get_width() * s)), max(1, int(pb.get_height() * s)), GdkPixbuf.InterpType.BILINEAR)
            self._finish(k, scaled)

        factory.generate_thumbnail_async(uri, ctype, None, generated)

    def _finish(self, k: tuple, pb: GdkPixbuf.Pixbuf | None) -> bool:
        self._running -= 1
        cbs = self._pending.pop(k, [])
        if pb is not None:
            tex = texture_from_pixbuf(pb)
            self._cache[k] = tex
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            for cb in cbs:
                cb(tex)
        self._pump()
        return GLib.SOURCE_REMOVE
