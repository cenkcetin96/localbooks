"""Use release-bundled tools first; retain Homebrew paths for source development."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def tool(name):
    bundled = ROOT / 'tools' / name
    return str(bundled) if bundled.is_file() else (shutil.which(name) or '/opt/homebrew/bin/' + name)


def browser():
    bundled = ROOT / 'browser' / 'chrome-headless-shell'
    return str(bundled) if bundled.is_file() else '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
