"""Managed Chrome over a verified loopback WebSocket, with optional mobile emulation.

This is device emulation, not a replacement for physical hardware. No browser
extension, canvas noise, or navigator getter patch is used.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import urllib.parse

from vpn_runtime import atomic_json
from chrome_transport import launch_browser


DEVICES = (
    dict(name='compact', width=360, height=800, scale=1.0),
    dict(name='standard', width=390, height=844, scale=1.0),
    dict(name='large', width=412, height=915, scale=1.0),
    dict(name='wide', width=432, height=960, scale=1.0),
)
# Native browser identity already propagates to workers. Only top-level pages
# need viewport configuration; frames inherit it. Avoid pausing background work.
TARGET_FILTER = [dict(type='page'), dict(exclude=True)]


def device_for_profile(directory):
    """Keep the emulation preset outside website storage and independent of slots."""
    directory = Path(directory)
    path = directory / 'mobile_device.json'
    if path.exists():
        device = json.loads(path.read_text(encoding='utf-8'))
        if device.get('version') != 1 or type(device.get('preset')) is not int or device['preset'] not in range(len(DEVICES)):
            raise ValueError('Invalid mobile_device.json; restore the profile backup')
    else:
        index = int.from_bytes(hashlib.sha256(directory.name.encode('utf-8')).digest()[:4], 'big') % len(DEVICES)
        device = dict(version=1, preset=index)
        atomic_json(str(path), device)
    return dict(DEVICES[device['preset']])


class ProtocolError(RuntimeError):
    pass


class PipeConnection:
    """Multiplex CDP responses and events without running callbacks on the reader."""
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.events = queue.Queue()
        self._lock = threading.Lock()
        self._pending = {}
        self._next_id = 0
        self.closed = threading.Event()
        self.job = None
        threading.Thread(target=self._read, daemon=True, name='chrome-control-reader').start()

    @classmethod
    def launch(cls, arguments):
        process, stream, job = launch_browser(arguments)
        try:
            connection = cls(stream, stream)
            connection.job = job
            return process, connection
        except BaseException:
            stream.close()
            job.close()
            process.wait(timeout=5)
            raise

    def _read(self):
        buffer = bytearray()
        try:
            while True:
                chunk = self.reader.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > 16 * 1024 * 1024:
                    raise ProtocolError('Chrome pipe message exceeds 16 MB')
                while b'\0' in buffer:
                    raw, _, rest = buffer.partition(b'\0')
                    buffer = bytearray(rest)
                    if not raw:
                        continue
                    message = json.loads(raw)
                    if 'id' in message:
                        with self._lock:
                            pending = self._pending.pop(message['id'], None)
                        if pending:
                            pending.put(message)
                    else:
                        self.events.put(message)
        except (OSError, ValueError, ProtocolError):
            pass
        finally:
            self.reader.close()
            self.closed.set()
            with self._lock:
                for pending in self._pending.values():
                    pending.put({'error': {'message': 'Chrome control pipe closed'}})
                self._pending.clear()
            self.events.put(None)

    def _send(self, method, params, session, pending):
        with self._lock:
            if self.closed.is_set():
                raise ProtocolError('Chrome control pipe closed')
            self._next_id += 1
            request_id = self._next_id
            if pending is not None:
                self._pending[request_id] = pending
            message = dict(id=request_id, method=method, params=params or {})
            if session:
                message['sessionId'] = session
            try:
                payload = json.dumps(message).encode('utf-8') + b'\0'
                while payload:
                    sent = self.writer.write(payload)
                    if not sent:
                        raise OSError('Chrome control pipe write failed')
                    payload = payload[sent:]
            except OSError as exc:
                self._pending.pop(request_id, None)
                raise ProtocolError(str(exc)) from exc
        return request_id

    def send(self, method, params=None, session=None):
        return self._send(method, params, session, None)

    def call(self, method, params=None, session=None, timeout=15):
        pending = queue.Queue(maxsize=1)
        request_id = self._send(method, params, session, pending)
        try:
            result = pending.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise ProtocolError('Chrome command timed out: ' + method) from exc
        if 'error' in result:
            raise ProtocolError(method + ': ' + result['error']['message'])
        return result.get('result', {})

    def close(self):
        self.closed.set()
        self.writer.close()


class MobileBrowser:
    def __init__(self, device, cleanup_legacy=False, storage_path=None, emulate_mobile=True):
        self.device = device
        self.emulate_mobile = bool(emulate_mobile)
        self.cleanup_legacy = cleanup_legacy
        self.connection = None
        self.process = None
        self.app_mode = False
        self.app_target = None
        self._condition = threading.Condition()
        self._targets = {}
        self._errors = {}
        self._view_size = None
        self._fit_pending = False
        self._desktop_targets = set()
        self._navigation_sessions = set()
        self._script_lock = threading.RLock()
        self._open_lock = threading.Lock()
        self._userscript = None
        self._script_ids = {}
        from userscript_privacy import PrivateScriptStore
        import secrets
        token = secrets.token_hex(24)
        self._private_store = PrivateScriptStore(storage_path) if storage_path else None
        self._private_config = {name: token + '-' + name for name in
                                ('binding', 'receiver', 'request', 'reply', 'ready', 'world')}
        self._private_sessions = set()

    def _prepare_private_world(self, session, frame):
        if session not in self._private_sessions or not frame.get('url', '').startswith(('http://', 'https://')):
            return
        from userscript_privacy import bridge_source
        context = self.connection.call('Page.createIsolatedWorld',
            dict(frameId=frame['id'], worldName=self._private_config['world']), session)['executionContextId']
        # Install into the existing isolated context. No Runtime.enable/console
        # subscription is needed, and no binding is exposed in the page world.
        self.connection.call('Runtime.addBinding',
            dict(name=self._private_config['binding'], executionContextId=context), session)
        self.connection.call('Runtime.evaluate',
            dict(expression=bridge_source(self._private_config), contextId=context), session)

    def _private_binding(self, event):
        params = event.get('params', {})
        if params.get('name') != self._private_config['binding']:
            return False
        request_id, client = None, None
        try:
            payload = params['payload']
            if not isinstance(payload, str) or len(payload) > 1048576:
                raise ValueError('Private storage request exceeds the limit')
            message = json.loads(payload)
            request = message['request']
            request_id = request['id']
            client = request['client']
            if (type(request_id) is not int or request_id < 1 or self._private_store is None or
                    not isinstance(client, str) or len(client) > 128):
                raise ValueError('Invalid private storage request')
            result = self._private_store.request(message['origin'], request)
            packet = dict(id=request_id, client=client, ok=True, result=result)
        except Exception:
            packet = dict(id=request_id, client=client, ok=False,
                          error='Не удалось сохранить приватные настройки бота; данные сайта не удалены.')
        expression = 'globalThis[' + json.dumps(self._private_config['receiver']) + '](' + json.dumps(packet) + ')'
        try:
            self.connection.call('Runtime.evaluate', dict(expression=expression,
                contextId=params['executionContextId']), event.get('sessionId'))
        except (ProtocolError, KeyError):
            pass  # The originating document may have closed after its write was saved.
        return True

    def _register_userscript(self, session, run_immediately=False):
        previous = self._script_ids.get(session)
        current = None
        if not self._userscript or not self._userscript.get('private_state'):
            self._private_sessions.discard(session)
        if self._userscript:
            source = self._userscript['bootstrap']
            if self._userscript.get('private_state'):
                if self._private_store is None:
                    raise RuntimeError('Private userscript profile storage is not configured')
                self._private_sessions.add(session)
                source = source.replace('/*__PROFILE_PRIVACY__*/null', json.dumps(self._private_config))
            parameters = dict(source=source)
            if run_immediately:
                parameters['runImmediately'] = True
            current = self.connection.call('Page.addScriptToEvaluateOnNewDocument',
                parameters, session)['identifier']
        if previous:
            self.connection.call('Page.removeScriptToEvaluateOnNewDocument',
                                 dict(identifier=previous), session)
        if current:
            self._script_ids[session] = current
        else:
            self._script_ids.pop(session, None)

    def set_userscript(self, script, reload=False):
        """Replace future-document hooks; reload open pages only when code changed."""
        with self._script_lock:
            if (script or {}).get('digest') == (self._userscript or {}).get('digest'):
                return
            self._userscript = script
            with self._condition:
                targets = list(self._targets.values())
            for target in targets:
                if target['type'] in ('page', 'iframe'):
                    session = target['session']
                    self._register_userscript(session)
                    if reload:
                        self.connection.call('Page.reload', {}, session)

    def start(self, arguments):
        self.app_mode = any(argument.startswith('--app=') for argument in arguments)
        if not self.emulate_mobile and self.app_mode:
            # A data-URL app shell leaves stale/zero outer-window metrics after
            # a cross-origin navigation. Prepare ordinary Chrome without a
            # startup window, then create only the actual destination window.
            arguments = [argument for argument in arguments if not argument.startswith('--app=')]
            arguments.append('--no-startup-window')
        self.process, self.connection = PipeConnection.launch(arguments)
        try:
            version = self.connection.call('Browser.getVersion')
            match = re.search(r'(?:HeadlessChrome|Chrome)/(\d+\.\d+\.\d+\.\d+)',
                              version.get('product', '') + ' ' + version['userAgent'])
            if not match:
                raise RuntimeError('Unable to determine the actual Chrome version')
            self.version = match.group(1)
            threading.Thread(target=self._events, daemon=True, name='chrome-mobile-events').start()
            self.connection.call('Target.setAutoAttach', dict(autoAttach=True,
                                 waitForDebuggerOnStart=True, flatten=True, filter=TARGET_FILTER))
            return self.process
        except BaseException:
            self.close()
            raise

    def _configure(self, session, kind):
        cdp = self.connection
        if kind in ('page', 'iframe'):
            cdp.call('Page.enable', session=session)
            if kind == 'page' and self.emulate_mobile:
                # Child frames inherit metrics; Chrome rejects this on OOPIFs.
                cdp.call('Emulation.setDeviceMetricsOverride', self._metrics(), session)
                cdp.call('Emulation.setTouchEmulationEnabled', dict(enabled=True, maxTouchPoints=5), session)
            if self.cleanup_legacy:
                # A storage migration only: no globals, DOM nodes or API wrappers.
                cdp.call('Page.addScriptToEvaluateOnNewDocument',
                         dict(source="try { localStorage.removeItem('__lg_mobile_shield_device'); } catch (_) {}",
                              worldName='storage-migration'), session)
            # A target created directly with its destination may already have a
            # document while execution is paused. Cover that document as well.
            self._register_userscript(session, run_immediately=True)
            if session in self._private_sessions:
                frame = cdp.call('Page.getFrameTree', session=session)['frameTree']['frame']
                self._prepare_private_world(session, frame)

    def _metrics(self):
        metrics = dict(width=self.device['width'], height=self.device['height'],
                       deviceScaleFactor=self.device['scale'], mobile=True,
                       screenWidth=self.device['width'], screenHeight=self.device['height'],
                       screenOrientation=dict(type='portraitPrimary', angle=0))
        if self._view_size:
            width, height, zoom = self._view_size
            # Fit the available width, and let the mobile layout use the remaining
            # height. Scaling the entire fixed phone screen leaves a large gutter.
            metrics['width'] = max(50, round(self.device['width'] / zoom))
            metrics['scale'] = width / metrics['width']
            metrics['height'] = max(50, round(height / metrics['scale']))
        return metrics

    def fit(self, width, height, zoom=1.0):
        """Fill the slot using a responsive mobile viewport and stable screen/DPR."""
        if not self.emulate_mobile:
            # The native window is resized by the grid. Keep Chrome's actual
            # screen, pointer, DPR and viewport; browser zoom belongs to Chrome.
            return
        size = (max(50, int(width)), max(50, int(height)), max(.5, min(2.0, float(zoom))))
        with self._condition:
            if size == self._view_size:
                return
            self._view_size = size
            if not self._fit_pending and not self.connection.closed.is_set():
                self._fit_pending = True
                self.connection.events.put({'method': 'local.fit'})

    def _events(self):
        while True:
            event = self.connection.events.get()
            if event is None:
                with self._condition:
                    self._condition.notify_all()
                return
            if event.get('method') == 'Runtime.bindingCalled' and self._private_binding(event):
                continue
            if (event.get('method') == 'Page.javascriptDialogOpening'
                    and event['params'].get('type') == 'beforeunload'
                    and event.get('sessionId') in self._navigation_sessions):
                try:
                    self.connection.send('Page.handleJavaScriptDialog', dict(accept=True), event['sessionId'])
                except ProtocolError:
                    pass  # The browser may have closed while the dialog opened.
                continue
            if event.get('method') == 'Page.frameNavigated':
                try:
                    self._prepare_private_world(event.get('sessionId'), event['params']['frame'])
                except ProtocolError:
                    pass  # A replaced frame cannot receive settings; the bot stays blocked.
            if event.get('method') == 'local.fit':
                with self._condition:
                    self._fit_pending = False
                    targets = list(self._targets.items())
                for target_id, target in targets:
                    if self.emulate_mobile and target['type'] == 'page' and target_id not in self._desktop_targets:
                        try:
                            self.connection.call('Emulation.setDeviceMetricsOverride',
                                                 self._metrics(), target['session'])
                        except ProtocolError:
                            pass  # A window may have closed during a grid resize.
                continue
            if event.get('method') == 'Target.detachedFromTarget':
                session_id = event['params']['sessionId']
                self._script_ids.pop(session_id, None)
                self._private_sessions.discard(session_id)
                with self._condition:
                    self._targets = {k: v for k, v in self._targets.items() if v['session'] != session_id}
                continue
            if event.get('method') == 'Page.frameNavigated' and self.app_mode and self.app_target:
                frame = event['params']['frame']
                if not frame.get('parentId') and frame.get('url', '').startswith(('chrome://newtab', 'chrome://new-tab-page')):
                    unwanted = next((k for k, v in self._targets.items()
                                     if v['session'] == event.get('sessionId') and k != self.app_target), None)
                    if unwanted:
                        try:
                            self.connection.send('Target.closeTarget', dict(targetId=unwanted))
                        except ProtocolError:
                            pass
                continue
            if event.get('method') != 'Target.attachedToTarget':
                continue
            info = event['params']
            target = info['targetInfo']
            session = info['sessionId']
            try:
                if self.app_mode and self.app_target and target.get('url', '').startswith(('chrome://newtab', 'chrome://new-tab-page')):
                    self.connection.send('Runtime.runIfWaitingForDebugger', session=session)
                    self.connection.send('Target.closeTarget', dict(targetId=target['targetId']))
                    continue
                with self._script_lock:
                    if not target.get('url', '').startswith(('chrome-extension://', 'chrome://')):
                        self._configure(session, target['type'])
                    # Send resumes without waiting for overlapping attach events.
                    self.connection.send('Runtime.runIfWaitingForDebugger', session=session)
                    with self._condition:
                        self._targets[target['targetId']] = dict(session=session, type=target['type'])
                        self._condition.notify_all()
            except (ProtocolError, OSError) as exc:
                with self._condition:
                    self._errors[target['targetId']] = str(exc)
                    self._condition.notify_all()
                # Do not let a new page continue with partially applied settings.
                try:
                    self.connection.call('Target.closeTarget', dict(targetId=target['targetId']))
                except ProtocolError:
                    pass

    def wait_target(self, target, timeout=15):
        deadline = time.monotonic() + timeout
        with self._condition:
            while target not in self._targets:
                if target in self._errors:
                    raise ProtocolError(self._errors[target])
                if self.connection.closed.is_set():
                    raise ProtocolError('Chrome control pipe closed')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolError('Timed out configuring a mobile page')
                self._condition.wait(remaining)
            return self._targets[target]['session']

    @property
    def has_app_window(self):
        return self.app_mode and self.app_target in self._targets

    def _close_extra_target(self, target):
        try:
            self.connection.call('Target.closeTarget', dict(targetId=target))
        except ProtocolError as exc:
            if 'No target with given id found' not in str(exc):
                raise  # An attach/frame event may have already closed this tab.

    def navigate(self, url, new_window=False, desktop=False, target_id=None):
        pages = self.connection.call('Target.getTargets')['targetInfos']
        if not self.emulate_mobile and (target_id is None or new_window):
            # Hold only document responses while the new native window gets its
            # userscript hooks. Do not edit requests, headers or response bodies.
            with self._open_lock:
                gated, target = False, None
                try:
                    if self._userscript:
                        self.connection.call('Fetch.enable', dict(patterns=[dict(
                            urlPattern='*', resourceType='Document', requestStage='Response')]))
                        gated = True
                    target = self.connection.call('Target.createTarget', dict(url=url, newWindow=True))['targetId']
                    self.wait_target(target)
                except BaseException:
                    if target:
                        self._close_extra_target(target)
                    raise
                finally:
                    if gated:
                        # Disabling the domain releases its pending responses.
                        self.connection.call('Fetch.disable')
            first_window = self.app_mode and self.app_target is None
            if first_window:
                self.app_target = target
            for page in pages:
                if (page['type'] == 'page' and page['targetId'] != target and
                        (first_window or page['url'] in ('about:blank', 'data:text/html,', 'chrome://newtab/'))):
                    self._close_extra_target(page['targetId'])
            return target
        blank = next((p for p in pages if p['type'] == 'page' and p['url'] in ('about:blank', 'data:text/html,')), None)
        if target_id:
            target = target_id
        elif blank and not new_window:
            target = blank['targetId']
        else:
            target = self.connection.call('Target.createTarget', dict(url='about:blank', newWindow=new_window))['targetId']
        session = self.wait_target(target)
        if self.app_mode and self.app_target is None:
            self.app_target = target
            # Chrome may also create a normal new-tab window when an app starts
            # from a previously used desktop profile. Keep one app window so the
            # Win32 embedder cannot accidentally dock that extra window.
            for page in pages:
                if page['type'] == 'page' and page['targetId'] != target:
                    self._close_extra_target(page['targetId'])
        # The Chrome Web Store requires the actual desktop browser environment.
        # Restore it before the store's first request; the game's pages stay mobile.
        if self.emulate_mobile and (desktop or urllib.parse.urlsplit(url).hostname == 'chromewebstore.google.com'):
            self._desktop_targets.add(target)
            self.connection.call('Emulation.clearDeviceMetricsOverride', session=session)
            self.connection.call('Emulation.setTouchEmulationEnabled', dict(enabled=False), session)
        self._navigation_sessions.add(session)
        try:
            result = self.connection.call('Page.navigate', dict(url=url), session)
            if result.get('errorText') == 'net::ERR_ABORTED':
                # A competing load can cancel the requested navigation. Stop it
                # and retry once in the same target, keeping this profile/proxy.
                self.connection.call('Page.stopLoading', {}, session)
                result = self.connection.call('Page.navigate', dict(url=url), session)
            if result.get('errorText'):
                raise ProtocolError('Page.navigate: ' + result['errorText'])
        finally:
            self._navigation_sessions.discard(session)
        if self.app_mode:
            for page in self.connection.call('Target.getTargets')['targetInfos']:
                if page['targetId'] != self.app_target and page['type'] == 'page' and page['url'].startswith(('chrome://newtab', 'chrome://new-tab-page')):
                    self._close_extra_target(page['targetId'])
        return target

    def close(self):
        if self.connection and not self.connection.closed.is_set():
            try:
                self.connection.call('Browser.close', timeout=3)
            except ProtocolError:
                pass
        try:
            if self.process and self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
        finally:
            if self.connection:
                if self.connection.job:
                    self.connection.job.close()
                self.connection.close()
