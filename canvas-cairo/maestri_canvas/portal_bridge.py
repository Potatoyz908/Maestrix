"""K3 executor: runs the server's portal commands on the GUI's WebKit portals (docs/linux-gtk-sprint9-spec.md §3 K3).

The server (`LinuxPortalManager`) publishes `portalCommand {requestId, workspaceId, portalId, op, args}` on /ws/events and waits
for `POST /api/portal-results {requestId, ok, result | error}`. `PortalBridge(canvas_widget, client).handle_event(evt)` finds the
portal node on the canvas, runs the operation on its `PortalView` and posts the answer from a worker thread.

Operations:
* `navigate {url}`, `back`, `forward`, `reload`, `info`, `screenshot` (PNG, base64), `snapshot` (accessibility-ish tree),
  `text`, `html`, `logs-start`, `logs`;
* anything carrying `args.js` (`evaluate` and the interaction commands the server already turns into JavaScript: click, fill,
  type, key, hover, scroll, drag, wait, select, check, focus…) runs as an async function, so a returned Promise is awaited;
* the interaction ops without `js` get a small built-in implementation from `args` (`selector`, `text`, `key`, `x`, `y`…).

Also: a link with `target=_blank` / `window.open` in a portal opens a new portal next to it (macOS behaviour), through the
window's `open_portal(url, near_node_id)` when it has one, else `_create_node("portal", ...)`.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
from typing import Any, Callable, Optional

from gi.repository import GLib

from .geometry import Point
from .scene import norm

MAX_LOG_LINES = 1000

# console capture: page script -> `maestriLog` script-message handler (WebKit UserContentManager)
LOG_HOOK_JS = r"""
(function () {
  if (window.__maestriConsoleHooked) return;
  window.__maestriConsoleHooked = true;
  const post = (level, args) => {
    try {
      const text = Array.from(args).map(a => { try { return typeof a === 'string' ? a : JSON.stringify(a); }
                                              catch (e) { return String(a); } }).join(' ');
      window.webkit.messageHandlers.maestriLog.postMessage(level + '\t' + text);
    } catch (e) {}
  };
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    const orig = console[level];
    console[level] = function () { post(level, arguments); return orig.apply(console, arguments); };
  }
  window.addEventListener('error', e => post('error', [e.message + ' (' + e.filename + ':' + e.lineno + ')']));
})();
"""


def _js_str(s: Any) -> str:
    return json.dumps("" if s is None else str(s))


def builtin_js(op: str, args: dict) -> Optional[str]:
    """JavaScript for an interaction op when the server did not send one (`args.js` absent). None = unknown op."""
    sel = _js_str(args.get("selector") or args.get("ref") or "")
    find = f"const el = document.querySelector({sel}); if (!el) throw new Error('element not found: ' + {sel});"
    text = _js_str(args.get("text") or args.get("value") or "")
    if op == "click":
        return f"{find} el.scrollIntoView({{block: 'center'}}); el.click(); return 'clicked';"
    if op == "focus":
        return f"{find} el.focus(); return 'focused';"
    if op == "hover":
        return f"{find} el.dispatchEvent(new MouseEvent('mouseover', {{bubbles: true}})); return 'hovered';"
    if op in ("fill", "type"):
        return (f"{find} el.focus(); el.value = {'' if op == 'fill' else 'el.value + '}{text}; "
                "el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); "
                "return 'ok';")
    if op == "select":
        return (f"{find} el.value = {text}; el.dispatchEvent(new Event('change', {{bubbles: true}})); return el.value;")
    if op == "check":
        on = "true" if str(args.get("checked", "true")).lower() not in ("false", "0", "no") else "false"
        return f"{find} el.checked = {on}; el.dispatchEvent(new Event('change', {{bubbles: true}})); return String(el.checked);"
    if op == "key":
        key = _js_str(args.get("key") or "Enter")
        return ("const el = document.activeElement || document.body; "
                f"for (const t of ['keydown', 'keypress', 'keyup']) el.dispatchEvent(new KeyboardEvent(t, {{key: {key}, bubbles: true}})); "
                "return 'ok';")
    if op == "scroll":
        x, y = float(args.get("x", 0) or 0), float(args.get("y", args.get("dy", 400)) or 0)
        return f"window.scrollBy({x}, {y}); return window.scrollX + ',' + window.scrollY;"
    if op == "wait":
        ms = int(args.get("timeout", 5000) or 5000)
        return (f"const deadline = Date.now() + {ms}; "
                f"while (Date.now() < deadline) {{ if (document.querySelector({sel})) return 'found'; "
                "await new Promise(r => setTimeout(r, 100)); } return 'timeout';")
    if op == "drag":
        to = _js_str(args.get("to") or "")
        return (f"{find} const dst = document.querySelector({to}); if (!dst) throw new Error('target not found');"
                "const dt = new DataTransfer(); el.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));"
                "dst.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: dt}));"
                "el.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt})); return 'dragged';")
    return None


class PortalBridge:
    def __init__(self, canvas_widget, client, post: Optional[Callable[[dict], None]] = None) -> None:
        self.cw, self.client = canvas_widget, client
        self._post = post            # tests inject a collector; default = POST /api/portal-results on a worker thread
        self.logs: dict[str, list[str]] = {}
        self._log_views: set[str] = set()
        canvas_widget.connect("portal-popup", self._on_popup)

    # ---- dispatch ----------------------------------------------------------------------------------------------
    def handle_event(self, evt: dict) -> None:
        rid = evt.get("requestId")
        if not rid:
            return
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else evt   # flat (EventBroadcaster) or nested
        op = str(payload.get("op") or "")
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        view = self.view_for(payload.get("portalId"))
        if view is None:
            return self.reply(rid, False, error=f"error: portal view unavailable (portal {payload.get('portalId')} is not on the canvas)")
        try:
            self.run(view, str(payload.get("portalId")), op, args, lambda ok, res=None, err=None: self.reply(rid, ok, res, err))
        except Exception as exc:   # noqa: BLE001 - the server must always get an answer
            self.reply(rid, False, error=f"error: {op}: {exc}")

    def view_for(self, portal_id) -> Any:
        if not portal_id:
            return None
        frame = self.cw.frames.get(norm(str(portal_id)))
        content = getattr(frame, "content", None)
        return content if content is not None and getattr(content, "web", None) is not None else None

    def run(self, view, portal_id: str, op: str, args: dict, done: Callable) -> None:
        web = view.web
        if op == "navigate":
            url = str(args.get("url") or "")
            return done(True, view.meta.current_url or url) if view.navigate(url) else done(False, err=f"error: invalid URL {url!r}")
        if op in ("back", "forward", "reload"):
            {"back": view.go_back, "forward": view.go_forward, "reload": view.reload}[op]()
            return done(True, "ok")
        if op == "info":
            return done(True, view.info())
        if op == "screenshot":
            return self._screenshot(view, done)
        if op == "snapshot" and not args.get("js"):
            return view.snapshot(lambda r, e: done(e is None, r, e))
        if op == "text" and not args.get("js"):
            return view.text(lambda r, e: done(e is None, r or "", e))
        if op == "html" and not args.get("js"):
            return view.html(lambda r, e: done(e is None, r or "", e))
        if op == "logs-start":
            self.start_logs(portal_id, view)
            return done(True, "Console capture started")
        if op == "logs" and not args.get("js"):
            lines = self.logs.get(norm(portal_id), [])
            return done(True, "\n".join(lines) if lines else "(no console output)")
        js = args.get("js")
        if js:
            return self.evaluate_async(web, f"return eval({_js_str(js)});", done)
        body = builtin_js(op, args)
        if body is None:
            return done(False, err=f"error: unsupported portal op {op!r}")
        self.evaluate_async(web, body, done)

    # ---- JavaScript ----------------------------------------------------------------------------------------------
    @staticmethod
    def evaluate_async(web, body: str, done: Callable) -> None:
        """Run `body` as an async function (WebKit awaits a returned Promise) and hand back its value as text."""
        def finished(w, res) -> None:
            try:
                v = w.call_async_javascript_function_finish(res)
                if v is None or v.is_undefined() or v.is_null():
                    return done(True, "")
                scalar = v.is_string() or v.is_number() or v.is_boolean()
                done(True, v.to_string() if scalar else (v.to_json(0) or v.to_string()))
            except GLib.Error as e:
                done(False, err=f"error: {e.message}")
        web.call_async_javascript_function(body, -1, None, None, None, None, finished)

    # ---- screenshot --------------------------------------------------------------------------------------------
    @staticmethod
    def _screenshot(view, done: Callable) -> None:
        fd, path = tempfile.mkstemp(prefix="maestri-portal-", suffix=".png")
        os.close(fd)

        def saved(p, err) -> None:
            try:
                if err is not None or not p:
                    return done(False, err=f"error: screenshot failed: {err}")
                with open(p, "rb") as fh:
                    done(True, base64.b64encode(fh.read()).decode("ascii"))
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        view.screenshot(path, saved)

    # ---- console ------------------------------------------------------------------------------------------------
    def start_logs(self, portal_id: str, view) -> None:
        key = norm(portal_id)
        self.logs.setdefault(key, [])
        if key in self._log_views:
            return
        self._log_views.add(key)
        from gi.repository import WebKit
        ucm = view.web.get_user_content_manager()
        ucm.add_script(WebKit.UserScript.new(LOG_HOOK_JS, WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                             WebKit.UserScriptInjectionTime.START, None, None))
        ucm.register_script_message_handler("maestriLog", None)

        def got(_m, value) -> None:
            try:
                level, _, text = value.to_string().partition("\t")
            except Exception:   # noqa: BLE001
                return
            lines = self.logs.setdefault(key, [])
            lines.append(f"[{level}] {text}")
            del lines[:-MAX_LOG_LINES]
        ucm.connect("script-message-received::maestriLog", got)
        view.web.evaluate_javascript(LOG_HOOK_JS, -1, None, None, None, None, None)   # the page already loaded, too

    # ---- results ------------------------------------------------------------------------------------------------
    def reply(self, request_id: str, ok: bool, result: Any = None, error: Optional[str] = None) -> None:
        body = {"requestId": request_id, "ok": bool(ok)}
        if ok:
            body["result"] = result if result is not None else ""
        else:
            body["error"] = error or "error: portal command failed"
        if self._post is not None:
            return self._post(body)
        threading.Thread(target=self._send, args=(body,), daemon=True).start()

    def _send(self, body: dict) -> None:
        c = self.client
        try:
            fn = getattr(c, "post_portal_result", None)
            if callable(fn):
                fn(body)
                return
            c.session.post(f"{c.base_url}/api/portal-results", json=body,
                           headers=c._headers({"Content-Type": "application/json"}), timeout=10)
        except Exception as exc:   # noqa: BLE001 - the server times out the request after 30 s
            print(f"[portal-bridge] could not post result {body.get('requestId')}: {exc!r}")

    # ---- target=_blank -----------------------------------------------------------------------------------------
    def _on_popup(self, _cw, node_id: str, url: str) -> None:
        root = self.cw.get_root()
        opener = getattr(root, "open_portal", None)
        if callable(opener):
            opener(url, node_id)
            return
        create = getattr(root, "_create_node", None)
        node = self.cw.scene.get(node_id)
        if not callable(create) or node is None:
            return
        try:
            from maestri_core.models.node import PortalContent
        except ImportError:
            return
        f = node.frame
        create("portal", at=Point(f.x + f.w + 40, f.y), size=(f.w, f.h), content=PortalContent(name="Portal", current_url=url))
