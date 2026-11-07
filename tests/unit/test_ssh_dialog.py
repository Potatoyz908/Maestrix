"""Remote SSH interface (docs/linux-ssh-spec.md, Pixel): Connect to Host… with a fake client (no real SSH, no server).

Run under Xvfb with OPEN_MAESTRI_DATA_DIR set to a temporary directory.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "gtk4-ui" / "src"), str(ROOT / "canvas-cairo")]

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from maestri_core.models.workspace import WorkspaceEntry  # noqa: E402
from ui.ssh_dialog import (ConnectToHostDialog, card_location, open_connect_to_host, parse_host_spec,  # noqa: E402
                           ssh_unavailable_reason, valid_token)

if not Gtk.init_check():
    pytest.skip("no display (run under Xvfb)", allow_module_level=True)

HOSTS = [{"alias": "devbox", "hostName": "10.0.0.5", "user": "euller", "port": 22},
         {"alias": "locked", "hostName": "locked.example", "user": "ana"}]


def pump(cond=lambda: False, timeout: float = 3.0) -> bool:
    ctx, end = GLib.MainContext.default(), time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def fake_client() -> MagicMock:
    c = MagicMock()
    c.ssh_hosts.return_value = HOSTS
    c.ssh_test.side_effect = lambda t: ({"ok": False, "needsPassword": True, "message": "Permission denied"}
                                        if t["host"] == "locked" else {"ok": True, "message": "ok", "needsPassword": False})
    c.ssh_list_dir.side_effect = lambda t, p: (
        {"needsPassword": True} if t["host"] == "locked" else
        {"path": p or "/home/euller", "entries": [{"name": "project/", "isDir": True}, {"name": "notes.txt", "isDir": False}]})
    c.create_ssh_workspace.return_value = {"id": "abcd-1234"}
    return c


@pytest.fixture
def dialog():
    opened: list[str] = []
    client = fake_client()
    parent = Gtk.Window()
    dlg = ConnectToHostDialog(parent, client, on_opened=opened.append)
    dlg.present()
    assert pump(lambda: dlg.host_list.get_row_at_index(len(HOSTS)) is not None)
    yield dlg, client, opened
    dlg.close()
    parent.destroy()
    pump(timeout=0.1)


def test_lists_hosts_and_add_new_entry(dialog):
    dlg, client, _ = dialog
    client.ssh_hosts.assert_called_once()
    rows = [dlg.host_list.get_row_at_index(i).host_key for i in range(len(HOSTS) + 1)]
    assert rows == [0, 1, "new"]
    assert not dlg.next_btn.get_sensitive()          # nothing selected yet


def test_test_connection_ok_and_needs_password(dialog):
    dlg, client, _ = dialog
    dlg.select_host(0)
    assert dlg.target == {"host": "devbox", "user": "euller", "port": 22}
    dlg.test_connection()
    assert pump(lambda: dlg.status.get_label() == "Connection OK.")
    dlg.select_host(1)
    dlg.test_connection()
    assert pump(lambda: "password" in dlg.status.get_label())
    assert dlg.needs_password and "terminal" in dlg.status.get_label()


def test_folder_step_browses_and_open_creates_ssh_workspace(dialog):
    dlg, client, opened = dialog
    dlg.select_host(0)
    dlg.show_folder()
    assert pump(lambda: dlg.dir_list.get_row_at_index(1) is not None)
    assert dlg.path_entry.get_text() == "/home/euller"
    folder = dlg.dir_list.get_row_at_index(0)
    assert folder.is_dir and folder.host_key == "project"     # dirs first
    dlg._enter(folder)
    assert pump(lambda: dlg.path_entry.get_text() == "/home/euller/project")
    dlg.open_workspace()
    assert pump(lambda: opened == ["ABCD-1234"])
    client.create_ssh_workspace.assert_called_once_with(
        "project", {"host": "devbox", "user": "euller", "port": 22, "remotePath": "/home/euller/project"})


def test_password_host_folder_step_is_text_only(dialog):
    dlg, _client, opened = dialog
    dlg.select_host(1)
    dlg.show_folder()
    assert pump(lambda: not dlg.dir_scroll.get_visible())
    assert "password" in dlg.status.get_label()
    dlg.path_entry.set_text("/srv/app")
    assert dlg.open_btn.get_sensitive()


def test_add_new_host_parses_and_validates(dialog):
    dlg, _client, _ = dialog
    dlg.select_host(len(HOSTS))
    assert dlg.new_host_box.get_visible()
    dlg.new_host_entry.set_text("root@box.local:2222")
    assert dlg.target == {"host": "box.local", "user": "root", "port": 2222}
    dlg.new_host_entry.set_text("-oProxyCommand=evil")
    assert dlg.target is None and not dlg.next_btn.get_sensitive()


def test_pure_validation():
    assert parse_host_spec("me@host", "~/.ssh/id") == {"host": "host", "user": "me", "identityFile": "~/.ssh/id"}
    for bad in ("-oProxyCommand=x", "a b", "me@-x", "h:0", "h:70000", ""):
        with pytest.raises(ValueError):
            parse_host_spec(bad)
    assert not valid_token("-x") and valid_token("user.name@h-1")


def test_card_shows_user_at_host_and_ssh_badge():
    local = WorkspaceEntry.from_dict({"id": "A", "name": "Local", "workingDirectory": "/tmp/x"})
    remote = WorkspaceEntry.from_dict({"id": "B", "name": "Remote", "workingDirectory": "/srv/app", "locationType": "ssh",
                                       "ssh": {"host": "devbox", "user": "euller", "remotePath": "/srv/app"}})
    assert card_location(local) is None
    assert card_location(remote) == "euller@devbox:/srv/app"
    from maestri_core.client.api import MaestriClient
    from ui.sidebar import SidebarView
    sidebar = SidebarView(MaestriClient(port=9, token="x", timeout=0.3))   # offline client, isolated data dir
    card = sidebar._create_workspace_card(remote)
    assert getattr(card, "ssh_badge", None) is not None
    assert card.ssh_badge.get_icon_name() == "maestri-network-symbolic"
    assert "euller@devbox:/srv/app" in (card.get_tooltip_text() or "")


def test_entry_point_disabled_without_core():
    win = MagicMock(core_connected=False)
    assert ssh_unavailable_reason(win) and "core" in ssh_unavailable_reason(win)
    win.core_connected = True
    assert ssh_unavailable_reason(win) is None
    parent = Gtk.Window()
    parent.core_connected, parent.client = True, fake_client()
    dlg = open_connect_to_host(parent)
    assert isinstance(dlg, ConnectToHostDialog)
    dlg.close()
    parent.destroy()


def test_settings_remote_ssh_section_persists_macos_keys(tmp_path):
    from maestri_core.preferences import PreferencesStore
    from ui.settings_window import SettingsWindow
    store = PreferencesStore(tmp_path)
    parent = Gtk.Window()
    sw = SettingsWindow(parent, store=store)
    assert sw.ssh_tunnel.get_value() == 7433 and sw.ssh_script.get_text() == "~/.local/bin/omaestri"
    assert not sw.ssh_tunnel.get_sensitive()
    sw.ssh_switch.set_active(True)
    sw.ssh_tunnel.set_value(7500)
    sw.ssh_script.set_text("~/bin/omaestri")
    import json
    data = json.loads((tmp_path / "preferences.json").read_text())
    assert (data["sshEnabled"], data["sshTunnelPort"], data["sshScriptPath"]) == (True, 7500, "~/bin/omaestri")
    assert sw.ssh_tunnel.get_sensitive()
    sw.close()
    parent.destroy()
