import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Handler, Store


class RestoreUploadTests(unittest.TestCase):
    def test_binary_restore_validates_and_keeps_a_safety_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save_party('companies', dict(name='Original', code='OLD'))
            backup = store.backup()
            store.save_party('companies', dict(name='Added later', code='NEW'))
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.store, server.token = store, 'restore-test'
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def upload(content, confirmation='RESTORE'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                connection.request('POST', '/api/restore', content, {
                    'Cookie': 'localbooks=restore-test',
                    'Origin': f'http://127.0.0.1:{server.server_port}',
                    'Content-Type': 'application/vnd.sqlite3',
                    'X-LocalBooks-Confirmation': confirmation,
                })
                response = connection.getresponse()
                result = response.status, json.loads(response.read())
                connection.close()
                return result

            try:
                self.assertEqual(upload(backup, 'NO')[0], 400)
                self.assertEqual(upload(b'not a backup')[0], 400)
                self.assertEqual(len(store.state()['companies']), 2)
                status, result = upload(backup)
                self.assertEqual(status, 200)
                self.assertTrue(Path(result['safety_copy']).is_file())
                self.assertEqual(len(store.state()['companies']), 1)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
                store.db.close()
