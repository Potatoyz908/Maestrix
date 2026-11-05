"""Native Web Portal node: a WebKitGTK 6.0 WebView with a browser chrome (back/forward/reload, URL bar, progress) for the canvas.

Needs the typelibs WebKit-6.0 + JavaScriptCore-6.0 (user dir, see gi_env). Everything async runs on the GLib main loop: no threads.
Storage: "isolated" = an ephemeral NetworkSession per portal (own cookies/storage, gone on dispose), "shared" = one persistent session
for all shared portals. Persisted state is a PortalMeta (schema in portal_model); `meta-changed` fires only when something persisted changes.
The automation surface mirrors `omaestri portal …` (navigate/back/forward/reload/evaluate/text/html/snapshot/screenshot/info); each call
takes a callback `cb(result, error)` so the caller (the core's signal bus) decides how to answer."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

from .gi_env import ensure_typelib_paths

ensure_typelib_paths()
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, GObject, Gtk, WebKit  # noqa: E402

from .portal_model import SNAPSHOT_JS, PortalMeta, display_host, normalize_url  # noqa: E402

Callback = Callable[[object, "str | None"], None]
_shared_session: "WebKit.NetworkSession | None" = None


def _session(scope: str) -> "WebKit.NetworkSession":
    global _shared_session
    if scope == "shared":
        if _shared_session is None:
            base = Path(GLib.get_user_data_dir()) / "open-maestri" / "portal-shared"
            _shared_session = WebKit.NetworkSession.new(str(base / "data"), str(base / "cache"))
        return _shared_session
    return WebKit.NetworkSession.new_ephemeral()


def _icon_button(icon: str, tip: str) -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tip)
    b.add_css_class("flat")
    return b


class PortalView(Gtk.Box):
    __gsignals__ = {
        "meta-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),      # PortalContent dict (persisted fields changed)
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "popup-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),       # target=_blank / window.open: the app decides (new portal?)
    }

    def __init__(self, meta: PortalMeta, autoload: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.meta = meta
        self._disposed = False
        self.add_css_class("maestri-portal")
        self.session = _session(meta.storage_scope)
        self.web = None

        self.back_btn = _icon_button("go-previous-symbolic", "Back")
        self.fwd_btn = _icon_button("go-next-symbolic", "Forward")
        self.reload_btn = _icon_button("view-refresh-symbolic", "Reload")
        self.chrome_btn = _icon_button("view-fullscreen-symbolic", "Hide/show address bar")
        self.back_btn.connect("clicked", lambda _b: self.go_back())
        self.fwd_btn.connect("clicked", lambda _b: self.go_forward())
        self.reload_btn.connect("clicked", lambda _b: self.stop() if self.web.is_loading() else self.reload())
        self.chrome_btn.connect("clicked", lambda _b: self.set_chrome_hidden(not self.meta.chrome_hidden))
        self.title_label = Gtk.Label(label=meta.name, xalign=0, hexpand=True, ellipsize=3)
        self.header_left = [self.back_btn, self.fwd_btn, self.reload_btn]
        self.header_title = self.title_label
        self.header_right = [self.chrome_btn]

        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Search or enter address", input_purpose=Gtk.InputPurpose.URL)
        self.entry.connect("activate", lambda e: self.navigate(e.get_text()))
        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class("osd")
        self.bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.bar.append(self.entry)
        self.bar.append(self.progress)
        self.append(self.bar)
        self._make_web()
        self.set_chrome_hidden(meta.chrome_hidden, emit=False)
        self._sync_nav()
        if autoload and meta.initial_url:
            self.web.load_uri(meta.initial_url)

    def _make_web(self) -> None:
        """(Re)build the WebView on self.session. A WebView's NetworkSession is construct-only, hence the rebuild on a session change."""
        self.web = WebKit.WebView(network_session=self.session, vexpand=True, hexpand=True)
        s = self.web.get_settings()
        s.set_enable_developer_extras(False)
        s.set_javascript_can_open_windows_automatically(False)
        self.append(self.web)
        self.web.connect("load-changed", self._on_load_changed)
        self.web.connect("notify::title", self._on_title)
        self.web.connect("notify::uri", self._on_uri)
        self.web.connect("notify::estimated-load-progress", self._on_progress)
        self.web.connect("create", self._on_create)
        self.web.connect("decide-policy", self._on_policy)

    def use_session(self, session: "WebKit.NetworkSession") -> bool:
        """Move this portal onto `session` (portal<->portal connection => the same session = shared cookies/storage; on disconnect a
        fresh ephemeral one). Reloads the current page. No-op (False) if it already uses it."""
        if session is self.session or self._disposed:
            return False
        url = self.meta.current_url or self.web.get_uri() or ""
        old = self.web
        old.stop_loading()
        self.remove(old)
        old.try_close()
        self.session = session
        self._make_web()
        self._sync_nav()
        if url:
            self.web.load_uri(url)
        return True

    # ---- header protocol -----------------------------------------------------------------------------
    def frame_attached(self, frame) -> None:
        pass

    def grab_focus(self) -> bool:   # type: ignore[override]
        return self.web.grab_focus()

    # ---- navigation -----------------------------------------------------------------------------------
    def navigate(self, text: str) -> bool:
        """URL-bar and API entry point (see normalize_url: http/https/file/about only; javascript:/data: refused). False if refused."""
        url = normalize_url(text)
        if not url:
            return False
        self.web.load_uri(url)
        return True

    def go_back(self) -> None:
        if self.web.can_go_back():
            self.web.go_back()

    def go_forward(self) -> None:
        if self.web.can_go_forward():
            self.web.go_forward()

    def reload(self) -> None:
        self.web.reload()

    def stop(self) -> None:
        self.web.stop_loading()

    def set_chrome_hidden(self, hidden: bool, emit: bool = True) -> None:
        self.bar.set_visible(not hidden)
        if self.meta.chrome_hidden != hidden:
            self.meta.chrome_hidden = hidden
            if emit:
                self._emit_meta()
        self.chrome_btn.set_icon_name("view-restore-symbolic" if hidden else "view-fullscreen-symbolic")

    # ---- WebKit callbacks -----------------------------------------------------------------------------
    def _sync_nav(self) -> None:
        self.back_btn.set_sensitive(self.web.can_go_back())
        self.fwd_btn.set_sensitive(self.web.can_go_forward())
        self.reload_btn.set_icon_name("process-stop-symbolic" if self.web.is_loading() else "view-refresh-symbolic")

    def _on_load_changed(self, _w, ev) -> None:
        loading = ev != WebKit.LoadEvent.FINISHED
        self.progress.set_visible(loading)
        status = "loading" if loading else "idle"
        changed = status != self.meta.status
        self.meta.status = status
        uri = self.web.get_uri() or ""
        if not loading and uri and not uri.startswith("about:") and uri != self.meta.current_url:
            self.meta.current_url = uri   # persisted only once the page finished: not on every redirect hop
            changed = True
        self._sync_nav()
        if changed:
            self._emit_meta()

    def _on_uri(self, *_a) -> None:
        uri = self.web.get_uri() or ""
        if uri and not self.entry.has_focus():
            self.entry.set_text(uri)   # the bar follows redirects; the persisted currentURL does not
        self._sync_nav()

    def _on_title(self, *_a) -> None:
        t = self.web.get_title() or ""
        shown = t or (display_host(self.meta.current_url) if self.meta.current_url else self.meta.name)
        self.title_label.set_label(shown)
        self.emit("title-changed", t)

    def _on_progress(self, *_a) -> None:
        self.progress.set_fraction(self.web.get_estimated_load_progress())

    def _on_create(self, _w, action):
        req = action.get_request()
        uri = req.get_uri() if req is not None else ""
        if uri:
            self.emit("popup-requested", uri)
        return None   # never create a second WebView: the app opens a portal if it wants one

    def _on_policy(self, _w, decision, dtype) -> bool:
        if dtype in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri() or ""
            scheme = uri.split(":", 1)[0].lower()
            if scheme not in ("http", "https", "file", "about") and uri:
                decision.ignore()   # no javascript:/custom-scheme navigations from page content
                return True
            if dtype == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
                decision.ignore()
                self.emit("popup-requested", uri)
                return True
        return False

    # ---- persistence ----------------------------------------------------------------------------------
    def _emit_meta(self) -> None:
        if not self._disposed:
            self.emit("meta-changed", self.to_content())

    def to_content(self) -> dict:
        return self.meta.to_dict()

    def bind(self, d: dict) -> None:
        """Apply external state (core -> view) without echoing meta-changed. Navigates only if the URL really differs."""
        new = PortalMeta.from_dict(d)
        self.meta.name = new.name
        self.meta.extra = new.extra
        self.set_chrome_hidden(new.chrome_hidden, emit=False)
        if new.current_url and new.current_url != self.meta.current_url:
            self.meta.current_url = new.current_url
            self.web.load_uri(new.current_url)

    # ---- automation (omaestri portal …) ---------------------------------------------------------------
    def evaluate(self, script: str, cb: Callback) -> None:
        def done(web, res):
            try:
                v = web.evaluate_javascript_finish(res)
                cb(None if v is None or v.is_undefined() or v.is_null() else v.to_string(), None)
            except GLib.Error as e:
                cb(None, e.message)
        self.web.evaluate_javascript(script, -1, None, None, None, done)

    def text(self, cb: Callback) -> None:
        self.evaluate("document.body ? document.body.innerText : ''", cb)

    def html(self, cb: Callback) -> None:
        self.evaluate("document.documentElement.outerHTML", cb)

    def snapshot(self, cb: Callback) -> None:
        self.evaluate(SNAPSHOT_JS, lambda r, e: cb(r or "", e))

    def info(self) -> dict:
        return {"title": self.web.get_title(), "url": self.web.get_uri(), "loading": self.web.is_loading(),
                "canGoBack": self.web.can_go_back(), "canGoForward": self.web.can_go_forward(), "scope": self.meta.storage_scope}

    def screenshot(self, path: str, cb: Callback) -> None:
        """Visible area -> PNG file; cb(path, None)."""
        def done(web, res):
            try:
                web.get_snapshot_finish(res).save_to_png(path)
                cb(path, None)
            except GLib.Error as e:
                cb(None, e.message)
        self.web.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, done)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.web.stop_loading()
        self.web.try_close()
