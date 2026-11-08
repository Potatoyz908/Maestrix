---
title: Getting started
section: Start
order: 10
description: Install Maestrix, open it for the first time and connect two agents.
---
## Requirements

- Ubuntu 22.04 or newer (x86_64). Other Debian-based distributions should work but are not the tested target.
- For the `.deb` package: nothing else. It ships the binaries and the Swift runtime.
- To build from source: Swift 6, Python 3.10+ with PyGObject, GTK 4, Cairo, VTE (GTK 4 build) and WebKitGTK. The full list is in the [Linux guide](https://github.com/Potatoyz908/Maestrix/blob/main/README.linux.md).

## Install

### Option 1: the `.deb` package

```bash
sudo apt install ./releases/maestrix_1.0.0_amd64.deb
```

You can also download the package from the [Releases](https://github.com/Potatoyz908/Maestrix/releases) page. Afterwards, search for **Maestrix** in the application menu, or run:

```bash
maestrix
```

### Option 2: build from source

```bash
git clone https://github.com/Potatoyz908/Maestrix.git
cd Maestrix
swift build -Xswiftc -use-ld=lld
./scripts/install-desktop.sh
maestrix
```

!!! note "Why `-use-ld=lld`?"
    On Linux the default linker fails to link some of the Swift targets. Passing `-Xswiftc -use-ld=lld` works for both `swift build` and `swift test`.

The `maestrix` launcher starts the background server when it is not running and then opens the interface. If the server dies while the app is open, the app starts it again (at most once a minute).

To build the `.deb` yourself: `./scripts/build-deb.sh`.

## First run

1. **Create a workspace.** Press <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>N</kbd> or the `+` in the sidebar. Choose a name, an icon and the working folder.
2. **Add a terminal.** Press <kbd>T</kbd> on the canvas, or <kbd>Ctrl</kbd>+<kbd>N</kbd>, or use the toolbar. In the dialog pick a preset: Claude Code, Codex, Gemini CLI, Antigravity, OpenCode or Shell.
3. **Add a second terminal** and **connect** the two: select a node and press <kbd>L</kbd>, or drag from one node to another.
4. **Talk across the rope.** In the first terminal:

```bash
omaestri list
omaestri ask "Terminal 2" "echo hello from the other side"
```

`omaestri list` shows the terminals, notes and portals connected to the one you are in. `omaestri ask` types the message into the other terminal and returns what comes back once it goes idle.

!!! warning "Only inside Maestrix terminals"
    `omaestri` works only in terminals created by Maestrix. In any other shell it prints `only available inside open-maestri terminals (MAESTRI_SOCKET not set)`. See [Troubleshooting](troubleshooting.html).

## Where your data lives

Maestrix keeps everything in `~/.open-maestri` (workspaces, notes, preferences, terminal history). The format is the same as the original Maestri, so no migration is needed. To try Maestrix without touching your real data, point `OPEN_MAESTRI_DATA_DIR` at an empty folder. See [Configuration](configuration.html).
