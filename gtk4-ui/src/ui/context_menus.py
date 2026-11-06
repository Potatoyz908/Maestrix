"""
Modern macOS-style Context Menus for Open Maestri GTK 4 Native.
Matches uploaded reference images:
- Canvas background context menu: uploaded_media_1_1793938730315.png
  (+ Add > [Terminal >, Note, File Tree, Portal, Text], Paste, Partituras >)
- Terminal / Node context menu: uploaded_media_2_1793938730315.png
  (Copy, Paste, Select All, Rename, Edit..., Monitor Activity, Blur, Reload, Unload,
   New Element Defaults >, Copy Element, Duplicate, Lock, Delete, Partituras >)

Uses clean monochrome symbolic outline icons, dark glass styling, and smooth submenus.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import norm
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent


def make_menu_row(
    icon_name: str,
    label_text: str,
    callback: Optional[Callable[[], None]] = None,
    is_danger: bool = False,
    has_chevron: bool = False,
    is_checked: bool = False,
) -> Gtk.Button:
    """Builds a single dark-glass context menu item with monochrome outline icon."""
    btn = Gtk.Button()
    btn.add_css_class("maestri-menu-item-btn")
    if is_danger:
        btn.add_css_class("danger")
    btn.set_has_frame(False)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    box.set_margin_start(4)
    box.set_margin_end(4)

    # Checkmark or Icon
    if is_checked:
        check_lbl = Gtk.Label(label="✓")
        check_lbl.add_css_class("maestri-menu-check")
        box.append(check_lbl)

    img = Gtk.Image.new_from_icon_name(icon_name)
    img.set_icon_size(Gtk.IconSize.NORMAL)
    img.add_css_class("maestri-menu-icon")
    box.append(img)

    lbl = Gtk.Label(label=label_text, xalign=0.0)
    lbl.set_hexpand(True)
    box.append(lbl)

    if has_chevron:
        chev = Gtk.Label(label="›")
        chev.add_css_class("maestri-menu-chevron")
        box.append(chev)

    btn.set_child(box)
    if callback is not None:
        btn.connect("clicked", lambda _b: callback())
    return btn


class CanvasContextMenu(Gtk.Popover):
    """Right-click menu on empty canvas background (uploaded_media_1)."""

    def __init__(self, window, canvas_point: Point) -> None:
        super().__init__()
        self.window = window
        self.cw = window.canvas_widget
        self.pt = canvas_point
        self.set_has_arrow(False)
        self.add_css_class("maestri-context-popover")

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(150)

        # 1. Main Page
        page_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        page_main.set_margin_top(4)
        page_main.set_margin_bottom(4)
        page_main.set_margin_start(4)
        page_main.set_margin_end(4)

        btn_add = make_menu_row(
            "maestri-plus-symbolic",
            "Add",
            callback=lambda: self.stack.set_visible_child_name("add"),
            has_chevron=True,
        )
        page_main.append(btn_add)

        btn_paste = make_menu_row(
            "maestri-paste-symbolic",
            "Paste",
            callback=self._on_paste,
        )
        page_main.append(btn_paste)

        btn_partituras = make_menu_row(
            "maestri-book-open-symbolic",
            "Partituras",
            callback=lambda: self.stack.set_visible_child_name("partituras"),
            has_chevron=True,
        )
        page_main.append(btn_partituras)

        self.stack.add_named(page_main, "main")

        # 2. Add Submenu Page
        page_add = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        page_add.set_margin_top(4)
        page_add.set_margin_bottom(4)
        page_add.set_margin_start(4)
        page_add.set_margin_end(4)

        # Header with back button
        hdr_add = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_back_add = Gtk.Button(label="‹")
        btn_back_add.add_css_class("maestri-menu-item-btn")
        btn_back_add.set_has_frame(False)
        btn_back_add.connect("clicked", lambda _b: self.stack.set_visible_child_name("main"))
        hdr_add.append(btn_back_add)
        lbl_add_title = Gtk.Label(label="<b>Add</b>", use_markup=True, xalign=0.0)
        lbl_add_title.set_hexpand(True)
        hdr_add.append(lbl_add_title)
        page_add.append(hdr_add)
        page_add.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        btn_term = make_menu_row(
            "maestri-terminal-symbolic",
            "Terminal",
            callback=lambda: self.stack.set_visible_child_name("terminal_presets"),
            has_chevron=True,
        )
        page_add.append(btn_term)

        btn_note = make_menu_row(
            "maestri-note-symbolic",
            "Note",
            callback=lambda: self._create_and_close("stickyNote"),
        )
        page_add.append(btn_note)

        btn_tree = make_menu_row(
            "maestri-file-tree-symbolic",
            "File Tree",
            callback=lambda: self._create_and_close("fileTree"),
        )
        page_add.append(btn_tree)

        btn_portal = make_menu_row(
            "maestri-globe-symbolic",
            "Portal",
            callback=lambda: self._create_and_close("portal"),
        )
        page_add.append(btn_portal)

        btn_text = make_menu_row(
            "maestri-text-symbolic",
            "Text",
            callback=self._select_text_tool,
        )
        page_add.append(btn_text)

        self.stack.add_named(page_add, "add")

        # 3. Terminal Presets Submenu Page
        page_term = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        page_term.set_margin_top(4)
        page_term.set_margin_bottom(4)
        page_term.set_margin_start(4)
        page_term.set_margin_end(4)

        hdr_term = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_back_term = Gtk.Button(label="‹")
        btn_back_term.add_css_class("maestri-menu-item-btn")
        btn_back_term.set_has_frame(False)
        btn_back_term.connect("clicked", lambda _b: self.stack.set_visible_child_name("add"))
        hdr_term.append(btn_back_term)
        lbl_term_title = Gtk.Label(label="<b>Terminal Presets</b>", use_markup=True, xalign=0.0)
        lbl_term_title.set_hexpand(True)
        hdr_term.append(lbl_term_title)
        page_term.append(hdr_term)
        page_term.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        presets = [
            ("Claude Code", "claude", "maestri-terminal-symbolic"),
            ("Codex", "codex", "maestri-terminal-symbolic"),
            ("Gemini CLI", "gemini", "maestri-terminal-symbolic"),
            ("OpenCode", "opencode", "maestri-terminal-symbolic"),
            ("Shell", "bash", "maestri-terminal-symbolic"),
        ]
        for name, cmd, ico in presets:
            btn_p = make_menu_row(
                ico,
                name,
                callback=lambda n=name, c=cmd: self._create_terminal_preset(n, c),
            )
            page_term.append(btn_p)

        page_term.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        btn_custom = make_menu_row(
            "maestri-sliders-symbolic",
            "Custom…",
            callback=self._open_terminal_dialog,
        )
        page_term.append(btn_custom)

        self.stack.add_named(page_term, "terminal_presets")

        # 4. Partituras Submenu Page
        page_part = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        page_part.set_margin_top(4)
        page_part.set_margin_bottom(4)
        page_part.set_margin_start(4)
        page_part.set_margin_end(4)

        hdr_part = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_back_part = Gtk.Button(label="‹")
        btn_back_part.add_css_class("maestri-menu-item-btn")
        btn_back_part.set_has_frame(False)
        btn_back_part.connect("clicked", lambda _b: self.stack.set_visible_child_name("main"))
        hdr_part.append(btn_back_part)
        lbl_part_title = Gtk.Label(label="<b>Partituras</b>", use_markup=True, xalign=0.0)
        lbl_part_title.set_hexpand(True)
        hdr_part.append(lbl_part_title)
        page_part.append(hdr_part)
        page_part.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        btn_save_part = make_menu_row(
            "maestri-book-open-symbolic",
            "Save Canvas as Partitura…",
            callback=self._save_partitura,
        )
        page_part.append(btn_save_part)

        self.stack.add_named(page_part, "partituras")

        self.set_child(self.stack)

    def _create_and_close(self, node_type: str) -> None:
        self.popdown()
        self.window._create_node(node_type, at=self.pt)

    def _create_terminal_preset(self, name: str, command: str) -> None:
        import uuid
        self.popdown()
        cwd = self.window.floors.terminal_cwd() if hasattr(self.window, "floors") else ""
        node_id = str(uuid.uuid4()).upper()
        frame = NodeFrame(x=self.pt.x, y=self.pt.y, width=420, height=300)
        content = TerminalContent(name=name, command=command, working_directory=cwd)
        node = CanvasNode(
            id=node_id,
            frame=frame,
            node_type="terminal",
            content=content,
            z_index=len(self.window.document.payload.nodes) + 1,
        )
        self.window.document.payload.nodes.append(node)
        self.window._add_node_to_canvas(node)
        self.window.canvas_widget.controller.dirty = True
        from maestri_core.signals.bus import bus
        bus.emit("node-added", node)
        if self.window.sync is not None:
            self.window.sync.flush()

    def _open_terminal_dialog(self) -> None:
        self.popdown()
        self.window.open_new_terminal_dialog(at=self.pt)

    def _select_text_tool(self) -> None:
        self.popdown()
        self.cw.set_tool("text")

    def _on_paste(self) -> None:
        self.popdown()
        sel = list(self.cw.scene.selection)
        if sel:
            mapping = {}
            for nid in sel:
                item = self.cw.scene.get(nid)
                if item:
                    f = item.frame
                    mapping[nid] = Rect(self.pt.x, self.pt.y, f.w, f.h)
            self.cw.emit("duplicate-requested", mapping)

    def _save_partitura(self) -> None:
        self.popdown()
        # Save partitura notice
        pass


class NodeContextMenu(Gtk.Popover):
    """Right-click menu on terminals and canvas nodes (uploaded_media_2)."""

    def __init__(self, window, node_id: str) -> None:
        super().__init__()
        self.window = window
        self.cw = window.canvas_widget
        self.node_id = node_id
        self.node = window._find_node(node_id)
        self.set_has_arrow(False)
        self.add_css_class("maestri-context-popover")

        menu_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_vbox.set_margin_top(4)
        menu_vbox.set_margin_bottom(4)
        menu_vbox.set_margin_start(4)
        menu_vbox.set_margin_end(4)

        # 1. Clipboard Group: Copy, Paste, Select All
        btn_copy = make_menu_row("maestri-copy-symbolic", "Copy", callback=self._on_copy)
        menu_vbox.append(btn_copy)

        btn_paste = make_menu_row("maestri-paste-symbolic", "Paste", callback=self._on_paste)
        menu_vbox.append(btn_paste)

        btn_select_all = make_menu_row("maestri-select-all-symbolic", "Select All", callback=self._on_select_all)
        menu_vbox.append(btn_select_all)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 2. Inspection & Identity: Rename, Edit..., Monitor Activity
        btn_rename = make_menu_row("maestri-pencil-symbolic", "Rename", callback=self._on_rename)
        menu_vbox.append(btn_rename)

        btn_edit = make_menu_row("maestri-sliders-symbolic", "Edit…", callback=self._on_edit)
        menu_vbox.append(btn_edit)

        is_monitored = bool(getattr(getattr(self.node, "content", None), "monitor_activity", True))
        btn_monitor = make_menu_row(
            "maestri-eye-symbolic",
            "Monitor Activity",
            callback=self._on_toggle_monitor,
            is_checked=is_monitored,
        )
        menu_vbox.append(btn_monitor)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 3. Lifecycle & View: Blur, Reload, Unload, New Element Defaults >
        btn_blur = make_menu_row("maestri-eye-off-symbolic", "Blur", callback=self._on_toggle_blur)
        menu_vbox.append(btn_blur)

        btn_reload = make_menu_row("maestri-reload-symbolic", "Reload", callback=self._on_reload)
        menu_vbox.append(btn_reload)

        btn_unload = make_menu_row("maestri-moon-symbolic", "Unload", callback=self._on_unload)
        menu_vbox.append(btn_unload)

        btn_defaults = make_menu_row("maestri-link-symbolic", "New Element Defaults", callback=None, has_chevron=True)
        menu_vbox.append(btn_defaults)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 4. Canvas Node Operations: Copy Element, Duplicate, Lock
        btn_copy_elem = make_menu_row("maestri-copy-symbolic", "Copy Element", callback=self._on_copy_element)
        menu_vbox.append(btn_copy_elem)

        btn_dup = make_menu_row("maestri-duplicate-symbolic", "Duplicate", callback=self._on_duplicate)
        menu_vbox.append(btn_dup)

        scene_item = self.cw.scene.get(self.node_id)
        is_locked = bool(scene_item and scene_item.locked)
        btn_lock = make_menu_row(
            "maestri-lock-symbolic",
            "Unlock" if is_locked else "Lock",
            callback=self._on_toggle_lock,
        )
        menu_vbox.append(btn_lock)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 5. Danger: Delete (Red)
        btn_delete = make_menu_row(
            "maestri-trash-symbolic",
            "Delete",
            callback=self._on_delete,
            is_danger=True,
        )
        menu_vbox.append(btn_delete)

        menu_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 6. Partituras
        btn_partituras = make_menu_row("maestri-book-open-symbolic", "Partituras", callback=None, has_chevron=True)
        menu_vbox.append(btn_partituras)

        self.set_child(menu_vbox)

    def _frame(self):
        return self.cw.frames.get(norm(self.node_id))

    def _on_copy(self) -> None:
        self.popdown()
        f = self._frame()
        if f and hasattr(f.content, "terminal"):
            try:
                import gi
                gi.require_version("Vte", "3.91")
                from gi.repository import Vte
                f.content.terminal.copy_clipboard_format(Vte.Format.TEXT)
            except Exception:
                pass

    def _on_paste(self) -> None:
        self.popdown()
        f = self._frame()
        if f and hasattr(f.content, "terminal"):
            try:
                f.content.terminal.paste_clipboard()
            except Exception:
                pass

    def _on_select_all(self) -> None:
        self.popdown()
        f = self._frame()
        if f and hasattr(f.content, "terminal"):
            try:
                f.content.terminal.select_all()
            except Exception:
                pass

    def _on_rename(self) -> None:
        self.popdown()
        f = self._frame()
        if f and hasattr(f, "start_inline_rename"):
            f.start_inline_rename()

    def _on_edit(self) -> None:
        self.popdown()
        if hasattr(self.window, "edit_selected_node"):
            self.window.edit_selected_node(self.node_id)

    def _on_toggle_monitor(self) -> None:
        self.popdown()
        if self.node and hasattr(self.node, "content"):
            curr = getattr(self.node.content, "monitor_activity", True)
            self.node.content.monitor_activity = not curr
            if self.window.sync is not None:
                self.window.sync.flush()

    def _on_toggle_blur(self) -> None:
        self.popdown()
        f = self._frame()
        if f:
            if f.has_css_class("maestri-node-blurred"):
                f.remove_css_class("maestri-node-blurred")
            else:
                f.add_css_class("maestri-node-blurred")

    def _on_reload(self) -> None:
        self.popdown()
        f = self._frame()
        if f and hasattr(f.content, "feed_output"):
            # Clear and reset terminal
            pass

    def _on_unload(self) -> None:
        self.popdown()
        pass

    def _on_copy_element(self) -> None:
        self.popdown()
        if self.node:
            try:
                clip = Gdk.Display.get_default().get_clipboard()
                clip.set(self.node.model_dump_json())
            except Exception:
                pass

    def _on_duplicate(self) -> None:
        self.popdown()
        if self.node:
            f = self.node.frame
            mapping = {self.node.id: Rect(f.x + 30, f.y + 30, f.width, f.height)}
            self.cw.emit("duplicate-requested", mapping)

    def _on_toggle_lock(self) -> None:
        self.popdown()
        item = self.cw.scene.get(self.node_id)
        if item:
            item.locked = not item.locked
            self.cw.controller.dirty = True
            f = self._frame()
            if f:
                f.set_badge("lock", "🔒" if item.locked else None)

    def _on_delete(self) -> None:
        self.popdown()
        self.cw.emit("delete-requested", (self.node_id,))
