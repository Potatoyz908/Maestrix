---
title: Development
section: Project
order: 10
description: Build, test and package Maestrix, and where things live in the repository.
---
## Repository layout

| Path | Contents |
|---|---|
| `gtk4-ui/` | GTK 4 interface: window, sidebar, dialogs, themes, floors |
| `canvas-cairo/` | Cairo canvas engine: nodes, ropes, VTE terminals, portals |
| `maestri_core/` | Python core: models, server client, preferences, sync |
| `Sources/` | Swift server and CLI (`open-maestri-server`, `omaestri`) |
| `Tests/` | Swift tests |
| `tests/` | Python unit, integration and end-to-end (Xvfb) tests |
| `scripts/` | Launcher, desktop install, `.deb` packaging, e2e runner |
| `docs/` | Architecture, roadmap, specs, product reference |
| `site/` | This documentation site |

## Build and run

```bash
swift build -Xswiftc -use-ld=lld              # server and CLI
./scripts/install-desktop.sh                  # menu entry and icons
maestrix                                      # or: scripts/open-maestri
```

## Tests

```bash
swift test -Xswiftc -use-ld=lld                                       # Swift tests
python3 -m pytest tests/unit canvas-cairo/tests tests/integration     # Python tests
scripts/run-e2e-xvfb.sh                                               # UI tests, private Xvfb
```

Guidelines the test suites follow:

- **Isolation.** Tests that start a server or a window set `OPEN_MAESTRI_DATA_DIR` to a temporary folder and give the server a private `HOME`. A guard fails the run if the real `~/.open-maestri` changes.
- **Headless UI.** GUI tests run on a virtual display (`Xvfb`), never on your screen.
- **Regression tests prove themselves.** A test for a bug should fail on the old code. The regression tests in `tests/e2e/test_regression_*` follow that rule.

## Documentation site

This site is generated from Markdown in `site/content` by `site/build.py` and published to GitHub Pages by `.github/workflows/pages.yml`.

```bash
pip install markdown pygments
python3 site/build.py                 # writes site/_site
python3 -m http.server -d site/_site 8000
```

Add a page by creating `site/content/<name>.md` with this front matter:

```yaml
---
title: Page title
section: Guide          # Start, Guide, Reference or Project
order: 30
description: One sentence for search and previews.
---
```

## Packaging

`./scripts/build-deb.sh` builds `releases/maestrix_<version>_amd64.deb` with the binaries, the embedded Swift runtime, the desktop entry and icons, and the `maestrix`, `maestrix-cli` and `maestrix-server` commands.

## Contract notes

A few rules matter when you change the code:

- A terminal node's id **is** its terminal id.
- `workspace.json` is schema version 2 and shares its format with the original Maestri. New optional fields are fine; renaming or removing existing ones is not.
- The server owns every PTY. The interface must never spawn a shell for a connected workspace.
