"""
E2E interaction tests under Xvfb (Sprint 4): real PortalView (WebKit 6.0), real Vte terminal,
FileTree -> terminal drag-and-drop, canvas context menu and 401 auth failure.

Every asynchronous expectation uses `wait_until` (condition polling with a deadline) instead of fixed sleeps,
so the suite is not timing-sensitive.
"""

import http.server
import sys
import threading
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("Requires an active display (X11/Wayland/Xvfb)", allow_module_level=True)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "canvas-cairo"))
sys.path.insert(0, str(ROOT_DIR / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT_DIR))

from maestri_canvas import gi_env  # noqa: E402

HAS_VTE = gi_env.vte_available()
HAS_WEBKIT = gi_env.webkit_available()

from maestri_canvas.demo import screenshot  # noqa: E402
from maestri_canvas.file_dnd import dropped_text, file_content_provider, shell_quote_paths  # noqa: E402
from maestri_canvas.file_tree_node import FileTreeView  # noqa: E402
from maestri_canvas.geometry import Point  # noqa: E402
from maestri_canvas.scene import norm  # noqa: E402
from maestri_core.client.api import AuthenticationError, MaestriClient  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def wait_until(cond, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """Pump the GLib main loop until `cond()` is truthy or `timeout` elapses."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(interval)
    return bool(cond())


def pump(seconds: float = 0.2) -> None:
    wait_until(lambda: False, timeout=seconds)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Isolate HOME so PreferencesStore and notes_dir never touch the real ~/.open-maestri."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".zshrc").touch()
    return tmp_path


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.interactions", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture
def window(app):
    win = OpenMaestriWindow(app, MaestriClient(port=0))  # offline standalone
    win.present()
    assert win.wait_loaded(), "initial workspace load did not finish"
    pump(0.2)
    yield win
    win.close()
    pump(0.05)


@pytest.fixture
def artifacts_dir() -> Path:
    d = ROOT_DIR / "artifacts" / "e2e-screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _last_content(window, kind: str):
    node = [n for n in window.document.payload.nodes if n.node_type == kind][-1]
    frame = window.canvas_widget.frames.get(norm(node.id)) or window.canvas_widget.frames[node.id]
    return node, frame.content


def _term_text(term) -> str:
    from gi.repository import Vte
    cols = term.get_column_count()
    text, _ = term.get_text_range_format(Vte.Format.TEXT, 0, 0, 10_000_000, cols - 1)
    return text or ""


@pytest.fixture(scope="module")
def http_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><head><title>MaestriE2E</title></head><body><h1>hello portal</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def unauthorized_server():
    """Backend that answers every request with 401 (invalid token)."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def _deny(self):
            body = b'{"error":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = _deny

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestPortalE2E:
    @pytest.mark.skipif(not HAS_WEBKIT, reason="WebKit 6.0 typelib not available")
    def test_portal_loads_local_page(self, window, http_server, artifacts_dir):
        from maestri_canvas.portal_node import PortalView

        window._create_node("portal")
        node, view = _last_content(window, "portal")
        assert isinstance(view, PortalView)
        url = f"http://127.0.0.1:{http_server.server_address[1]}/"
        assert view.navigate(url)
        assert wait_until(
            lambda: view.web.get_title() == "MaestriE2E" and view.meta.status == "idle",
            timeout=20,
        ), "page never finished loading"
        assert view.web.get_uri().startswith(url)
        assert node.content.current_url.startswith(url)

        # The DOM is really rendered: read it back through JavaScript (cb signature: (result_str, err_str)).
        result = {}
        view.text(lambda text, err: result.update(text=text, err=err))
        assert wait_until(lambda: "text" in result, timeout=10)
        assert result["err"] is None
        assert "hello portal" in (result["text"] or "")

        # Chrome visibility toggle updates view and persisted metadata.
        view.set_chrome_hidden(True)
        assert not view.bar.get_visible() and node.content.chrome_hidden is True
        view.set_chrome_hidden(False)
        assert view.bar.get_visible() and node.content.chrome_hidden is False

        shot = artifacts_dir / "06_portal_real_widget.png"
        screenshot(window.canvas_widget, str(shot))
        assert shot.stat().st_size > 1000


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
@pytest.mark.skipif(not HAS_VTE, reason="Vte 3.91 typelib not available")
class TestTerminalE2E:
    def test_real_vte_runs_shell_command(self, window, artifacts_dir):
        from gi.repository import Vte

        window._create_node("terminal")
        _node, term = _last_content(window, "terminal")
        assert isinstance(term, Vte.Terminal)
        assert wait_until(lambda: getattr(term, "maestri_pid", -1) > 0, timeout=10), "shell never spawned"

        term.feed_child(b"echo MAESTRI_$((40+2))_OK\n")
        assert wait_until(lambda: "MAESTRI_42_OK" in _term_text(term), timeout=15), _term_text(term)

        shot = artifacts_dir / "07_terminal_real_vte.png"
        screenshot(window.canvas_widget, str(shot))
        assert shot.stat().st_size > 1000

    def test_filetree_drop_pastes_quoted_paths_into_terminal(self, window, tmp_path, artifacts_dir):
        spaced = tmp_path / "my file.txt"
        spaced.write_text("x")

        window._create_node("terminal")
        _n, term = _last_content(window, "terminal")
        window._create_node("fileTree")
        _n, tree = _last_content(window, "fileTree")
        assert isinstance(tree, FileTreeView)
        assert wait_until(lambda: getattr(term, "maestri_pid", -1) > 0, timeout=10)

        tree.set_root(str(tmp_path))
        assert wait_until(lambda: any(p.endswith("my file.txt") for _d, p in tree.rows()), timeout=10), tree.rows()

        # Source side: the row's drag payload for the file.
        rel = next(p for _d, p in tree.rows() if p.endswith("my file.txt"))
        row = tree.row_for(rel)
        assert row is not None
        gfile = Gio.File.new_for_path(str(spaced))
        provider = file_content_provider([gfile])
        assert provider is not None

        # Wait until the shell prompt is ready so readline echoes the pasted path.
        assert wait_until(lambda: bool(_term_text(term).strip()), timeout=10)
        value = Gdk.FileList.new_from_list([gfile])
        assert dropped_text(value) == shell_quote_paths([gfile])
        assert term.drop_target.emit("drop", value, 5.0, 5.0) in (True, False)
        expected = "'%s'" % spaced
        assert wait_until(lambda: expected in _term_text(term), timeout=15), _term_text(term)
        # Pasted without pressing Enter: the shell must not have executed it.
        assert "command not found" not in _term_text(term)
        assert "No such file" not in _term_text(term)


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestCanvasContextMenuE2E:
    def test_right_click_on_background_opens_menu_and_creates_node_at_point(self, window):
        cw = window.canvas_widget
        x, y = 60.0, 60.0
        before = len(window.document.payload.nodes)
        # An empty spot: shift the click if a node happens to be under it.
        from maestri_canvas.interaction import Background
        while not isinstance(cw.target_at(x, y), Background):
            x += 40.0
            assert x < cw.get_width(), "no empty background found"

        window._on_canvas_right_click(Gtk.GestureClick(), 1, x, y)
        assert wait_until(window._ctx_popover.get_visible, timeout=5), "context menu did not open"
        expected = cw.viewport.to_canvas(Point(x, y))
        assert (window._ctx_point.x, window._ctx_point.y) == (expected.x, expected.y)

        # Activate "New Note here" through the real action group on the canvas.
        assert cw.activate_action("ctx.new-stickyNote", None)
        assert wait_until(lambda: len(window.document.payload.nodes) == before + 1, timeout=5)
        node = window.document.payload.nodes[-1]
        assert node.node_type == "stickyNote"
        assert node.frame.x == pytest.approx(expected.x)
        assert node.frame.y == pytest.approx(expected.y)
        window._ctx_popover.popdown()

    def test_right_click_on_node_does_not_open_menu(self, window):
        cw = window.canvas_widget
        node = window.document.payload.nodes[0]
        frame = cw.viewport.to_screen(Point(node.frame.x + 20, node.frame.y + 20))
        window._on_canvas_right_click(Gtk.GestureClick(), 1, frame.x, frame.y)
        pump(0.1)
        assert not window._ctx_popover.get_visible()


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestAuthFailureE2E:
    def test_401_shows_invalid_token_and_disables_sync(self, app, unauthorized_server):
        port = unauthorized_server.server_address[1]
        client = MaestriClient(port=port, auth_token="wrong-token")
        assert client.check_connection_status() == "unauthorized"

        win = OpenMaestriWindow(app, client)
        try:
            win.present()
            assert win.wait_loaded(), "initial load did not finish on 401"
            assert win.auth_failed is True
            assert win.sync is None, "sync must stay disabled with an invalid token"
            assert "Token Inválido" in win.lbl_status.get_label()
            assert win.document.payload.name == "Não Conectado"
            assert win.document.payload.nodes == [], "no demo workspace on auth failure"
        finally:
            win.close()
            pump(0.05)

    def test_401_on_get_workspace_sets_auth_failed(self, app, monkeypatch):
        """Even if check_connection_status returned 'connected', a 401 from get_workspace() marks auth_failed."""
        client = MaestriClient(port=1, auth_token="expired-token")
        monkeypatch.setattr(client, "check_connection_status", lambda: "connected")

        def raise_401(*_a, **_kw):
            raise AuthenticationError("401 Unauthorized")

        monkeypatch.setattr(client, "get_workspace", raise_401)
        win = OpenMaestriWindow(app, client)
        try:
            win.present()
            assert win.wait_loaded()
            assert win.auth_failed is True
            assert win.sync is None
            assert "Token Inválido" in win.lbl_status.get_label()
            assert win.document.payload.nodes == []
        finally:
            win.close()
            pump(0.05)
