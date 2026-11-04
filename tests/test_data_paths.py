import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_paths


class DataPathTests(unittest.TestCase):
    def test_source_state_and_profiles_stay_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True),
                  patch.object(app_paths, 'RESOURCE_DIR', root / 'repository')):
                expected = (root / 'local/LegionMultiBot/Development').resolve()
                self.assertEqual(app_paths.data_directory(False), expected)
                self.assertEqual(app_paths.profile_directory(False), expected / 'Bots')
                self.assertFalse(expected.exists(), 'Path lookup must not create working data')

    def test_source_override_inside_the_repository_is_rejected(self):
        with patch.dict(os.environ, {'LEGION_DATA_DIR': str(app_paths.RESOURCE_DIR / 'private')}):
            with self.assertRaisesRegex(ValueError, 'outside the source repository'):
                app_paths.data_directory(False)

    def test_portable_state_and_existing_profile_layout_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True),
                  patch.object(app_paths, 'APP_DIR', root / 'portable/app')):
                self.assertEqual(app_paths.data_directory(True), (root / 'portable/app').resolve())
                self.assertEqual(app_paths.profile_directory(True), root / 'local/LegionMultiBot/Bots')
