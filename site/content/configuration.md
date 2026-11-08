---
title: Configuration
section: Reference
order: 20
description: The data directory, the files Maestrix writes, preferences and environment variables.
---
## Data directory

Everything lives in `~/.open-maestri`, or in the folder named by `OPEN_MAESTRI_DATA_DIR` when that variable is set.

```
~/.open-maestri/
├── manifest.json            # index of workspaces
├── sidebar-layout.json      # groups and order of the sidebar
├── preferences.json         # your preferences (see below)
├── app-state.json           # includes the cleanShutdown flag used for crash recovery
├── routines.json            # scheduled routines
├── roles/                   # role presets
├── skills/                  # skills written by the server
├── notes/                   # notes not tied to a workspace folder
├── run/
│   ├── agent.sock           # Unix socket of the server
│   ├── auth.token           # token for HTTP access (mode 0600)
│   ├── server.port          # dynamic port of the server
│   └── server.log           # server output
└── workspaces/<UUID>/
    ├── workspace.json       # nodes, connections, canvas origin and zoom, floors
    ├── notes/               # notes of this workspace (Markdown)
    ├── terminals/           # saved scrollback per terminal
    └── snapshots/
```

The format is compatible with the original Maestri. Writes are atomic (a temporary file is written and swapped in), and the file layout of `workspace.json` is part of that compatibility, so do not rename keys by hand.

## Environment variables

### For you

| Variable | Effect |
|---|---|
| `OPEN_MAESTRI_DATA_DIR` | Use another data directory. The right way to try Maestrix, or to run tests, without touching your real data. |
| `MAESTRI_SERVER_PORT` | Force the port the interface connects to. Normally not needed: the port is read from `run/server.port`. |
| `MAESTRI_AUTH_TOKEN` | Override the token otherwise read from `run/auth.token`. |
| `OMAESTRI_PATH` | Path of the `omaestri` binary to expose inside terminals. |
| `OPEN_MAESTRI_SSH_CONFIG` | Alternate ssh config for the host list (tests). |
| `OPEN_MAESTRI_SSH_BIN` | Alternate `ssh` executable (tests use a fake one). |

### Set by Maestrix

The launcher exports `OPEN_MAESTRI_SERVER_BIN` so the app can restart the server. Inside terminals, the server sets `MAESTRI_SOCKET`, `MAESTRI_TERMINAL_ID` (also `OMAESTRI_TERMINAL_ID`), `MAESTRI_SERVER_PORT`, `MAESTRI_AUTH_TOKEN`, `MAESTRI_CLI` (also `OMAESTRI_CLI`), and `MAESTRI_FLOOR_NAME` / `MAESTRI_FLOOR_PATH` inside a floor.

## preferences.json

```json
{
  "schemaVersion": 1,
  "interfaceTheme": "dark",
  "terminalTheme": "dark",
  "terminalFontFamily": "system",
  "terminalFontSize": 13,
  "canvasBackground": "dotGrid",
  "canvasSnapping": true,
  "preferredIDE": "cursor",
  "language": "en",
  "autosaveIntervalSeconds": 30,
  "scrollbackMemoryLimit": true,
  "optionAsMeta": false
}
```

Unknown keys (agent presets, role presets, skill paths and anything written by other versions) are preserved when Maestrix saves. Invalid values fall back to the default. `interfaceTheme` is `light` (default) or `dark`.

## Ports and security

The server listens on `127.0.0.1` only, on a dynamic port, and on the Unix socket. HTTP requests need the bearer token from `run/auth.token`. Requests must come from loopback hosts and origins. The `omaestri` CLI identifies itself with its terminal id and the server enforces the connection graph for every command.
