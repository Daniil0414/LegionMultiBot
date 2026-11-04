"""Persistent, isolated Chrome instances with explicit proxy settings for each bot."""
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.parse
import json
import atexit
import random
from userscripts import UserscriptManager
from vpn_runtime import atomic_json, process_identity, runtime_lock
from chrome_mobile import MobileBrowser, device_for_profile
from browser_lifecycle import close_abandoned_browser, process_exists
from app_paths import profile_directory


class LaunchCancelled(RuntimeError):
    pass


class ProfileBusyError(RuntimeError):
    def __init__(self, message, user_message):
        super().__init__(message)
        self.user_message = user_message


def security_args(proxy, allow_direct=False):
    if proxy == 'DIRECT' and allow_direct:
        return ['--no-proxy-server', '--no-first-run', '--no-default-browser-check']
    if not proxy or proxy == 'ERROR_ALL_DOWN' or proxy == 'DIRECT':
        raise ValueError('VPN is required; direct browser launch is blocked')
    parsed = urllib.parse.urlsplit(str(proxy))
    if parsed.scheme not in {'socks5', 'socks5h', 'http', 'https'} or not parsed.hostname:
        raise ValueError('Invalid explicit VPN proxy')
    if not parsed.port or parsed.username or parsed.password:
        raise ValueError('Use a local Xray proxy without embedded credentials')
    value = str(proxy).replace('socks5h://', 'socks5://', 1)
    return [
        f'--proxy-server={value}',
        '--proxy-bypass-list=<-loopback>',
        '--disable-quic',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--disable-background-networking',
        '--disable-domain-reliability',
        '--dns-prefetch-disable',
        '--no-first-run',
        '--no-default-browser-check'
    ]


