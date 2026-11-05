"""FileTreeModel: /api/files + /api/git/status -> typed entries with propagated badges."""
from unittest.mock import patch

from maestri_core.client.api import MaestriClient, NotFoundError
from maestri_core.filetree import FileEntry, FileTreeModel
from maestri_core.models.node import FileTreeContent


class FakeClient:
    def __init__(self):
        self.calls = []

    def list_files(self, root="", path=""):
        self.calls.append(("files", root, path))
        if path == "missing":
            raise NotFoundError("nope", 404)
        return {
            "": [{"name": "src", "path": "src", "isDir": True},
                 {"name": "README.md", "path": "README.md", "isDir": False, "gitStatus": "M"}],
            "src": [{"name": "a.py", "path": "src/a.py", "isDir": False}],
        }[path]

    def get_git_status(self, root=""):
        self.calls.append(("git", root))
        return [{"path": "src/a.py", "status": "?"}, {"path": "src/b.py", "status": "D"}]


def make():
    c = FakeClient()
    return c, FileTreeModel(c, FileTreeContent(root_path="/repo"))


def test_refresh_loads_root_and_git_status():
    c, m = make()
    m.refresh()
    assert [e.name for e in m.entries()] == ["src", "README.md"]
    assert ("files", "/repo", "") in c.calls and ("git", "/repo") in c.calls


def test_badges_file_direct_and_folder_inherits_strongest():
    _, m = make()
    m.refresh()
    src, readme = m.entries()
    assert m.badge_for(readme) == "M"
    assert m.badge_for(src) == "D"          # D outranks ? among descendants
    m.load_dir("src")
    assert m.badge_for(m.entries("src")[0]) == "?"


def test_refresh_reloads_expanded_dirs():
    c, m = make()
    m.load_dir("src")
    c.calls.clear()
    m.refresh()
    assert ("files", "/repo", "src") in c.calls


def test_errors_are_captured_not_raised():
    _, m = make()
    assert m.load_dir("missing") == [] and m.error


def test_real_client_parses_server_shapes():
    client = MaestriClient(port=1, auth_token="t")
    with patch.object(client.session, "get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = [{"name": "x", "path": "x", "isDir": False, "gitStatus": "A"}]
        m = FileTreeModel(client, FileTreeContent(root_path="/r"))
        assert m.load_dir() == [FileEntry("x", "x", False, "A")]


def test_offline_client_yields_empty_tree():
    m = FileTreeModel(MaestriClient(port=1, auth_token="t"), FileTreeContent(root_path="/r"))
    m.refresh()
    assert m.entries() == [] and m.error
