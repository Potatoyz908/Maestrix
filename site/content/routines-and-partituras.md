---
title: Routines and partituras
section: Guide
order: 50
description: Schedule prompts for agents and save or reapply a whole arrangement of agents.
---
## Routines

A routine is a command or prompt sent to a terminal on a schedule: a recurring check, a reminder, a nightly task. Routines live in `~/.open-maestri/routines.json` and run while the server is up.

```bash
omaestri routine list
omaestri routine show "Standup"
omaestri routine create "Standup" --command "summarize yesterday's commits" --every 30m --terminal "Planner" [--disabled]
omaestri routine edit "Standup" [--name "New"] [--command "..."] [--every 1h] [--terminal "Name"]
omaestri routine enable|disable|run|delete "Standup"      # run = run it now
```

`--every` accepts `45s`, `30m`, `2h`, `1h30m` or a bare number of minutes.

The routine manager window in the app lists, pauses, resumes and runs routines too. Before delivering a prompt the scheduler waits for the target agent to be idle.

## Partituras

A **partitura** is a saved arrangement: the agents on the canvas, their roles and their connections. Save the team that works well for one kind of task and apply it again in any workspace.

```bash
omaestri partitura list
omaestri partitura create "Review team" ["Agent" ...]    # save you and your team (alias: save)
omaestri partitura update "Review team" ["Agent" ...]
omaestri partitura insert "Review team" [--workspace W] [--at X,Y]    # alias: apply
omaestri partitura delete "Review team"
```

`create` saves the calling terminal and the agents connected to it, or the agents you name.

## Workspace commands

Agents can also manage workspaces:

```bash
omaestri workspace list
omaestri workspace create "New project" --dir ~/code/new-project
omaestri workspace rename "New project" "Project"
omaestri workspace move   "Project" --group "Group name"   # or --root
omaestri workspace unload "Project"
```
