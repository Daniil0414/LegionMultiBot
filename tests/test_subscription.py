import base64
import gzip
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

import vpn_dispatcher as vpn
from vpn_dispatcher import subscription_device_id as read_windows_subscription_identity


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='legion-subscription-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        identity = patch.object(vpn, 'subscription_device_id', return_value='01234567-89ab-cdef-0123-456789abcdef')
        identity.start()
        self.addCleanup(identity.stop)
        for name, value in {
            'RUNTIME_DIR': str(self.root), 'CONFIG_FILE': str(self.root / 'vpn.json'),
            'LEGACY_CONFIG_FILE': str(self.root / 'legacy.json'),
        }.items():
            patcher = patch.object(vpn, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch.object(vpn.VpnDispatcher, '_find_xray_binary', return_value=None), patch.object(vpn.atexit, 'register'):
            self.dispatcher = vpn.VpnDispatcher()
        self.dispatcher.nodes = vpn.parse_raw_nodes('socks5://old.invalid:1080')
        self.dispatcher.config.update(raw_nodes_text='socks5://old.invalid:1080', subscription_url='https://old.invalid/sub')
        self.old_config = dict(self.dispatcher.config)
        self.old_nodes = list(self.dispatcher.nodes)

    def serve(self, payload, headers=None):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append(self.headers)
                self.send_response(200)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/subscription', requests

    def assert_routes_preserved(self):
        self.assertEqual(self.dispatcher.nodes, self.old_nodes)
        self.assertEqual(self.dispatcher.config, self.old_config)

    def test_direct_loader_uses_existing_device_identity_without_creating_one(self):
        payload = base64.b64encode(b'socks5://first.invalid:1080\nsocks5://second.invalid:1081')
        url, requests = self.serve(payload)
        with patch.object(self.dispatcher, 'start_xray', return_value=True):
            self.assertTrue(self.dispatcher.fetch_subscription('happ://add/' + url)[0])
            self.assertTrue(self.dispatcher.fetch_subscription()[0])
        self.assertEqual(len(self.dispatcher.nodes), 2)
        self.assertEqual(self.dispatcher.config['subscription_url'], url)
        self.assertEqual(json.loads(Path(vpn.CONFIG_FILE).read_text())['subscription_url'], url)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]['X-Hwid'], '01234567-89ab-cdef-0123-456789abcdef')
        self.assertEqual(requests[1]['X-Hwid'], requests[0]['X-Hwid'])
        self.assertEqual(requests[0]['User-Agent'], 'Happ/4.3.0')
        self.assertTrue(requests[0]['X-Device-Model'])
        self.assertFalse((self.root / 'subscription-device.json').exists())

    def test_standalone_json_fetch_needs_neither_happ_nor_xray(self):
        url, requests = self.serve(base64.b64encode(b'socks5://standalone.invalid:1080'))
        self.assertIsNone(self.dispatcher.xray_bin)
        with (patch('happ_memory.read_happ_configs', side_effect=AssertionError('Happ dependency')),
              patch.object(vpn.os, 'startfile', create=True, side_effect=AssertionError('Application opened')),
              patch.object(self.dispatcher, 'start_xray') as start,
              patch.object(self.dispatcher, 'save_config') as save):
            ok, message, raw = self.dispatcher.export_subscription_json(url)
        self.assertTrue(ok, message)
        self.assertEqual(len(json.loads(raw)), 1)
        self.assertEqual(requests[0]['X-Hwid'], '01234567-89ab-cdef-0123-456789abcdef')
        self.assert_routes_preserved()
        start.assert_not_called()
        save.assert_not_called()

    def test_every_outbound_survives_plain_and_base64_json_subscriptions(self):
        configs = [{'remarks': 'fixture', 'outbounds': [
            {'protocol': 'socks', 'settings': {'servers': [{'address': 'one.invalid', 'port': 1080}]}},
            {'protocol': 'socks', 'settings': {'servers': [{'address': 'two.invalid', 'port': 1081}]}},
            {'protocol': 'freedom'}, {'protocol': 'blackhole'}]}]
        plain = json.dumps(configs).encode()
        for payload in (plain, base64.b64encode(plain)):
            with self.subTest(base64=payload != plain):
                url, _ = self.serve(payload)
                ok, message, raw = self.dispatcher.export_subscription_json(url)
                self.assertTrue(ok, message)
                parsed = json.loads(raw)
                self.assertEqual(len(parsed), 2)
                self.assertEqual({item['outbounds'][0]['settings']['servers'][0]['address'] for item in parsed}, {'one.invalid', 'two.invalid'})
                self.assert_routes_preserved()

    def test_apply_saves_subscription_source_atomically_with_servers(self):
        with patch.object(self.dispatcher, 'start_xray', return_value=True):
            ok, message = self.dispatcher.set_custom_nodes_text('socks5://source.invalid:1080', source_url='https://source.invalid/sub')
        self.assertTrue(ok, message)
        saved = json.loads(Path(vpn.CONFIG_FILE).read_text())
        self.assertEqual(saved['subscription_url'], 'https://source.invalid/sub')
        self.assertEqual(saved['raw_nodes_text'], 'socks5://source.invalid:1080')

    @unittest.skipUnless(os.name == 'nt', 'Windows device identity')
    def test_device_identity_reads_windows_registry_without_happ_settings(self):
        with (patch('winreg.OpenKey') as opened,
              patch('winreg.QueryValueEx', return_value=('01234567-89ab-cdef-0123-456789abcdef', 1))):
            self.assertEqual(read_windows_subscription_identity(), '01234567-89ab-cdef-0123-456789abcdef')
        self.assertEqual(opened.call_args.args[1], r'SOFTWARE\Microsoft\Cryptography')

    def test_subscription_opens_in_happ_without_fetching_or_changing_routes(self):
        url = 'https://example.invalid/private-subscription'
        with (patch.object(vpn.os, 'startfile', create=True) as startfile,
              patch.object(vpn.urllib.request, 'build_opener') as network,
              patch.object(self.dispatcher, 'start_xray') as xray):
            self.assertTrue(self.dispatcher.open_subscription_in_happ(url)[0])
        startfile.assert_called_once_with('happ://add/' + url)
        network.assert_not_called()
        xray.assert_not_called()
        self.assertEqual(self.dispatcher.nodes, self.old_nodes)
        self.assertEqual(self.dispatcher.config['raw_nodes_text'], self.old_config['raw_nodes_text'])
        self.assertEqual(self.dispatcher.config['subscription_url'], self.old_config['subscription_url'])
        self.assertEqual(self.dispatcher.config['happ_subscription_url'], url)
        self.assertFalse((self.root / 'subscription-device.json').exists())

    def test_existing_happ_json_is_imported_without_subscription_request(self):
        config = dict(remarks='Happ fixture', outbounds=[dict(protocol='socks', settings=dict(
            servers=[dict(address='happ-json.invalid', port=1080)]))])
        with (patch('happ_memory.read_happ_configs', return_value=[config]),
              patch.object(self.dispatcher, 'start_xray', return_value=True),
              patch.object(self.dispatcher, 'fetch_subscription') as direct,
              patch.object(vpn.urllib.request, 'build_opener') as network):
            self.assertTrue(self.dispatcher.import_happ_servers()[0])
        network.assert_not_called()
        direct.assert_not_called()
        self.assertEqual(len(self.dispatcher.nodes), 2)
        saved = json.loads(self.dispatcher.config['raw_nodes_text'])
        self.assertTrue(any(item['outbounds'][0]['settings']['servers'][0]['address'] == 'happ-json.invalid' for item in saved))

    def test_export_happ_json_does_not_apply_or_save_or_request_subscription(self):
        config = dict(remarks='Happ export fixture', outbounds=[dict(protocol='socks', settings=dict(
            servers=[dict(address='export-only.invalid', port=1080)]))])
        with (patch('happ_memory.read_happ_configs', return_value=[config]),
              patch.object(self.dispatcher, 'start_xray') as start,
              patch.object(self.dispatcher, 'save_config') as save,
              patch.object(vpn.urllib.request, 'build_opener') as network):
            ok, message, raw = self.dispatcher.export_happ_json()
        self.assertTrue(ok)
        self.assertIn('Получено серверов', message)
        self.assertEqual(json.loads(raw)[0]['outbounds'][0]['settings']['servers'][0]['address'], 'export-only.invalid')
        self.assert_routes_preserved()
        start.assert_not_called()
        save.assert_not_called()
        network.assert_not_called()

    def test_export_happ_json_reports_reader_error_without_touching_routes(self):
        with patch('happ_memory.read_happ_configs', side_effect=RuntimeError('Open exactly one Happ client with its server list first.')):
            ok, message, raw = self.dispatcher.export_happ_json()
        self.assertFalse(ok)
        self.assertIn('ровно один Happ', message)
        self.assertEqual(raw, '')
        self.assert_routes_preserved()

    def test_happ_launch_failure_preserves_working_routes(self):
        with (patch.object(vpn.os, 'startfile', create=True, side_effect=OSError('Happ missing')),
              patch.object(vpn.urllib.request, 'build_opener') as network):
            self.assertFalse(self.dispatcher.open_subscription_in_happ('https://example.invalid/sub')[0])
        network.assert_not_called()
        self.assert_routes_preserved()

    def test_percent_encoded_base64_provider_refusal_preserves_working_routes(self):
        payload = base64.b64encode(b'vless://00000000-0000-0000-0000-000000000000@example.invalid:443#App%20not%20supported')
        url, requests = self.serve(payload)
        with patch.object(self.dispatcher, 'start_xray') as start:
            ok, message = self.dispatcher.fetch_subscription(url)
        self.assertFalse(ok)
        self.assertIn('App not supported', message)
        self.assert_routes_preserved()
        start.assert_not_called()
        self.assertEqual({request['X-Hwid'] for request in requests}, {'01234567-89ab-cdef-0123-456789abcdef'})

    def test_device_limit_header_stops_retries_without_changing_routes(self):
        url, requests = self.serve(b'socks5://should-not-apply.invalid:1080', {'X-Hwid-Max-Devices-Reached': 'true'})
        with patch.object(self.dispatcher, 'start_xray') as start:
            ok, message = self.dispatcher.fetch_subscription(url)
        self.assertFalse(ok)
        self.assertIn('лимит устройств', message)
        self.assertEqual(len(requests), 1)
        self.assert_routes_preserved()
        start.assert_not_called()

    def test_gzip_json_subscription_is_supported(self):
        config = dict(remarks='fixture', outbounds=[dict(protocol='socks', settings=dict(
            servers=[dict(address='json.invalid', port=1080)]))])
        url, _ = self.serve(gzip.compress(json.dumps(config).encode()))
        with patch.object(self.dispatcher, 'start_xray', return_value=True):
            self.assertTrue(self.dispatcher.fetch_subscription(url)[0])
        self.assertEqual(self.dispatcher.nodes[0].address, 'json.invalid')

    def test_failed_xray_application_keeps_previous_subscription(self):
        url, _ = self.serve(b'socks5://new.invalid:1080')
        with patch.object(self.dispatcher, 'start_xray', return_value=False):
            self.assertFalse(self.dispatcher.fetch_subscription(url)[0])
        self.assert_routes_preserved()

    def test_network_errors_do_not_expose_secret_subscription_url(self):
        secret = 'private-subscription-key'
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError('request failed: https://example.invalid/' + secret)
        with patch.object(vpn.urllib.request, 'build_opener', return_value=opener):
            ok, message = self.dispatcher.fetch_subscription('https://example.invalid/' + secret)
        self.assertFalse(ok)
        self.assertNotIn(secret, message)
        self.assertIn('URLError', message)
        self.assert_routes_preserved()

    def test_invalid_url_never_opens_network_or_creates_device_identity(self):
        with patch.object(vpn.urllib.request, 'build_opener') as opener:
            for url in ('', 'file:///tmp/secret', 'ftp://example.invalid/sub',
                        'https://user:password@example.invalid/sub', 'https://example.invalid/a b'):
                with self.subTest(url=url):
                    self.assertFalse(self.dispatcher.fetch_subscription(url)[0])
        opener.assert_not_called()
        self.assertFalse((self.root / 'subscription-device.json').exists())

    def test_happ_read_failure_preserves_working_routes_without_direct_fallback(self):
        with (patch('happ_memory.read_happ_configs', side_effect=RuntimeError('Happ not open')),
              patch.object(self.dispatcher, 'fetch_subscription') as direct,
              patch.object(vpn.urllib.request, 'build_opener') as network):
            self.assertFalse(self.dispatcher.import_happ_servers()[0])
        network.assert_not_called()
        direct.assert_not_called()
        self.assert_routes_preserved()

    def test_oversized_compressed_response_is_rejected_without_changing_routes(self):
        url, _ = self.serve(gzip.compress(b'x' * 100))
        with patch.object(vpn, 'MAX_SUBSCRIPTION_BYTES', 64):
            self.assertFalse(self.dispatcher.fetch_subscription(url)[0])
        self.assert_routes_preserved()


