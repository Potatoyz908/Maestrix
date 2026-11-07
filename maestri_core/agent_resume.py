"""Relaunch an agent CLI so it picks its last conversation up instead of opening an empty chat."""
from __future__ import annotations

import os
import shlex

# executable -> (flags to insert right after it, flags that mean the command already resumes/continues)
_RESUME = {
    "claude": (["--continue"], {"--continue", "-c", "--resume", "-r"}),
    "agy": (["--continue"], {"--continue", "-c", "--conversation"}),
    "opencode": (["--continue"], {"--continue", "-c", "--session", "-s"}),
    "codex": (["resume", "--last"], {"resume"}),
}


def resume_command(command: str) -> str:
    """`claude` -> `claude --continue`, `agy --dangerously-skip-permissions` -> `agy --continue --dangerously-skip-permissions`,
    `codex` -> `codex resume --last`; each falls back to the original command when there is nothing to continue. Commands that are not a known agent, already resume, or are not plain argv stay as they are."""
    try:
        argv = shlex.split(command or "")
    except ValueError:
        return command
    if not argv or any(c in command for c in "|;&<>$`"):
        return command
    spec = _RESUME.get(os.path.basename(argv[0]))
    if spec is None:
        return command
    add, already = spec
    if already & set(argv[1:]):
        return command
    resumed = " ".join(shlex.quote(a) for a in [argv[0], *add, *argv[1:]])
    return f"{resumed} || {command}"   # nothing to continue yet (first run in this folder): start the agent normally
