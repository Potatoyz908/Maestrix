"""Ctrl+Z on the canvas: undo node moves/resizes, node creation and node deletion.

Text inside notes and shells keeps its own Ctrl+Z (GtkTextView undo / the shell); the canvas widget only hands Ctrl+Z here
when no text field or terminal has the focus. A deleted node comes back as a copy at the same place (a note with its text,
a terminal as a fresh shell) with its ropes; the stack is per workspace and cleared on a switch.
"""
from __future__ import annotations

import json
from typing import Callable

from gi.repository import GLib

from maestri_core.models.node import NodeFrame
from maestri_core.signals.bus import bus
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import norm

LIMIT = 100


class CanvasUndo:
    def __init__(self, win) -> None:
        self.win = win
        self._stack: list[Callable[[], None]] = []
        self._busy = False              # replaying an undo: its own changes are not recorded
        self._gesture: dict[str, NodeFrame] = {}   # node id -> frame before the drag/resize in progress
        self._moved: dict[str, NodeFrame] = {}     # finished gesture, grouped until idle (a multi-node drag)
        self._added: list[str] = []
        self._undone_adds: set[str] = set()   # nodes removed by undoing their creation (notes report it asynchronously)
        self._idle = 0
        bus.connect("node-added", lambda _b, node: self._node_added(node))
        for sig in ("workspace-switched", "workspace-loaded"):
            bus.connect(sig, lambda *_: self.clear())

    def clear(self) -> None:
        self._stack.clear()
        self._gesture.clear()
        self._moved.clear()
        self._added.clear()

    def can_undo(self) -> bool:
        return bool(self._stack)

    # ---- recording ---------------------------------------------------------------------------------------------------
    def frame_changed(self, node_id: str, old: NodeFrame, new: NodeFrame, final: bool) -> None:
        if self._busy:
            return
        k = node_id.upper()
        start = self._gesture.setdefault(k, old)
        if not final:
            return
        del self._gesture[k]
        if (start.x, start.y, start.width, start.height) != (new.x, new.y, new.width, new.height):
            self._moved.setdefault(k, start)
            self._schedule()

    def _node_added(self, node) -> None:
        if not self._busy and getattr(node, "id", None):
            self._added.append(node.id)
            self._schedule()

    def nodes_deleting(self, ids) -> None:
        """Called before the window removes `ids`: keeps what is needed to bring them back."""
        if self._busy:
            return
        self._commit()
        doc = self.win.document.payload
        want = {i.upper() for i in ids} - self._undone_adds
        self._undone_adds -= {i.upper() for i in ids}
        nodes = [n for n in doc.nodes if n.id.upper() in want and not n.is_locked]
        if not nodes:
            return
        data = json.loads(self.win._element_json(nodes))
        external = [(c.a_id, c.b_id) for c in doc.all_connections()
                    if (c.a_id.upper() in want) != (c.b_id.upper() in want)]
        self._push(lambda: self._restore(json.dumps(data), [n.id.upper() for n in nodes], external))

    def _schedule(self) -> None:
        if not self._idle:
            self._idle = GLib.idle_add(self._commit)

    def _commit(self) -> bool:
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        if self._moved:
            frames, self._moved = self._moved, {}
            self._push(lambda: self._set_frames(frames))
        if self._added:
            ids, self._added = self._added, []
            self._push(lambda: self._remove_added(ids))
        return False

    def _push(self, fn: Callable[[], None]) -> None:
        self._stack.append(fn)
        del self._stack[:-LIMIT]

    # ---- replay ------------------------------------------------------------------------------------------------------
    def undo(self) -> bool:
        self._commit()
        if not self._stack:
            return False
        fn = self._stack.pop()
        self._busy = True
        try:
            fn()
        finally:
            self._busy = False
        return True

    def _remove_added(self, ids: list[str]) -> None:
        self._undone_adds.update(i.upper() for i in ids)
        self.win.canvas_widget.request_delete(tuple(ids))

    def _set_frames(self, frames: dict[str, NodeFrame]) -> None:
        cw = self.win.canvas_widget
        for nid, f in frames.items():
            if self.win._find_node(nid) is None:
                continue
            cw.update_node_frame(nid, Rect(f.x, f.y, f.width, f.height))
            self.win._on_canvas_frame_changed(cw, nid, f.x, f.y, f.width, f.height, True)

    def _restore(self, data: str, old_ids: list[str], external: list[tuple[str, str]]) -> None:
        obj = json.loads(data)
        xs = [n["frame"][0][0] for n in obj["nodes"]]
        ys = [n["frame"][0][1] for n in obj["nodes"]]
        created = self.win._paste_payload(data, Point(min(xs), min(ys)))
        id_map = {old: new.id for old, new in zip(old_ids, created)}
        for a, b in external:   # ropes to nodes that were not deleted
            a2, b2 = id_map.get(a.upper(), a), id_map.get(b.upper(), b)
            if self.win._find_node(a2) is not None and self.win._find_node(b2) is not None:
                self.win._on_canvas_connect_requested(self.win.canvas_widget, a2, None, b2, None)
        cw = self.win.canvas_widget
        cw.scene.selection = {norm(n.id) for n in created}
        cw._sync_selection_css()
        cw.controller.dirty = True
