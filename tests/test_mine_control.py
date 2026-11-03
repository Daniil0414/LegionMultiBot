"""All-profile mine commands, cancellation, browser acknowledgements and overlay UI."""
import json
import gc
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from browser_profiles import BrowserProfiles
from userscripts import UserscriptManager, compile_script


SOURCE = '''// ==UserScript==
// @namespace https://github.com/Daniil0414/LegionAutoMiner
// @match http://device-a.localhost/*
// @run-at document-idle
// ==/UserScript==
(() => {
  window.__mineBotRunning = true;
  let isPaused = true;
  const panel = document.createElement('div'); panel.id = 'mine-bot-v3';
  const btn = document.createElement('button'); btn.textContent = 'Start';
  panel.appendChild(btn); document.body.appendChild(panel);
  document.body.dataset.clicks = '0'; document.body.dataset.mineState = 'paused';
  function startBot() { isPaused = false; document.body.dataset.mineState = 'running'; }
  function pauseBot() { isPaused = true; document.body.dataset.mineState = 'paused'; }
  btn.addEventListener('click', () => { document.body.dataset.clicks = String(+document.body.dataset.clicks + 1); });
  btn.onclick = () => { if (isPaused) { startBot(); } else { pauseBot(); } };
  window.closeFixture = () => { window.__mineBotRunning = false; };
})();'''


class BatchMineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='legion-mine-batch-')
        self.addCleanup(temp.cleanup)
        self.profiles = BrowserProfiles(root=temp.name, userscripts=UserscriptManager(Path(temp.name) / 'scripts.json'))
        self.addCleanup(self.profiles.close)

    @staticmethod
    def controller(command):
        return SimpleNamespace(command_mine=command, close=lambda: None,
                               connection=SimpleNamespace(closed=threading.Event(), job=None))

    def test_all_open_profiles_are_addressed_even_if_one_fails(self):
        called = []
        def success(key):
            def send(action):
                called.append((key, action))
                return [dict(ok=True, state='running')]
            return send
        def fail(action):
            raise RuntimeError('fixture disconnected')
        self.profiles._controllers = {key: self.controller(success(key)) for key in ('page-1', 'page-9')}
        self.profiles._controllers['broken'] = self.controller(fail)
        results = self.profiles.command_mine_all('start')
        self.assertEqual(set(called), {('page-1', 'start'), ('page-9', 'start')})
        self.assertEqual(sum(result['ok'] for result in results), 2)
        self.assertEqual(next(r['error'] for r in results if r['key'] == 'broken'), 'unavailable')

    def test_stop_supersedes_queued_starts_and_runs_after_inflight_starts(self):
        release, eight_started = threading.Event(), threading.Event()
        lock, calls = threading.Lock(), {str(i): [] for i in range(12)}
        def command(key):
            def send(action):
                with lock:
                    calls[key].append(action)
                    if sum(values.count('start') for values in calls.values()) == 8:
                        eight_started.set()
                if action == 'start' and not release.wait(5):
                    raise TimeoutError('fixture start was not released')
                return [dict(ok=True, state='running' if action == 'start' else 'paused')]
            return send
        self.profiles._controllers = {key: self.controller(command(key)) for key in calls}
        start = threading.Thread(target=lambda: self.profiles.command_mine_all('start', requested_at=1))
        stop = threading.Thread(target=lambda: self.profiles.command_mine_all('pause', requested_at=2))
        start.start()
        try:
            self.assertTrue(eight_started.wait(3))
            stop.start()
            deadline = time.monotonic() + 3
            while self.profiles._mine_requested_at != 2 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.profiles._mine_requested_at, 2)
        finally:
            release.set()
            start.join(5)
            if stop.ident is not None:
                stop.join(5)
        self.assertFalse(start.is_alive() or stop.is_alive())
        self.assertEqual(sum(values.count('start') for values in calls.values()), 8)
        self.assertTrue(all(values[-1] == 'pause' for values in calls.values()))
        self.assertEqual(self.profiles.command_mine_all('start', requested_at=1), [])
        self.assertTrue(all(values[-1] == 'pause' for values in calls.values()))

    def test_empty_swarm_and_invalid_commands_do_not_launch_browsers(self):
        self.assertEqual(self.profiles.command_mine_all('pause'), [])
        with self.assertRaises(ValueError):
            self.profiles.command_mine_all('anything')

    def test_supported_cached_script_has_one_mine_control(self):
        path = Path(__file__).resolve().parents[1] / 'userscript_config.json'
        if not path.exists():
            self.skipTest('No installed script in this checkout')
        entries = json.loads(path.read_text(encoding='utf-8')).get('scripts', [])
        checked = 0
        for entry in entries:
            if entry.get('installed') and 'Daniil0414/LegionAutoMiner' in entry.get('source', ''):
                self.assertTrue(compile_script(entry['source'])['mine_control'], entry.get('version'))
                checked += 1
        if not checked:
            self.skipTest('No supported installed script')


