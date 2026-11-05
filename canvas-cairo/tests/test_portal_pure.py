"""Portal model, URL rules, and schema-v2 connection rules — no GTK."""
import itertools
import types

import pytest

from maestri_canvas.connection_rules import LIST_ATTRS, plan_connection, purge_node
from maestri_canvas.portal_model import PortalMeta, normalize_url

PID = "11111111-AAAA-BBBB-CCCC-000000000001"


def test_roundtrip_source_url_and_empty():
    d = {"id": PID, "name": "Docs", "currentURL": "https://a.b/x", "source": {"url": {"_0": "https://a.b"}}, "status": "idle",
         "chromeHidden": True, "storageScope": "shared", "futureKey": {"k": [1]}}
    assert PortalMeta.from_dict(d).to_dict() == d
    e = {"id": PID, "name": "Blank", "currentURL": "", "source": {}, "status": "idle", "chromeHidden": False, "storageScope": "isolated"}
    assert PortalMeta.from_dict(e).to_dict() == e   # empty source stays {}


def test_source_derived_only_when_absent_and_never_rewritten():
    m = PortalMeta.from_dict({"id": PID, "currentURL": "https://x.y"})
    assert m.to_dict()["source"] == {"url": {"_0": "https://x.y"}}
    m2 = PortalMeta.from_dict({"id": PID, "currentURL": "https://moved", "source": {"url": {"_0": "https://orig"}}})
    assert m2.to_dict()["source"] == {"url": {"_0": "https://orig"}}


def test_scope_garbage_falls_back_and_initial_url():
    assert PortalMeta.from_dict({"storageScope": "weird"}).storage_scope == "isolated"
    assert PortalMeta.from_dict({"source": {"url": {"_0": "https://s"}}}).initial_url == "https://s"
    assert PortalMeta.from_dict({"currentURL": "https://c", "source": {"url": {"_0": "https://s"}}}).initial_url == "https://c"


@pytest.mark.parametrize("text,want", [
    ("example.com", "https://example.com"), ("localhost:3000", "http://localhost:3000"), ("10.0.0.1:8080/x", "http://10.0.0.1:8080/x"),
    ("https://a.b/c", "https://a.b/c"), ("file:///tmp/a.html", "file:///tmp/a.html"), ("about:blank", "about:blank"),
    ("javascript:alert(1)", ""), ("JavaScript:alert(1)", ""), ("data:text/html,<script>1</script>", ""), ("ftp://x.y", ""), ("   ", ""),
])
def test_normalize_url(text, want):
    assert normalize_url(text) == want


def test_search_fallback():
    assert normalize_url("hello world").startswith("https://duckduckgo.com/?q=hello+world")


T, N, P, F, S = "terminal", "stickyNote", "portal", "fileTree", "shape"
VALID = {(T, T): ("terminal", "connections"), (T, N): ("terminalToNote", "note_connections"),
         (T, P): ("terminalToPortal", "portal_connections"), (P, P): ("portalToPortal", "portal_to_portal_connections"),
         (N, N): ("noteToNote", "note_to_note_connections")}


@pytest.mark.parametrize("ta,tb", list(itertools.product([T, N, P], repeat=2)))
def test_all_nine_pairs(ta, tb):
    plan = plan_connection("aa", ta, "L", "bb", tb, "R")
    key = (ta, tb) if (ta, tb) in VALID else (tb, ta)
    if key not in VALID:   # note<->portal either order
        assert isinstance(plan, str) and "Note" in plan and "Portal" in plan
        return
    assert (plan.kind, plan.list_attr) == VALID[key]
    assert plan.a == "AA" or plan.a == "BB"   # ids uppercased
    if T in (ta, tb) and ta != tb:   # terminal is always A, sides travel with their node
        if ta == T:
            assert (plan.a, plan.side_a, plan.b, plan.side_b) == ("AA", "L", "BB", "R")
        else:
            assert (plan.a, plan.side_a, plan.b, plan.side_b) == ("BB", "R", "AA", "L")


@pytest.mark.parametrize("other", [T, N, P, F, S])
def test_filetree_and_shapes_refused(other):
    for x, y in ((F, other), (other, F)):
        assert isinstance(plan_connection("a", x, None, "b", y, None), str)
    if other == S:
        assert isinstance(plan_connection("a", S, None, "b", T, None), str)


def test_purge_node_all_six_lists():
    C = lambda a, b: types.SimpleNamespace(a_id=a, b_id=b)
    X = "DDDDDDDD-0000-0000-0000-000000000009"
    keep = C("E", "F")
    payload = types.SimpleNamespace(**{a: [C(X.lower(), "1"), C("2", X), keep] for a in LIST_ATTRS if a != "cross_floor_connections"},
                                    cross_floor_connections=[{"nodeIdA": X, "nodeIdB": "9"}, {"nodeIdA": "8", "nodeIdB": X.lower()},
                                                             {"nodeIdA": "8", "nodeIdB": "9"}])
    assert len(LIST_ATTRS) == 6
    assert purge_node(payload, X) == 5 * 2 + 2
    for a in LIST_ATTRS:
        left = getattr(payload, a)
        assert len(left) == 1
    assert payload.note_connections == [keep]
