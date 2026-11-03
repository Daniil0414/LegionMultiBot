"""Native login, popup containment and return to the grid without a profile restart."""
from concurrent.futures import ThreadPoolExecutor
import gc
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME'), 'Set LEGION_TEST_CHROME')
class NativeEmbeddedLoginTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import test_mobile_browser as fixture
        import app_container as app
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        def collect():
            gc.collect()
            if enabled:
                gc.enable()
        self.addCleanup(collect)
        fixture.MobileIntegrationTests.setUp(self)
        self.profiles.emulate_mobile = False
        app.init_windows_dpi_awareness()
        self.window = tk.Tk()
        self.window.title('Local embedded-login fixture')
        self.window.geometry('1000x740+30+30')
        self.grid = tk.Frame(self.window, bg='black')
        self.grid.pack(fill='both', expand=True)
        self.window.update()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.close_ui)

    def close_ui(self):
        if getattr(self, 'view', None) is not None:
            self.view.release()
        self.profiles.close()
        self.window.destroy()
        self.pool.shutdown(wait=True)

    def wait(self, predicate, timeout=15):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.window.update()
            result = predicate()
            if result:
                return result
            time.sleep(.015)
        self.fail('Embedded login did not settle')

    def call(self, method, params=None, session=None):
        future = self.pool.submit(self.browser.connection.call, method, params or {}, session)
        self.wait(future.done)
        return future.result()

    def evaluate(self, expression, **params):
        session = self.browser.wait_target(self.browser.app_target)
        result = self.call('Runtime.evaluate', dict(expression=expression, returnByValue=True, **params), session)
        self.assertNotIn('exceptionDetails', result)
        return result['result'].get('value')

    def test_main_and_oauth_windows_are_children_then_same_browser_returns_to_grid(self):
        import app_container as app
        from embedded_login_ui import EmbeddedLoginView, native
        future = self.pool.submit(self.profiles.launch, os.environ['LEGION_TEST_CHROME'], 'account-a',
            f'http://127.0.0.1:{self.proxy.server_port}',
            ['--window-size=900,650', '--app=http://device-a.localhost/start'])
        self.wait(future.done)
        info = future.result()
        self.browser = self.profiles._controllers['account-a']
        ready, failures = [], []
        harness = SimpleNamespace(slot_cards=[dict(game_area=self.grid)], _docked_hwnds=set(),
            _chrome_insets={}, profile_hwnds={}, browser_profiles=self.profiles,
            get_zoom_factor=lambda: 1.0, after=self.window.after, _login_view=None)
        def finish(view):
            hwnd, windows = view.release()
            for popup in windows:
                if popup != hwnd:
                    native.PostMessageW(popup, 0x0010, 0, 0)
            harness.profile_hwnds['account-a'] = hwnd
            app.HighQualityMultiBoxerApp.dock_hwnd_to_slot(harness, hwnd, 0)
        self.view = EmbeddedLoginView(self.window, finish)
        harness._login_view = self.view
        self.view.show('account-a', 'Fixture account', info['pid'], ready=ready.append, failed=failures.append)
        self.wait(lambda: ready)
        primary = ready[0]
        self.assertEqual(native.GetParent(primary), self.view.viewport.winfo_id())
        self.assertIs(self.view.winfo_toplevel(), self.window)
        app.HighQualityMultiBoxerApp.dock_hwnd_to_slot(harness, primary, 0)
        self.assertEqual(native.GetParent(primary), self.view.viewport.winfo_id(), 'Grid stole the login browser')
        native.SetParent(primary, None)
        self.wait(lambda: native.GetParent(primary) == self.view.viewport.winfo_id())
        self.evaluate("localStorage.setItem('login_fixture','saved'); document.cookie='login_fixture=saved'; window.loginDocument=Math.random()")
        marker = self.evaluate('window.loginDocument')
        target, pid = self.browser.app_target, self.browser.process.pid
        self.evaluate("window.open('http://device-a.localhost/popup','login-fixture','width=600,height=650'); true", userGesture=True)
        self.wait(lambda: len(self.view.browser_hwnds) >= 2 and self.view._active != primary)
        popup = self.view._active
        self.assertEqual(native.GetParent(popup), self.view.viewport.winfo_id())
        native.PostMessageW(popup, 0x0010, 0, 0)
        self.wait(lambda: self.view._active == primary and len(self.view.browser_hwnds) == 1)
        if os.environ.get('LEGION_EMBEDDED_LOGIN_SCREENSHOT'):
            from PIL import ImageGrab
            self.window.attributes('-topmost', True)
            self.window.update()
            time.sleep(.15)
            x, y = self.window.winfo_rootx(), self.window.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + self.window.winfo_width(), y + self.window.winfo_height())).save(
                os.environ['LEGION_EMBEDDED_LOGIN_SCREENSHOT'])
            self.window.attributes('-topmost', False)
        finish(self.view)
        self.window.update()
        self.assertFalse(self.view.visible)
        self.assertEqual(native.GetParent(primary), self.grid.winfo_id())
        self.assertEqual(self.browser.process.pid, pid)
        self.assertEqual(self.browser.app_target, target)
        self.assertEqual(self.evaluate('window.loginDocument'), marker)
        self.assertEqual(self.evaluate("localStorage.getItem('login_fixture')"), 'saved')
        self.assertIn('login_fixture=saved', self.evaluate('document.cookie'))
        self.assertEqual(failures, [])


@unittest.skipUnless(os.name == 'nt', 'Windows login presentation')
class LoginPresentationTests(unittest.TestCase):
    def test_back_to_grid_uses_the_ui_thread_and_preserves_the_browser(self):
        import app_container as app
        view = SimpleNamespace(key='account-2', release=Mock(return_value=(123, (123, 456))))
        launcher = SimpleNamespace(_shutting_down=False, _closing_browsers=False,
            bot_profiles=[dict(key='account-1'), dict(key='account-2')], profile_hwnds={},
            slot_urls={}, fullscreen_slot=0, page_size=1, btn_exit_fs=Mock(),
            safe_switch_page=Mock(), switch_page=Mock(), reposition_all_active_windows=Mock())
        with patch.object(app.user32, 'IsWindow', return_value=True), patch.object(app.user32, 'PostMessageW') as close:
            app.HighQualityMultiBoxerApp._finish_login_browser(launcher, view)
        launcher.safe_switch_page.assert_not_called()
        launcher.switch_page.assert_called_once_with(2, force=True)
        self.assertEqual(launcher.profile_hwnds['account-2'], 123)
        self.assertIsNone(launcher.fullscreen_slot)
        close.assert_called_once_with(456, app.WM_CLOSE, 0, 0)

    def test_login_task_waits_for_embedding_completion(self):
        import app_container as app
        launcher = SimpleNamespace(_closing_browsers=False, use_vpn=False,
            browser_profiles=Mock(generation=1), bot_profiles=[dict(key='one', name='One')],
            status_lbl=Mock(), mute_var=Mock(get=lambda: False), browser_sessions={},
            get_launch_proxy=Mock(return_value='DIRECT'), after=lambda delay, callback: callback(),
            _present_login_browser=Mock())
        launcher.browser_profiles.launch.return_value = dict(pid=123)
        with patch.object(app.threading, 'Thread') as thread:
            task = app.HighQualityMultiBoxerApp.launch_account_for_login(launcher, 'one')
            thread.call_args.kwargs['target']()
        self.assertFalse(task.done())
        launcher._present_login_browser.assert_called_once_with('one', dict(pid=123), task, 1)
        task.set_result(dict(pid=123, embedded_hwnd=456))
        self.assertTrue(task.done())
