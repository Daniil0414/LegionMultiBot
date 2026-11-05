"""Exercise actual Win32 embedding with a local page and disposable Chrome profiles."""
import ctypes
import gc
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import Mock, patch

from browser_profiles import BrowserProfiles
from userscripts import UserscriptManager
from vpn_runtime import atomic_json


class DockPage(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b'''<!doctype html><title>Local docking test</title>
        <style>body{margin:0;background:#f1f5f9;font:18px sans-serif}
        header,footer{position:fixed;left:0;right:0;padding:12px;color:white}
        header{top:0;background:#15803d}footer{bottom:0;background:#1d4ed8}
        main{padding:90px 16px}</style><header>TOP: page starts here</header>
        <main>Only the page should be visible.<br>No Chrome toolbar.</main>
        <footer>BOTTOM: page ends here</footer>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME'),
                     'Set LEGION_TEST_CHROME for the native docking test')
class DockedChromeTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        def collect_on_main_thread():
            gc.collect()
            if enabled:
                gc.enable()
        self.addCleanup(collect_on_main_thread)

    def test_page_fills_slot_after_resize_reparent_and_dpi_change(self):
        import tkinter as tk
        import app_container as app

        app.init_windows_dpi_awareness()
        user32 = app.user32
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def rect(hwnd):
            result = wintypes.RECT()
            self.assertTrue(user32.GetWindowRect(hwnd, ctypes.byref(result)))
            return result.left, result.top, result.right, result.bottom

        def browser_window(pid):
            found = []

            @callback
            def visit(hwnd, _):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, len(title))
                if owner.value == pid and title.value.startswith('Local docking test'):
                    found.append(hwnd)
                return True

            user32.EnumWindows(visit, 0)
            return found[0] if found else None

        server = ThreadingHTTPServer(('127.0.0.1', 0), DockPage)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        root = tk.Tk()
        callback_errors = []
        root.report_callback_exception = lambda *details: callback_errors.append(''.join(traceback.format_exception(*details)))
        root.title('Legion local docking fixture')
        root.geometry('360x520+20+30')
        frames = [tk.Frame(root, bg='black') for _ in range(2)]
        for frame in frames:
            frame.place(x=0, y=0, width=360, height=520)
        root.update()

        class Harness:
            dock_hwnd_to_slot = app.HighQualityMultiBoxerApp.dock_hwnd_to_slot
            reload_slot = app.HighQualityMultiBoxerApp.reload_slot
            _restore_slot_docking = app.HighQualityMultiBoxerApp._restore_slot_docking
            slot_cards = [{'game_area': frame, 'btn_reload': Mock(), 'bot_lbl': Mock()} for frame in frames]
            fullscreen_slot = None
            _docked_hwnds = set()
            _chrome_insets = {}
            profile_hwnds = {}
            slot_urls = {}
            current_page = 1
            page_size = 2
            current_grid_key = '6x2'
            bot_profiles = [dict(key='unused', name='Unused', gaia=''),
                            dict(key='fixture', name='Fixture', gaia='')]
            status_lbl = Mock()
            update_slot_proxy_badge = Mock()
            get_zoom_factor = staticmethod(lambda: 1.0)
            after = staticmethod(root.after)

        harness = Harness()
        focus_router = app.DockedBrowserFocus(root, lambda: harness.profile_hwnds.values())

        def wait_until(predicate, timeout=5, details=None):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                root.update()
                result = predicate()
                if result:
                    return result
                time.sleep(.025)
            self.fail(f'Native Chrome content did not settle inside its slot: {details() if details else ""}')

        try:
            with tempfile.TemporaryDirectory(prefix='legion-docking-test-') as tmp:
                atomic_json(str(Path(tmp) / 'scripts.json'), {'version': 2, 'scripts': []})
                profiles = BrowserProfiles(root=Path(tmp) / 'profiles', allow_direct=True,
                                           userscripts=UserscriptManager(Path(tmp) / 'scripts.json'))
                harness.browser_profiles = profiles
                try:
                    for scale in (None, 1.5):
                        arguments = ['--window-size=600,700', f'--app=http://127.0.0.1:{server.server_port}/']
                        if scale:
                            arguments.insert(0, f'--force-device-scale-factor={scale}')
                        profiles.launch(os.environ['LEGION_TEST_CHROME'], 'fixture', 'DIRECT', arguments)
                        browser = profiles._controllers['fixture']
                        hwnd = wait_until(lambda: browser_window(browser.process.pid))
                        harness.profile_hwnds = {'fixture': hwnd}
                        for slot, width, height, fullscreen in ((0, 320, 480, None), (0, 210, 360, None),
                                                               (1, 360, 520, None), (1, 700, 520, 1),
                                                               (1, 1280, 720, 1), (1, 360, 520, None)):
                            with self.subTest(scale=scale, size=(width, height), slot=slot):
                                frame = frames[slot]
                                root.geometry(f'{width}x{height}+20+30')
                                frame.place(width=width, height=height)
                                frame.lift()
                                root.update()
                                harness.fullscreen_slot = fullscreen
                                harness.dock_hwnd_to_slot(hwnd, slot, width, height)

                                def aligned():
                                    insets = app.chrome_content_insets(hwnd)
                                    if insets is None:
                                        return False
                                    left, top, right, bottom = insets
                                    x1, y1, x2, y2 = rect(hwnd)
                                    fx, fy, _, _ = rect(frame.winfo_id())
                                    # At fractional display scales Chrome rounds the far
                                    # edge of its CSS viewport to a whole physical pixel.
                                    tolerance = 1 if scale else 0
                                    return ((x1 + left, y1 + top) == (fx, fy)
                                        and abs(x2 - right - (fx + width)) <= tolerance
                                        and abs(y2 - bottom - (fy + height)) <= tolerance)

                                wait_until(aligned, details=lambda: dict(window=rect(hwnd),
                                    frame=rect(frame.winfo_id()), insets=app.chrome_content_insets(hwnd)))
                                # A click must move OS keyboard focus out of Tk,
                                # including after reparenting and fullscreen sizes.
                                # Model the foreground precondition of a real
                                # click without activating over the user's app.
                                with patch.object(user32, 'GetForegroundWindow',
                                                  return_value=user32.GetAncestor(hwnd, 2)):
                                    root.focus_force()
                                    root.update()
                                    focus_router.clicked(hwnd)
                                    wait_until(lambda: user32.IsChild(hwnd, user32.GetFocus()),
                                               details=lambda: dict(focus=user32.GetFocus(),
                                                   browser=hwnd, visible=user32.IsWindowVisible(hwnd)))
                                # Check again after all deferred layout corrections.
                                until = time.monotonic() + .65
                                while time.monotonic() < until:
                                    root.update()
                                    time.sleep(.025)
                                self.assertTrue(aligned(), 'Delayed docking moved the page out of the slot')
                                self.assertEqual(user32.GetParent(hwnd), frame.winfo_id())
                                if scale is None and slot == 1 and fullscreen is None and os.environ.get('LEGION_DOCK_SCREENSHOT'):
                                    from PIL import ImageGrab
                                    root.lift()
                                    root.update()
                                    time.sleep(.2)
                                    ImageGrab.grab(bbox=rect(frame.winfo_id())).save(os.environ['LEGION_DOCK_SCREENSHOT'])
                        if scale is None:
                            from mine_control_ui import MineControlPanel
                            try:
                                from PIL import ImageGrab, ImageChops
                            except ImportError:
                                ImageGrab = None
                            previous_topmost = root.attributes('-topmost')
                            panel = MineControlPanel(root, profiles)
                            try:
                                baseline = None
                                if ImageGrab is not None:
                                    root.attributes('-topmost', True)
                                    root.update()
                                    time.sleep(.1)
                                    baseline = ImageGrab.grab(bbox=rect(root.winfo_id()))
                                panel.show()
                                root.update()
                                self.assertIsInstance(panel, tk.Toplevel)
                                # Owned frameless overlay stays on top of root and its embedded Chrome
                                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                    app.SWP_NOMOVE | app.SWP_NOSIZE | app.SWP_NOACTIVATE)
                                root.update()
                                self.assertTrue(aligned())
                                if baseline is not None:
                                    old = panel._bounds()
                                    panel.header.event_generate('<ButtonPress-1>', x=8, y=8)
                                    panel.header.event_generate('<B1-Motion>',
                                        rootx=root.winfo_rootx() + 24, rooty=root.winfo_rooty() + 280)
                                    root.update_idletasks()
                                    panel.header.event_generate('<ButtonRelease-1>', x=8, y=8)
                                    self.assertGreaterEqual(panel.winfo_y(), old[3])
                                    def browser_repainted():
                                        image = ImageGrab.grab(bbox=rect(root.winfo_id()))
                                        return ImageChops.difference(baseline.crop(old), image.crop(old)).getbbox() is None
                                    wait_until(browser_repainted)
                                if os.environ.get('LEGION_MINE_EMBED_SCREENSHOT'):
                                    root.update()
                                    time.sleep(.2)
                                    ImageGrab.grab(bbox=rect(root.winfo_id())).save(
                                        os.environ['LEGION_MINE_EMBED_SCREENSHOT'])
                                panel.hide()
                                root.update()
                                self.assertFalse(panel.winfo_ismapped())
                            finally:
                                root.attributes('-topmost', previous_topmost)
                                panel.destroy()
                        session = browser.wait_target(browser.app_target)
                        with ThreadPoolExecutor(max_workers=1) as commands:
                            def evaluate(expression, **options):
                                # Fullscreen sends synchronous messages to the Tk
                                # parent. Keep its event loop running during CDP.
                                future = commands.submit(browser.connection.call, 'Runtime.evaluate',
                                    dict(expression=expression, returnByValue=True, **options), session)
                                wait_until(future.done, timeout=20)
                                return future.result()

                            for path in ('/ip', '/game'):
                                navigation = commands.submit(profiles.navigate_app, 'fixture',
                                    f'http://127.0.0.1:{server.server_port}{path}')
                                wait_until(navigation.done, timeout=20)
                                navigation.result()
                                wait_until(lambda: evaluate('location.pathname')['result'].get('value') == path)
                            history_before = browser.connection.call('Page.getNavigationHistory', {}, session)
                            fullscreen_result = evaluate('document.documentElement.requestFullscreen()',
                                                         userGesture=True, awaitPromise=True)
                            self.assertNotIn('exceptionDetails', fullscreen_result)
                            self.assertIs(evaluate('!!document.fullscreenElement')['result']['value'], True)
                            before_reload = evaluate('performance.timeOrigin')['result']['value']
                            harness.profile_hwnds = {'fixture': hwnd}
                            harness.reload_slot(1)

                            def reloaded_and_aligned():
                                result = evaluate('[performance.timeOrigin, !!document.fullscreenElement, location.pathname]')
                                values = result.get('result', {}).get('value', [])
                                return (len(values) == 3 and values[0] != before_reload and not values[1]
                                        and values[2] == '/game'
                                        and aligned())

                            wait_until(reloaded_and_aligned, details=lambda: dict(window=rect(hwnd),
                                frame=rect(frame.winfo_id()), insets=app.chrome_content_insets(hwnd),
                                parent=user32.GetParent(hwnd), style=hex(user32.GetWindowLongW(hwnd, app.GWL_STYLE)),
                                url=evaluate('location.href').get('result', {}).get('value')))
                            history_after = browser.connection.call('Page.getNavigationHistory', {}, session)
                            self.assertEqual(history_after['currentIndex'], history_before['currentIndex'],
                                             'Reload changed the current browser history entry')
                        harness.profile_hwnds = {}
                        profiles.close()
                    self.assertEqual(callback_errors, [], 'A deferred Tk callback failed')
                finally:
                    profiles.close()
        finally:
            root.destroy()
            server.shutdown()
            server.server_close()
