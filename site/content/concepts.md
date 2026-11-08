---
title: How it works
section: Start
order: 20
description: The processes behind Maestrix, who owns what, and how agents reach each other.
---
## The three parts

```
┌──────────────────────────────┐        ┌────────────────────────────────────┐
│  GTK 4 + Cairo interface     │  REST  │  Swift server                      │
│  canvas, ropes, terminals    │◄──────►│  workspaces, terminals (PTY),      │
│  (VTE), notes, files,        │  WS    │  connections, floors, routines,    │
│  portals (WebKitGTK)         │        │  SSH                               │
└──────────────────────────────┘        └──────────────┬─────────────────────┘
                                                       │ Unix socket + HTTP
                                                       ▼
                                     ┌─────────────────────────────────────┐
                                     │  omaestri (CLI) inside each         │
                                     │  terminal: list · ask · check · …   │
                                     └─────────────────────────────────────┘
```

| Part | Technology | Responsibility |
|---|---|---|
| Interface | Python, GTK 4, Cairo, VTE, WebKitGTK | Draws the canvas and ropes, shows terminals, notes, files and portals, runs dialogs and preferences. |
| Server | Swift (`open-maestri-server`) | Owns shells (PTYs), stores workspaces, applies connection permissions, runs floors, routines and SSH. |
| CLI | Swift (`omaestri`) | Runs inside terminals. Sends commands to the server, which routes them to other terminals. |

## The server owns the shells

A terminal on the canvas is a view of a PTY that **belongs to the server**. The interface connects to it over a WebSocket and the server replays the saved screen on every connection. Consequences:

- Switching workspaces, or closing and reopening the window, does not kill a running shell or an agent.
- Terminal history survives restarts of the interface.
- If the server itself restarts, shells are gone. Maestrix then relaunches agents on their last conversation. See [Terminals and agents](terminals-and-agents.html#resuming-a-conversation).

## How the interface finds the server

The server binds a dynamic port and writes it to `~/.open-maestri/run/server.port`. The interface reads that file, and re-reads it when the connection drops. While connected, the interface checks the server every few seconds; two misses in a row switch it to **Standalone** mode and it tries to reconnect.

## How agents reach each other

When the server creates a terminal it sets these variables inside it:

| Variable | Meaning |
|---|---|
| `MAESTRI_SOCKET` | Unix socket of the server. |
| `MAESTRI_TERMINAL_ID` | Identifies this terminal. Node id and terminal id are the same value. |
| `MAESTRI_SERVER_PORT`, `MAESTRI_AUTH_TOKEN` | Loopback port and token for HTTP access. |
| `MAESTRI_CLI` | Absolute path to `omaestri`, used when `PATH` is reset. |

`omaestri` sends each command together with the caller's terminal id. The server checks the ropes of that terminal's workspace and only lets it see and address the terminals, notes and portals **connected to it**.

## Data model

A workspace is stored as `workspace.json` (schema version 2): a list of nodes (`terminal`, `stickyNote`, `portal`, `fileTree`, drawings), the connections between them, canvas origin and zoom, and floors. Node frames are `[[x, y], [width, height]]`, and node content uses the wrapper `{ "terminal": { "_0": { … } } }`. This is the format of the original Maestri, which is why the two apps can share data.

## Standalone mode

Without a server the interface still opens. It shows **Standalone** in the header, terminals are plain local shells without the `MAESTRI_*` variables, and Floors are disabled. Edits are saved to disk locally and synced when the server is back. See [Troubleshooting](troubleshooting.html#the-header-says-standalone).
