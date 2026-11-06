"""Floating context toolbars for the single selected node (Sprint 5), shown over the canvas just above the node.

  NoteContextToolbar     sticky note: Markdown formatting on the raw editor (bold, italic, strike, code, H1, H2, list, task).
  DrawingContextToolbar  text / shape / freehand: colour palette, stroke width or font size, stroke style and fill style
                         (shapes), delete. Values and ranges follow ShapeContextToolbar / FreehandContextToolbar /
                         TextContextToolbar on macOS.

The CanvasWidget hosts them (`_sync_context_toolbar`): it decides which node gets one, positions it every frame and routes
drawing changes as `drawn-content-changed`, so persistence stays in the app. Buttons never take keyboard focus: clicking Bold
must not move the caret out of the note being edited.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .drawn import resolve_rgb  # noqa: E402
from .geometry import Point  # noqa: E402
from .markdown_format import format_edit  # noqa: E402

# NoteColorPickerPopover rows (shapes, drawings and text labels resolve their colour names with this palette)
PALETTE = ("yellow", "pink", "blue", "green", "orange", "purple", "white", "black", "darkgray", "darkblue")
STROKE_STYLES = (("solid", "━  Sólido"), ("dashed", "╍  Tracejado"), ("dotted", "┈  Pontilhado"))
FILL_STYLES = (("solid", "■  Sólido"), ("none", "□  Sem preenchimento"), ("hatched", "▨  Hachurado"),
               ("crossHatched", "▦  Hachura cruzada"))
STROKE_RANGE = (1, 20)                                    # ShapeContextToolbar / FreehandContextToolbar
FONT_RANGE = {"text": (8, 96), "shape": (8, 72)}          # TextContextToolbar / shape caption
NOTE_ACTIONS = (("bold", "<b>B</b>", "Negrito (**)"), ("italic", "<i>I</i>", "Itálico (*)"),
                ("strike", "<s>S</s>", "Tachado (~~)"), ("code", "<tt>&lt;/&gt;</tt>", "Código inline (`)"),
                ("h1", "H1", "Título 1 (#)"), ("h2", "H2", "Título 2 (##)"),
                ("bullet", "•≡", "Item de lista (-)"), ("task", "☐", "Tarefa (- [ ])"))

_CSS = b"""
.maestri-context-toolbar { background: #ffffff; color: #1f1f1f; border: 1px solid #e3e3e3; border-radius: 9px;
  padding: 3px 6px; box-shadow: 0 2px 8px alpha(black, .12); }
.maestri-context-toolbar button { background: transparent; color: #1f1f1f; border: none; box-shadow: none; min-height: 26px;
  min-width: 26px; padding: 0 6px; border-radius: 6px; }
