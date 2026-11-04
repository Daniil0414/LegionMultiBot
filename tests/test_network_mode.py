import os
import gc
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import browser_profiles as bp
from userscripts import UserscriptManager
from vpn_runtime import atomic_json

if os.name == 'nt':
    import tkinter as tk
    import app_container as app


class DirectBrowserTests(unittest.TestCase):
    def test_direct_launch_requires_explicit_opt_in_even_without_a_proxy(self):
        for proxy in (None, '', 'DIRECT', 'ERROR_ALL_DOWN'):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                bp.security_args(proxy)
        for proxy in (None, '', 'ERROR_ALL_DOWN'):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                bp.security_args(proxy, allow_direct=True)

    def test_direct_browser_command_bypasses_inherited_proxy_and_records_route(self):
        with tempfile.TemporaryDirectory() as directory:
            atomic_json(str(Path(directory) / 'scripts.json'), {'version': 2, 'scripts': []})
            scripts = UserscriptManager(Path(directory) / 'scripts.json')
            profiles = bp.BrowserProfiles(root=directory, userscripts=scripts, allow_direct=True)
            try:
                with (patch.object(profiles, '_has_unmanaged_browser', return_value=False),
                      patch.object(bp, 'MobileBrowser') as factory,
                      patch.object(bp, 'process_identity', return_value={'pid': 1234})):
                    result = profiles.launch('chrome.exe', 'account', 'DIRECT', ['https://fixture.invalid'])
                    command = factory.return_value.start.call_args.args[0]
                    self.assertIn('--no-proxy-server', command)
                    self.assertFalse(any(arg.startswith('--proxy-server=') for arg in command))
                    self.assertEqual(result['proxy'], 'DIRECT')
                    factory.return_value.navigate.assert_called_once_with('https://fixture.invalid')
            finally:
                profiles.shutdown()

    def test_fresh_profiles_install_default_before_opening_each_window(self):
        from userscripts import DEFAULT_SCRIPT_URL, github_raw_url
        with tempfile.TemporaryDirectory() as directory:
            scripts = UserscriptManager(Path(directory) / 'scripts.json')
            profiles = bp.BrowserProfiles(root=directory, userscripts=scripts, allow_direct=True)
            events = []
            def download(url):
                self.assertEqual(url, github_raw_url(DEFAULT_SCRIPT_URL))
                events.append('download')
                return 'window.defaultScriptFixture = true;'
            try:
                with (patch.object(profiles, '_has_unmanaged_browser', return_value=False),
                      patch.object(bp, 'MobileBrowser') as factory,
                      patch.object(bp, 'process_identity', return_value={'pid': 1234})):
                    with patch('userscripts.download_script', side_effect=OSError('first download offline')):
                        with self.assertRaisesRegex(OSError, '^first download offline$'):
                            profiles.launch('chrome.exe', 'account-a', 'DIRECT', ['https://fixture.invalid'])
                    factory.assert_not_called()
                    factory.return_value.start.side_effect = lambda *args: events.append('start') or Mock()
                    factory.return_value.navigate.side_effect = lambda *args: events.append('navigate')
                    with patch('userscripts.download_script', side_effect=download):
                        for account in ('account-a', 'account-b'):
                            result = profiles.launch('chrome.exe', account, 'DIRECT', ['https://fixture.invalid'])
                            self.assertEqual(result['userscript']['enabled_count'], 1)
                    self.assertEqual(events, ['download', 'start', 'navigate'] * 2)
                    for call in factory.return_value.set_userscript.call_args_list:
                        self.assertIn('window.defaultScriptFixture', call.args[0]['bootstrap'])
            finally:
                profiles.shutdown()


