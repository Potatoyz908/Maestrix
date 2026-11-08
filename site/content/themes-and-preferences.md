---
title: Themes and preferences
section: Guide
order: 80
description: The Dark and Light interface themes, every preference and the keyboard shortcuts.
---
Open preferences with <kbd>Ctrl</kbd>+<kbd>,</kbd> or the gear in the header. Changes apply immediately and are saved to `~/.open-maestri/preferences.json`.

## Interface theme

*Preferences → General → Interface theme*

| Theme | Description |
|---|---|
| **Dark · Maestrix** | Navy ground with the palette of the app icon: blue `#168bf1` to purple `#9354e8` gradient for selections and primary actions, cyan `#71dcec` for focus and the active tool. The canvas background and grid are dark too. |
| **Light** | The base theme. |

![Maestrix in the Light theme](assets/img/home-light.png)

The terminal colour theme is a separate setting, so you can keep, for instance, a light terminal on the dark interface.

## Preferences

| Tab | Setting | Values |
|---|---|---|
| General | Language | English, 简体中文 (applies on restart) |
| General | Canvas background | Dot grid, Solid, Transparent |
| General | Preferred IDE | Cursor, VS Code, Xcode |
| General | Autosave interval | 5 to 600 seconds (default 30) |
| General | Snap to alignment guides | On / off |
| General | Interface theme | Light, Dark · Maestrix |
| General | Remote SSH | Enable, tunnel port (7433), script path |
| Terminal | Font family and size | Any monospace font, 8 to 32 pt |
| Terminal | Colour theme | system, dark, light, Dracula, Solarized dark/light, Nord, One Dark, Tokyo Night (and Day), Catppuccin (Mocha, Macchiato, Latte), Gruvbox dark/light, Rosé Pine |
| Terminal | Scrollback memory limit | On / off |
| Agents | Presets | Name, command, icon, colour and agent type |

## Keyboard shortcuts

**Workspaces**

| Action | Keys |
|---|---|
| Next / previous workspace | <kbd>Ctrl</kbd>+<kbd>↓</kbd> / <kbd>Ctrl</kbd>+<kbd>↑</kbd> |
| New workspace | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>N</kbd> |

**Elements**

| Action | Keys |
|---|---|
| Center the selected node | <kbd>\</kbd> |
| Start a connection | <kbd>L</kbd> |
| Delete the selected node | <kbd>Ctrl</kbd>+<kbd>W</kbd> or <kbd>Delete</kbd> |
| Jump to terminal 1 to 9 | <kbd>Ctrl</kbd>+<kbd>1</kbd> … <kbd>9</kbd> |

**Canvas**

| Action | Keys |
|---|---|
| Select tool / Terminal tool / Browser tool | <kbd>V</kbd> / <kbd>T</kbd> / <kbd>P</kbd> |
| Zoom in / out / reset | <kbd>Ctrl</kbd>+<kbd>=</kbd> / <kbd>Ctrl</kbd>+<kbd>-</kbd> / <kbd>Ctrl</kbd>+<kbd>0</kbd> |
| Zoom with the wheel | <kbd>Ctrl</kbd>+scroll |
| Snap to alignment guides | <kbd>Ctrl</kbd>+<kbd>;</kbd> |
| Copy / paste element | <kbd>Ctrl</kbd>+<kbd>C</kbd> / <kbd>Ctrl</kbd>+<kbd>V</kbd> |

**Terminal**

| Action | Keys |
|---|---|
| Copy / paste | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>C</kbd> / <kbd>V</kbd> |
| Select all | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>A</kbd> |
| Find | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>F</kbd> |

**App**

| Action | Keys |
|---|---|
| New terminal | <kbd>Ctrl</kbd>+<kbd>N</kbd> |
| Save | <kbd>Ctrl</kbd>+<kbd>S</kbd> |
| Open in editor | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>E</kbd> |
| Toggle sidebar | <kbd>Ctrl</kbd>+<kbd>B</kbd> |
| Open settings | <kbd>Ctrl</kbd>+<kbd>,</kbd> |
| Quit | <kbd>Ctrl</kbd>+<kbd>Q</kbd> |
