---
title: CLI reference
section: Reference
order: 10
description: Every omaestri command, grouped by what it does.
---
`omaestri` is injected into every terminal Maestrix creates. `maestrix-cli` and `open-maestri` are aliases. If `omaestri` is not on `PATH` (a shell that resets it), use `"$MAESTRI_CLI"`. Run `omaestri help` for the built-in summary and `omaestri debug` to diagnose a broken setup.

Names are case-sensitive here: run `omaestri list` first and copy the exact name.

## Discovering and talking

| Command | Description |
|---|---|
| `omaestri list` | Terminals, notes and portals connected to the caller. |
| `omaestri ask "Agent" "prompt"` | Send a prompt and wait for the reply (the agent going idle). |
| `omaestri check "Agent" [lines]` | Read the agent's current screen without interrupting it. |

## Notes

| Command | Description |
|---|---|
| `omaestri note create ["content"]` | Create a note linked and connected to this terminal. |
| `omaestri note read "Note" [offset limit]` | Read a note, or `limit` lines from line `offset`. |
| `omaestri note write "Note" "content"` | Replace the whole note. |
| `omaestri note edit "Note" "old" "new"` | Replace a substring in place. |

## Portals

All portal commands need the portal name. `@e1` style references come from `snapshot`.

| Command | Description |
|---|---|
| `omaestri portal create "URL" ["Name"]` | Open a new portal. |
| `omaestri portal navigate "Portal" "URL"` | Go to a URL (`edit --url` also works). |
| `omaestri portal info / snapshot / screenshot / html "Portal"` | Inspect the page. `snapshot` lists elements with references. |
| `omaestri portal text "Portal" @e1` | Text of an element. |
| `omaestri portal click "Portal" @e1` | Click by reference, CSS selector or `"x,y"`. |
| `omaestri portal fill "Portal" @e1 "value"` | Fill an input. |
| `omaestri portal type "Portal" "text"` / `key "Portal" "Enter"` | Type text or press a key. |
| `omaestri portal hover / scroll / drag / wait / evaluate …` | Hover, scroll (`down [amount]`), drag `@e1 @e2`, wait for an element, run JavaScript. |

## Maestro Mode

These need the calling terminal to be in [Maestro Mode](agent-communication.html#maestro-mode).

| Command | Description |
|---|---|
| `omaestri recruit "Name" [--preset P] [--role R] [--command C] [--dir D] [--icon I] [--color C] [--floor F] [--replace]` | Create an agent terminal and connect it. |
| `omaestri dismiss "Name"` | Stop and remove a recruited terminal. |
| `omaestri connect "From" "To"` | Wire two existing terminals. |

## Roles and presets

| Command | Description |
|---|---|
| `omaestri role list` | All roles with a preview of their instructions. |
| `omaestri role show "Role"` | Name and full prompt. |
| `omaestri role create "Role" "instructions"` | Create a role. |
| `omaestri role edit "Role" --prompt "…"` | Change a role (`write` is an alias). |
| `omaestri role assign "Agent" "Role"` | Give an agent a role, effective on its next restart. `--none` clears it. |
| `omaestri preset list` | The agent presets you can pass to `recruit --preset`. |

## Workspaces, floors, routines, partituras

| Command | Description |
|---|---|
| `omaestri workspace list / create "Name" --dir PATH / rename "A" "B" / move "Name" --group G / --root / unload "Name"` | Manage workspaces. |
| `omaestri floor list / create / land / delete / status` | Manage floors. See [Floors](floors.html#from-the-cli). |
| `omaestri routine list / show / create / edit / enable / disable / run / delete` | Scheduled prompts. See [Routines](routines-and-partituras.html#routines). |
| `omaestri partitura list / create / update / insert / delete` | Saved arrangements. See [Partituras](routines-and-partituras.html#partituras). |

## Exit and errors

| Message | Cause |
|---|---|
| `only available inside open-maestri terminals (MAESTRI_SOCKET not set)` | The shell is not a Maestrix terminal, or the app is in Standalone mode. |
| `agent 'X' not found. Use 'omaestri list' …` | No connected terminal with that name. Check the name and the ropes. |
| `this terminal is not in Maestro mode …` | `recruit`, `dismiss` and `connect` need the Maestro toggle. |
| `an agent named 'X' already exists. Use --replace …` | `recruit` refuses a duplicate name. |
