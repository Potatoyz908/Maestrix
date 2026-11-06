"""Native sticky note: the content widget of a `stickyNote` node (spec: docs/reference §5 + the architect's GTK mapping).

Header (provided to NodeFrame through the `header_*` protocol): [Raw|Formatted] toggle <-> isPreviewing, editable title, "⋯" menu
(Rename / Move to… / Delete). Body: Gtk.Stack with "raw" (monospace TextView) and "formatted" (read-only TextView rendering Markdown with
Gtk.TextTags: headings, bold/italic/strike, code, lists, quotes, tables, links, inline images). Footer: colour swatches, font size, status.

Persistence: edits are debounced (500 ms) and written through a NoteStorage (core REST API for managed notes, atomic local file for
custom paths). The persisted metadata (`NoteMeta`) keeps the colour string verbatim and unknown keys; `meta-changed` fires only on a real
change. A file monitor reflects external writes (agents) without moving the cursor, and never overwrites unsaved edits."""
from __future__ import annotations

import os
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from .markdown_spans import Run, parse_markdown
from .note_colors import (PRESETS, clamp_font_size, css_class, css_font_size_rule, css_rule, resolve_hex, to_rgb)
from .note_model import NoteMeta, note_name, title_from_text, unique_name, valid_note_name
from .note_saver import NoteSaver
from .note_storage import (CoreNoteStorage, CoreNotesClient, LocalNoteStorage, NoteStorage, is_note_file, watch_file)

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_W = 520

_BASE_CSS = """
.maestri-note { border-radius: 10px; box-shadow: 0 2px 8px alpha(black, .25); }
.maestri-note textview, .maestri-note textview text { background: transparent; color: inherit; }
.maestri-note textview { padding: 6px 8px; }
.maestri-note textview.note-raw { font-family: monospace; }
.maestri-note .maestri-node-header { padding: 0 6px; }
.maestri-note .maestri-node-header button { color: inherit; }   /* the theme's light button text vanishes on a pastel header */
.maestri-note .maestri-node-header button, .maestri-note .maestri-node-header togglebutton { min-height: 0; min-width: 0; padding: 0 7px; }
.maestri-note .maestri-node-header editablelabel { background: transparent; }
.note-bar { padding: 3px 6px; border-radius: 0 0 9px 9px; }
.note-bar colorbutton, .note-bar colorbutton button { min-width: 0; min-height: 0; padding: 0 2px; }
button.note-swatch { min-width: 14px; min-height: 14px; padding: 0; border-radius: 50%; border: 1px solid alpha(black, .35); box-shadow: none; }
button.note-swatch.active { outline: 2px solid #3b82f6; outline-offset: 1px; }
.note-status { font-size: 11px; opacity: .65; }
.note-status.error { color: #c01c28; opacity: 1; }
"""