@unittest.skipUnless(os.name == 'nt', 'Windows settings UI')
class SubscriptionSettingsTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import app_container as app
        self.app = app
        self.tk = tk
        self.root = app.HighQualityMultiBoxerApp.__new__(app.HighQualityMultiBoxerApp)
        tk.Tk.__init__(self.root)
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.dispatcher = Mock(config={'subscription_url': 'https://example.invalid/saved', 'raw_nodes_text': ''}, nodes=[])
        self.dispatcher.open_subscription_in_happ.return_value = (True, 'fixture opened')
        self.dispatcher.export_happ_json.return_value = (True, 'fixture loaded', '[{"remarks":"loaded JSON fixture","outbounds":[]}]')
        self.dispatcher.export_subscription_json.return_value = (True, 'fixture loaded', '[{"remarks":"loaded JSON fixture","outbounds":[]}]')
        self.dispatcher.get_nodes_snapshot.return_value = []
        self.root.use_vpn = True
        self.root.proxy_manager = SimpleNamespace(vpn_dispatcher=self.dispatcher)
        self.root.browser_sessions = {}
        self.root._happ_import_busy = False
        self.root._happ_import_buttons = []
        self.root.happ_status_var = tk.StringVar(master=self.root)
        self.root.periodic_vpn_check = Mock()
        self.root.update_all_slot_proxy_badges = Mock()
        self.entry_bindings = {}
        original_bind = tk.Entry.bind

        def capture_binding(widget, sequence=None, func=None, add=None):
            if func is not None:
                self.entry_bindings[sequence] = func
            return original_bind(widget, sequence, func, add)

        with patch.object(tk.Entry, 'bind', autospec=True, side_effect=capture_binding):
            self.root.open_proxy_settings()
        self.widgets = []

        def visit(parent):
            for widget in parent.winfo_children():
                self.widgets.append(widget)
                visit(widget)
        visit(self.root)
        self.entry = next(widget for widget in self.widgets if isinstance(widget, tk.Entry))
        self.button = next(widget for widget in self.widgets
                           if isinstance(widget, tk.Button) and widget.cget('text') == 'Получить JSON по ссылке')
        self.import_button = next(widget for widget in self.widgets
                                  if isinstance(widget, tk.Button) and widget.cget('text') == 'Получить JSON из открытого Happ')

    def tearDown(self):
        # Finalize Tk interpreters on their creating thread, before an HTTP
        # fixture thread can trigger cyclic GC of a previously destroyed root.
        self.doCleanups()
        self.widgets.clear()
        self.entry_bindings.clear()
        self.entry = self.button = self.import_button = None
        self.root._happ_settings_refresh = None
        self.root._happ_settings_dialog = None
        self.root._happ_import_buttons.clear()
        self.root.happ_status_var = None
        self.root = None
        gc.collect()

    def test_paste_button_replaces_saved_link_and_trims_clipboard_whitespace(self):
        button = next(widget for widget in self.widgets
                      if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Вставить')
        with patch.object(self.entry, 'clipboard_get', return_value='  https://example.invalid/pasted\r\n'):
            button.invoke()
        self.assertEqual(self.entry.get(), 'https://example.invalid/pasted')
        self.assertIn('вставлена', self.root.happ_status_var.get())
        self.dispatcher.open_subscription_in_happ.assert_not_called()

    def test_ctrl_v_in_russian_layout_pastes_once_and_replaces_selection(self):
        self.entry.selection_range(0, 'end')
        event = SimpleNamespace(keycode=0x56, keysym='Cyrillic_em')
        with patch.object(self.entry, 'clipboard_get', return_value='https://example.invalid/russian') as clipboard:
            result = self.entry_bindings['<Control-KeyPress>'](event)
        self.assertEqual(result, 'break')
        clipboard.assert_called_once_with()
        self.assertEqual(self.entry.get(), 'https://example.invalid/russian')

    def test_ctrl_a_in_russian_layout_selects_whole_link(self):
        event = SimpleNamespace(keycode=0x41, keysym='Cyrillic_ef')
        self.assertEqual(self.entry_bindings['<Control-KeyPress>'](event), 'break')
        self.assertEqual(self.entry.index('sel.first'), 0)
        self.assertEqual(self.entry.index('sel.last'), len(self.entry.get()))

    def test_shift_insert_and_virtual_paste_use_clipboard(self):
        for binding in ('<Shift-Insert>', '<<Paste>>'):
            with self.subTest(binding=binding):
                self.entry.selection_range(0, 'end')
                with patch.object(self.entry, 'clipboard_get', return_value='https://example.invalid/shortcut'):
                    self.assertEqual(self.entry_bindings[binding](None), 'break')
                self.assertEqual(self.entry.get(), 'https://example.invalid/shortcut')

    def test_context_menu_pastes_link(self):
        menu = next(widget for widget in self.widgets if isinstance(widget, self.tk.Menu))
        with patch.object(self.entry, 'clipboard_get', return_value='https://example.invalid/menu'):
            menu.invoke('Вставить ссылку')
        self.assertEqual(self.entry.get(), 'https://example.invalid/menu')

    def test_missing_clipboard_text_preserves_saved_link(self):
        saved = self.entry.get()
        for result in ('', self.tk.TclError('CLIPBOARD selection does not exist')):
            with self.subTest(result=type(result).__name__):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(self.entry, 'clipboard_get', **kwargs):
                    self.entry_bindings['<<Paste>>'](None)
                self.assertEqual(self.entry.get(), saved)

    def test_link_fetches_json_without_opening_happ_or_applying_routes(self):
        self.assertEqual(self.entry.get(), 'https://example.invalid/saved')
        self.assertTrue(self.entry.cget('show'))
        self.entry.delete(0, 'end')
        self.entry.insert(0, 'https://example.invalid/new')

        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback())):
            self.button.invoke()
        self.dispatcher.export_subscription_json.assert_called_once_with('https://example.invalid/new')
        self.dispatcher.open_subscription_in_happ.assert_not_called()
        self.dispatcher.export_happ_json.assert_not_called()
        self.dispatcher.import_happ_servers.assert_not_called()
        self.dispatcher.fetch_subscription.assert_not_called()
        self.root.update_all_slot_proxy_badges.assert_not_called()
        self.assertEqual(self.dispatcher.config['raw_nodes_text'], '')
        texts = [widget.get('1.0', 'end') for widget in self.widgets if isinstance(widget, self.tk.Text)]
        self.assertTrue(any('loaded JSON fixture' in text for text in texts))
        self.assertEqual(self.button.cget('state'), 'normal')
        self.assertEqual(self.root._pending_subscription_url, 'https://example.invalid/new')

    def test_standalone_fetch_with_running_bots_preserves_their_routes(self):
        self.root.browser_sessions = {'active-account': {'pid': 1}}
        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback())):
            self.button.invoke()
        self.dispatcher.export_subscription_json.assert_called_once_with('https://example.invalid/saved')
        self.dispatcher.open_subscription_in_happ.assert_not_called()
        self.dispatcher.import_happ_servers.assert_not_called()
        self.dispatcher.fetch_subscription.assert_not_called()
        self.dispatcher.start_xray.assert_not_called()
        self.dispatcher.set_custom_nodes_text.assert_not_called()

    def test_apply_of_downloaded_json_includes_its_source_link(self):
        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback())):
            self.button.invoke()
        apply = next(widget for widget in self.widgets
                     if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Применить и закрыть')
        self.dispatcher.set_custom_nodes_text.return_value = (True, 'applied fixture')
        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback()),
              patch.object(self.app.messagebox, 'showinfo')):
            apply.invoke()
        self.assertEqual(self.dispatcher.set_custom_nodes_text.call_args.kwargs,
                         {'source_url': 'https://example.invalid/saved'})
        self.assertIsNone(self.root._pending_subscription_url)
        self.assertFalse(self.root._happ_settings_dialog.winfo_exists())

    def test_get_json_works_while_bots_are_running_without_changing_routes(self):
        self.root.browser_sessions = {'active-account': {'pid': 1}}
        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback())):
            self.import_button.invoke()
        self.dispatcher.export_happ_json.assert_called_once_with()
        self.dispatcher.import_happ_servers.assert_not_called()
        self.dispatcher.set_custom_nodes_text.assert_not_called()
        self.dispatcher.start_xray.assert_not_called()
        self.assertEqual(self.dispatcher.config['raw_nodes_text'], '')
        self.assertIn('loaded JSON fixture', self.root._pending_happ_json)

    def test_apply_checks_live_processes_instead_of_stale_session_entries(self):
        self.root.browser_sessions = {'active-account': {'pid': 1}}
        keys = next(widget for widget in self.widgets if isinstance(widget, self.tk.Text))
        keys.insert('1.0', 'socks5://apply-fixture.invalid:1080')
        apply = next(widget for widget in self.widgets
                     if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Применить и закрыть')
        with patch.object(self.app, 'process_identity', return_value={'pid': 1}):
            apply.invoke()
        self.dispatcher.set_custom_nodes_text.assert_not_called()
        self.dispatcher.set_custom_nodes_text.return_value = (True, 'applied fixture')
        with (patch.object(self.app, 'process_identity', return_value=None),
              patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback()),
              patch.object(self.app.messagebox, 'showinfo')):
            apply.invoke()
        self.dispatcher.set_custom_nodes_text.assert_called_once_with('socks5://apply-fixture.invalid:1080')
        self.assertFalse(self.root._happ_settings_dialog.winfo_exists())

    def test_close_settings_keeps_staged_json_without_implicitly_applying_it(self):
        keys = next(widget for widget in self.widgets if isinstance(widget, self.tk.Text))
        keys.insert('1.0', '[{"remarks":"pending fixture","outbounds":[]}]')
        close = next(widget for widget in self.widgets
                     if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Не применять и закрыть')
        close.invoke()
        self.dispatcher.set_custom_nodes_text.assert_not_called()
        self.dispatcher.start_xray.assert_not_called()
        self.root.open_proxy_settings()

        def first_text(parent):
            for widget in parent.winfo_children():
                if isinstance(widget, self.tk.Text):
                    return widget
                found = first_text(widget)
                if found is not None:
                    return found
        reopened_keys = first_text(self.root._happ_settings_dialog)
        self.assertIn('pending fixture', reopened_keys.get('1.0', 'end'))

    def test_export_failure_displays_reason_and_preserves_previous_field(self):
        keys = next(widget for widget in self.widgets if isinstance(widget, self.tk.Text))
        keys.insert('1.0', 'previous fixture')
        message = 'Откройте ровно один Happ со списком серверов.'
        self.dispatcher.export_happ_json.return_value = (False, message, '')
        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback()),
              patch.object(self.app.messagebox, 'showerror') as error):
            self.import_button.invoke()
        self.assertEqual(error.call_args.args, ('Экспорт из Happ', message))
        self.assertEqual(self.root.happ_status_var.get(), message)
        self.assertEqual(keys.get('1.0', 'end').strip(), 'previous fixture')

    def test_settings_wait_for_explicit_apply_before_closing_or_exporting(self):
        keys = next(widget for widget in self.widgets if isinstance(widget, self.tk.Text))
        keys.insert('1.0', 'socks5://applying.invalid:1080')
        apply = next(widget for widget in self.widgets
                     if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Применить и закрыть')
        close = next(widget for widget in self.widgets
                     if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Не применять и закрыть')
        self.dispatcher.set_custom_nodes_text.return_value = (True, 'applied fixture')
        with patch.object(self.app.threading, 'Thread') as worker:
            apply.invoke()
            close.invoke()
            self.import_button.invoke()
            self.assertTrue(self.root._happ_settings_dialog.winfo_exists())
            self.dispatcher.export_happ_json.assert_not_called()
            with (patch.object(self.root, 'after', side_effect=lambda delay, callback: callback()),
                  patch.object(self.app.messagebox, 'showinfo')):
                worker.call_args.kwargs['target']()
        self.assertFalse(self.root._network_settings_busy)

    def test_apply_failure_keeps_settings_open_with_the_entered_json(self):
        keys = next(widget for widget in self.widgets if isinstance(widget, self.tk.Text))
        keys.insert('1.0', 'socks5://failed-apply.invalid:1080')
        apply = next(widget for widget in self.widgets
                     if isinstance(widget, self.tk.Button) and widget.cget('text') == 'Применить и закрыть')
        self.dispatcher.set_custom_nodes_text.return_value = (False, 'fixture apply failed')
        with (patch.object(self.app.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)),
              patch.object(self.root, 'after', side_effect=lambda delay, callback: callback()),
              patch.object(self.app.messagebox, 'showerror') as error):
            apply.invoke()
        self.assertTrue(self.root._happ_settings_dialog.winfo_exists())
        self.assertEqual(keys.get('1.0', 'end').strip(), 'socks5://failed-apply.invalid:1080')
        self.assertEqual(error.call_args.args, ('Ошибка', 'fixture apply failed'))

    def test_clear_button_empties_fields_and_displays_actual_shared_core_result(self):
        button = next(widget for widget in self.widgets
                      if isinstance(widget, self.tk.Button) and widget.cget('text') == '🗑️ Удалить все сервера')
        keys = next(widget for widget in self.widgets if isinstance(widget, self.tk.Text))
        keys.insert('1.0', '[{"remarks":"old fixture"}]')
        message = 'Список очищен. Общее ядро другого запускателя продолжает работать.'
        self.dispatcher.clear_all_nodes.return_value = (True, message)
        with (patch.object(self.app.messagebox, 'askyesno', return_value=True),
              patch.object(self.app.messagebox, 'showinfo') as info):
            button.invoke()
        self.dispatcher.clear_all_nodes.assert_called_once_with()
        self.assertEqual(self.entry.get(), '')
        self.assertEqual(keys.get('1.0', 'end').strip(), '')
        self.assertEqual(info.call_args.args, ('Готово', message))

    def test_clear_button_keeps_fields_when_persistence_fails(self):
        button = next(widget for widget in self.widgets
                      if isinstance(widget, self.tk.Button) and widget.cget('text') == '🗑️ Удалить все сервера')
        saved = self.entry.get()
        self.dispatcher.clear_all_nodes.return_value = (False, 'disk full')
        with (patch.object(self.app.messagebox, 'askyesno', return_value=True),
              patch.object(self.app.messagebox, 'showwarning') as warning):
            button.invoke()
        self.assertEqual(self.entry.get(), saved)
        self.assertEqual(warning.call_args.args, ('Удаление серверов', 'disk full'))
