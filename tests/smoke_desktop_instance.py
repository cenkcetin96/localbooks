"""Check native reopen behavior in a temporary profile; never touches real records."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


def wait_for(check):
    for _ in range(150):
        if check():
            return
        time.sleep(.1)
    raise AssertionError('Desktop instance did not become ready')


def main():
    binary = Path(__file__).resolve().parents[1] / 'LocalBooks.app/Contents/MacOS/LocalBooks'
    processes = []
    with tempfile.TemporaryDirectory(prefix='localbooks-desktop-qa-') as temp:
        support = Path(temp)
        runtime = support / 'data/runtime.json'
        lock = support / 'desktop.lock'

        def launch():
            process = subprocess.Popen([str(binary), '--test-support', temp])
            processes.append(process)
            return process

        def owns_lock(process):
            return lock.exists() and lock.read_text().strip() == str(process.pid)

        try:
            first = launch()
            wait_for(lambda: owns_lock(first) and runtime.exists())
            backend = json.loads(runtime.read_text())['pid']
            duplicates = [launch() for _ in range(3)]
            for process in duplicates:
                assert process.wait(timeout=15) == 0, 'Duplicate launch failed'
            assert first.poll() is None and owns_lock(first), 'Original app was replaced'
            first.terminate()
            first.wait(timeout=10)
            os.kill(backend, 0)
            with lock.open('r+') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            reopened = launch()
            wait_for(lambda: owns_lock(reopened))
            assert reopened.poll() is None, 'Reopened app exited'
            assert json.loads(runtime.read_text())['pid'] == backend, 'Reopen created another server'
            print('Passed: three duplicate opens reuse one desktop instance; closing releases the lock; reopening reuses one server.')
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            if runtime.exists():
                os.kill(json.loads(runtime.read_text())['pid'], signal.SIGINT)
                wait_for(lambda: not runtime.exists())


if __name__ == '__main__':
    main()
