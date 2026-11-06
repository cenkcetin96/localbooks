from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import local_tools


class LocalToolsTests(unittest.TestCase):
    def test_release_tools_take_priority_over_host_installations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            (root / 'browser').mkdir()
            (root / 'tools/node').touch()
            (root / 'browser/chrome-headless-shell').touch()
            with patch.object(local_tools, 'ROOT', root), patch('shutil.which', return_value='/host/node'):
                self.assertEqual(local_tools.tool('node'), str(root / 'tools/node'))
                self.assertEqual(local_tools.browser(), str(root / 'browser/chrome-headless-shell'))

    def test_source_tools_can_use_installed_commands(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_tools, 'ROOT', Path(directory)), patch('shutil.which', return_value='/host/tesseract'):
            self.assertEqual(local_tools.tool('tesseract'), '/host/tesseract')
