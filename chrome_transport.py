"""Local CDP WebSocket transport with verified Windows process ownership."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit

from browser_lifecycle import BrowserJob, resume_browser

MAX_MESSAGE = 16 * 1024 * 1024


def listener_owner(port):
    """Return the IPv4 listener's owning PID and address; do not trust HTTP alone."""
    api = ctypes.WinDLL('iphlpapi', use_last_error=True)
    api.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                       wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.GetExtendedTcpTable.restype = wintypes.DWORD
    class Row(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in
                    ('state', 'address', 'port', 'remote_address', 'remote_port', 'pid')]
    size = wintypes.DWORD()
    for _ in range(4):
        buffer = ctypes.create_string_buffer(max(size.value, 4))
        code = api.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 3, 0)
        if code == 122:  # ERROR_INSUFFICIENT_BUFFER: table may grow between calls.
            continue
        if code:
            raise OSError(code, 'Unable to verify Chrome listener ownership')
        count = wintypes.DWORD.from_buffer(buffer).value
        if 4 + count * ctypes.sizeof(Row) > len(buffer):
            raise OSError('Invalid Windows TCP table')
        found = []
        for i in range(count):
            row = Row.from_buffer(buffer, 4 + i * ctypes.sizeof(Row))
            if socket.ntohs(row.port & 0xffff) == port:
                found.append((row.pid, socket.inet_ntoa(struct.pack('=I', row.address))))
        if not found:
            return None
        if len(found) != 1:
            raise OSError('Ambiguous Chrome listener ownership')
        return found[0]
    raise OSError('Windows TCP listener table kept changing')


def verify_listener(process, port):
    owner = listener_owner(port)
    if owner is None:
        return False
    if process.poll() is not None or owner != (process.pid, '127.0.0.1'):
        raise OSError('Chrome control endpoint is not owned by the launched browser')
    return True


class WebSocketStream:
    """Adapt bounded RFC 6455 text messages to the existing NUL-framed CDP reader."""
    def __init__(self, connection, reader):
        self.socket, self.reader = connection, reader
        self._write_lock = threading.Lock()
        self._output = bytearray()
        self._fragments = bytearray()
        self._fragmenting = False
        self._closed = threading.Event()

    @classmethod
    def connect(cls, url, port):
        target = urlsplit(url)
        if (target.scheme != 'ws' or target.hostname != '127.0.0.1' or target.port != port
                or target.username or target.password or target.query or target.fragment
                or not re.fullmatch(r'/devtools/browser/[0-9a-fA-F-]+', target.path)):
            raise OSError('Unexpected Chrome control endpoint URL')
        connection = socket.create_connection(('127.0.0.1', port), timeout=3)
        reader = connection.makefile('rb')
        try:
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            request = (f'GET {target.path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                       'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                       f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
            # No Origin is sent by this native client; Chrome rejects web origins
            # by default. Do not add a wildcard --remote-allow-origins switch.
            connection.sendall(request.encode('ascii'))
            if reader.readline(4096).split()[:2] != [b'HTTP/1.1', b'101']:
                raise OSError('Chrome WebSocket upgrade was rejected')
            headers, total = {}, 0
            while True:
                line = reader.readline(4096)
                total += len(line)
                if not line or total > 16384:
                    raise OSError('Invalid Chrome WebSocket headers')
                if line == b'\r\n':
                    break
                name, separator, value = line.partition(b':')
                if not separator:
                    raise OSError('Invalid Chrome WebSocket header')
                headers[name.lower()] = value.strip()
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest())
            if (headers.get(b'sec-websocket-accept') != accept
                    or headers.get(b'upgrade', b'').lower() != b'websocket'
                    or b'upgrade' not in [part.strip().lower() for part in headers.get(b'connection', b'').split(b',')]):
                raise OSError('Invalid Chrome WebSocket handshake')
            connection.settimeout(None)
            return cls(connection, reader)
        except BaseException:
            reader.close()
            connection.close()
            raise

    def _exact(self, count):
        result = bytearray()
        while len(result) < count:
            block = self.reader.read(count - len(result))
            if not block:
                raise OSError('Chrome WebSocket closed')
            result.extend(block)
        return bytes(result)

    def _frame(self, opcode, payload):
        if len(payload) > MAX_MESSAGE or (opcode >= 8 and len(payload) > 125):
            raise OSError('Chrome WebSocket message exceeds limit')
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size <= 65535:
            header += bytes([0xfe]) + struct.pack('!H', size)
        else:
            header += bytes([0xff]) + struct.pack('!Q', size)
        masked = bytes(byte ^ mask[i & 3] for i, byte in enumerate(payload))
        with self._write_lock:
            self.socket.sendall(header + mask + masked)

    def write(self, data):
        if self._closed.is_set() or not data.endswith(b'\0'):
            raise OSError('Invalid or closed Chrome control message')
        self._frame(1, data[:-1])
        return len(data)

    def read(self, count):
        while not self._output:
            first, second = self._exact(2)
            opcode, final = first & 15, bool(first & 128)
            if first & 112 or second & 128:
                raise OSError('Invalid Chrome WebSocket frame flags')
            size = second & 127
            if size == 126:
                size = struct.unpack('!H', self._exact(2))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._exact(8))[0]
            if size > MAX_MESSAGE or len(self._fragments) + size > MAX_MESSAGE:
                raise OSError('Chrome WebSocket message exceeds limit')
            if opcode >= 8 and (not final or size > 125):
                raise OSError('Invalid Chrome WebSocket control frame')
            payload = self._exact(size)
            if opcode == 8:
                return b''
            if opcode == 9:
                self._frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1) or (opcode == 0) != self._fragmenting:
                raise OSError('Invalid Chrome WebSocket continuation')
            self._fragments.extend(payload)
            self._fragmenting = not final
            if final:
                self._output = self._fragments + b'\0'
                self._fragments = bytearray()
        result = bytes(self._output[:count])
        del self._output[:count]
        return result

    def close(self):
        if not self._closed.is_set():
            self._closed.set()
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
            self.reader.close()


def launch_browser(arguments):
    if os.name != 'nt':
        raise RuntimeError('The native Chrome launcher currently requires Windows')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    job, process, stream = BrowserJob(), None, None
    try:
        process = subprocess.Popen([*arguments, '--remote-debugging-address=127.0.0.1',
                                    f'--remote-debugging-port={port}'],
                                   close_fds=True, creationflags=4, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        job.attach(process)
        resume_browser(process)
        deadline = time.monotonic() + 12
        last_error = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise OSError('Chrome exited before its control endpoint was ready')
            if verify_listener(process, port):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                try:
                    connection.request('GET', '/json/version')
                    response = connection.getresponse()
                    body = response.read(65537)
                    if response.status != 200 or len(body) > 65536:
                        raise OSError('Invalid Chrome control endpoint response')
                    endpoint = json.loads(body)['webSocketDebuggerUrl']
                except (TimeoutError, ConnectionError, http.client.HTTPException) as exc:
                    # The owned listener can open before Chrome serves DevTools.
                    # Retry within the startup deadline, rechecking its owner.
                    last_error = exc
                    time.sleep(.04)
                    continue
                finally:
                    connection.close()
                stream = WebSocketStream.connect(endpoint, port)
                if not verify_listener(process, port):
                    raise OSError('Chrome listener disappeared during connection')
                return process, stream, job
            time.sleep(.04)
        raise OSError('Timed out waiting for the Chrome control endpoint') from last_error
    except BaseException:
        if stream:
            stream.close()
        job.close()
        if process:
            process.wait(timeout=5)
        raise
