# Make LocalBooks your own

Use the ready-made app if you want to start your books today. Use this guide if you want to change how LocalBooks works, including with AI coding tools such as Codex, Claude Code or Cursor. The MIT license allows you to fork, modify and redistribute the application code; bundled components keep their own licenses.

## Start with a safe demo

Fork this repository, open your copy in your coding tool and tell it the business workflow you want to improve. Ask it to use a separate demo profile, keep real customer and bank records out of Git, and run the existing tests before changing calculations, payments or backups.

Example prompt:

> Help me customize LocalBooks for my business. First read the README and existing code. Use a separate demo database, never my live records. Make the smallest change that solves my workflow and run the relevant tests.

## Run from source

The desktop build currently targets **Apple Silicon Macs** with Homebrew at `/opt/homebrew`. It needs Python 3.11, Node.js, Google Chrome, Poppler, Tesseract and Apple's command-line developer tools. Intel Macs require adjusting the hardcoded Homebrew paths in `launcher.swift`, `documents.py` and `scan.py`. The downloadable release bundles these tools; source development uses the installations below.

Install [Homebrew](https://brew.sh) if needed, then run:

```sh
xcode-select --install
brew install python@3.11 node poppler tesseract
brew install --cask google-chrome
git clone https://github.com/cenkcetin96/localbooks.git
cd localbooks
./build_app.sh
open LocalBooks.app
```

If a tool is already installed, skip its installation step. Thai and English OCR models are bundled. The build signs the app locally; it is not Apple notarized. The runtime uses Python's standard library and a dependency-free Node PDF helper.

To try the source edition in a browser with a separate, empty profile:

```sh
/opt/homebrew/bin/python3.11 app.py --data "$HOME/Library/Application Support/LocalBooks-demo/data" --port 8766 --open
```

The desktop app uses `~/Library/Application Support/LocalBooks/data`. If you already use LocalBooks, that path contains your existing records: use the demo command above to evaluate this edition separately. `dev.py` also uses the normal LocalBooks data directory.


## Build the downloadable Mac app

The **Build Mac app** workflow builds on a clean Apple Silicon Mac, bundles Python, Node, Chromium headless shell, PDF tools and Thai/English OCR, runs isolated app checks and publishes the tested disk image. End users install by dragging LocalBooks to Applications.

Build files are `packaging/release.spec`, `packaging/build_release.py` and `.github/workflows/mac-release.yml`. Set `LOCALBOOKS_BROWSER` to a Playwright Chromium headless-shell directory when building locally. `packaging/collect_notices.py` records exact component versions and retains upstream sources, checksums, licenses and Homebrew build recipes alongside the binary release.

The download is ad-hoc signed and is not Apple notarized. A Developer ID certificate and notarization are required to remove the first-launch Open Anyway step.
