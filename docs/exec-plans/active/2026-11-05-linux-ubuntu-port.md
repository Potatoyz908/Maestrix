# Exec Plan: Linux Ubuntu Port of Open-Maestri

- **Date:** 2026-11-05
- **Branch:** `feat/linux-port`
- **Owner / Coordinator:** Maestro
- **Team:**
  - Arquiteto (Claude Code / Sonnet 5.5) — Tech Lead & Core Architecture
  - Pixel (Claude Code / Sonnet 5.5) — UI, Viewport & Canvas Specialist
  - Sentinela (Antigravity / Gemini 3.8 Flash) — IPC, PTY & POSIX Backend
  - Forja (Antigravity / Gemini 3.8 Flash) — Toolchain, Build System & CI

---

## Problem Statement

Open-Maestri is currently tied exclusively to macOS (14.0+), depending heavily on AppKit (`NSView`, `NSHostingView`, `NSEvent`, `NSColor`), SwiftUI, Sparkle (auto-updates), CoreSpotlight, and macOS-specific SwiftTerm views.

To run Open-Maestri on Linux (Ubuntu), ~30,000 LOC across 190 files must be unblocked. 108 files import macOS-only frameworks. Furthermore, the Linux host currently lacks a Swift toolchain and GTK development libraries.

## Intended End State

1. **Modular Architecture**:
   - `OpenMaestriCore`: Pure headless Swift (Foundation + Glibc) target compiling on both macOS and Linux. Houses models (`CanvasNode`, `WorkspaceDocument`), persistence (`PersistenceManager`, atomic writes with `schemaVersion: 2` compatibility), IPC server (`InterAgentServer` speaking HTTP `/cli` and Unix Domain Sockets), CLI router, PTY process management, and Git/Floors logic.
   - Platform abstractions behind protocols: `PlatformPaths`, `ShellResolver`, `Updater`, `Notifier`, `Indexer`, `FileWatcher`.
2. **Data Compatibility**:
   - Strict binary and schema compatibility with Maestri v0.25.4 (`schemaVersion: 2`): frames stored as `[[x, y], [w, h]]` arrays and `NodeContent` wrapped as `{ "type": { "_0": ... } }`.
3. **Linux Presentation Layer**:
   - Decoupled web shell (TypeScript canvas engine + xterm.js terminal integration) communicating with `OpenMaestriCore` via local HTTP and WebSocket, avoiding the complete rewrite of terminal emulation in native GTK.
4. **Build & CI**:
   - `Package.swift` modularized with `#if os(macOS)` and `#if os(Linux)` conditionals.
   - Compiles cleanly on Ubuntu using Swift 5.10 / 6.0 in user space.

## Verification Path

- Toolchain verification:
  ```bash
  swift --version
  ```
- Core library compilation:
  ```bash
  swift build --target OpenMaestriCore
  swift build --target omaestri
  ```
- Test suite verification:
  ```bash
  swift test --filter OpenMaestriTests
  ```
- IPC and CLI end-to-end test:
  ```bash
  omaestri ask --help
  ```

## Phased Execution Roadmap

- [x] **Stage 0: Team Setup & Feature Branching**
  - Branch `feat/linux-port` initialized.
  - Multi-agent orchestration setup on Maestri with Claude Code and Gemini 3.8 agents.
- [x] **Stage 1: Complete Dependency & Architecture Analysis**
  - Codebase survey completed (Arquiteto + Pixel + Sentinela + Forja).
  - Web shell + headless `OpenMaestriCore` strategy aligned.
- [ ] **Stage 2: Toolchain Setup & Package Modularization**
  - Install Swift toolchain in user space (`~/.swift` or `~/.local/share/swiftly`).
  - Modularize `Package.swift` into `OpenMaestriCore`, conditionalizing macOS-only dependencies.
- [ ] **Stage 3: POSIX & IPC Porting**
  - Replace macOS `Network.framework` (`NWListener`) with POSIX sockets.
  - Adapt `SwiftTermProvider` PTY handling for Linux (`/bin/bash`, Linux pseudo-terminals).
  - Port `PersistenceManager` atomic file writes and path resolution (`~/.open-maestri/`).
- [ ] **Stage 4: Linux Viewport & Canvas Integration**
  - Port canvas geometry (`CanvasHitTesting`, `TileSnapping`, `RopeSimulation`, `ResizeEdge`) to TypeScript/Canvas.
  - Embed `xterm.js` for terminals and provide bridge to `OpenMaestriCore`.
- [ ] **Stage 5: Integration Testing & Pull Request**
  - Run test suite and manual verification.
  - Prepare and submit PR from `feat/linux-port` to `main`.

## Open Risks or Blockers

- Host Swift toolchain installation in non-root user space on Ubuntu.
- SwiftTerm PTY compatibility under Linux POSIX needs validation once Swift compiles.
