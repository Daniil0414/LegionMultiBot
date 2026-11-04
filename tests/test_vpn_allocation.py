"""Allocate current profile routes, including exhausted pools and old histories."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import vpn_dispatcher as vpn
from proxy_manager import ProxyManager, ProxySelectionCancelled, _node_endpoint
from vpn_failover import FailoverProxy, Route


class AllocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='legion-allocation-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dispatcher = Mock()
        self.dispatcher.lock = threading.RLock()
        self.dispatcher.nodes = vpn.parse_raw_nodes('socks5://a.invalid:1080\nsocks5://b.invalid:1080')
        self.dispatcher.get_nodes_snapshot.side_effect = lambda: list(self.dispatcher.nodes)
        self.dispatcher.get_slot_info.side_effect = lambda i: (f'socks5://127.0.0.1:{10810+i}', '', '')
        self.dispatcher._health_revision, self.dispatcher._nodes_revision = -1, 0
        self.dispatcher.is_running = True
        self.factory = patch('proxy_manager.VpnDispatcher', return_value=self.dispatcher)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.core = patch.object(ProxyManager, 'ensure_core_running', return_value=True)
        self.core.start()
        self.addCleanup(self.core.stop)
        self.monitor = patch.object(FailoverProxy, '_monitor')
        self.monitor.start()
        self.addCleanup(self.monitor.stop)
        self.manager = self.make_manager()

    def make_manager(self):
        manager = ProxyManager(self.root, start_monitor=False, isolation_root=self.root / 'profiles')
        self.addCleanup(manager.close)
        return manager

    def claim(self, owner, index):
        self.manager._claim_route(f'profile:{owner}', _node_endpoint(self.dispatcher.nodes[index]), '8.8.8.8')

    def test_six_accounts_share_two_servers_evenly(self):
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            routes = [self.manager.get_working_proxy_for_slot(0, profile_key=f'account{i}') for i in range(6)]
        self.assertEqual(routes.count('socks5://127.0.0.1:10810'), 3)
        self.assertEqual(routes.count('socks5://127.0.0.1:10811'), 3)
        self.assertEqual(len(self.manager._isolation_snapshot()['profiles']), 6)

    def test_free_server_is_preferred_to_the_busy_primary(self):
        self.claim('first', 0)
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8') as fetch:
            self.assertEqual(self.manager.get_working_proxy_for_slot(0, profile_key='second'), 'socks5://127.0.0.1:10811')
        fetch.assert_called_once_with('socks5://127.0.0.1:10811', timeout=6)

    def test_busy_working_server_is_reused_when_free_server_is_down(self):
        self.claim('first', 0)
        with patch('proxy_manager.fetch_proxy_ip', side_effect=[ConnectionError('fixture down'), '8.8.8.8']) as fetch:
            self.assertEqual(self.manager.get_working_proxy_for_slot(0, profile_key='second'), 'socks5://127.0.0.1:10810')
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         ['socks5://127.0.0.1:10811', 'socks5://127.0.0.1:10810'])

    def test_least_used_server_wins_when_every_server_is_busy(self):
        self.claim('one', 0)
        self.claim('two', 0)
        self.claim('three', 1)
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.assertEqual(self.manager.get_working_proxy_for_slot(0, profile_key='sixth'), 'socks5://127.0.0.1:10811')

    def test_checks_do_not_add_profiles_and_switching_moves_the_single_assignment(self):
        self.claim('one', 0)
        for _ in range(5):
            self.claim('one', 0)
            self.manager._claim_route('profile:one', _node_endpoint(self.dispatcher.nodes[1]), '1.1.1.1', reserve=False)
        state = self.manager._isolation_snapshot()
        self.assertEqual(len(state['profiles']), 1)
        self.assertEqual(self.manager._route_load(state, 'profile:other', _node_endpoint(self.dispatcher.nodes[0])), 1)
        self.claim('one', 1)
        state = self.manager._isolation_snapshot()
        self.assertEqual(len(state['profiles']), 1)
        self.assertEqual(self.manager._route_load(state, 'profile:other', _node_endpoint(self.dispatcher.nodes[0])), 0)

    def test_81_historical_reservations_are_not_current_load(self):
        self.dispatcher.nodes = vpn.parse_raw_nodes('\n'.join(f'socks5://node{i}.invalid:1080' for i in range(96)))
        historical = {'version': 1, 'routes': {}, 'ips': {'8.8.8.8': 'profile:old'}}
        for node in self.dispatcher.nodes[:81]:
            historical['routes'][self.manager._route_key(_node_endpoint(node))] = 'profile:old'
        registry = self.manager._isolation_root / '.network-isolation.json'
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps(historical))
        progress = []
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8') as fetch:
            self.assertEqual(self.manager.get_working_proxy_for_slot(5, profile_key='sixth', status_callback=progress.append),
                             'socks5://127.0.0.1:10815')
        fetch.assert_called_once()
        self.assertIn('других профилей на сервере: 0', progress[0])
        self.assertNotIn('81', progress[0])
        state = self.manager._isolation_snapshot()
        self.assertEqual(len(state['profiles']), 1)
        self.assertNotIn('routes', state)
        self.assertNotIn('ips', state)
        self.assertEqual(json.loads(registry.read_text()), historical)

    def test_simultaneous_managers_recheck_load_after_probing(self):
        managers = [self.make_manager() for _ in range(6)]
        barrier, local = threading.Barrier(6), threading.local()

        def fetch(proxy, timeout):
            if not getattr(local, 'started', False):
                local.started = True
                barrier.wait(timeout=5)
            return '8.8.8.8'

        with patch('proxy_manager.fetch_proxy_ip', side_effect=fetch):
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                routes = list(pool.map(lambda i: managers[i].get_working_proxy_for_slot(0, profile_key=f'account{i}'), range(6)))
        self.assertEqual(routes.count('socks5://127.0.0.1:10810'), 3)
        self.assertEqual(routes.count('socks5://127.0.0.1:10811'), 3)

    def test_failover_callback_reuses_a_busy_server_and_transfers_load(self):
        self.claim('other', 1)
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.manager.get_resilient_proxy_for_slot(0, profile_key='playing')
            gateway = self.manager._failover_routes['profile:playing'][1]
            standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
            self.assertEqual(standby[0], 'socks5://127.0.0.1:10811')
            gateway._standby = Route(*standby, time.monotonic())
            gateway._failed(gateway._active)
        self.assertEqual(gateway.status['proxy'], standby[0])
        state = self.manager._isolation_snapshot()
        self.assertEqual(self.manager._route_load(state, 'profile:observer', _node_endpoint(self.dispatcher.nodes[1])), 2)
        self.assertEqual(self.manager._route_load(state, 'profile:observer', _node_endpoint(self.dispatcher.nodes[0])), 0)

    def test_closed_manager_releases_current_load_without_writing_history(self):
        self.claim('one', 0)
        self.manager.close()
        state = self.manager._isolation_snapshot()
        self.assertEqual(state['profiles'], {})
        self.assertFalse((self.manager._isolation_root / '.network-isolation.json').exists())

    def test_dead_process_assignments_are_not_counted_as_busy(self):
        self.claim('gone', 0)
        registry = self.manager._isolation_root / '.network-usage.json'
        state = json.loads(registry.read_text())
        state['profiles']['profile:gone']['process']['created'] = '0'
        registry.write_text(json.dumps(state))
        self.assertEqual(self.manager._isolation_snapshot()['profiles'], {})

    def test_old_manager_cannot_release_a_route_reassigned_by_another_instance(self):
        self.claim('same', 0)
        other = self.make_manager()
        other._claim_route('profile:same', _node_endpoint(self.dispatcher.nodes[1]), '8.8.8.8')
        self.manager.close()
        self.assertEqual(len(other._isolation_snapshot()['profiles']), 1)

    def test_release_profile_and_close_all_free_load_and_allow_restart(self):
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.manager.get_resilient_proxy_for_slot(0, profile_key='one')
            self.manager.get_resilient_proxy_for_slot(0, profile_key='two')
            self.manager.release_profile('one')
            self.assertEqual(set(self.manager._isolation_snapshot()['profiles']), {'profile:two'})
            self.manager.release_all_profiles()
            self.assertEqual(self.manager._isolation_snapshot()['profiles'], {})
            self.manager.get_resilient_proxy_for_slot(0, profile_key='one')
        self.assertEqual(len(self.manager._isolation_snapshot()['profiles']), 1)

    def test_standby_ignores_an_alias_of_the_current_server(self):
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.manager.get_resilient_proxy_for_slot(5, profile_key='sixth')
            gateway = self.manager._failover_routes['profile:sixth'][1]
            standby = gateway._choose_backup({'socks5://127.0.0.1:10815'})
        self.assertEqual(standby[0], 'socks5://127.0.0.1:10810')

    def test_stale_standby_callback_reselects_a_freer_server(self):
        self.dispatcher.nodes += vpn.parse_raw_nodes('socks5://c.invalid:1080')
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.manager.get_resilient_proxy_for_slot(0, profile_key='playing')
            gateway = self.manager._failover_routes['profile:playing'][1]
            standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
            self.assertEqual(standby[0], 'socks5://127.0.0.1:10811')
            self.claim('other', 1)
            gateway._standby = Route(*standby, time.monotonic())
            gateway._failed(gateway._active)
            self.assertIsNone(gateway.status['proxy'])
            self.assertNotIn(standby[0], gateway._failed_until)
            replacement = gateway._choose_backup({'socks5://127.0.0.1:10810'})
        self.assertEqual(replacement[0], 'socks5://127.0.0.1:10812')

    def test_failed_free_backups_do_not_hide_a_working_shared_server(self):
        self.dispatcher.nodes += vpn.parse_raw_nodes('socks5://c.invalid:1080')
        self.claim('other', 1)
        def fetch(proxy, timeout):
            if proxy.endswith('10812'):
                raise ConnectionError('fixture free server down')
            return '8.8.8.8'
        with patch('proxy_manager.fetch_proxy_ip', side_effect=fetch):
            self.manager.get_resilient_proxy_for_slot(0, profile_key='playing')
            gateway = self.manager._failover_routes['profile:playing'][1]
            standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
            gateway._standby = Route(*standby, time.monotonic())
            gateway._failed(gateway._active)
        self.assertEqual(gateway.status['proxy'], 'socks5://127.0.0.1:10811')

    def test_close_during_a_probe_cannot_register_a_late_assignment(self):
        def fetch(proxy, timeout):
            self.manager.close()
            return '8.8.8.8'
        with patch('proxy_manager.fetch_proxy_ip', side_effect=fetch):
            with self.assertRaises(ProxySelectionCancelled):
                self.manager.get_working_proxy_for_slot(0, profile_key='late')
        self.assertEqual(self.manager._isolation_snapshot()['profiles'], {})

    def test_login_and_game_reuse_one_profile_assignment(self):
        from app_container import HighQualityMultiBoxerApp
        launcher = Mock()
        launcher.use_vpn = True
        launcher._closing_browsers = False
        launcher.proxy_manager = self.manager
        launcher.browser_profiles.generation = 0
        launcher.browser_profiles.proxy_slot.return_value = 0
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            login = HighQualityMultiBoxerApp.get_launch_proxy(launcher, 'account', 0)
            playing = HighQualityMultiBoxerApp.get_launch_proxy(launcher, 'account', 0)
        self.assertEqual(login, playing)
        self.assertEqual(len(self.manager._isolation_snapshot()['profiles']), 1)

    def test_standby_chooses_the_less_used_of_two_busy_servers(self):
        self.dispatcher.nodes += vpn.parse_raw_nodes('socks5://c.invalid:1080')
        self.claim('b1', 1)
        self.claim('c1', 2)
        self.claim('c2', 2)
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.manager.get_resilient_proxy_for_slot(0, profile_key='playing')
            gateway = self.manager._failover_routes['profile:playing'][1]
            standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
        self.assertEqual(standby[0], 'socks5://127.0.0.1:10811')

    def test_broken_legacy_ownership_is_ignored_and_never_rewritten(self):
        legacy = self.manager._isolation_root / '.network-isolation.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('broken old ownership data')
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            self.manager.get_working_proxy_for_slot(0, profile_key='new')
        self.assertEqual(legacy.read_text(), 'broken old ownership data')
        state = self.manager._isolation_snapshot()
        self.assertEqual(set(state), {'version', 'profiles'})
        self.assertEqual(set(state['profiles']['profile:new']), {'route', 'manager', 'process'})

    def test_closed_browser_releases_gateway_and_reopening_creates_a_usable_one(self):
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            with self.manager.profile_launch('one'):
                self.manager.get_resilient_proxy_for_slot(0, profile_key='one')
                self.manager.track_profile_process('one', self.manager._process_identity)
            old = self.manager._failover_routes['profile:one'][1]
            self.manager.release_closed_profiles()
            self.assertIn('profile:one', self.manager._isolation_snapshot()['profiles'])
            with patch.object(self.manager, '_process_current', return_value=False):
                self.manager.release_closed_profiles()
            self.assertTrue(old._closed.is_set())
            self.assertEqual(self.manager._isolation_snapshot()['profiles'], {})
            with self.manager.profile_launch('one'):
                self.manager.get_resilient_proxy_for_slot(0, profile_key='one')
                self.manager.track_profile_process('one', self.manager._process_identity)
            current = self.manager._failover_routes['profile:one'][1]
            self.assertIsNot(current, old)
            old._on_change(old.proxy, old.status)
            self.assertIn('profile:one', self.manager._isolation_snapshot()['profiles'])
            self.assertTrue(current._valid())
            with self.assertRaises(ConnectionError):
                old._verify('socks5://127.0.0.1:10810')

    def test_failed_browser_launch_releases_usage_and_gateway(self):
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            with self.assertRaisesRegex(RuntimeError, 'fixture browser failure'):
                with self.manager.profile_launch('one'):
                    self.manager.get_resilient_proxy_for_slot(0, profile_key='one')
                    gateway = self.manager._failover_routes['profile:one'][1]
                    raise RuntimeError('fixture browser failure')
        self.assertTrue(gateway._closed.is_set())
        self.assertEqual(self.manager._isolation_snapshot()['profiles'], {})
        self.assertEqual(self.manager._profile_launches, {})

    def test_browser_restart_is_not_reaped_during_launch(self):
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8'):
            with self.manager.profile_launch('one'):
                self.manager.get_resilient_proxy_for_slot(0, profile_key='one')
                self.manager.track_profile_process('one', self.manager._process_identity)
            gateway = self.manager._failover_routes['profile:one'][1]
            with self.manager.profile_launch('one'):
                with patch.object(self.manager, '_process_current', return_value=False):
                    self.manager.release_closed_profiles()
                self.assertFalse(gateway._closed.is_set())
                self.manager.track_profile_process('one', self.manager._process_identity)
        self.assertEqual(len(self.manager._isolation_snapshot()['profiles']), 1)
        self.assertEqual(self.manager._profile_launches, {})

    def test_known_working_shared_nodes_precede_failed_free_nodes(self):
        self.dispatcher.nodes += vpn.parse_raw_nodes('socks5://failed.invalid:1080')
        self.dispatcher.alive_indices = {0, 1}
        self.dispatcher._health_revision = self.dispatcher._nodes_revision
        self.claim('busy0', 0)
        self.claim('busy1', 1)
        progress = []
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8') as fetch:
            selected = self.manager.get_working_proxy_for_slot(0, profile_key='third', status_callback=progress.append)
        self.assertEqual(selected, 'socks5://127.0.0.1:10810')
        fetch.assert_called_once_with(selected, timeout=6)
        self.assertIn('проверка VPN 1/3', progress[0])

    def test_callback_does_not_stall_on_a_known_failed_free_node(self):
        self.dispatcher.nodes += vpn.parse_raw_nodes('socks5://failed.invalid:1080')
        self.dispatcher.alive_indices = {0, 1}
        self.dispatcher._health_revision = self.dispatcher._nodes_revision
        self.claim('busy1', 1)
        with patch('proxy_manager.fetch_proxy_ip', return_value='8.8.8.8') as fetch:
            self.manager.get_resilient_proxy_for_slot(0, profile_key='playing')
            gateway = self.manager._failover_routes['profile:playing'][1]
            standby = gateway._choose_backup({'socks5://127.0.0.1:10810'})
            self.assertEqual(standby[0], 'socks5://127.0.0.1:10811')
            gateway._standby = Route(*standby, time.monotonic())
            gateway._failed(gateway._active)
        self.assertEqual(gateway.status['proxy'], 'socks5://127.0.0.1:10811')
        self.assertTrue(all(not call.args[0].endswith('10812') for call in fetch.call_args_list))
        self.assertEqual(len(self.manager._isolation_snapshot()['profiles']), 2)

    def test_failed_health_cache_does_not_hide_a_recovered_server(self):
        self.dispatcher.alive_indices = {0}
        self.dispatcher._health_revision = self.dispatcher._nodes_revision
        def fetch(proxy, timeout):
            if proxy.endswith('10810'):
                raise ConnectionError('fixture known server failed')
            return '8.8.8.8'
        with patch('proxy_manager.fetch_proxy_ip', side_effect=fetch):
            self.assertEqual(self.manager.get_working_proxy_for_slot(0, profile_key='fallback'),
                             'socks5://127.0.0.1:10811')
