"""Git status badges for the FileTree (M modified, A added, D deleted, R renamed, U conflict, ? untracked).

`parse_porcelain` / `GitBadges` are pure. `GitStatusRunner` runs `git rev-parse --show-toplevel` then
`git status --porcelain=v1 -b -z` through Gio.Subprocess (argv list, no shell, GLib main loop: never blocks the UI) and delivers a
`GitBadges` index. Porcelain paths are always relative to the repository top level, whatever the FileTree root is.

Folders get the strongest badge among their changed descendants; files inside an untracked folder (`?? dir/`, git does not list
them one by one in the default untracked mode) are untracked too.
"""
from __future__ import annotations

import os
from typing import Callable

PRIORITY = {"U": 6, "D": 5, "M": 4, "R": 3, "A": 2, "?": 1}
CSS_CLASS = {"M": "git-modified", "A": "git-added", "D": "git-deleted", "R": "git-renamed", "U": "git-conflict", "?": "git-untracked"}
TOOLTIP = {"M": "Modified", "A": "Added", "D": "Deleted", "R": "Renamed", "U": "Conflict", "?": "Untracked"}


def badge_for_xy(xy: str) -> str | None:
    """Two-letter porcelain status -> one badge. Index and worktree columns are folded (the tree shows one letter)."""
    if xy == "??":
        return "?"
    if xy == "!!":
        return None
    if "U" in xy or xy in ("AA", "DD"):
        return "U"
    for letter in ("D", "R", "A", "M"):
        if letter in xy:
            return letter
    if "C" in xy or "T" in xy:
        return "M"
    return None


def parse_porcelain(data: str) -> dict[str, str]:
    """`git status --porcelain=v1 -z` output -> {repo-relative path: badge}. An untracked folder keeps git's trailing '/'
    ("build/"), which tells GitBadges that everything below it is untracked.
    With -z a rename/copy record is followed by an extra NUL-terminated field (the source path), which is skipped."""
    out: dict[str, str] = {}
    fields = data.split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4 or rec[2] != " ":
            continue
        xy, path = rec[:2], rec[3:]
        if xy[0] in "RC" or xy[1] in "RC":
            i += 1   # skip the original path
        b = badge_for_xy(xy)
        if b:
            out[path] = b
    return out


def parse_branch(data: str) -> str:
    """The branch from the `## branch...upstream [ahead 1]` header that `git status -b` prints first ('' when absent).
    Detached HEAD reads '## HEAD (no branch)'; a fresh repo '## No commits yet on main'."""
    head = data.split("\0", 1)[0]
    if not head.startswith("## "):
        return ""
    h = head[3:]
    if h.startswith("No commits yet on "):
        return h[len("No commits yet on "):]
    if h.startswith("HEAD (no branch)"):
        return "HEAD (detached)"
    return h.split("...", 1)[0].split(" ", 1)[0]


def _stronger(a: str | None, b: str) -> str:
    return b if a is None or PRIORITY.get(b, 0) > PRIORITY.get(a, 0) else a


class GitBadges:
    """Absolute path -> badge, folders included. Empty (no repo / git failed) answers None for everything."""

    def __init__(self, toplevel: str = "", changes: dict[str, str] | None = None, branch: str = "") -> None:
        self.branch = branch
        self.toplevel = os.path.realpath(toplevel) if toplevel else ""
        self._untracked_dirs: set[str] = set()
        self._files: dict[str, str] = {}
        self._folders: dict[str, str] = {}
        for rel, b in (changes or {}).items():
            p = os.path.join(self.toplevel, rel.rstrip("/"))
            if rel.endswith("/"):
                self._untracked_dirs.add(p)
            self._files[p] = b
            parent = os.path.dirname(p)
            while parent and parent.startswith(self.toplevel) and parent != p:
                self._folders[parent] = _stronger(self._folders.get(parent), b)
                if parent == self.toplevel:
                    break
                p, parent = parent, os.path.dirname(parent)

    def __bool__(self) -> bool:
        return bool(self._files)

    def __len__(self) -> int:
        return len(self._files)

    def badge(self, path: str, is_dir: bool = False) -> str | None:
        p = os.path.normpath(path)   # callers pass resolved paths (FileTreeView resolves its root); no syscall per row bind
        if not self.toplevel or not (p == self.toplevel or p.startswith(self.toplevel + os.sep)):
            return None
        own = self._files.get(p)
        if is_dir:
            own = self._folders.get(p) if own is None else _stronger(self._folders.get(p), own)
        if own is not None:
            return own
        parent = os.path.dirname(p)
        while parent.startswith(self.toplevel) and len(parent) >= len(self.toplevel):
            if parent in self._untracked_dirs:
                return "?"
            if parent == self.toplevel:
                break
            parent = os.path.dirname(parent)
        return None


class GitStatusRunner:
    """Async status for `root`. `refresh(cb)` coalesces: while one run is in flight a second request just re-runs once after it."""

    def __init__(self, git_bin: str = "git") -> None:
        self.git_bin = git_bin
        self._busy = False
        self._again: tuple[str, Callable] | None = None

    def refresh(self, root: str, cb: Callable[[GitBadges], None]) -> None:
        if self._busy:
            self._again = (root, cb)
            return
        self._busy = True

        def finish(badges: GitBadges) -> None:
            self._busy = False
            cb(badges)
            if self._again is not None:
                again, self._again = self._again, None
                self.refresh(*again)

        def got_top(ok: bool, out: str) -> None:
            if not ok or not out.strip():
                finish(GitBadges())
                return
            top = out.strip().splitlines()[0]
            self._run([self.git_bin, "-C", top, "status", "--porcelain=v1", "-b", "-z"],
                      lambda ok2, out2: finish(GitBadges(top, parse_porcelain(out2), parse_branch(out2)) if ok2 else GitBadges()))

        self._run([self.git_bin, "-C", root, "rev-parse", "--show-toplevel"], got_top)

    @staticmethod
    def _run(argv: list[str], cb: Callable[[bool, str], None]) -> None:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        launcher.setenv("GIT_OPTIONAL_LOCKS", "0", True)   # a status refresh must never take index.lock from the user's git
        launcher.setenv("GIT_TERMINAL_PROMPT", "0", True)
        try:
            proc = launcher.spawnv(argv)
        except GLib.Error:
            GLib.idle_add(lambda: (cb(False, ""), False)[1])
            return

        # Binary communicate: `-z` output is NUL-separated and communicate_utf8 hands back a C string cut at the first NUL.
        def done(p, res):
            try:
                _ok, out, _err = p.communicate_finish(res)
            except GLib.Error:
                cb(False, "")
                return
            data = bytes(out.get_data()) if out is not None else b""
            cb(p.get_successful(), data.decode("utf-8", errors="surrogateescape"))

        proc.communicate_async(None, None, done)
