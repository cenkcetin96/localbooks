# Build with: LOCALBOOKS_BROWSER=/path/to/chrome-headless-shell-mac-arm64 pyinstaller packaging/release.spec
import json
import os
from pathlib import Path

root = Path(SPECPATH).parent
browser = Path(os.environ['LOCALBOOKS_BROWSER']).resolve()
assert (browser / 'chrome-headless-shell').is_file(), 'Set LOCALBOOKS_BROWSER to the Chromium headless-shell directory'
analysis = Analysis([str(root / 'app.py')], pathex=[str(root)],
    binaries=[(('/opt/homebrew/opt/node@24/bin/node' if name == 'node' else '/opt/homebrew/bin/' + name), 'tools') for name in ('node', 'pdfinfo', 'pdftotext', 'pdftoppm', 'tesseract')],
    datas=[(str(root / name), name) for name in ('static', 'ocr')] + [(str(root / 'pdf.mjs'), '.'), (str(browser), 'browser')],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
# Record exact Homebrew components for license notices and corresponding-source archives.
components = {}
for _, source, _ in analysis.binaries:
    path = Path(source).resolve()
    parts = path.parts
    if 'Cellar' in parts:
        index = parts.index('Cellar')
        components[parts[index+1]] = str(Path(*parts[:index+3]))
(root / 'build').mkdir(exist_ok=True)
(root / 'build/components.json').write_text(json.dumps(components, indent=2))
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='LocalBooksBackend', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True, target_arch='arm64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='LocalBooksBackend')
