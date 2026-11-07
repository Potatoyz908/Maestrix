"""SSH phase A contract: the server (Codice) and the Connect-to-Host dialog's data layer (Pixel, `SshApi` in
gtk4-ui/src/ui/ssh_dialog.py) speak the same REST contract (docs/linux-ssh-spec.md). No GUI: an isolated core
(OPEN_MAESTRI_DATA_DIR), a FAKE ssh (OPEN_MAESTRI_SSH_BIN) and a test ssh_config (OPEN_MAESTRI_SSH_CONFIG); the real
MaestriClient. Never touches ~/.ssh or a real host."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gtk4-ui" / "src"))
sys.path.insert(0, str(ROOT))

from maestri_core.client.api import MaestriClient  # noqa: E402
from tests.data_dir_isolation import start_isolated_server, stop_server  # noqa: E402

ssh_dialog = pytest.importorskip("ui.ssh_dialog")

FAKE_SSH = """#!/bin/sh
{ for a in "$@"; do printf '%s\\n' "$a"; done; echo ---; } >> "$FAKE_SSH_LOG"
for a in "$@"; do case "$a" in *locked*) echo 'Permission denied (publickey,password).' >&2; exit 255;; esac; done
last=""; for a in "$@"; do last="$a"; done
case "$last" in *'ls -1Ap'*) printf '/home/me/proj\\nsrc/\\nREADME.md\\n';; esac
exit 0
"""


@pytest.fixture
def ssh_server(tmp_path, monkeypatch):
    fake = tmp_path / "ssh"
    fake.write_text(FAKE_SSH)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    key = tmp_path / "id_test"
    key.write_text("not a real key\n")
    cfg = tmp_path / "ssh_config"
    cfg.write_text(f"Host *\n  User nobody\nHost box\n  HostName 192.0.2.7\n  User me\n  Port 2222\n  IdentityFile {key}\n")
    log = tmp_path / "argv.log"
    monkeypatch.setenv("OPEN_MAESTRI_SSH_BIN", str(fake))
    monkeypatch.setenv("OPEN_MAESTRI_SSH_CONFIG", str(cfg))
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    srv = start_isolated_server(tmp_path / "home")
    try:
        yield srv, MaestriClient(port=srv["port"], auth_token=srv["token"]), log, key
    finally:
        stop_server(srv["proc"])


@pytest.mark.e2e
def test_dialog_data_layer_matches_server_contract(ssh_server):
    srv, client, log, key = ssh_server
    api = ssh_dialog.SshApi(client)

    # hosts(): fields the dialog reads, with the spec's names and types (wildcard Host * skipped)
    hosts = api.hosts()
    assert [h["alias"] for h in hosts] == ["box"], hosts
    h = hosts[0]
    assert h["hostName"] == "192.0.2.7" and h["user"] == "me"
    assert isinstance(h["port"], int) and h["port"] == 2222
    assert h["identityFile"] == str(key)

    # test(target): ok / message / needsPassword
    target = ssh_dialog.target_from_host(h)
    res = api.test(target)
    assert set(res) >= {"ok", "message", "needsPassword"}, res
    assert res["ok"] is True and isinstance(res["message"], str) and res["needsPassword"] is False
    locked = api.test({"host": "locked"})
    assert locked["ok"] is False and locked["needsPassword"] is True and locked["message"]

    # list_dir(target, path): path / entries[name, isDir]
    listing = api.list_dir(target, "/home/me/proj")
    assert listing["path"] == "/home/me/proj"
    assert listing["entries"] == [{"isDir": True, "name": "src"}, {"isDir": False, "name": "README.md"}]
    assert all(isinstance(e["name"], str) and isinstance(e["isDir"], bool) for e in listing["entries"])

    # malicious host from the dialog: the server refuses (400) and ssh never runs
    calls_before = log.read_text().count("---")
    with pytest.raises(Exception):
        api.test({"host": "-oProxyCommand=touch /tmp/pwn"})
    assert log.read_text().count("---") == calls_before

    # create_workspace(): locationType ssh + ssh object, back from GET and in the manifest
    wid = api.create_workspace("Remote proj", target, "/home/me/proj")
    assert wid and wid != "NONE"
    resp = client.session.get(f"{client.base_url}/api/workspaces", headers=client._headers(), timeout=10)
    entry = next(w for w in resp.json() if w["id"].upper() == wid)
    assert entry["locationType"] == "ssh"
    assert entry["ssh"]["host"] == "box" and entry["ssh"]["remotePath"] == "/home/me/proj"
    assert entry["workingDirectory"] == "/home/me/proj"

    manifest = json.loads((srv["data_dir"] / "manifest.json").read_text())
    m = next(w for w in manifest["workspaces"] if w["id"].upper() == wid)
    assert m["locationType"] == "ssh" and m["ssh"]["host"] == "box"
    ws = json.loads((srv["data_dir"] / "workspaces" / wid / "workspace.json").read_text())
    assert ws["payload"]["locationType"] == "ssh" and ws["payload"]["ssh"]["remotePath"] == "/home/me/proj"
    assert ws["schemaVersion"] == 2
