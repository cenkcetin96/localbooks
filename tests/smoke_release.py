"""Validate the distributable with no Homebrew commands in PATH or real records."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
app = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'release/LocalBooks.app'
backend = app / 'Contents/Resources/backend/_internal'
assert not list(app.rglob('*.sqlite3')), 'The download must contain no business database'
for name in ('node', 'pdfinfo', 'pdftotext', 'pdftoppm', 'tesseract'):
    assert (backend / 'tools' / name).is_file(), name
assert (backend / 'browser/chrome-headless-shell').is_file()
assert (backend / 'ocr/tessdata/tha.traineddata').is_file()
env = dict(os.environ, PATH='/usr/bin:/bin')
with tempfile.TemporaryDirectory(prefix='localbooks-release-test-') as directory:
    support = Path(directory)
    runtime = support / 'data/runtime.json'
    process = subprocess.Popen([str(app / 'Contents/MacOS/LocalBooks'), '--test-support', directory, '--no-open'], env=env)
    try:
        assert process.wait(timeout=30) == 0
        for _ in range(200):
            if runtime.exists():
                break
            time.sleep(.1)
        assert runtime.exists(), 'Packaged server failed to start'
        db = sqlite3.connect(support / 'data/localbooks.sqlite3')
        assert db.execute('SELECT count(*) FROM companies').fetchone()[0] == 0
        db.close()
        (root / 'output/verification').mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(root / 'tests/smoke_package.py'), directory], check=True, env=env)
        db = sqlite3.connect(support / 'data/localbooks.sqlite3')
        pdf = db.execute('SELECT pdf FROM invoices LIMIT 1').fetchone()[0]
        db.close()
        source = support / 'synthetic-invoice.pdf'
        source.write_bytes(pdf)
        text = subprocess.check_output([str(backend / 'tools/pdftotext'), str(source), '-'], env=env, text=True)
        assert 'Package QA company' in text, text
        subprocess.run([str(backend / 'tools/pdfinfo'), str(source)], env=env, check=True)
        image = support / 'scan.png'
        subprocess.run(['/usr/bin/sips', '-s', 'format', 'png', str(source), '--out', str(image)], check=True, env=env)
        ocr = subprocess.check_output([str(backend / 'tools/tesseract'), str(image), 'stdout', '--tessdata-dir', str(backend / 'ocr/tessdata'), '-l', 'tha+eng'], env=env, text=True)
        assert len(ocr.strip()) > 30, 'Bundled OCR produced no readable text'
        print('Passed: empty first launch, bundled PDF generation, invoice/receipt/payment/backup flows, PDF text extraction and English/Thai OCR with no Homebrew in PATH.')
    finally:
        if runtime.exists():
            try:
                os.kill(json.loads(runtime.read_text())['pid'], signal.SIGINT)
            except ProcessLookupError:
                pass
