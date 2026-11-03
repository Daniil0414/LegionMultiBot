import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import concurrent.futures
import types
import unittest
from unittest.mock import patch, Mock

import browser_profiles as bp
from vpn_runtime import atomic_json, process_identity


class ProfileLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='legion-lifecycle-')
        self.addCleanup(self.temp.cleanup)
        self.profiles = bp.BrowserProfiles(root=self.temp.name)
        self.addCleanup(self.profiles.shutdown)
        self.directory = self.profiles.prepare('account').resolve()
        self.executable = os.path.normcase(os.path.abspath('chrome.exe'))
        self.child = dict(pid=4321, created='20', executable=self.executable)
        self.entry = dict(ProcessId=4321, ParentProcessId=1234)

    def test_abandoned_legacy_profile_recovers_without_deleting_data(self):
        saved = self.directory / 'saved-data'
        saved.write_bytes(b'cookies and settings')
        with (patch.object(self.profiles, '_profile_browser', side_effect=[self.entry, self.entry, None]),
              patch.object(bp, 'process_identity', side_effect=[self.child, None]),
              patch.object(bp, 'process_exists', return_value=False),
              patch.object(bp, 'close_abandoned_browser') as close):
            self.assertTrue(self.profiles._recover_profile(self.directory, {}, self.executable))
            close.assert_called_once_with(self.child)
        self.assertEqual(saved.read_bytes(), b'cookies and settings')

    def test_live_owner_is_never_closed(self):
        owner = dict(pid=1234, created='10', executable='python.exe')
        for record in ({}, dict(identity=self.child, owner=owner)):
            with (patch.object(self.profiles, '_profile_browser', return_value=self.entry),
                  patch.object(bp, 'process_identity', side_effect=[self.child, owner]),
                  patch.object(bp, 'close_abandoned_browser') as close):
                self.assertFalse(self.profiles._recover_profile(self.directory, record, self.executable))
                close.assert_not_called()

    def test_inaccessible_owner_is_not_assumed_dead(self):
        with (patch.object(self.profiles, '_profile_browser', return_value=self.entry),
              patch.object(bp, 'process_identity', side_effect=[self.child, None]),
              patch.object(bp, 'process_exists', return_value=True),
              patch.object(bp, 'close_abandoned_browser') as close):
            self.assertFalse(self.profiles._recover_profile(self.directory, {}, self.executable))
            close.assert_not_called()

    def test_reused_parent_pid_does_not_block_orphan_recovery(self):
        parent = dict(pid=1234, created='30', executable='unrelated.exe')
        with (patch.object(self.profiles, '_profile_browser', side_effect=[self.entry, self.entry, None]),
              patch.object(bp, 'process_identity', side_effect=[self.child, parent]),
              patch.object(bp, 'close_abandoned_browser') as close):
            self.assertTrue(self.profiles._recover_profile(self.directory, {}, self.executable))
            close.assert_called_once_with(self.child)

    def test_changed_pid_and_external_profile_are_not_closed(self):
        with (patch.object(self.profiles, '_profile_browser', side_effect=[self.entry, dict(ProcessId=9999)]),
              patch.object(bp, 'process_identity', side_effect=[self.child, None]),
              patch.object(bp, 'process_exists', return_value=False),
              patch.object(bp, 'close_abandoned_browser') as close):
            with self.assertRaisesRegex(RuntimeError, 'changed during recovery'):
                self.profiles._recover_profile(self.directory, {}, self.executable)
            with self.assertRaisesRegex(RuntimeError, 'outside the bot directory'):
                self.profiles._recover_profile(self.directory / 'other', {}, self.executable)
            close.assert_not_called()

    def test_close_cancels_queued_launch_and_shutdown_blocks_new_work(self):
        generation = self.profiles.generation
        self.profiles.close()
        with patch.object(bp, 'MobileBrowser') as browser:
            with self.assertRaises(bp.LaunchCancelled):
                self.profiles.launch('chrome.exe', 'account', 'http://127.0.0.1:1234', [], generation=generation)
            self.profiles.shutdown()
            with self.assertRaises(bp.LaunchCancelled):
                self.profiles.launch('chrome.exe', 'account', 'http://127.0.0.1:1234', [])
            browser.assert_not_called()

    def test_cancellation_during_start_closes_child_before_registration(self):
        entered, resume = threading.Event(), threading.Event()
        controller = Mock()
        controller.start.return_value = types.SimpleNamespace(pid=4321)
        def navigate(url):
            entered.set()
            self.assertTrue(resume.wait(5))
        controller.navigate.side_effect = navigate
        with (patch.object(self.profiles, '_has_unmanaged_browser', return_value=False),
              patch.object(bp, 'MobileBrowser', return_value=controller),
              patch.object(bp, 'process_identity', return_value=self.child),
              concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool):
            task = pool.submit(self.profiles.launch, 'chrome.exe', 'account', 'http://127.0.0.1:1234', [])
            self.assertTrue(entered.wait(5))
            self.profiles.cancel_pending()
            resume.set()
            with self.assertRaises(bp.LaunchCancelled):
                task.result(5)
        controller.close.assert_called_once()
        self.assertFalse(self.profiles._controllers)
        self.assertFalse((self.directory / '.legion-session.json').exists())

    def test_close_attempts_every_controller_when_one_close_fails(self):
        first, second = Mock(), Mock()
        first.close.side_effect = OSError('fixture close failure')
        self.profiles._controllers = dict(first=first, second=second)
        with self.assertRaisesRegex(OSError, 'fixture close failure'):
            self.profiles.close()
        second.close.assert_called_once()
        self.assertFalse(self.profiles._controllers)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LEGION_TEST_CHROME'), 'Requires Windows and LEGION_TEST_CHROME')
