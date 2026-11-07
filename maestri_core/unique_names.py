"""Terminal names must be unique inside a workspace: `omaestri ask <name>` resolves by name."""
from __future__ import annotations


def _key(name: str) -> str:
    return (name or "").strip().casefold()


def unique_name(name: str, taken) -> str:
    """`name` if free (case-insensitive), else `name #2`, `name #3`... The first free one."""
    base = (name or "Terminal").strip() or "Terminal"
    used = {_key(t) for t in taken}
    if _key(base) not in used:
        return base
    n = 2
    while _key(f"{base} #{n}") in used:
        n += 1
    return f"{base} #{n}"


def dedupe_terminal_names(nodes) -> bool:
    """Rename already-saved duplicates in place: every member of a group of equal names becomes `name #1`, `name #2`...
    Returns True when something changed."""
    terms = [n for n in nodes if getattr(n, "node_type", "") == "terminal" and hasattr(n.content, "name")]
    groups: dict[str, list] = {}
    for n in terms:
        groups.setdefault(_key(n.content.name), []).append(n)
    changed = False
    taken = {_key(n.content.name) for n in terms}
    for members in groups.values():
        if len(members) < 2:
            continue
        base = members[0].content.name.strip() or "Terminal"
        taken.discard(_key(base))
        for i, n in enumerate(members, 1):
            new = f"{base} #{i}"
            k = 1
            while _key(new) in taken:      # an unrelated node already uses it
                k += 1
                new = f"{base} #{i}.{k}"
            taken.add(_key(new))
            n.content.name = new
        changed = True
    return changed
