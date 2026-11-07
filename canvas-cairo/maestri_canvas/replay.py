"""Normalization of the core's scrollback replay frame (pure, no GTK).

On every websocket connection the core sends ONE binary frame first: a plain-text snapshot of its headless terminal
(`getBufferAsData`): rows joined by a bare "\\n" (no "\\r") and padded with one "\\n" per empty row below the cursor. Fed as is, a real
terminal shows a staircase (each line starts where the previous one ended) and the prompt ends up at the bottom, far from the shell's
actual cursor. Live PTY output is different: it has "\\r\\n" (tty onlcr) and usually escape sequences.

Heuristic, applied to the FIRST frame of a connection only: if it has "\\n" but no "\\r" and no ESC it is a snapshot, so drop the trailing
blank rows and convert "\\n" to "\\r\\n". A protocol marker for the replay frame would remove the guesswork (proposed to the core owner).
"""
from __future__ import annotations


def normalize_replay(data: bytes) -> bytes:
    if b"\n" not in data or b"\r" in data or b"\x1b" in data:
        return data   # not a plain-text snapshot: leave live output untouched
    return data.rstrip(b"\n").replace(b"\n", b"\r\n")