class NativeLifecycleTests(unittest.TestCase):
    @staticmethod
    def cleanup_profile(temp):
        # Windows can finish outstanding file I/O just after the process is signalled.
        deadline = time.monotonic() + 5
        while True:
            try:
                temp.cleanup()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)

    def test_abrupt_launcher_exit_reaps_browser_job(self):
        temp = tempfile.TemporaryDirectory(prefix='legion-job-test-')
        self.addCleanup(self.cleanup_profile, temp)
        directory = temp.name
        if directory:
            output = Path(directory) / 'child.json'
            script = r"""
import json, os, pathlib, sys, time
from chrome_mobile import MobileBrowser, DEVICES
from vpn_runtime import process_identity
import subprocess
b = MobileBrowser(DEVICES[0])
p = b.start([sys.argv[1], '--user-data-dir=' + sys.argv[2], '--headless=new', '--no-first-run', 'about:blank'])
children = subprocess.check_output(['powershell', '-NoProfile', '-Command', 'Get-CimInstance Win32_Process -Filter "Name=\'chrome.exe\'" | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress'], creationflags=0x08000000, text=True)
entries = json.loads(children)
owned = {p.pid}
for _ in range(8):
    owned.update(e['ProcessId'] for e in entries if e['ParentProcessId'] in owned)
pathlib.Path(sys.argv[3]).write_text(json.dumps(dict(root=process_identity(p.pid), children=[process_identity(pid) for pid in owned if process_identity(pid)])))
time.sleep(60)
"""
            launcher = subprocess.Popen([sys.executable, '-B', '-c', script, os.environ['LEGION_TEST_CHROME'],
                                         str(Path(directory) / 'profile'), str(output)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            identity = None
            try:
                deadline = time.monotonic() + 20
                while not output.exists() and launcher.poll() is None and time.monotonic() < deadline:
                    time.sleep(.1)
                self.assertTrue(output.exists(), 'Launcher did not initialize Chrome')
                saved = json.loads(output.read_text())
                identity, identities = saved['root'], saved['children']
                self.assertEqual(process_identity(identity['pid']), identity)
                launcher.kill()
                launcher.wait(timeout=5)
                deadline = time.monotonic() + 10
                while any(process_identity(i['pid']) == i for i in identities) and time.monotonic() < deadline:
                    time.sleep(.1)
                self.assertFalse([i['pid'] for i in identities if process_identity(i['pid']) == i])
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.wait(timeout=5)
                if identity and process_identity(identity['pid']) == identity:
                    bp.close_abandoned_browser(identity, timeout=.2)


if __name__ == '__main__':
    unittest.main()
