"""Shutdown must attempt all owned resources even when individual cleanup fails."""
import os
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from browser_profiles import BrowserProfiles
from proxy_manager import ProxyManager
import secure_launcher
from vpn_runtime import process_identity, terminate_verified_process


class CleanupTests(unittest.TestCase):
    def test_gateway_failure_does_not_leave_other_gateways_or_core_running(self):
        manager = ProxyManager.__new__(ProxyManager)
        manager._closed = threading.Event()
        manager._lock = threading.RLock()
        manager.vpn_dispatcher = Mock()
        first, second = Mock(), Mock()
        first.close.side_effect = OSError('gateway close failed')
        manager._failover_routes = {'first': (None, first), 'second': (None, second)}
        with self.assertRaisesRegex(OSError, 'gateway close failed'):
            manager.close()
        self.assertTrue(manager._closed.is_set())
        second.close.assert_called_once()
        manager.vpn_dispatcher.stop_xray.assert_called_once()
        manager.close()
        first.close.assert_called_once()

    def test_job_failure_does_not_skip_remaining_browser_jobs(self):
        profiles = BrowserProfiles.__new__(BrowserProfiles)
        profiles._state_lock = threading.Lock()
        profiles._lock = threading.RLock()
        profiles._generation = 0
        first, second = Mock(), Mock()
        first.connection.job.close.side_effect = OSError('job close failed')
        profiles._controllers = {'first': first, 'second': second}
        with self.assertRaisesRegex(OSError, 'job close failed'):
            profiles.close()
        second.close.assert_called_once()
        second.connection.job.close.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows launchers')
    def test_window_close_cleans_vpn_after_browser_failure_and_is_idempotent(self):
        import app_container
        import gui_launcher
        for module, app_class in ((app_container, app_container.HighQualityMultiBoxerApp),
                                  (gui_launcher, gui_launcher.ProfilePagerApp)):
            with self.subTest(app=app_class.__name__):
                callbacks = []
                app = SimpleNamespace(_shutting_down=False, _browser_focus=Mock(),
                    _status_spinner_timer=None, browser_profiles=Mock(), proxy_manager=Mock(),
                    withdraw=Mock(), destroy=Mock(), after=lambda ms, fn: callbacks.append(fn))
                app.browser_profiles.shutdown.side_effect = OSError('browser close failed')
                with patch.object(module.threading, 'Thread') as thread, patch.object(module.logging if module is gui_launcher else module.LAUNCH_LOG, 'getLogger' if module is gui_launcher else 'exception'):
                    app_class.shutdown_app(app)
                    thread.call_args.kwargs['target']()
                    callbacks.pop()()
                    app_class.shutdown_app(app)
                app.proxy_manager.close.assert_called_once()
                app.browser_profiles.shutdown.assert_called_once()
                app.destroy.assert_called_once()


class BridgeShutdownTests(unittest.TestCase):
    def run_bridge(self, finish=None, failure=None):
        manager, profiles = Mock(), Mock()
        profiles.shutdown.side_effect = failure
        lines = [json.dumps({'key': 'fixture'})]
        if finish is not None:
            lines.append(json.dumps({'action': 'finish', 'wait_for_browsers': finish}))
        session = {'pid': 4321, 'identity': {'pid': 4321}}
        with (patch.object(secure_launcher, 'ProxyManager', return_value=manager),
              patch.object(secure_launcher, 'BrowserProfiles', return_value=profiles),
              patch.object(secure_launcher, 'launch_request', return_value=session),
              patch.object(secure_launcher, 'process_identity', side_effect=[session['identity'], None]),
              patch.object(secure_launcher.time, 'sleep') as sleep,
              patch.object(sys, 'argv', ['secure_launcher.py']),
              patch.object(sys, 'stdin', io.StringIO('\n'.join(lines) + '\n')),
              patch.object(sys, 'stdout', io.StringIO())):
            try:
                secure_launcher.main()
            finally:
                profiles.shutdown.assert_called_once()
                manager.close.assert_called_once()
        return sleep

    def test_stdin_eof_closes_live_browsers_instead_of_orphaning_them(self):
        self.run_bridge().assert_not_called()

    def test_explicit_quick_launch_finish_waits_until_browsers_close(self):
        self.run_bridge(finish=True).assert_called_once_with(1)

    def test_manager_finish_does_not_wait_for_live_browsers(self):
        self.run_bridge(finish=False).assert_not_called()

    def test_browser_cleanup_failure_still_closes_vpn(self):
        with self.assertRaisesRegex(OSError, 'bridge cleanup failed'):
            self.run_bridge(failure=OSError('bridge cleanup failed'))

    @unittest.skipUnless(os.name == 'nt', 'Windows process jobs')
    def test_killed_host_reaps_worker_and_its_owned_child(self):
        worker_code = '''
import os, subprocess, sys, time
from browser_lifecycle import BrowserJob, resume_browser
from secure_launcher import watch_parent
from vpn_runtime import atomic_json, process_identity
watch_parent(os.getppid())
job = BrowserJob()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],
    creationflags=4, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
job.attach(child)
resume_browser(child)
atomic_json(sys.argv[1], [process_identity(os.getpid()), process_identity(child.pid)])
time.sleep(300)
'''
        with tempfile.TemporaryDirectory(prefix='legion-host-shutdown-') as directory:
            output = Path(directory) / 'ready.json'
            host_code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-B","-c",sys.argv[1],sys.argv[2]]); time.sleep(300)'
            identities = []
            host = subprocess.Popen([sys.executable, '-B', '-c', host_code, worker_code, str(output)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not output.exists() and host.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(output.exists(), 'Worker did not initialize its child')
                identities = json.loads(output.read_text())
                self.assertTrue(all(identities))
                host.kill()
                host.wait(timeout=5)
                deadline = time.monotonic() + 5
                while any(process_identity(i['pid']) == i for i in identities) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse([i['pid'] for i in identities if process_identity(i['pid']) == i])
            finally:
                if host.poll() is None:
                    host.kill()
                    host.wait(timeout=5)
                for identity in identities:
                    if identity and process_identity(identity['pid']) == identity:
                        terminate_verified_process(identity)


if __name__ == '__main__':
    unittest.main()
