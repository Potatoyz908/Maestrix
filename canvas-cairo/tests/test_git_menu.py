"""FileTree git UI: GitBranchButton (branch indicator + Commit/Pull/Push/Checkout/New Branch/Merge/Fetch/Stash menu) and the
colored DiffView, against a real temporary repository. Needs a display; skipped otherwise."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # maestri_core, when run from inside canvas-cairo/

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from maestri_canvas.diff_view import DiffView  # noqa: E402
from maestri_canvas.git_menu import GitBranchButton  # noqa: E402
from maestri_core.gitops import GitStatusCache  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


def pump_until(cond, timeout=10.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.003)
    return cond()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    git(r, "config", "commit.gpgsign", "false")
    (r / "a.txt").write_text("one\n")
    git(r, "add", ".")
    git(r, "commit", "-qm", "init")
    git(r, "branch", "feature")
    return r


@pytest.fixture
def window():
    win = Gtk.Window(default_width=500, default_height=500)
    yield win
    win.destroy()
    pump_until(lambda: False, 0.05)


def make_button(root, window):
    b = GitBranchButton(str(root))
    b.git.cache = GitStatusCache()          # isolate from other tests' cached status
    b.refresh(force=True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(b)
    box.append(b.feedback)
    window.set_child(box)
    window.present()
    assert pump_until(lambda: b.status.is_repo)
    return b


def run(b, action, arg=None):
    done = []
    hid = b.connect("action-finished", lambda _b, r: done.append(r))
    b.run_action(action, arg)
    assert pump_until(lambda: done)
    b.disconnect(hid)
    assert pump_until(lambda: b.busy_action is None)
    return done[0]


def test_indicator_shows_branch_and_dirty_state(repo, window):
    b = make_button(repo, window)
    assert b.get_visible() and b.label.get_label() == "main"
    (repo / "a.txt").write_text("two\n")
    b.refresh(force=True)
    assert pump_until(lambda: b.label.get_label() == "main ●")
    assert "1 changed path" in b.menu_button.get_tooltip_text()


def test_hidden_outside_a_repository(tmp_path, window):
    plain = tmp_path / "plain"
    plain.mkdir()
    b = GitBranchButton(str(plain))
    b.git.cache = GitStatusCache()
    seen = []
    b.connect("status-changed", lambda _b, st: seen.append(st))
    b.refresh(force=True)
    window.set_child(b)
    window.present()
    assert pump_until(lambda: seen)
    assert not b.get_visible()


def test_menu_lists_every_reference_operation_and_branches(repo, window):
    b = make_button(repo, window)
    b._load_branches()
    assert pump_until(lambda: b.branches)
    labels = b.menu_labels()
    for op in ("Commit…", "Pull", "Push", "Fetch", "Checkout", "New Branch…", "Merge", "Stash", "Stash Pop", "Show Changes"):
        assert op in labels
    assert "Checkout/✓ main" in labels and "Checkout/feature" in labels
    assert "Merge/feature" in labels and "Merge/main" not in labels   # never offer merging a branch into itself


def test_checkout_via_menu_action(repo, window):
    b = make_button(repo, window)
    done = []
    b.connect("action-finished", lambda _b, r: done.append(r))
    b.actions.activate_action("checkout", GLib.Variant.new_string("feature"))
    assert pump_until(lambda: done and b.label.get_label() == "feature")
    assert done[0].ok and git(repo, "branch", "--show-current").strip() == "feature"
    assert "✓ checkout" in b.feedback_label.get_label() and b.feedback.get_reveal_child()


def test_new_branch_prompt_validates_and_creates(repo, window):
    b = make_button(repo, window)
    b.prompt("new-branch")
    b.input_entry.set_text("bad name")
    assert not b.input_ok.get_sensitive()
    b.input_entry.set_text("topic/x")
    assert b.input_ok.get_sensitive()
    done = []
    b.connect("action-finished", lambda _b, r: done.append(r))
    b.input_entry.emit("activate")
    assert pump_until(lambda: done and b.label.get_label() == "topic/x")
    assert git(repo, "branch", "--show-current").strip() == "topic/x"


def test_commit_prompt_then_merge(repo, window):
    b = make_button(repo, window)
    assert run(b, "checkout", "feature").ok
    (repo / "f.txt").write_text("feature\n")
    b.prompt("commit")
    assert not b.input_ok.get_sensitive()            # empty message
    b.input_entry.set_text("feat: add f")
    done = []
    b.connect("action-finished", lambda _b, r: done.append(r))
    b.input_ok.emit("clicked")
    assert pump_until(lambda: done)
    assert done[0].ok and git(repo, "log", "-1", "--format=%s").strip() == "feat: add f"
    assert run(b, "checkout", "main").ok
    assert run(b, "merge", "feature").ok and (repo / "f.txt").exists()


def test_stash_fetch_and_failure_feedback(repo, window):
    b = make_button(repo, window)
    (repo / "a.txt").write_text("wip\n")
    assert run(b, "stash").ok and (repo / "a.txt").read_text() == "one\n"
    assert run(b, "stash-pop").ok and (repo / "a.txt").read_text() == "wip\n"
    r = run(b, "push")                                 # no remote configured
    assert not r.ok and "✗ push" in b.feedback_label.get_label()
    assert b.feedback_label.has_css_class("error")
    assert run(b, "fetch").ok                          # no remotes: fetch is a no-op success


def test_actions_do_not_overlap(repo, window):
    b = make_button(repo, window)
    done = []
    b.connect("action-finished", lambda _b, r: done.append(r.action))
    b.run_action("stash")
    assert not b.menu_button.get_sensitive() and b.spinner.get_visible()
    b.run_action("fetch")                              # ignored while busy
    assert pump_until(lambda: done and b.busy_action is None)
    pump_until(lambda: False, 0.2)
    assert done == ["stash"]


def test_set_root_switches_repositories(repo, tmp_path, window):
    b = make_button(repo, window)
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "trunk")
    b.set_root(str(other))
    assert pump_until(lambda: b.label.get_label() == "trunk")


# ---- diff view -------------------------------------------------------------------------------------------------------
def test_diff_view_colors_lines_by_kind(repo, window):
    (repo / "a.txt").write_text("uno\n")
    (repo / "b.txt").write_text("new\n")
    git(repo, "add", "b.txt")
    v = DiffView(str(repo))
    window.set_child(v)
    window.present()
    loaded = []
    v.connect("loaded", lambda _v: loaded.append(1))
    v.reload()
    assert pump_until(lambda: loaded)
    kinds = [k for k, _ in v.lines]
    assert kinds.count("file") == 2 and "add" in kinds and "del" in kinds and "hunk" in kinds
    assert v.summary.get_label() == "2 files, +2 −1"
    buf = v.buffer
    tags = {t.get_property("name") for t in buf.get_iter_at_line(kinds.index("del"))[1].get_tags()}
    assert tags == {"del"}
    tags = {t.get_property("name") for t in buf.get_iter_at_line(kinds.index("add"))[1].get_tags()}
    assert tags == {"add"}


def test_diff_view_clean_tree_and_ask_selection(repo, window):
    v = DiffView(str(repo))
    window.set_child(v)
    window.present()
    loaded = []
    v.connect("loaded", lambda _v: loaded.append(1))
    v.reload()
    assert pump_until(lambda: loaded)
    assert "clean" in v.buffer.get_text(v.buffer.get_start_iter(), v.buffer.get_end_iter(), False)
    assert not v.btn_ask.get_sensitive()

    v.set_diff_text("diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new")
    start = v.buffer.get_iter_at_line(2)[1]
    end = v.buffer.get_end_iter()
    v.buffer.select_range(start, end)
    assert v.btn_ask.get_sensitive()
    asked = []
    v.connect("ask-requested", lambda _v, q: asked.append(q))
    v.btn_ask.emit("clicked")                          # opens the question popover, nothing sent yet
    assert asked == [] and v.ask_popover.get_visible()
    assert v.ask_preview.get_label() == "-old  (+1 lines)"
    v.ask_entry.set_text("Why was this renamed?")
    v.ask_entry.emit("activate")
    assert asked == ["Why was this renamed?\n\n```diff\n-old\n+new\n```"]
    assert not v.ask_popover.get_visible()
    v.btn_ask.emit("clicked")
    v.ask_send.emit("clicked")                         # empty question -> default prompt
    assert asked[-1].startswith("Explain this change:\n\n```diff")
    v.set_ask_status("Sent to Claude.", True)
    assert v.ask_status.get_visible() and v.ask_status.has_css_class("success")
