"""Keep slot navigation and status changes consistent with the docked browser."""
from contextlib import ExitStack
import gc
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

if os.name == 'nt':
    import app_container as app


@unittest.skipUnless(os.name == 'nt', 'Windows slot navigation')
class SlotNavigationTests(unittest.TestCase):
    def switch(self, failure=None):
        callbacks = []
        button, label = Mock(), Mock()
        launcher = SimpleNamespace(current_page=1, page_size=12, current_grid_key='6x2',
            bot_profiles=[dict(key='account', name='Account', gaia='')],
            slot_cards=[dict(btn_ip=button, bot_lbl=label)], profile_hwnds={'account': 101},
            slot_urls={}, status_lbl=Mock(), browser_profiles=Mock(generation=0),
            run_launch_single=Mock(), _launch_errors={'account': 'old error'},
            update_slot_proxy_badge=Mock(), _restore_slot_docking=Mock(),
            after=lambda delay, callback: callbacks.append((delay, callback)))
        launcher.browser_profiles.navigate_app.side_effect = failure
        with (patch.object(app.user32, 'IsWindow', return_value=True),
              patch.object(app.threading, 'Thread') as thread,
              patch.object(app.LAUNCH_LOG, 'exception')):
            app.HighQualityMultiBoxerApp.toggle_slot_url(launcher, 0)
            self.assertEqual(launcher.slot_urls, {}, 'Mode changed before navigation succeeded')
            thread.call_args.kwargs['target']()
        next(callback for delay, callback in callbacks if delay == 0)()
        return launcher, button, label, callbacks

    def test_ip_button_navigates_existing_window_and_commits_mode_on_success(self):
        launcher, button, label, callbacks = self.switch()
        launcher.browser_profiles.navigate_app.assert_called_once_with('account', 'https://2ip.io', generation=0)
        launcher.run_launch_single.assert_not_called()
        self.assertEqual(launcher.slot_urls, {'account': '2ip'})
        self.assertEqual(launcher._launch_errors, {})
        self.assertEqual(button.config.call_args.kwargs, dict(text='🎮', fg='#34d399'))
        self.assertEqual(label.config.call_args.kwargs, dict(text='• Account [2ip]', fg='#ffffff'))
        next(callback for delay, callback in callbacks if delay == 100)()
        launcher._restore_slot_docking.assert_called_once_with('account', 0, 101)

    def test_failed_ip_navigation_preserves_game_mode_and_shows_the_exact_error(self):
        launcher, button, label, _ = self.switch(RuntimeError('Page.navigate: net::ERR_ABORTED'))
        self.assertEqual(launcher.slot_urls, {})
        launcher.run_launch_single.assert_not_called()
        self.assertEqual(button.config.call_args.kwargs, dict(state='normal'))
        self.assertEqual(label.config.call_args.kwargs, dict(text='• Account', fg='#ffffff'))
        self.assertEqual(launcher.status_lbl.config.call_args.kwargs,
                         dict(text='Не удалось открыть 2ip.io: Page.navigate: net::ERR_ABORTED'))


@unittest.skipUnless(os.name == 'nt', 'Windows launcher layout')
class StatusLayoutTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        def collect_on_main_thread():
            self.root = None
            gc.collect()
            if enabled:
                gc.enable()
        self.addCleanup(collect_on_main_thread)
        cls = app.HighQualityMultiBoxerApp
        with ExitStack() as stack:
            for method in ('bind_events', 'update_page_nav', 'apply_clip_children_styles', 'deiconify', 'state', 'after'):
                stack.enter_context(patch.object(cls, method))
            stack.enter_context(patch.object(cls, 'choose_network_mode', return_value=False))
            stack.enter_context(patch.object(app, 'load_accounts', return_value=[]))
            stack.enter_context(patch.object(app, 'get_system_monitors', return_value=[]))
            stack.enter_context(patch.object(app.kernel32, 'SetPriorityClass'))
            stack.enter_context(patch.object(app, 'BrowserProfiles'))
            manager = stack.enter_context(patch.object(app, 'ProxyManager'))
            manager.return_value.vpn_dispatcher.nodes = []
            self.root = cls()
        self.addCleanup(self.root.destroy)
        self.root.update_idletasks()

    def test_multiline_status_cannot_resize_header_or_status_request(self):
        before = (self.root.header_bar.winfo_reqheight(),
                  self.root.status_lbl.winfo_reqheight(), self.root.status_lbl.winfo_reqwidth())
        for message in ('VPN server\nHTTPS through proxy: 192.0.2.1\nProcess: 1234',
                        'Long network status ' * 50, '', 'Short status'):
            with self.subTest(message_lines=len(message.splitlines())):
                self.root.status_lbl.config(text=message)
                self.root.update_idletasks()
                self.assertEqual((self.root.header_bar.winfo_reqheight(),
                                  self.root.status_lbl.winfo_reqheight(),
                                  self.root.status_lbl.winfo_reqwidth()), before)


