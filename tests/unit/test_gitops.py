"""GitOps: async Gio.Subprocess git actions against a real temp repo, porcelain parsing and the 60 s status cache."""
import subprocess
import time

import pytest

pytest.importorskip("gi")
from gi.repository import GLib

from maestri_core.diffparse import classify, stat
from maestri_core.gitops import (STATUS_TTL_SECONDS, GitOps, GitStatus, GitStatusCache, _valid_ref, parse_branch_header,
                                 parse_porcelain)


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    tmp_path = tmp_path / "repo"
    tmp_path.mkdir()
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("1\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    git(tmp_path, "branch", "feature")
    return tmp_path


def pump(cond, timeout=10):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while not cond() and time.time() < end:
        ctx.iteration(False)
        time.sleep(0.002)
    assert cond(), "async git call timed out"


def run(ops, action, arg=None):
    out = []
    ops.run(action, out.append, arg)
    pump(lambda: out)
    return out[0]


def status(ops, force=False):
    out = []
    ops.status(out.append, force=force)
    pump(lambda: out)
    return out[0]


def fresh(repo):
    return GitOps(str(repo), cache=GitStatusCache())


# ---- pure ------------------------------------------------------------------------------------------------------------
def test_argv_builders_and_validation():
    assert GitOps.argv_for("pull") == ["pull", "--ff-only"]
    assert GitOps.argv_for("fetch") == ["fetch", "--prune"]
    assert GitOps.argv_for("checkout", "feature") == ["switch", "feature"]
    assert GitOps.argv_for("new-branch", "feat/x") == ["switch", "--create", "feat/x"]
    assert GitOps.argv_for("merge", "feature") == ["merge", "--no-edit", "feature"]
    assert GitOps.steps_for("commit", "-m oops") == [["add", "--all"], ["commit", "--message=-m oops"]]
    for bad in ("--force", "", "a b", "x..y", "a~1", "x:y", "a.lock", "/a", "a/", ".hidden", "a\x01", "@{-1}"):
        with pytest.raises(ValueError):
            GitOps.argv_for("checkout", bad)
        with pytest.raises(ValueError):
            GitOps.argv_for("new-branch", bad)
    with pytest.raises(ValueError):
        GitOps.steps_for("commit", "   ")
    with pytest.raises(ValueError):
        GitOps.argv_for("rm-rf")
    assert _valid_ref("feature/login-2")


def test_parse_branch_header():
    assert parse_branch_header("## main...origin/main [ahead 2, behind 1]") == ("main", "origin/main", 2, 1, False)
    assert parse_branch_header("## main") == ("main", "", 0, 0, False)
    assert parse_branch_header("## No commits yet on main") == ("main", "", 0, 0, False)
    assert parse_branch_header("## HEAD (no branch)") == ("", "", 0, 0, True)
    assert parse_branch_header("## dev...origin/dev [behind 3]") == ("dev", "origin/dev", 0, 3, False)


def test_parse_porcelain_badges_and_renames():
    out = "\0".join(["## main...origin/main [ahead 1]", " M src/a.py", "A  new.py", "R  b2.py", "b.py", " D gone.txt",
                     "?? build/", "UU conflict.txt", ""])
    st = parse_porcelain(out, "/r")
    assert st.branch == "main" and st.ahead == 1 and st.upstream == "origin/main"
    assert st.changes == {"src/a.py": "M", "new.py": "A", "b2.py": "R", "gone.txt": "D", "build/": "?", "conflict.txt": "U"}
    # folders fold their descendants by priority; files inside an untracked folder are untracked
    assert st.badge_for("/r/src", is_dir=True) == "M"
    assert st.badge_for("/r/src/a.py") == "M"
    assert st.badge_for("/r/build/out/x.o") == "?"
    assert st.badge_for("/r/build", is_dir=True) == "?"
    assert st.badge_for("/r/clean.txt") is None
    assert st.badge_for("/elsewhere/x") is None
    assert st.dirty


def test_status_cache_ttl_and_invalidation():
    now = [1000.0]
    cache = GitStatusCache(clock=lambda: now[0])
    st = GitStatus(True, "/r", "main")
    cache.put("/r", st)
    cache.put("/r/sub", GitStatus(True, "/r", "main"))
    assert cache.get("/r") is st
    now[0] += STATUS_TTL_SECONDS - 0.1
    assert cache.get("/r") is st
    now[0] += 0.2
    assert cache.get("/r") is None          # expired after 60 s
    cache.put("/r", st)
    cache.invalidate("/r")                   # same toplevel -> both roots dropped
    assert cache.get("/r") is None and cache.get("/r/sub") is None


def test_diff_classifier_tells_headers_from_content():
    diff = "\n".join([
        "diff --git a/x.py b/x.py", "index 1..2 100644", "--- a/x.py", "+++ b/x.py",
        "@@ -1,3 +1,3 @@ def f():", " keep", "--- removed line that starts with dashes", "+++ added line with pluses", " end",
        "\\ No newline at end of file",
    ])
    kinds = [k for k, _ in classify(diff)]
    assert kinds == ["file", "meta", "meta", "meta", "hunk", "context", "del", "add", "context", "meta"]
    s = stat(classify(diff))
    assert (s.files, s.added, s.removed) == (1, 1, 1) and str(s) == "1 file, +1 −1"
    assert str(stat([])) == "no changes"


# ---- real repo ---------------------------------------------------------------------------------------------------------
def test_checkout_and_branch_listing(repo):
    ops = fresh(repo)
    assert run(ops, "checkout", "feature").ok
    assert git(repo, "branch", "--show-current").strip() == "feature"
    names = []
    ops.list_branches(names.extend)
    pump(lambda: names)
    assert set(names) == {"main", "feature"}


def test_stash_and_pop_roundtrip_including_untracked(repo):
    ops = fresh(repo)
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("x")
    assert run(ops, "stash").ok
    assert (repo / "a.txt").read_text() == "1\n" and not (repo / "new.txt").exists()
    assert run(ops, "stash-pop").ok
    assert (repo / "a.txt").read_text() == "changed" and (repo / "new.txt").exists()


def test_commit_stages_everything(repo):
    ops = fresh(repo)
    (repo / "a.txt").write_text("2\n")
    (repo / "b.txt").write_text("new\n")
    r = run(ops, "commit", "feat: second")
    assert r.ok, r.output
    assert git(repo, "log", "-1", "--format=%s").strip() == "feat: second"
    assert git(repo, "status", "--porcelain") == ""
    assert not run(ops, "commit", "nothing to commit").ok      # git refuses an empty commit


def test_new_branch_merge_and_fetch(repo):
    ops = fresh(repo)
    assert run(ops, "new-branch", "topic").ok
    (repo / "t.txt").write_text("t\n")
    assert run(ops, "commit", "topic work").ok
    assert run(ops, "checkout", "main").ok
    assert run(ops, "merge", "topic").ok
    assert (repo / "t.txt").exists()
    assert not run(ops, "new-branch", "topic").ok               # already exists

    remote = repo.parent / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    assert run(ops, "fetch").ok
    git(repo, "branch", "--set-upstream-to=origin/main")
    (repo / "u.txt").write_text("u\n")
    assert run(ops, "commit", "ahead").ok
    st = status(ops, force=True)
    assert st.branch == "main" and st.upstream == "origin/main" and st.ahead == 1
    assert run(ops, "push").ok
    assert status(ops, force=True).ahead == 0


def test_status_cached_for_ttl_and_invalidated_by_actions(repo):
    ops = fresh(repo)
    st = status(ops)
    assert st.is_repo and st.branch == "main" and not st.dirty and st.toplevel == str(repo)
    (repo / "a.txt").write_text("dirty\n")
    assert status(ops) is st                     # cache hit: still the clean snapshot
    assert status(ops, force=True).changes == {"a.txt": "M"}
    assert run(ops, "stash").ok                  # mutating action drops the cache
    assert not status(ops).dirty


def test_status_requests_are_coalesced(repo):
    ops = fresh(repo)
    got = []
    for _ in range(3):
        ops.status(got.append)
    assert len(ops.cache.inflight[ops.root]) == 3
    pump(lambda: len(got) == 3)
    assert got[0] is got[1] is got[2]


def test_status_outside_a_repo(tmp_path):
    (tmp_path / "plain").mkdir()
    st = status(GitOps(str(tmp_path / "plain"), cache=GitStatusCache()))
    assert not st.is_repo and st.badge_for(str(tmp_path / "plain" / "x")) is None


def test_diff_shows_uncommitted_changes(repo):
    ops = fresh(repo)
    (repo / "a.txt").write_text("1\n2\n")
    out = []
    ops.diff(out.append)
    pump(lambda: out)
    assert out[0].ok
    kinds = classify(out[0].output)
    assert ("add", "+2") in kinds and kinds[0][0] == "file"


def test_failure_is_reported_not_raised(repo):
    r = run(fresh(repo), "push")  # no remote configured
    assert not r.ok and r.output
    assert not run(fresh(repo), "checkout", "--evil").ok
    assert not run(fresh(repo), "checkout", "nope").ok
