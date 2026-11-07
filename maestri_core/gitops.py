"""
Async Git operations for the FileTree branch menu (spec: docs/reference/maestri-reference-index.md §7, "Git 操作菜单").

Actions: Commit / Pull / Push / Checkout / New Branch / Merge / Fetch / Stash (+ Stash Pop), plus the read-only
queries the UI needs: branch list, `status` (branch, ahead/behind, per-file badges) and `diff` (uncommitted changes).

Everything runs through Gio.Subprocess (argv list only, never a shell) so the GTK main loop never blocks; results are
delivered to a callback on the main context as GitResult(ok, action, output). Callbacks are ALWAYS asynchronous, even for
validation errors and cache hits.

`status` is cached per repository root for STATUS_TTL_SECONDS (60 s): every FileTree row needs it, and several trees on
the canvas may point at the same repo. Concurrent requests for the same root share one `git` run, and every action that
can change the working tree or refs invalidates the cache.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

TIMEOUT_SECONDS = 120
STATUS_TTL_SECONDS = 60.0

# Badge priority when a folder summarises its changed descendants (U = unmerged/conflict).
BADGE_PRIORITY = {"U": 6, "D": 5, "M": 4, "R": 3, "A": 2, "?": 1}

# Actions that only read: they never invalidate the status cache.
READ_ONLY_ACTIONS = frozenset({"branches", "current-branch", "toplevel", "status", "diff", "diff-index"})

_BAD_REF_CHARS = set("~^:?*[\\")


@dataclass(frozen=True)
class GitResult:
    ok: bool
    action: str
    output: str
    timed_out: bool = False


def _valid_ref(name: Optional[str]) -> bool:
    """Conservative subset of `git check-ref-format --branch`: never parseable as an option, no whitespace/control chars,
    no `..`, no characters git forbids in ref names, no `.lock` suffix, no leading/trailing slash or dot."""
    if not name or name.startswith("-") or ".." in name or "@{" in name:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in _BAD_REF_CHARS for c in name):
        return False
    if name.startswith("/") or name.endswith("/") or name.endswith(".") or name.endswith(".lock") or "//" in name:
        return False
    return not any(part.startswith(".") for part in name.split("/"))


# ---------------------------------------------------------------------------------------------------------------------
# status model + porcelain parser (pure)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GitStatus:
    is_repo: bool
    toplevel: str = ""
    branch: str = ""             # "" when detached
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    changes: Dict[str, str] = field(default_factory=dict)   # path relative to toplevel ("dir/" for untracked dirs) -> badge
    error: str = ""

    @property
    def dirty(self) -> bool:
        return bool(self.changes)

    def badge_for(self, abs_path: str, is_dir: bool = False) -> Optional[str]:
        """Badge for a file, or the strongest badge among a folder's changed descendants. Files inside an untracked folder
        (reported once as "dir/") are untracked too."""
        if not self.is_repo or not self.toplevel:
            return None
        rel = os.path.relpath(abs_path, self.toplevel)
        if rel == "." or rel.startswith(".." + os.sep) or rel == "..":
            return None
        rel = rel.replace(os.sep, "/")
        found: List[str] = []
        for path, badge in self.changes.items():
            if path.endswith("/"):            # untracked directory
                if rel == path[:-1] or rel.startswith(path):
                    found.append(badge)
                elif is_dir and path.startswith(rel + "/"):
                    found.append(badge)
            elif path == rel:
                found.append(badge)
            elif is_dir and path.startswith(rel + "/"):
                found.append(badge)
        return max(found, key=lambda b: BADGE_PRIORITY.get(b, 0)) if found else None


def _badge(xy: str) -> str:
    x, y = xy[0], xy[1]
    if xy == "??":
        return "?"
    if "U" in xy or xy in ("AA", "DD"):
        return "U"
    if "D" in xy:
        return "D"
    if "R" in xy or "C" in xy:
        return "R"
    if x == "A":
        return "A"
    return "M"


def parse_branch_header(line: str) -> Tuple[str, str, int, int, bool]:
    """`## main...origin/main [ahead 1, behind 2]` -> (branch, upstream, ahead, behind, detached)."""
    head = line[3:] if line.startswith("## ") else line
    ahead = behind = 0
    if head.endswith("]") and " [" in head:
        head, _, counts = head[:-1].rpartition(" [")
        for part in counts.split(", "):
            kind, _, n = part.partition(" ")
            if kind == "ahead" and n.isdigit():
                ahead = int(n)
            elif kind == "behind" and n.isdigit():
                behind = int(n)
    if head.startswith("No commits yet on "):
        return head[len("No commits yet on "):], "", 0, 0, False
    if head.startswith("Initial commit on "):
        return head[len("Initial commit on "):], "", 0, 0, False
    if head.startswith("HEAD (no branch)"):
        return "", "", 0, 0, True
    branch, _, upstream = head.partition("...")
    return branch, upstream, ahead, behind, False


def parse_porcelain(output: str, toplevel: str = "") -> GitStatus:
    """Parse `git status --porcelain=v1 -z --branch` output. Rename/copy entries carry the old path as an extra field."""
    fields = output.split("\0")
    branch = upstream = ""
    ahead = behind = 0
    detached = False
    changes: Dict[str, str] = {}
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        if entry.startswith("## "):
            branch, upstream, ahead, behind, detached = parse_branch_header(entry)
            continue
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if xy[0] in "RC":
            i += 1    # skip the original path
        changes[path] = _badge(xy)
    return GitStatus(True, toplevel, branch, upstream, ahead, behind, detached, changes)


# ---------------------------------------------------------------------------------------------------------------------
# status cache
# ---------------------------------------------------------------------------------------------------------------------
class GitStatusCache:
    """root -> (timestamp, GitStatus) with a TTL; also tracks in-flight requests so callers can coalesce."""

    def __init__(self, ttl: float = STATUS_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self.clock = clock
        self._entries: Dict[str, Tuple[float, GitStatus]] = {}
        self.inflight: Dict[str, List[Callable[[GitStatus], None]]] = {}

    @staticmethod
    def _key(root: str) -> str:
        return os.path.realpath(root)

    def get(self, root: str) -> Optional[GitStatus]:
        hit = self._entries.get(self._key(root))
        if hit is None:
            return None
        ts, status = hit
        if self.clock() - ts >= self.ttl:
            self._entries.pop(self._key(root), None)
            return None
        return status

    def put(self, root: str, status: GitStatus) -> None:
        self._entries[self._key(root)] = (self.clock(), status)

    def invalidate(self, root: Optional[str] = None) -> None:
        """Drop one root, or everything. A repo can be reached from several roots (subfolders), so mutating actions
        drop every entry that shares the same toplevel."""
        if root is None:
            self._entries.clear()
            return
        key = self._key(root)
        top = self._entries.get(key, (0, GitStatus(False)))[1].toplevel
        for k in list(self._entries):
            if k == key or (top and self._entries[k][1].toplevel == top):
                del self._entries[k]


STATUS_CACHE = GitStatusCache()


# ---------------------------------------------------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------------------------------------------------
class GitOps:
    def __init__(self, root: str, git_bin: str = "git", cache: Optional[GitStatusCache] = None) -> None:
        self.root = root
        self.git_bin = git_bin
        self.cache = cache if cache is not None else STATUS_CACHE

    # -- action argv builders (pure, unit-testable) -------------------------
    @staticmethod
    def steps_for(action: str, arg: Optional[str] = None) -> List[List[str]]:
        """The git invocations (without `git -C root`) an action runs, in order; each must succeed before the next."""
        if action == "commit":
            if arg is None or not arg.strip():
                raise ValueError("commit message is empty")
            # stage everything (the menu has no staging UI), then commit; --message= keeps a leading "-" a value
            return [["add", "--all"], ["commit", f"--message={arg}"]]
        return [GitOps.argv_for(action, arg)]

    @staticmethod
    def argv_for(action: str, arg: Optional[str] = None) -> List[str]:
        if action == "pull":
            return ["pull", "--ff-only"]
        if action == "push":
            return ["push"]
        if action == "fetch":
            return ["fetch", "--prune"]
        if action in ("checkout", "new-branch", "merge"):
            if arg is None or not _valid_ref(arg):
                raise ValueError(f"invalid branch name: {arg!r}")
            if action == "checkout":
                return ["switch", arg]
            if action == "new-branch":
                return ["switch", "--create", arg]
            return ["merge", "--no-edit", arg]
        if action == "commit":
            return GitOps.steps_for("commit", arg)[-1]
        if action == "stash":
            return ["stash", "push", "--include-untracked"]
        if action == "stash-pop":
            return ["stash", "pop"]
        if action == "branches":
            return ["branch", "--format=%(refname:short)"]
        if action == "current-branch":
            return ["branch", "--show-current"]
        if action == "toplevel":
            return ["rev-parse", "--show-toplevel"]
        if action == "status":
            return ["status", "--porcelain=v1", "-z", "--branch"]
        if action == "diff":
            return ["diff", "--no-color", "--no-ext-diff", "HEAD", "--"]
        if action == "diff-index":   # repo without commits: staged + unstaged against the empty tree
            return ["diff", "--no-color", "--no-ext-diff", "--cached", "--"]
        raise ValueError(f"unknown git action: {action!r}")

    # -- async execution -----------------------------------------------------
    def _spawn(self, argv_tail: List[str], done: Callable[[bool, str], None]) -> None:
        argv = [self.git_bin, "-c", "color.ui=false", "-c", "core.quotepath=false", "-C", self.root] + argv_tail
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"     # fail instead of hanging on credential prompts
        env["GIT_EDITOR"] = "true"           # never open an editor (merge messages etc.)
        env.setdefault("LC_ALL", "C.UTF-8")  # stable, parseable messages
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.set_environ([f"{k}={v}" for k, v in env.items()])
        try:
            proc = launcher.spawnv(argv)
        except GLib.Error as exc:
            self._later(lambda: done(False, exc.message))
            return

        timed_out = {"v": False}

        def on_timeout():
            timed_out["v"] = True
            proc.force_exit()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add_seconds(TIMEOUT_SECONDS, on_timeout)

        def finished(p, res):
            if not timed_out["v"]:
                GLib.source_remove(timeout_id)
            try:
                _ok, out, _err = p.communicate_finish(res)
            except GLib.Error as exc:
                done(False, exc.message, False)
                return
            text = out.get_data().decode("utf-8", "replace") if out is not None else ""
            if timed_out["v"]:
                done(False, f"timed out after {TIMEOUT_SECONDS}s", True)
                return
            done(p.get_successful(), text, False)

        proc.communicate_async(None, None, finished)

    def run(self, action: str, callback: Callable[[GitResult], None], arg: Optional[str] = None) -> None:
        try:
            steps = self.steps_for(action, arg)
        except ValueError as exc:
            self._fail_later(callback, action, str(exc))
            return
        mutating = action not in READ_ONLY_ACTIONS
        outputs: List[str] = []

        def step(i: int) -> None:
            def done(ok: bool, out: str, timed_out: bool = False) -> None:
                outputs.append(out.rstrip("\n") if action in ("diff", "diff-index") else out.strip())
                if ok and i + 1 < len(steps):
                    step(i + 1)
                    return
                if mutating:
                    self.cache.invalidate(self.root)
                callback(GitResult(ok, action, "\n".join(o for o in outputs if o), timed_out=timed_out))
            self._spawn(steps[i], done)

        step(0)

    def status(self, callback: Callable[[GitStatus], None], force: bool = False) -> None:
        """Branch + per-file badges. Served from the 60 s cache unless `force`; concurrent calls share one git run."""
        if not force:
            hit = self.cache.get(self.root)
            if hit is not None:
                self._later(lambda: callback(hit))
                return
        waiting = self.cache.inflight.get(self.root)
        if waiting is not None:
            waiting.append(callback)
            return
        self.cache.inflight[self.root] = [callback]

        def deliver(status: GitStatus) -> None:
            if status.is_repo:
                self.cache.put(self.root, status)
            for cb in self.cache.inflight.pop(self.root, []):
                cb(status)

        def got_top(ok: bool, out: str, *_) -> None:
            if not ok:
                deliver(GitStatus(False, error=out.strip()))
                return
            top = out.strip()
            self._spawn(self.argv_for("status"),
                        lambda ok2, out2, *_: deliver(parse_porcelain(out2, top) if ok2 else GitStatus(False, top, error=out2.strip())))

        self._spawn(self.argv_for("toplevel"), got_top)

    def diff(self, callback: Callable[[GitResult], None]) -> None:
        """Unified diff of every uncommitted change (staged + unstaged) against HEAD; falls back to the index diff in a
        repository that has no commit yet."""
        def first(r: GitResult) -> None:
            if r.ok:
                callback(r)
            else:
                self.run("diff-index", lambda r2: callback(GitResult(r2.ok, "diff", r2.output)))
        self.run("diff", first)

    def list_branches(self, callback: Callable[[List[str]], None]) -> None:
        self.run("branches", lambda r: callback([b for b in r.output.splitlines() if b] if r.ok else []))

    @staticmethod
    def _later(fn: Callable[[], None]) -> None:
        def deliver():
            fn()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)

    @classmethod
    def _fail_later(cls, callback, action: str, message: str) -> None:
        """Deliver an error result asynchronously (keeps callbacks always-async)."""
        cls._later(lambda: callback(GitResult(False, action, message)))
