"""Exercise real SOCKS sockets and Chrome against isolated local VPN fixtures."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import browser_profiles as bp
from chrome_mobile import ProtocolError
from proxy_manager import ProxyManager, RouteConflictError
from userscripts import UserscriptManager
import vpn_dispatcher as vpn
from vpn_failover import FailoverProxy, Route, read_address, read_exact
from vpn_runtime import atomic_json


class SocksServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


class SocksHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(3)
            version, count = read_exact(self.request, 2)
            read_exact(self.request, count)
            self.request.sendall(b'\x05\x00')
            header = read_exact(self.request, 4)
            address = read_address(self.request, header[3])
            port = int.from_bytes(read_exact(self.request, 2), 'big')
            self.server.destinations.append((header[3], address, port))
            code = 1 if self.server.broken else getattr(self.server, 'reject', 0)
            self.request.sendall(bytes([5, code, 0, 1]) + b'\0' * 6)
            if code:
                return
            while True:
                data = self.request.recv(65536)
                if not data:
                    return
                self.server.payloads.append(data)
                if self.server.reset:
                    self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('hh', 1, 0))
                    return
                if data.startswith(b'GET '):
                    body = (('8.8.8.8' if self.server.label == 'first' else '1.1.1.1') if b'GET /egress ' in data else
                            '<title>' + self.server.label + '</title><body>VPN fixture</body>').encode()
                    self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: ' +
                                         str(len(body)).encode() + b'\r\n\r\n' + body)
                    return
                self.request.sendall(self.server.label.encode() + b':' + data)
        except OSError:
            pass


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return bool(predicate())


def connect_gateway(proxy, host='unresolvable-vpn-fixture.invalid', port=443, command=1):
    from urllib.parse import urlsplit
    stream = socket.create_connection(('127.0.0.1', urlsplit(proxy).port), 3)
    stream.sendall(b'\x05\x01\x00')
    if read_exact(stream, 2) != b'\x05\x00':
        stream.close()
        raise AssertionError('SOCKS greeting failed')
    encoded = host.encode('ascii')
    stream.sendall(bytes([5, command, 0, 3, len(encoded)]) + encoded + port.to_bytes(2, 'big'))
    reply = read_exact(stream, 4)
    read_address(stream, reply[3])
    read_exact(stream, 2)
    return stream, reply[1]


class GatewayFixture(unittest.TestCase):
    def setUp(self):
        self.first = self.server('first')
        self.second = self.server('second')

    def server(self, label):
        server = SocksServer(('127.0.0.1', 0), SocksHandler)
        server.label, server.broken, server.reset = label, False, False
        server.destinations, server.payloads = [], []
        server.proxy = f'socks5://127.0.0.1:{server.server_address[1]}'
        threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def gateway(self, *, monitor=True, verify=None, backup=True):
        def check(proxy):
            server = self.first if proxy == self.first.proxy else self.second
            if server.broken:
                raise ConnectionError('fixture VPN is down')
            return '8.8.8.8' if server is self.first else '1.1.1.1'

        def choose(excluded):
            if backup and self.second.proxy not in excluded and not self.second.broken:
                return self.second.proxy, '1.1.1.1'
            return None

        with ExitStack() as stack:
            if not monitor:
                stack.enter_context(patch.object(FailoverProxy, '_monitor'))
            gateway = FailoverProxy(self.first.proxy, '8.8.8.8', verify or check, choose, Mock())
        gateway.CHECK_INTERVAL = .05
        gateway._wake.set()
        self.addCleanup(gateway.close)
        return gateway

    def exchange(self, proxy, expected, **kwargs):
        stream, code = connect_gateway(proxy, **kwargs)
        with stream:
            self.assertEqual(code, 0)
            stream.sendall(b'fixture')
            self.assertEqual(stream.recv(100), expected + b':fixture')


class GatewayTests(GatewayFixture):
    def test_single_transient_probe_failure_does_not_disconnect_active_route(self):
        call_count = [0]
        def flaky_check(proxy):
            if proxy == self.first.proxy:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ConnectionError('transient network hiccup')
                return '8.8.8.8'
            return '1.1.1.1'
        gateway = self.gateway(verify=flaky_check)
        self.assertTrue(wait_for(lambda: gateway.status['standby_ready']))
        time.sleep(.15)
        self.assertEqual(gateway.status['proxy'], self.first.proxy)

    def test_health_failure_switches_same_gateway_and_closes_old_tunnels(self):
        gateway = self.gateway()
        self.assertTrue(wait_for(lambda: gateway.status['standby_ready']))
        old, code = connect_gateway(gateway.proxy)
        self.addCleanup(old.close)
        self.assertEqual(code, 0)
        old.sendall(b'before')
        self.assertEqual(old.recv(100), b'first:before')
        self.first.broken = True
        self.assertTrue(wait_for(lambda: gateway.status['proxy'] == self.second.proxy))
        self.exchange(gateway.proxy, b'second')
        try:
            self.assertEqual(old.recv(100), b'')
        except ConnectionResetError:
            pass

    def test_socks_failure_uses_ready_standby_without_waiting_for_health_timer(self):
        gateway = self.gateway(monitor=False)
        gateway._standby = Route(self.second.proxy, '1.1.1.1', time.monotonic())
        self.first.broken = True
        started = time.monotonic()
        self.exchange(gateway.proxy, b'second')
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(gateway.status['proxy'], self.second.proxy)

    def test_all_servers_down_never_contacts_the_requested_direct_destination(self):
        gateway = self.gateway(monitor=False, backup=False)
        self.first.broken = True
        with socket.socket() as direct:
            direct.bind(('127.0.0.1', 0))
            direct.listen()
            direct.settimeout(.1)
            stream, code = connect_gateway(gateway.proxy, host='127.0.0.1', port=direct.getsockname()[1])
            stream.close()
            self.assertNotEqual(code, 0)
            with self.assertRaises(socket.timeout):
                direct.accept()

    def test_rejected_standby_activation_never_forwards_account_traffic(self):
        gateway = self.gateway(monitor=False)
        gateway._activate = Mock(side_effect=RouteConflictError('fixture standby belongs to another profile'))
        gateway._standby = Route(self.second.proxy, '1.1.1.1', time.monotonic())
        self.first.broken = True
        stream, code = connect_gateway(gateway.proxy)
        stream.close()
        self.assertNotEqual(code, 0)
        gateway._activate.assert_called_once_with(self.second.proxy, '1.1.1.1')
        self.assertEqual(self.second.destinations, [])
        self.assertIsNone(gateway.status['proxy'])

    def test_expired_standby_is_not_used(self):
        gateway = self.gateway(monitor=False)
        gateway._standby = Route(self.second.proxy, '1.1.1.1', time.monotonic() - 60)
        self.first.broken = True
        stream, code = connect_gateway(gateway.proxy)
        stream.close()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.second.destinations, [])

    def test_udp_and_bind_are_blocked(self):
        gateway = self.gateway(monitor=False)
        for command in (2, 3):
            stream, code = connect_gateway(gateway.proxy, command=command)
            stream.close()
            self.assertEqual(code, 7)
        self.assertEqual(self.first.destinations, [])

    def test_destination_hostname_is_forwarded_without_local_dns(self):
        gateway = self.gateway(monitor=False)
        host = 'unresolvable-vpn-fixture.invalid'
        original = socket.getaddrinfo

        def resolve(name, *args, **kwargs):
            self.assertEqual(name, '127.0.0.1')
            return original(name, *args, **kwargs)

        with patch('socket.getaddrinfo', side_effect=resolve):
            self.exchange(gateway.proxy, b'first', host=host)
        self.assertEqual(self.first.destinations[0][1], bytes([len(host)]) + host.encode())

    def test_non_loopback_upstream_is_rejected_before_connect(self):
        gateway = self.gateway(monitor=False)
        gateway._active = Route('socks5://example.invalid:1080', '8.8.8.8', time.monotonic())
        original = socket.create_connection

        def connect(address, *args, **kwargs):
            self.assertEqual(address[0], '127.0.0.1')
            return original(address, *args, **kwargs)

        with patch('socket.create_connection', side_effect=connect):
            stream, code = connect_gateway(gateway.proxy)
            stream.close()
        self.assertNotEqual(code, 0)

    def test_established_request_is_never_replayed_on_another_server(self):
        gateway = self.gateway(monitor=False)
        gateway._standby = Route(self.second.proxy, '1.1.1.1', time.monotonic())
        self.first.reset = True
        stream, code = connect_gateway(gateway.proxy)
        with stream:
            self.assertEqual(code, 0)
            stream.sendall(b'NON-IDEMPOTENT-FIXTURE')
            try:
                self.assertEqual(stream.recv(100), b'')
            except ConnectionResetError:
                pass
        self.assertTrue(wait_for(lambda: self.first.payloads))
        self.assertEqual(self.second.payloads, [])

    def test_a_cancelled_browser_request_does_not_mark_the_vpn_dead(self):
        gateway = self.gateway(monitor=False)
        gateway._standby = Route(self.second.proxy, '1.1.1.1', time.monotonic())
        client = Mock()
        client.recv.side_effect = [b'\x05\x01', b'\x00', b'\x05\x01\x00\x03', b'\x01', b'x', b'\x01\xbb']
        client.sendall.side_effect = [None, ConnectionResetError('fixture client cancelled')]
        gateway.handle(client)
        self.assertEqual(gateway.status['proxy'], self.first.proxy)
        self.assertTrue(gateway.status['standby_ready'])

    def test_a_destination_refusal_does_not_mark_the_vpn_dead(self):
        gateway = self.gateway(monitor=False)
        self.first.reject = 5
        stream, code = connect_gateway(gateway.proxy)
        stream.close()
        self.assertEqual(code, 5)
        self.assertEqual(gateway.status['proxy'], self.first.proxy)

    def test_closed_gateway_stays_blocked(self):
        gateway = self.gateway(monitor=False)
        proxy = gateway.proxy
        gateway.close()
        with self.assertRaises(OSError):
            connect_gateway(proxy)
        self.assertIsNone(gateway.status['ip'])

    def test_a_blocked_gateway_recovers_without_changing_the_browser_proxy(self):
        self.first.broken = self.second.broken = True
        gateway = self.gateway()
        proxy = gateway.proxy
        self.assertTrue(wait_for(lambda: gateway.status['ip'] is None))
        stream, code = connect_gateway(proxy)
        stream.close()
        self.assertNotEqual(code, 0)
        self.second.broken = False
        gateway._wake.set()
        self.assertTrue(wait_for(lambda: gateway.status['proxy'] == self.second.proxy))
        self.assertEqual(gateway.proxy, proxy)
        self.exchange(proxy, b'second')


class ManagerFailoverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='legion-failover-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dispatcher = Mock()
        self.dispatcher.lock = threading.RLock()
        self.dispatcher.nodes = vpn.parse_raw_nodes('socks5://first.invalid:1080\nsocks5://second.invalid:1080\nsocks5://third.invalid:1080')
        self.dispatcher.get_nodes_snapshot.side_effect = lambda: list(self.dispatcher.nodes)
        self.dispatcher.get_slot_info.side_effect = lambda i: (f'socks5://127.0.0.1:{10810+i}', '', '')
        self.dispatcher._health_revision, self.dispatcher._nodes_revision = -1, 0
        self.dispatcher.is_running = True
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('proxy_manager.VpnDispatcher', return_value=self.dispatcher))
        self.stack.enter_context(patch.object(FailoverProxy, '_monitor'))
        self.fetch = self.stack.enter_context(patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'))
        self.manager = ProxyManager(self.root, start_monitor=False, isolation_root=self.root / 'profiles')
        self.addCleanup(self.manager.close)

    def test_gateway_stays_fixed_across_calls_and_reports_current_verified_exit(self):
        proxy = self.manager.get_resilient_proxy_for_slot(0, profile_key='a')
        self.assertNotEqual(proxy, 'socks5://127.0.0.1:10810')
        self.assertEqual(self.manager.get_resilient_proxy_for_slot(0, profile_key='A'), proxy)
        self.assertEqual(self.manager.failover_status(proxy)['ip'], '8.8.8.8')
        self.assertEqual(self.manager._route_ips[proxy], '8.8.8.8')

    def test_standby_prefers_a_free_server_even_when_it_shares_a_public_ip(self):
        self.manager._claim_route('profile:b', ('socks5', 'second.invalid', 1080), '1.1.1.1')
        self.fetch.side_effect = lambda proxy, timeout: '8.8.8.8' if proxy.endswith('10810') else '1.1.1.1'
        self.manager.get_resilient_proxy_for_slot(0, profile_key='a')
        gateway = self.manager._failover_routes['profile:a'][1]
        self.assertEqual(gateway._choose_backup({'socks5://127.0.0.1:10810'}), ('socks5://127.0.0.1:10812', '1.1.1.1'))

    def test_changed_shared_exit_is_registered_without_blocking_the_working_route(self):
        self.manager.get_resilient_proxy_for_slot(0, profile_key='a')
        gateway = self.manager._failover_routes['profile:a'][1]
        self.manager._claim_route('profile:b', ('socks5', 'third.invalid', 1080), '1.1.1.1')
        self.fetch.return_value = '1.1.1.1'
        self.assertEqual(gateway._verify('socks5://127.0.0.1:10810'), '1.1.1.1')
        state = self.manager._isolation_snapshot()
        self.assertEqual(len(state['profiles']), 2)
        self.assertEqual(self.manager._route_ips['socks5://127.0.0.1:10810'], '1.1.1.1')

    def test_changed_node_order_blocks_the_old_gateway(self):
        self.manager.get_resilient_proxy_for_slot(0, profile_key='a')
        gateway = self.manager._failover_routes['profile:a'][1]
        self.dispatcher.nodes.reverse()
        self.assertFalse(gateway._valid())
        with self.assertRaises(ConnectionError):
            gateway._verify('socks5://127.0.0.1:10810')

    def test_standby_probes_do_not_consume_servers_or_ips(self):
        self.fetch.side_effect = lambda proxy, timeout: '8.8.8.8' if proxy.endswith('10810') else '1.1.1.1'
        self.manager.get_resilient_proxy_for_slot(0, profile_key='a')
        gateway = self.manager._failover_routes['profile:a'][1]
        registry = self.manager._isolation_root / '.network-usage.json'
        original = registry.read_bytes()
        standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
        self.assertIsNotNone(standby)
        gateway._probe(standby[0])
        self.assertEqual(registry.read_bytes(), original)
        gateway._standby = Route(*standby, time.monotonic())
        gateway._failed(gateway._active)
        self.assertEqual(gateway.status['proxy'], standby[0])
        state = json.loads(registry.read_text())
        self.assertEqual(len(state['profiles']), 1)
        self.assertEqual(self.manager._route_ips[standby[0]], '1.1.1.1')
        self.assertNotIn('ips', state)

    def test_standby_taken_by_another_profile_can_be_shared_on_activation(self):
        self.dispatcher.nodes = self.dispatcher.nodes[:2]
        self.fetch.side_effect = lambda proxy, timeout: '8.8.8.8' if proxy.endswith('10810') else '1.1.1.1'
        self.manager.get_resilient_proxy_for_slot(0, profile_key='a')
        gateway = self.manager._failover_routes['profile:a'][1]
        standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
        self.manager._claim_route('profile:b', ('socks5', 'second.invalid', 1080), standby[1])
        gateway._standby = Route(*standby, time.monotonic())
        gateway._failed(gateway._active)
        self.assertEqual(gateway.status['proxy'], standby[0])
        self.assertEqual(len(self.manager._isolation_snapshot()['profiles']), 2)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME'), 'Set LEGION_TEST_CHROME for Chrome failover tests')
class ChromeFailoverTests(GatewayFixture):
    def test_browser_changes_vpn_without_restart_and_cannot_use_direct_when_all_fail(self):
        gateway = self.gateway()
        self.assertTrue(wait_for(lambda: gateway.status['standby_ready']))
        with tempfile.TemporaryDirectory(prefix='legion-chrome-failover-') as directory:
            root = Path(directory)
            atomic_json(str(root / 'scripts.json'), {'version': 2, 'scripts': []})
            profiles = bp.BrowserProfiles(root=root / 'profiles', userscripts=UserscriptManager(root / 'scripts.json'))
            try:
                session = profiles.launch(os.environ['LEGION_TEST_CHROME'], 'fixture', gateway.proxy,
                                          ['--headless=new', 'http://vpn-failover-fixture.invalid/first'])
                browser = profiles._controllers['fixture']
                self.assertTrue(wait_for(lambda: any(b'GET /first ' in p for p in self.first.payloads)))
                self.first.broken = True
                gateway._wake.set()
                started = time.monotonic()
                self.assertTrue(wait_for(lambda: gateway.status['proxy'] == self.second.proxy))
                switched = time.monotonic() - started
                browser.navigate('http://vpn-failover-fixture.invalid/second')
                self.assertTrue(wait_for(lambda: any(b'GET /second ' in p for p in self.second.payloads)))
                self.assertEqual(profiles._controllers['fixture'].process.pid, session['pid'])
                self.second.broken = True
                gateway._wake.set()
                self.assertTrue(wait_for(lambda: gateway.status['ip'] is None))
                with socket.socket() as direct:
                    direct.bind(('127.0.0.1', 0))
                    direct.listen()
                    direct.settimeout(.75)
                    try:
                        browser.navigate(f'http://127.0.0.1:{direct.getsockname()[1]}/must-not-arrive')
                    except ProtocolError:
                        pass
                    with self.assertRaises(socket.timeout):
                        direct.accept()
                print(f'Chrome failover: {switched:.3f}s; browser PID unchanged; direct destination not contacted')
            finally:
                profiles.shutdown()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME') and os.environ.get('LEGION_TEST_XRAY'),
                     'Set LEGION_TEST_CHROME and LEGION_TEST_XRAY for the complete local flow')
class XrayChromeFailoverTests(ChromeFailoverTests):
    def gateway(self):
        temporary = tempfile.TemporaryDirectory(prefix='legion-real-failover-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {
            'RUNTIME_DIR': str(root), 'CONFIG_FILE': str(root / 'vpn.json'),
            'LEGACY_CONFIG_FILE': str(root / 'legacy.json'), 'XRAY_CONFIG_FILE': str(root / 'core.json'),
            'CORE_OWNER_FILE': str(root / 'owner.json'), 'CORE_LOCK_FILE': str(root / 'core.lock'),
        }.items():
            stack.enter_context(patch.object(vpn, name, value))
        for _ in range(30):
            with socket.socket() as first, socket.socket() as second:
                first.bind(('127.0.0.1', 0))
                base = first.getsockname()[1]
                try:
                    second.bind(('127.0.0.1', base + 1))
                except OSError:
                    continue
                break
        else:
            self.fail('Could not reserve adjacent local Xray ports')
        stack.enter_context(patch.object(vpn, 'BASE_LOCAL_PORT', base))
        stack.enter_context(patch('proxy_manager.BASE_LOCAL_PORT', base))
        with (patch.object(vpn.VpnDispatcher, '_find_xray_binary', return_value=os.environ['LEGION_TEST_XRAY']),
              patch.object(vpn.atexit, 'register')):
            dispatcher = vpn.VpnDispatcher()
        self.addCleanup(dispatcher.stop_xray)
        dispatcher.nodes = vpn.parse_raw_nodes(self.first.proxy + '\n' + self.second.proxy)
        generate = dispatcher.generate_xray_config
        dispatcher.generate_xray_config = lambda: generate(max_slots=2)
        self.assertTrue(dispatcher.start_xray(), dispatcher.last_error)
        stack.enter_context(patch('proxy_manager.VpnDispatcher', return_value=dispatcher))

        def check_exit(proxy, timeout):
            result = subprocess.run(['curl.exe', '--silent', '--show-error', '--fail', '--max-time', str(timeout),
                                     '--noproxy', '', '--proxy', proxy.replace('socks5:', 'socks5h:', 1),
                                     'http://vpn-health-fixture.invalid/egress'], capture_output=True, text=True,
                                    timeout=timeout + 2, creationflags=0x08000000)
            if result.returncode:
                raise ConnectionError(result.stderr.strip())
            return result.stdout.strip()

        stack.enter_context(patch('proxy_manager.fetch_proxy_ip', side_effect=check_exit))
        manager = ProxyManager(root, start_monitor=False, isolation_root=root / 'profiles')
        self.addCleanup(manager.close)
        manager.get_resilient_proxy_for_slot(0, profile_key='fixture')
        gateway = manager._failover_routes['profile:fixture'][1]
        gateway.CHECK_INTERVAL = .05
        gateway._wake.set()
        # The browser-flow assertions compare backend identifiers. Here those
        # identifiers are Xray's stable inbounds, not the remote SOCKS fixtures.
        self.first.proxy = f'socks5://127.0.0.1:{base}'
        self.second.proxy = f'socks5://127.0.0.1:{base + 1}'
        return gateway