.maestri-context-toolbar button:hover { background: alpha(black, .06); }
.maestri-context-toolbar button.destructive:hover { background: alpha(#ef4444, .10); color: #dc2626; }
.maestri-context-toolbar .ctx-sep { background: #e3e3e3; min-width: 1px; margin: 4px 4px; }
.maestri-context-toolbar .ctx-value { min-width: 34px; font-size: 0.9em; }
.maestri-ctx-popover button { min-height: 24px; }
"""
_css_displays: set = set()


def _install_css() -> None:
    display = Gdk.Display.get_default()
    if display is None or id(display) in _css_displays:
        return
    prov = Gtk.CssProvider()
    prov.load_from_data(_CSS)
    # USER priority: a user theme installed as ~/.config/gtk-4.0/gtk.css loads at USER and would otherwise win (see DESIGN.md)
    Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    _css_displays.add(id(display))


def _button(label: str, tooltip: str, on_click: Callable[[], None], markup: bool = False) -> Gtk.Button:
    b = Gtk.Button(focus_on_click=False, can_focus=False, tooltip_text=tooltip)
    lbl = Gtk.Label()
    (lbl.set_markup if markup else lbl.set_text)(label)
    b.set_child(lbl)
    b.connect("clicked", lambda _b: on_click())
    return b


def _separator() -> Gtk.Widget:
    sep = Gtk.Box()
    sep.add_css_class("ctx-sep")
    return sep


def _swatch(color: str, size: int = 16) -> Gtk.DrawingArea:
    area = Gtk.DrawingArea(content_width=size, content_height=size)
    area.color = color

    def draw(_a, cr, w, h):
        r = min(w, h) / 2 - 1
        cr.arc(w / 2, h / 2, r, 0, 6.2832)
        cr.set_source_rgb(*resolve_rgb(area.color))
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        cr.stroke()

    area.set_draw_func(draw)
    return area


class ContextToolbar(Gtk.Box):
    """Base: a horizontal pill. `node_id` / `kind` identify the node it edits."""

    def __init__(self, node_id: str, kind: str) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, halign=Gtk.Align.START, valign=Gtk.Align.START)
        _install_css()
        self.node_id, self.kind = node_id, kind
        self.add_css_class("maestri-context-toolbar")

    def update(self, content: dict) -> None:   # drawn nodes: refresh after the app applied a change
        pass


class NoteContextToolbar(ContextToolbar):
    def __init__(self, node_id: str, note) -> None:
        super().__init__(node_id, "stickyNote")
        self.note = note
        self.buttons: dict[str, Gtk.Button] = {}
        for i, (action, label, tip) in enumerate(NOTE_ACTIONS):
            if i in (4, 6):
                self.append(_separator())
            b = _button(label, tip, lambda a=action: self.apply(a), markup=True)
            self.buttons[action] = b
            self.append(b)

    def apply(self, action: str) -> bool:
        """Format the selection (or the caret's line) of the note's raw Markdown. Switches a formatted note to Raw first, so
        the user sees what changed. Goes through the buffer, so undo and the debounced save work as for typing."""
        note = self.note
        if not getattr(note, "_loaded", True) or getattr(note, "_disposed", False):
            return False
        if note.meta.is_previewing:
            note.set_previewing(False)
        buf = note.buf
        bounds = buf.get_selection_bounds()   # PyGObject: () without a selection, else (start, end)
        if bounds:
            s_it, e_it = bounds
        else:
            s_it = e_it = buf.get_iter_at_mark(buf.get_insert())
        edit = format_edit(action, note.text(), s_it.get_offset(), e_it.get_offset())
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(edit.start), buf.get_iter_at_offset(edit.end))
        buf.insert(buf.get_iter_at_offset(edit.start), edit.insert)
        buf.end_user_action()
        buf.select_range(buf.get_iter_at_offset(edit.sel_start), buf.get_iter_at_offset(edit.sel_end))
        note.raw.grab_focus()
        return True


class DrawingContextToolbar(ContextToolbar):
    """`on_change(patch)` receives only the changed keys; `on_delete()` removes the node."""

    def __init__(self, node_id: str, kind: str, content: dict, on_change: Callable[[dict], None],
                 on_delete: Callable[[], None] | None = None) -> None:
        super().__init__(node_id, kind)
        self.content = dict(content)
        self.on_change = on_change
        self.color_key = "color" if kind == "text" else "strokeColor"

        # colour (shapes: theme colour = stroke AND fill, like macOS)
        self.color_swatch = _swatch(self.content.get(self.color_key, "blue"), 18)
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.palette_buttons: dict[str, Gtk.Button] = {}
        for i, name in enumerate(PALETTE):
            b = Gtk.Button(child=_swatch(name, 20), focus_on_click=False, tooltip_text=name, has_frame=False)
            b.connect("clicked", lambda _b, n=name: self._pick_color(n))
            grid.attach(b, i % 7, i // 7, 1, 1)
            self.palette_buttons[name] = b
        self.color_popover = Gtk.Popover(child=grid)
        self.color_popover.add_css_class("maestri-ctx-popover")
        self.color_btn = Gtk.MenuButton(child=self.color_swatch, popover=self.color_popover, focus_on_click=False,
                                        can_focus=False, tooltip_text="Cor do texto" if kind == "text" else "Cor")
        self.append(self.color_btn)
        self.append(_separator())

        # stroke width (shape/freehand) or font size (text)
        self.value_key = "fontSize" if kind == "text" else "strokeWidth"
        self.value_range = FONT_RANGE["text"] if kind == "text" else STROKE_RANGE
        self.value_label = Gtk.Label(css_classes=["ctx-value"])
        word = "fonte" if kind == "text" else "espessura do traço"
        self.minus_btn = _button("−", f"Diminuir {word}", lambda: self._step(-1))
        self.plus_btn = _button("+", f"Aumentar {word}", lambda: self._step(+1))
        for w in (self.minus_btn, self.value_label, self.plus_btn):
            self.append(w)

        self.stroke_style_btn = self.fill_style_btn = None
        if kind == "shape":
            self.append(_separator())
            self.stroke_style_btn, self.stroke_items = self._choice_menu("Estilo do traço", "strokeStyle", STROKE_STYLES)
            self.fill_style_btn, self.fill_items = self._choice_menu("Estilo de preenchimento", "fillStyle", FILL_STYLES)
            self.append(self.stroke_style_btn)
            self.append(self.fill_style_btn)
        if on_delete is not None:
            self.append(_separator())
            delete = _button("🗑", "Apagar", on_delete)
            delete.add_css_class("destructive")
            self.append(delete)
        self.update(self.content)

    def _choice_menu(self, tip: str, key: str, options) -> tuple[Gtk.MenuButton, dict[str, Gtk.Button]]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        items = {}
        pop = Gtk.Popover(child=box)
        pop.add_css_class("maestri-ctx-popover")
        for value, label in options:
            b = Gtk.Button(label=label, has_frame=False, focus_on_click=False)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, v=value: (pop.popdown(), self._set(key, v)))
            box.append(b)
            items[value] = b
        btn = Gtk.MenuButton(popover=pop, focus_on_click=False, can_focus=False, tooltip_text=tip)
        return btn, items

    # ---- state -----------------------------------------------------------------------------------------
    def update(self, content: dict) -> None:
        self.content = dict(content)
        self.color_swatch.color = self.content.get(self.color_key) or "blue"
        self.color_swatch.queue_draw()
        v = self._value()
        self.value_label.set_text(f"{v:g}pt")
        lo, hi = self.value_range
        self.minus_btn.set_sensitive(v > lo)
        self.plus_btn.set_sensitive(v < hi)
        if self.stroke_style_btn is not None:
            self._mark(self.stroke_style_btn, self.stroke_items, STROKE_STYLES, self.content.get("strokeStyle", "solid"))
            self._mark(self.fill_style_btn, self.fill_items, FILL_STYLES, self.content.get("fillStyle", "solid"))

    @staticmethod
    def _mark(btn: Gtk.MenuButton, items: dict[str, Gtk.Button], options, current: str) -> None:
        labels = dict(options)
        btn.set_label(labels.get(current, labels[options[0][0]]).split("  ")[0])
        for value, b in items.items():
            b.set_label(("✓ " if value == current else "    ") + labels[value])
            b.get_child().set_xalign(0)

    def _value(self) -> float:
        try:
            return float(self.content.get(self.value_key, 18.0 if self.kind == "text" else 2.0))
        except (TypeError, ValueError):
            return 2.0

    def _set(self, key: str, value) -> None:
        if self.content.get(key) != value:
            self.on_change({key: value})

    def _pick_color(self, name: str) -> None:
        self.color_popover.popdown()
        patch = {"strokeColor": name, "fillColor": name} if self.kind == "shape" else {self.color_key: name}
        if any(self.content.get(k) != v for k, v in patch.items()):
            self.on_change(patch)

    def _step(self, direction: int) -> None:
        lo, hi = self.value_range
        new = max(lo, min(hi, round(self._value()) + direction))
        self._set(self.value_key, float(new))


DRAWN_TOOLBAR_KINDS = ("text", "shape", "freehand")


class ContextToolbarHost:
    """Owned by CanvasWidget: one toolbar at a time, for exactly one selected, unlocked node of a supported kind. Placed
    centred just above the node in screen space (below it when there is no room above), clamped to the canvas; hidden while a
    gesture runs, while the drawn-text editor is open, and while the node is off screen. Re-created only when the target changes."""
    GAP = 10
    MARGIN = 4

    def __init__(self, canvas, role_attr: str) -> None:
        self.canvas = canvas
        self.role_attr = role_attr   # the canvas' pointer-role tag: the toolbar is "content" of its node, so clicks reach it
        self.toolbar: ContextToolbar | None = None
        self.key: tuple[str, str] | None = None
        self._pos: tuple[int, int] | None = None

    def _wanted(self) -> tuple[str, str] | None:
        c = self.canvas
        if len(c.scene.selection) != 1:
            return None
        nid = next(iter(c.scene.selection))
        node = c.scene.get(nid)
        if node is None or node.locked:
            return None
        if node.kind in DRAWN_TOOLBAR_KINDS and isinstance(node.content, dict):
            return nid, node.kind
        if node.kind == "stickyNote":
            frame = c.frames.get(nid)
            if frame is not None and hasattr(frame.content, "buf") and hasattr(frame.content, "meta"):
                return nid, "stickyNote"
        return None

    def sync(self, width: int, height: int) -> None:
        key = self._wanted()
        if key != self.key:
            self._replace(key)
        if self.toolbar is not None:
            self._place(width, height)

    def _replace(self, key: tuple[str, str] | None) -> None:
        c = self.canvas
        if self.toolbar is not None:
            c.remove_overlay(self.toolbar)
            self.toolbar = None
        self.key, self._pos = key, None
        if key is None:
            return
        nid, kind = key
        if kind == "stickyNote":
            tb: ContextToolbar = NoteContextToolbar(nid, c.frames[nid].content)
        else:
            tb = DrawingContextToolbar(nid, kind, c.scene.get(nid).content, on_change=lambda patch, n=nid: self._change(n, patch),
                                       on_delete=lambda n=nid: c.emit("delete-requested", (n,)))
        setattr(tb, self.role_attr, ("content", nid))
        tb.set_visible(False)   # shown by _place once it has a position (no flash at 0,0)
        c.add_overlay(tb)
        self.toolbar = tb

    def _change(self, node_id: str, patch: dict) -> None:
        node = self.canvas.scene.get(node_id)
        if node is not None:   # the app merges, persists and calls set_node_content -> content_changed
            self.canvas.emit("drawn-content-changed", node.id, {**(node.content or {}), **patch})

    def content_changed(self, node_id: str, content: dict) -> None:
        if self.toolbar is not None and self.key is not None and self.key[0] == node_id:
            self.toolbar.update(content)

    def _place(self, width: int, height: int) -> None:
        c, tb = self.canvas, self.toolbar
        editor = getattr(c, "_drawn_editor", None)
        node = c.scene.get(self.key[0])
        f, vp = node.frame, c.viewport
        tl, br = vp.to_screen(Point(f.x, f.y)), vp.to_screen(Point(f.max_x, f.max_y))
        hidden = (c.controller.busy or (editor is not None and editor.active)
                  or br.x < 0 or tl.x > width or br.y < 0 or tl.y > height)
        if hidden:
            tb.set_visible(False)
            return
        tw = tb.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        th = tb.measure(Gtk.Orientation.VERTICAL, tw)[1]
        x = min(max(self.MARGIN, (tl.x + br.x) / 2 - tw / 2), max(self.MARGIN, width - tw - self.MARGIN))
        y = tl.y - th - self.GAP
        if y < self.MARGIN:   # no room above: below the node, else pinned inside the top edge
            y = br.y + self.GAP if br.y + self.GAP + th <= height - self.MARGIN else self.MARGIN
        pos = (round(x), round(y))
        if pos != self._pos:
            self._pos = pos
            tb.set_margin_start(pos[0])
            tb.set_margin_top(pos[1])
        if not tb.get_visible():
            tb.set_visible(True)
