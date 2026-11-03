"""Compare launcher-visible signals with ordinary, non-debugged Chrome locally."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from unittest.mock import patch

from browser_lifecycle import BrowserJob, resume_browser
from browser_profiles import BrowserProfiles
from chrome_mobile import MobileBrowser
from userscripts import UserscriptManager
from vpn_runtime import atomic_json


PROBE = r'''(() => {
const report = {
  scriptBeforePage: window.fixtureBeforePage === true,
  webdriver: navigator.webdriver, touch: navigator.maxTouchPoints,
  screen: [screen.width, screen.height, screen.availWidth, screen.availHeight, screen.colorDepth],
  viewport: [innerWidth, innerHeight, outerWidth, outerHeight, devicePixelRatio, visualViewport.scale],
  pointer: [matchMedia('(pointer:coarse)').matches, matchMedia('(pointer:fine)').matches, matchMedia('(hover:hover)').matches],
  hints: navigator.userAgentData?.toJSON(),
  plugins: Array.from(navigator.plugins, plugin => plugin.name),
  browser: Object.keys(window.chrome || {}),
  globals: Object.getOwnPropertyNames(window).sort(),
  hiddenOwn: !!Object.getOwnPropertyDescriptor(document, 'hidden'),
  natives: [Function.prototype.toString.call(fetch), Function.prototype.toString.call(requestAnimationFrame),
    Function.prototype.toString.call(Function.prototype.toString)]
};
let access = 0;
const error = new Error();
Object.defineProperty(error, 'stack', {get() { access++; return 'local probe'; }});
console.log(error);
report.consoleStackAccess = access;
fetch('/collect', {method:'POST', body:JSON.stringify(report)});
})();'''


class ProbeServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, address):
        # Chrome can close denied background connections while shutting down.
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError)):
            super().handle_error(request, address)


class ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path != '/probe':
            self.send_response(204)
            self.end_headers()
            return
        body = ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<body><script>' + PROBE + '</script>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if urllib.parse.urlsplit(self.path).path == '/collect':
            report = json.loads(data)
            report['headers'] = {key: self.headers.get(key) for key in
                ('User-Agent', 'Sec-CH-UA', 'Sec-CH-UA-Mobile', 'Sec-CH-UA-Platform')}
            self.server.reports.put(report)
        self.send_response(204)
        self.end_headers()

    def do_CONNECT(self):
        self.send_error(502)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME'),
                     'Set LEGION_TEST_CHROME for the ordinary Chrome comparison')
class NativeChromeIdentityTests(unittest.TestCase):
    def test_native_app_creates_only_destination_window_on_cold_and_reused_profile(self):
        with tempfile.TemporaryDirectory(prefix='legion-single-window-') as name:
            for opening in ('cold', 'reused'):
                with self.subTest(opening=opening):
                    browser = MobileBrowser({}, emulate_mobile=False)
                    try:
                        browser.start([os.environ['LEGION_TEST_CHROME'], f'--user-data-dir={name}',
                            '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                            '--window-size=390,844', '--app=about:blank'])
                        pages = browser.connection.call('Target.getTargets')['targetInfos']
                        self.assertFalse(any(page['type'] == 'page' for page in pages),
                                         'Chrome opened a window before launch preparation finished')

                        target = browser.navigate('about:blank')
                        self.assertEqual(browser.app_target, target)
                        for navigation in range(2):
                            self.assertEqual(browser.navigate('about:blank', target_id=target), target)
                            pages = browser.connection.call('Target.getTargets')['targetInfos']
                            self.assertEqual([page['targetId'] for page in pages if page['type'] == 'page'],
                                             [target], 'Repeated navigation opened an extra window')
                        bounds = browser.connection.call('Browser.getWindowForTarget', dict(targetId=target))['bounds']
                        self.assertGreater(bounds['width'], 0)
                        self.assertGreater(bounds['height'], 0)
                    finally:
                        browser.close()

    def test_default_launcher_matches_ordinary_chrome_without_mobile_emulation(self):
        with tempfile.TemporaryDirectory(prefix='legion-native-identity-') as name:
            root = Path(name)
            server = ProbeServer(('127.0.0.1', 0), ProbeHandler)
            server.reports = queue.Queue()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            chrome = os.environ['LEGION_TEST_CHROME']
            proxy = f'http://127.0.0.1:{server.server_port}'
            url = 'http://launcher-audit.localhost/probe'
            arguments = ['--start-minimized', '--window-size=390,844', '--no-first-run', '--no-default-browser-check']
            job, process = BrowserJob(), None
            atomic_json(str(root / 'empty.json'), {'version': 2, 'scripts': []})
            profiles = BrowserProfiles(root=root / 'profiles', userscripts=UserscriptManager(root / 'empty.json'))
            try:
                # No debugger, automation flag, headless mode, extension or script
                # injection in the baseline. The page itself sends the observation.
                process = subprocess.Popen([chrome, f'--user-data-dir={root / "ordinary"}',
                    f'--proxy-server={proxy}', '--proxy-bypass-list=<-loopback>', *arguments, url],
                    creationflags=4, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                job.attach(process)
                resume_browser(process)
                baseline = server.reports.get(timeout=20)
                job.close()
                process.wait(timeout=5)
                info = profiles.launch(chrome, 'native', proxy, [*arguments, f'--app={url}'])
                actual = server.reports.get(timeout=20)
                self.assertIs(info['mobile'], False)
                self.assertFalse((root / 'profiles' / 'native' / 'mobile_device.json').exists())
                for key in ('webdriver', 'touch', 'screen', 'pointer', 'hints', 'plugins', 'browser',
                            'hiddenOwn', 'natives', 'consoleStackAccess', 'headers', 'scriptBeforePage', 'viewport'):
                    with self.subTest(signal=key):
                        self.assertEqual(actual[key], baseline[key])
                self.assertEqual(set(actual['globals']) - set(baseline['globals']), set())
                self.assertEqual(set(baseline['globals']) - set(actual['globals']), set())
                self.assertGreater(actual['viewport'][2], 0)
                self.assertGreater(actual['viewport'][3], 0)
                # Fitting a grid slot must not reactivate emulated screen/touch.
                profiles.fit('native', 180, 320, zoom=.6)
                browser = profiles._controllers['native']
                browser.navigate(url, target_id=browser.app_target)
                resized = server.reports.get(timeout=20)
                for key in ('screen', 'touch', 'pointer', 'globals'):
                    self.assertEqual(resized[key], baseline[key], key)
                profiles.close()
                profiles.launch(chrome, 'native', proxy, [*arguments, f'--app={url}'])
                reopened = server.reports.get(timeout=20)
                for key in ('screen', 'touch', 'pointer', 'globals', 'webdriver', 'viewport'):
                    self.assertEqual(reopened[key], baseline[key], key)
            finally:
                profiles.close()
                job.close()
                if process is not None:
                    process.wait(timeout=5)
                server.shutdown()
                server.server_close()

    def test_native_destination_window_gets_document_start_script_before_page_code(self):
        with tempfile.TemporaryDirectory(prefix='legion-native-script-') as name:
            root = Path(name)
            server = ProbeServer(('127.0.0.1', 0), ProbeHandler)
            server.reports = queue.Queue()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            atomic_json(str(root / 'scripts.json'), {'version': 2, 'scripts': []})
            manager = UserscriptManager(root / 'scripts.json')
            source = ('// ==UserScript==\n// @match http://launcher-audit.localhost/*\n'
                      '// @run-at document-start\n// ==/UserScript==\nwindow.fixtureBeforePage = true;')
            with patch('userscripts.download_script', return_value=source):
                manager.install('https://raw.githubusercontent.com/example/bot/main/test.js')
            profiles = BrowserProfiles(root=root / 'profiles', userscripts=manager)
            try:
                with patch('userscripts.download_script', return_value=source):
                    profiles.launch(os.environ['LEGION_TEST_CHROME'], 'scripted',
                        f'http://127.0.0.1:{server.server_port}',
                        ['--start-minimized', '--window-size=390,844', '--app=http://launcher-audit.localhost/probe'])
                report = server.reports.get(timeout=20)
                self.assertIs(report['scriptBeforePage'], True)
                self.assertIs(report['webdriver'], False)
                self.assertGreater(report['viewport'][2], 0)
                browser = profiles._controllers['scripted']
                pages = [target for target in browser.connection.call('Target.getTargets')['targetInfos']
                         if target['type'] == 'page']
                self.assertEqual(len(pages), 1)
                target, pid = browser.app_target, browser.process.pid
                activation = browser.connection.call('Runtime.evaluate', dict(
                    expression="window.onbeforeunload = e => { e.preventDefault(); e.returnValue = 'leave'; }; navigator.userActivation.hasBeenActive",
                    userGesture=True, returnByValue=True), browser.wait_target(target))
                self.assertIs(activation['result']['value'], True)
                profiles.navigate_app('scripted', 'http://ip-audit.localhost/probe')
                ip_report = server.reports.get(timeout=20)
                self.assertIs(ip_report['scriptBeforePage'], False)
                profiles.navigate_app('scripted', 'http://launcher-audit.localhost/probe')
                game_report = server.reports.get(timeout=20)
                self.assertIs(game_report['scriptBeforePage'], True)
                self.assertEqual((browser.app_target, browser.process.pid), (target, pid))
                pages = [page for page in browser.connection.call('Target.getTargets')['targetInfos']
                         if page['type'] == 'page']
                self.assertEqual(len(pages), 1)
            finally:
                profiles.close()
                server.shutdown()
                server.server_close()
