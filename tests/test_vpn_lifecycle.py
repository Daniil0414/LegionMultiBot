"""Real local Xray/Chrome lifecycle checks without the game or subscription requests."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import vpn_dispatcher as vpn
from browser_profiles import BrowserProfiles
from proxy_manager import ProxyManager
from userscripts import UserscriptManager
from vpn_runtime import atomic_json, process_details, process_identity, process_is_alive, terminate_verified_process


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_XRAY'),
                     'Set LEGION_TEST_XRAY for isolated real core tests')
class CoreLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='legion-core-lifecycle-')
        def cleanup_directory():
            for attempt in range(20):
                try:
                    temporary.cleanup()
                    return
                except (PermissionError, NotADirectoryError):
                    if attempt == 19:
                        raise
                    time.sleep(.05)
        self.addCleanup(cleanup_directory)
        self.root = Path(temporary.name)
        atomic_json(str(self.root / 'scripts.json'), {'version': 2, 'scripts': []})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {
            'RUNTIME_DIR': str(self.root), 'CONFIG_FILE': str(self.root / 'vpn.json'),
            'LEGACY_CONFIG_FILE': str(self.root / 'legacy.json'),
            'XRAY_CONFIG_FILE': str(self.root / 'core.json'),
            'CORE_OWNER_FILE': str(self.root / 'owner.json'), 'CORE_LOCK_FILE': str(self.root / 'core.lock'),
        }.items():
            self.stack.enter_context(patch.object(vpn, name, value))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        self.stack.enter_context(patch.object(vpn, 'BASE_LOCAL_PORT', self.port))
        self.stack.enter_context(patch('proxy_manager.BASE_LOCAL_PORT', self.port))
        with (patch.object(vpn.VpnDispatcher, '_find_xray_binary', return_value=os.environ['LEGION_TEST_XRAY']),
              patch.object(vpn.atexit, 'register')):
            self.dispatcher = vpn.VpnDispatcher()
        self.addCleanup(self.dispatcher.stop_xray)
        generate = self.dispatcher.generate_xray_config
        self.dispatcher.generate_xray_config = lambda: generate(max_slots=1)

    @unittest.skipUnless(os.environ.get('LEGION_TEST_CHROME'), 'Set LEGION_TEST_CHROME for local Chrome flow')
    def test_clear_export_apply_browser_clear_reapply_and_clean_shutdown(self):
        from test_mobile_browser import FixtureServer, FixtureProxy
        origin = FixtureServer(('127.0.0.1', 0), FixtureProxy)
        origin.requests = []
        origin.daemon_threads = True
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        self.addCleanup(origin.server_close)
        self.addCleanup(origin.shutdown)
        d = self.dispatcher
        self.assertTrue(d.clear_all_nodes()[0])
        node = vpn.parse_raw_nodes(f'http://127.0.0.1:{origin.server_port}')[0]
        config = {'remarks': 'Local lifecycle fixture', 'outbounds': [node.outbound_config]}
        with patch('happ_memory.read_happ_configs', return_value=[config]):
            ok, message, raw = d.export_happ_json()
        self.assertTrue(ok, message)
        self.assertFalse(d.is_running)
        ok, message = d.set_custom_nodes_text(raw)
        self.assertTrue(ok, message)
        first = d._owned_identity
        owner = json.loads(Path(vpn.CORE_OWNER_FILE).read_text())
        self.assertEqual(owner['launcher'], process_identity(os.getpid()))
        profiles = BrowserProfiles(root=self.root / 'profiles',
                                   userscripts=UserscriptManager(self.root / 'scripts.json'))
        self.addCleanup(profiles.shutdown)
        profiles.launch(os.environ['LEGION_TEST_CHROME'], 'fixture', f'socks5://127.0.0.1:{self.port}',
                        ['--headless=new', 'http://lifecycle-fixture.localhost/check'])
        deadline = time.monotonic() + 5
        while not any(request['path'].endswith('/check') for request in origin.requests) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(any(request['path'].endswith('/check') for request in origin.requests))
        profiles.close()
        self.assertTrue(d.clear_all_nodes()[0])
        self.assertNotEqual(process_identity(first['pid']), first)
        self.assertTrue(d.set_custom_nodes_text(raw)[0])
        second = d._owned_identity
        manager = ProxyManager.__new__(ProxyManager)
        manager._closed = threading.Event()
        manager._lock = threading.RLock()
        manager.vpn_dispatcher = d
        manager.close()
        self.assertNotEqual(process_identity(second['pid']), second)
        self.assertFalse(manager.ensure_core_running())
        self.assertFalse(Path(vpn.CORE_OWNER_FILE).exists())

    def test_hard_launcher_exit_kills_its_core_without_atexit(self):
        code = r'''
import json, os, time
from pathlib import Path
import vpn_dispatcher as v
root = Path(os.environ['LEGION_LIFECYCLE_ROOT'])
for name, filename in {'CONFIG_FILE':'child-vpn.json','LEGACY_CONFIG_FILE':'child-legacy.json',
    'XRAY_CONFIG_FILE':'child-core.json','CORE_OWNER_FILE':'child-owner.json','CORE_LOCK_FILE':'child-core.lock'}.items():
    setattr(v, name, str(root / filename))
v.RUNTIME_DIR = str(root)
v.BASE_LOCAL_PORT = int(os.environ['LEGION_LIFECYCLE_PORT'])
d = v.VpnDispatcher()
d.xray_bin = os.environ['LEGION_TEST_XRAY']
d.nodes = v.parse_raw_nodes('socks5://127.0.0.1:12345')
generate = d.generate_xray_config
d.generate_xray_config = lambda: generate(max_slots=1)
if not d.start_xray():
    raise RuntimeError(d.last_error)
print(json.dumps(d._owned_identity), flush=True)
time.sleep(300)
'''
        environment = dict(os.environ, LEGION_LIFECYCLE_ROOT=str(self.root), LEGION_LIFECYCLE_PORT=str(self.port))
        log = self.root / 'child-stderr.log'
        with log.open('w') as errors:
            child = subprocess.Popen([sys.executable, '-B', '-u', '-c', code], env=environment,
                                     stdout=subprocess.PIPE, stderr=errors, text=True, creationflags=0x08000000)
        def cleanup():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
            child.stdout.close()
        self.addCleanup(cleanup)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True).start()
        line = ready.get(timeout=15)
        self.assertTrue(line, log.read_text()[-1500:])
        identity = json.loads(line)
        self.assertEqual(process_identity(identity['pid']), identity)
        child.terminate()
        child.wait(timeout=5)
        deadline = time.monotonic() + 5
        while process_is_alive(identity['pid']) and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertNotEqual(process_identity(identity['pid']), identity)

    @unittest.skipUnless(os.environ.get('LEGION_TEST_CHROME'), 'Set LEGION_TEST_CHROME for local fallback flow')
    def test_failed_route_falls_back_before_launching_real_chrome(self):
        from test_mobile_browser import FixtureServer, FixtureProxy
        origin = FixtureServer(('127.0.0.1', 0), FixtureProxy)
        origin.requests = []
        origin.daemon_threads = True
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        self.addCleanup(origin.server_close)
        self.addCleanup(origin.shutdown)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            dead_port = listener.getsockname()[1]
        # The origin itself may have received the next ephemeral port. Reserve
        # a fresh adjacent pair for the two Xray inbounds before releasing it.
        for _ in range(20):
            with socket.socket() as first, socket.socket() as second:
                first.bind(('127.0.0.1', 0))
                base = first.getsockname()[1]
                try:
                    second.bind(('127.0.0.1', base + 1))
                except OSError:
                    continue
                self.port = base
                break
        else:
            self.fail('Could not reserve two adjacent local fixture ports')
        self.stack.enter_context(patch.object(vpn, 'BASE_LOCAL_PORT', self.port))
        self.stack.enter_context(patch('proxy_manager.BASE_LOCAL_PORT', self.port))
        raw = f'http://127.0.0.1:{dead_port}\nhttp://127.0.0.1:{origin.server_port}'
        d = self.dispatcher
        ok, message = d.set_custom_nodes_text(raw)
        self.assertTrue(ok, message)
        with patch('proxy_manager.VpnDispatcher', return_value=d):
            manager = ProxyManager(self.root, start_monitor=False, isolation_root=self.root / 'isolation')
        self.addCleanup(manager.close)

        def ip_check(proxy, timeout):
            if proxy == f'socks5://127.0.0.1:{self.port}':
                raise ConnectionError('curl (7): fixture primary is unavailable')
            return '8.8.8.8'

        with patch('proxy_manager.fetch_proxy_ip', side_effect=ip_check):
            proxy = manager.get_working_proxy_for_slot(0, profile_key='fallback-fixture')
        self.assertEqual(proxy, f'socks5://127.0.0.1:{self.port + 1}')
        profiles = BrowserProfiles(root=self.root / 'profiles',
                                   userscripts=UserscriptManager(self.root / 'scripts.json'))
        self.addCleanup(profiles.shutdown)
        profiles.launch(os.environ['LEGION_TEST_CHROME'], 'fallback-fixture', proxy,
                        ['--headless=new', 'http://fallback-fixture.localhost/check'])
        deadline = time.monotonic() + 5
        while not any(request['path'].endswith('/check') for request in origin.requests) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(any(request['path'].endswith('/check') for request in origin.requests))

    def test_recovery_handle_rejects_reused_or_wrong_identity(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=0x08000000)
        try:
            identity = process_identity(child.pid)
            details = process_details(child.pid)
            self.assertEqual(details['parent_pid'], os.getpid())
            self.assertEqual(details['arguments'][1], '-c')
            wrong = dict(identity, created=str(int(identity['created']) + 1))
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                terminate_verified_process(wrong)
            self.assertIsNone(child.poll())
            terminate_verified_process(identity)
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def test_apply_new_routes_recovers_a_real_legacy_orphan(self):
        d = self.dispatcher
        d.nodes = vpn.parse_raw_nodes('socks5://127.0.0.1:12345')
        old_config = d.generate_xray_config()
        atomic_json(vpn.XRAY_CONFIG_FILE, old_config)
        code = '''
import json, subprocess, sys, time, warnings
from vpn_runtime import process_identity
warnings.simplefilter('ignore', ResourceWarning)
child = subprocess.Popen([sys.argv[1], '-config', sys.argv[2]],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
identity = None
for attempt in range(50):
    identity = process_identity(child.pid)
    if identity: break
    time.sleep(.02)
print(json.dumps(identity), flush=True)
'''
        helper = subprocess.run([sys.executable, '-B', '-c', code, d.xray_bin, vpn.XRAY_CONFIG_FILE],
                                capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        self.assertEqual(helper.returncode, 0, helper.stderr)
        identity = json.loads(helper.stdout)
        self.assertIsNotNone(identity)
        def cleanup_orphan():
            if process_identity(identity['pid']) == identity:
                terminate_verified_process(identity)
        self.addCleanup(cleanup_orphan)
        self.assertTrue(d._ports_ready(type('Running', (), {'poll': lambda self: None})(), [self.port]))
        atomic_json(vpn.CORE_OWNER_FILE, {'identity': identity, 'fingerprint': d._fingerprint(old_config)})
        d.nodes = vpn.parse_raw_nodes('socks5://127.0.0.1:12346')
        with patch.object(d, '_managed_proxy_browsers_alive', return_value=False):
            self.assertTrue(d.start_xray(), d.last_error)
        self.assertNotEqual(process_identity(identity['pid']), identity)
        self.assertTrue(d.is_running)
        owner = json.loads(Path(vpn.CORE_OWNER_FILE).read_text())
        self.assertEqual(owner['launcher'], process_identity(os.getpid()))