class _NoteCss:
    """One display-wide provider: static rules + one rule per colour actually used (a colour spelled two ways shares its rule)."""

    def __init__(self) -> None:
        self.provider: Gtk.CssProvider | None = None
        self.colors: set[str] = set()

    def ensure(self, *colors: str) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        new = {resolve_hex(c) for c in colors} - self.colors
        if self.provider is not None and not new:
            return
        self.colors |= new
        swatches = "".join(f"button.note-swatch.note-sw-{h[1:].lower()} {{ background: {h}; }}\n" for h, _ in PRESETS)
        css = _BASE_CSS + css_font_size_rule() + swatches + "".join(css_rule(c) for c in sorted(self.colors))
        if self.provider is None:
            self.provider = Gtk.CssProvider()
            # USER priority on purpose: themes installed as ~/.config/gtk-4.0/gtk.css load at USER (800) and would beat anything lower,
            # painting the note body with the theme's dark `textview` background and flattening the swatches. Only note-specific
            # classes are styled here, so winning over the theme cannot affect other widgets.
            Gtk.StyleContext.add_provider_for_display(display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.provider.load_from_string(css)


_css = _NoteCss()


def _md_dest(path: str) -> str:
    return f"<{path}>" if any(c in path for c in " ()") else path


class StickyNoteView(Gtk.Box):
    __gtype_name__ = "MaestriStickyNoteView"
    __gsignals__ = {
        "meta-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),    # persisted dict (StickyNoteContent): fires on real changes only
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),       # the file is gone: the node should be removed
        "file-dropped": (GObject.SignalFlags.RUN_FIRST, None, (str,)),       # a .md/.markdown/.txt was dropped: the app decides (adopt_file)
    }

    def __init__(self, meta: NoteMeta | dict, storage: NoteStorage, client: CoreNotesClient | None = None,
                 name_taken: Callable[[str], bool] | None = None, base_dir: str | None = None, watch_path: str | None = None,
                 debounce_ms: int = 500) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.meta = meta if isinstance(meta, NoteMeta) else NoteMeta.from_dict(meta)
        self.storage, self.client, self.name_taken = storage, client, name_taken
        self.base_dir, self._watch_path = base_dir, watch_path
        self._frame: Gtk.Widget | None = None
        self._applied: list[str] = []
        self._applying = False
        self._busy = False
        self._disposed = False
        self._loaded = False
        self._saved_text = ""
        self._last_meta = self.meta.to_dict()
        self._shown_title = ""
        self._monitor: Gio.FileMonitor | None = None
        self._links: dict[str, str] = {}
        self._open_uri: Callable[[str], None] = lambda uri: Gio.AppInfo.launch_default_for_uri(uri, None)
        self.saver = NoteSaver(lambda text, done: self.storage.write(text, done), debounce_ms, self._on_save_state,
                               lambda fn, ms: GLib.timeout_add(ms, lambda: (fn(), GLib.SOURCE_REMOVE)[1]), GLib.source_remove)

        self._build_header()
        self._build_body()
        self._build_footer()
        self.add_css_class("maestri-note-content")
        _css.ensure(self.meta.color)
        self._set_mode(self.meta.is_previewing, emit=False)
        self._update_swatches()
        self.status.set_text("Loading…")
        self.storage.read(self._on_loaded)
        self._start_watch()

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    # ---- header (NodeFrame protocol) ---------------------------------------------------------------------
    def _build_header(self) -> None:
        self.raw_btn = Gtk.ToggleButton(label="Raw")
        self.fmt_btn = Gtk.ToggleButton(label="Formatted")
        self.fmt_btn.set_group(self.raw_btn)
        self._mode_sync = False   # True while WE flip the buttons: their "toggled" must not be mistaken for a user click
        self.fmt_btn.connect("toggled", lambda b: b.get_active() and not self._mode_sync and self._set_mode(True))
        self.raw_btn.connect("toggled", lambda b: b.get_active() and not self._mode_sync and self._set_mode(False))
        mode = Gtk.Box()
        mode.add_css_class("linked")
        mode.append(self.raw_btn)
        mode.append(self.fmt_btn)

        self.title_label = Gtk.EditableLabel(text=self.meta.name)
        self.title_label.set_hexpand(True)
        self.title_label.connect("notify::editing", self._on_title_editing)

        menu = Gio.Menu()
        menu.append("Rename", "note.rename")
        menu.append("Move to…", "note.move")
        menu.append("Larger text", "note.larger")
        menu.append("Smaller text", "note.smaller")
        menu.append("Delete", "note.delete")
        self.menu_btn = Gtk.MenuButton(label="⋯", menu_model=menu, has_frame=False)
        group = Gio.SimpleActionGroup()
        for name, fn in (("rename", self.begin_rename), ("move", self.choose_folder_and_move), ("delete", self.request_delete),
                         ("larger", lambda: self.set_font_size(self.meta.font_size + 1)), ("smaller", lambda: self.set_font_size(self.meta.font_size - 1))):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, f=fn: f())
            group.add_action(act)
        self.menu_btn.insert_action_group("note", group)   # the button lives in the node header, outside this widget's subtree

        self.header_left: list[Gtk.Widget] = [mode]
        self.header_title: Gtk.Widget = self.title_label
        self.header_right: list[Gtk.Widget] = [self.menu_btn]

    def frame_attached(self, frame: Gtk.Widget) -> None:
        """Called by NodeFrame once the chrome exists: lets the note style the card and tag header widgets for the canvas gesture."""
        self._frame = frame
        self._apply_style()

    # ---- body --------------------------------------------------------------------------------------------
    def _build_body(self) -> None:
        self.raw = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=True)
        self.raw.add_css_class("note-raw")
        self.raw.set_sensitive(False)   # until the saved text has loaded: never type over something not yet read
        self.buf = self.raw.get_buffer()
        self.buf.connect("changed", self._on_changed)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c: self.flush())
        self.raw.add_controller(focus)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._raw_key)
        self.raw.add_controller(keys)

        self.fmt = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.fmt.add_css_class("note-formatted")
        self._make_tags(self.fmt.get_buffer())
        click = Gtk.GestureClick(button=1)
        click.connect("released", self._on_fmt_click)
        self.fmt.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_fmt_motion)
        self.fmt.add_controller(motion)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        raw_sw = Gtk.ScrolledWindow(child=self.raw, hscrollbar_policy=Gtk.PolicyType.NEVER)
        fmt_sw = Gtk.ScrolledWindow(child=self.fmt, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.stack.add_named(raw_sw, "raw")
        self.stack.add_named(fmt_sw, "formatted")
        self.append(self.stack)

    @staticmethod
    def _make_tags(buf: Gtk.TextBuffer) -> None:
        def rgba(s: str) -> Gdk.RGBA:
            c = Gdk.RGBA(); c.parse(s); return c
        mono_bg = rgba("rgba(0,0,0,0.09)")
        buf.create_tag("h1", scale=1.55, weight=700, pixels_above_lines=6, pixels_below_lines=3)
        buf.create_tag("h2", scale=1.32, weight=700, pixels_above_lines=5, pixels_below_lines=2)
        buf.create_tag("h3", scale=1.15, weight=700, pixels_above_lines=4, pixels_below_lines=1)
        buf.create_tag("bold", weight=700)
        buf.create_tag("italic", style=2)   # Pango.Style.ITALIC
        buf.create_tag("strike", strikethrough=True)
        buf.create_tag("code", family="monospace", background_rgba=mono_bg)
        buf.create_tag("code-block", family="monospace", paragraph_background_rgba=mono_bg, left_margin=8, right_margin=8)
        buf.create_tag("quote", left_margin=16, style=2, foreground_rgba=rgba("rgba(90,90,90,1)"))
        buf.create_tag("hr", foreground_rgba=rgba("rgba(120,120,120,1)"), pixels_above_lines=4, pixels_below_lines=4)
        buf.create_tag("table", family="monospace", wrap_mode=Gtk.WrapMode.NONE)
        buf.create_tag("link", underline=1, foreground_rgba=rgba("#1c71d8"))   # Pango.Underline.SINGLE
        buf.create_tag("li", left_margin=14, indent=-14)
        for n in range(5):
            buf.create_tag(f"indent{n}", left_margin=14 * (n + 1), indent=-14)
        buf.create_tag("image-missing", style=2, foreground_rgba=rgba("rgba(120,120,120,1)"))

    # ---- footer ------------------------------------------------------------------------------------------
    def _build_footer(self) -> None:
        bar = Gtk.Box(spacing=5)
        bar.add_css_class("note-bar")
        self._swatches: list[tuple[str, Gtk.Button]] = []
        for value, label in PRESETS:
            b = Gtk.Button(tooltip_text=label)
            b.add_css_class("note-swatch")
            b.add_css_class("note-sw-" + resolve_hex(value)[1:].lower())
            b.connect("clicked", lambda _b, v=value: self.set_color(v))
            bar.append(b)
            self._swatches.append((value, b))
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(), tooltip_text="Custom colour")
        self.color_btn.connect("notify::rgba", self._on_custom_color)
        bar.append(self.color_btn)
        self.status = Gtk.Label(xalign=1, hexpand=True, ellipsize=3, width_chars=0)   # ellipsized: it must never widen the card
        self.status.add_css_class("note-status")
        bar.append(self.status)
        self.footer = bar
        self.append(bar)

    # ---- text / loading ----------------------------------------------------------------------------------
    def text(self) -> str:
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)

    def _set_text(self, text: str) -> None:
        self._applying = True
        self.buf.set_text(text)
        self._applying = False

    def _on_loaded(self, text: str | None, err: Exception | None) -> None:
        if self._disposed:
            return
        if err is not None or text is None:
            self.status.set_text("Could not load note")
            self.status.add_css_class("error")
            return
        self._loaded = True
        self._saved_text = text
        self._set_text(text)
        self.raw.set_sensitive(True)
        self.status.remove_css_class("error")
        self.status.set_text("")
        self._after_text_change()

    def _on_changed(self, _buf) -> None:
        if self._applying or not self._loaded:
            return
        self.saver.schedule(self.text())
        self._after_text_change()

    def _after_text_change(self) -> None:
        self._update_title()
        if self.meta.is_previewing:
            self._render_formatted()

    def flush(self, done: Callable[[], None] | None = None) -> None:
        self.saver.flush(done)

    def _on_save_state(self, state: str, err: Exception | None) -> None:
        label = {"dirty": "Unsaved", "saving": "Saving…", "saved": "Saved", "error": "Save failed", "idle": ""}[state]
        self.status.set_text(label)
        (self.status.add_css_class if state == "error" else self.status.remove_css_class)("error")
        if state == "error":
            self.status.set_tooltip_text(str(err))
        if state == "saved":
            self._saved_text = self.text()

    # ---- mode / render -----------------------------------------------------------------------------------
    def set_previewing(self, on: bool) -> None:
        self._set_mode(on)

    def _set_mode(self, previewing: bool, emit: bool = True) -> None:
        self.meta.is_previewing = previewing
        self._mode_sync = True
        (self.fmt_btn if previewing else self.raw_btn).set_active(True)
        self._mode_sync = False
        self.stack.set_visible_child_name("formatted" if previewing else "raw")
        if previewing:
            self._render_formatted()
        if emit:
            self._emit_meta()

    def _render_formatted(self) -> None:
        buf = self.fmt.get_buffer()
        buf.set_text("")
        for anchor_child in list(getattr(self, "_pictures", [])):
            if anchor_child.get_parent() is not None:
                anchor_child.unparent()
        self._pictures: list[Gtk.Widget] = []
        self._links.clear()
        n = 0
        for run in parse_markdown(self.text()):
            it = buf.get_end_iter()
            if run.image is not None:
                self._insert_image(buf, run)
                continue
            tags = list(run.tags)
            if run.link:
                n += 1
                name = f"link-{n}"
                if buf.get_tag_table().lookup(name) is None:   # the table outlives set_text(""): reuse, a duplicate name is refused by GTK
                    buf.create_tag(name)
                self._links[name] = run.link
                tags.append(name)
            table = buf.get_tag_table()
            tags = [t for t in tags if table.lookup(t) is not None]   # semantic-only tags ("li", "blank") have no style: skip, never fail
            if tags:
                buf.insert_with_tags_by_name(it, run.text, *tags)
            else:
                buf.insert(it, run.text)

    def _image_dir(self) -> str | None:
        if self.base_dir:
            return self.base_dir
        return os.path.dirname(self.meta.custom_path) if self.meta.custom_path else None

    def _insert_image(self, buf: Gtk.TextBuffer, run: Run) -> None:
        src, alt = run.image or "", run.text
        path = None
        if not src.lower().startswith(("http://", "https://", "data:")):   # never fetch from the network while rendering a note
            path = src if os.path.isabs(src) else (os.path.join(self._image_dir(), src) if self._image_dir() else None)
        try:
            if path is None or not os.path.isfile(path) or os.path.getsize(path) > MAX_IMAGE_BYTES:
                raise OSError("unavailable")
            tex = Gdk.Texture.new_from_filename(path)
        except (OSError, GLib.Error):
            buf.insert_with_tags_by_name(buf.get_end_iter(), f"[{alt}]", "image-missing")
            return
        w = min(tex.get_width(), MAX_IMAGE_W)
        pic = Gtk.Picture.new_for_paintable(tex)
        pic.set_can_shrink(True)
        pic.set_size_request(w, max(1, round(tex.get_height() * w / max(tex.get_width(), 1))))
        pic.set_tooltip_text(alt)
        anchor = buf.create_child_anchor(buf.get_end_iter())
        self.fmt.add_child_at_anchor(pic, anchor)
        self._pictures.append(pic)

    # ---- links -------------------------------------------------------------------------------------------
    def _link_at(self, x: float, y: float) -> str | None:
        bx, by = self.fmt.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(x), int(y))
        ok, it = self.fmt.get_iter_at_location(bx, by)
        if not ok:
            return None
        for tag in it.get_tags():
            name = tag.get_property("name")
            if name in self._links:
                return self._links[name]
        return None

    def _on_fmt_click(self, g: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        if self.fmt.get_buffer().get_has_selection():
            return   # the user was selecting text, not clicking a link
        url = self._link_at(x, y)
        if url:
            try:
                self._open_uri(url)   # only http/https/mailto ever reach _links (markdown_spans filters the rest)
            except GLib.Error as e:
                self.status.set_text(f"Could not open link: {e.message}")

    def _on_fmt_motion(self, _c: Gtk.EventControllerMotion, x: float, y: float) -> None:
        self.fmt.set_cursor_from_name("pointer" if self._link_at(x, y) else None)

    # ---- colour / size -----------------------------------------------------------------------------------
    def set_color(self, color: str) -> None:
        """`color` is stored verbatim (a hex or a Maestri name); it is only resolved for rendering."""
        if color == self.meta.color:
            return
        self.meta.color = color
        _css.ensure(color)
        self._apply_style()
        self._update_swatches()
        self._emit_meta()

    def _on_custom_color(self, btn: Gtk.ColorDialogButton, _p) -> None:
        c = btn.get_rgba()
        self.set_color("#{:02X}{:02X}{:02X}".format(round(c.red * 255), round(c.green * 255), round(c.blue * 255)))

    def _update_swatches(self) -> None:
        cur = resolve_hex(self.meta.color)
        for value, b in self._swatches:
            (b.add_css_class if resolve_hex(value) == cur else b.remove_css_class)("active")
        r, g, b_ = to_rgb(self.meta.color)
        rgba = Gdk.RGBA(); rgba.red, rgba.green, rgba.blue, rgba.alpha = r, g, b_, 1.0
        self.color_btn.handler_block_by_func(self._on_custom_color)
        self.color_btn.set_rgba(rgba)
        self.color_btn.handler_unblock_by_func(self._on_custom_color)

    def set_font_size(self, n: int) -> None:
        n = clamp_font_size(n)
        if n == self.meta.font_size:
            return
        self.meta.font_size = n
        self._apply_style()
        self._emit_meta()

    def _apply_style(self) -> None:
        f = self._frame
        if f is None:
            return
        for c in self._applied:
            f.remove_css_class(c)
        self._applied = ["maestri-note", css_class(self.meta.color), f"note-fs-{clamp_font_size(self.meta.font_size)}"]
        for c in self._applied:
            f.add_css_class(c)

    # ---- title / rename ----------------------------------------------------------------------------------
    def _title(self) -> str:
        return self.meta.name if self.meta.has_custom_name else title_from_text(self.text(), self.meta.name or "Note")

    def _update_title(self) -> None:
        if self.title_label.get_editing():
            return
        t = self._title()
        if t != self._shown_title:
            self._shown_title = t
            self.title_label.set_text(t)
            self.emit("title-changed", t)
        self.title_label.set_tooltip_text(self.meta.file_name or "")

    def begin_rename(self) -> None:
        self.title_label.set_text(self.meta.name if self.meta.has_custom_name else self._title())
        self.title_label.start_editing()

    on_header_double_click = begin_rename   # NodeFrame/CanvasWidget call this on a double-click of the header

    def _on_title_editing(self, label: Gtk.EditableLabel, _p) -> None:
        editing = label.get_editing()
        if self._frame is not None:   # while editing the label must receive pointer events (caret, selection), not start a node drag
            self._frame.tag(label, "content" if editing else "header")
        if not editing:
            new = label.get_text().strip()
            if new and new not in (self._shown_title, self.meta.name):
                self.rename(new)
            else:
                label.set_text(self._shown_title)

    def rename(self, new_name: str, done: Callable[[Exception | None], None] | None = None) -> None:
        def fail(msg: str) -> None:
            self.status.set_text(msg)
            self.status.add_css_class("error")
            self.title_label.set_text(self._shown_title)
            if done: done(ValueError(msg))
        if self._busy:
            return fail("Busy")
        if not valid_note_name(new_name):
            return fail("Invalid name")
        if self.name_taken and new_name.lower() != self.meta.name.lower() and self.name_taken(new_name):
            return fail("Name already used")
        self._busy = True

        def finish(err: Exception | None, new_storage: NoteStorage | None = None, file_name: str | None = None, path: str | None = None) -> None:
            self._busy = False
            if err is not None:
                return fail(f"Rename failed: {err}")
            if new_storage is not None:
                self.storage = new_storage
            self.meta.file_name = file_name
            self.meta.has_custom_name = True
            if path:
                self.meta.set_custom_path(path)
                self._start_watch()
            self._shown_title = ""
            self._update_title()
            self._emit_meta()
            if done: done(None)

        def do_rename() -> None:
            if self.meta.is_custom:
                old = self.meta.custom_path
                ext = os.path.splitext(old)[1] or ".md"
                target = os.path.join(os.path.dirname(old), new_name + ext)
                if os.path.exists(target):
                    return finish(FileExistsError("target exists"))
                def renamed(f, res):
                    try:
                        f.set_display_name_finish(res)
                    except GLib.Error as e:
                        return finish(e)
                    finish(None, LocalNoteStorage(target), new_name + ext, target)
                Gio.File.new_for_path(old).set_display_name_async(new_name + ext, GLib.PRIORITY_DEFAULT, None, renamed)
            elif self.client is not None:
                self.client.rename(self.meta.name, new_name, lambda err: finish(err, CoreNoteStorage(self.client, new_name), new_name + ".md"))
            else:   # no core (offline demo): the storage is not name-addressed
                finish(None, None, new_name + ".md")

        self.flush(do_rename)   # pending text must land in the OLD note before it is copied/moved

    # ---- move / delete / adopt ---------------------------------------------------------------------------
    def choose_folder_and_move(self) -> None:
        dialog = Gtk.FileDialog(title="Move note to…")
        root = self.get_root()
        dialog.select_folder(root if isinstance(root, Gtk.Window) else None, None,
                             lambda d, res: self._folder_chosen(d, res))

    def _folder_chosen(self, dialog: Gtk.FileDialog, res) -> None:
        try:
            folder = dialog.select_folder_finish(res)
        except GLib.Error:
            return   # cancelled
        if folder is not None and folder.get_path():
            self.move_to(folder.get_path())

    def move_to(self, folder: str, done: Callable[[Exception | None], None] | None = None) -> None:
        def fail(err: Exception) -> None:
            self._busy = False
            self.status.set_text(f"Move failed: {err}")
            self.status.add_css_class("error")
            if done: done(err)
        if self._busy:
            return
        file_name = self.meta.file_name or (self.meta.name + ".md")
        target = os.path.join(folder, file_name)
        if os.path.exists(target):
            return fail(FileExistsError("a file with that name exists there"))
        if not os.path.isdir(folder):
            return fail(NotADirectoryError(folder))
        self._busy = True
        text = self.text()
        old_storage, new_storage = self.storage, LocalNoteStorage(target)

        def written(err: Exception | None) -> None:
            if err is not None:
                return fail(err)
            old_storage.delete(lambda _e: None)   # best effort: the new copy is already safe
            self.storage = new_storage
            self.meta.set_custom_path(target)
            self._saved_text = text
            self._busy = False
            self._start_watch()
            self._emit_meta()
            if done: done(None)

        self.flush(lambda: new_storage.write(text, written))

    def adopt_file(self, path: str, done: Callable[[Exception | None], None] | None = None) -> None:
        """Use an existing .md/.markdown/.txt as this note's storage (drop). Refused while there are unsaved edits."""
        if not is_note_file(path):
            return done and done(ValueError("not a note file"))
        if self.saver.dirty or self.text() != self._saved_text:
            self.status.set_text("Unsaved edits: not replaced")
            return done and done(RuntimeError("dirty"))
        new_storage = LocalNoteStorage(path)

        def got(text: str | None, err: Exception | None) -> None:
            if err is not None or text is None:
                return done and done(err or RuntimeError("unreadable"))
            self.storage = new_storage
            self.meta.set_custom_path(path)
            self.meta.file_name = os.path.basename(path)
            self._saved_text = text
            self._set_text(text)
            self._loaded = True
            self._start_watch()
            self._after_text_change()
            self._emit_meta()
            if done: done(None)

        new_storage.read(got)

    def _on_drop(self, _t: Gtk.DropTarget, value, _x: float, _y: float) -> bool:
        path = value.get_path() if isinstance(value, Gio.File) else None
        if path and is_note_file(path):
            self.emit("file-dropped", path)
            return True
        return False

    def request_delete(self) -> None:
        dialog = Gtk.AlertDialog(message="Delete this note?", detail=f"“{self._title()}” and its file will be deleted. This cannot be undone.")
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        root = self.get_root()

        def chosen(d, res) -> None:
            try:
                if d.choose_finish(res) == 1:
                    self.delete_file()
            except GLib.Error:
                pass
        dialog.choose(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def delete_file(self, done: Callable[[Exception | None], None] | None = None) -> None:
        """Delete the note's file (no confirmation: use request_delete for the dialog) and ask for the node to be removed.
        Pending edits are dropped first (a late debounced write would recreate the file) and a write already in flight is awaited."""
        self._disposed = True   # nothing new may be written after this
        self._stop_watch()
        self.saver.cancel()

        def deleted(err: Exception | None) -> None:
            if err is not None:
                self._disposed = False
                self._start_watch()
                if self.text() != self._saved_text:
                    self.saver.schedule(self.text())   # the delete failed: the edit we cancelled must not be lost
                self.status.set_text(f"Delete failed: {err}")
                self.status.add_css_class("error")
            else:
                self.emit("delete-requested")
            if done: done(err)

        self.saver.flush(lambda: self.storage.delete(deleted))

    # ---- external changes --------------------------------------------------------------------------------
    def _start_watch(self) -> None:
        self._stop_watch()
        path = self.meta.custom_path or self._watch_path
        if path:
            self._monitor = watch_file(path, self._external_changed)

    def _stop_watch(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None

    def _external_changed(self) -> None:
        if not self._disposed:
            self.storage.read(self._on_external_read)

    def _on_external_read(self, text: str | None, err: Exception | None) -> None:
        if self._disposed or err is not None or text is None or text == self.text():
            if text is not None and text == self.text():
                self._saved_text = text   # our own write echoing back
            return
        if self.saver.dirty or self.text() != self._saved_text:
            self.status.set_text("Changed on disk: your edits are kept")
            return
        old = self.text()
        p = 0
        while p < min(len(old), len(text)) and old[p] == text[p]:
            p += 1
        s = 0
        while s < min(len(old), len(text)) - p and old[len(old) - 1 - s] == text[len(text) - 1 - s]:
            s += 1
        # replace only the differing middle: GTK keeps the cursor and scroll position where they are outside that range
        self._applying = True
        self.buf.delete(self.buf.get_iter_at_offset(p), self.buf.get_iter_at_offset(len(old) - s))
        self.buf.insert(self.buf.get_iter_at_offset(p), text[p:len(text) - s])
        self._applying = False
        self._saved_text = text
        self._after_text_change()
        self.status.set_text("Updated from disk")

    # ---- images ------------------------------------------------------------------------------------------
    def _raw_key(self, _c: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK and self._image_dir():
            fmts = self.get_clipboard().get_formats()
            if fmts.contain_gtype(Gdk.Texture) and not fmts.contain_gtype(str):   # an image and no text: otherwise plain paste
                self.get_clipboard().read_texture_async(None, self._on_clip_texture)
                return True
        return False

    def _on_clip_texture(self, clip: Gdk.Clipboard, res) -> None:
        try:
            tex = clip.read_texture_finish(res)
        except GLib.Error:
            return
        if tex is not None:
            self.paste_texture(tex)

    def paste_texture(self, tex: Gdk.Texture) -> str | None:
        """Save the image NEXT TO the note's .md and insert `![](name.png)` at the cursor. Returns the file name, or None."""
        d = self._image_dir()
        if not d:
            self.status.set_text("Image paste needs a folder for this note")
            return None
        fname = f"{self.meta.name or 'note'}-{int(time.time() * 1000)}.png"
        if not tex.save_to_png(os.path.join(d, fname)):
            self.status.set_text("Could not save the image")
            return None
        self.buf.insert_at_cursor(f"![]({_md_dest(fname)})")
        return fname

    # ---- persisted metadata ------------------------------------------------------------------------------
    def to_content(self) -> dict:
        """The StickyNoteContent to persist (colour string verbatim, unknown keys preserved)."""
        return self.meta.to_dict()

    def bind(self, content: dict) -> None:
        """Apply persisted metadata (e.g. an external update): colour, size, mode, name. Storage changes go through move_to/adopt_file."""
        new = NoteMeta.from_dict(content)
        self.meta.color, self.meta.font_size, self.meta.extra = new.color, new.font_size, new.extra
        self.meta.has_custom_name = new.has_custom_name
        if new.file_name != self.meta.file_name:
            self.meta.file_name = new.file_name
        _css.ensure(new.color)
        self._apply_style()
        self._update_swatches()
        self._set_mode(new.is_previewing, emit=False)
        self._last_meta = self.meta.to_dict()
        self._shown_title = ""
        self._update_title()

    def _emit_meta(self) -> None:
        d = self.meta.to_dict()
        if d != self._last_meta:   # only a real change reaches the app (and from there the core)
            self._last_meta = d
            self.emit("meta-changed", d)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._stop_watch()
        self.flush()   # last write; the storage callback may complete after the widget is gone
        self._disposed = True
