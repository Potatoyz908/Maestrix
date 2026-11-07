"""Schema-v2 connection rules (pure). Only five node-type pairs may be connected; each lives in its own payload list.

    terminal-terminal -> connections            terminal-note   -> noteConnections (terminal always side A)
    terminal-portal   -> portalConnections      portal-portal   -> portalToPortalConnections
    note-note         -> noteToNoteConnections
Everything else (fileTree, note-portal, shapes, ...) is refused with a human reason."""
from __future__ import annotations

from dataclasses import dataclass

KINDS = {  # frozenset of types -> (kind, payload list attribute)
    frozenset({"terminal"}): ("terminal", "connections"),
    frozenset({"terminal", "stickyNote"}): ("terminalToNote", "note_connections"),
    frozenset({"terminal", "portal"}): ("terminalToPortal", "portal_connections"),
    frozenset({"portal"}): ("portalToPortal", "portal_to_portal_connections"),
    frozenset({"stickyNote"}): ("noteToNote", "note_to_note_connections"),
}
LIST_ATTRS = tuple(v[1] for v in KINDS.values()) + ("cross_floor_connections",)   # the 6 lists a deleted node must be purged from
_LABEL = {"terminal": "Terminal", "stickyNote": "Note", "portal": "Portal", "fileTree": "File Tree"}


@dataclass(frozen=True)
class Plan:
    kind: str
    list_attr: str
    a: str          # ids uppercased, terminal first
    b: str
    side_a: object
    side_b: object


def refusal_reason(type_a: str, type_b: str) -> str:
    la, lb = _LABEL.get(type_a, type_a), _LABEL.get(type_b, type_b)
    if "fileTree" in (type_a, type_b):
        return "A File Tree can't be connected"
    if {type_a, type_b} == {"stickyNote", "portal"}:
        return "A Note can't be connected to a Portal"
    return f"{la} and {lb} can't be connected"


def plan_connection(a: str, type_a: str, side_a, b: str, type_b: str, side_b) -> Plan | str:
    """Plan | refusal reason. Normalises: ids uppercase, terminal always A (sides swap with it)."""
    entry = KINDS.get(frozenset({type_a, type_b}))
    if entry is None:
        return refusal_reason(type_a, type_b)
    a, b = a.upper(), b.upper()
    if type_a != "terminal" and type_b == "terminal":
        a, b, side_a, side_b = b, a, side_b, side_a
    return Plan(entry[0], entry[1], a, b, side_a, side_b)


def purge_node(payload, node_id: str) -> int:
    """Drop every connection touching node_id from all 6 lists; returns how many were removed."""
    nid = node_id.upper()
    removed = 0
    for attr in LIST_ATTRS:
        lst = getattr(payload, attr, None)
        if not lst:
            continue
        keep = [c for c in lst if not _touches(c, nid)]
        removed += len(lst) - len(keep)
        lst[:] = keep
    return removed


def _touches(c, nid: str) -> bool:
    if isinstance(c, dict):   # crossFloorConnections are raw dicts
        return nid in (str(v).upper() for k, v in c.items() if k in ("nodeIdA", "nodeIdB"))
    return str(getattr(c, "a_id", "")).upper() == nid or str(getattr(c, "b_id", "")).upper() == nid
