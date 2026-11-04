"""A loopback-only SOCKS gateway with verified VPN standby routes and no direct path."""
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import select
import socket
import socketserver
import threading
import time
import urllib.parse


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ConnectionError('SOCKS connection closed')
        data.extend(chunk)
    return bytes(data)


def read_address(stream, kind):
    if kind == 1:
        return read_exact(stream, 4)
    if kind == 4:
        return read_exact(stream, 16)
    if kind == 3:
        length = read_exact(stream, 1)
        if not length[0]:
            raise ValueError('Empty SOCKS hostname')
        return length + read_exact(stream, length[0])
    raise ValueError('Unsupported SOCKS address')


@dataclass(frozen=True)
class Route:
    proxy: str
    ip: str
    checked_at: float


class RouteSelectionChanged(ConnectionError):
    """A ready route needs fresh load balancing, rather than a network cooldown."""


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 64


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.gateway.handle(self.request)


class FailoverProxy:
    CHECK_INTERVAL = 6.0
    STANDBY_MAX_AGE = 45.0
    CONNECT_TIMEOUT = 2.0
    MAX_PROBE_FAILURES = 2

    def __init__(self, primary, ip, verify, choose_backup, on_change, valid=lambda: True, *, probe=None, activate=None):
        self._lock = threading.RLock()
        self._publish_lock = threading.Lock()
        self._closed = threading.Event()
        self._wake = threading.Event()
        self._verify = verify
        self._probe = probe or verify
        self._activate = activate or (lambda proxy, ip: None)
        self._choose_backup = choose_backup
        self._on_change = on_change
        self._valid = valid
        self._active = Route(primary, ip, time.monotonic())
        self._standby = None
        self._preparing = False
        self._failed_until = {}
        self._probe_failures = {}
        self._tunnels = {}
        self._server = _Server(('127.0.0.1', 0), _Handler)
        self._server.gateway = self
        self.proxy = f'socks5://127.0.0.1:{self._server.server_address[1]}'
        threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': .1}, daemon=True).start()
        self._publish()
        threading.Thread(target=self._monitor, daemon=True).start()

    @property
    def status(self):
        with self._lock:
            standby = self._standby
            return {'proxy': self._active.proxy if self._active else None,
                    'ip': self._active.ip if self._active else None,
                    'standby_ready': bool(standby and time.monotonic() - standby.checked_at <= self.STANDBY_MAX_AGE)}

    def _publish(self):
        with self._publish_lock:
            self._on_change(self.proxy, self.status)

    def _failed(self, route):
        with self._lock:
            self._probe_failures.pop(route.proxy, None)
            self._failed_until[route.proxy] = time.monotonic() + self.STANDBY_MAX_AGE
            changed = bool(self._active and self._active.proxy == route.proxy)
            if changed:
                standby = self._standby
                self._active = None
                self._standby = None
                if standby and time.monotonic() - standby.checked_at <= self.STANDBY_MAX_AGE:
                    try:
                        self._activate(standby.proxy, standby.ip)
                        self._active = standby
                    except RouteSelectionChanged:
                        pass
                    except Exception:
                        self._failed_until[standby.proxy] = time.monotonic() + self.STANDBY_MAX_AGE
            elif self._standby and self._standby.proxy == route.proxy:
                self._standby = None
            sockets = [pair for pair, upstream in self._tunnels.items() if upstream == route.proxy]
        for pair in sockets:
            self._disconnect(pair)
        if changed:
            self._publish()
        self._wake.set()

    @staticmethod
    def _disconnect(pair):
        for stream in pair:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()

    def _prepare(self):
        with self._lock:
            if self._preparing or self._standby or self._closed.is_set():
                return
            self._preparing = True
            excluded = {self._active.proxy} if self._active else set()
            excluded.update(proxy for proxy, until in self._failed_until.items() if until > time.monotonic())

        def work():
            changed = False
            try:
                result = self._choose_backup(excluded)
                if result:
                    route = Route(*result, time.monotonic())
                    with self._lock:
                        if self._closed.is_set():
                            return
                        if self._active is None:
                            self._activate(route.proxy, route.ip)
                            self._active, changed = route, True
                        elif self._active.proxy != route.proxy:
                            self._standby = route
                            changed = True
            except Exception:
                # No verified alternative means the gateway remains blocked.
                pass
            finally:
                with self._lock:
                    self._preparing = False
                if changed:
                    self._publish()
        threading.Thread(target=work, daemon=True).start()

    def _monitor(self):
        self._prepare()
        while not self._closed.is_set():
            self._wake.wait(self.CHECK_INTERVAL)
            self._wake.clear()
            if self._closed.is_set():
                break
            with self._lock:
                routes = [r for r in (self._active, self._standby) if r]
            def check(route):
                try:
                    ip = self._probe(route.proxy)
                    with self._lock:
                        self._probe_failures[route.proxy] = 0
                    refreshed = Route(route.proxy, ip, time.monotonic())
                    with self._lock:
                        if self._active == route:
                            self._activate(refreshed.proxy, refreshed.ip)
                            self._active = refreshed
                        elif self._standby == route:
                            self._standby = refreshed
                    self._publish()
                except Exception:
                    with self._lock:
                        fails = self._probe_failures.get(route.proxy, 0) + 1
                        self._probe_failures[route.proxy] = fails
                        should_fail = fails >= getattr(self, 'MAX_PROBE_FAILURES', 2)
                        if should_fail:
                            self._probe_failures[route.proxy] = 0
                    if should_fail:
                        self._failed(route)
            with ThreadPoolExecutor(max_workers=2) as pool:
                for result in as_completed([pool.submit(check, route) for route in routes]):
                    result.result()
            self._prepare()

    def handle(self, client):
        upstream = None
        established = False
        try:
            client.settimeout(5)
            version, count = read_exact(client, 2)
            if version != 5 or not count:
                return
            if 0 not in read_exact(client, count):
                client.sendall(b'\x05\xff')
                return
            client.sendall(b'\x05\x00')
            header = read_exact(client, 4)
            request = header + read_address(client, header[3]) + read_exact(client, 2)
            if header[:3] != b'\x05\x01\x00':  # CONNECT only: UDP and BIND never get an exit.
                client.sendall(b'\x05\x07\x00\x01' + b'\x00' * 6)
                return
            for attempt in range(2):
                with self._lock:
                    route = self._active
                if not route or self._closed.is_set() or not self._valid():
                    break
                try:
                    parsed = urllib.parse.urlsplit(route.proxy)
                    # Only connect to numeric loopback Xray SOCKS inbounds. The
                    # requested destination is forwarded verbatim, never resolved here.
                    if parsed.scheme not in ('socks5', 'socks5h') or parsed.hostname != '127.0.0.1' or not parsed.port:
                        raise ValueError('A numeric loopback SOCKS upstream is required')
                    upstream = socket.create_connection(('127.0.0.1', parsed.port), self.CONNECT_TIMEOUT)
                    upstream.sendall(b'\x05\x01\x00')
                    if read_exact(upstream, 2) != b'\x05\x00':
                        raise ConnectionError('SOCKS upstream rejected authentication')
                    upstream.sendall(request)
                    reply = read_exact(upstream, 4)
                    if reply[0] != 5 or reply[2] != 0:
                        raise ConnectionError('Invalid SOCKS upstream reply')
                    reply += read_address(upstream, reply[3]) + read_exact(upstream, 2)
                    if reply[1] == 1:
                        raise ConnectionError('SOCKS upstream unavailable')
                    if reply[1] != 0:
                        established = True
                        client.sendall(reply)  # A destination rejection is not a VPN outage.
                        return
                    with self._lock:
                        if not self._active or self._active.proxy != route.proxy or self._closed.is_set() or not self._valid():
                            upstream.close()
                            upstream = None
                            continue
                        self._tunnels[(client, upstream)] = route.proxy
                    established = True
                    client.sendall(reply)
                    client.settimeout(None)
                    upstream.settimeout(None)
                    while not self._closed.is_set():
                        readable, _, _ = select.select([client, upstream], [], [], 1)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is client else client).sendall(data)
                    return
                except (OSError, ValueError):
                    if upstream:
                        upstream.close()
                        upstream = None
                    # Never replay application bytes from an established tunnel.
                    if established:
                        self._wake.set()
                        return
                    self._failed(route)
            self._wake.set()
            client.sendall(b'\x05\x01\x00\x01' + b'\x00' * 6)
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                for pair in list(self._tunnels):
                    if pair[0] is client:
                        self._tunnels.pop(pair, None)
            self._disconnect([s for s in (client, upstream) if s is not None])

    def close(self):
        self._closed.set()
        self._wake.set()
        with self._lock:
            self._probe_failures.clear()
            self._active = self._standby = None
            pairs = list(self._tunnels)
        for pair in pairs:
            self._disconnect(pair)
        self._publish()
        self._server.shutdown()
        self._server.server_close()
