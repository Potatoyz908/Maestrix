"""
Async Git actions (pull / push / checkout / stash) for the FileTree toolbar.

Runs `git` through Gio.Subprocess (no shell, argv list only) so the GTK main
loop never blocks. Results are delivered to a callback on the main thread as
GitResult(ok, action, output).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class GitResult:
    ok: bool
    action: str
    output: str


def _valid_ref(name: str) -> bool:
    # Never let a ref be parsed as an option or contain whitespace/control chars.
    return bool(name) and not name.startswith("-") and not any(c.isspace() or ord(c) < 32 for c in name) and ".." not in name


class GitOps:
    def __init__(self, root: str, git_bin: str = "git") -> None:
        self.root = root
        self.git_bin = git_bin

    # -- action argv builders (pure, unit-testable) -------------------------
    @staticmethod
    def argv_for(action: str, arg: Optional[str] = None) -> List[str]:
        if action == "pull":
            return ["pull", "--ff-only"]
        if action == "push":
            return ["push"]
        if action == "checkout":
            if arg is None or not _valid_ref(arg):
                raise ValueError(f"invalid branch name: {arg!r}")
            return ["switch", arg]
        if action == "stash":
            return ["stash", "push", "--include-untracked"]
        if action == "stash-pop":
            return ["stash", "pop"]
        if action == "branches":
            return ["branch", "--format=%(refname:short)"]
        raise ValueError(f"unknown git action: {action!r}")

    # -- async execution -----------------------------------------------------
    def run(self, action: str, callback: Callable[[GitResult], None], arg: Optional[str] = None) -> None:
        try:
            argv = [self.git_bin, "-C", self.root] + self.argv_for(action, arg)
        except ValueError as exc:
            self._fail_later(callback, action, str(exc))
            return
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"  # fail instead of hanging on credential prompts
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
        )
        launcher.set_environ([f"{k}={v}" for k, v in env.items()])
        try:
            proc = launcher.spawnv(argv)
        except GLib.Error as exc:
            self._fail_later(callback, action, exc.message)
            return

        cancellable = Gio.Cancellable()
        fired = {"v": False}

        def on_timeout():
            fired["v"] = True
            proc.force_exit()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add_seconds(TIMEOUT_SECONDS, on_timeout)

        def done(p, res):
            if not fired["v"]:
                GLib.source_remove(timeout_id)
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error as exc:
                callback(GitResult(False, action, exc.message))
                return
            callback(GitResult(p.get_successful(), action, (out or "").strip()))

        proc.communicate_utf8_async(None, cancellable, done)

    @staticmethod
    def _fail_later(callback, action: str, message: str) -> None:
        """Deliver an error result asynchronously (keeps callbacks always-async)."""
        def deliver():
            callback(GitResult(False, action, message))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)

    def list_branches(self, callback: Callable[[List[str]], None]) -> None:
        self.run("branches", lambda r: callback([b for b in r.output.splitlines() if b] if r.ok else []))
