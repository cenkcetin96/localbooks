"""Retain Chromium provenance and the bundled LGPL FFmpeg source revision."""
from pathlib import Path
import base64
import hashlib
import json
import re
import subprocess
import urllib.request


def collect(destination, version):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    deps_url = f'https://chromium.googlesource.com/chromium/src/+/refs/tags/{version}/DEPS?format=TEXT'
    with urllib.request.urlopen(deps_url, timeout=60) as response:
        deps = base64.b64decode(response.read()).decode()
    (destination / 'Chromium-DEPS.txt').write_text(deps)
    revision = re.search(r"['\"]ffmpeg_revision['\"]\s*:\s*['\"]([a-f0-9]{40})['\"]", deps)[1]
    url = f'https://chromium.googlesource.com/chromium/third_party/ffmpeg/+archive/{revision}.tar.gz'
    source = destination / ('chromium-ffmpeg-' + revision + '.tar.gz')
    subprocess.run(['curl', '--fail', '--location', '--retry', '4', '--retry-all-errors', '--max-time', '300', '--output', str(source), url], check=True)
    manifest = dict(chromium_version=version, ffmpeg_revision=revision, ffmpeg_source=url,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        chromium_source=f'https://chromium.googlesource.com/chromium/src/+/refs/tags/{version}/',
        build_instructions='https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md')
    (destination / 'browser-components.json').write_text(json.dumps(manifest, indent=2))
    (destination / 'README.txt').write_text('Chromium headless shell is the separately bundled PDF engine. Its license and third-party notices are in the app at backend/_internal/browser/LICENSE.headless_shell.\nThe archive here contains the exact FFmpeg source revision referenced by Chromium DEPS. The complete Chromium source and Mac build instructions are linked in browser-components.json; use fetch chromium, gclient sync --revision src@' + version + ', gn gen out/Release, then autoninja -C out/Release headless_shell to build a modified engine. Replace the bundled browser directory with your compatible build and ad-hoc sign your modified app.\n')


if __name__ == '__main__':
    import sys
    collect(sys.argv[1], sys.argv[2])
