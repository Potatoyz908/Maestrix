"""K2: nodes and connections created/removed by the server (CLI: note create, recruit, dismiss, connect, partitura apply...)
show up on the open canvas without a reload and WITHOUT an echo.

The window subscribes to /ws/events (all workspaces) and hands each event to `LiveEvents.handle(type, event)` on the main loop.

Rules (docs/linux-gtk-sprint9-spec.md §3 K2):
  * the event's workspace is not the open document -> only the sidebar badge (`terminal-count-changed`) is updated;
  * an id the document already has (our own save/POST coming back) -> ignored: that is the no-echo guarantee;
  * otherwise the node/connection is added to (removed from) `document.payload` and the canvas directly. Nothing is emitted on
    the bus 'node-added' / 'connection-created' signals (WorkspaceSync would re-POST the document), nothing is POSTed to
    /api/connections, and no core terminal is deleted (the server already did whatever it announced).

Accepted shapes (the core's flat events, plus the older/nested ones the tests use):
  nodeAdded          {workspaceId, node: <CanvasNode JSON>, nodeId?, terminalCount?}
  nodeRemoved        {workspaceId, nodeId, terminalCount?}
  connectionCreated  {workspaceId, connectionId|id, kind, a|sourceNodeId, b|targetNodeId, connection?: <schema-v2 JSON>}
  connectionRemoved  {workspaceId, connectionId|id}
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from maestri_core.models.connection import Connection
from maestri_core.models.node import CanvasNode
from maestri_core.signals.bus import bus

from maestri_canvas.connection_rules import KINDS, LIST_ATTRS, plan_connection, purge_node
from maestri_canvas.scene import ConnItem, norm

KIND_LIST = {kind: attr for kind, attr in KINDS.values()}          # "terminalToNote" -> "note_connections"
KIND_ALIASES = {"note": "terminalToNote", "portal": "terminalToPortal", "terminalToTerminal": "terminal"}


def _upper(v: Any) -> str:
    return str(v or "").upper()


class LiveEvents:
    def __init__(self, window) -> None:
        self.win = window
        self.applied = 0            # events that changed the open document (tests / diagnostics)

    # ---- helpers ------------------------------------------------------------------------------------------------------
    @property
    def payload(self):
        return self.win.document.payload

    def _is_open(self, ws_id: str) -> bool:
        return not ws_id or ws_id == _upper(self.payload.id)

    def node_id_for(self, any_id: str) -> Optional[str]:
        """Node id for a node id OR a legacy terminal id (old GUI workspaces had node id != terminalId)."""
        key = _upper(any_id)
        for n in self.payload.nodes:
            if n.id.upper() == key or _upper(getattr(n.content, "terminal_id", "")) == key:
                return n.id.upper()
        return None

    def _conn_known(self, cid: str) -> bool:
        return any(c.id.upper() == cid for c in self.payload.all_connections()) or \
            norm(cid) in self.win.canvas_widget.scene.connections

    # ---- dispatch -----------------------------------------------------------------------------------------------------
    def handle(self, etype: str, evt: Dict[str, Any]) -> bool:
        """True when the open document changed."""
        if not isinstance(evt, dict):
            return False
        ws = _upper(evt.get("workspaceId"))
        count = evt.get("terminalCount")
        if ws and isinstance(count, int) and not isinstance(count, bool):
            bus.emit("terminal-count-changed", ws, count)
        if not self._is_open(ws) or not getattr(self.win, "loaded", True):
            return False
        fn = {"nodeAdded": self.node_added, "nodeRemoved": self.node_removed,
              "connectionCreated": self.connection_created, "connectionRemoved": self.connection_removed,
              "terminalExited": self.terminal_state, "terminalBecameIdle": self.terminal_state,
              "terminalBecameBusy": self.terminal_state}.get(etype)
        changed = bool(fn and fn(dict(evt, type=etype)))
        if changed:
            self.applied += 1
            cw = self.win.canvas_widget
            cw.controller.dirty = True
            cw.queue_draw()
        return changed

    def terminal_state(self, evt: Dict[str, Any]) -> bool:
        nid = self.node_id_for(evt.get("terminalId"))
        if nid is None:
            return False
        frame = self.win.canvas_widget.frames.get(norm(nid))
        features = getattr(frame, "terminal_features", None)
        if features is None:
            return False
        kind = evt.get("type")
        if kind == "terminalExited":
            self.win.canvas_widget.set_terminal_exited(nid, int(evt.get("exitCode", 0)))
        else:
            features._set_busy(kind == "terminalBecameBusy")
        return True

    # ---- nodes --------------------------------------------------------------------------------------------------------
    def node_added(self, evt: Dict[str, Any]) -> bool:
        raw = evt.get("node")
        if not isinstance(raw, dict):
            return False   # an id alone is not enough to build the node; the next workspace load brings it
        node = CanvasNode.from_dict(raw)
        if self.win._find_node(node.id) is not None:
            return False   # our own node coming back: no echo
        self.payload.nodes.append(node)
        self.win._add_node_to_canvas(node)
        self._recount()
        return True

    def node_removed(self, evt: Dict[str, Any]) -> bool:
        nid = self.node_id_for(evt.get("nodeId") or evt.get("id"))
        if nid is None:
            return False
        node = self.win._find_node(nid)
        if node is not None:
            self.payload.nodes.remove(node)
        purge_node(self.payload, nid)
        self.win.canvas_widget.remove_node(nid)
        self._recount()
        return True

    def _recount(self) -> None:
        from .core_link import count_terminals
        bus.emit("terminal-count-changed", _upper(self.payload.id), count_terminals(self.payload.nodes))

    # ---- connections --------------------------------------------------------------------------------------------------
    def connection_created(self, evt: Dict[str, Any]) -> bool:
        raw = evt.get("connection") if isinstance(evt.get("connection"), dict) else None
        kind = KIND_ALIASES.get(str(evt.get("kind") or ""), str(evt.get("kind") or ""))
        if raw is not None:
            conn = Connection.from_dict(raw, default_kind=kind or "terminal")
            a, b = conn.a_id, conn.b_id
            cid = _upper(evt.get("connectionId") or conn.id)
        else:
            a = evt.get("a") or evt.get("sourceNodeId") or evt.get("aId")
            b = evt.get("b") or evt.get("targetNodeId") or evt.get("bId")
            cid = _upper(evt.get("connectionId") or evt.get("id"))
            conn = None
        if not cid or self._conn_known(cid):
            return False
        na, nb = self.node_id_for(a), self.node_id_for(b)
        if na is None or nb is None:
            return False
        cw = self.win.canvas_widget
        if not cw.scene.can_connect(na, nb):
            return False   # our own rope (pending or confirmed) between the same pair: the POST's reply confirms it
        if kind not in KIND_LIST:   # unknown/alias kind: derive it from the node types, like a UI drop
            ta, tb = self.win._find_node(na).node_type, self.win._find_node(nb).node_type
            plan = plan_connection(na, ta, None, nb, tb, None)
            if isinstance(plan, str):
                return False
            kind, na, nb = plan.kind, plan.a, plan.b
        if conn is None:
            conn = Connection(id=cid, a_id=na, b_id=nb, kind=kind)
        conn.id = cid
        conn.kind = kind
        getattr(self.payload, KIND_LIST[kind]).append(conn)
        cw.add_connection(ConnItem(cid, na, nb))
        return True

    def connection_removed(self, evt: Dict[str, Any]) -> bool:
        cid = _upper(evt.get("connectionId") or evt.get("id"))
        if not cid:
            return False
        changed = False
        for attr in LIST_ATTRS:
            lst = getattr(self.payload, attr, None)
            if lst is None:
                continue
            keep = [c for c in lst if c.id.upper() != cid]
            if len(keep) != len(lst):
                lst[:] = keep
                changed = True
        if norm(cid) in self.win.canvas_widget.scene.connections:
            self.win.canvas_widget.remove_connection(cid)
            changed = True
        return changed
