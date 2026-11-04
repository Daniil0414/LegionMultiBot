"""Verified VPN route selection shared by every browser launcher."""
import atexit
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.parse
import uuid
from proxy_network import NETWORK_LOG, fetch_proxy_ip
from vpn_dispatcher import VpnDispatcher, BASE_LOCAL_PORT, parse_raw_nodes, _node_identity
from vpn_runtime import atomic_json, runtime_lock, process_identity, process_is_alive
from vpn_failover import FailoverProxy, RouteSelectionChanged
from app_paths import profile_directory


class RouteConflictError(ConnectionError):
    pass


class ProxySelectionCancelled(RuntimeError):
    pass


def _node_endpoint(node):
    return (node.protocol, str(node.address).lower().rstrip('.'), int(node.port))


class ProxyEntry(str):
    def __new__(cls, clean, host, port, scheme, user=None, passw=None, raw=None):
        obj = super().__new__(cls, clean)
        obj.clean = obj.cli_arg = clean
        obj.host, obj.port, obj.scheme = host, port, scheme
        obj.user, obj.passw, obj.raw = user, passw, raw or clean
        return obj


def parse_proxy_entry(raw):
    if not raw or str(raw).strip().startswith(('#', '//', ';')):
        return None
    value = str(raw).strip().strip('\"\'')
    try:
        if '://' not in value:
            parts = value.split(':')
            if len(parts) == 4 and not value.startswith('['):
                host, port, user, password = parts
                scheme = 'socks5' if int(port) in (1080, 10808, 9050, 10811, 10812) else 'http'
                auth = urllib.parse.quote(user, safe='') + ':' + urllib.parse.quote(password, safe='')
                value = f'{scheme}://{auth}@{host}:{port}'
            else:
                value = 'socks5://' + value
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {'socks5', 'socks5h', 'http', 'https'} or not parsed.hostname:
            return None
        port = parsed.port or (1080 if parsed.scheme.startswith('socks') else 443 if parsed.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            return None
        host = parsed.hostname
        address = '[' + host + ']' if ':' in host else host
        auth = ''
        user = urllib.parse.unquote(parsed.username) if parsed.username else None
        password = urllib.parse.unquote(parsed.password) if parsed.password else None
        if user is not None:
            auth = urllib.parse.quote(user, safe='') + ':' + urllib.parse.quote(password or '', safe='') + '@'
        clean = f'{parsed.scheme}://{auth}{address}:{port}'
        return ProxyEntry(clean, host, port, parsed.scheme, user, password, str(raw))
    except (ValueError, TypeError):
        return None


class ProxyManager:
    def __init__(self, base_dir, start_monitor=True, isolation_root=None):
        self.base_dir = base_dir
        self.proxies_file = os.path.join(base_dir, 'proxies.txt')
        self.default_happ_proxy = 'socks5://127.0.0.1:10808'
        self.kill_switch = True
        self._lock = threading.RLock()
        self._route_ips = {}
        self._proxy_errors = {}
        self._selected_routes = {}
        self._failover_routes = {}
        self._failover_status = {}
        self._manager_token = uuid.uuid4().hex
        self._process_identity = process_identity(os.getpid())
        self._registered_owners = set()
        self._profile_processes = {}
        self._profile_launches = {}
        self._isolation_root = (Path(isolation_root) if isolation_root is not None
                                else profile_directory())
        self._closed = threading.Event()
        self.proxies = []
        self.vpn_dispatcher = VpnDispatcher()
        self._load_proxies()
        if not self.vpn_dispatcher.nodes:
            # Credentials are handled by Xray, never passed to Chrome.
            external = [p for p in self.proxies if p.clean != self.default_happ_proxy]
            if external:
                self.vpn_dispatcher.nodes = parse_raw_nodes('\n'.join(p.clean for p in external))
        if start_monitor:
            threading.Thread(target=self._monitor, daemon=True).start()
            atexit.register(self.close)

    def close(self):
        self._closed.set()
        with self._lock:
            gateways = list(getattr(self, '_failover_routes', {}).values())
            self._failover_routes = {}
        error = None
        for _, gateway in gateways:
            try:
                gateway.close()
            except Exception as exc:
                error = error or exc
        for owner in list(getattr(self, '_registered_owners', ())):
            try:
                self._release_route(owner)
            except Exception as exc:
                error = error or exc
        with self._lock:
            try:
                self.vpn_dispatcher.stop_xray()
            except Exception as exc:
                error = error or exc
        if error:
            raise error

    def _monitor(self):
        while not self._closed.is_set():
            try:
                self.release_closed_profiles()
                self.ensure_core_running()
            except Exception:
                pass
            self._closed.wait(4)

    def ensure_core_running(self):
        with self._lock:
            if self._closed.is_set():
                return False
            vd = self.vpn_dispatcher
            return not vd.nodes or vd.is_running or vd.start_xray()

    def _load_proxies(self):
        loaded, seen = [], set()
        if os.path.exists(self.proxies_file):
            try:
                with open(self.proxies_file, encoding='utf-8') as stream:
                    for line in stream:
                        entry = parse_proxy_entry(line)
                        if entry and entry.clean not in seen:
                            loaded.append(entry)
                            seen.add(entry.clean)
            except (OSError, UnicodeError):
                self.vpn_dispatcher.last_error = 'Не удалось прочитать proxies.txt'
        with self._lock:
            self.proxies = loaded or [parse_proxy_entry(self.default_happ_proxy)]

    def reload(self):
        self._load_proxies()

    def get_assigned_proxy_for_slot(self, slot_idx):
        proxy, _, _ = self.vpn_dispatcher.get_slot_info(slot_idx)
        return proxy or self.default_happ_proxy

    def failover_status(self, proxy):
        with self._lock:
            status = self._failover_status.get(proxy)
            return dict(status) if status is not None else None

    def get_resilient_proxy_for_slot(self, slot_idx, status_callback=None, profile_key=None, cancel_callback=None):
        """Keep Chrome's proxy address fixed while replacing its verified VPN exit."""
        if type(slot_idx) is not int or slot_idx < 0:
            raise ValueError('Invalid proxy slot index')
        if profile_key is not None and (not isinstance(profile_key, str) or
                                       not re.fullmatch(r'[A-Za-z0-9 _-]+', profile_key)):
            raise ValueError('Invalid account directory name')
        owner = f'profile:{profile_key.casefold()}' if isinstance(profile_key, str) else f'slot:{slot_idx}'
        vd = self.vpn_dispatcher
        signature = tuple(_node_identity(n) for n in vd.get_nodes_snapshot())
        with self._lock:
            existing = self._failover_routes.get(owner)
        if existing:
            if self._closed.is_set() or (cancel_callback and cancel_callback()):
                raise ProxySelectionCancelled('Запуск отменён')
            if existing[0] != signature:
                raise ConnectionError('Настройки VPN изменились; перезапустите программу перед повторным запуском.')
            return existing[1].proxy if existing[1].status['ip'] else 'ERROR_ALL_DOWN'
        primary = self.get_working_proxy_for_slot(slot_idx, status_callback, profile_key, cancel_callback)
        if primary == 'ERROR_ALL_DOWN':
            return primary
        failed_until, gateway_ref = {}, [None]

        def valid():
            with self._lock:
                gateway = gateway_ref[0]
                current = self._failover_routes.get(owner)
                return (not self._closed.is_set() and
                        (gateway is None or (current is not None and current[1] is gateway)) and
                        tuple(_node_identity(n) for n in vd.get_nodes_snapshot()) == signature)

        def endpoint_for(proxy):
            if not valid():
                raise ConnectionError('Назначение VPN изменилось; маршрут заблокирован.')
            port = urllib.parse.urlsplit(proxy).port
            nodes = vd.get_nodes_snapshot()
            if nodes:
                index = port - BASE_LOCAL_PORT
                if not 0 <= index < max(48, len(nodes)):
                    raise ConnectionError('Неизвестный маршрут VPN')
                node = nodes[index % len(nodes)]
                endpoint = _node_endpoint(node)
            else:
                endpoint = ('happ', self.default_happ_proxy)
            return endpoint

        def activate(proxy, ip):
            with self._lock:
                blocked = {item for item, until in failed_until.items() if until > time.monotonic()}
            gateway, rebalance = gateway_ref[0], False
            if gateway:
                with gateway._lock:
                    rebalance = gateway._active is None
                    blocked.update(item for item, until in gateway._failed_until.items() if until > time.monotonic())
            with self._lock, vd.lock:
                alternatives = None
                if rebalance:
                    blocked_routes = {self._route_key(endpoint_for(item)) for item in blocked}
                    endpoints = [_node_endpoint(node) for node in vd.get_nodes_snapshot()] or [('happ', self.default_happ_proxy)]
                    candidates = [(i, endpoint) for i, endpoint in enumerate(endpoints)
                                  if self._route_key(endpoint) not in blocked_routes]
                    if vd._health_revision == vd._nodes_revision:
                        healthy = [(i, endpoint) for i, endpoint in candidates if i in vd.alive_indices]
                        candidates = healthy or candidates
                    alternatives = [endpoint for _, endpoint in candidates]
                if not self._claim_route(owner, endpoint_for(proxy), ip, alternatives=alternatives):
                    raise RouteSelectionChanged('Загрузка VPN изменилась; выбирается менее занятый сервер.')
                self._route_ips[proxy] = ip

        def verify(proxy, reserve=True):
            endpoint = endpoint_for(proxy)
            # Validate registry integrity without reserving capacity for a probe.
            self._claim_route(owner, endpoint, reserve=False)
            ip = fetch_proxy_ip(proxy, timeout=4)
            with self._lock, vd.lock:
                if not valid():
                    raise ConnectionError('Назначение VPN изменилось; маршрут заблокирован.')
                self._claim_route(owner, endpoint, ip, reserve=reserve)
                if reserve:
                    self._route_ips[proxy] = ip
            return ip

        def choose_backup(excluded):
            if not valid():
                return None
            nodes = vd.get_nodes_snapshot()
            with vd.lock:
                healthy = set(vd.alive_indices) if vd._health_revision == vd._nodes_revision else None
            state = self._isolation_snapshot()
            excluded_routes = {self._route_key(endpoint_for(proxy)) for proxy in excluded}
            indices = list(range(len(nodes))) if nodes else [None]
            indices.sort(key=lambda i: (
                healthy is not None and i not in healthy,
                self._route_load(state, owner, _node_endpoint(nodes[i]) if i is not None else ('happ', self.default_happ_proxy)),
                i is not None and i < 48, i if i is not None else -1))
            # Continue through the pool on subsequent passes; never stall health
            # monitoring behind a long serial scan of unavailable candidates.
            attempts = 0
            for index in indices:
                proxy = self.default_happ_proxy if index is None else f'socks5://127.0.0.1:{BASE_LOCAL_PORT + index}'
                with self._lock:
                    cooling_down = failed_until.get(proxy, 0) > time.monotonic()
                if (self._route_key(endpoint_for(proxy)) in excluded_routes or
                        cooling_down):
                    continue
                try:
                    # Probing a standby must not permanently consume its server/IP.
                    # Activation claims it atomically before any account traffic.
                    return proxy, verify(proxy, reserve=False)
                except Exception:
                    with self._lock:
                        failed_until[proxy] = time.monotonic() + FailoverProxy.STANDBY_MAX_AGE
                    attempts += 1
                    if attempts >= 2:
                        break
            return None

        def changed(proxy, status):
            with self._lock:
                current = self._failover_routes.get(owner)
                if gateway_ref[0] is not None and (current is None or current[1] is not gateway_ref[0]):
                    return
                previous = self._failover_status.get(proxy, {}).get('proxy')
                self._failover_status[proxy] = dict(status)
                if status['ip']:
                    self._route_ips[proxy] = status['ip']
                    if signature:
                        index = (urllib.parse.urlsplit(status['proxy']).port - BASE_LOCAL_PORT) % len(signature)
                        self._selected_routes[owner] = (signature, index)
                else:
                    self._route_ips.pop(proxy, None)
                    self._release_route(owner)
            if previous != status['proxy']:
                NETWORK_LOG.info('VPN failover owner=%s gateway=%s upstream=%s previous=%s blocked=%s',
                                 owner, proxy, status['proxy'], previous, not bool(status['ip']))

        # Serialize creation for the same owner, but never hold the manager lock
        # while waiting for network checks or shutting down a gateway.
        with self._lock:
            existing = self._failover_routes.get(owner)
            if existing:
                return existing[1].proxy
            if not valid() or (cancel_callback and cancel_callback()):
                raise ProxySelectionCancelled('Запуск отменён')
            gateway = FailoverProxy(primary, self._route_ips[primary], verify, choose_backup, changed, valid,
                                    probe=lambda proxy: verify(proxy, reserve=False), activate=activate)
            gateway_ref[0] = gateway
            self._failover_routes[owner] = (signature, gateway)
            return gateway.proxy

    def check_proxy_alive(self, proxy_target, timeout=4):
        entry = parse_proxy_entry(proxy_target)
        if not entry:
            return False
        try:
            ip = fetch_proxy_ip(entry.clean, timeout=timeout)
            with self._lock:
                self._route_ips[entry.clean] = ip
                self._proxy_errors.pop(entry.clean, None)
            return True
        except Exception as exc:
            with self._lock:
                self._route_ips.pop(entry.clean, None)
                self._proxy_errors[entry.clean] = str(exc)
            return False

    @staticmethod
    def _public_ip(value):
        address = ipaddress.ip_address(value)
        address = getattr(address, 'ipv4_mapped', None) or address
        if not address.is_global:
            raise ValueError('A public VPN exit IP is required')
        return str(address)

    @staticmethod
    def _route_key(endpoint):
        return hashlib.sha256(json.dumps(endpoint, ensure_ascii=True).encode()).hexdigest()

    def _read_isolation_state(self):
        """Read only current usage; legacy server/IP ownership is never consulted."""
        registry = self._isolation_root / '.network-usage.json'
        try:
            state = json.loads(registry.read_text(encoding='utf-8'))
        except FileNotFoundError:
            state = {'version': 1, 'profiles': {}}
        except (OSError, ValueError) as exc:
            raise ConnectionError('Не удалось прочитать счётчик текущих подключений VPN.') from exc
        try:
            if (not isinstance(state, dict) or type(state.get('version')) is not int or
                    state['version'] != 1):
                raise ValueError('Invalid current VPN usage')
            profiles = state.get('profiles')
            if not isinstance(profiles, dict):
                raise ValueError('Invalid current VPN assignments')
            for owner, assignment in profiles.items():
                if (not isinstance(owner, str) or not re.fullmatch(r'(?:profile:[a-z0-9 _-]+|slot:[0-9]+)', owner) or
                        not isinstance(assignment, dict) or
                        not isinstance(assignment.get('route'), str) or not re.fullmatch(r'[a-f0-9]{64}', assignment['route']) or
                        not isinstance(assignment.get('manager'), str) or not re.fullmatch(r'[a-f0-9]{32}', assignment['manager'])):
                    raise ValueError('Invalid current VPN assignment')
                identity = assignment.get('process')
                if (not isinstance(identity, dict) or type(identity.get('pid')) is not int or identity['pid'] <= 0 or
                        not isinstance(identity.get('created'), str) or not identity['created'].isdigit() or
                        not isinstance(identity.get('executable'), str) or not identity['executable']):
                    raise ValueError('Invalid VPN usage process')
            identities = {}
            for owner, assignment in list(profiles.items()):
                identity = assignment['process']
                pid = identity['pid']
                if pid not in identities:
                    identities[pid] = process_identity(pid)
                actual = identities[pid]
                if ((actual is not None and actual != identity) or
                        (actual is None and not process_is_alive(pid))):
                    del profiles[owner]
        except (TypeError, ValueError) as exc:
            raise ConnectionError('Счётчик текущих подключений VPN повреждён.') from exc
        return state

    def _isolation_snapshot(self):
        with self._lock, runtime_lock(str(self._isolation_root / '.network-usage.lock')):
            return self._read_isolation_state()

    def _route_load(self, state, owner, endpoint):
        route = self._route_key(endpoint)
        return sum(key != owner and item['route'] == route for key, item in state['profiles'].items())

    def _claim_route(self, owner, endpoint, ip=None, *, reserve=True, alternatives=None):
        """Count one current route per profile; a shared server/IP is allowed."""
        if reserve or ip is not None:
            ip = self._public_ip(ip)
        route = self._route_key(endpoint)
        registry = self._isolation_root / '.network-usage.json'
        with self._lock, runtime_lock(str(self._isolation_root / '.network-usage.lock')):
            state = self._read_isolation_state()
            if not reserve:
                return True
            if alternatives is not None:
                load = self._route_load(state, owner, endpoint)
                if any(self._route_load(state, owner, candidate) < load for candidate in alternatives):
                    return False
            assignment = {'route': route, 'manager': self._manager_token, 'process': self._process_identity}
            if state['profiles'].get(owner) != assignment:
                state['profiles'][owner] = assignment
                atomic_json(str(registry), state)
            self._registered_owners.add(owner)
            return True

    def _release_route(self, owner):
        with self._lock, runtime_lock(str(self._isolation_root / '.network-usage.lock')):
            state = self._read_isolation_state()
            assignment = state['profiles'].get(owner)
            if assignment and assignment['manager'] == self._manager_token:
                del state['profiles'][owner]
                atomic_json(str(self._isolation_root / '.network-usage.json'), state)
            self._registered_owners.discard(owner)

    def _detach_profile(self, owner):
        """Caller holds the manager lock; callbacks from the old gateway are ignored."""
        existing = self._failover_routes.pop(owner, None)
        self._selected_routes.pop(owner, None)
        self._profile_processes.pop(owner, None)
        self._release_route(owner)
        if existing:
            self._failover_status.pop(existing[1].proxy, None)
        return existing[1] if existing else None

    def release_profile(self, profile_key):
        with self._lock:
            gateway = self._detach_profile(f'profile:{profile_key.casefold()}')
        if gateway:
            gateway.close()

    @staticmethod
    def _process_current(identity):
        if not identity:
            return False
        actual = process_identity(identity['pid'])
        return actual == identity or (actual is None and process_is_alive(identity['pid']))

    @contextmanager
    def profile_launch(self, profile_key):
        """Keep a browser restart from being mistaken for a closed profile."""
        self.release_closed_profiles()
        owner = f'profile:{profile_key.casefold()}'
        with self._lock:
            if self._closed.is_set():
                raise ProxySelectionCancelled('Запуск отменён')
            self._profile_launches[owner] = self._profile_launches.get(owner, 0) + 1
        try:
            yield
        finally:
            gateway = None
            with self._lock:
                count = self._profile_launches[owner] - 1
                if count:
                    self._profile_launches[owner] = count
                else:
                    del self._profile_launches[owner]
                    if not self._process_current(self._profile_processes.get(owner)):
                        gateway = self._detach_profile(owner)
            if gateway:
                gateway.close()

    def track_profile_process(self, profile_key, identity):
        owner = f'profile:{profile_key.casefold()}'
        with self._lock:
            self._profile_processes[owner] = dict(identity)

    def release_closed_profiles(self):
        with self._lock:
            processes = list(self._profile_processes.items())
        for owner, identity in processes:
            if self._process_current(identity):
                continue
            with self._lock:
                if self._profile_processes.get(owner) is not identity or self._profile_launches.get(owner):
                    continue
                gateway = self._detach_profile(owner)
            if gateway:
                gateway.close()

    def release_all_profiles(self):
        with self._lock:
            gateways, self._failover_routes = self._failover_routes, {}
            owners = list(self._registered_owners)
            self._selected_routes.clear()
            self._profile_processes.clear()
            self._failover_status.clear()
        error = None
        for _, gateway in gateways.values():
            try:
                gateway.close()
            except Exception as exc:
                error = error or exc
        for owner in owners:
            try:
                self._release_route(owner)
            except Exception as exc:
                error = error or exc
        if error:
            raise error

    def get_working_proxy_for_slot(self, slot_idx, status_callback=None, profile_key=None, cancel_callback=None):
        if type(slot_idx) is not int or slot_idx < 0:
            raise ValueError('Invalid proxy slot index')
        if profile_key is not None and (not isinstance(profile_key, str) or
                                       not re.fullmatch(r'[A-Za-z0-9 _-]+', profile_key)):
            raise ValueError('Invalid account directory name')
        if self._closed.is_set() or (cancel_callback and cancel_callback()):
            raise ProxySelectionCancelled('Запуск отменён')
        owner = f'profile:{profile_key.casefold()}' if profile_key is not None else f'slot:{slot_idx}'
        vd = self.vpn_dispatcher
        with vd.lock:
            nodes = vd.get_nodes_snapshot()
            primary = self.get_assigned_proxy_for_slot(slot_idx)
            signature = tuple(_node_identity(item) for item in nodes)
            if nodes:
                index = (urllib.parse.urlsplit(primary).port - BASE_LOCAL_PORT) % len(nodes)
                health = set(vd.alive_indices) if vd._health_revision == vd._nodes_revision else None
            else:
                index, health = None, None
        with self._lock:
            selected = self._selected_routes.get(owner)
        NETWORK_LOG.info('VPN selection slot=%s servers=%s healthy=%s', slot_idx + 1, len(nodes),
                         len(health) if health is not None else 'unchecked')
        indices = []
        if selected and selected[0] == signature:
            indices.append(selected[1])
        if nodes:
            indices += [index, *range(len(nodes))]
            indices = list(dict.fromkeys(indices))
            if health is not None:
                indices.sort(key=lambda candidate: candidate not in health)
        else:
            indices = [None]
        def endpoint_for(candidate):
            return _node_endpoint(nodes[candidate]) if candidate is not None else ('happ', self.default_happ_proxy)

        if nodes and not self.ensure_core_running():
            return 'ERROR_ALL_DOWN'
        remaining = list(indices)
        checked = {}
        attempts, last_error = 0, ''
        while remaining:
            if self._closed.is_set() or (cancel_callback and cancel_callback()):
                raise ProxySelectionCancelled('Запуск отменён')
            state = self._isolation_snapshot()
            assigned = state['profiles'].get(owner, {}).get('route')
            remaining.sort(key=lambda candidate: (
                health is not None and candidate not in health,
                self._route_load(state, owner, endpoint_for(candidate)),
                self._route_key(endpoint_for(candidate)) != assigned,
                candidate != index))
            candidate = remaining[0]
            endpoint = endpoint_for(candidate)
            load = self._route_load(state, owner, endpoint)
            proxy = primary if candidate is None or candidate == index else f'socks5://127.0.0.1:{BASE_LOCAL_PORT + candidate}'
            if candidate not in checked:
                attempts += 1
                if status_callback:
                    status_callback(f'Слот #{slot_idx+1}: проверка VPN {attempts}/{len(indices)}; '
                                    f'других профилей на сервере: {load}' +
                                    ('; пробуем другой сервер…' if attempts > 1 else '…'))
                if not self.check_proxy_alive(proxy, timeout=6):
                    with self._lock:
                        last_error = self._proxy_errors.get(proxy, 'HTTPS через прокси не прошёл.')
                    remaining.remove(candidate)
                    continue
                with self._lock:
                    checked[candidate] = self._route_ips.get(proxy)
            if cancel_callback and cancel_callback():
                raise ProxySelectionCancelled('Запуск отменён')
            # Recheck load under the cross-process lock after the network probe.
            # Concurrent launches must not all select the same formerly free node.
            with self._lock, vd.lock:
                if self._closed.is_set():
                    raise ProxySelectionCancelled('Запускатель закрывается')
                current = vd.get_nodes_snapshot()
                if tuple(_node_identity(item) for item in current) != signature:
                    raise ConnectionError('Назначение VPN изменилось во время проверки; повторите запуск.')
                alternatives = [endpoint_for(item) for item in remaining
                                if health is None or (item in health) == (candidate in health)]
                if not self._claim_route(owner, endpoint, checked[candidate], alternatives=alternatives):
                    continue
                if candidate is not None:
                    self._selected_routes[owner] = (signature, candidate)
                NETWORK_LOG.info('VPN selected slot=%s node=%s attempts=%s shared_profiles=%s fallback=%s',
                                 slot_idx + 1, candidate, attempts, load, proxy != primary)
                vd.last_error = ''
                return proxy
        vd.last_error = (f'Не найден рабочий VPN для слота #{slot_idx+1}; проверено серверов: {attempts}. {last_error}')
        return 'ERROR_ALL_DOWN'
