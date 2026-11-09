import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "gtk4-ui" / "src"), str(ROOT)]

from ui.terminal_attention import AttentionTracker  # noqa: E402

W = "WS-1"


def row(tid, last, ws=W, running=True):
    return {"terminalId": tid, "workspaceId": ws, "lastOutputAt": last, "running": running, "displayName": tid}


def test_first_sight_is_baseline_not_unread():
    t = AttentionTracker()
    t.update([row("a", 100.0)], now=200.0)
    assert t.state("a", 200.0) == "idle"


def test_working_then_unread_after_settling_in_a_closed_workspace():
    t = AttentionTracker()
    t.update([row("a", 100.0)], now=100.0)
    t.update([row("a", 110.0)], now=110.5)
    assert t.state("a", 110.5) == "busy"
    assert t.summary(W, 110.5) == (1, 0)
    t.update([row("a", 110.0)], now=113.0)
    assert t.state("a", 113.0) == "unread"
    assert t.summary(W, 113.0) == (0, 1)


def test_focus_or_mark_seen_clears_unread():
    t = AttentionTracker()
    t.update([row("a", 100.0)], now=100.0)
    t.update([row("a", 110.0)], now=120.0)
    assert t.state("a", 120.0) == "unread"
    t.update([row("a", 110.0)], now=121.0, focused=["A"])
    assert t.state("a", 121.0) == "idle"
    t.update([row("a", 130.0)], now=140.0)
    assert t.state("a", 140.0) == "unread"
    t.mark_seen("a", 141.0)
    assert t.state("a", 141.0) == "idle"


def test_exited_and_other_workspace():
    t = AttentionTracker()
    t.update([row("a", 1.0, running=False), row("b", 1.0, ws="OTHER")], now=5.0)
    assert t.state("a", 5.0) == "exited"
    assert t.summary(W, 5.0) == (0, 0)
    assert [x["id"] for x in t.terminals("OTHER", 5.0)] == ["B"]
