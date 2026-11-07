"""Remote SSH workspaces, VS Code "Remote-SSH" style (docs/linux-ssh-spec.md, workstream Pixel).

"Connect to Host…" is a two-step modal:
  1. host: the hosts of ~/.ssh/config (GET /api/ssh/hosts) + "Add New SSH Host…" (user@host[:port] + optional identity
     file); "Test connection" (POST /api/ssh/test) shows OK / the error, and warns when the password will be asked in the
     terminal (needsPassword).
  2. folder: a path field + a browsable list (GET /api/ssh/list-dir: double click enters, Up, Home); with needsPassword only
     the field. "Open" creates the workspace (POST /api/workspaces, locationType "ssh" + "ssh": {...}) and switches to it.

SshApi talks to the core: it prefers client methods (ssh_hosts / ssh_test / ssh_list_dir / create_ssh_workspace, e.g. a fake
client in tests) and otherwise calls the REST contract through the client's session. SSH needs the core: without it the
entry points are disabled with a clear message (`ssh_unavailable_reason`).
"""
from __future__ import annotations

import os
import posixpath
import re
import threading
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .iconography import icon_name  # noqa: E402

_TOKEN = re.compile(r"^[A-Za-z0-9._@:-]+$")
UNAVAILABLE = "Remote SSH needs the Open Maestri core (it is not connected)."


# ---------------------------------------------------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------------------------------------------------
def valid_token(value: str) -> bool:
    """host / user: only [A-Za-z0-9._@:-] and never a leading '-' (an option like -oProxyCommand=... would be injection)."""
    return bool(value) and not value.startswith("-") and bool(_TOKEN.match(value))


def parse_host_spec(text: str, identity_file: str = "") -> dict:
    """'user@host[:port]' -> {"host", "user"?, "port"?, "identityFile"?}. Raises ValueError with a readable message."""
    spec = (text or "").strip()
    if not spec:
        raise ValueError("Enter a host as user@host or user@host:port.")
    user = ""
    if "@" in spec:
        user, spec = spec.rsplit("@", 1)
    port = None
    if spec.count(":") == 1:
        spec, p = spec.split(":")
        if not p.isdigit() or not 1 <= int(p) <= 65535:
            raise ValueError("The port must be a number between 1 and 65535.")
        port = int(p)
    if not valid_token(spec):
        raise ValueError(f"Invalid host: {spec!r}.")
    if user and not valid_token(user):
        raise ValueError(f"Invalid user: {user!r}.")
    out: dict = {"host": spec}
    if user:
        out["user"] = user
    if port is not None:
        out["port"] = port
    ident = (identity_file or "").strip()
    if ident:
        if ident.startswith("-"):
            raise ValueError("Invalid identity file.")
        out["identityFile"] = ident
    return out


def target_from_host(h: dict) -> dict:
    """A /api/ssh/hosts entry -> the target of test / list-dir / workspace (the alias resolves through ~/.ssh/config)."""
    out: dict = {"host": h.get("alias") or h.get("hostName") or ""}
    for src, dst in (("user", "user"), ("port", "port"), ("identityFile", "identityFile")):
        if h.get(src):
            out[dst] = h[src]
    return out


def ssh_label(target: dict) -> str:
    host = target.get("host", "")
    user = target.get("user")
    port = target.get("port")
    s = f"{user}@{host}" if user else host
    return f"{s}:{port}" if port and int(port) != 22 else s


def entry_ssh(entry: Any) -> Optional[dict]:
    """The `ssh` object of a workspace entry (manifest / WorkspaceEntry._extra), or None for a local workspace."""
    if getattr(entry, "location_type", "local") != "ssh":
        return None
    ssh = getattr(entry, "_extra", {}).get("ssh") if hasattr(entry, "_extra") else None
    return ssh if isinstance(ssh, dict) else {}


