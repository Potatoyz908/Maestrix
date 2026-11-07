"""PortalView on real GTK 4 + WebKit 6.0 (local pages only: no network), and the canvas connection rules."""
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

from maestri_canvas.gi_env import webkit_available  # noqa: E402

if not webkit_available():
    pytest.skip("WebKit 6.0 typelib not available", allow_module_level=True)

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.portal_model import PortalMeta  # noqa: E402
from maestri_canvas.portal_node import PortalView  # noqa: E402
from maestri_canvas.scene import ConnItem, NodeItem  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402

ID = {k: f"{k * 8}-0000-0000-0000-00000000000{i}" for i, k in enumerate("ABCD", 1)}


def pump_until(cond, timeout=10.0, what="condition"):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return
        time.sleep(0.004)
    raise AssertionError(f"timed out waiting for {what}")


def load(view, html, base="file:///tmp/"):
    done = []
    h = view.web.connect("load-changed", lambda w, ev: done.append(ev.value_nick) if ev.value_nick == "finished" else None)
    view.web.load_html(html, base)
    pump_until(lambda: done, what="page load")
    view.web.disconnect(h)


def call(fn, *a):
    out = []
    fn(*a, lambda r, e: out.append((r, e)))
    pump_until(lambda: out, what="async result")
    return out[0]


def win(child):
    w = Gtk.Window(child=child, default_width=700, default_height=500)
    w.present()
    pump_until(lambda: child.get_width() > 0, what="realize")
    return w


def meta(**kw):
    return PortalMeta(portal_id=ID["A"], **kw)


def test_automation_on_local_page():
    v = PortalView(meta(), autoload=False)
    win(v)
    load(v, "<title>Hi</title><body><button>Go</button><input placeholder='name'><p>hello text</p>")
    text, err = call(v.text)
    assert err is None and "hello text" in text
    assert "<button>Go</button>" in call(v.html)[0]
    snap = call(v.snapshot)[0]
    assert "@e1 [button] Go" in snap and "@e2 [input] " in snap
    assert call(v.evaluate, "1+2")[0] == "3"
    r, err = call(v.evaluate, "throw new Error('boom')")
    assert r is None and "boom" in err
    assert v.info()["title"] == "Hi"
    v.dispose()


def test_screenshot_png(tmp_path):
    v = PortalView(meta(), autoload=False)
    win(v)
    load(v, "<body style='background:#08f'>x")
    p = tmp_path / "s.png"
    assert call(v.screenshot, str(p)) == (str(p), None)
    assert p.read_bytes()[:4] == b"\x89PNG"
    v.dispose()


def test_url_bar_refuses_scripts_and_data():
    v = PortalView(meta(), autoload=False)
    win(v)
    load(v, "<body>stay</body>")
    for bad in ("javascript:document.body.innerText='pwned'", "data:text/html,<b>x</b>", "ftp://x.y", ""):
        assert v.navigate(bad) is False
    pump_until(lambda: True)
    assert "stay" in call(v.text)[0]
    v.dispose()


def test_current_url_saved_only_on_finished_and_meta_only_on_change():
    emitted = []
    v = PortalView(meta(source={}), autoload=False)
    v.connect("meta-changed", lambda _v, d: emitted.append(d))
    win(v)
    load(v, "<body>a</body>", base="about:blank")        # about: is never persisted as currentURL
    assert v.meta.current_url == ""
    # pretend a redirect hop: uri changes while loading must not persist
    from gi.repository import WebKit
    v._on_load_changed(v.web, WebKit.LoadEvent.REDIRECTED)
    assert v.meta.current_url == ""
    n = len(emitted)
    v.set_chrome_hidden(True)
    v.set_chrome_hidden(True)    # same value: no second emit
    assert len(emitted) == n + 1 and emitted[-1]["chromeHidden"] is True
    assert emitted[-1]["source"] == {}      # source preserved verbatim, never auto-filled
    v.dispose()


