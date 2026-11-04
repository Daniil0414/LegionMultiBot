"""Exercise actual Git index and historical blobs using synthetic credentials."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_repository.py'
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location('repository_privacy', SCRIPT)
    privacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(privacy)


@unittest.skipUnless(SCRIPT.exists(), 'Repository-only development tool')
class RepositoryPrivacyTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)

    def repository(self, folder):
        root = Path(folder)
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.name', 'Privacy Test')
        self.git(root, 'config', 'user.email', 'test@example.invalid')
        return root

    def test_index_rejects_private_state_even_if_force_added(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            (root / '.gitignore').write_text('accounts.json\n')
            (root / 'accounts.json').write_text('[]')
            self.git(root, 'add', '-f', 'accounts.json')
            _, findings = privacy.check(root, 'staged')
            self.assertEqual(findings, [('accounts.json', 'private configuration')])

    def test_history_catches_secret_removed_from_latest_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            token = 'gh' + 'p_' + 'a' * 36
            (root / 'settings.txt').write_text(token)
            self.git(root, 'add', '.')
            self.git(root, 'commit', '-qm', 'Synthetic historical credential')
            (root / 'settings.txt').write_text('safe configuration')
            self.git(root, 'commit', '-qam', 'Remove synthetic credential')
            self.assertEqual(privacy.check(root, 'staged')[1], [])
            self.assertIn(('settings.txt', 'GitHub token'), privacy.check(root, 'history')[1])

    def test_source_and_public_fixtures_are_allowed(self):
        for name in ['app_paths.py', 'tests/fixtures/mine-bot-v9.1.8.user.js', '.env.example', 'vendor/acorn.js']:
            self.assertIsNone(privacy.path_rule(name))

    def test_history_checks_every_path_for_identical_blobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.repository(folder)
            (root / 'readme.txt').write_text('[]')
            (root / 'accounts.json').write_text('[]')
            self.git(root, 'add', '.')
            self.git(root, 'commit', '-qm', 'Duplicate synthetic data')
            self.assertIn(('accounts.json', 'private configuration'), privacy.check(root, 'history')[1])
