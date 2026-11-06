"""Window-level actions the menus, keys and canvas call (Sprint 9 Vertice §4, contracts K9 / K10 / K11 / K2).

Mixed into OpenMaestriWindow (window.py), which owns `document`, `canvas_widget`, `client`, `sync`, `core_connected`.

  K9   delete_connection(conn_id) · node-lock-changed / node-z-changed persistence · locked nodes refuse delete
  K10  copy_selected_nodes() · copy_node(node_id) · paste_nodes(at=None)   (mime application/x-maestri-element)
  K11  save_partitura(name, node_ids=None) · apply_partitura(partitura_id, at=None)
  §4.8 next_workspace() / prev_workspace() (Ctrl+Down / Ctrl+Up) · jump_to_terminal(n) (Ctrl+1..9)
  §4.9 open_in_editor() (View > Open in Editor, Ctrl+Shift+E; header button)
  §4.16 close_workspace() - the last workspace was deleted: empty canvas, no autosave re-creating it
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from typing import Iterable, List, Optional

from gi.repository import Gdk, GLib, GObject

from maestri_core.models.connection import Connection
from maestri_core.models.node import CanvasNode, NodeFrame, PortalContent, StickyNoteContent, current_iso8601
from maestri_core.signals.bus import bus

from maestri_canvas.connection_rules import LIST_ATTRS
from maestri_canvas.geometry import Point, Rect
from maestri_canvas.scene import norm

ELEMENT_MIME = "application/x-maestri-element"
URL_RE = re.compile(r"^(https?|file)://\S+$", re.IGNORECASE)
PASTE_OFFSET = 24.0
# preferredIDE (Preferences.swift: "cursor" | "vscode" | "xcode") -> Linux launchers, first one found on PATH wins
IDE_COMMANDS = {
    "cursor": ("cursor",),
    "vscode": ("code", "codium", "code-oss"),
    "zed": ("zed", "zeditor"),
}


class WindowActionsMixin:
    # ============================================================================================================
    # K9 - connections, lock, z-order
    # ============================================================================================================
    def delete_connection(self, conn_id: str) -> bool:
        """Remove a rope: canvas + payload now, then DELETE /api/connections/:id (the core revokes the permission, K6)."""
        cid = str(conn_id).upper()
        removed = False
        for attr in LIST_ATTRS:
            lst = getattr(self.document.payload, attr, None)
            if lst is None:
                continue
            keep = [c for c in lst if c.id.upper() != cid]
            if len(keep) != len(lst):
                lst[:] = keep
                removed = True
        cw = self.canvas_widget
        if norm(cid) in cw.scene.connections:
            cw.remove_connection(cid)
            removed = True
        if not removed:
            return False
        self.document.payload.last_modified_at = current_iso8601()
        cw.controller.dirty = True
        cw.queue_draw()
        bus.emit("connection-removed", cid)
        if self.core_connected and not cid.startswith("LOCAL-"):
            ws = self.document.payload.id
            threading.Thread(target=self._delete_core_connection, args=(cid, ws), daemon=True).start()
        if self.sync is not None:
            self.sync.flush()
        return True

    def _delete_core_connection(self, cid: str, ws: str) -> None:
        try:
            self.client.delete_connection(cid, workspace_id=ws)
        except Exception as exc:   # noqa: BLE001 - the saved document no longer has it; the core rebuilds on save (K6)
            print(f"[window] DELETE connection {cid[:8]} failed: {exc!r}")

    def _connect_k9_signals(self) -> None:
        """Persist lock / z-order changes the canvas reports (signals provided by the canvas engine, K9)."""
        cw = self.canvas_widget
        if GObject.signal_lookup("node-lock-changed", type(cw)):
            cw.connect("node-lock-changed", lambda _c, nid, locked: self.set_node_locked(nid, locked))
        if GObject.signal_lookup("node-z-changed", type(cw)):
            cw.connect("node-z-changed", lambda _c, nid, z: self._on_node_z_changed(nid, z))

    def set_node_locked(self, node_id: str, locked: bool) -> bool:
        node = self._find_node(node_id)
        if node is None:
            return False
        item = self.canvas_widget.scene.get(node.id)
        if item is not None:
            item.locked = bool(locked)
        if node.is_locked == bool(locked):
            return True
        node.is_locked = bool(locked)
        node.last_modified_at = current_iso8601()
        bus.emit("node-updated", node.id)
        if self.sync is not None:
            self.sync.flush()
        return True

    def _on_node_z_changed(self, node_id: str, z: int) -> None:
        node = self._find_node(node_id)
        if node is None or node.z_index == int(z):
            return
        node.z_index = int(z)
        bus.emit("node-updated", node.id)   # debounced save; z changes come in bursts while clicking around

    def is_node_locked(self, node_id: str) -> bool:
        node = self._find_node(node_id)
        return bool(node is not None and node.is_locked)

    # ============================================================================================================
    # K10 - element clipboard
    # ============================================================================================================
    def _element_json(self, nodes: List[CanvasNode]) -> str:
        ids = {n.id.upper() for n in nodes}
        conns = [c.to_dict() | {"kind": c.kind} for c in self.document.payload.all_connections()
                 if c.a_id.upper() in ids and c.b_id.upper() in ids]
        return json.dumps({"nodes": [n.to_dict() for n in nodes], "connections": conns})

    def _set_element_clipboard(self, nodes: List[CanvasNode]) -> bool:
        if not nodes:
            return False
        data = self._element_json(nodes)
        self._element_clip = data   # in-process copy (reading our own provider back through a pipe is pointless)
        display = Gdk.Display.get_default()
        if display is None:
            return True
        names = ", ".join(getattr(n.content, "name", n.node_type) for n in nodes)
        provider = Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_bytes(ELEMENT_MIME, GLib.Bytes.new(data.encode("utf-8"))),
            Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(names.encode("utf-8"))),
        ])
        display.get_clipboard().set_content(provider)
        self._element_clip_provider = provider
        return True

    def copy_node(self, node_id: str) -> bool:
        node = self._find_node(node_id)
        return self._set_element_clipboard([node] if node is not None else [])

    def copy_selected_nodes(self) -> bool:
        sel = {s.upper() for s in self.canvas_widget.scene.selection}
        return self._set_element_clipboard([n for n in self.document.payload.nodes if n.id.upper() in sel])

    def paste_nodes(self, at: Optional[Point] = None) -> None:
        """Element JSON -> clones with new ids; a URL -> Portal; other text -> Note; nothing recognised -> nothing."""
        if not getattr(self, "loaded", True):
            return
        display = Gdk.Display.get_default()
        clip = display.get_clipboard() if display is not None else None
        local = getattr(self, "_element_clip", None)
        if clip is None:
            if local:
                self._paste_payload(local, at)
            return
        formats = clip.get_formats()
        if clip.is_local() and local and formats.contain_mime_type(ELEMENT_MIME):
            self._paste_payload(local, at)
        elif formats.contain_mime_type(ELEMENT_MIME):
            self._read_clipboard_mime(clip, at)
        else:
            clip.read_text_async(None, lambda c, res: self._paste_text_done(c, res, at))

    def _read_clipboard_mime(self, clip, at) -> None:
        def on_stream(c, res):
            try:
                stream, _mime = c.read_finish(res)
            except GLib.Error:
                return
            chunks: list[bytes] = []

            def on_bytes(s, r):
                try:
                    b = s.read_bytes_finish(r)
                except GLib.Error:
                    return
                if b.get_size() == 0:
                    s.close_async(GLib.PRIORITY_DEFAULT, None, None)
                    self._paste_payload(b"".join(chunks).decode("utf-8", "replace"), at)
                    return
                chunks.append(b.get_data())
                s.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, on_bytes)

            stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, on_bytes)

        clip.read_async([ELEMENT_MIME], GLib.PRIORITY_DEFAULT, None, on_stream)

    def _paste_text_done(self, clip, res, at) -> None:
        try:
            text = clip.read_text_finish(res)
        except GLib.Error:
            text = None
        text = (text or "").strip()
        if not text:
            return
        if text.startswith("{") and '"nodes"' in text:
            self._paste_payload(text, at)
        elif URL_RE.match(text):
            self._create_node("portal", at=at, content=PortalContent(name=_url_title(text), current_url=text))
        else:
            self._paste_note(text, at)

    def _paste_note(self, text: str, at) -> None:
        before = {n.id for n in self.document.payload.nodes}
        self._create_node("stickyNote", at=at)
        new = next((n for n in self.document.payload.nodes if n.id not in before), None)
        if new is None:
            return
        frame = self.canvas_widget.frames.get(norm(new.id))
        view = getattr(frame, "content", None)
        tries = [0]

        def fill():   # the note loads its (empty) file asynchronously; write once it is editable
            tries[0] += 1
            if view is None or getattr(view, "_disposed", False):
                return GLib.SOURCE_REMOVE
            if getattr(view, "_loaded", True) and hasattr(view, "buf"):
                view.buf.set_text(text)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE if tries[0] < 60 else GLib.SOURCE_REMOVE

        if fill() == GLib.SOURCE_CONTINUE:
            GLib.timeout_add(50, fill)

    def _paste_payload(self, data: str, at: Optional[Point]) -> List[CanvasNode]:
        try:
            obj = json.loads(data)
        except ValueError:
            return []
        raw_nodes = [d for d in (obj.get("nodes") or ([obj["node"]] if obj.get("node") else [])) if isinstance(d, dict)]
        if not raw_nodes:
            return []
        srcs = [CanvasNode.from_dict(d) for d in raw_nodes]
        min_x = min(n.frame.x for n in srcs)
        min_y = min(n.frame.y for n in srcs)
        dx, dy = (at.x - min_x, at.y - min_y) if at is not None else (PASTE_OFFSET, PASTE_OFFSET)
        names = {getattr(n.content, "name", None) for n in self.document.payload.nodes}
        id_map: dict[str, str] = {}
        created: List[CanvasNode] = []
        max_z = max((n.z_index for n in self.document.payload.nodes), default=0)
        for i, src in enumerate(srcs):
            new_id = str(uuid.uuid4()).upper()
            id_map[src.id.upper()] = new_id
            c = src.content
            if hasattr(c, "terminal_id"):          # K1: terminal id == node id
                c.id = c.terminal_id = new_id
                c.scrollback_file = None
            elif hasattr(c, "id") and not isinstance(c, dict):
                c.id = str(uuid.uuid4()).upper()
            name = getattr(c, "name", None)
            if name and name in names:            # " Copy" only when the name collides
                c.name = f"{name} Copy"
            if isinstance(c, StickyNoteContent) and "managed" in (c.storage_mode or {}):
                c.file_name = f"{c.name}.md"
            node = CanvasNode(id=new_id, frame=NodeFrame(x=src.frame.x + dx, y=src.frame.y + dy, width=src.frame.width,
                                                         height=src.frame.height),
                              node_type=src.node_type, content=c, z_index=max_z + 1 + i)
            self.document.payload.nodes.append(node)
            self._add_node_to_canvas(node)
            bus.emit("node-added", node)
            created.append(node)
        for raw in obj.get("connections") or []:   # recreate ropes between pasted nodes through the normal path
            if not isinstance(raw, dict):
                continue
            conn = Connection.from_dict(raw, default_kind=raw.get("kind", "terminal"))
            a, b = id_map.get(conn.a_id.upper()), id_map.get(conn.b_id.upper())
            if a and b:
                self._on_canvas_connect_requested(self.canvas_widget, a, None, b, None)
        self.canvas_widget.controller.dirty = True
        if self.sync is not None:
            self.sync.flush()
        return created

    # ============================================================================================================
    # K11 - partituras
    # ============================================================================================================
    def list_partituras(self) -> list:
        """Saved partituras (GET /api/partituras). Blocking: the menus call it off the main thread."""
        try:
            return self.client.list_partituras()
        except Exception:   # noqa: BLE001 - offline / no route yet
            return []

    def save_partitura(self, name: str, node_ids: Optional[Iterable[str]] = None) -> None:
        """Save the given nodes (default: every terminal) and the connections among them as a reusable layout."""
        wanted = {i.upper() for i in node_ids} if node_ids else None
        nodes = [n for n in self.document.payload.nodes
                 if (n.id.upper() in wanted if wanted is not None else n.node_type == "terminal")]
        if not nodes or not name.strip():
            return
        min_x = min(n.frame.x for n in nodes)
        min_y = min(n.frame.y for n in nodes)
        out_nodes = []
        for n in nodes:
            d = n.to_dict()
            d["frame"] = [[n.frame.x - min_x, n.frame.y - min_y], [n.frame.width, n.frame.height]]   # relative to the bbox
            out_nodes.append(d)
        ids = {n.id.upper() for n in nodes}
        conns = [c.to_dict() | {"kind": c.kind} for c in self.document.payload.all_connections()
                 if c.a_id.upper() in ids and c.b_id.upper() in ids]
        client, label = self.client, name.strip()

        def worker():
            try:
                client.create_partitura(label, out_nodes, conns)
                ok = True
            except Exception as exc:   # noqa: BLE001
                print(f"[window] save partitura failed: {exc!r}")
                ok = False
            GLib.idle_add(lambda: (self._toast(f"Saved partitura “{label}”" if ok else "Could not save the partitura"),
                                   GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=worker, name="maestri-partitura-save", daemon=True).start()

    def apply_partitura(self, partitura_id: str, at: Optional[Point] = None) -> None:
        """The core creates the nodes/connections (K11) and announces them (K2); a reply carrying them is applied too
        (deduplicated by id), so the canvas updates even without the event stream."""
        if at is None:
            cw = self.canvas_widget
            at = cw.viewport.to_canvas(Point((cw.get_width() or 1200) / 2.0, (cw.get_height() or 800) / 2.0))
        client, ws, pt = self.client, self.document.payload.id, [at.x, at.y]

        def worker():
            try:
                res = client.apply_partitura(partitura_id, workspace_id=ws, at=pt)
            except Exception as exc:   # noqa: BLE001
                print(f"[window] apply partitura failed: {exc!r}")
                res = None
            GLib.idle_add(self._apply_partitura_result, ws, res)
        threading.Thread(target=worker, name="maestri-partitura-apply", daemon=True).start()

    def _apply_partitura_result(self, ws: str, res) -> bool:
        if res is None:
            self._toast("Could not apply the partitura")
            return GLib.SOURCE_REMOVE
        live = getattr(self, "live_events", None)
        if live is not None and isinstance(res, dict):
            for node in res.get("nodes") or []:
                if isinstance(node, dict):
                    live.handle("nodeAdded", {"workspaceId": ws, "node": node})
            for conn in res.get("connections") or []:
                if isinstance(conn, dict):
                    live.handle("connectionCreated", {"workspaceId": ws, "connection": conn, "kind": conn.get("kind", ""),
                                                      "connectionId": conn.get("id")})
        return GLib.SOURCE_REMOVE

    def _toast(self, text: str) -> None:
        lbl = getattr(self, "lbl_status", None)
        if lbl is not None:
            lbl.set_tooltip_text(text)
        print(f"[window] {text}")

    # ============================================================================================================
    # §4.8 workspace / terminal navigation
    # ============================================================================================================
    def _sidebar_order(self) -> List[str]:
        sb = getattr(self, "sidebar", None)
        entries = list(getattr(sb, "_raw_workspaces", []) or [])
        entries.sort(key=lambda w: (not w.is_pinned, w.name.lower()))   # the order the sidebar shows
        return [w.id.upper() for w in entries]

    def _step_workspace(self, step: int) -> Optional[str]:
        order = self._sidebar_order()
        if not order:
            return None
        cur = str(self.document.payload.id).upper()
        i = order.index(cur) if cur in order else -1
        target = order[(i + step) % len(order)]
        if target != cur:
            bus.emit("workspace-switched", target)
        return target

    def next_workspace(self) -> Optional[str]:
        return self._step_workspace(+1)

    def prev_workspace(self) -> Optional[str]:
        return self._step_workspace(-1)

    def terminals_in_order(self) -> List[CanvasNode]:
        """Terminals by creation order (createdAt, then document order): the Ctrl+1..9 numbering."""
        terms = [n for n in self.document.payload.nodes if n.node_type == "terminal"]
        return sorted(terms, key=lambda n: (n.created_at or "", self.document.payload.nodes.index(n)))

    def jump_to_terminal(self, n: int) -> bool:
        cw = self.canvas_widget
        if hasattr(cw, "jump_to_terminal"):   # K9 (canvas engine) when available
            return bool(cw.jump_to_terminal(n))
        terms = self.terminals_in_order()
        if not 1 <= n <= len(terms):
            return False
        node = terms[n - 1]
        f = node.frame
        cw.viewport.focus_on(Rect(f.x, f.y, f.width, f.height), cw.get_width() or 1200, cw.get_height() or 800)
        cw.controller.dirty = True
        frame = cw.frames.get(norm(node.id))
        if frame is not None and frame.content is not None:
            frame.content.grab_focus()
        cw.queue_draw()
        return True

    def step_terminal(self, step: int) -> bool:
        """View > Next / Previous Terminal (Ctrl+Tab / Ctrl+Shift+Tab on macOS)."""
        terms = self.terminals_in_order()
        if not terms:
            return False
        sel = {s.upper() for s in self.canvas_widget.scene.selection}
        cur = next((i for i, t in enumerate(terms) if t.id.upper() in sel), -1)
        return self.jump_to_terminal(((cur + step) % len(terms)) + 1)

    # ============================================================================================================
    # §4.9 Open in Editor
    # ============================================================================================================
    def editor_command(self) -> Optional[List[str]]:
        prefs = getattr(self, "preferences", None)
        ide = str(getattr(prefs, "preferred_ide", "") or getattr(self.document.payload, "_extra", {}).get("preferredIDE")
                  or "cursor").lower()
        for exe in IDE_COMMANDS.get(ide, ()) + tuple(c for k, v in IDE_COMMANDS.items() if k != ide for c in v):
            path = shutil.which(exe)
            if path:
                return [path]
        return None

    def open_in_editor(self) -> bool:
        """Open the workspace's working directory in the preferred IDE (Preferences > General > Preferred IDE); without a
        known IDE on PATH, the folder opens in the file manager (macOS does the same with Finder)."""
        wd = os.path.expanduser(self.document.payload.working_directory or "~")
        cmd = self.editor_command()
        argv = (cmd + [wd]) if cmd else ["xdg-open", wd]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=wd
                             if os.path.isdir(wd) else None)
            return True
        except OSError as exc:
            self._toast(f"Could not open the editor: {exc}")
            return False

    # ============================================================================================================
    # §4.16 the last workspace was deleted
    # ============================================================================================================
    def close_workspace(self) -> None:
        """Back to the empty state: drop pending saves, clear the canvas, keep no document that autosave could re-create."""
        from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
        if self.sync is not None:
            self.sync.close()
            self.sync = None
        if getattr(self, "_autosave_id", 0):
            GLib.source_remove(self._autosave_id)
            self._autosave_id = 0
        cw = self.canvas_widget
        for frame in list(cw.frames.values()):
            content = frame.content
            if getattr(content, "transport", None) is not None:
                content.dispose()
        for nid in list(cw.scene.nodes):
            cw.remove_node(nid)
        self.document = WorkspaceDocument(payload=WorkspacePayload(id="", name="", nodes=[]))
        self._no_workspace = True
        cw.controller.dirty = True
        cw.queue_draw()
        try:
            self.set_title("Open Maestri")
        except Exception:   # noqa: BLE001
            pass


def _url_title(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/]+)", url, re.IGNORECASE)
    return m.group(1) if m else "Portal"
