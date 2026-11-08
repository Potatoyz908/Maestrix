---
title: Floors
section: Guide
order: 40
description: Isolated git worktrees for each task, with hooks, and how to land the work back.
---
A **floor** is an isolated copy of your repository, created as a git worktree on its own branch. Terminals started inside a floor work on that branch only, so several agents can change the same project at once without stepping on each other.

The **Ground** is the original repository. The button in the bottom-right controls shows where you are: *Ground*, or the name of the active floor.

!!! note "Needs the server"
    Floors are driven by the server. In **Standalone** mode the button is disabled. See [Troubleshooting](troubleshooting.html#the-header-says-standalone).

## Create a floor

1. Click the Ground button and choose **New Floor…**.
2. Type a name. The branch name is filled in as `floor/<name>`, and you can change it.
3. Create. A worktree is added and terminals you open inside it start in that folder.

The floor popover lists *Ground* and every floor. Right-click a floor to rename it, configure its hooks, land it or delete it.

## Hooks

Each floor can run commands at three moments, each a list of shell commands:

| Phase | When |
|---|---|
| `setup` | After the floor is created (install dependencies, copy `.env`, …) |
| `run` | On demand, to start the project |
| `teardown` | Before the floor is removed |

Open **Configure Hooks…** from the popover to edit and run them. Terminals inside a floor receive the variables `MAESTRI_FLOOR_NAME` and `MAESTRI_FLOOR_PATH`.

## Land

**Land…** brings the commits of the active floor back into the Ground repository. It is only available from a floor, never from the Ground. The dialog shows why a landing is blocked when it is (for example uncommitted changes) and lets you pick the target branch.

## From the CLI

```bash
omaestri floor list
omaestri floor create "fix-login" [--from PATH] [--branch NAME] [--existing-branch] [--partitura P]
omaestri floor land   "fix-login" [--into BRANCH] [--delete-branch] [--keep-floor]
omaestri floor delete "fix-login" [--keep-branch] [--keep-folder]
omaestri floor status "Reproduced the bug" --state working --progress 2/5
```

`floor status` is for the agent working on a floor to post what it is doing. `--state` is one of `working`, `blocked`, `review` or `done`; `--progress` takes a fraction such as `2/5`, and `--clear` removes the line. `--partitura` on `create` applies a saved arrangement to the new floor (see [Routines and partituras](routines-and-partituras.html#partituras)).
