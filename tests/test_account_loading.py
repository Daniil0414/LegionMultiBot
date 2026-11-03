"""Account login stages, duplicate prevention and the dialog's loading layer."""
from concurrent.futures import Future
import gc
import os
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

if os.name == 'nt':
    import app_container as app


@unittest.skipUnless(os.name == 'nt', 'Windows account login')
class LoginTaskTests(unittest.TestCase):
    def launcher(self):
        return SimpleNamespace(_closing_browsers=False, use_vpn=True,
            browser_profiles=Mock(generation=1), bot_profiles=[dict(key='one', name='Account 1')],
            status_lbl=Mock(), mute_var=Mock(get=lambda: False), browser_sessions={},
            get_launch_proxy=Mock(return_value='http://127.0.0.1:9000'), after=lambda delay, fn: fn())

    def test_one_pending_task_covers_vpn_and_browser_opening(self):
        launcher, progress = self.launcher(), []
        launcher._show_mine_control_after_game_open = Mock()
        def check(key, index, status_callback):
            status_callback('Server 2/3')
            return 'http://127.0.0.1:9000'
        launcher.get_launch_proxy.side_effect = check
        with patch.object(app.threading, 'Thread') as thread:
            task = app.HighQualityMultiBoxerApp.launch_account_for_login(launcher, 'one', progress.append)
            duplicate = app.HighQualityMultiBoxerApp.launch_account_for_login(launcher, 'one', progress.append)
            self.assertIs(duplicate, task)
            self.assertFalse(task.done())
            self.assertEqual(thread.call_count, 1)
            def open_browser(*args, **kwargs):
                self.assertFalse(task.done(), 'The loading task completed before browser creation')
                self.assertEqual(progress[-1][0], 'Открытие браузера')
                return dict(browser_pid=123)
            launcher.browser_profiles.launch.side_effect = open_browser
            thread.call_args.kwargs['target']()
        self.assertEqual(task.result(), dict(browser_pid=123))
        self.assertIn(('Проверка VPN', 'Server 2/3'), progress)
        self.assertEqual(launcher.browser_sessions['one'], task.result())
        launcher._show_mine_control_after_game_open.assert_called_once_with()

    def test_failed_vpn_and_browser_launch_both_release_pending_task(self):
        for failure in ('vpn', 'browser', 'cancelled'):
            with self.subTest(failure=failure):
                launcher = self.launcher()
                if failure == 'vpn':
                    launcher.get_launch_proxy.return_value = 'ERROR_ALL_DOWN'
                else:
                    launcher.browser_profiles.launch.side_effect = (app.LaunchCancelled('cancelled')
                        if failure == 'cancelled' else RuntimeError('fixture browser failure'))
                with patch.object(app.threading, 'Thread') as thread:
                    task = app.HighQualityMultiBoxerApp.launch_account_for_login(launcher, 'one', lambda data: None)
                    thread.call_args.kwargs['target']()
                    self.assertTrue(task.done())
                    self.assertIsNotNone(task.exception())
                    self.assertEqual(launcher.browser_sessions, {})
                    retry = app.HighQualityMultiBoxerApp.launch_account_for_login(launcher, 'one', lambda data: None)
                    self.assertIsNot(retry, task)
                    self.assertEqual(thread.call_count, 2)


