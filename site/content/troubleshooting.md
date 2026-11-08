---
title: Troubleshooting
section: Reference
order: 30
description: Offline mode, missing servers, agents that cannot reach each other and other common problems.
---
## The header says "Standalone"

Standalone means the interface cannot reach the server. In that state:

- the **Ground / Floors** button is disabled and clicking it does nothing,
- terminals are plain local shells **without** `MAESTRI_SOCKET`, so `omaestri` fails inside them,
- leaving a workspace ends its local shells.

What to do:

1. Open Maestrix with the `maestrix` launcher (or from the application menu), not by running `app.py` directly. The launcher starts the server and exports what the app needs to restart it.
2. Check that a server is running: `pgrep -a open-maestri-server`, and that `~/.open-maestri/run/server.port` exists.
3. Read the log: `tail ~/.open-maestri/run/server.log`.
4. If the server died while the app was open, wait up to a minute: the app notices within seconds and restarts it when started through the launcher.

Terminals created while the app was in Standalone mode stay without the variables. Close them and create new ones after the app is connected.

## `omaestri: only available inside open-maestri terminals`

You ran `omaestri` in a shell that Maestrix did not create, or in a terminal made in Standalone mode. Open a terminal from the canvas after the header shows `Porta <number>` (connected).

## `agent 'Name' not found`

The terminals are not connected, or the name differs. Run `omaestri list` in the calling terminal and use the name exactly as printed. Names are unique per workspace, so a duplicate may have become `Name #2`.

## A Maestro does not recruit

- The terminal must have the **Maestro** toggle on. Otherwise commands answer *"this terminal is not in Maestro mode"*.
- If you also have the official Maestri skills installed, the agent may follow those and call `maestri` instead of `omaestri`. Tell the agent to use `omaestri` or `"$MAESTRI_CLI"`.
- Restart the Maestrix server once after an update so the refreshed skills are written.

## An agent opens with an empty chat

Maestrix resumes the last conversation only for `claude`, `agy`, `codex` and `opencode`, and only when the shell of that terminal no longer exists. With two agents of the same kind in one folder, both resume the same conversation. See [Terminals and agents](terminals-and-agents.html#resuming-a-conversation).

## Stale socket after a reboot

If `~/.open-maestri/run/agent.sock` is left over from a dead server, the launcher checks that something is really listening on it, removes the stale file and starts a fresh server. If you start the server by hand, delete the file first.

## An older server keeps running

A server from before `run/server.port` existed cannot be found by a newer app. The launcher detects this (a live server without that file) and replaces the server that owns the data directory's socket. To do it yourself: stop that `open-maestri-server` process and start `maestrix` again.

## The build fails at link time

Use the `lld` linker: `swift build -Xswiftc -use-ld=lld` and `swift test -Xswiftc -use-ld=lld`.

## The theme looks different from the screenshots

A custom GTK theme (for example WhiteSur) can override some colours. Maestrix applies its own interface theme on top, but if a widget still looks off, test with the default GTK theme to confirm and open an issue with a screenshot.

## Still stuck?

Run `omaestri debug` inside a Maestrix terminal and attach its output, together with `server.log`, to an [issue](https://github.com/Potatoyz908/Maestrix/issues).
