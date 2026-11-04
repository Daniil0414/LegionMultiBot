"""Native browser tests use an HTTP proxy fixture; they never contact the game."""
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
from pathlib import Path
import queue
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import browser_profiles as bp
from chrome_mobile import MobileBrowser, PipeConnection, ProtocolError, device_for_profile
from vpn_runtime import atomic_json
from userscripts import UserscriptManager
from chrome_transport import listener_owner


class MobileUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legion-mobile-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_cancelled_navigation_retries_once_in_the_same_target(self):
        for errors, expected in ((['net::ERR_ABORTED', None], None),
                                 (['net::ERR_ABORTED', 'net::ERR_ABORTED'], 'net::ERR_ABORTED'),
                                 (['net::ERR_PROXY_CONNECTION_FAILED'], 'net::ERR_PROXY_CONNECTION_FAILED')):
            with self.subTest(errors=errors):
                browser = MobileBrowser({})
                browser.connection = Mock()
                browser.wait_target = Mock(return_value='session')
                responses = iter(errors)

                def call(method, params=None, session=None):
                    if method == 'Target.getTargets':
                        return {'targetInfos': []}
                    if method == 'Page.navigate':
                        error = next(responses)
                        return {'errorText': error} if error else {}
                    return {}

                browser.connection.call.side_effect = call
                if expected:
                    with self.assertRaisesRegex(ProtocolError, 'Page.navigate: ' + expected):
                        browser.navigate('https://ip-fixture.invalid', target_id='target')
                else:
                    self.assertEqual(browser.navigate('https://ip-fixture.invalid', target_id='target'), 'target')
                navigations = [entry for entry in browser.connection.call.call_args_list if entry.args[0] == 'Page.navigate']
                self.assertEqual(len(navigations), len(errors))
                self.assertTrue(all(entry.args[2] == 'session' for entry in navigations))
                self.assertEqual(browser._navigation_sessions, set())

    def test_only_beforeunload_for_an_explicit_navigation_is_accepted(self):
        browser = MobileBrowser({})
        browser.connection = Mock(events=queue.Queue())
        browser._navigation_sessions.add('navigating')
        for session, kind in (('navigating', 'beforeunload'), ('other', 'beforeunload'), ('navigating', 'confirm')):
            browser.connection.events.put(dict(method='Page.javascriptDialogOpening',
                                               sessionId=session, params=dict(type=kind)))
        browser.connection.events.put(None)
        browser._events()
        browser.connection.send.assert_called_once_with('Page.handleJavaScriptDialog', {'accept': True}, 'navigating')

    def test_existing_app_navigation_preserves_controller_and_route(self):
        profiles = bp.BrowserProfiles(root=self.root)
        controller = Mock(app_target='target', has_app_window=True)
        controller.connection.closed.is_set.return_value = False
        profiles._controllers['account'] = controller
        try:
            with patch.object(profiles, 'launch') as launch:
                profiles.navigate_app('account', 'https://ip-fixture.invalid')
            controller.navigate.assert_called_once_with('https://ip-fixture.invalid', target_id='target')
            launch.assert_not_called()
            controller.set_userscript.assert_not_called()
        finally:
            profiles.shutdown()

    def test_profile_device_survives_restart_and_reordering(self):
        directory = self.root / 'account-a'
        first = device_for_profile(directory)
        device_for_profile(self.root / 'account-b')
        self.assertEqual(device_for_profile(directory), first)
        self.assertEqual(device_for_profile(directory), device_for_profile(directory))

    def test_corrupt_device_settings_are_not_silently_replaced(self):
        path = self.root / 'mobile_device.json'
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            device_for_profile(self.root)
        self.assertEqual(path.read_text(), '{broken')

    def test_mobile_configuration_keeps_browser_identity_native(self):
        browser = MobileBrowser(device_for_profile(self.root))
        browser.connection = Mock()
        browser._configure('page-session', 'page')
        browser._configure('worker-session', 'shared_worker')
        methods = [call.args[0] for call in browser.connection.call.call_args_list]
        self.assertNotIn('Emulation.setUserAgentOverride', methods)
        self.assertNotIn('Network.setUserAgentOverride', methods)
        self.assertIn('Emulation.setDeviceMetricsOverride', methods)

    def test_mobile_view_fills_slot_in_both_dimensions_at_each_zoom(self):
        browser = MobileBrowser(device_for_profile(self.root))
        browser.connection = Mock()
        for zoom in (.6, .8, 1.0):
            browser.fit(320, 465, zoom=zoom)
            metrics = browser._metrics()
            self.assertAlmostEqual(metrics['width'] * metrics['scale'], 320)
            self.assertAlmostEqual(metrics['height'] * metrics['scale'], 465, delta=1)
            self.assertEqual(metrics['screenWidth'], browser.device['width'])
            self.assertEqual(metrics['screenHeight'], browser.device['height'])
            self.assertEqual(metrics['deviceScaleFactor'], 1)

    def test_overrides_and_multiple_initial_urls_are_rejected(self):
        profiles = bp.BrowserProfiles(root=self.root)
        for arguments in (['--load-extension=anything'], ['--remote-debugging-port=9222'],
                          ['--user-agent=fake'], ['--touch-events=enabled'],
                          ['https://one.invalid', 'https://two.invalid'], ['javascript:alert(1)']):
            with self.subTest(arguments=arguments), patch.object(bp, 'MobileBrowser') as browser:
                with self.assertRaises(ValueError):
                    profiles.launch('chrome.exe', 'account', 'http://127.0.0.1:1234', arguments)
                browser.assert_not_called()

    def test_proxy_slot_does_not_bind_servers_when_accounts_reorder(self):
        profiles = bp.BrowserProfiles(root=self.root)
        self.assertEqual(profiles.proxy_slot('account-a', 0), 0)
        self.assertEqual(profiles.proxy_slot('account-b', 0), 0)
        (self.root / '.network-slots.json').write_text('broken legacy data')
        restarted = bp.BrowserProfiles(root=self.root)
        self.assertEqual(restarted.proxy_slot('account-a', 9), 9)
        self.assertEqual(restarted.proxy_slot('account-b', 0), 0)

    def test_native_privacy_preference_preserves_other_settings_and_backup(self):
        directory = self.root / 'account'
        prefs = directory / 'Default' / 'Preferences'
        prefs.parent.mkdir(parents=True)
        original = b'{"fixture_setting":"keep","webrtc":{"fixture":true}}'
        prefs.write_bytes(original)
        bp.BrowserProfiles._privacy_preferences(directory)
        result = json.loads(prefs.read_text())
        self.assertEqual(result['fixture_setting'], 'keep')
        self.assertTrue(result['webrtc']['fixture'])
        self.assertEqual(result['webrtc']['ip_handling_policy'], 'disable_non_proxied_udp')
        self.assertEqual(result['net']['network_prediction_options'], 2)
        self.assertEqual((directory / 'preferences-before-network-privacy.json').read_bytes(), original)
        prefs.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'original file was preserved'):
            bp.BrowserProfiles._privacy_preferences(directory)
        self.assertEqual(prefs.read_text(), '{broken')

    def test_configuration_failure_closes_only_new_browser(self):
        profiles = bp.BrowserProfiles(root=self.root)
        with patch.object(profiles, '_has_unmanaged_browser', return_value=False), patch.object(bp, 'MobileBrowser') as factory, patch.object(bp, 'process_identity', return_value={'pid': 1}):
            factory.return_value.navigate.side_effect = ProtocolError('configuration failed')
            with self.assertRaisesRegex(ProtocolError, 'configuration failed'):
                profiles.launch('chrome.exe', 'account', 'http://127.0.0.1:1234', ['https://fixture.invalid'])
            factory.return_value.close.assert_called_once()
        self.assertFalse((self.root / 'account' / '.legion-session.json').exists())

    def test_other_launcher_cannot_adopt_live_pipe_profile(self):
        profiles = bp.BrowserProfiles(root=self.root)
        directory = profiles.prepare('account')
        identity = {'pid': 42, 'created': 'a'}
        atomic_json(str(directory / '.legion-session.json'), dict(identity=identity, proxy='http://127.0.0.1:1234'))
        with patch.object(bp, 'process_identity', return_value=identity), patch.object(bp, 'MobileBrowser') as browser:
            with self.assertRaisesRegex(RuntimeError, 'before starting native mobile'):
                profiles.launch('chrome.exe', 'account', 'http://127.0.0.1:1234', ['https://fixture.invalid'])
            browser.assert_not_called()


    def test_pipe_handles_fragmented_responses_and_events(self):
        to_browser_read, to_browser_write = os.pipe()
        from_browser_read, from_browser_write = os.pipe()
        reader = os.fdopen(from_browser_read, 'rb', buffering=0)
        writer = os.fdopen(to_browser_write, 'wb', buffering=0)
        cdp = PipeConnection(reader, writer)

        def child():
            with os.fdopen(to_browser_read, 'rb', buffering=0) as incoming, os.fdopen(from_browser_write, 'wb', buffering=0) as outgoing:
                message = bytearray()
                while not message.endswith(b'\0'):
                    message.extend(incoming.read(1))
                request = json.loads(message[:-1])
                response = json.dumps(dict(id=request['id'], result={'ok': True})).encode() + b'\0'
                outgoing.write(b'{"method":"fixture.event"}\0' + response[:7])
                outgoing.write(response[7:])
        thread = threading.Thread(target=child)
        thread.start()
        self.assertEqual(cdp.call('fixture.command'), {'ok': True})
        self.assertEqual(cdp.events.get(timeout=2)['method'], 'fixture.event')
        thread.join(timeout=2)
        cdp.close()
        reader.close()