def card_location(entry: Any) -> Optional[str]:
    """Sidebar card subtitle of an SSH workspace: user@host (+ :path), else None."""
    ssh = entry_ssh(entry)
    if ssh is None:
        return None
    label = ssh_label(ssh) if ssh.get("host") else "SSH"
    path = ssh.get("remotePath") or getattr(entry, "working_directory", "")
    return f"{label}:{path}" if path else label


def ssh_unavailable_reason(window) -> Optional[str]:
    return None if getattr(window, "core_connected", False) else UNAVAILABLE


# ---------------------------------------------------------------------------------------------------------------------
# REST adapter
# ---------------------------------------------------------------------------------------------------------------------
class SshApi:
    def __init__(self, client) -> None:
        self.client = client

    def _own(self, name: str):
        fn = getattr(self.client, name, None)
        return fn if callable(fn) else None

    def _get(self, path: str, params: Optional[dict] = None):
        c = self.client
        r = c.session.get(f"{c.base_url}{path}", params=params, headers=c._headers(), timeout=20)
        return c._handle_response(r).json()

    def _post(self, path: str, body: dict):
        c = self.client
        r = c.session.post(f"{c.base_url}{path}", json=body, headers=c._headers({"Content-Type": "application/json"}),
                           timeout=20)
        return c._handle_response(r).json()

    def hosts(self) -> list[dict]:
        fn = self._own("ssh_hosts")
        data = fn() if fn else self._get("/api/ssh/hosts")
        return [h for h in (data or []) if isinstance(h, dict) and (h.get("alias") or h.get("hostName"))]

    def test(self, target: dict) -> dict:
        fn = self._own("ssh_test")
        return dict((fn(target) if fn else self._post("/api/ssh/test", target)) or {})

    def list_dir(self, target: dict, path: str = "") -> dict:
        fn = self._own("ssh_list_dir")
        if fn:
            return dict(fn(target, path) or {})
        return dict(self._get("/api/ssh/list-dir", {**{k: str(v) for k, v in target.items()}, "path": path}) or {})

    def create_workspace(self, name: str, target: dict, remote_path: str) -> str:
        ssh = {**target, "remotePath": remote_path}
        fn = self._own("create_ssh_workspace")
        if fn:
            res = fn(name, ssh)
        else:
            res = self._post("/api/workspaces", {"name": name, "workingDirectory": remote_path, "icon": "network",
                                                 "color": "blue", "locationType": "ssh", "ssh": ssh})
        wid = res.get("id") if isinstance(res, dict) else getattr(res, "id", res)
        return str(wid).upper()


def _run(fn: Callable[[], Any], done: Callable[[Any, Optional[Exception]], None]) -> None:
    """fn off the main thread; done(result, error) back on it."""
    def work() -> None:
        try:
            res, err = fn(), None
        except Exception as exc:   # noqa: BLE001 - shown to the user
            res, err = None, exc
        GLib.idle_add(lambda: (done(res, err), GLib.SOURCE_REMOVE)[1])
    threading.Thread(target=work, name="maestri-ssh", daemon=True).start()


