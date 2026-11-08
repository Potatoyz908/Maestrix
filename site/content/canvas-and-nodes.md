---
title: Canvas and nodes
section: Guide
order: 10
description: The infinite canvas, the node types you can place on it and how to move around.
---
The canvas is an infinite 2D surface. Everything you work with is a **node** placed on it, and nodes can be joined by **ropes**.

![Maestrix canvas with terminals, a note and a file tree](assets/img/agents-dark.png)

## Node types

| Node | What it is | Create it with |
|---|---|---|
| **Terminal** | A shell or an AI agent running in a VTE terminal. | <kbd>T</kbd>, <kbd>Ctrl</kbd>+<kbd>N</kbd>, or the toolbar |
| **Note** | A Markdown sticky note with a raw and a formatted view. It can also point to an existing file. | Toolbar, or drop a text file on the canvas |
| **File Tree** | A folder browser with Git badges. | Toolbar, or drop a folder on the canvas |
| **Portal** | An embedded browser (WebKitGTK) that agents can drive. | <kbd>P</kbd>, or the toolbar |
| **Text label** | Free text on the canvas. | Toolbar |
| **Drawings** | Rectangle, ellipse and diamond shapes. | Toolbar → Draw |

!!! note "Not ported yet"
    The arrow and line drawing tools of the macOS app are not available on Linux yet.

Dropping other files on the canvas (images, PDFs, video) opens them in a read-only portal.

Every node has a header with its icon and title, a close button, and can be moved, resized and locked. A title can be renamed in place. Terminal names are unique within a workspace; see [Terminals and agents](terminals-and-agents.html#unique-names).

## Ropes (connections)

A rope links two nodes. Ropes are drawn with a physics simulation and settle after you move a node. Ropes also carry **permissions**: a terminal can only see and address the terminals, notes and portals it is connected to.

- Select a node and press <kbd>L</kbd> to start a connection, then click the target.
- A terminal can be connected to another terminal, a note, or a portal. Notes and portals can also be linked to each other.

## Navigating

| Action | How |
|---|---|
| Pan | Drag the empty canvas, or scroll |
| Zoom | <kbd>Ctrl</kbd>+scroll, <kbd>Ctrl</kbd>+<kbd>=</kbd>, <kbd>Ctrl</kbd>+<kbd>-</kbd>, reset with <kbd>Ctrl</kbd>+<kbd>0</kbd> |
| Center the selected node | <kbd>\</kbd> |
| Jump to the Nth terminal | <kbd>Ctrl</kbd>+<kbd>1</kbd> … <kbd>9</kbd> |
| Select several nodes | Drag a selection box, or <kbd>Ctrl</kbd>+<kbd>A</kbd> |
| Delete the selection | <kbd>Delete</kbd>, or <kbd>Ctrl</kbd>+<kbd>W</kbd> (from inside a terminal use <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>W</kbd>) |
| Copy / paste elements | <kbd>Ctrl</kbd>+<kbd>C</kbd> / <kbd>Ctrl</kbd>+<kbd>V</kbd>; works across workspaces |

The bottom-right controls hold the **Ground / Floors** button ([Floors](floors.html)), the minimap and the zoom control.

## Alignment and background

While you drag, nodes snap to alignment guides. Toggle snapping with <kbd>Ctrl</kbd>+<kbd>;</kbd> or in *Preferences → General*. The canvas background can be a **dot grid**, a **solid** colour or **transparent**.
