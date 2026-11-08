"""Pure parsing/monitor tests for maestri_core.agent_usage (no CLI, no display)."""
import threading
import time

from maestri_core import agent_usage as u


def test_parse_claude_windows_and_plan():
    r = u.parse_claude({"subscription_type": "team", "rate_limits_available": True, "rate_limits": {
        "five_hour": {"utilization": 12, "resets_at": "2026-11-08T22:40:00+00:00"},
        "seven_day": {"utilization": 23, "resets_at": "2026-11-14T09:00:00+00:00"}, "seven_day_opus": None}})
    assert r.plan == "Team" and [w.label for w in r.windows] == ["Session (5h)", "Weekly"]
    assert r.peak == 23 and r.windows[0].resets_at


def test_parse_codex_primary_secondary():
    b = {"planType": "plus", "primary": {"usedPercent": 4, "windowDurationMins": 300, "resetsAt": 1794189368},
         "secondary": {"usedPercent": 97, "windowDurationMins": 10080, "resetsAt": 1794420656}}
    r = u.parse_codex({"rateLimits": b, "rateLimitsByLimitId": {"codex": b}})
    assert r.plan == "Plus" and [(w.label, w.used) for w in r.windows] == [("Session (5h)", 4.0), ("Weekly", 97.0)]


def test_parse_antigravity_remaining_becomes_used():
    r = u.parse_antigravity({"groups": [{"name": "Gemini Models", "buckets": [
        {"id": "gemini-weekly", "name": "Weekly Limit Remaining", "window": "weekly", "remaining_fraction": 0.8135,
         "reset_time": "2026-11-14T00:41:01Z"},
        {"id": "gemini-5h", "name": "Five Hour Limit Remaining", "remaining_fraction": 0.99}]}]})
    assert [w.label for w in r.windows] == ["Gemini Models · Weekly", "Gemini Models · Session (5h)"]
    assert abs(r.windows[0].used - 18.65) < 0.01


def test_unknown_values_are_none_not_zero():
    assert u.parse_codex({"rateLimits": {"primary": {"usedPercent": "n/a"}}}).windows == []
    assert u.parse_claude({"rate_limits_available": False}).error


def test_format_reset():
    assert u.format_reset(None) == "" and u.format_reset(100, now=200) == "resetting"
    assert u.format_reset(200 + 3 * 3600 + 120, now=200) == "resets in 3h 2m"


def test_monitor_keeps_last_good_reading_on_failure():
    calls = []
    done = threading.Event()

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            return u.ProviderUsage("claude", "Claude Code", windows=[u.UsageWindow("Weekly", 10.0)])
        raise RuntimeError("offline")

    m = u.UsageMonitor({"claude": fetch}, on_update=lambda: len(calls) >= 1 and done.set(), intervals={"claude": 60})
    m.start()
    assert done.wait(5)
    m._poll("claude", fetch)   # the second (failing) poll
    m.stop()
    snap = m.snapshot()["claude"]
    assert snap.windows and snap.error == "offline"