# ---------------------------------------------------------------------------------------------------------------------
# dialog
# ---------------------------------------------------------------------------------------------------------------------
class ConnectToHostDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, client, on_opened: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(title="Connect to Host…", transient_for=parent, modal=True, resizable=False)
        self.set_default_size(520, 460)
        self.add_css_class("maestri-modal-window")
        self.api = SshApi(client)
        self.on_opened = on_opened
        self.hosts: list[dict] = []
        self.target: Optional[dict] = None
        self.needs_password = False
        self.current_path = ""

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=20, margin_bottom=20, margin_start=24,
                       margin_end=24)
        self.title_lbl = Gtk.Label(label="Connect to Host…", xalign=0)
        self.title_lbl.add_css_class("heading")
        root.append(self.title_lbl)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.stack.add_named(self._build_hosts_page(), "hosts")
        self.stack.add_named(self._build_folder_page(), "folder")
        root.append(self.stack)

        self.status = Gtk.Label(xalign=0, wrap=True, visible=False)
        root.append(self.status)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.back_btn = Gtk.Button(label="Back", visible=False)
        self.back_btn.connect("clicked", lambda _b: self.show_hosts())
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.test_btn = Gtk.Button(label="Test connection")
        self.test_btn.connect("clicked", lambda _b: self.test_connection())
        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", lambda _b: self.show_folder())
        self.open_btn = Gtk.Button(label="Open", visible=False)
        self.open_btn.add_css_class("suggested-action")
        self.open_btn.connect("clicked", lambda _b: self.open_workspace())
        for w in (self.back_btn, cancel, self.test_btn, self.next_btn, self.open_btn):
            actions.append(w)
        root.append(actions)
        self.set_child(root)
        self._update_buttons()
        self.load_hosts()

    # ---- step 1: host -------------------------------------------------------------------------------------------
    def _build_hosts_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hint = Gtk.Label(label="Select configured SSH host or enter user@host", xalign=0)
        hint.add_css_class("dim-label")
        box.append(hint)
        self.host_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.host_list.add_css_class("boxed-list")
        self.host_list.connect("row-selected", lambda _l, row: self._host_selected(row))
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, min_content_height=160)
        sc.set_child(self.host_list)
        box.append(sc)

        self.new_host_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.new_host_entry = Gtk.Entry(placeholder_text="user@host or user@host:port", activates_default=True)
        self.new_host_entry.connect("changed", lambda _e: self._new_host_changed())
        ident_row = Gtk.Box(spacing=8)
        self.identity_entry = Gtk.Entry(placeholder_text="Identity file (optional), e.g. ~/.ssh/id_ed25519", hexpand=True)
        self.identity_entry.connect("changed", lambda _e: self._new_host_changed())
        browse = Gtk.Button(label="Browse…")
        browse.connect("clicked", lambda _b: self._browse_identity())
        ident_row.append(self.identity_entry)
        ident_row.append(browse)
        self.new_host_box.append(self.new_host_entry)
        self.new_host_box.append(ident_row)
        box.append(self.new_host_box)
        return box

    def _host_row(self, label: str, subtitle: str, icon: str, key: Any) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.host_key = key
        h = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        h.append(Gtk.Image.new_from_icon_name(icon_name(icon)))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        texts.append(Gtk.Label(label=label, xalign=0))
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            texts.append(sub)
        h.append(texts)
        row.set_child(h)
        return row

    def load_hosts(self) -> None:
        self._set_status("Loading hosts from ~/.ssh/config…", None)
        _run(self.api.hosts, self._on_hosts)

    def _on_hosts(self, hosts, err) -> None:
        self.hosts = list(hosts or [])
        child = self.host_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.host_list.remove(child)
            child = nxt
        for i, h in enumerate(self.hosts):
            sub = ssh_label({"host": h.get("hostName") or h.get("alias"), "user": h.get("user"), "port": h.get("port")})
            self.host_list.append(self._host_row(h.get("alias") or h.get("hostName"), sub, "network", i))
        self.host_list.append(self._host_row("Add New SSH Host…", "", "plus", "new"))
        if err is not None:
            self._set_status(f"Could not read the SSH hosts: {err}", False)
        else:
            self._set_status("" if self.hosts else "No hosts in ~/.ssh/config. Add a new SSH host.", None)

    def _host_selected(self, row) -> None:
        key = getattr(row, "host_key", None)
        self.new_host_box.set_visible(key == "new")
        self.needs_password = False
        if key == "new":
            self._new_host_changed()
            self.new_host_entry.grab_focus()
        elif isinstance(key, int) and 0 <= key < len(self.hosts):
            self.target = target_from_host(self.hosts[key])
            self._set_status("", None)
        else:
            self.target = None
        self._update_buttons()

    def _new_host_changed(self) -> None:
        try:
            self.target = parse_host_spec(self.new_host_entry.get_text(), self.identity_entry.get_text())
            self._set_status("", None)
        except ValueError as exc:
            self.target = None
            if self.new_host_entry.get_text().strip():
                self._set_status(str(exc), False)
        self._update_buttons()

    def _browse_identity(self) -> None:
        dlg = Gtk.FileDialog(title="Identity File")

        def done(d, res) -> None:
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                self.identity_entry.set_text(f.get_path().replace(os.path.expanduser("~"), "~", 1))

        dlg.open(self, None, done)

    def select_host(self, index: int) -> None:
        """Select the index-th row (the last row is "Add New SSH Host…")."""
        row = self.host_list.get_row_at_index(index)
        if row is not None:
            self.host_list.select_row(row)

    def test_connection(self) -> None:
        if self.target is None:
            return
        target = dict(self.target)
        self._set_status(f"Testing {ssh_label(target)}…", None)
        self.test_btn.set_sensitive(False)
        _run(lambda: self.api.test(target), self._on_tested)

    def _on_tested(self, res, err) -> None:
        self.test_btn.set_sensitive(self.target is not None)
        if err is not None:
            self._set_status(f"Connection failed: {err}", False)
            return
        res = res or {}
        self.needs_password = bool(res.get("needsPassword"))
        if res.get("ok"):
            self._set_status("Connection OK.", True)
        elif self.needs_password:
            self._set_status("This host needs a password or passphrase: it will be asked in the terminal.", None)
        else:
            self._set_status(f"Connection failed: {res.get('message') or 'unknown error'}", False)

    # ---- step 2: folder -----------------------------------------------------------------------------------------
    def _build_folder_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.folder_host_lbl = Gtk.Label(xalign=0)
        self.folder_host_lbl.add_css_class("dim-label")
        box.append(self.folder_host_lbl)
        row = Gtk.Box(spacing=6)
        self.path_entry = Gtk.Entry(placeholder_text="/home/user/project", hexpand=True, activates_default=True)
        self.path_entry.connect("activate", lambda _e: self.browse(self.path_entry.get_text().strip()))
        self.path_entry.connect("changed", lambda _e: self._update_buttons())
        up = Gtk.Button(icon_name=icon_name("arrow-up"), tooltip_text="Up")
        up.connect("clicked", lambda _b: self.go_up())
        home = Gtk.Button(icon_name=icon_name("folder"), tooltip_text="Home")
        home.connect("clicked", lambda _b: self.browse(""))
        self.nav_buttons = (up, home)
        for w in (self.path_entry, up, home):
            row.append(w)
        box.append(row)
        self.dir_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.dir_list.connect("row-activated", lambda _l, r: self._enter(r))
        self.dir_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, min_content_height=180)
        self.dir_scroll.set_child(self.dir_list)
        box.append(self.dir_scroll)
        return box

    def show_hosts(self) -> None:
        self.stack.set_visible_child_name("hosts")
        self.title_lbl.set_label("Connect to Host…")
        self._update_buttons()

    def show_folder(self) -> None:
        if self.target is None:
            return
        self.stack.set_visible_child_name("folder")
        self.title_lbl.set_label("Open Folder")
        self.folder_host_lbl.set_label(f"Remote folder on {ssh_label(self.target)}")
        self._set_status("", None)
        self._update_buttons()
        self.browse(self.path_entry.get_text().strip())

    def browse(self, path: str) -> None:
        if self.target is None:
            return
        target = dict(self.target)
        self._set_status("Listing…", None)
        _run(lambda: self.api.list_dir(target, path), self._on_listed)

    def _on_listed(self, res, err) -> None:
        child = self.dir_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.dir_list.remove(child)
            child = nxt
        if err is not None:
            self._set_status(f"Could not list the folder: {err}", False)
            return
        res = res or {}
        if res.get("needsPassword"):   # no browsing without a key: type the path, the password is asked in the terminal
            self.needs_password = True
            self.dir_scroll.set_visible(False)
            for b in self.nav_buttons:
                b.set_visible(False)
            self._set_status("This host needs a password: type the remote folder path; the password will be asked in the "
                             "terminal.", None)
            self._update_buttons()
            return
        self.dir_scroll.set_visible(True)
        self.current_path = str(res.get("path") or "")
        if self.current_path:
            self.path_entry.set_text(self.current_path)
        entries = sorted((e for e in res.get("entries") or [] if isinstance(e, dict) and e.get("name") not in (".", "..")),
                         key=lambda e: (not e.get("isDir"), str(e.get("name")).lower()))
        for e in entries:
            name = str(e.get("name")).rstrip("/")
            row = self._host_row(name, "", "folder" if e.get("isDir") else "file", name)
            row.is_dir = bool(e.get("isDir"))
            row.set_activatable(row.is_dir)
            if not row.is_dir:
                row.add_css_class("dim-label")
            self.dir_list.append(row)
        self._set_status("", None)
        self._update_buttons()

    def _enter(self, row) -> None:
        if getattr(row, "is_dir", False):
            self.browse(posixpath.join(self.current_path or self.path_entry.get_text().strip(), row.host_key))

    def go_up(self) -> None:
        base = self.current_path or self.path_entry.get_text().strip()
        if base:
            self.browse(posixpath.dirname(base.rstrip("/")) or "/")

    def open_workspace(self) -> None:
        path = self.path_entry.get_text().strip()
        if self.target is None or not path:
            return
        target = dict(self.target)
        name = posixpath.basename(path.rstrip("/")) or target["host"]
        self.open_btn.set_sensitive(False)
        self._set_status("Creating the workspace…", None)
        _run(lambda: self.api.create_workspace(name, target, path), self._on_created)

    def _on_created(self, wid, err) -> None:
        if err is not None:
            self.open_btn.set_sensitive(True)
            self._set_status(f"Could not create the workspace: {err}", False)
            return
        self.created_id = wid
        self.close()
        if self.on_opened is not None:
            self.on_opened(wid)

    # ---- chrome --------------------------------------------------------------------------------------------------
    def _update_buttons(self) -> None:
        folder = self.stack.get_visible_child_name() == "folder"
        self.back_btn.set_visible(folder)
        self.test_btn.set_visible(not folder)
        self.next_btn.set_visible(not folder)
        self.open_btn.set_visible(folder)
        self.test_btn.set_sensitive(self.target is not None)
        self.next_btn.set_sensitive(self.target is not None)
        self.open_btn.set_sensitive(bool(self.path_entry.get_text().strip()))

    def _set_status(self, text: str, ok: Optional[bool]) -> None:
        self.status.set_label(text)
        self.status.set_visible(bool(text))
        for c in ("success", "error", "dim-label"):
            self.status.remove_css_class(c)
        self.status.add_css_class("dim-label" if ok is None else "success" if ok else "error")

    def close(self) -> None:
        try:
            self.set_visible(False)
            self.set_modal(False)
            self.set_transient_for(None)
        except Exception:
            pass
        super().close()


def open_connect_to_host(window) -> Optional[ConnectToHostDialog]:
    """Entry point (File / Add / sidebar "+"): the dialog, or a message when the core is not connected."""
    reason = ssh_unavailable_reason(window)
    if reason:
        alert = Gtk.AlertDialog(message="Connect to Host…", detail=reason)
        alert.show(window)
        return None

    def opened(wid: str) -> None:
        sidebar = getattr(window, "sidebar", None)
        if sidebar is not None and hasattr(sidebar, "refresh"):
            sidebar.refresh()
        if hasattr(window, "_on_workspace_switched"):
            window._on_workspace_switched(None, wid)

    dlg = ConnectToHostDialog(window, window.client, on_opened=opened)
    window.ssh_dialog = dlg
    dlg.present()
    return dlg
