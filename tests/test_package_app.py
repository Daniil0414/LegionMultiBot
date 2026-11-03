import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from package_app import SOURCE_FILES, build_package


class PackageTests(unittest.TestCase):
    def test_distribution_ignores_private_state_and_generates_empty_vpn_template(self):
        with tempfile.TemporaryDirectory(prefix='legion-package-test-') as directory:
            root = Path(directory)
            for name in SOURCE_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('public source', encoding='utf-8')
            for name in ('accounts.json', 'userscript_config.json', 'vpn_config.json',
                         '.vpn_runtime/private.json', 'profiles/Cookies', 'debug.log'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('PRIVATE_FIXTURE_VALUE', encoding='utf-8')
            output = build_package(root, root / 'release.zip')
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), set(SOURCE_FILES) | {'vpn_config.json'})
                self.assertEqual(json.loads(archive.read('vpn_config.json'))['raw_nodes_text'], '')
                self.assertFalse(any(b'PRIVATE_FIXTURE_VALUE' in archive.read(name) for name in archive.namelist()))
