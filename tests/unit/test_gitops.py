"""GitOps: async Gio.Subprocess git actions against a real temp repo."""
import subprocess
import time

import pytest

pytest.importorskip("gi")
from gi.repository import GLib

from maestri_core.gitops import GitOps


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    git(tmp_path, "branch", "feature")
    return tmp_path


def run(ops, action, arg=None, timeout=10):
    out = []
    ops.run(action, out.append, arg)
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while not out and time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)
    assert out, "git action timed out"
    return out[0]


def test_argv_builders_and_validation():
    assert GitOps.argv_for("pull") == ["pull", "--ff-only"]
    assert GitOps.argv_for("checkout", "feature") == ["switch", "feature"]
    for bad in ("--force", "", "a b", "x..y"):
        with pytest.raises(ValueError):
            GitOps.argv_for("checkout", bad)
    with pytest.raises(ValueError):
        GitOps.argv_for("rm-rf")


def test_checkout_and_branch_listing(repo):
    ops = GitOps(str(repo))
    assert run(ops, "checkout", "feature").ok
    cur = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    assert cur == "feature"
    names = []
    ops.list_branches(names.extend)
    ctx = GLib.MainContext.default()
    end = time.time() + 10
    while not names and time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)
    assert set(names) == {"main", "feature"}


def test_stash_and_pop_roundtrip_including_untracked(repo):
    ops = GitOps(str(repo))
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("x")
    assert run(ops, "stash").ok
    assert (repo / "a.txt").read_text() == "1" and not (repo / "new.txt").exists()
    assert run(ops, "stash-pop").ok
    assert (repo / "a.txt").read_text() == "changed" and (repo / "new.txt").exists()


def test_failure_is_reported_not_raised(repo):
    r = run(GitOps(str(repo)), "push")  # no remote configured
    assert not r.ok and r.output
    assert not run(GitOps(str(repo)), "checkout", "--evil").ok
    assert not run(GitOps(str(repo)), "checkout", "nope").ok
