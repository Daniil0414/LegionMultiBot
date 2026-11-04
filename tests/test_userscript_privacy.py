import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest

from userscript_privacy import PrivateScriptStore
from userscripts import UserscriptManager, compile_script
from unittest.mock import patch


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legion-private-script-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private.json'
        self.store = PrivateScriptStore(self.path)
        self.origin = 'https://game.example'

    def test_migration_persists_and_never_overwrites_newer_private_settings(self):
        self.store.request(self.origin, {'action': 'load', 'legacy': {'lg_bot_min': '1'}})
        self.store.request(self.origin, {'action': 'set', 'key': 'lg_bot_min', 'value': '0'})
        other = PrivateScriptStore(self.path)
        result = other.request(self.origin, {'action': 'load', 'legacy': {'lg_bot_min': '1'}})
        self.assertEqual(result['values'], {'lg_bot_min': '0'})

    def test_origins_and_profiles_have_independent_settings(self):
        self.store.request(self.origin, {'action': 'set', 'key': 'lg_bot_min', 'value': '1'})
        self.assertEqual(self.store.request('https://other.example', {'action': 'load'})['values'], {})
        other = PrivateScriptStore(self.path.parent / 'second.json')
        self.assertEqual(other.request(self.origin, {'action': 'load'})['values'], {})

    def test_unrelated_game_keys_and_oversized_values_are_rejected(self):
        for key, value in [('session', 'secret'), ('lgCityR', '1'), ('lg_bot_min', 'x' * 65537)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.request(self.origin, {'action': 'set', 'key': key, 'value': value})
        self.assertFalse(self.path.exists())

    def test_damaged_file_is_not_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.request(self.origin, {'action': 'load'})
        self.assertEqual(self.path.read_text(), '{broken')

    def test_parallel_writers_preserve_independent_settings(self):
        def write(index):
            PrivateScriptStore(self.path).request(self.origin,
                {'action': 'set', 'key': f'lg_bot_{index}', 'value': str(index)})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(12)))
        self.assertEqual(len(json.loads(self.path.read_text())['origins'][self.origin]), 12)

    def test_invalid_origin_does_not_create_a_file(self):
        for origin in ('null', 'file:///tmp', 'https://user:pass@game.example', 'https://game.example/path'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.store.request(origin, {'action': 'load'})
        self.assertFalse(self.path.exists())

    def test_supported_bot_uses_private_runtime_but_other_scripts_are_unchanged(self):
        source = '// ==UserScript==\n// @namespace https://github.com/Daniil0414/LegionAutoMiner\n// ==/UserScript==\nwindow.__mineBotRunning=true;'
        private = compile_script(source)
        ordinary = compile_script('window.ordinaryScript = true;')
        self.assertTrue(private['private_state'])
        self.assertEqual(private['privacy_changes']['globals'], 1)
        self.assertFalse(ordinary['private_state'])
        self.assertIn('window.ordinaryScript = true;', ordinary['bootstrap'])

    def test_update_cannot_silently_remove_private_state_mode(self):
        with patch('userscripts.download_script', return_value='window.newVersion = true;'):
            with self.assertRaisesRegex(ValueError, 'изменило формат приватного состояния'):
                UserscriptManager._download('https://raw.githubusercontent.com/example/bot/main/test.js',
                    {'private_required': True})

    def test_private_bundles_keep_private_runtime_when_combined_with_other_scripts(self):
        import hashlib
        source = '// ==UserScript==\n// @namespace https://github.com/Daniil0414/LegionAutoMiner\n// ==/UserScript==\nwindow.__mineBotRunning=true;'
        entries = [dict(id=str(i), installed=True, source=s,
                        digest=hashlib.sha256(s.encode()).hexdigest())
                   for i, s in enumerate((source, 'window.fixture = true;'))]
        self.assertTrue(UserscriptManager._bundle({'scripts': entries})['private_state'])

    def test_supported_bot_preserves_mine_control_even_with_compound_toggles(self):
        compound_source = '''// ==UserScript==
// @namespace https://github.com/Daniil0414/LegionAutoMiner
// ==/UserScript==
(() => {
  window.__mineBotRunning = true;
  let isPaused = true, miningStartToken = null;
  const btn = {isConnected: true, onclick() {}};
  function startBot() { isPaused = false; }
  function pauseBot() { isPaused = true; }
  btn.onclick = () => { if (isPaused && !miningStartToken) { startBot(); } else { pauseBot(); } };
})();'''
        compiled = compile_script(compound_source)
        self.assertTrue(compiled['mine_control'])
        self.assertEqual(compiled['privacy_changes'].get('mineControls'), 1)

    def test_supported_bot_always_has_mine_control_enabled_even_if_toggle_ast_unmatched(self):
        unknown_toggle_source = '''// ==UserScript==
// @namespace https://github.com/Daniil0414/LegionAutoMiner
// ==/UserScript==
(() => {
  window.__mineBotRunning = true;
  const btn = document.querySelector('#b-btn');
})();'''
        compiled = compile_script(unknown_toggle_source)
        self.assertTrue(compiled['mine_control'])
        self.assertEqual(compiled['privacy_changes'].get('mineControls', 0), 0)
