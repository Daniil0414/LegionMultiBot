"""Verified VPN route selection shared by every browser launcher."""
import atexit
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import urllib.parse
from proxy_network import NETWORK_LOG, fetch_proxy_ip
from vpn_dispatcher import VpnDispatcher, BASE_LOCAL_PORT, parse_raw_nodes, _node_identity
from vpn_runtime import atomic_json, runtime_lock
from vpn_failover import FailoverProxy


class RouteConflictError(ConnectionError):
    pass


class ProxySelectionCancelled(RuntimeError):
    pass


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
        local = Path(os.environ.get('LOCALAPPDATA', ''))
        self._isolation_root = (Path(isolation_root) if isolation_root is not None
                                else local / 'LegionMultiBot' / 'Bots')
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

        def valid():
            return (not self._closed.is_set() and
                    tuple(_node_identity(n) for n in vd.get_nodes_snapshot()) == signature)

        def verify(proxy):
            if not valid():
                raise ConnectionError('Назначение VPN изменилось; маршрут заблокирован.')
            port = urllib.parse.urlsplit(proxy).port
            nodes = vd.get_nodes_snapshot()
            if nodes:
                index = port - BASE_LOCAL_PORT
                if not 0 <= index < max(48, len(nodes)):
                    raise ConnectionError('Неизвестный маршрут VPN')
                node = nodes[index % len(nodes)]
                endpoint = (node.protocol, str(node.address).lower().rstrip('.'), int(node.port))
            else:
                endpoint = ('happ', self.default_happ_proxy)
            ip = fetch_proxy_ip(proxy, timeout=2)
            with self._lock, vd.lock:
                if not valid():
                    raise ConnectionError('Назначение VPN изменилось; маршрут заблокирован.')
                self._claim_route(owner, endpoint, ip)
                self._route_ips[proxy] = ip
            return ip

        cursor = [0]

        def choose_backup(excluded):
            if not valid():
                return None
            nodes = vd.get_nodes_snapshot()
            with vd.lock:
                healthy = set(vd.alive_indices) if vd._health_revision == vd._nodes_revision else None
            indices = sorted(range(len(nodes)), key=lambda i: (healthy is not None and i not in healthy, i < 48, i))
            if not nodes:
                indices = [None]
            # Continue through the pool on subsequent passes; never stall health
            # monitoring behind a long serial scan of unavailable candidates.
            attempts = 0
            for _ in indices:
                index = indices[cursor[0] % len(indices)]
                cursor[0] += 1
                proxy = self.default_happ_proxy if index is None else f'socks5://127.0.0.1:{BASE_LOCAL_PORT + index}'
                if proxy in excluded:
                    continue
                try:
                    return proxy, verify(proxy)
                except Exception:
                    attempts += 1
                    if attempts >= 2:
                        break
            return None

        def changed(proxy, status):
            with self._lock:
                previous = self._failover_status.get(proxy, {}).get('proxy')
                self._failover_status[proxy] = dict(status)
                if status['ip']:
                    self._route_ips[proxy] = status['ip']
                    if signature:
                        index = (urllib.parse.urlsplit(status['proxy']).port - BASE_LOCAL_PORT) % len(signature)
                        self._selected_routes[owner] = (signature, index)
                else:
                    self._route_ips.pop(proxy, None)
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
            gateway = FailoverProxy(primary, self._route_ips[primary], verify, choose_backup, changed, valid)
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

    def _claim_route(self, owner, endpoint, ip):
        """Retain observed ownership, including closed profiles and old addresses."""
        ip = self._public_ip(ip)
        route = hashlib.sha256(json.dumps(endpoint, ensure_ascii=True).encode()).hexdigest()
        registry = self._isolation_root / '.network-isolation.json'
        with self._lock, runtime_lock(str(self._isolation_root / '.network-isolation.lock')):
            try:
                state = json.loads(registry.read_text(encoding='utf-8'))
            except FileNotFoundError:
                state = {'version': 1, 'routes': {}, 'ips': {}}
            except (OSError, ValueError) as exc:
                raise ConnectionError('Не удалось прочитать историю изоляции VPN; запуск заблокирован.') from exc
            try:
                if (not isinstance(state, dict) or type(state.get('version')) is not int or
                        state['version'] != 1 or not isinstance(state.get('routes'), dict) or
                        not isinstance(state.get('ips'), dict)):
                    raise ValueError('Invalid network isolation registry')
                for kind in ('routes', 'ips'):
                    for value, saved_owner in state[kind].items():
                        if not isinstance(saved_owner, str) or not re.fullmatch(r'(?:profile:[a-z0-9 _-]+|slot:[0-9]+)', saved_owner):
                            raise ValueError('Invalid network isolation owner')
                        if kind == 'routes' and not re.fullmatch(r'[a-f0-9]{64}', value):
                            raise ValueError('Invalid network isolation route')
                        if kind == 'ips' and self._public_ip(value) != value:
                            raise ValueError('Noncanonical network isolation IP')
            except (TypeError, ValueError) as exc:
                raise ConnectionError('История изоляции VPN повреждена; восстановите резервную копию.') from exc
            for kind, value in (('routes', route), ('ips', ip)):
                previous = state[kind].get(value)
                if previous is not None and previous != owner:
                    reason = 'VPN-сервер' if kind == 'routes' else 'Внешний IP'
                    raise RouteConflictError(f'{reason} уже закреплён за другим профилем. Назначьте отдельный VPN-сервер.')
            if state['routes'].get(route) != owner or state['ips'].get(ip) != owner:
                state['routes'][route] = owner
                state['ips'][ip] = owner
                atomic_json(str(registry), state)

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
        if nodes and not self.ensure_core_running():
            return 'ERROR_ALL_DOWN'
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
        first_conflict, last_error = None, ''
        for attempt, candidate in enumerate(indices, 1):
            if cancel_callback and cancel_callback():
                raise ProxySelectionCancelled('Запуск отменён')
            if self._closed.is_set():
                raise ProxySelectionCancelled('Запускатель закрывается')
            if candidate is None:
                proxy, endpoint = primary, ('happ', self.default_happ_proxy)
            else:
                node = nodes[candidate]
                proxy = primary if candidate == index else f'socks5://127.0.0.1:{BASE_LOCAL_PORT + candidate}'
                endpoint = (node.protocol, str(node.address).lower().rstrip('.'), int(node.port))
            if status_callback:
                status_callback(f'Слот #{slot_idx+1}: проверка VPN {attempt}/{len(indices)}' +
                                ('; пробуем другой сервер…' if attempt > 1 else '…'))
            if not self.check_proxy_alive(proxy, timeout=6):
                with self._lock:
                    last_error = self._proxy_errors.get(proxy, 'HTTPS через прокси не прошёл.')
                continue
            if cancel_callback and cancel_callback():
                raise ProxySelectionCancelled('Запуск отменён')
            # One lock order everywhere: manager -> dispatcher -> isolation file.
            with self._lock, vd.lock:
                current = vd.get_nodes_snapshot()
                if tuple(_node_identity(item) for item in current) != signature:
                    raise ConnectionError('Назначение VPN изменилось во время проверки; повторите запуск.')
                try:
                    self._claim_route(owner, endpoint, self._route_ips.get(proxy))
                except RouteConflictError as exc:
                    first_conflict = first_conflict or exc
                    continue
                if candidate is not None:
                    self._selected_routes[owner] = (signature, candidate)
                NETWORK_LOG.info('VPN selected slot=%s node=%s attempts=%s fallback=%s',
                                 slot_idx + 1, candidate, attempt, proxy != primary)
                vd.last_error = ''
                return proxy
        if first_conflict:
            raise first_conflict
        vd.last_error = f'Не найден рабочий VPN для слота #{slot_idx+1}; проверено серверов: {len(indices)}. {last_error}'
        return 'ERROR_ALL_DOWN'
