"""Keep component licenses and exact corresponding source beside binary releases."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
notices = root / 'build/third-party-notices'
sources = root / 'build/third-party-sources'
notices.mkdir(parents=True, exist_ok=True)
sources.mkdir(parents=True, exist_ok=True)
components = json.loads((root / 'build/components.json').read_text())


def collect(item):
    name, directory = item
    prefix = Path(directory)
    recipe = next((prefix / '.brew').glob('*.rb'))
    text = recipe.read_text()
    url = re.search(r'^\s*url "([^"]+)"', text, re.M)[1]
    checksum = re.search(r'^\s*sha256 "([a-f0-9]{64})"', text, re.M)[1]
    assert '#{' not in url, f'Unresolved source URL for {name}'
    target = sources / name
    target.mkdir(exist_ok=True)
    shutil.copy2(recipe, target / recipe.name)
    source = target / url.rsplit('/', 1)[-1]
    subprocess.run(['curl', '--fail', '--location', '--retry', '4', '--retry-all-errors', '--max-time', '300', '--output', str(source), url], check=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum, f'Source checksum mismatch: {name}'
    dest = notices / name
    dest.mkdir(exist_ok=True)
    pattern = re.compile(r'(license|copying|copyright|notice|authors)', re.I)
    for path in prefix.iterdir():
        if path.is_file() and pattern.search(path.name):
            shutil.copy2(path, dest / path.name)
    # Retain license texts even where the binary package did not install them.
    if tarfile.is_tarfile(source):
        with tarfile.open(source) as archive:
            for member in archive:
                if member.isfile() and pattern.search(Path(member.name).name) and member.size < 5_000_000:
                    content = archive.extractfile(member).read()
                    digest = hashlib.sha256(member.name.encode()).hexdigest()[:8]
                    (dest / (digest + '-' + Path(member.name).name)).write_bytes(content)
    record = dict(name=name, version=prefix.name, source_url=url, sha256=checksum)
    (dest / 'component.json').write_text(json.dumps(record, indent=2))
    print('Source and licenses retained:', name, flush=True)
    return record


with ThreadPoolExecutor(max_workers=6) as pool:
    manifest = list(pool.map(collect, sorted(components.items())))
(sources / 'components.json').write_text(json.dumps(manifest, indent=2))
(notices / 'components.json').write_text(json.dumps(manifest, indent=2))
(sources / 'README.txt').write_text('Exact upstream sources and Homebrew build recipes for the separately bundled tools and libraries.\nLocalBooks application source: https://github.com/cenkcetin96/localbooks/tree/v1.1.0\nLibraries were relocated and ad-hoc signed by PyInstaller; upstream source was not edited.\nBuild: packaging/release.spec, packaging/build_release.py and .github/workflows/mac-release.yml in the application repository.\n')
(root / 'release').mkdir(exist_ok=True)
with zipfile.ZipFile(root / 'release/LocalBooks-1.1.0-third-party-sources.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
    for path in sources.rglob('*'):
        if path.is_file():
            archive.write(path, path.relative_to(sources))
# PyInstaller distributes its bootloader under an exception that allows MIT applications.
import PyInstaller
for path in Path(PyInstaller.__file__).resolve().parents[1].glob('pyinstaller-*.dist-info/licenses/*'):
    if path.is_file():
        (notices / ('PyInstaller-' + path.name)).write_bytes(path.read_bytes())
