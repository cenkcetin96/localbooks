"""Assemble a clean, self-contained Apple Silicon app and disk image."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
release = ROOT / 'release'
release.mkdir(exist_ok=True)
app = release / 'LocalBooks.app'
if app.exists():
    shutil.rmtree(app)
resources = app / 'Contents/Resources'
(app / 'Contents/MacOS').mkdir(parents=True)
resources.mkdir()
shutil.copy2('packaging/Info.plist', app / 'Contents/Info.plist')
with (app / 'Contents/Info.plist').open('rb') as stream:
    info = plistlib.load(stream)
info['LSMinimumSystemVersion'] = '15.0'
with (app / 'Contents/Info.plist').open('wb') as stream:
    plistlib.dump(info, stream)
shutil.copy2('packaging/AppIcon.icns', resources)
shutil.copytree('dist/LocalBooksBackend', resources / 'backend', symlinks=True)
for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
    shutil.copy2(name, resources)
shutil.copytree('build/third-party-notices', resources / 'ThirdPartyNotices')
subprocess.run(['swiftc', '-target', 'arm64-apple-macos15.0', 'launcher.swift', '-o', str(app / 'Contents/MacOS/LocalBooks')], check=True)
subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
# Audit every Mach-O, not only the entry points: installed Homebrew must not be required.
for path in app.rglob('*'):
    if not path.is_file() or path.is_symlink():
        continue
    with path.open('rb') as stream:
        magic = stream.read(4)
    if magic not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xce\xfa\xed\xfe'):
        continue
    linked = subprocess.check_output(['otool', '-L', str(path)], text=True)
    assert not any('/opt/homebrew/' in line or '/usr/local/' in line for line in linked.splitlines()[1:]), str(path)
    assert subprocess.check_output(['lipo', '-archs', str(path)], text=True).strip() in ('arm64', 'x86_64 arm64', 'arm64 x86_64'), str(path)
print('All bundled binaries resolve without Homebrew; Apple Silicon architecture verified.')
