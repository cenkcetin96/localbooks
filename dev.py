#!/usr/bin/env python3
"""Run LocalBooks from source and restart it when backend files change."""
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
data = Path.home() / 'Library/Application Support/LocalBooks/data'
env = dict(os.environ, LOCALBOOKS_DEV_TOKEN=secrets.token_urlsafe(32))

def backend_version():
    digest = hashlib.sha256()
    for path in sorted([*root.glob('*.py'), root / 'pdf.mjs']):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.digest()

version = backend_version()
first = True
while True:
    command = [sys.executable, str(root / 'app.py'), '--data', str(data), '--dev']
    if first:
        command.append('--open')
        first = False
    process = subprocess.Popen(command, cwd=root, env=env)
    try:
        while process.poll() is None:
            time.sleep(0.5)
            current = backend_version()
            if current != version:
                version = current
                process.terminate()
                process.wait()
                break
        else:
            break  # Normal quit or startup error; don't silently restart.
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        break
