import io
import struct
import types
import unittest
from unittest.mock import Mock, patch

import chrome_transport as ct


class Socket:
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, data):
        self.sent.extend(data)

    def shutdown(self, how):
        pass

    def close(self):
        pass


def frame(opcode, payload=b'', final=True):
    header = bytes([opcode | (128 if final else 0)])
    if len(payload) < 126:
        return header + bytes([len(payload)]) + payload
    return header + b'\x7e' + struct.pack('!H', len(payload)) + payload


class TransportTests(unittest.TestCase):
    def test_fragmented_messages_and_ping_preserve_cdp_framing(self):
        socket = Socket()
        data = frame(1, b'{"id":', False) + frame(9, b'ping') + frame(0, b'1}') + frame(1, b'{"id":2}')
        stream = ct.WebSocketStream(socket, io.BytesIO(data))
        self.assertEqual(stream.read(4) + stream.read(99), b'{"id":1}\0')
        self.assertEqual(stream.read(99), b'{"id":2}\0')
        self.assertEqual(socket.sent[0] & 15, 10)  # Pong, client-masked.
        self.assertTrue(socket.sent[1] & 128)

    def test_client_messages_are_masked_and_support_extended_lengths(self):
        for count in (10, 200, 70000):
            socket = Socket()
            stream = ct.WebSocketStream(socket, io.BytesIO())
            data = b'x' * count + b'\0'
            self.assertEqual(stream.write(data), len(data))
            wire = bytes(socket.sent)
            self.assertEqual(wire[0], 0x81)
            self.assertTrue(wire[1] & 128)
            offset = 2 if count < 126 else (4 if count <= 65535 else 10)
            mask, encoded = wire[offset:offset+4], wire[offset+4:]
            self.assertEqual(bytes(b ^ mask[i & 3] for i,b in enumerate(encoded)), data[:-1])

    def test_oversized_masked_and_invalid_continuation_frames_are_rejected(self):
        for data in (b'\x81\x7f' + struct.pack('!Q', ct.MAX_MESSAGE + 1),
                     b'\x81\x80', frame(0, b'no message'), frame(2, b'binary'), frame(9, b'x', False)):
            with self.subTest(data=data), self.assertRaises(OSError):
                ct.WebSocketStream(Socket(), io.BytesIO(data)).read(65536)

    def test_foreign_or_wildcard_listener_is_rejected(self):
        process = types.SimpleNamespace(pid=42, poll=Mock(return_value=None))
        for owner in ((43, '127.0.0.1'), (42, '0.0.0.0')):
            with patch.object(ct, 'listener_owner', return_value=owner), self.assertRaisesRegex(OSError, 'not owned'):
                ct.verify_listener(process, 12345)
        with patch.object(ct, 'listener_owner', return_value=None):
            self.assertFalse(ct.verify_listener(process, 12345))
        with patch.object(ct, 'listener_owner', return_value=(42, '127.0.0.1')):
            self.assertTrue(ct.verify_listener(process, 12345))
            process.poll.return_value = 0
            with self.assertRaisesRegex(OSError, 'not owned'):
                ct.verify_listener(process, 12345)

    def test_endpoint_url_cannot_redirect_the_native_client(self):
        for url in ('ws://evil.example:12345/devtools/browser/abc',
                    'ws://127.0.0.1:23456/devtools/browser/abc',
                    'ws://127.0.0.1:12345/other',
                    'ws://127.0.0.1:12345/devtools/browser/abc?other=1'):
            with patch.object(ct.socket, 'create_connection') as connect:
                with self.assertRaisesRegex(OSError, 'Unexpected'):
                    ct.WebSocketStream.connect(url, 12345)
                connect.assert_not_called()

    def test_failed_ownership_check_closes_only_the_new_browser_job(self):
        process = types.SimpleNamespace(pid=42, poll=Mock(return_value=None), wait=Mock())
        with (patch.object(ct, 'BrowserJob') as factory,
              patch.object(ct.subprocess, 'Popen', return_value=process),
              patch.object(ct, 'resume_browser'),
              patch.object(ct, 'verify_listener', side_effect=OSError('fixture wrong owner'))):
            with self.assertRaisesRegex(OSError, 'fixture wrong owner'):
                ct.launch_browser(['chrome.exe'])
            factory.return_value.close.assert_called_once()
            process.wait.assert_called_once()

    def test_slow_control_endpoint_is_retried_without_restarting_browser(self):
        process = types.SimpleNamespace(pid=42, poll=Mock(return_value=None), wait=Mock())
        first, second, stream = Mock(), Mock(), Mock()
        first.getresponse.side_effect = TimeoutError('fixture endpoint warming up')
        second.getresponse.return_value = types.SimpleNamespace(
            status=200, read=Mock(return_value=b'{"webSocketDebuggerUrl":"ws://127.0.0.1:12345/devtools/browser/abc"}'))
        with (patch.object(ct, 'BrowserJob') as factory,
              patch.object(ct.subprocess, 'Popen', return_value=process) as spawn,
              patch.object(ct, 'resume_browser'),
              patch.object(ct, 'verify_listener', return_value=True) as verify,
              patch.object(ct.http.client, 'HTTPConnection', side_effect=[first, second]),
              patch.object(ct.WebSocketStream, 'connect', return_value=stream),
              patch.object(ct.time, 'sleep')):
            self.assertEqual(ct.launch_browser(['chrome.exe']), (process, stream, factory.return_value))
        spawn.assert_called_once()
        self.assertEqual(verify.call_count, 3)
        first.close.assert_called_once()
        second.close.assert_called_once()
        factory.return_value.close.assert_not_called()
        process.wait.assert_not_called()

    def test_unready_control_endpoint_stops_at_startup_deadline(self):
        process = types.SimpleNamespace(pid=42, poll=Mock(return_value=None), wait=Mock())
        connection = Mock()
        connection.getresponse.side_effect = TimeoutError('fixture endpoint warming up')
        with (patch.object(ct, 'BrowserJob') as factory,
              patch.object(ct.subprocess, 'Popen', return_value=process),
              patch.object(ct, 'resume_browser'),
              patch.object(ct, 'verify_listener', return_value=True),
              patch.object(ct.http.client, 'HTTPConnection', return_value=connection),
              patch.object(ct.time, 'monotonic', side_effect=[0, 0, 13]),
              patch.object(ct.time, 'sleep')):
            with self.assertRaisesRegex(OSError, 'Timed out waiting for the Chrome control endpoint') as raised:
                ct.launch_browser(['chrome.exe'])
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)
        connection.close.assert_called_once()
        factory.return_value.close.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_control_endpoint_retry_rejects_a_changed_listener_owner(self):
        process = types.SimpleNamespace(pid=42, poll=Mock(return_value=None), wait=Mock())
        connection = Mock()
        connection.getresponse.side_effect = TimeoutError('fixture endpoint warming up')
        with (patch.object(ct, 'BrowserJob') as factory,
              patch.object(ct.subprocess, 'Popen', return_value=process),
              patch.object(ct, 'resume_browser'),
              patch.object(ct, 'verify_listener', side_effect=[True, OSError('fixture wrong owner')]),
              patch.object(ct.http.client, 'HTTPConnection', return_value=connection) as http,
              patch.object(ct.time, 'sleep')):
            with self.assertRaisesRegex(OSError, 'fixture wrong owner'):
                ct.launch_browser(['chrome.exe'])
        http.assert_called_once()
        connection.close.assert_called_once()
        factory.return_value.close.assert_called_once()
        process.wait.assert_called_once()
