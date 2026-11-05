"""Sprint 4 GTK: asynchronous initial load ("Conectando..."), real VTE terminals (core /ws/pty when connected, local shell
offline) and live preferences (terminal font/theme, canvas background, autosave interval)."""
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

from maestri_canvas.gi_env import vte_available  # noqa: E402

if not vte_available():
    pytest.skip("Vte 3.91 (GTK 4) typelib not available", allow_module_level=True)

gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

from maestri_canvas.scene import norm  # noqa: E402
from maestri_canvas.terminal_node import VteTerminalView  # noqa: E402
from maestri_canvas.widget import is_terminal  # noqa: E402
from maestri_core.client.api import MaestriClient  # noqa: E402
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent  # noqa: E402
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload  # noqa: E402
from maestri_core.preferences import Preferences, PreferencesStore  # noqa: E402
from ui.window import OpenMaestriWindow  # noqa: E402


def pump(seconds):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.004)


@pytest.fixture(scope="module")
def app():
    a = Gtk.Application(application_id="org.openmaestri.e2e.termprefs", flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """PreferencesStore and notes_dir resolve ~ at runtime: never touch the real ~/.open-maestri."""
    monkeypatch.setenv("HOME", str(tmp_path))
    PreferencesStore().save(Preferences(terminal_font_family="DejaVu Sans Mono", terminal_font_size=15.0,
                                        terminal_theme="nord", canvas_background="solid", autosave_interval_seconds=30))
    return tmp_path


class FakeCoreClient(MaestriClient):
    """A reachable core, minus the network: the workspace load is gated so the test can observe the placeholder."""

    def __init__(self, doc):
        super().__init__(port=1, auth_token="t")   # port 1: the /ws/pty socket fails fast (connection refused)
        self.doc = doc
        self.release = threading.Event()
        self.ensured, self.deleted, self.saved = [], [], []

    def check_connection_status(self):
        self.release.wait(5)
        return "connected"

    def get_workspace(self, workspace_id=None):
        return self.doc

    def ensure_terminal(self, terminal_id, **kw):
        self.ensured.append((terminal_id, kw))
        return True

    def delete_terminal(self, terminal_id):
        self.deleted.append(terminal_id)
        return True

    def save_workspace_dict(self, data, workspace_id=None):
        self.saved.append(workspace_id)
        return True


def _doc_with_terminal():
    node = CanvasNode(frame=NodeFrame(x=9900, y=8600, width=420, height=300), node_type="terminal",
                      content=TerminalContent(name="Agent", command="bash", working_directory="/tmp"), z_index=1)
    return WorkspaceDocument(payload=WorkspacePayload(name="Remote", working_directory="/tmp", nodes=[node])), node


def _font_of(term):
    fd = term.get_font()
    return fd.get_family(), fd.get_size() / Pango.SCALE


@pytest.mark.e2e
@pytest.mark.linux
@pytest.mark.requires_gtk
class TestInitialLoadAndTerminals:
    def test_placeholder_then_core_backed_terminal(self, app):
        doc, node = _doc_with_terminal()
        client = FakeCoreClient(doc)
        win = OpenMaestriWindow(app, client)
        try:
            win.present()
            pump(0.2)
            # The window is up while the core is still being asked: placeholder, nothing on the canvas, no sync yet.
            assert not win.loaded and win.loading_label.get_visible()
            assert win.loading_label.get_label() == "Conectando..."
            assert "Conectando" in win.lbl_status.get_label()
            assert win.canvas_widget.scene.nodes == {} and win.sync is None
            win._create_node("terminal")   # ignored until loaded (the placeholder document is about to be replaced)
            assert win.document.payload.nodes == []

            client.release.set()
            assert win.wait_loaded()
            assert not win.loading_label.get_visible()
            assert win.core_connected and win.sync is not None
            assert win.document is doc and "Porta" in win.lbl_status.get_label()

            content = win.canvas_widget.frames[norm(node.id)].content
            assert isinstance(content, VteTerminalView), "connected: the terminal streams the core PTY"
            tid = node.content.terminal_id.upper() or node.id.upper()
            assert content.transport.url.endswith(f"/ws/pty/{tid}")
            pump(0.3)
            assert client.ensured and client.ensured[0][0] == tid
            assert client.ensured[0][1]["working_directory"] == "/tmp"
            assert _font_of(content.term) == ("DejaVu Sans Mono", 15.0)   # preferences, not the old hard-coded 11pt

            win._on_canvas_delete_requested(win.canvas_widget, (node.id,))
            pump(0.2)
            assert content.disposed and client.deleted == [tid]   # deleting the node ends the core shell
        finally:
            win.close()
            pump(0.05)

    def test_offline_uses_local_vte_shell(self, app):
        win = OpenMaestriWindow(app, MaestriClient(port=0))
        try:
            win.present()
            assert win.wait_loaded()
            assert not win.core_connected and "Standalone" in win.lbl_status.get_label()
            terms = [n for n in win.document.payload.nodes if n.node_type == "terminal"]
            assert terms, "offline demo workspace has terminals"
            for n in terms:
                content = win.canvas_widget.frames[norm(n.id)].content
                assert is_terminal(content), f"expected a local Vte.Terminal, got {type(content).__name__}"
                assert _font_of(content) == ("DejaVu Sans Mono", 15.0)
            assert win.canvas_widget.renderer.background == "solid"
        finally:
            win.close()
            pump(0.05)

    def test_preferences_apply_live(self, app):
        doc, node = _doc_with_terminal()
        client = FakeCoreClient(doc)
        client.release.set()
        win = OpenMaestriWindow(app, client)
        try:
            win.present()
            assert win.wait_loaded()
            term = win.canvas_widget.frames[norm(node.id)].content.term
            old_autosave = win._autosave_id
            assert old_autosave

            prefs = Preferences(terminal_font_family="Monospace", terminal_font_size=20.0, terminal_theme="light",
                                canvas_background="transparent", autosave_interval_seconds=5)
            win._on_preferences_changed(prefs)
            assert _font_of(term) == ("Monospace", 20.0)
            assert win.canvas_widget.renderer.background == "transparent"
            assert win._autosave_id and win._autosave_id != old_autosave, "autosave timer restarted with the new interval"

            # A per-node override (TerminalContent.fontSize) beats the preference.
            node.content.font_size = 9.0
            win._style_terminal(node)
            assert _font_of(term) == ("Monospace", 9.0)
        finally:
            win.close()
            pump(0.05)