@unittest.skipUnless(os.environ.get('LEGION_TEST_CHROME'), 'Set LEGION_TEST_CHROME for browser integration')
class BrowserMineTests(unittest.TestCase):
    def setUp(self):
        import test_mobile_browser as fixture
        fixture.MobileIntegrationTests.setUp(self)
        self.profiles.emulate_mobile = False
        self.launch = lambda key='account-a': fixture.MobileIntegrationTests.launch(self, key)
        self.evaluate = lambda expression, **kw: fixture.MobileIntegrationTests.evaluate(self, expression, **kw)

    def ready(self, **kw):
        self.evaluate('''new Promise((resolve,reject) => {
          const until = Date.now() + 5000;
          function check() {
            if (document.body?.dataset.mineState) resolve(true);
            else if (Date.now() > until) reject(new Error('Mine fixture not ready'));
            else setTimeout(check, 25);
          } check();
        })''', await_promise=True, **kw)

    def test_multiple_profiles_windows_repeat_commands_and_reload(self):
        with patch('userscripts.download_script', return_value=SOURCE):
            self.profiles.install_userscript('https://raw.githubusercontent.com/example/bot/main/mine.js')
            self.launch('account-a')
            self.launch('account-b')
            first = self.profiles._controllers['account-a']
            def live_target(browser):
                return next(item['targetId'] for item in browser.connection.call('Target.getTargets')['targetInfos']
                            if item['type'] == 'page' and item['url'].startswith('http://device-a.localhost/'))
            target = live_target(first)
            extra = first.navigate('http://device-a.localhost/extra', new_window=True)
            self.ready(target=target)
            self.ready(target=extra)
            self.ready(key='account-b')
            self.launch('account-c')
            third = self.profiles._controllers['account-c']
            third_target = live_target(third)
            self.ready(key='account-c', target=third_target)
            third.navigate('http://device-b.localhost/no-script', target_id=third_target)
            for action, state, clicks in [('start', 'running', '1'), ('start', 'running', '1'),
                                           ('pause', 'paused', '2'), ('pause', 'paused', '2')]:
                with self.subTest(action=action, clicks=clicks):
                    results = self.profiles.command_mine_all(action)
                    self.assertEqual(sum(r.get('ok') is True for r in results), 3, results)
                    self.assertEqual([r['error'] for r in results if not r.get('ok')], ['not_ready'])
                    for key, page in [('account-a', target), ('account-a', extra), ('account-b', None)]:
                        actual = self.evaluate('[document.body.dataset.mineState,document.body.dataset.clicks]',
                                               key=key, target=page)
                        self.assertEqual(actual, [state, clicks])
            first.navigate('http://device-a.localhost/reloaded', target_id=target)
            self.ready(target=target)
            self.assertTrue(all(r['ok'] for r in first.command_mine('start')))
            self.assertEqual(self.evaluate('document.body.dataset.clicks', target=target), '1')
            self.evaluate('closeFixture()', target=target)
            self.assertIn('unavailable', [r.get('error') for r in first.command_mine('pause')])
            first.set_userscript(None)
            self.assertEqual(first.command_mine('start'), [dict(ok=False, error='unsupported')])