class BrowserProfiles:
    def __init__(self, root=None, source=None, userscripts=None, allow_direct=False, emulate_mobile=False):
        self.allow_direct = allow_direct
        self.emulate_mobile = bool(emulate_mobile)
        self.root = Path(root) if root else profile_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._controllers = {}
        self._mine_command_generation = 0
        self._mine_requested_at = 0
        self._mine_command_locks = {}
        self._mine_cancel = threading.Event()
        self._mine_state_lock = threading.Lock()
        self._mine_cancel_before = 0
        self.userscripts = userscripts if userscripts is not None else UserscriptManager()
        self._state_lock = threading.Lock()
        self._generation = 0
        self._stopped = False
        self._owner = process_identity(os.getpid())
        atexit.register(self.shutdown)

    @property
    def generation(self):
        with self._state_lock:
            return self._generation

    def cancel_pending(self):
        self.cancel_mine_commands()
        with self._state_lock:
            self._generation += 1

    def _check_launch(self, generation):
        with self._state_lock:
            if self._stopped or generation != self._generation:
                raise LaunchCancelled('Browser launch was cancelled')

    def directory(self, key):
        if not re.fullmatch(r'[A-Za-z0-9 _-]+', key):
            raise ValueError('Invalid account directory name')
        path = self.root / key
        return path

    def prepare(self, key):
        """Ensure clean isolated directory for bot account."""
        with self._lock:
            destination = self.directory(key)
            destination.mkdir(parents=True, exist_ok=True)
            return destination

    def apply_userscript(self, script):
        with self._lock:
            for controller in self._controllers.values():
                if not controller.connection.closed.is_set():
                    controller.set_userscript(script, reload=True)

    def install_userscript(self, url, script_id=None):
        with self._lock:
            script = self.userscripts.install(url, script_id)
            self.apply_userscript(script)
            return dict(self.userscripts.status(), selected_id=script['selected_id'])

    def disable_userscript(self, script_id=None):
        with self._lock:
            script = self.userscripts.disable(script_id)
            self.apply_userscript(script)
            return self.userscripts.status()

    def cancel_mine_commands(self, before=None):
        """Cancel queued input without waiting for a profile/VPN launch lock."""
        before = time.perf_counter_ns() if before is None else before
        with self._mine_state_lock:
            self._mine_cancel_before = max(self._mine_cancel_before, before)
            self._mine_cancel.set()

    def command_mine_all(self, action, requested_at=None, delay_range=(0, 0), profile_order=None, progress=None):
        """Visit each open page in account order, with interruptible delays."""
        if action not in ('start', 'pause'):
            raise ValueError('Unknown mine command')
        delay_range = tuple(delay_range)
        if delay_range not in ((0, 0), (1, 5), (1, 10)) or any(type(value) is not int for value in delay_range):
            raise ValueError('Unknown mine command delay')
        with self._mine_state_lock:
            if requested_at is None:
                requested_at = max(time.perf_counter_ns(), self._mine_requested_at + 1, self._mine_cancel_before + 1)
            if requested_at <= self._mine_requested_at or requested_at < self._mine_cancel_before:
                return []
            self._mine_requested_at = requested_at
            self._mine_cancel.set()
            cancel = self._mine_cancel = threading.Event()
            self._mine_command_generation += 1
            generation = self._mine_command_generation
        with self._lock:
            controllers = list(self._controllers.items())
            if profile_order:
                order = {key: index for index, key in enumerate(profile_order)}
                controllers.sort(key=lambda item: order.get(item[0], len(order)))
            locks = {key: self._mine_command_locks.setdefault(key, threading.Lock()) for key, _ in controllers}
        plan, results = [], []
        for key, controller in controllers:
            if cancel.is_set():
                return results
            try:
                if controller.connection.closed.is_set():
                    raise RuntimeError('Closed browser')
                plan.extend((key, controller, target) for target in controller.mine_targets())
            except Exception:
                results.append(dict(key=key, ok=False, error='unavailable'))

        def superseded(index):
            return results + [dict(key=key, target=target, ok=False, error='superseded')
                              for key, _, target in plan[index:]]

        for index, (key, controller, target) in enumerate(plan):
            if cancel.is_set():
                return superseded(index)
            if index and delay_range[1]:
                delay = random.randint(*delay_range)
                if progress:
                    progress(dict(completed=index, total=len(plan), delay=delay, key=key))
                if cancel.wait(delay):
                    return superseded(index)
            with locks[key]:
                with self._lock:
                    with self._mine_state_lock:
                        if cancel.is_set() or generation != self._mine_command_generation:
                            return superseded(index)
                    if self._controllers.get(key) is not controller or controller.connection.closed.is_set():
                        results.append(dict(key=key, target=target, ok=False, error='unavailable'))
                        continue
                try:
                    results.extend(dict(result, key=key, target=target)
                                   for result in controller.command_mine(action, target_id=target))
                except Exception:
                    results.append(dict(key=key, target=target, ok=False, error='unavailable'))
            if progress:
                progress(dict(completed=index + 1, total=len(plan), delay=0, key=key))
        return results

    def navigate_app(self, key, url, generation=None):
        """Navigate the existing app window without relaunching or changing its route."""
        target = urllib.parse.urlsplit(url)
        if target.scheme not in ('http', 'https') or not target.hostname or target.username or target.password:
            raise ValueError('An HTTP or HTTPS target URL without credentials is required')
        generation = self.generation if generation is None else generation
        with self._lock:
            self._check_launch(generation)
            controller = self._controllers.get(key)
            if not controller or controller.connection.closed.is_set() or not controller.has_app_window:
                raise ProfileBusyError('The managed app window is no longer available',
                                       'Связь с окном Chrome потеряна. Перезапустите этот профиль.')
            controller.navigate(url, target_id=controller.app_target)
            self._check_launch(generation)

    def launch(self, executable, key, proxy, arguments, generation=None):
        generation = self.generation if generation is None else generation
        self._check_launch(generation)
        forbidden = ('--proxy', '--no-proxy-server', '--user-data-dir', '--profile-directory',
                     '--host-resolver-rules', '--force-webrtc-ip-handling-policy', '--enable-quic',
                     '--remote-debugging', '--user-agent', '--touch-events', '--load-extension',
                     '--disable-extensions-except', '--enable-automation')
        if any(str(argument).startswith(forbidden) for argument in arguments):
            raise ValueError('Launch arguments must not override profile or proxy isolation')
        required = security_args(proxy, allow_direct=self.allow_direct)
        cleaned, urls = [], []
        for argument in arguments:
            if argument.startswith('--app='):
                urls.append(argument.split('=', 1)[1])
                cleaned.append('--app=data:text/html,')
            elif not argument.startswith('--'):
                urls.append(argument)
            else:
                cleaned.append(argument)
        if len(urls) > 1:
            raise ValueError('Only one initial browser URL is supported')
        target_url = urls[0] if urls else 'about:blank'
        if target_url != 'about:blank':
            target = urllib.parse.urlsplit(target_url)
            if target.scheme not in ('http', 'https') or not target.hostname or target.username or target.password:
                raise ValueError('An HTTP or HTTPS target URL without credentials is required')
        if not any(a.startswith('--app=') for a in cleaned):
            cleaned.append('about:blank')
        with self._lock:
            self._check_launch(generation)
            script = self.userscripts.for_launch()
            self._check_launch(generation)
            self.apply_userscript(script)
            directory = self.prepare(key).resolve()
            with runtime_lock(str(directory / '.legion-launch.lock')):
                record_path = directory / '.legion-session.json'
                record = {}
                try:
                    record = json.loads(record_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
                identity = record.get('identity')
                existing = bool(identity and process_identity(identity['pid']) == identity)
                controller = self._controllers.get(key)
                own_controller = bool(controller and not controller.connection.closed.is_set())
                if controller and not own_controller:
                    controller.close()
                    self._controllers.pop(key, None)
                    existing = bool(identity and process_identity(identity['pid']) == identity)
                recovered = False
                if existing and not own_controller:
                    recovered = self._recover_profile(directory, record, executable)
                    if recovered:
                        existing = False
                if existing and record.get('proxy') != proxy:
                    raise ProfileBusyError('Close this profile before changing its VPN route',
                                           'Профиль уже открыт с другим VPN-маршрутом. Закройте его окно Chrome и повторите запуск.')
                if existing:
                    if not controller or controller.connection.closed.is_set():
                        raise ProfileBusyError('Close this profile before starting native mobile emulation',
                                               'Этот аккаунт уже открыт в другом работающем экземпляре программы.')
                    app_requested = any(a.startswith('--app=') for a in cleaned)
                    if app_requested and not controller.has_app_window:
                        # Transition our own setup/login browser to an app window.
                        # A normal CDP-created window would retain the URL toolbar.
                        controller.close()
                        self._controllers.pop(key, None)
                        existing = False
                    else:
                        controller.navigate(target_url, new_window=not app_requested,
                                            target_id=controller.app_target if app_requested else None)
                        return {'pid': identity['pid'], 'identity': identity,
                                'proxy': proxy, 'directory': str(directory),
                                'device': dict(controller.device), 'mobile': self.emulate_mobile,
                                'userscript': self.userscripts.status(),
                                'reused_window': app_requested}
                if not existing and not recovered and self._has_unmanaged_browser(directory):
                    if not self._recover_profile(directory, record, executable):
                        raise ProfileBusyError('Close the previously opened profile before secure launch',
                                               'Этот аккаунт уже открыт другим работающим экземпляром программы.')
                self._check_launch(generation)
                previous = self._controllers.pop(key, None)
                if previous:
                    previous.close()
                legacy_profile = (directory / 'Default').is_dir()
                self._privacy_preferences(directory)
                controller = MobileBrowser(device_for_profile(directory) if self.emulate_mobile else {},
                                           cleanup_legacy=legacy_profile,
                                           storage_path=directory / 'userscript-private.json',
                                           emulate_mobile=self.emulate_mobile)
                controller.set_userscript(script)
                try:
                    process = controller.start(
                        [executable, f'--user-data-dir={directory}', '--disable-extensions', *required, *cleaned])
                    identity = process_identity(process.pid)
                    if not identity:
                        raise RuntimeError('Unable to verify the launched browser process')
                    controller.navigate(target_url)
                    self._check_launch(generation)
                    atomic_json(str(record_path), {'identity': identity, 'owner': self._owner,
                                                   'proxy': proxy, 'mobile': self.emulate_mobile})
                except BaseException:
                    controller.close()
                    raise
                self._controllers[key] = controller
                return {'pid': identity['pid'], 'identity': identity,
                        'proxy': proxy, 'directory': str(directory),
                        'device': dict(controller.device), 'mobile': self.emulate_mobile,
                        'userscript': self.userscripts.status()}

    def proxy_slot(self, key, suggested):
        """Use the current UI position only as a tie-breaker, never a binding."""
        self.directory(key)
        if type(suggested) is not int or suggested < 0:
            raise ValueError('Invalid proxy slot index')
        return suggested

    @staticmethod
    def _privacy_preferences(directory):
        """Apply Chrome's native WebRTC policy only while this profile is closed."""
        path = directory / 'Default' / 'Preferences'
        original = path.read_bytes() if path.exists() else None
        try:
            preferences = json.loads(original.decode('utf-8-sig')) if original else {}
            if not isinstance(preferences, dict) or any(not isinstance(preferences.get(k, {}), dict) for k in ('webrtc', 'net')):
                raise ValueError('Unexpected Chrome preference structure')
        except (UnicodeError, ValueError) as exc:
            raise ValueError('Invalid Chrome Preferences; the original file was preserved') from exc
        rtc = preferences.setdefault('webrtc', {})
        network = preferences.setdefault('net', {})
        if rtc.get('ip_handling_policy') == 'disable_non_proxied_udp' and network.get('network_prediction_options') == 2:
            return
        backup = directory / 'preferences-before-network-privacy.json'
        if original and not backup.exists():
            backup.write_bytes(original)
        rtc['ip_handling_policy'] = 'disable_non_proxied_udp'
        network['network_prediction_options'] = 2
        atomic_json(str(path), preferences)

    def close(self):
        self.cancel_pending()
        with self._lock:
            controllers, self._controllers = self._controllers, {}
            error = None
            try:
                for controller in controllers.values():
                    try:
                        controller.close()
                    except Exception as exc:
                        error = error or exc
            finally:
                # Keep strong references until every job has been released, even on error.
                for controller in controllers.values():
                    try:
                        if controller.connection and controller.connection.job:
                            controller.connection.job.close()
                    except Exception as exc:
                        error = error or exc
            if error:
                raise error

    def shutdown(self):
        with self._state_lock:
            self._stopped = True
        self.close()

    def _recover_profile(self, directory, record, executable):
        """Recover only our dedicated profile, after proving its previous owner is gone."""
        if directory.parent != self.root.resolve():
            raise RuntimeError('Profile recovery outside the bot directory is blocked')
        entry = self._profile_browser(directory)
        if not entry:
            return False
        identity = process_identity(entry['ProcessId'])
        if not identity or identity['executable'] != os.path.normcase(os.path.abspath(executable)):
            raise RuntimeError('Unable to verify the existing profile browser')
        owner = record.get('owner') if record.get('identity') == identity else None
        if owner:
            current = process_identity(owner['pid'])
            if current == owner or (current is None and process_exists(owner['pid'])):
                return False
        else:
            parent_pid = entry.get('ParentProcessId')
            if not parent_pid:
                return False
            parent = process_identity(parent_pid)
            if parent and int(parent['created']) <= int(identity['created']):
                return False
            if parent is None and process_exists(parent_pid):
                return False
        # Re-read the exact directory mapping before touching a captured PID.
        current = self._profile_browser(directory)
        if not current or current['ProcessId'] != identity['pid']:
            raise RuntimeError('Profile process changed during recovery')
        close_abandoned_browser(identity)
        if self._profile_browser(directory):
            raise RuntimeError('Profile remained busy after automatic recovery')
        return True

    def fit(self, key, width, height, zoom=1.0):
        controller = self._controllers.get(key)
        if controller:
            controller.fit(width, height, zoom=zoom)

    @staticmethod
    def _has_unmanaged_browser(directory):
        return BrowserProfiles._profile_browser(directory) is not None

    @staticmethod
    def _profile_browser(directory):
        if os.name != 'nt':
            return None
        script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                  "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=10, creationflags=0x08000000)
        if result.returncode:
            raise RuntimeError('Unable to check existing Chrome processes')
        entries = json.loads(result.stdout.strip() or '[]')
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries:
            command = entry.get('CommandLine') or ''
            if '--type=' in command:
                continue
            match = re.search(r'(?:"--user-data-dir=([^"]+)"|--user-data-dir="([^"]+)"|--user-data-dir=(\S+))', command)
            if match:
                path = next(x for x in match.groups() if x is not None)
                if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(directory)):
                    return entry
        return None
