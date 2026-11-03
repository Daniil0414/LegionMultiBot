import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from chrome_mobile import MobileBrowser, device_for_profile
from userscripts import DEFAULT_SCRIPT_URL, UserscriptManager, compile_script, download_script, github_raw_url, match_regex
from vpn_runtime import atomic_json

URL = 'https://github.com/example/bot/blob/main/bot.user.js'
SOURCE = '''// ==UserScript==
// @name Fixture bot
// @version 1
// @match https://*.example.org/game/*
// @grant none
// @run-at document-start
// ==/UserScript==
window.fixtureBot = 1;
'''


class UserscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legion-script-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'userscript_config.json'
        # Existing configurations keep their explicitly saved list.
        atomic_json(str(self.path), {'version': 2, 'scripts': []})
        self.manager = UserscriptManager(self.path)

    def test_fresh_install_enables_default_and_downloads_it_on_first_launch(self):
        self.path.unlink()
        with patch('userscripts.download_script') as fetch:
            status = self.manager.status()
            self.assertIsNone(self.manager.current())
        fetch.assert_not_called()
        self.assertEqual(len(status['scripts']), 1)
        entry = status['scripts'][0]
        self.assertEqual(entry['name'], 'LegionAutoMiner')
        self.assertEqual(entry['script_url'], github_raw_url(DEFAULT_SCRIPT_URL))
        self.assertTrue(entry['installed'])
        self.assertEqual(status['enabled_count'], 1)
        self.assertEqual(UserscriptManager(self.path).status(), status)
        with patch('userscripts.download_script', return_value=SOURCE) as fetch:
            script = self.manager.for_launch()
        fetch.assert_called_once_with(github_raw_url(DEFAULT_SCRIPT_URL))
        self.assertIn('window.fixtureBot', script['bootstrap'])
        self.assertEqual(UserscriptManager(self.path).current()['digest'], script['digest'])
        self.assertEqual(len(self.manager.status()['scripts']), 1)

    def test_first_default_download_failure_is_reported_and_retried(self):
        self.path.unlink()
        with patch('userscripts.download_script', side_effect=OSError('GitHub fixture offline')):
            with self.assertRaisesRegex(OSError, '^GitHub fixture offline$'):
                self.manager.for_launch()
        with patch('userscripts.download_script', return_value=SOURCE):
            self.assertIn('window.fixtureBot', self.manager.for_launch()['bootstrap'])
        with patch('userscripts.download_script', side_effect=OSError('offline after install')):
            cached = UserscriptManager(self.path).for_launch()
        self.assertEqual(cached['digest'], self.manager.current()['digest'])
        self.assertTrue(self.manager.status()['using_cached'])

    def test_default_can_be_disabled_before_first_download(self):
        self.path.unlink()
        selected_id = self.manager.status()['scripts'][0]['id']
        with patch('userscripts.download_script') as fetch:
            self.assertIsNone(self.manager.disable(selected_id))
            self.assertIsNone(UserscriptManager(self.path).for_launch())
        fetch.assert_not_called()
        self.assertEqual(self.manager.status()['enabled_count'], 0)

    def test_default_script_install_updates_same_entry_and_stays_disabled_on_restart(self):
        self.path.unlink()
        selected_id = self.manager.status()['scripts'][0]['id']
        with patch('userscripts.download_script', return_value=SOURCE) as fetch:
            installed = self.manager.install(DEFAULT_SCRIPT_URL, selected_id)
            self.manager.install(github_raw_url(DEFAULT_SCRIPT_URL))
        self.assertEqual(installed['selected_id'], selected_id)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(self.manager.status()['scripts']), 1)
        self.assertEqual(self.manager.status()['enabled_count'], 1)
        self.manager.disable(selected_id)
        restarted = UserscriptManager(self.path)
        self.assertEqual(len(restarted.status()['scripts']), 1)
        self.assertFalse(restarted.status()['scripts'][0]['installed'])
        with patch('userscripts.download_script') as fetch:
            self.assertIsNone(restarted.for_launch())
        fetch.assert_not_called()

    def test_new_script_preserves_default_and_existing_lists_are_not_reseeded(self):
        self.assertEqual(self.manager.status()['scripts'], [])
        self.path.unlink()
        self.install()
        scripts = UserscriptManager(self.path).status()['scripts']
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[0]['script_url'], github_raw_url(DEFAULT_SCRIPT_URL))
        self.assertTrue(scripts[0]['installed'])
        self.assertTrue(scripts[1]['installed'])
        atomic_json(str(self.path), {'version': 2, 'scripts': [scripts[1]]})
        self.assertEqual(UserscriptManager(self.path).status()['scripts'], [scripts[1]])

    def install(self, source=SOURCE):
        with patch('userscripts.download_script', return_value=source):
            return self.manager.install(URL)

    def test_file_links_raw_links_and_branch_paths(self):
        self.assertEqual(github_raw_url(URL + '?raw=true#L1'),
                         'https://raw.githubusercontent.com/example/bot/main/bot.user.js')
        raw = 'https://raw.githubusercontent.com/example/bot/refs/heads/main/bot.user.js'
        self.assertEqual(github_raw_url(raw), raw)
        for value in ('http://github.com/a/b/blob/main/b.js', 'https://github.com/a/b',
                      'https://github.com.evil.test/a/b/blob/main/b.js',
                      'https://user@github.com/a/b/blob/main/b.js', None, ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                github_raw_url(value)

    def test_each_launch_downloads_again_and_shared_manager_survives_restart(self):
        first = self.install()
        restarted = UserscriptManager(self.path)
        second = SOURCE.replace('@version 1', '@version 2').replace('fixtureBot = 1', 'fixtureBot = 2')
        with patch('userscripts.download_script', side_effect=[second, second]) as fetch:
            updated = restarted.for_launch()
            self.assertEqual(restarted.for_launch()['digest'], updated['digest'])
        self.assertEqual(fetch.call_count, 2)
        self.assertNotEqual(first['digest'], updated['digest'])
        status = restarted.status()
        self.assertTrue(status['auto_update_on_launch'])
        self.assertEqual(status['version'], '2')
        self.assertNotIn('source', status)
        self.assertNotIn('bootstrap', status)

    def test_bad_update_uses_intact_cache_and_records_exact_error(self):
        original = self.install()
        with patch('userscripts.download_script', side_effect=OSError('GitHub fixture offline')):
            cached = self.manager.for_launch()
        self.assertEqual(cached['digest'], original['digest'])
        self.assertEqual(self.manager.status()['last_error'], 'GitHub fixture offline')
        self.assertTrue(self.manager.status()['using_cached'])
        with patch('userscripts.download_script', return_value='<html>Login</html>'):
            self.assertEqual(self.manager.for_launch()['digest'], original['digest'])
        self.install()
        self.assertEqual(self.manager.status()['last_error'], '')

    def test_failed_replacement_preserves_previous_install_byte_for_byte(self):
        self.install()
        before = self.path.read_bytes()
        with patch('userscripts.download_script', side_effect=OSError('HTTP 404')):
            with self.assertRaisesRegex(OSError, 'HTTP 404'):
                self.manager.install(URL.replace('example', 'other'))
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_cache_blocks_launch(self):
        self.install()
        import json
        config = json.loads(self.path.read_text(encoding='utf-8'))
        config['scripts'][0]['source'] += '\nwindow.corrupted = true;'
        atomic_json(str(self.path), config)
        with patch('userscripts.download_script', side_effect=OSError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'повреждена'):
                self.manager.for_launch()

    def test_second_script_preserves_first_and_can_be_disabled_independently(self):
        first = self.install()
        first_id = first['selected_id']
        second_url = URL.replace('/bot/blob/', '/helper/blob/')
        source = SOURCE.replace('Fixture bot', 'Fixture helper').replace('fixtureBot', 'fixtureHelper')
        with patch('userscripts.download_script', return_value=source):
            combined = self.manager.install(second_url)
        status = self.manager.status()
        self.assertEqual(status['enabled_count'], 2)
        self.assertEqual(status['scripts'][0]['id'], first_id)
        self.assertIn('window.fixtureBot', combined['bootstrap'])
        self.assertIn('window.fixtureHelper', combined['bootstrap'])
        self.assertNotEqual(first['digest'], combined['digest'])
        second_id = combined['selected_id']
        remaining = self.manager.disable(second_id)
        self.assertEqual(remaining['digest'], first['digest'])
        self.assertEqual(self.manager.status()['enabled_count'], 1)
        with patch('userscripts.download_script', return_value=SOURCE) as fetch:
            self.manager.for_launch()
        fetch.assert_called_once_with(github_raw_url(URL))
        with patch('userscripts.download_script', return_value=source):
            self.manager.install(second_url, second_id)
            self.manager.install(second_url)  # Repeated links update the same entry.
        self.assertEqual(len(self.manager.status()['scripts']), 2)
        self.assertEqual(self.manager.status()['enabled_count'], 2)

    def test_updates_and_cached_fallback_are_independent_for_each_script(self):
        self.install()
        second_url = URL.replace('/bot/blob/', '/helper/blob/')
        with patch('userscripts.download_script', return_value=SOURCE.replace('Fixture bot', 'Helper')):
            self.manager.install(second_url)
        def fetch(url):
            if url == github_raw_url(second_url):
                raise OSError('helper offline')
            return SOURCE.replace('@version 1', '@version 2')
        with patch('userscripts.download_script', side_effect=fetch) as download:
            self.manager.for_launch()
        self.assertEqual(download.call_count, 2)
        status = self.manager.status()
        self.assertEqual(status['scripts'][0]['version'], '2')
        self.assertFalse(status['scripts'][0]['using_cached'])
        self.assertEqual(status['scripts'][1]['version'], '1')
        self.assertEqual(status['scripts'][1]['last_error'], 'helper offline')
        self.assertTrue(status['scripts'][1]['using_cached'])
        self.assertEqual(status['enabled_count'], 2)

    def test_old_single_script_migrates_with_cache_and_enabled_state(self):
        legacy = dict(script_url=github_raw_url(URL), source=SOURCE, installed=True,
                      name='Fixture bot', version='1', digest=hashlib.sha256(SOURCE.encode()).hexdigest())
        atomic_json(str(self.path), legacy)
        self.assertEqual(self.manager.status()['scripts'][0]['id'], 'legacy')
        self.assertEqual(self.manager.current()['digest'], legacy['digest'])
        with patch('userscripts.download_script', return_value=SOURCE.replace('Fixture bot', 'Helper')):
            self.manager.install(URL.replace('/bot/blob/', '/helper/blob/'))
        import json
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(stored['scripts'][0]['source'], SOURCE)
        self.assertTrue(stored['scripts'][0]['installed'])
        self.assertEqual(len(stored['scripts']), 2)

    def test_failed_selected_update_or_unknown_id_does_not_change_other_scripts(self):
        first = self.install()
        with patch('userscripts.download_script', return_value=SOURCE):
            self.manager.install(URL.replace('/bot/blob/', '/helper/blob/'))
        before = self.path.read_bytes()
        with patch('userscripts.download_script', side_effect=OSError('update failed')):
            with self.assertRaisesRegex(OSError, 'update failed'):
                self.manager.install(URL, first['selected_id'])
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'не найден'):
            self.manager.disable('missing-script')
        self.assertEqual(self.path.read_bytes(), before)

    def test_disable_and_legacy_settings_do_not_download_or_enable_scripts(self):
        self.install()
        self.manager.disable()
        with patch('userscripts.download_script') as fetch:
            self.assertIsNone(self.manager.for_launch())
        fetch.assert_not_called()
        atomic_json(str(self.path), dict(script_url=URL, auto_update_on_launch=False))
        self.assertIsNone(self.manager.for_launch())

    def test_metadata_rules_and_unsupported_managers_are_validated(self):
        import re
        self.assertRegex('https://sub.example.org/game/1', match_regex('https://*.example.org/game/*'))
        self.assertRegex('https://example.org/game/1', match_regex('https://*.example.org/game/*'))
        self.assertIsNone(re.search(match_regex('https://*.example.org/game/*'), 'https://example.org.evil/game/1'))
        for metadata in ('// @grant GM_getValue\n', '// @require https://example.org/a.js\n',
                         '// @noframes\n// @grant GM_setValue\n', '// @match invalid\n'):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                compile_script(SOURCE.replace('// @grant none\n', metadata))
        self.assertIn('"noframes": true', compile_script(SOURCE.replace('// @grant none', '// @noframes\n// @grant none'))['bootstrap'])

    def test_fetch_bypasses_cache_checks_type_and_limits_size(self):
        from email.message import Message
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = github_raw_url(URL)
        response.headers = Message()
        response.headers['Content-Type'] = 'text/plain'
        response.read.return_value = SOURCE.encode()
        with patch('userscripts.urllib.request.build_opener') as build:
            build.return_value.open.return_value = response
            self.assertEqual(download_script(github_raw_url(URL)), SOURCE)
            request = build.return_value.open.call_args.args[0]
            self.assertIn('?legion_update=', request.full_url)
            self.assertEqual(request.get_header('Cache-control'), 'no-cache')
            response.headers.replace_header('Content-Type', 'text/html')
            with self.assertRaisesRegex(ValueError, 'HTML'):
                download_script(github_raw_url(URL))
            response.headers.replace_header('Content-Type', 'text/plain')
            with patch('userscripts.MAX_BYTES', 8), self.assertRaisesRegex(ValueError, '4 МБ'):
                download_script(github_raw_url(URL))

    def test_replacing_hooks_updates_every_page_without_duplicate_registration(self):
        browser = MobileBrowser(device_for_profile(Path(self.temp.name) / 'profile'))
        browser.connection = Mock()
        browser.connection.call.return_value = {'identifier': 'hook-1'}
        script = compile_script(SOURCE)
        browser.set_userscript(script)
        browser._configure('session-a', 'page')
        browser._targets = {'a': dict(session='session-a', type='page'),
                            'b': dict(session='session-b', type='page')}
        browser._configure('session-b', 'page')
        browser.connection.call.reset_mock()
        browser.set_userscript(script, reload=True)
        browser.connection.call.assert_not_called()
        browser.set_userscript(compile_script(SOURCE + '\n// new version'), reload=True)
        calls = browser.connection.call.call_args_list
        self.assertEqual(sum(c.args[0] == 'Page.reload' for c in calls), 2)
        self.assertEqual(sum(c.args[0] == 'Page.removeScriptToEvaluateOnNewDocument' for c in calls), 2)
        self.assertEqual(sum(c.args[0] == 'Page.addScriptToEvaluateOnNewDocument' for c in calls), 2)
        browser.connection.call.reset_mock()
        browser.set_userscript(None, reload=True)
        self.assertFalse(browser._script_ids)
        self.assertFalse(any(c.args[0] == 'Page.addScriptToEvaluateOnNewDocument' for c in browser.connection.call.call_args_list))


if __name__ == '__main__':
    unittest.main()