PROBE = r'''({
 ua:navigator.userAgent, platform:navigator.platform,
 webdriver:navigator.webdriver,ownWebdriver:Object.hasOwn(navigator,'webdriver'),
 webdriverGetter:Function.prototype.toString.call(Object.getOwnPropertyDescriptor(Navigator.prototype,'webdriver').get),
 uaData:navigator.userAgentData?.toJSON(), touch:navigator.maxTouchPoints,
 width:screen.width,height:screen.height,innerWidth:innerWidth,
 dpr:devicePixelRatio,legacy:localStorage.getItem('__lg_mobile_shield_device'),
 cookie:document.cookie, ownScreenGetter:!!Object.getOwnPropertyDescriptor(screen,'width'),
 shieldCss:!!document.getElementById('__lg_phone_shield_css'),
 gm:typeof window.GM_info,unsafe:typeof window.unsafeWindow
})'''


class FixtureServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return  # Chrome may close a denied background CONNECT immediately.
        super().handle_error(request, client_address)


class FixtureProxy(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        if not self.path.split(':')[0].endswith('.localhost'):
            self.send_error(502)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.flush()
        self.close_connection = False
        self.handle_one_request()

    def do_GET(self):
        self.server.requests.append(dict(path=self.path, host=self.headers.get('Host'),
                                         ua=self.headers.get('User-Agent'),
                                         platform=self.headers.get('Sec-CH-UA-Platform'),
                                         mobile=self.headers.get('Sec-CH-UA-Mobile')))
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            header = self.rfile.read(2)
            if len(header) != 2:
                return
            length = header[1] & 127
            mask = self.rfile.read(4)
            data = self.rfile.read(length)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            self.wfile.write(bytes([0x81, len(payload)]) + payload)
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path.endswith('/shared.js') or self.path.endswith('/sw.js'):
            shared = self.path.endswith('/shared.js')
            source = "({ua:navigator.userAgent,platform:navigator.platform,data:navigator.userAgentData?.toJSON()})"
            body = (('onconnect=e=>e.ports[0].postMessage(' + source + ');') if shared else
                    ('onmessage=e=>e.source.postMessage(' + source + ');')).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript')
            self.send_header('Service-Worker-Allowed', '/')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Local mobile fixture</title><script>window.firstProbe='+PROBE+';</script>'
                '<body><button style="width:100%;height:60px">Mobile fixture</button>'
                '<script>if(top!==window)top.postMessage({fixtureProbe:window.firstProbe},"*");</script>' + ('<script>localStorage.setItem("legacy-fixture","keep");</script>' if self.path.endswith('/legacy-seed') else '') + '</body>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SocksFixture(socketserver.ThreadingTCPServer):
    daemon_threads = True


class SocksHandler(socketserver.BaseRequestHandler):
    def read(self, count):
        data = b''
        while len(data) < count:
            chunk = self.request.recv(count - len(data))
            if not chunk:
                raise ConnectionError('SOCKS fixture peer disconnected')
            data += chunk
        return data

    def handle(self):
        try:
            self.request.settimeout(5)
            version, count = self.read(2)
            if version != 5 or 0 not in self.read(count):
                return
            self.request.sendall(b'\x05\x00')
            version, command, _, kind = self.read(4)
            if version != 5 or command != 1 or kind != 3:
                self.request.sendall(b'\x05\x08\x00\x01' + b'\0' * 6)
                return
            host = self.read(self.read(1)[0]).decode('ascii')
            port = int.from_bytes(self.read(2), 'big')
            self.server.destinations.append((host, port, kind))
            allowed = host.endswith('.invalid')
            self.request.sendall(bytes([5, 0 if allowed else 4, 0, 1]) + b'\0' * 6)
            if allowed:
                FixtureProxy(self.request, self.client_address, self.server)
        except OSError:
            return


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME'),
                     'Set LEGION_TEST_CHROME to run the isolated local Chrome tests')
class MobileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legion-mobile-integration-')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.proxy = FixtureServer(('127.0.0.1', 0), FixtureProxy)
        self.proxy.daemon_threads = True
        self.proxy.requests = []
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.addCleanup(self.proxy.server_close)
        self.addCleanup(self.proxy.shutdown)
        atomic_json(str(self.root / 'userscript_config.json'), {'version': 2, 'scripts': []})
        self.profiles = bp.BrowserProfiles(root=self.root, userscripts=UserscriptManager(self.root / 'userscript_config.json'),
                                          emulate_mobile=True)
        self.addCleanup(self.profiles.close)

    def launch(self, key='account-a'):
        return self.profiles.launch(os.environ['LEGION_TEST_CHROME'], key,
                                   f'http://127.0.0.1:{self.proxy.server_port}',
                                   ['--headless=new', '--window-size=390,844', 'http://device-a.localhost/start'])

    def test_explicit_direct_mode_loads_local_page_without_proxy(self):
        self.profiles.allow_direct = True
        target = f'http://127.0.0.1:{self.proxy.server_port}/direct-mode'
        self.profiles.launch(os.environ['LEGION_TEST_CHROME'], 'account-a', 'DIRECT',
                             ['--headless=new', target])
        self.assertEqual(self.evaluate('document.title'), 'Local mobile fixture')
        self.assertTrue(any(request['path'] == '/direct-mode' for request in self.proxy.requests))
        self.assertFalse(any(request['path'].startswith('http://') for request in self.proxy.requests))

    def test_private_bot_state_settings_migration_reload_and_profile_isolation(self):
        self.launch()
        self.evaluate("localStorage.setItem('lg_bot_min','7'); localStorage.setItem('session_fixture','preserved'); document.cookie='auth_fixture=preserved'")
        source = '''// ==UserScript==
// @namespace https://github.com/Daniil0414/LegionAutoMiner
// @match http://device-a.localhost/*
// @run-at document-idle
// ==/UserScript==
const G = window;
G.__mineBotRunning = true;
G.fixtureModule = {};
G.fixtureModule.__botHooked = true;
const previous = localStorage.getItem('lg_bot_min') || '0';
document.body.dataset.privateBefore = previous;
localStorage.setItem('lg_bot_min', String(Number(previous) + 1));
const panel = document.createElement('div'); panel.id = 'mine-bot-v3';
panel.innerHTML = '<button id="b-private-button">Panel</button>';
document.body.appendChild(panel);
document.getElementById('b-private-button').onclick = () => document.body.dataset.privateClick = 'yes';
document.getElementById('b-private-button').click();
document.title = 'private bot title';
document.body.dataset.privateDone = '1';
'''
        with patch('userscripts.download_script', return_value=source):
            self.profiles.install_userscript('https://raw.githubusercontent.com/example/bot/main/test.js')
        def ready(key='account-a', path='/start'):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                value = self.evaluate("({path:location.pathname,done:document.body?.dataset.privateDone,value:document.body?.dataset.privateBefore})", key=key)
                if value.get('path') == path and value.get('done'):
                    return value['value']
                time.sleep(.025)
            self.fail('Private bot did not finish initialization')
        self.assertEqual(ready(), '7')
        result = self.evaluate("({global:typeof window.__mineBotRunning,flags:Object.keys(window.fixtureModule),stored:localStorage.getItem('lg_bot_min'),session:localStorage.getItem('session_fixture'),cookie:document.cookie,fetch:Function.prototype.toString.call(fetch),raf:Function.prototype.toString.call(requestAnimationFrame)})")
        self.assertEqual(result['global'], 'undefined')
        self.assertEqual(result['flags'], [])
        self.assertIsNone(result['stored'])
        self.assertEqual(result['session'], 'preserved')
        self.assertIn('auth_fixture=preserved', result['cookie'])
        self.assertIn('[native code]', result['fetch'])
        self.assertIn('[native code]', result['raf'])
        self.assertIsNone(self.evaluate("document.getElementById('mine-bot-v3')"))
        self.assertIsNone(self.evaluate("document.querySelector('#b-private-button')"))
        self.assertEqual(self.evaluate('document.body.dataset.privateClick'), 'yes')
        self.assertNotEqual(self.evaluate('document.title'), 'private bot title')
        self.assertEqual(self.evaluate("Array.from(document.body.children).filter(n=>n.shadowRoot).length"), 0)
        browser = self.profiles._controllers['account-a']
        token = browser._private_config['binding'].split('-')[0]
        self.assertEqual(self.evaluate('Object.getOwnPropertyNames(window).filter(k=>k.startsWith(' + json.dumps(token) + '))'), [])
        # Reload waits for a saved write acknowledged by the same event queue.
        path = self.root / 'account-a' / 'userscript-private.json'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if path.exists() and json.loads(path.read_text())['origins']['http://device-a.localhost'].get('lg_bot_min') == '8':
                break
            time.sleep(.025)
        else:
            self.fail('Private setting was not persisted')
        browser.navigate('http://device-a.localhost/reloaded', target_id=next(iter(browser._targets)))
        self.assertEqual(ready(path='/reloaded'), '8')
        self.launch('account-b')
        self.assertEqual(ready('account-b'), '0')

    def test_corrupt_private_settings_block_bot_without_deleting_legacy_settings(self):
        self.launch()
        self.evaluate("localStorage.setItem('lg_bot_min','7')")
        path = self.root / 'account-a' / 'userscript-private.json'
        path.write_text('{broken', encoding='utf-8')
        source = '''// ==UserScript==
// @namespace https://github.com/Daniil0414/LegionAutoMiner
// @match http://device-a.localhost/*
// ==/UserScript==
window.__mineBotRunning = true;
document.body.dataset.shouldNotRun = '1';
'''
        with patch('userscripts.download_script', return_value=source):
            self.profiles.install_userscript('https://raw.githubusercontent.com/example/bot/main/test.js')
        self.evaluate('new Promise(resolve => setTimeout(resolve, 500))', await_promise=True)
        self.assertEqual(self.evaluate("localStorage.getItem('lg_bot_min')"), '7')
        self.assertIsNone(self.evaluate('document.body.dataset.shouldNotRun'))
        self.assertEqual(path.read_text(), '{broken')

    def test_visible_mobile_browser_has_native_webdriver_false_and_rejects_web_origins(self):
        info = self.profiles.launch(os.environ['LEGION_TEST_CHROME'], 'account-a',
                                   f'http://127.0.0.1:{self.proxy.server_port}',
                                   ['--start-minimized', '--app=http://device-a.localhost/start', '--window-size=390,844'])
        first = self.evaluate('window.firstProbe')
        self.assertIs(first['webdriver'], False)
        self.assertFalse(first['ownWebdriver'])
        self.assertEqual(first['webdriverGetter'], 'function get webdriver() { [native code] }')
        self.assertEqual(first['touch'], 5)
        self.assertEqual(first['innerWidth'], info['device']['width'])
        iframe = self.evaluate("new Promise((resolve,reject)=>{addEventListener('message',e=>{if(e.data.fixtureProbe)resolve(e.data.fixtureProbe)},{once:true});const f=document.createElement('iframe');f.src='http://frame-b.localhost/frame';document.body.appendChild(f);setTimeout(()=>reject(Error('iframe timeout')),5000)})", await_promise=True)
        self.assertIs(iframe['webdriver'], False)
        browser = self.profiles._controllers['account-a']
        popup = browser.navigate('http://device-a.localhost/popup', new_window=True)
        self.assertIs(self.evaluate('window.firstProbe.webdriver', target=popup), False)
        port = browser.connection.writer.socket.getpeername()[1]
        self.assertEqual(listener_owner(port), (info['pid'], '127.0.0.1'))
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('GET', '/json/version')
            response = connection.getresponse()
            self.assertNotEqual(response.getheader('Access-Control-Allow-Origin'), '*')
            endpoint = json.loads(response.read())['webSocketDebuggerUrl']
            connection.request('GET', endpoint.split(str(port), 1)[1], headers={
                'Upgrade': 'websocket', 'Connection': 'Upgrade', 'Sec-WebSocket-Version': '13',
                'Sec-WebSocket-Key': base64.b64encode(os.urandom(16)).decode(),
                'Origin': 'https://game.example'})
            response = connection.getresponse()
            self.assertIn(response.status, (400, 403))
            response.read()
        finally:
            connection.close()

    def test_two_github_scripts_run_in_order_in_each_window_and_disable_separately(self):
        header = '''// ==UserScript==
// @name Local script fixture
// @match http://*.localhost/*
// @grant none
// @run-at document-start
// ==/UserScript==
'''
        first_source = header + "window.fixtureOrder = ['first'];window.fixtureFirstCount = (window.fixtureFirstCount || 0) + 1;"
        second_source = header + "window.fixtureOrder = (window.fixtureOrder || []).concat('second');window.fixtureSecond = true;"
        first_url = 'https://github.com/fixture/first/blob/main/bot.user.js'
        second_url = 'https://github.com/fixture/second/blob/main/bot.user.js'

        def download(url):
            return second_source if '/second/' in url else first_source

        def wait_order(expected, key='account-a'):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.evaluate('window.fixtureOrder', key=key) == expected:
                    return
                time.sleep(.05)
            self.assertEqual(self.evaluate('window.fixtureOrder', key=key), expected)

        with patch('userscripts.download_script', side_effect=download):
            self.profiles.install_userscript(first_url)
            result = self.profiles.install_userscript(second_url)
            second_id = result['selected_id']
            self.launch()
            self.launch('account-b')
            self.assertEqual(self.evaluate('window.fixtureOrder'), ['first', 'second'])
            self.assertEqual(self.evaluate('window.fixtureOrder', key='account-b'), ['first', 'second'])
            browser = self.profiles._controllers['account-a']
            popup = browser.navigate('http://device-a.localhost/popup', new_window=True)
            self.assertEqual(self.evaluate('window.fixtureOrder', target=popup), ['first', 'second'])
            self.assertEqual(self.evaluate('window.fixtureFirstCount'), 1)
            self.profiles.disable_userscript(second_id)
            wait_order(['first'])
            wait_order(['first'], key='account-b')
            self.assertEqual(self.evaluate('typeof window.fixtureSecond'), 'undefined')
            self.profiles.install_userscript(second_url, second_id)
            wait_order(['first', 'second'])
            self.assertEqual(self.evaluate('window.fixtureFirstCount'), 1)

    def test_github_script_first_navigation_all_windows_update_cache_and_disable(self):
        source = '''// ==UserScript==
// @name Local script fixture
// @version 1
// @match http://*.localhost/*
// @grant none
// @run-at document-start
// ==/UserScript==
window.fixtureScriptVersion = 1;
window.fixtureScriptWasFirst = typeof window.firstProbe === 'undefined';
'''
        url = 'https://github.com/fixture/bot/blob/main/bot.user.js'

        def wait_value(expression, expected, **kwargs):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    if self.evaluate(expression, **kwargs) == expected:
                        return
                except AssertionError:
                    pass  # A reload can replace the execution context between calls.
                time.sleep(.05)
            self.assertEqual(self.evaluate(expression, **kwargs), expected)

        with patch('userscripts.download_script', return_value=source) as fetch:
            self.profiles.install_userscript(url)
            self.launch()
            self.assertEqual(self.evaluate('window.fixtureScriptVersion'), 1)
            self.assertTrue(self.evaluate('window.fixtureScriptWasFirst'))
            self.assertFalse(self.evaluate("Object.getOwnPropertySymbols(window).some(s=>Symbol.keyFor(s)==='legion.userscript')"))
            self.launch('account-b')
            self.assertEqual(self.evaluate('window.fixtureScriptVersion', key='account-b'), 1)
            browser = self.profiles._controllers['account-a']
            popup = browser.navigate('http://device-a.localhost/popup', new_window=True)
            self.assertEqual(self.evaluate('window.fixtureScriptVersion', target=popup), 1)
            self.assertEqual(fetch.call_count, 3)

        updated = source.replace('@version 1', '@version 2').replace('fixtureScriptVersion = 1', 'fixtureScriptVersion = 2')
        with patch('userscripts.download_script', return_value=updated):
            self.launch()
        wait_value('window.fixtureScriptVersion', 2)
        wait_value('window.fixtureScriptVersion', 2, key='account-b')
        wait_value('window.fixtureScriptVersion', 2, target=popup)
        with patch('userscripts.download_script', side_effect=OSError('GitHub fixture offline')):
            result = self.launch('account-b')
        self.assertTrue(result['userscript']['using_cached'])
        self.assertEqual(result['userscript']['last_error'], 'GitHub fixture offline')
        self.assertEqual(self.evaluate('window.fixtureScriptVersion', key='account-b'), 2)
        self.profiles.disable_userscript()
        wait_value('typeof window.fixtureScriptVersion', 'undefined')
        wait_value('typeof window.fixtureScriptVersion', 'undefined', key='account-b')

    def test_legacy_orphan_recovers_and_keeps_data_and_other_profile(self):
        self.launch()
        self.evaluate('localStorage.setItem("recovery-fixture","keep");document.cookie="recoveryCookie=keep;path=/;max-age=3600"')
        self.profiles.close()
        other = self.launch('account-b')
        executable = os.environ['LEGION_TEST_CHROME']
        directory = self.profiles.directory('account-a').resolve()
        args = [executable, '--user-data-dir=' + str(directory), '--headless=new',
                *bp.security_args(f'http://127.0.0.1:{self.proxy.server_port}'), 'about:blank']
        script = 'import json,subprocess,sys; p=subprocess.Popen(json.loads(sys.argv[1]),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)'
        legacy_pid = int(subprocess.check_output([sys.executable, '-B', '-c', script, json.dumps(args)], text=True))
        identity = bp.process_identity(legacy_pid)
        self.assertIsNotNone(identity)
        try:
            deadline = time.monotonic() + 10
            while not self.profiles._profile_browser(directory) and time.monotonic() < deadline:
                time.sleep(.1)
            recovered = self.launch()
            self.assertNotEqual(recovered['pid'], legacy_pid)
            self.assertNotEqual(bp.process_identity(legacy_pid), identity)
            self.assertEqual(bp.process_identity(other['pid']), other['identity'])
            self.assertEqual(self.evaluate('localStorage.getItem("recovery-fixture")'), 'keep')
            self.assertIn('recoveryCookie=keep', self.evaluate('document.cookie'))
        finally:
            if bp.process_identity(legacy_pid) == identity:
                bp.close_abandoned_browser(identity, timeout=.2)

    def test_closed_control_pipe_is_recovered_without_manual_close(self):
        first = self.launch()
        self.profiles._controllers['account-a'].connection.close()
        second = self.launch()
        self.assertNotEqual(first['pid'], second['pid'])
        self.assertNotEqual(bp.process_identity(first['pid']), first['identity'])

    def test_socks_dns_name_reaches_proxy_without_host_resolver_override(self):
        server = SocksFixture(('127.0.0.1', 0), SocksHandler)
        server.requests, server.destinations = [], []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.profiles.launch(os.environ['LEGION_TEST_CHROME'], 'account-a',
                             f'socks5://127.0.0.1:{server.server_address[1]}',
                             ['--headless=new', 'http://remote-dns-fixture.invalid:8097/dns'])
        self.assertIn(('remote-dns-fixture.invalid', 8097, 3), server.destinations)
        self.assertEqual(self.evaluate('document.title'), 'Local mobile fixture')

    def evaluate(self, expression, key='account-a', target=None, await_promise=False):
        browser = self.profiles._controllers[key]
        if target is None:
            target = browser.app_target or next(k for k, v in browser._targets.items() if v['type'] == 'page')
        session = browser.wait_target(target)
        result = browser.connection.call('Runtime.evaluate', dict(expression=expression,
                                          returnByValue=True, awaitPromise=await_promise), session)
        if 'exceptionDetails' in result:
            self.fail(str(result['exceptionDetails']))
        return result['result'].get('value')

    def test_first_request_mobile_view_workers_popups_and_websocket(self):
        info = self.launch()
        first = self.evaluate('window.firstProbe')
        device = info['device']
        self.assertEqual(first['width'], device['width'])
        self.assertEqual(first['innerWidth'], device['width'])
        self.assertEqual(first['uaData']['platform'], 'Windows')
        self.assertFalse(first['uaData']['mobile'])
        self.assertEqual(first['touch'], 5)
        self.assertFalse(first['ownWebdriver'])
        self.assertEqual(first['webdriverGetter'], 'function get webdriver() { [native code] }')
        self.assertFalse(first['shieldCss'])
        self.assertFalse(first['ownScreenGetter'])
        self.assertEqual(first['gm'], 'undefined')
        self.assertEqual(first['unsafe'], 'undefined')
        self.evaluate("window.fixtureClicks=0;document.querySelector('button').onclick=()=>window.fixtureClicks++")
        browser = self.profiles._controllers['account-a']
        page_session = browser.wait_target(next(k for k, v in browser._targets.items() if v['type'] == 'page'))
        # Touch emulation may defer the press acknowledgement until release.
        browser.connection.send('Input.dispatchMouseEvent',
                                dict(type='mousePressed', x=30, y=30, button='left', clickCount=1), page_session)
        browser.connection.call('Input.dispatchMouseEvent',
                                dict(type='mouseReleased', x=30, y=30, button='left', clickCount=1), page_session)
        self.assertEqual(self.evaluate('window.fixtureClicks'), 1)
        request = next(r for r in self.proxy.requests if r['path'].endswith('/start'))
        self.assertEqual(request['ua'], first['ua'])
        self.assertEqual(request['mobile'], '?0')
        self.assertEqual(request['platform'], '"Windows"')
        worker = self.evaluate("new Promise((resolve,reject)=>{const w=new Worker(URL.createObjectURL(new Blob([`postMessage({ua:navigator.userAgent,platform:navigator.platform,data:navigator.userAgentData?.toJSON()})`],{type:'text/javascript'})));w.onmessage=e=>{w.terminate();resolve(e.data)};w.onerror=reject;setTimeout(()=>reject(Error('worker timeout')),5000)})", await_promise=True)
        self.assertEqual(worker['ua'], first['ua'])
        self.assertEqual(worker['data']['platform'], 'Windows')
        self.assertEqual(worker['platform'], first['platform'])
        iframe = self.evaluate("new Promise((resolve,reject)=>{addEventListener('message',e=>{if(e.data.fixtureProbe)resolve(e.data.fixtureProbe)},{once:true});const f=document.createElement('iframe');f.src='http://frame-b.localhost/frame';document.body.appendChild(f);setTimeout(()=>reject(Error('iframe timeout')),5000)})", await_promise=True)
        self.assertEqual(iframe['ua'], first['ua'])
        self.assertEqual(iframe['uaData']['platform'], 'Windows')
        self.assertEqual(iframe['webdriver'], first['webdriver'])
        popup = self.profiles._controllers['account-a'].navigate('http://device-a.localhost/popup', new_window=True)
        self.assertEqual(self.evaluate('window.firstProbe.ua', target=popup), first['ua'])
        self.assertEqual(self.evaluate('window.firstProbe.webdriver', target=popup), first['webdriver'])
        echo = self.evaluate("new Promise((resolve,reject)=>{const w=new WebSocket('ws://device-a.localhost/ws');w.onopen=()=>w.send('fixture');w.onmessage=e=>{resolve(e.data);w.close()};w.onerror=()=>reject(Error('websocket failed'));setTimeout(()=>reject(Error('websocket timeout')),5000)})", await_promise=True)
        self.assertEqual(echo, 'fixture')
        self.assertFalse((self.root/'account-a'/'DevToolsActivePort').exists())

    def test_shared_and_service_workers_use_the_same_mobile_metadata(self):
        self.launch()
        first = self.evaluate('window.firstProbe')
        shared = self.evaluate("new Promise((resolve,reject)=>{const w=new SharedWorker('/shared.js');w.port.onmessage=e=>{resolve(e.data);w.port.close()};w.onerror=()=>reject(Error('shared worker failed'));setTimeout(()=>reject(Error('shared worker timeout')),5000)})", await_promise=True)
        self.assertEqual(shared['ua'], first['ua'])
        self.assertEqual(shared['data']['platform'], 'Windows')
        self.assertEqual(shared['platform'], first['platform'])
        service = self.evaluate("new Promise(async(resolve,reject)=>{try{await navigator.serviceWorker.register('/sw.js');const r=await navigator.serviceWorker.ready;navigator.serviceWorker.addEventListener('message',e=>resolve(e.data),{once:true});r.active.postMessage('probe');setTimeout(()=>reject(Error('service worker timeout')),5000)}catch(e){reject(e)}})", await_promise=True)
        self.assertEqual(service['ua'], first['ua'])
        self.assertEqual(service['data']['platform'], 'Windows')
        self.assertEqual(service['platform'], first['platform'])

    def test_grid_scale_keeps_device_identity_and_store_can_use_desktop(self):
        info = self.launch()
        browser = self.profiles._controllers['account-a']
        browser.fit(210, 450)
        deadline = time.monotonic() + 3
        while browser._fit_pending and time.monotonic() < deadline:
            time.sleep(.01)
        # A barrier after the asynchronous fit, then verify the actual CSS viewport.
        session = browser.wait_target(next(k for k,v in browser._targets.items() if v['type'] == 'page'))
        deadline = time.monotonic() + 3
        expected = browser._metrics()
        while time.monotonic() < deadline:
            view = browser.connection.call('Page.getLayoutMetrics', session=session)['cssLayoutViewport']
            if view['clientHeight'] == expected['height']:
                break
            time.sleep(.02)
        self.assertEqual(view['clientWidth'], expected['width'])
        self.assertEqual(view['clientHeight'], expected['height'])
        self.assertEqual(self.evaluate('screen.width'), info['device']['width'])
        self.assertEqual(self.evaluate('devicePixelRatio'), info['device']['scale'])
        desktop = browser.navigate('http://device-a.localhost/store-fixture', new_window=True, desktop=True)
        probe = self.evaluate('window.firstProbe', target=desktop)
        self.assertEqual(probe['platform'], 'Win32')
        self.assertEqual(probe['uaData']['platform'], 'Windows')
        self.assertFalse(probe['uaData']['mobile'])
        self.assertNotIn('Android', probe['ua'])

    def test_app_window_reuses_the_same_target_and_setup_transition_preserves_data(self):
        setup = self.launch()
        self.evaluate("localStorage.setItem('fixture-setup','keep')")
        arguments = ['--headless=new', '--app=http://device-a.localhost/start']
        first = self.profiles.launch(os.environ['LEGION_TEST_CHROME'], 'account-a',
                                    f'http://127.0.0.1:{self.proxy.server_port}', arguments)
        self.assertNotEqual(first['pid'], setup['pid'])
        self.assertEqual(self.evaluate("localStorage.getItem('fixture-setup')"), 'keep')
        browser = self.profiles._controllers['account-a']
        target = browser.app_target
        second = self.profiles.launch(os.environ['LEGION_TEST_CHROME'], 'account-a',
                                     f'http://127.0.0.1:{self.proxy.server_port}',
                                     ['--headless=new', '--app=http://device-a.localhost/second'])
        self.assertEqual(first['pid'], second['pid'])
        self.assertEqual(browser.app_target, target)
        self.assertTrue(second['reused_window'])
        self.assertEqual(self.evaluate('location.pathname'), '/second')
        self.assertEqual(sum(p['type'] == 'page' for p in browser.connection.call('Target.getTargets')['targetInfos']), 1)


    def test_restart_migrates_only_legacy_marker_and_preserves_account_data(self):
        first = self.launch()
        self.evaluate("localStorage.setItem('__lg_mobile_shield_device','old');localStorage.setItem('fixture-account','keep');document.cookie='fixture_auth=keep;path=/;max-age=86400'")
        self.profiles.close()
        second = self.launch()
        self.assertEqual(first['device'], second['device'])
        self.assertIsNone(self.evaluate('window.firstProbe.legacy'))
        self.assertEqual(self.evaluate("localStorage.getItem('fixture-account')"), 'keep')
        self.assertIn('fixture_auth=keep', self.evaluate('document.cookie'))
        self.launch('account-b')
        self.assertIsNone(self.evaluate("localStorage.getItem('fixture-account')", key='account-b'))
        self.assertNotIn('fixture_auth=keep', self.evaluate('document.cookie', key='account-b'))

    def test_proxy_resolves_target_names_and_local_webrtc_has_no_direct_udp_candidates(self):
        self.launch()
        candidates = self.evaluate("new Promise(async(resolve,reject)=>{const p=new RTCPeerConnection({iceServers:[]});const found=[];const timer=setTimeout(()=>{p.close();reject(Error('ICE gathering timeout'))},5000);p.onicecandidate=e=>{if(e.candidate)found.push(e.candidate.candidate);else{clearTimeout(timer);p.close();resolve(found)}};try{p.createDataChannel('fixture');await p.setLocalDescription(await p.createOffer())}catch(e){clearTimeout(timer);p.close();reject(e)}})", await_promise=True)
        self.assertFalse(any(' udp ' in candidate.lower() for candidate in candidates), candidates)
        browser = self.profiles._controllers['account-a']
        browser.navigate('http://unresolvable-fixture.invalid:8097/dns', new_window=True)
        self.assertTrue(any('unresolvable-fixture.invalid:8097/dns' in row['path'] for row in self.proxy.requests))

    def test_proxy_outage_does_not_fall_back_to_direct_loopback(self):
        self.launch()
        # Stop the fixture proxy, then use a real listening origin that must never
        # receive a direct request, including Chrome's usual loopback bypass.
        direct = FixtureServer(('127.0.0.1', 0), FixtureProxy)
        direct.requests = []
        direct.daemon_threads = True
        threading.Thread(target=direct.serve_forever, daemon=True).start()
        try:
            self.proxy.shutdown()
            self.proxy.server_close()
            browser = self.profiles._controllers['account-a']
            with self.assertRaises(ProtocolError):
                browser.navigate(f'http://127.0.0.1:{direct.server_port}/should-not-arrive')
            self.assertEqual(direct.requests, [])
        finally:
            direct.shutdown()
            direct.server_close()
        class IPv6Fixture(FixtureServer):
            address_family = socket.AF_INET6
        try:
            ipv6 = IPv6Fixture(('::1', 0), FixtureProxy)
        except OSError:
            return  # IPv6 can be disabled by the host OS.
        ipv6.requests = []
        ipv6.daemon_threads = True
        threading.Thread(target=ipv6.serve_forever, daemon=True).start()
        try:
            client = http.client.HTTPConnection('::1', ipv6.server_port, timeout=2)
            try:
                client.request('GET', '/control')
            except OSError:
                client.close()
                return  # Some Windows firewall policies deny IPv6 loopback.
            self.assertEqual(client.getresponse().status, 200)
            client.close()
            ipv6.requests.clear()
            with self.assertRaises(ProtocolError):
                browser.navigate(f'http://[::1]:{ipv6.server_port}/should-not-arrive')
            self.assertEqual(ipv6.requests, [])
        finally:
            ipv6.shutdown()
            ipv6.server_close()


if __name__ == '__main__':
    unittest.main()
