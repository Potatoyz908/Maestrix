---
title: Files, notes and Git
section: Guide
order: 60
description: The File Tree node with Git status, and Markdown notes that live on the canvas.
---
![Notes and file tree on the canvas](assets/img/agents-dark.png)

## File Tree

A File Tree node browses a folder, starting from the workspace folder by default.

- **List** and **grid** views, with navigation buttons, an *up* button and a hidden-files toggle.
- **Git integration**: the header shows the current branch with a marker when the tree is dirty. Files carry badges for **modified**, **added**, **deleted**, **renamed**, **conflict** and **untracked**, and you can open a diff.
- Open a file in your preferred editor (*Preferences → General → Preferred IDE*) with <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>E</kbd>.
- Right-click for file operations such as creating, renaming and deleting.
- The tree is also an input for agents: a selected file or folder can be sent to a connected agent as an *ask*.

Drop a folder from your file manager onto the canvas to create a File Tree rooted there.

## Notes

A Note is a Markdown sticky note. Switch between **Raw** (editable text) and **Formatted** (rendered Markdown). Pick a colour from the strip at the bottom.

- By default a note is stored inside the workspace (`~/.open-maestri/workspaces/<id>/notes/`).
- A note can also be backed by an existing file: drop a text or Markdown file on the canvas.
- Connected agents read and write notes with `omaestri note`, which makes notes a convenient shared memory between agents. See [Agent communication](agent-communication.html#notes-as-shared-memory).

## Portals

A Portal is an embedded web view. Use it to keep a running app, documentation or a dashboard next to the agents. Connected agents can drive it with `omaestri portal …`; see the [CLI reference](cli-reference.html#portals).
