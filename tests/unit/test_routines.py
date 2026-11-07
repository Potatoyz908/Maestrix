"""Routines: `&&` parser, Swift-compatible routines.json round-trip, fake-clock scheduler, REST client."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from maestri_core.routines import (MIN_INTERVAL_SECONDS, Routine, RoutineScheduler, RoutinesClient, describe_interval,
                                   dump_container, join_prompts, load_container, parse_prompts)

TID = "6B1D7C4A-0000-4000-8000-000000000001"


# ---- parser -------------------------------------------------------------------------------------------------------
def test_own_line_separator_chains_prompts():
    text = "pull the latest changes\n&&\nrun the test suite\n  &&  \r\nsummarize the results"
    assert parse_prompts(text) == ["pull the latest changes", "run the test suite", "summarize the results"]


def test_inline_ampersands_are_part_of_the_prompt():
    assert parse_prompts("run make && make test") == ["run make && make test"]
    assert parse_prompts("first line\nsecond && third") == ["first line\nsecond && third"]


def test_empty_segments_dropped_and_join_roundtrip():
    assert parse_prompts("&&\n\n&&\nonly\n&&") == ["only"]
    assert parse_prompts("   ") == []
    prompts = ["a", "b && c", "multi\nline"]
    assert parse_prompts(join_prompts(prompts)) == prompts


# ---- model / persistence format -----------------------------------------------------------------------------------
def test_swift_container_roundtrip():
    swift_json = {
        "payload": [{
            "id": "0F0E0D0C-0B0A-4908-8706-050403020100", "name": "Tests", "prompts": ["run the tests", "summarize"],
            "intervalSeconds": 300, "targetTerminalId": TID.lower(), "isActive": False, "createdAt": "2026-11-05T12:00:00Z",
        }]
    }
    (r,) = load_container(swift_json)
    assert r.name == "Tests" and r.prompts == ["run the tests", "summarize"] and r.interval_seconds == 300.0
    assert r.target_terminal_id == TID and not r.is_active
    assert r.created_at == datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    out = dump_container([r])
    assert list(out) == ["payload"]                                    # Swift writes `payload`
    assert out["payload"][0] == {**swift_json["payload"][0], "targetTerminalId": TID, "intervalSeconds": 300.0}
    assert load_container({"routines": out["payload"]})[0].id == r.id  # legacy key still read
    assert load_container(json.loads(json.dumps(out)))[0] == r


def test_swift_fixture_file_if_present():
    p = Path(__file__).resolve().parents[2] / "Tests" / "OpenMaestriTests" / "Fixtures" / "routines.json"
    if p.exists():
        assert all(isinstance(r, Routine) for r in load_container(json.loads(p.read_text())))


def test_validation():
    r = Routine.create("n", "do it", 60, TID)
    r.validate()
    with pytest.raises(ValueError):
        Routine.create("n", "do it", MIN_INTERVAL_SECONDS - 1, TID).validate()
    with pytest.raises(ValueError):
        Routine.create("n", "&&", 120, TID).validate()
    with pytest.raises(ValueError):
        Routine.create(" ", "x", 120, TID).validate()
    with pytest.raises(ValueError):
        Routine.create("n", "x", 120, "not-a-uuid").validate()
    assert describe_interval(300) == "every 5m" and describe_interval(7200) == "every 2h" and describe_interval(90) == "every 90s"


# ---- scheduler ----------------------------------------------------------------------------------------------------
class Harness:
    def __init__(self):
        self.now = 0.0
        self.sent = []
        self.idle = True
        self.alive = {TID}
        self.events = []
        self.s = RoutineScheduler(self.send, lambda t: self.idle, lambda k, rid, info: self.events.append((k, info)),
                                  clock=lambda: self.now, grace=3.0, idle_timeout=300.0)

    def send(self, tid, text):
        if tid not in self.alive:
            return False
        self.sent.append((self.now, text))
        return True

    def advance(self, seconds, step=0.5):
        end = self.now + seconds
        while self.now < end:
            self.now = min(end, self.now + step)
            self.s.tick()

    def kinds(self):
        return [k for k, _ in self.events]


def test_runs_every_interval():
    h = Harness()
    h.s.set_routines([Routine.create("r", "ping", 60, TID)])
    h.advance(59)
    assert h.sent == []
    h.advance(1)
    assert [t for t, _ in h.sent] == [60.0]
    h.advance(120)
    assert [t for t, _ in h.sent] == [60.0, 120.0, 180.0]
    assert h.kinds().count("routineFinished") == 3


def test_interval_below_minimum_is_clamped():
    h = Harness()
    h.s.set_routines([Routine.create("r", "ping", 5, TID)])
    h.advance(59)
    assert h.sent == []
    h.advance(1)
    assert len(h.sent) == 1


def test_chain_waits_for_idle_and_grace():
    h = Harness()
    h.s.set_routines([Routine.create("r", "one\n&&\ntwo\n&&\nthree", 600, TID)])
    h.idle = False
    h.advance(600)
    assert [p for _, p in h.sent] == ["one"]
    h.advance(10)
    assert [p for _, p in h.sent] == ["one"]           # agent busy: wait
    h.idle = True
    h.advance(0.5)
    assert [p for _, p in h.sent] == ["one", "two"]
    h.advance(2.5)
    assert [p for _, p in h.sent] == ["one", "two"]    # grace period even when idle
    h.advance(0.5)
    assert [p for _, p in h.sent] == ["one", "two", "three"]
    assert ("routineStep", {"i": 3, "n": 3}) in h.events and h.kinds()[-1] == "routineFinished"


def test_idle_timeout_sends_anyway():
    h = Harness()
    h.s.set_routines([Routine.create("r", "one\n&&\ntwo", 600, TID)])
    h.idle = False
    h.advance(600)
    h.advance(302.5, step=1)
    assert [p for _, p in h.sent] == ["one"]
    h.advance(1, step=1)
    assert [p for _, p in h.sent] == ["one", "two"]


def test_no_overlap_skips_tick():
    h = Harness()
    r = Routine.create("r", "one\n&&\ntwo", 60, TID)
    h.s.set_routines([r])
    h.idle = False
    h.advance(60)                                   # run 1 starts, waits on the busy agent
    assert h.s.is_running(r.id)
    h.advance(60)                                   # next tick while still running
    assert ("routineSkipped", {"reason": "overlap"}) in h.events
    assert [p for _, p in h.sent] == ["one"]
    assert h.kinds().count("routineStarted") == 1


def test_missing_terminal_pauses_and_fails():
    h = Harness()
    r = Routine.create("r", "ping", 60, TID)
    h.s.set_routines([r])
    h.alive.clear()
    h.advance(60)
    assert h.sent == [] and not h.s.routines[r.id].is_active and not h.s.is_running(r.id)
    assert ("routineFailed", {"reason": "target terminal not found"}) in h.events
    h.advance(600)
    assert h.kinds().count("routineStarted") == 1   # paused: never fires again


def test_pause_resume_run_now_and_refresh_keeps_phase():
    h = Harness()
    r = Routine.create("r", "ping", 100, TID)
    h.s.set_routines([r])
    h.advance(50)
    h.s.set_routines([r])                          # REST refresh with no change: phase kept
    h.advance(50)
    assert len(h.sent) == 1
    h.s.pause(r.id)
    h.advance(300)
    assert len(h.sent) == 1
    h.s.resume(r.id)
    h.advance(99)
    assert len(h.sent) == 1
    h.advance(1)
    assert len(h.sent) == 2
    assert h.s.run_now(r.id) and len(h.sent) == 3
    h.s.remove(r.id)
    h.advance(1000)
    assert len(h.sent) == 3


def test_long_stall_catches_up_without_burst():
    h = Harness()
    h.s.set_routines([Routine.create("r", "ping", 60, TID)])
    h.now = 600.0                                   # e.g. system suspended
    h.s.tick()
    assert len(h.sent) == 1
    h.advance(59)
    assert len(h.sent) == 1
    h.advance(1)
    assert len(h.sent) == 2


# ---- REST client --------------------------------------------------------------------------------------------------
class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class FakeCore:
    """Mimics /api/routines as implemented in InterAgentServer.swift (PATCH only applies name/isActive)."""

    base_url = "http://core"
    timeout = 5

    def __init__(self):
        self.items = {}
        self.calls = []
        self.session = self

    def _headers(self, extra=None):
        return dict(extra or {})

    def _handle_response(self, r):
        return r

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url[len(self.base_url):]
        self.calls.append((method, path))
        if method == "GET":
            return FakeResp([r.to_dict() for r in self.items.values()])
        if method == "POST":
            r = Routine.create(json["name"], json["prompt"], json["intervalSeconds"], json["targetTerminalId"])
            self.items[r.id] = r
            return FakeResp(r.to_dict(), 201)
        rid = path.rsplit("/", 1)[1]
        if method == "PATCH":
            r = self.items[rid]
            if "isActive" in json:
                r.is_active = json["isActive"]
            if json.get("name"):
                r.name = json["name"]
            return FakeResp(r.to_dict())
        if method == "DELETE":
            self.items.pop(rid)
            return FakeResp({"status": "ok"})
        raise AssertionError(method)


def test_rest_client_crud_and_full_edit_fallback():
    core = FakeCore()
    rc = RoutinesClient(core)
    r = rc.create(Routine.create("tests", "run tests\n&&\nsummarize", 300, TID))
    assert r.prompts == ["run tests", "summarize"] and core.calls[-1] == ("POST", "/api/routines")
    assert [x.id for x in rc.list()] == [r.id]
    r = rc.set_active(r.id, False)
    assert not r.is_active
    edited = Routine.from_dict({**r.to_dict(), "prompts": ["only this"], "intervalSeconds": 600})
    got = rc.update(edited)                         # core ignores prompt/interval in PATCH -> re-created
    assert got.prompts == ["only this"] and got.interval_seconds == 600 and not got.is_active
    assert [x.prompts for x in rc.list()] == [["only this"]]
    rc.delete(got.id)
    assert rc.list() == []