def test_file_page_persists_url_after_finish(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<title>P</title>ok")
    emitted = []
    v = PortalView(meta(extra={"keep": 1}), autoload=False)
    v.connect("meta-changed", lambda _v, d: emitted.append(d))
    win(v)
    v.navigate(page.as_uri())
    pump_until(lambda: v.meta.current_url == page.as_uri() and v.meta.status == "idle", what="finish")
    d = v.to_content()
    assert d["currentURL"] == page.as_uri() and d["keep"] == 1 and d["status"] == "idle"
    assert emitted and emitted[-1]["currentURL"] == page.as_uri()
    v.dispose()


def test_bind_does_not_echo():
    emitted = []
    v = PortalView(meta(), autoload=False)
    v.connect("meta-changed", lambda _v, d: emitted.append(d))
    win(v)
    v.bind({**v.to_content(), "name": "Renamed", "chromeHidden": True})
    assert emitted == [] and v.meta.chrome_hidden and not v.bar.get_visible()
    v.dispose()


def test_isolated_sessions_differ_and_use_session_swaps_and_reloads(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<title>P</title>ok")
    a = PortalView(meta(), autoload=False)
    b = PortalView(meta(), autoload=False)
    assert a.session is not b.session
    box = Gtk.Box(); box.append(a); box.append(b)
    win(box)
    a.navigate(page.as_uri())
    pump_until(lambda: a.meta.current_url == page.as_uri(), what="a loaded")
    old_web = a.web
    assert a.use_session(b.session) is True and a.session is b.session and a.web is not old_web
    assert a.use_session(b.session) is False
    pump_until(lambda: a.web.get_uri() == page.as_uri(), what="reload on new session")
    a.dispose(); b.dispose()


def _canvas():
    c = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    kinds = {"A": "portal", "B": "portal", "C": "terminal", "D": "stickyNote"}
    for i, (k, kind) in enumerate(kinds.items()):
        c.add_node(NodeItem(ID[k], Rect(40 + i * 330, 60, 300, 240), i + 1, kind, kind), 
                   Gtk.Label(label=kind) if kind == "terminal" else None)
    win(c)
    return c


def test_connected_portals_share_one_session_and_split_on_disconnect():
    c = _canvas()
    va, vb = c.frames[ID["A"]].content, c.frames[ID["B"]].content
    assert va.session is not vb.session
    c.add_connection(ConnItem("local-1", ID["A"], ID["B"]))
    assert va.session is vb.session
    c.remove_connection("local-1")
    assert va.session is not vb.session
    # deleting a node also splits
    c.add_connection(ConnItem("local-2", ID["A"], ID["B"]))
    assert va.session is vb.session
    c.remove_node(ID["B"])
    assert "local-2" not in c.scene.connections and ID["B"] not in c.frames


def test_optimistic_rope_lifecycle():
    c = _canvas()
    c.add_connection(ConnItem("local-x", ID["C"], ID["A"], pending=True))
    assert "local-x" in c.controller.ropes
    assert c.replace_connection_id("local-x", "REAL-1")
    assert "REAL-1" in c.scene.connections and not c.scene.connections["REAL-1"].pending
    assert "REAL-1" in c.controller.ropes and "local-x" not in c.controller.ropes
    c.add_connection(ConnItem("local-y", ID["C"], ID["B"], pending=True))
    assert c.remove_connection("local-y") and "local-y" not in c.scene.connections and "local-y" not in c.controller.ropes
    assert c.remove_connection("local-y") is False


def test_refused_pairs_give_feedback_and_valid_pass():
    from maestri_canvas.interaction import ConnectRequested
    c = _canvas()
    refused, accepted = [], []
    c.connect("connection-refused", lambda _c, a, b, why: refused.append(why))
    c.connect("connect-requested", lambda _c, a, sa, b, sb: accepted.append((a, b)))
    c._on_event(ConnectRequested(ID["D"], None, ID["A"], None))     # note <-> portal
    assert refused and "Note" in refused[0] and not accepted
    assert "maestri-refused" in c.frames[ID["A"]].get_css_classes()
    pump_until(lambda: "maestri-refused" not in c.frames[ID["A"]].get_css_classes(), what="flash ends", timeout=4)
    for a, b in ((ID["C"], ID["A"]), (ID["A"], ID["B"])):                # terminal-portal, portal-portal
        c._on_event(ConnectRequested(a, None, b, None))
    assert len(accepted) == 2


def test_webkit_missing_gives_placeholder(monkeypatch):
    import maestri_canvas.widget as w
    monkeypatch.setattr(w, "webkit_available", lambda: False)
    c = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    c.add_node(NodeItem(ID["A"], Rect(0, 0, 300, 200), 1, "portal", "P"))
    assert isinstance(c.frames[ID["A"]].content, Gtk.Label)