@unittest.skipUnless(os.name == 'nt', 'Native Windows overlay')
class OverlayMineTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        def collect_on_main_thread():
            gc.collect()
            if enabled:
                gc.enable()
        self.addCleanup(collect_on_main_thread)

    def test_drag_repaints_exposed_slots_without_leaving_panel_pixels(self):
        import tkinter as tk
        import app_container as app
        from mine_control_ui import MineControlPanel
        try:
            from PIL import ImageGrab, ImageChops
        except ImportError:
            self.skipTest('Pillow is required for the screen repaint regression')
        app.init_windows_dpi_awareness()
        root = tk.Tk()
        root.geometry('744x504+30+30')
        root.attributes('-topmost', True)
        host = tk.Frame(root, bg='black')
        host.pack(fill='both', expand=True, padx=12, pady=12)
        cards = []
        for index in range(6):
            box = tk.Frame(host, bg='#090d16')
            box.place(x=(index % 3) * 240, y=(index // 3) * 240, width=240, height=240)
            tk.Label(box, text=f'Slot {index + 1}', bg='#131d33', fg='#60a5fa').pack(fill='x')
            game = tk.Frame(box, bg='black')
            game.pack(fill='both', expand=True)
            tk.Label(game, text=f'Account {index + 1}', bg='black', fg='#387599').place(relx=.5, rely=.5, anchor='center')
            cards.append(dict(box=box, game_area=game))
        root.update()
        app.HighQualityMultiBoxerApp.apply_clip_children_styles(SimpleNamespace(
            update_idletasks=root.update_idletasks, winfo_id=root.winfo_id, slots_frame=host, slot_cards=cards))
        root.update()
        def capture():
            # Capture the actual displayed pixels: PrintWindow forces a fresh
            # paint and would conceal the stale pixels being tested here.
            x, y = host.winfo_rootx(), host.winfo_rooty()
            return ImageGrab.grab(bbox=(x, y, x + host.winfo_width(), y + host.winfo_height()))
        try:
            # Let the Windows opening animation finish before comparing pixels.
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            baseline = capture()
            if os.environ.get('LEGION_MINE_DRAG_SCREENSHOT'):
                baseline.save(str(Path(os.environ['LEGION_MINE_DRAG_SCREENSHOT']).with_name('legion-drag-baseline.png')))
            panel = MineControlPanel(host, SimpleNamespace())
            panel.show()
            root.update()
            panel.header.event_generate('<ButtonPress-1>', x=8, y=8)
            for x, y in [(80, 40), (160, 90), (250, 150), (350, 210), (120, 250), (20, 30)] * 3:
                previous = panel.winfo_x(), panel.winfo_y()
                panel.header.event_generate('<B1-Motion>', rootx=host.winfo_rootx() + x + 8,
                                            rooty=host.winfo_rooty() + y + 8)
                # Keep input queued as during a continuous drag. Geometry and
                # drawing idles run, while general window messages remain busy.
                root.update_idletasks()
                self.assertNotEqual((panel.winfo_x(), panel.winfo_y()), previous)
                time.sleep(.02)
                actual = capture()
                px, py = panel.winfo_x(), panel.winfo_y()
                right, bottom = px + panel.winfo_width(), py + panel.winfo_height()
                regions = [(0, 0, 720, py), (0, bottom, 720, 480), (0, py, px, bottom), (right, py, 720, bottom)]
                differences = [region for region in regions if region[2] > region[0] and region[3] > region[1]
                               and ImageChops.difference(baseline.crop(region), actual.crop(region)).getbbox()]
                if differences and os.environ.get('LEGION_MINE_DRAG_SCREENSHOT'):
                    actual.save(os.environ['LEGION_MINE_DRAG_SCREENSHOT'])
                self.assertEqual(differences, [], f'Pixels outside the panel changed after moving to {(x, y)}')
            panel.header.event_generate('<ButtonRelease-1>', x=8, y=8)
            if os.environ.get('LEGION_MINE_DRAG_SCREENSHOT'):
                capture().save(os.environ['LEGION_MINE_DRAG_SCREENSHOT'])
            panel.hide()
            root.update_idletasks()
            self.assertIsNone(ImageChops.difference(baseline, capture()).getbbox(), 'Hiding the panel left stale pixels')
        finally:
            root.destroy()

    def test_stop_remains_available_and_old_start_result_cannot_replace_pause_status(self):
        import tkinter as tk
        from mine_control_ui import MineControlPanel
        root = tk.Tk()
        root.geometry('450x330+40+40')
        root.update()
        release = threading.Event()
        def command(action, **kwargs):
            if action == 'start':
                release.wait(3)
            return [dict(key='a', ok=True, state='running' if action == 'start' else 'paused')]
        overlay = MineControlPanel(root, SimpleNamespace(command_mine_all=command), lambda: {'a': 'Account 1'})
        def settle(predicate):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                root.update()
                if predicate():
                    return
                time.sleep(.02)
            self.fail('Overlay did not settle')
        try:
            overlay.show()
            self.assertEqual(overlay.toggle_button['text'], 'Запустить автошахту для всех')
            overlay.toggle_button.invoke()
            self.assertEqual(str(overlay.toggle_button['state']), 'normal')
            self.assertEqual(overlay.toggle_button['text'], 'Стоп')
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            self.assertIn('на паузе: 1 из 1', overlay.status.get())
            self.assertEqual(overlay.toggle_button['text'], 'Запустить автошахту для всех')
            release.set()
            time.sleep(.05)
            overlay._poll()
            self.assertIn('на паузе: 1 из 1', overlay.status.get())
            self.assertEqual(overlay.toggle_button['text'], 'Запустить автошахту для всех')
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            self.assertIn('Копание включено: 1 из 1', overlay.status.get())
            self.assertEqual(overlay.toggle_button['text'], 'Стоп')
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            self.assertIn('на паузе: 1 из 1', overlay.status.get())
            self.assertEqual(overlay.toggle_button['text'], 'Запустить автошахту для всех')
            if os.environ.get('LEGION_MINE_SCREENSHOT'):
                from PIL import ImageGrab
                root.update()
                time.sleep(.15)
                x, y = overlay.winfo_rootx(), overlay.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + overlay.winfo_width(), y + overlay.winfo_height())).save(
                    os.environ['LEGION_MINE_SCREENSHOT'])
            overlay.profiles.command_mine_all = lambda *args, **kw: [dict(key='a', ok=False, error='not_ready')]
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            self.assertIn('Не выполнено: 1', overlay.status.get())
            self.assertIn('Account 1', overlay.details.get('1.0', 'end'))
            self.assertEqual(overlay.toggle_button['text'], 'Запустить автошахту для всех')
            overlay.profiles.command_mine_all = command
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            overlay.profiles.command_mine_all = lambda *args, **kw: [dict(key='a', ok=False, error='unavailable')]
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            self.assertEqual(overlay.toggle_button['text'], 'Стоп')
            self.assertEqual(str(overlay.toggle_button['state']), 'normal')
            overlay.profiles.command_mine_all = command
            overlay.toggle_button.invoke()
            settle(lambda: not overlay._busy)
            self.assertEqual(overlay.toggle_button['text'], 'Запустить автошахту для всех')
            overlay.hide()
            overlay.show()
            self.assertTrue(overlay.winfo_exists())
        finally:
            release.set()
            root.destroy()

    def test_panel_moves_with_parent_and_drag_stays_inside_grid(self):
        import tkinter as tk
        from mine_control_ui import MineControlPanel
        root = tk.Tk()
        root.geometry('700x480+40+40')
        host = tk.Frame(root, bg='#090d16')
        host.pack(fill='both', expand=True)
        root.update()
        panel = MineControlPanel(host, SimpleNamespace())
        try:
            panel.show()
            root.update()
            self.assertIs(panel.winfo_toplevel(), root)
            self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in root.winfo_children()))
            local = panel.winfo_x(), panel.winfo_y()
            root.geometry('+160+130')
            root.update()
            self.assertEqual((panel.winfo_x(), panel.winfo_y()), local)
            self.assertEqual((panel.winfo_rootx() - host.winfo_rootx(), panel.winfo_rooty() - host.winfo_rooty()), local)
            panel.header.event_generate('<ButtonPress-1>', x=8, y=8)
            panel.header.event_generate('<B1-Motion>', rootx=host.winfo_rootx() + 2000,
                                        rooty=host.winfo_rooty() + 2000)
            root.update()
            panel.header.event_generate('<ButtonRelease-1>', x=8, y=8)
            self.assertLessEqual(panel.winfo_x() + panel.winfo_width(), host.winfo_width())
            self.assertLessEqual(panel.winfo_y() + panel.winfo_height(), host.winfo_height())
            self.assertIsNone(panel.grab_current())
            root.geometry('380x260')
            root.update()
            self.assertLessEqual(panel.winfo_x() + panel.winfo_width(), host.winfo_width())
            self.assertLessEqual(panel.winfo_y() + panel.winfo_height(), host.winfo_height())
            root.withdraw()
            root.update()
            self.assertFalse(panel.winfo_ismapped())
            root.deiconify()
            root.update()
            self.assertTrue(panel.winfo_ismapped())
            panel.hide()
            root.update()
            self.assertFalse(panel.winfo_ismapped())
            panel.show()
            root.update()
            self.assertTrue(panel.winfo_ismapped())
        finally:
            root.destroy()
