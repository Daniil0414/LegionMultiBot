import concurrent.futures
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import browser_profiles as bp
import proxy_network
import vpn_dispatcher as vpn
from proxy_manager import ProxyManager, ProxySelectionCancelled, parse_proxy_entry
from vpn_runtime import atomic_json
from userscripts import UserscriptManager


class Process:
    pid = 123456

    def __init__(self, code=None):
        self.code = code
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.terminate()


class VpnSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legion-vpn-tests-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        atomic_json(str(root / 'scripts.json'), {'version': 2, 'scripts': []})
        scripts = patch.object(bp, 'UserscriptManager', side_effect=lambda: UserscriptManager(root / 'scripts.json'))
        scripts.start()
        self.addCleanup(scripts.stop)
        for name, value in {
            'RUNTIME_DIR': str(root), 'CONFIG_FILE': str(root / 'private.json'),
            'LEGACY_CONFIG_FILE': str(root / 'legacy.json'),
            'XRAY_CONFIG_FILE': str(root / 'core.json'),
            'CORE_OWNER_FILE': str(root / 'owner.json'), 'CORE_LOCK_FILE': str(root / 'core.lock'),
        }.items():
            patcher = patch.object(vpn, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with (patch.object(vpn.VpnDispatcher, '_find_xray_binary', return_value=os.path.abspath('fake-xray.exe')),
              patch.object(vpn.atexit, 'register')):
            self.dispatcher = vpn.VpnDispatcher()
        self.dispatcher.nodes = vpn.parse_raw_nodes('socks5://a.invalid:1080\nsocks5://b.invalid:1080')
        self.dispatcher.config['raw_nodes_text'] = 'socks5://a.invalid:1080\nsocks5://b.invalid:1080'
        self.addCleanup(self.dispatcher.stop_xray)

    def test_probe_failure_never_marks_nodes_alive_or_deletes_keys(self):
        d = self.dispatcher
        original = d.config.copy()
        process = Process()
        with patch.object(d, '_spawn', return_value=process), patch.object(d, 'check_port_alive', return_value=False), patch.object(d, 'stop_xray') as stop:
            self.assertEqual(d.filter_alive_nodes(), (0, 2))
            stop.assert_not_called()
        self.assertEqual(d.alive_nodes, [])
        self.assertEqual(d.alive_indices, set())
        self.assertTrue(process.terminated)
        self.assertFalse(d.purge_dead_nodes()[0])
        self.assertEqual(d.config, original)
        self.assertEqual(len(d.nodes), 2)

    def test_probe_uses_other_ports_and_preserves_live_routes(self):
        d = self.dispatcher
        before = d.generate_xray_config()
        running = Process()
        d.xray_proc = running
        configs = []
        def spawn(path, cfg):
            configs.append(cfg)
            return Process()
        with patch.object(d, '_spawn', side_effect=spawn), patch.object(d, 'check_port_alive', return_value=True):
            self.assertEqual(d.filter_alive_nodes(), (2, 2))
        live_ports = {x['port'] for x in before['inbounds']}
        self.assertTrue(live_ports.isdisjoint({x['port'] for x in configs[0]['inbounds']}))
        self.assertEqual(before, d.generate_xray_config())
        self.assertFalse(running.terminated)

    def test_all_nodes_have_a_route_and_unmatched_traffic_is_blocked(self):
        d = self.dispatcher
        d.nodes *= 32
        cfg = d.generate_xray_config()
        self.assertEqual(len(cfg['inbounds']), 64)
        self.assertEqual(cfg['outbounds'][0], {'tag': 'block-all', 'protocol': 'blackhole'})
        self.assertTrue(all(x['listen'] == '127.0.0.1' for x in cfg['inbounds']))
        self.assertNotIn('freedom', [x['protocol'] for x in cfg['outbounds']])

    def test_concurrent_pool_requests_share_one_probe(self):
        d = self.dispatcher
        entered, release = threading.Event(), threading.Event()
        def spawn(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return Process()
        with patch.object(d, '_spawn', side_effect=spawn) as start, patch.object(d, 'check_port_alive', return_value=True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(d.ensure_alive_pool)
                self.assertTrue(entered.wait(3))
                second = pool.submit(d.ensure_alive_pool)
                time.sleep(0.05)
                release.set()
                self.assertEqual(first.result(3), 2)
                self.assertEqual(second.result(3), 2)
            self.assertEqual(start.call_count, 1)

    def test_exited_child_is_not_running(self):
        d = self.dispatcher
        d.xray_proc = Process(1)
        self.assertFalse(d.is_running)
        with patch.object(vpn.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)), patch.object(d, '_spawn', side_effect=RuntimeError('Xray exited')):
            self.assertFalse(d.start_xray())
        self.assertFalse(d.is_running)

    def test_invalid_config_does_not_stop_working_child(self):
        d = self.dispatcher
        process = Process()
        d.xray_proc = process
        identity = {'pid': process.pid, 'created': '1', 'executable': d.xray_bin}
        atomic_json(vpn.CORE_OWNER_FILE, {'identity': identity, 'fingerprint': 'previous'})
        with patch.object(vpn, 'process_identity', return_value=identity), patch.object(vpn.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(d.start_xray())
        self.assertFalse(process.terminated)
        self.assertTrue(d.is_running)

    def test_failed_owner_registration_cleans_up_new_child(self):
        d = self.dispatcher
        process = Process()
        identity = {'pid': process.pid, 'created': '5', 'executable': d.xray_bin}
        def write(path, data):
            if path == vpn.CORE_OWNER_FILE:
                raise OSError('registration failed')
            atomic_json(path, data)
        with (patch.object(vpn, 'atomic_json', side_effect=write),
              patch.object(vpn, 'process_identity', return_value=identity),
              patch.object(d, '_check_ports_free'),
              patch.object(d, '_spawn', return_value=process),
              patch.object(vpn.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0))):
            self.assertFalse(d.start_xray())
        self.assertTrue(process.terminated)
        self.assertFalse(d.is_running)

    def test_adopted_core_is_verified_and_never_killed(self):
        d = self.dispatcher
        identity = {'pid': 456789, 'created': '2', 'executable': os.path.normcase(d.xray_bin)}
        atomic_json(vpn.CORE_OWNER_FILE, {'identity': identity, 'fingerprint': d._fingerprint(d.generate_xray_config())})
        with (patch.object(vpn, 'process_identity', return_value=identity),
              patch.object(vpn, 'process_details', return_value=None),
              patch.object(vpn.subprocess, 'Popen') as spawn):
            self.assertTrue(d.start_xray())
            self.assertTrue(d.is_running)
            d.stop_xray()
            spawn.assert_not_called()
        self.assertTrue(Path(vpn.CORE_OWNER_FILE).exists())

    def test_clear_saved_servers_detaches_without_killing_shared_core(self):
        d = self.dispatcher
        identity = {'pid': 456789, 'created': '2', 'executable': os.path.normcase(d.xray_bin)}
        owner = {'identity': identity, 'fingerprint': d._fingerprint(d.generate_xray_config())}
        atomic_json(vpn.CORE_OWNER_FILE, owner)
        d._external_identity = identity
        with patch.object(vpn, 'process_identity', return_value=identity), patch.object(d, '_terminate') as terminate:
            ok, message = d.clear_all_nodes()
        self.assertTrue(ok)
        self.assertIn('другого запускателя', message)
        terminate.assert_not_called()
        self.assertEqual(json.loads(Path(vpn.CORE_OWNER_FILE).read_text()), owner)
        self.assertEqual(json.loads(Path(vpn.CONFIG_FILE).read_text()), vpn.DEFAULT_CONFIG)
        self.assertEqual(d.nodes, [])
        self.assertEqual(d.alive_nodes, [])
        self.assertIsNone(d._external_identity)

    def test_clear_saved_servers_stops_owned_core(self):
        d = self.dispatcher
        process = Process()
        identity = {'pid': process.pid, 'created': '5', 'executable': d.xray_bin}
        d.xray_proc, d._owned_identity = process, identity
        atomic_json(vpn.CORE_OWNER_FILE, {'identity': identity, 'fingerprint': 'owned'})
        ok, message = d.clear_all_nodes()
        self.assertTrue(ok)
        self.assertIn('остановлено', message)
        self.assertTrue(process.terminated)
        self.assertFalse(Path(vpn.CORE_OWNER_FILE).exists())
        self.assertEqual(d.nodes, [])
        self.assertEqual(d.config, vpn.DEFAULT_CONFIG)

    def test_failed_clear_save_preserves_servers_and_running_core(self):
        d = self.dispatcher
        d.save_config()
        saved = Path(vpn.CONFIG_FILE).read_bytes()
        previous = (d.nodes, d.alive_nodes, d.alive_indices, dict(d.config),
                    d._nodes_revision, d._health_revision, d._health_checked_at)
        with patch.object(d, 'save_config', side_effect=OSError('disk full')), patch.object(d, 'stop_xray') as stop:
            ok, message = d.clear_all_nodes()
        self.assertFalse(ok)
        self.assertIn('disk full', message)
        stop.assert_not_called()
        self.assertEqual(Path(vpn.CONFIG_FILE).read_bytes(), saved)
        self.assertEqual((d.nodes, d.alive_nodes, d.alive_indices, d.config,
                          d._nodes_revision, d._health_revision, d._health_checked_at), previous)

    def test_foreign_listener_is_not_killed(self):
        d = self.dispatcher
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            cfg = {'inbounds': [{'port': listener.getsockname()[1]}]}
            with patch.object(vpn.subprocess, 'Popen') as spawn:
                with self.assertRaises(OSError):
                    d._spawn('unused.json', cfg)
                spawn.assert_not_called()

    def test_foreign_listener_keeps_existing_runtime_file(self):
        d = self.dispatcher
        Path(vpn.XRAY_CONFIG_FILE).write_text('preserved config')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            cfg = {'inbounds': [{'port': listener.getsockname()[1]}]}
            with patch.object(d, 'generate_xray_config', return_value=cfg), patch.object(vpn.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
                self.assertFalse(d.start_xray())
        self.assertEqual(Path(vpn.XRAY_CONFIG_FILE).read_text(), 'preserved config')

    def test_subscription_failure_is_reported_and_old_routes_remain(self):
        d = self.dispatcher
        old = list(d.nodes)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'socks5://new.invalid:1080'
        opener = types.SimpleNamespace(open=lambda *a, **kw: response)
        with patch.object(vpn.urllib.request, 'build_opener', return_value=opener), patch.object(d, 'start_xray', return_value=False):
            self.assertFalse(d.fetch_subscription('https://subscription.invalid/list')[0])
        self.assertEqual(d.nodes, old)

    def test_managed_browser_launch_contains_all_network_guards(self):
        profiles = bp.BrowserProfiles(root=Path(self.temp.name) / 'profiles')
        identity = {'pid': Process.pid, 'created': '4', 'executable': 'chrome.exe'}
        with patch.object(profiles, '_has_unmanaged_browser', return_value=False), patch.object(bp, 'process_identity', return_value=identity), patch.object(bp.subprocess, 'Popen', return_value=Process()) as spawn, patch.object(bp, 'MobileBrowser') as mobile:
            mobile.return_value.start.side_effect = lambda args: bp.subprocess.Popen(args)
            mobile.return_value.device = {'model': 'Pixel 7'}
            session = profiles.launch('chrome.exe', 'test', 'socks5://127.0.0.1:10810', ['https://example.invalid'])
        self.assertEqual(session['pid'], Process.pid)
        args = spawn.call_args.args[0]
        self.assertIn('--proxy-server=socks5://127.0.0.1:10810', args)
        self.assertIn('--disable-quic', args)
        self.assertIn('--force-webrtc-ip-handling-policy=disable_non_proxied_udp', args)
        self.assertNotIn('--host-resolver-rules', ' '.join(args))
        self.assertIn('--dns-prefetch-disable', args)
        self.assertNotIn('https://example.invalid', args)
        self.assertIn('about:blank', args)
        mobile.return_value.navigate.assert_called_once_with('https://example.invalid')

    def test_headless_launcher_blocks_browser_when_vpn_is_down(self):
        from secure_launcher import launch_request
        manager, profiles = Mock(), Mock()
        manager.get_resilient_proxy_for_slot.return_value = 'ERROR_ALL_DOWN'
        with self.assertRaises(ConnectionError):
            launch_request(manager, profiles, {'key': 'test', 'url': 'https://example.invalid'})
        profiles.launch.assert_not_called()

    def test_corrupted_config_is_preserved(self):
        Path(vpn.CONFIG_FILE).write_text('{broken config')
        with patch.object(vpn.VpnDispatcher, '_find_xray_binary', return_value=None), patch.object(vpn.atexit, 'register'):
            d = vpn.VpnDispatcher()
        self.assertEqual(d.nodes, [])
        self.assertIn('Не удалось прочитать', d.last_error)
        self.assertEqual(Path(vpn.CONFIG_FILE).read_text(), '{broken config')

    def test_replacing_nodes_clears_old_health_cache(self):
        d = self.dispatcher
        d.alive_nodes, d.alive_indices = [d.nodes[0]], {0}
        with patch.object(d, 'start_xray', return_value=True):
            self.assertTrue(d.set_custom_nodes_text('socks5://new.invalid:1080')[0])
        self.assertEqual(d.nodes[0].address, 'new.invalid')
        self.assertEqual(d.alive_nodes, [])
        self.assertEqual(d.alive_indices, set())
        self.assertEqual(json.loads(Path(vpn.CONFIG_FILE).read_text())['raw_nodes_text'], 'socks5://new.invalid:1080')

    def test_failed_apply_reports_failure_and_preserves_previous_keys(self):
        d = self.dispatcher
        old_nodes, old_config = list(d.nodes), dict(d.config)
        with patch.object(d, 'start_xray', return_value=False):
            self.assertFalse(d.set_custom_nodes_text('socks5://new.invalid:1080')[0])
        self.assertEqual(d.nodes, old_nodes)
        self.assertEqual(d.config, old_config)

    def test_bad_proxy_never_starts_browser(self):
        profiles = bp.BrowserProfiles(root=Path(self.temp.name) / 'profiles')
        for proxy in (None, '', 'DIRECT', 'ERROR_ALL_DOWN', 'ftp://example.invalid:22', 'socks5://user:pass@example.invalid:1080'):
            with self.subTest(proxy=proxy), patch.object(bp.subprocess, 'Popen') as spawn:
                with self.assertRaises(ValueError):
                    profiles.launch('chrome.exe', 'test', proxy, [])
                spawn.assert_not_called()

    def test_proxy_flags_cannot_be_overridden(self):
        profiles = bp.BrowserProfiles(root=Path(self.temp.name) / 'profiles')
        for flag in ('--no-proxy-server', '--proxy-server=direct://', '--profile-directory=Default', '--enable-quic', '--force-webrtc-ip-handling-policy=default'):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                profiles.launch('chrome.exe', 'test', 'socks5://127.0.0.1:10810', [flag])

    def test_existing_unmanaged_profile_is_rejected(self):
        profiles = bp.BrowserProfiles(root=Path(self.temp.name) / 'profiles')
        with patch.object(profiles, '_has_unmanaged_browser', return_value=True), patch.object(profiles, '_recover_profile', return_value=False), patch.object(bp.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'previously opened profile'):
                profiles.launch('chrome.exe', 'test', 'socks5://127.0.0.1:10810', [])
            spawn.assert_not_called()

    def test_live_profile_cannot_silently_switch_proxy(self):
        profiles = bp.BrowserProfiles(root=Path(self.temp.name) / 'profiles')
        directory = profiles.prepare('test')
        identity = {'pid': 123, 'created': '3', 'executable': 'chrome.exe'}
        atomic_json(str(directory / '.legion-session.json'), {'identity': identity, 'proxy': 'socks5://127.0.0.1:10810'})
        with patch.object(bp, 'process_identity', return_value=identity), patch.object(profiles, '_recover_profile', return_value=False), patch.object(bp.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'before changing'):
                profiles.launch('chrome.exe', 'test', 'socks5://127.0.0.1:10811', [])
            spawn.assert_not_called()

    def test_curl_fallback_keeps_exact_proxy_and_remote_dns(self):
        failed = types.SimpleNamespace(returncode=1, stdout='')
        success = types.SimpleNamespace(returncode=0, stdout='8.8.8.8\n')
        with patch.object(proxy_network.subprocess, 'run', side_effect=[failed, success]) as run:
            self.assertEqual(proxy_network.fetch_proxy_ip('socks5://127.0.0.1:10810'), '8.8.8.8')
        for call in run.call_args_list:
            args = call.args[0]
            self.assertEqual(args[args.index('--proxy') + 1], 'socks5h://127.0.0.1:10810')
            self.assertEqual(args[args.index('--noproxy') + 1], '')

    def test_authentication_is_retained_for_xray(self):
        entry = parse_proxy_entry('socks5://user:pass@proxy.invalid:1080')
        self.assertIn('user:pass@', entry.clean)
        self.assertIsNone(parse_proxy_entry('unsupported://proxy.invalid:22'))

    def test_https_proxy_retains_tls_and_default_port(self):
        entry = parse_proxy_entry('https://user:pass@proxy.invalid')
        node = vpn.parse_raw_nodes(entry.clean)[0]
        self.assertEqual(node.port, 443)
        self.assertEqual(node.outbound_config['streamSettings']['security'], 'tls')
        self.assertFalse(node.outbound_config['streamSettings']['tlsSettings']['allowInsecure'])

    def test_remote_dns_socks_url_is_accepted_by_xray_parser(self):
        node = vpn.parse_raw_nodes('socks5h://user:pass@proxy.invalid:1080')[0]
        self.assertEqual(node.outbound_config['protocol'], 'socks')
        self.assertEqual(node.outbound_config['settings']['servers'][0]['users'][0]['user'], 'user')

    def test_monitor_recovers_dead_core(self):
        manager = ProxyManager.__new__(ProxyManager)
        manager._lock = threading.RLock()
        manager._closed = threading.Event()
        manager.vpn_dispatcher = types.SimpleNamespace(nodes=[1], is_running=False, start_xray=Mock(return_value=True))
        self.assertTrue(manager.ensure_core_running())
        manager.vpn_dispatcher.start_xray.assert_called_once()

    def test_manager_close_stops_owned_core_and_prevents_monitor_restart(self):
        manager = ProxyManager.__new__(ProxyManager)
        manager._lock = threading.RLock()
        manager._closed = threading.Event()
        manager.vpn_dispatcher = Mock(nodes=[1], is_running=False)
        manager.close()
        manager.vpn_dispatcher.stop_xray.assert_called_once_with()
        self.assertFalse(manager.ensure_core_running())
        manager.vpn_dispatcher.start_xray.assert_not_called()

    def orphan_case(self, *, parent_alive=False, browser_alive=False, wrong_config=False):
        d = self.dispatcher
        identity = {'pid': 456789, 'created': '200', 'executable': os.path.normcase(d.xray_bin)}
        owner = {'identity': identity, 'fingerprint': 'old configuration'}
        atomic_json(vpn.CORE_OWNER_FILE, owner)
        terminated = []

        def identify(pid):
            if pid == identity['pid']:
                return None if terminated else identity
            if pid == 456788 and parent_alive:
                return {'pid': pid, 'created': '100', 'executable': 'python.exe'}
            return None

        arguments = [d.xray_bin, '-config', 'unrelated.json' if wrong_config else vpn.XRAY_CONFIG_FILE]
        with (patch.object(vpn, 'process_identity', side_effect=identify),
              patch.object(vpn, 'process_details', return_value={'parent_pid': 456788, 'arguments': arguments}),
              patch.object(vpn, 'process_is_alive', return_value=parent_alive),
              patch.object(d, '_managed_proxy_browsers_alive', return_value=browser_alive),
              patch.object(vpn, 'terminate_verified_process', side_effect=lambda value: terminated.append(value))):
            recovered = d._recover_orphan_core(owner)
        return recovered, terminated

    def test_legacy_orphan_with_our_exact_config_is_recovered(self):
        recovered, terminated = self.orphan_case()
        self.assertTrue(recovered)
        self.assertEqual(len(terminated), 1)
        self.assertFalse(Path(vpn.CORE_OWNER_FILE).exists())

    def test_running_parent_prevents_orphan_recovery(self):
        recovered, terminated = self.orphan_case(parent_alive=True)
        self.assertFalse(recovered)
        self.assertEqual(terminated, [])
        self.assertTrue(Path(vpn.CORE_OWNER_FILE).exists())

    def test_live_browser_prevents_orphan_recovery(self):
        recovered, terminated = self.orphan_case(browser_alive=True)
        self.assertFalse(recovered)
        self.assertEqual(terminated, [])

    def test_other_config_cannot_be_killed_even_with_same_xray_executable(self):
        recovered, terminated = self.orphan_case(wrong_config=True)
        self.assertFalse(recovered)
        self.assertEqual(terminated, [])

    def isolation_manager(self):
        with patch('proxy_manager.VpnDispatcher', return_value=self.dispatcher):
            manager = ProxyManager(self.temp.name, start_monitor=False,
                                   isolation_root=Path(self.temp.name) / 'profiles')
        self.addCleanup(manager.close)
        return manager

    def test_failed_routes_try_other_vpn_nodes_but_never_happ_or_direct(self):
        manager = self.isolation_manager()
        with patch.object(manager, 'ensure_core_running', return_value=True), patch.object(manager, 'check_proxy_alive', return_value=False) as check:
            self.assertEqual(manager.get_working_proxy_for_slot(0, profile_key='a'), 'ERROR_ALL_DOWN')
        self.assertEqual([call.args[0] for call in check.call_args_list],
                         ['socks5://127.0.0.1:10810', 'socks5://127.0.0.1:10811'])
        self.assertTrue(all(call.kwargs['timeout'] == 6 for call in check.call_args_list))

    def test_failed_primary_falls_back_and_reuses_the_selected_vpn(self):
        manager = self.isolation_manager()
        with (patch.object(manager, 'ensure_core_running', return_value=True),
              patch('proxy_manager.fetch_proxy_ip', side_effect=[ConnectionError('curl (28): timed out'), '8.8.8.8', '8.8.8.8']) as fetch):
            self.assertEqual(manager.get_working_proxy_for_slot(0, profile_key='a'), 'socks5://127.0.0.1:10811')
            self.assertEqual(manager.get_working_proxy_for_slot(0, profile_key='a'), 'socks5://127.0.0.1:10811')
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         ['socks5://127.0.0.1:10810', 'socks5://127.0.0.1:10811', 'socks5://127.0.0.1:10811'])

    def test_startup_health_results_are_used_before_the_unhealthy_primary(self):
        manager = self.isolation_manager()
        self.dispatcher.alive_indices = {1}
        self.dispatcher._health_revision = self.dispatcher._nodes_revision
        with (patch.object(manager, 'ensure_core_running', return_value=True),
              patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8') as fetch):
            self.assertEqual(manager.get_working_proxy_for_slot(0, profile_key='a'), 'socks5://127.0.0.1:10811')
        fetch.assert_called_once_with('socks5://127.0.0.1:10811', timeout=6)

    def test_fallback_skips_other_profiles_routes_and_exit_ips(self):
        manager = self.isolation_manager()
        self.dispatcher.nodes += vpn.parse_raw_nodes('socks5://third.invalid:1080')
        manager._claim_route('profile:a', ('socks5', 'a.invalid', 1080), '8.8.8.8')
        with (patch.object(manager, 'ensure_core_running', return_value=True),
              patch('proxy_manager.fetch_proxy_ip', side_effect=['8.8.8.8', '1.1.1.1']) as fetch):
            self.assertEqual(manager.get_working_proxy_for_slot(1, profile_key='b'), 'socks5://127.0.0.1:10812')
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         ['socks5://127.0.0.1:10811', 'socks5://127.0.0.1:10812'])
        state = json.loads((manager._isolation_root / '.network-isolation.json').read_text())
        self.assertEqual(state['ips']['8.8.8.8'], 'profile:a')
        self.assertEqual(state['ips']['1.1.1.1'], 'profile:b')

    def test_cancelled_fallback_does_not_start_core_or_probe_servers(self):
        manager = self.isolation_manager()
        with (patch.object(manager, 'ensure_core_running') as core,
              patch.object(manager, 'check_proxy_alive') as check):
            with self.assertRaises(ProxySelectionCancelled):
                manager.get_working_proxy_for_slot(0, profile_key='a', cancel_callback=lambda: True)
        core.assert_not_called()
        check.assert_not_called()

    def test_https_failure_preserves_both_curl_errors_and_never_uses_direct(self):
        errors = [types.SimpleNamespace(returncode=28, stdout='', stderr='curl: (28) Operation timed out'),
                  types.SimpleNamespace(returncode=35, stdout='', stderr='curl: (35) TLS handshake failed')]
        with patch.object(proxy_network.subprocess, 'run', side_effect=errors) as run:
            with self.assertRaises(ConnectionError) as caught:
                proxy_network.fetch_proxy_ip('socks5://127.0.0.1:10810')
        self.assertIn('curl (28)', str(caught.exception))
        self.assertIn('curl (35)', str(caught.exception))
        for call in run.call_args_list:
            self.assertEqual(call.args[0][call.args[0].index('--proxy') + 1], 'socks5h://127.0.0.1:10810')

    def test_https_error_log_keeps_code_but_omits_proxy_credentials(self):
        proxy = 'socks5://private-user:private-password@127.0.0.1:10810'
        errors = [types.SimpleNamespace(returncode=28, stdout='', stderr='curl: (28) ' + proxy)] * 2
        with (patch.object(proxy_network.subprocess, 'run', side_effect=errors),
              self.assertLogs(proxy_network.NETWORK_LOG, level='WARNING') as captured):
            with self.assertRaises(ConnectionError):
                proxy_network.fetch_proxy_ip(proxy)
        text = '\n'.join(captured.output)
        self.assertIn('curl (28)', text)
        self.assertNotIn('private-user', text)
        self.assertNotIn('private-password', text)

    def test_invalid_primary_ip_response_uses_backup_check_service(self):
        replies = [types.SimpleNamespace(returncode=0, stdout='not json'),
                   types.SimpleNamespace(returncode=0, stdout='8.8.8.8\n')]
        with patch.object(proxy_network.subprocess, 'run', side_effect=replies):
            self.assertEqual(proxy_network.fetch_proxy_ip('socks5://127.0.0.1:10810'), '8.8.8.8')

    def test_shared_exit_blocks_other_profile_across_manager_restart(self):
        manager = self.isolation_manager()
        with patch.object(manager, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            manager.get_working_proxy_for_slot(0, profile_key='a')
        other = self.isolation_manager()
        with patch.object(other, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            with self.assertRaisesRegex(ConnectionError, 'Внешний IP уже закреплён'):
                other.get_working_proxy_for_slot(1, profile_key='b')
            self.assertEqual(other.get_working_proxy_for_slot(0, profile_key='A'), 'socks5://127.0.0.1:10810')

    def test_unique_exits_are_allowed_and_prior_ips_stay_reserved(self):
        manager = self.isolation_manager()
        with patch.object(manager, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', side_effect=['8.8.8.8', '1.1.1.1', '9.9.9.9', '8.8.8.8']):
            manager.get_working_proxy_for_slot(0, profile_key='a')
            manager.get_working_proxy_for_slot(1, profile_key='b')
            manager.get_working_proxy_for_slot(0, profile_key='a')
            with self.assertRaisesRegex(ConnectionError, 'Внешний IP уже закреплён'):
                manager.get_working_proxy_for_slot(1, profile_key='b')

    def test_reused_server_blocks_even_if_exit_ip_rotates(self):
        manager = self.isolation_manager()
        with patch.object(manager, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', side_effect=['8.8.8.8', '1.1.1.1']):
            manager.get_working_proxy_for_slot(0, profile_key='a')
            with self.assertRaisesRegex(ConnectionError, 'VPN-сервер уже закреплён'):
                manager._claim_route('profile:b', ('socks5', 'a.invalid', 1080), '1.1.1.1')

    def test_fully_reserved_pool_fails_without_any_network_probes(self):
        manager = self.isolation_manager()
        manager._claim_route('profile:a', ('socks5', 'a.invalid', 1080), '8.8.8.8')
        manager._claim_route('profile:b', ('socks5', 'b.invalid', 1080), '1.1.1.1')
        with (patch.object(manager, 'ensure_core_running', return_value=True) as core,
              patch('proxy_manager.fetch_proxy_ip') as fetch):
            with self.assertRaisesRegex(ConnectionError, 'нет свободных серверов: занято 2'):
                manager.get_working_proxy_for_slot(5, profile_key='sixth')
        fetch.assert_not_called()
        core.assert_not_called()

    def test_restart_reuses_owned_server_before_consuming_a_new_one(self):
        manager = self.isolation_manager()
        manager._claim_route('profile:a', ('socks5', 'b.invalid', 1080), '8.8.8.8')
        with (patch.object(manager, 'ensure_core_running', return_value=True),
              patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8') as fetch):
            self.assertEqual(manager.get_working_proxy_for_slot(0, profile_key='a'), 'socks5://127.0.0.1:10811')
        fetch.assert_called_once_with('socks5://127.0.0.1:10811', timeout=6)

    def test_reserved_servers_are_excluded_from_progress_and_failure_counts(self):
        manager = self.isolation_manager()
        manager._claim_route('profile:a', ('socks5', 'a.invalid', 1080), '8.8.8.8')
        progress = []
        with (patch.object(manager, 'ensure_core_running', return_value=True),
              patch('proxy_manager.fetch_proxy_ip', side_effect=ConnectionError('fixture timeout')) as fetch):
            self.assertEqual(manager.get_working_proxy_for_slot(5, profile_key='sixth', status_callback=progress.append), 'ERROR_ALL_DOWN')
        fetch.assert_called_once_with('socks5://127.0.0.1:10815', timeout=6)
        self.assertIn('проверка VPN 1/1', progress[0])
        self.assertIn('занято другими профилями: 1', manager.vpn_dispatcher.last_error)
        self.assertIn('fixture timeout', manager.vpn_dispatcher.last_error)

    def test_corrupt_isolation_history_blocks_without_overwriting(self):
        manager = self.isolation_manager()
        path = manager._isolation_root / '.network-isolation.json'
        path.parent.mkdir(parents=True)
        for content in ('broken', '{}', '{"version":1,"routes":{},"ips":{"127.0.0.1":"profile:a"}}'):
            with self.subTest(content=content):
                path.write_text(content, encoding='utf-8')
                with patch.object(manager, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
                    with self.assertRaises(ConnectionError):
                        manager.get_working_proxy_for_slot(0, profile_key='a')
                self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_parallel_managers_cannot_claim_same_exit(self):
        managers = [self.isolation_manager(), self.isolation_manager()]
        barrier = threading.Barrier(2)
        def claim(index):
            barrier.wait(timeout=5)
            try:
                managers[index]._claim_route(f'profile:{index}', ('server', index), '8.8.8.8')
                return True
            except ConnectionError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(claim, range(2))), [False, True])

    def test_ipv4_mapped_ipv6_cannot_bypass_exit_ownership(self):
        manager = self.isolation_manager()
        manager._claim_route('profile:a', ('server', 0), '8.8.8.8')
        with self.assertRaisesRegex(ConnectionError, 'Внешний IP уже закреплён'):
            manager._claim_route('profile:b', ('server', 1), '::ffff:8.8.8.8')

    def test_failed_check_discards_old_verified_ip(self):
        manager = self.isolation_manager()
        proxy = 'socks5://127.0.0.1:10810'
        manager._route_ips[proxy] = '8.8.8.8'
        with patch('proxy_manager.fetch_proxy_ip', side_effect=ConnectionError('probe failed')):
            self.assertFalse(manager.check_proxy_alive(proxy))
        self.assertNotIn(proxy, manager._route_ips)

    def test_changed_node_count_during_probe_blocks_route_reassignment(self):
        manager = self.isolation_manager()
        def probe(*args, **kwargs):
            self.dispatcher.nodes.extend(vpn.parse_raw_nodes('socks5://c.invalid:1080'))
            return '8.8.8.8'
        with patch.object(manager, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', side_effect=probe):
            with self.assertRaisesRegex(ConnectionError, 'Назначение VPN изменилось'):
                manager.get_working_proxy_for_slot(2, profile_key='a')
        self.assertFalse((manager._isolation_root / '.network-isolation.json').exists())

    def test_happ_without_nodes_can_only_be_claimed_by_one_profile(self):
        manager = self.isolation_manager()
        self.dispatcher.nodes = []
        with patch('proxy_manager.fetch_proxy_ip', side_effect=['8.8.8.8', '1.1.1.1']):
            self.assertEqual(manager.get_working_proxy_for_slot(0, profile_key='a'), manager.default_happ_proxy)
            with self.assertRaisesRegex(ConnectionError, 'VPN-сервер уже закреплён'):
                manager.get_working_proxy_for_slot(1, profile_key='b')

    def test_shared_exit_rejected_before_launcher_opens_browser(self):
        from secure_launcher import launch_request
        manager = self.isolation_manager()
        profiles = Mock()
        profiles.proxy_slot.side_effect = lambda key, slot: slot
        with patch.object(manager, 'ensure_core_running', return_value=True), patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'), patch('secure_launcher.find_chrome', return_value='chrome.exe'):
            launch_request(manager, profiles, {'key': 'a', 'slot': 0})
            with self.assertRaisesRegex(ConnectionError, 'Внешний IP уже закреплён'):
                launch_request(manager, profiles, {'key': 'b', 'slot': 1})
        self.assertEqual(profiles.launch.call_count, 1)
        self.assertEqual(profiles.launch.call_args.args[1], 'a')


if __name__ == '__main__':
    unittest.main()
