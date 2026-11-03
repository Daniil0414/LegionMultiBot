"""Restore native keyboard focus when clicking a Chrome embedded in Tk."""
import ctypes
from ctypes import wintypes
import logging
import queue


user32 = ctypes.windll.user32
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class MouseEvent(ctypes.Structure):
    _fields_ = [('point', wintypes.POINT), ('mouse_data', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('extra_info', ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetFocus.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


class DockedBrowserFocus:
    def __init__(self, root, browser_hwnds):
        self.root = root
        self.browser_hwnds = browser_hwnds
        self._serial = 0
        self._pending = None
        self._clicks = queue.SimpleQueue()
        self._poll_timer = None
        self._callback = HOOKPROC(self._mouse_event)
        self._hook = user32.SetWindowsHookExW(14, self._callback, None, 0)  # WH_MOUSE_LL
        if not self._hook:
            raise ctypes.WinError()
        root.bind('<Destroy>', self._destroyed, add='+')
        self._poll_timer = root.after(20, self._poll)

    def _mouse_event(self, code, message, data):
        try:
            if code >= 0 and message in (0x0201, 0x0204, 0x0207):
                point = ctypes.cast(data, ctypes.POINTER(MouseEvent)).contents.point
                # A native hook can interrupt Tcl's event loop. Never reenter Tk
                # here; transfer only the clicked HWND to its normal timer.
                self._clicks.put((user32.WindowFromPoint(point), message == 0x0201))
        except Exception:
            logging.getLogger('legion.launch').exception('Could not restore embedded Chrome focus')
        # Observe clicks without consuming, replaying or changing their contents.
        return user32.CallNextHookEx(self._hook, code, message, data)

    def _poll(self):
        self._poll_timer = None
        target = None
        while not self._clicks.empty():
            target = self._clicks.get_nowait()
        if target is not None:
            self.clicked(*target)
        if self._hook:
            self._poll_timer = self.root.after(20, self._poll)

    def clicked(self, target, restore=True):
        self._serial += 1
        serial = self._serial
        if not restore:
            return  # Leave context menus and middle-click actions to Chrome.
        for browser in tuple(self.browser_hwnds()):
            if target == browser or user32.IsChild(browser, target):
                # Wait for Windows/Tk activation and Chrome's own click handling.
                self._pending = self.root.after_idle(lambda: self._focus(browser, serial))
                break

    def _focus(self, browser, serial):
        self._pending = None
        if (not self._hook or serial != self._serial or browser not in self.browser_hwnds()
                or not user32.IsWindow(browser) or not user32.IsWindowVisible(browser)
                or user32.GetAncestor(browser, 2) != user32.GetForegroundWindow()):  # GA_ROOT
            return
        focus = user32.GetFocus()
        if focus == browser or user32.IsChild(browser, focus):
            return
        renderers = []

        @ENUMPROC
        def visit(child, _):
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, name, len(name))
            if name.value == 'Chrome_RenderWidgetHostHWND' and user32.IsWindowVisible(child):
                bounds = wintypes.RECT()
                if user32.GetWindowRect(child, ctypes.byref(bounds)):
                    area = (bounds.right - bounds.left) * (bounds.bottom - bounds.top)
                    if area > 0:
                        renderers.append((area, child))
            return True

        user32.EnumChildWindows(browser, visit, 0)
        if renderers:
            # SetParent already joins the input queues; no extra attachment or
            # keyboard/clipboard interception is needed. Chrome handles shortcuts.
            user32.SetFocus(max(renderers)[1])

    def _destroyed(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._serial += 1
        if self._pending is not None:
            self.root.after_cancel(self._pending)
            self._pending = None
        if self._poll_timer is not None:
            self.root.after_cancel(self._poll_timer)
            self._poll_timer = None