@unittest.skipUnless(os.name == 'nt', 'Windows launcher')
class NetworkModeTests(unittest.TestCase):
    def setUp(self):
        # Mock call histories retain bound Tk callbacks in cycles. Collect these
        # on the main thread, not in a later Chrome transport/server worker.
        self._gc_was_enabled = gc.isenabled()
        gc.disable()

    def tearDown(self):
        try:
            gc.collect()
        finally:
            if self._gc_was_enabled:
                gc.enable()

    def test_direct_route_skips_vpn_checks_and_slot_assignment(self):
        launcher = SimpleNamespace(use_vpn=False, proxy_manager=Mock(), browser_profiles=Mock())
        self.assertEqual(app.HighQualityMultiBoxerApp.get_launch_proxy(launcher, 'account', 4), 'DIRECT')
        launcher.proxy_manager.get_resilient_proxy_for_slot.assert_not_called()
        launcher.browser_profiles.proxy_slot.assert_not_called()

    def test_vpn_route_failure_is_preserved_without_direct_fallback(self):
        launcher = SimpleNamespace(use_vpn=True, proxy_manager=Mock(), browser_profiles=Mock())
        launcher.browser_profiles.proxy_slot.return_value = 7
        launcher.proxy_manager.get_resilient_proxy_for_slot.return_value = 'ERROR_ALL_DOWN'
        self.assertEqual(app.HighQualityMultiBoxerApp.get_launch_proxy(launcher, 'account', 4), 'ERROR_ALL_DOWN')
        self.assertEqual(launcher.proxy_manager.get_resilient_proxy_for_slot.call_args.args, (7,))
        self.assertEqual(launcher.proxy_manager.get_resilient_proxy_for_slot.call_args.kwargs['profile_key'], 'account')

    def test_direct_mode_skips_background_vpn_probes(self):
        launcher = SimpleNamespace(use_vpn=False, proxy_manager=Mock(), after=Mock())
        app.HighQualityMultiBoxerApp.periodic_vpn_check(launcher)
        app.HighQualityMultiBoxerApp._check_browser_routes_async(launcher)
        launcher.proxy_manager.vpn_dispatcher.get_nodes_snapshot.assert_not_called()
        launcher.after.assert_not_called()

    def test_batch_launches_skip_vpn_pool_in_direct_mode(self):
        for method in ('run_launch_current_page', 'run_launch_all'):
            with self.subTest(method=method):
                launcher = SimpleNamespace(use_vpn=False, browser_profiles=Mock(generation=0),
                                           proxy_manager=Mock(), bot_profiles=[], current_page=1,
                                           page_size=12, total_pages=0, after=Mock(), safe_switch_page=Mock())
                launcher.proxy_manager.vpn_dispatcher.nodes = ['unavailable server']
                launcher.proxy_manager.vpn_dispatcher.alive_nodes = []
                with patch.object(app.time, 'sleep'):
                    getattr(app.HighQualityMultiBoxerApp, method)(launcher)
                launcher.proxy_manager.vpn_dispatcher.filter_alive_nodes.assert_not_called()

    def test_login_and_single_launch_use_direct_route(self):
        launcher = SimpleNamespace(use_vpn=False, _closing_browsers=False,
                                   browser_profiles=Mock(generation=0), proxy_manager=Mock(),
                                   bot_profiles=[dict(key='account', name='Account')],
                                   status_lbl=Mock(), mute_var=Mock(), browser_sessions={},
                                   after=lambda delay, callback: callback(), slot_urls={},
                                   update_slot_proxy_badge=Mock())
        launcher.mute_var.get.return_value = False
        launcher.get_launch_proxy = lambda key, slot: app.HighQualityMultiBoxerApp.get_launch_proxy(launcher, key, slot)
        with patch.object(app.threading, 'Thread') as thread:
            app.HighQualityMultiBoxerApp.launch_account_for_login(launcher, 'account')
            thread.call_args.kwargs['target']()
        self.assertEqual(launcher.browser_profiles.launch.call_args.args[2], 'DIRECT')
        launcher.browser_profiles.launch.reset_mock()
        launcher.browser_profiles.launch.side_effect = bp.LaunchCancelled()
        with patch.object(app.user32, 'EnumWindows'):
            app.HighQualityMultiBoxerApp.run_launch_single(launcher, 'account', 0)
        self.assertEqual(launcher.browser_profiles.launch.call_args.args[2], 'DIRECT')
        launcher.proxy_manager.get_resilient_proxy_for_slot.assert_not_called()

    def test_startup_direct_mode_does_not_start_monitor_or_open_vpn_settings(self):
        cls = app.HighQualityMultiBoxerApp
        with (patch.object(cls, 'choose_network_mode', return_value=False),
              patch.object(cls, 'setup_ui'), patch.object(cls, 'bind_events'),
              patch.object(cls, 'update_page_nav'), patch.object(cls, 'apply_clip_children_styles'),
              patch.object(cls, 'deiconify'), patch.object(cls, 'after') as after,
              patch.object(app, 'load_accounts', return_value=[]),
              patch.object(app, 'get_system_monitors', return_value=[]),
              patch.object(app, 'get_happ_active_server') as happ,
              patch.object(app.kernel32, 'SetPriorityClass'),
              patch.object(app, 'ProxyManager') as manager,
              patch.object(app, 'BrowserProfiles') as profiles):
            launcher = cls()
            try:
                manager.assert_called_once_with(str(app.DATA_DIR), start_monitor=False)
                profiles.assert_called_once_with(allow_direct=True)
                callbacks = [call.args[1] for call in after.call_args_list]
                self.assertNotIn(launcher.periodic_vpn_check, callbacks)
                self.assertNotIn(launcher.open_proxy_settings, callbacks)
                happ.assert_not_called()
            finally:
                launcher.destroy()

    def test_startup_dialog_offers_both_modes_and_closing_cancels(self):
        for label, expected in (('С VPN', True), ('Без VPN', False), (None, None)):
            with self.subTest(label=label):
                root = tk.Tk()
                root.withdraw()

                def select():
                    dialog = root.winfo_children()[0]
                    if label is None:
                        dialog.destroy()
                        return
                    buttons = [button for widget in dialog.winfo_children()
                               for button in widget.winfo_children() if isinstance(button, tk.Button)]
                    next(button for button in buttons if button.cget('text') == label).invoke()

                try:
                    root.after(100, select)
                    self.assertIs(app.HighQualityMultiBoxerApp.choose_network_mode(root), expected)
                finally:
                    root.destroy()
