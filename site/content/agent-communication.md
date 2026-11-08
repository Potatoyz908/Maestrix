---
title: Agent communication
section: Guide
order: 30
description: How connected agents delegate work to each other, and how Maestro Mode lets one agent assemble a team.
---
## Talking through ropes

Connect two terminals and each becomes visible to the other. From inside a terminal:

```bash
omaestri list                               # who and what is connected to me
omaestri ask "Reviewer" "Review my PR"      # send a prompt and wait for the reply
omaestri check "Builder"                    # read the other agent's current screen
```

- **`ask`** types the prompt into the target terminal, waits until the agent goes idle, and prints what it answered. If your tool's timeout expires first, do not send the prompt again: use `check` to see progress, then wait.
- **`check`** reads the target's output without interrupting it. An optional line count limits it: `omaestri check "Builder" 40`.
- **`list`** prints the terminals, notes and portals connected to the caller. Always run it first to get the exact names.

Connections are the access control. A terminal that is not connected to another cannot ask or check it.

## Notes as shared memory

A note connected to a terminal is readable and writable by that agent:

```bash
omaestri note create "initial content"         # new note, linked to this terminal
omaestri note read "Spec"                      # whole note
omaestri note read "Spec" 10 20                # 20 lines starting at line 10
omaestri note write "Spec" "new content"       # replace everything
omaestri note edit "Spec" "old text" "new text"   # replace a substring
```

## Portals

Agents can drive a connected browser portal: navigate, take a snapshot of the page structure with element references (`@e1`), click, fill forms, press keys, scroll, take screenshots and evaluate JavaScript. See [Portal commands](cli-reference.html#portals).

## Maestro Mode

Maestro Mode turns a terminal into a **lead** that can build its own team. Enable it with the **Maestro** toggle when you create the terminal (or edit it later).

A Maestro terminal can:

| Command | Effect |
|---|---|
| `omaestri recruit "Name" --preset "Claude Code" --role "Reviewer"` | Create a new agent terminal on the canvas and connect it |
| `omaestri connect "A" "B"` | Wire two existing terminals together |
| `omaestri dismiss "Name"` | Stop a recruited terminal and remove it |
| `omaestri role …`, `omaestri preset list` | Manage roles and see the available presets |

Recruiting is refused when an agent with that name already exists, unless you pass `--replace`. Commands other than from a Maestro terminal answer *"this terminal is not in Maestro mode"*.

The usual flow the lead follows:

1. `omaestri list` to see whether a teammate already covers the job.
2. `omaestri preset list` and `omaestri role list` to pick a preset and a role.
3. `omaestri recruit "Name" --preset … --role …`.
4. `omaestri ask "Name" "the task"` and read the reply, or `omaestri check "Name"` to follow progress.
5. `omaestri dismiss "Name"` when the work is done.

### Skills

For agents to know all this without being told, Maestrix installs skill files (`open-maestri`, `open-maestri-manager`, `open-maestri-portal`, `open-maestri-routines`, `open-maestri-workspace`) into the skill folders of the agents you use, for example `~/.claude/skills` and `~/.gemini/antigravity-cli/skills`. The server refreshes them at start when their content changed.

!!! note "Skills from the macOS Maestri"
    If you also have the official Maestri skills (`maestri`, `maestri-manager`) installed, an agent may read those first. Their CLI is named `maestri`, not `omaestri`. If an agent tries the wrong CLI, tell it to use `omaestri` or `"$MAESTRI_CLI"`.