@unittest.skipUnless(os.name == 'nt', 'Windows loading overlay')
class AccountLoadingTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        gc.collect()
        self.gc_enabled = gc.isenabled()
        gc.disable()
        self.root = tk.Tk()
        self.root.geometry('680x560+35+35')
        self.root.configure(bg='#0b1120')
        self.root.bot_profiles = [dict(key='one', name='Account 1')]
        self.root.use_vpn = True
        self.root.browser_profiles = Mock()
        self.root.sync_accounts_ui = Mock()
        self.root.profile_hwnds = {}
        self.root.browser_sessions = {}
        self.task = Future()
        self.progress = None
        def launch(key, progress=None):
            self.progress = progress
            return self.task
        self.root.launch_account_for_login = Mock(side_effect=launch)
        self.dialog = app.HighQualityMultiBoxerApp.open_accounts_dialog(self.root)
        self.root.update()
        self.overlay = self.dialog._login_loading

    def tearDown(self):
        self.root.destroy()
        self.root = self.dialog = self.overlay = self.progress = None
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def button(self, text):
        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'Button' and text in child['text']:
                    return child
                result = walk(child)
                if result:
                    return result
        return walk(self.dialog)

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.015)
        self.fail('Loading overlay did not settle')

    def test_blurred_dialog_blocks_repeated_login_and_restores_after_open(self):
        login = self.button('Войти / Проверить')
        login.invoke()
        self.root.update()
        self.assertTrue(self.overlay.busy)
        self.assertTrue(self.overlay.winfo_ismapped())
        self.assertIs(self.overlay.winfo_toplevel(), self.dialog)
        self.assertIsNotNone(self.overlay._snapshot, 'The Windows blur snapshot was not created')
        self.assertEqual(str(login['state']), 'disabled')
        login.invoke()
        self.assertEqual(self.root.launch_account_for_login.call_count, 1)
        self.assertIs(app.HighQualityMultiBoxerApp.open_accounts_dialog(self.root), self.dialog)
        self.progress(('Проверка VPN', 'Server 2/3'))
        self.wait(lambda: self.overlay.detail.get() == 'Server 2/3')
        self.progress(('Открытие браузера', 'Account 1'))
        self.wait(lambda: self.overlay.title.get() == 'Открытие браузера')
        self.assertTrue(self.overlay.busy)
        if os.environ.get('LEGION_ACCOUNT_LOADING_SCREENSHOT'):
            import ctypes
            from PIL import ImageGrab
            ImageGrab.grab(window=ctypes.windll.user32.GetAncestor(self.dialog.winfo_id(), 2)).save(
                os.environ['LEGION_ACCOUNT_LOADING_SCREENSHOT'])
        self.task.set_result(dict(browser_pid=123))
        self.wait(lambda: not self.overlay.busy)
        self.assertFalse(self.overlay.winfo_ismapped())
        self.assertEqual(str(login['state']), 'normal')
        self.assertIs(self.dialog.grab_current(), self.dialog)

    def test_error_restores_controls_before_showing_message_and_allows_retry(self):
        login = self.button('Войти / Проверить')
        with patch.object(app.messagebox, 'showerror') as show_error:
            login.invoke()
            self.task.set_exception(RuntimeError('fixture failure'))
            self.wait(lambda: show_error.called)
            self.assertFalse(self.overlay.busy)
            self.assertEqual(str(login['state']), 'normal')
            self.assertIn('fixture failure', show_error.call_args.args[1])
            self.assertIs(show_error.call_args.kwargs['parent'], self.dialog)
            self.task = Future()
            login.invoke()
            self.assertTrue(self.overlay.busy)
            self.assertEqual(self.root.launch_account_for_login.call_count, 2)
            self.task.set_result(dict(browser_pid=124))
            self.wait(lambda: not self.overlay.busy)

    def test_direct_mode_shows_browser_stage_without_vpn(self):
        self.root.use_vpn = False
        self.button('Войти / Проверить').invoke()
        self.assertEqual(self.overlay.title.get(), 'Открытие браузера')
        self.task.set_result(dict(browser_pid=127))
        self.wait(lambda: not self.overlay.busy)

    def test_new_account_waits_for_browser_before_success_message(self):
        with (patch.object(app.simpledialog, 'askstring', return_value='New account'),
              patch.object(app, 'save_accounts'), patch.object(app.messagebox, 'showinfo') as info):
            self.button('Добавить аккаунт').invoke()
            self.assertTrue(self.overlay.busy)
            info.assert_not_called()
            self.task.set_result(dict(browser_pid=125))
            self.wait(lambda: info.called)
            self.assertFalse(self.overlay.busy)

    def test_close_during_loading_does_not_destroy_dialog(self):
        self.button('Войти / Проверить').invoke()
        self.dialog.tk.call(self.dialog.protocol('WM_DELETE_WINDOW'))
        self.assertTrue(self.dialog.winfo_exists())
        self.task.set_result(dict(browser_pid=126))
        self.wait(lambda: not self.overlay.busy)
        self.button('Готово / Закрыть').invoke()
        self.assertIsNone(self.root._accounts_dialog)

    def test_accounts_dialog_closes_only_after_browser_is_embedded(self):
        self.button('Войти / Проверить').invoke()
        self.assertTrue(self.dialog.winfo_exists())
        self.task.set_result(dict(pid=123, embedded_hwnd=456))
        self.wait(lambda: self.root._accounts_dialog is None)
        self.assertFalse(self.dialog.winfo_exists())
