---
title: Remote SSH
section: Guide
order: 70
description: Open a workspace whose terminals run on a remote machine, similar to VS Code Remote-SSH.
---
A **remote workspace** is a workspace whose terminals run on another machine, inside a folder you choose there. Maestrix connects with the system `ssh`, so your keys, agent and `~/.ssh/config` work as usual.

!!! note "Phase A"
    The first phase is delivered: host list, connection test, remote folder browser and remote terminals. The reverse tunnel, the `omaestri` CLI on the remote host, and a remote File Tree are **not** implemented yet. That means agents running on the remote machine cannot use `omaestri` there for now. The design is in [docs/linux-ssh-spec.md](https://github.com/Potatoyz908/Maestrix/blob/main/docs/linux-ssh-spec.md).

## Connect

1. Open **Connect to Host…** from the `+` menu of the sidebar, the File menu or the Add menu.
2. Pick a host. The list comes from your `~/.ssh/config` (patterns with `*` or `?` are skipped). Or choose **Add New SSH Host…** and enter `user@host[:port]` and, optionally, an identity file.
3. Press **Test connection**. It runs `ssh -o BatchMode=yes -o ConnectTimeout=8 … true`. If a key or password is needed, Maestrix tells you the password will be asked in the terminal.
4. Choose the remote folder: type a path or browse (double-click to enter, *Up*, *Home*). If the host needs a password, only the text field is available.
5. Press **Open**. The workspace is created and marked with a network badge and `user@host` in the sidebar.

## How terminals start

A remote terminal runs `ssh -tt` to the host with a shared connection (`ControlMaster`, kept for 10 minutes), then `cd <folder>` and your login shell, or the command you configured. There is no local shell. Password and passphrase prompts appear in the terminal itself, so nothing is stored or logged.

## Safety

Host, user, port and identity file are validated before any command is built: a host or user may only contain letters, digits and `._@:-` and never starts with `-`, the port must be 1 to 65535, and the identity file must exist. The target is passed after `--` and paths and commands are quoted, so nothing reaches a local shell.

## Settings

*Preferences → General → Remote SSH* has the same fields as the macOS app: enable, tunnel port (7433) and script path.
