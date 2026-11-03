"""Focus routing must preserve native Chrome shortcuts and other windows' input."""
import ctypes
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

if os.name == 'nt':
    import browser_focus as focus


@unittest.skipUnless(os.name == 'nt', 'Windows native browser focus')
class BrowserFocusTests(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        self.native.SetWindowsHookExW.return_value = 77
        self.native.GetAncestor.return_value = 1
        self.native.GetForegroundWindow.return_value = 1
        self.native.GetFocus.return_value = 9
        self.native.IsChild.side_effect = lambda parent, child: parent == 100 and child == 101
        self.patch = patch.object(focus, 'user32', self.native)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.root = Mock()
        self.browsers = [100]
        self.router = focus.DockedBrowserFocus(self.root, lambda: self.browsers)
        self.addCleanup(self.router.close)

        def children(browser, callback, data):
            callback(101, data)

        def classname(child, name, length):
            name.value = 'Chrome_RenderWidgetHostHWND'

        def bounds(child, pointer):
            rect = ctypes.cast(pointer, ctypes.POINTER(focus.wintypes.RECT)).contents
            rect.right, rect.bottom = 640, 480
            return True

        self.native.EnumChildWindows.side_effect = children
        self.native.GetClassNameW.side_effect = classname
        self.native.GetWindowRect.side_effect = bounds

    def flush_click(self, target=101):
        self.router.clicked(target)
        self.root.after_idle.call_args.args[0]()

    def test_click_focuses_native_renderer_without_changing_clipboard_or_keyboard(self):
        self.flush_click()
        self.native.SetFocus.assert_called_once_with(101)

    def test_click_inside_another_application_or_tk_entry_is_ignored(self):
        self.router.clicked(999)
        self.root.after_idle.assert_not_called()

    def test_background_hidden_closed_and_removed_browsers_cannot_take_focus(self):
        for condition in ('background', 'hidden', 'closed', 'removed'):
            with self.subTest(condition=condition):
                self.native.GetForegroundWindow.return_value = 2 if condition == 'background' else 1
                self.native.IsWindowVisible.return_value = condition != 'hidden'
                self.native.IsWindow.return_value = condition != 'closed'
                self.browsers[:] = [100]
                self.router.clicked(101)
                if condition == 'removed':
                    self.browsers.clear()
                self.root.after_idle.call_args.args[0]()
                self.native.SetFocus.assert_not_called()

    def test_existing_browser_focus_is_preserved(self):
        self.native.GetFocus.return_value = 101
        self.flush_click()
        self.native.SetFocus.assert_not_called()

    def test_right_click_does_not_take_focus_from_chrome_context_menu(self):
        self.router.clicked(101, restore=False)
        self.root.after_idle.assert_not_called()

    def test_later_click_in_tk_cancels_pending_browser_focus(self):
        self.router.clicked(101)
        pending = self.root.after_idle.call_args.args[0]
        self.router.clicked(999)
        pending()
        self.native.SetFocus.assert_not_called()

    def test_native_hook_queues_click_without_reentering_tk(self):
        event = focus.MouseEvent()
        self.native.WindowFromPoint.return_value = 101
        self.root.reset_mock()
        self.router._mouse_event(0, 0x0201, ctypes.addressof(event))
        self.assertEqual(self.root.mock_calls, [])
        self.native.CallNextHookEx.assert_called_once()
        self.router._poll()
        self.root.after_idle.call_args.args[0]()
        self.native.SetFocus.assert_called_once_with(101)

    def test_destroy_child_keeps_hook_but_destroy_root_releases_hook_and_timers(self):
        self.router._destroyed(SimpleNamespace(widget=object()))
        self.native.UnhookWindowsHookEx.assert_not_called()
        self.router._destroyed(SimpleNamespace(widget=self.root))
        self.router.close()
        self.native.UnhookWindowsHookEx.assert_called_once_with(77)
        self.root.after_cancel.assert_called()