@unittest.skipUnless(os.name == 'nt', 'Windows docking geometry')
class DockingUpdateTests(unittest.TestCase):
    def harness(self):
        area = Mock()
        area.winfo_id.return_value = 202
        area.winfo_width.return_value = 300
        area.winfo_height.return_value = 400
        return SimpleNamespace(slot_cards=[{'game_area': area}], fullscreen_slot=None,
                               _docked_hwnds={101}, _chrome_insets={}, profile_hwnds={}, after=Mock())

    def test_missing_renderer_stops_scheduling_after_settle_budget(self):
        launcher = self.harness()
        with (patch.object(app.user32, 'IsWindow', return_value=True),
              patch.object(app.user32, 'GetParent', return_value=202),
              patch.object(app.user32, 'SetWindowPos'),
              patch.object(app, 'chrome_content_insets', return_value=None)):
            app.HighQualityMultiBoxerApp.dock_hwnd_to_slot(launcher, 101, 0, _settle_attempt=100)
        launcher.after.assert_not_called()

    def test_already_aligned_chrome_is_not_moved_again(self):
        self.check_native_move(current_top=170, visible=True, expected_moves=0)

    def test_misaligned_chrome_is_repositioned(self):
        self.check_native_move(current_top=180, visible=True, expected_moves=1)

    def test_hidden_chrome_is_shown_even_when_aligned(self):
        self.check_native_move(current_top=170, visible=False, expected_moves=1)

    def check_native_move(self, current_top, visible, expected_moves):
        launcher = self.harness()

        def window_rect(hwnd, pointer):
            bounds = pointer._obj
            bounds.left, bounds.top = 100, current_top
            bounds.right, bounds.bottom = 400, current_top + 430
            return True

        def parent_origin(hwnd, pointer):
            pointer._obj.x, pointer._obj.y = 100, 200
            return True

        with (patch.object(app.user32, 'IsWindow', return_value=True),
              patch.object(app.user32, 'IsWindowVisible', return_value=visible),
              patch.object(app.user32, 'GetParent', return_value=202),
              patch.object(app.user32, 'GetWindowRect', side_effect=window_rect),
              patch.object(app.user32, 'ClientToScreen', side_effect=parent_origin),
              patch.object(app.user32, 'SetWindowPos') as move,
              patch.object(app, 'chrome_content_insets', return_value=(0, 30, 0, 0))):
            app.HighQualityMultiBoxerApp.dock_hwnd_to_slot(launcher, 101, 0)
        self.assertEqual(move.call_count, expected_moves)
        launcher.after.assert_not_called()

    def test_crashed_browser_window_is_automatically_recovered(self):
        label = Mock()
        launcher = SimpleNamespace(
            _closing_browsers=False, is_launching=False,
            current_page=1, page_size=12,
            bot_profiles=[dict(key='bot_1', name='Bot 1', gaia='')],
            slot_cards=[dict(bot_lbl=label)],
            profile_hwnds={'bot_1': 999},
            _docked_hwnds={999},
            browser_sessions={'bot_1': dict(pid=1234, identity={'pid': 1234, 'create_time': 100})},
            slot_urls={'bot_1': 'game'},
            status_lbl=Mock(),
            browser_profiles=Mock(generation=1),
            run_launch_single=Mock(),
            _mark_slot_browser_failed=Mock()
        )
        with (patch.object(app.user32, 'IsWindow', return_value=False),
              patch.object(app, 'process_identity', return_value=None),
              patch.object(app.threading, 'Thread') as thread):
            app.HighQualityMultiBoxerApp._check_active_slots_health(launcher)
            self.assertNotIn('bot_1', launcher.profile_hwnds)
            self.assertNotIn('bot_1', launcher.browser_sessions)
            self.assertNotIn(999, launcher._docked_hwnds)
            self.assertTrue(thread.called)
            worker = thread.call_args.kwargs['target']
            worker()
            launcher.run_launch_single.assert_called_once_with(
                'bot_1', 0, proxy_slot_idx=0,
                target_url='https://play.multidonate.ru/c/zakvielchannel/',
                generation=1
            )
