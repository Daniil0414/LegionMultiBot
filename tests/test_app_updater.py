"""Update behavior, archive boundaries, and real Windows install/rollback checks."""
import hashlib
import gc
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

import app_updater as updater


def release_data(version='v1.2.0', portable=False):
    name = updater.WINDOWS_ASSET if portable else updater.SOURCE_ASSET
    return dict(tag_name=version, body='Changes', assets=[dict(name=name, state='uploaded', size=42,
                digest='sha256:' + 'a' * 64,
                browser_download_url=f'https://github.com/{updater.REPOSITORY}/releases/download/{version}/{name}')])


class UpdateCheckTests(unittest.TestCase):
    def test_version_comparison_does_not_compare_strings_or_downgrade(self):
        self.assertIsNotNone(updater.select_release(release_data('v1.10.0'), '1.9.0'))
        for tag in ('v1.1.2', 'v1.1.1', 'v1.2.0-beta.1', 'not-a-version'):
            self.assertIsNone(updater.select_release(release_data(tag), '1.1.2'))
        for flag in ('draft', 'prerelease'):
            data = release_data()
            data[flag] = True
            self.assertIsNone(updater.select_release(data, '1.1.2'))

    def test_chooses_matching_asset_and_source_fallback_is_tagged(self):
        data = release_data(portable=True)
        release = updater.select_release(data, '1.1.2', portable=True)
        self.assertEqual(release.kind, 'portable')
        self.assertTrue(release.url.endswith(updater.WINDOWS_ASSET))
        source = updater.select_release(data, '1.1.2', portable=False)
        self.assertTrue(source.wrapped)
        self.assertEqual(source.url, f'https://api.github.com/repos/{updater.REPOSITORY}/zipball/v1.2.0')
        self.assertIsNone(updater.select_release(release_data(), '1.1.2', portable=True))

    def test_rejects_wrong_repository_missing_checksum_and_bad_size(self):
        for changes in (dict(browser_download_url='https://github.com/other/repo/file.zip'),
                        dict(digest=None), dict(size=-1)):
            data = release_data()
            data['assets'][0].update(changes)
            with self.assertRaises(ValueError):
                updater.select_release(data, '1.1.2')

    def test_404_is_no_release_but_network_failure_is_not_a_release(self):
        with patch.object(updater, 'open_github', side_effect=urllib.error.HTTPError('url', 404, '', {}, None)):
            self.assertIsNone(updater.check_for_update())
        with patch.object(updater, 'open_github', side_effect=TimeoutError('offline')):
            with self.assertRaisesRegex(TimeoutError, 'offline'):
                updater.check_for_update()

    def test_https_and_redirect_destinations_are_restricted(self):
        for url in ('http://github.com/archive.zip', 'https://github.com.evil.test/x',
                    'https://github.com:8443/x', 'https://user:secret@github.com/x', 'file:///C:/app.zip'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                updater.validate_download_url(url)
        updater.validate_download_url('https://release-assets.githubusercontent.com/file.zip?token=public')


class FakeResponse(io.BytesIO):
    def __init__(self, body, size=None):
        super().__init__(body)
        self.headers = {'Content-Length': str(len(body) if size is None else size)}


class DownloadTests(unittest.TestCase):
    def test_checksum_truncated_download_and_cancel_are_checked(self):
        data = b'archive fixture'
        release = updater.Release('1.2.0', 'https://github.com/file', digest=hashlib.sha256(data).hexdigest(), size=len(data))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'release.zip'
            with patch.object(updater, 'open_github', return_value=FakeResponse(data)):
                updater.download_release(release, target)
                self.assertEqual(target.read_bytes(), data)
            with patch.object(updater, 'open_github', return_value=FakeResponse(b'wrong')):
                with self.assertRaisesRegex(ValueError, 'не полностью'):
                    updater.download_release(release, target)
            with patch.object(updater, 'open_github', return_value=FakeResponse(b'x' * len(data))):
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    updater.download_release(release, target)
            cancelled = threading.Event()
            cancelled.set()
            with patch.object(updater, 'open_github', return_value=FakeResponse(data)):
                with self.assertRaises(updater.UpdateCancelled):
                    updater.download_release(release, target, cancelled=cancelled)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'installed'
        self.target.mkdir()
        self.release = updater.Release('1.2.0', 'https://github.com/file')

    def archive(self, extra=None, wrapped=False):
        files = dict.fromkeys(('app_container.py', 'app_updater.py', 'app_update_ui.py'), '# source\n')
        files['app_version.py'] = "APP_VERSION = '1.2.0'\n"
        files.update(extra or {})
        path = self.root / 'release.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in files.items():
                entry = zipfile.ZipInfo()
                entry.filename = ('owner-repo-sha/' if wrapped else '') + name
                archive.writestr(entry, value)
        return path

    def test_sources_are_staged_without_replacing_code_or_private_data(self):
        (self.target / 'app_container.py').write_text('old source')
        private = ('accounts.json', 'vpn_config.json', '.vpn_runtime/keys.json',
                   'profiles/Cookies', 'userscript_config.json', 'proxies.txt', '.git/config')
        archive = self.archive({**dict.fromkeys(private, 'must never be installed'), 'public/app.js': 'new UI'})
        payload = updater.extract_release(archive, self.root / 'payload', self.target, self.release)
        self.assertEqual((self.target / 'app_container.py').read_text(), 'old source')
        self.assertTrue((payload / 'public/app.js').is_file())
        for name in private:
            self.assertFalse((payload / name).exists(), name)

    def test_tagged_source_wrapper_is_removed_and_version_must_match(self):
        release = updater.Release('1.2.0', 'https://github.com/file', wrapped=True)
        payload = updater.extract_release(self.archive(wrapped=True), self.root / 'payload', self.target, release)
        self.assertTrue((payload / 'app_container.py').is_file())
        with self.assertRaisesRegex(ValueError, 'не совпадает'):
            updater.extract_release(self.archive({'app_version.py': "APP_VERSION='0.1.0'"}),
                                    self.root / 'wrong-version', self.target, self.release)

    def test_rejects_traversal_absolute_ads_and_windows_reserved_paths(self):
        for name in ('../outside.py', '/outside.py', 'public/../../outside.py', 'C:/outside.py',
                     'public\\outside.py', 'public/file.js:stream', 'public/CON.js', 'public/foo./x.js'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                updater.extract_release(self.archive({name: 'bad'}), self.root / 'payload', self.target, self.release)
        self.assertFalse((self.root / 'outside.py').exists())

    def test_rejects_symlink_duplicate_case_and_incomplete_archives(self):
        path = self.archive()
        with zipfile.ZipFile(path, 'a') as archive:
            entry = zipfile.ZipInfo('public/link.js')
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, '../private')
        with self.assertRaisesRegex(ValueError, 'Ссылки'):
            updater.extract_release(path, self.root / 'links', self.target, self.release)
        with self.assertRaisesRegex(ValueError, 'Повторяющийся'):
            updater.extract_release(self.archive({'APP_CONTAINER.PY': '# duplicate'}),
                                    self.root / 'duplicates', self.target, self.release)
        empty = self.root / 'empty.zip'
        with zipfile.ZipFile(empty, 'w'):
            pass
        with self.assertRaisesRegex(ValueError, 'полную версию'):
            updater.extract_release(empty, self.root / 'empty', self.target, self.release)

    def test_portable_layout_preserves_app_state_and_includes_components(self):
        path = self.root / 'portable.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in {'app/LegionMultiBot.exe': b'MZfixture', 'app/runtime/node/node.exe': b'node',
                                'app/accounts.json': b'private', 'app/.vpn_runtime/keys.json': b'private',
                                'app/vpn_config.json': b'empty', 'Запустить_Мультибот.bat': b'launcher'}.items():
                archive.writestr(name, value)
        release = updater.Release('1.2.0', 'https://github.com/file', kind='portable')
        payload = updater.extract_release(path, self.root / 'portable', self.target, release)
        self.assertTrue((payload / 'app/runtime/node/node.exe').exists())
        self.assertFalse((payload / 'app/accounts.json').exists())
        self.assertFalse((payload / 'app/.vpn_runtime').exists())
        self.assertFalse((payload / 'app/vpn_config.json').exists())

    def test_failed_preparation_cleans_staging(self):
        directory = self.root / 'stage'
        directory.mkdir()
        with (patch.object(updater.tempfile, 'mkdtemp', return_value=str(directory)),
              patch.object(updater, 'download_release', side_effect=TimeoutError('offline'))):
            with self.assertRaises(TimeoutError):
                updater.prepare_update(self.release, self.target)
        self.assertFalse(directory.exists())

    def test_portable_release_accepts_the_published_top_level_folder(self):
        path = self.root / 'portable-wrapped.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('LegionMultiBot/app/LegionMultiBot.exe', b'MZfixture')
            archive.writestr('LegionMultiBot/app/runtime/node/node.exe', b'node')
            archive.writestr('LegionMultiBot/app/accounts.json', b'private')
            archive.writestr('LegionMultiBot/Запустить_Мультибот.bat', b'launcher')
        release = updater.Release('1.2.0', 'https://github.com/file', kind='portable')
        payload = updater.extract_release(path, self.root / 'wrapped', self.target, release)
        self.assertTrue((payload / 'app/LegionMultiBot.exe').is_file())
        self.assertTrue((payload / 'Запустить_Мультибот.bat').is_file())
        self.assertFalse((payload / 'app/accounts.json').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows installer')
class WindowsInstallerTests(unittest.TestCase):
    def install(self, failure=False):
        with tempfile.TemporaryDirectory(prefix='legion-installer-test-') as temp:
            root = Path(temp)
            target, payload, work = root / 'папка приложения', root / 'payload', root / 'helper'
            target.mkdir()
            payload.mkdir()
            work.mkdir()
            (target / 'a.py').write_text('old')
            (target / 'accounts.json').write_text('PRIVATE')
            (payload / 'a.py').write_text('new')
            (payload / 'b.py').write_text('added')
            if failure:
                # A directory occupying a file's destination forces rollback after a.py was installed.
                (target / 'b.py').mkdir()
            marker = root / 'restarted.txt'
            stop = root / 'exit-parent'
            parent = subprocess.Popen([sys.executable, '-c',
                'import time; from pathlib import Path\n'
                f'while not Path({str(stop)!r}).exists(): time.sleep(0.05)'])
            self.addCleanup(lambda: parent.poll() is None and parent.kill())
            plan = dict(parent_pid=parent.pid, payload=str(payload), target=str(target), notify=False,
                        restart_exe=sys.executable, restart_arguments=subprocess.list2cmdline([
                            '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').write_text("yes")']))
            (work / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
            script = work / 'install.ps1'
            script.write_text(updater.INSTALLER_SCRIPT, encoding='utf-8-sig')
            process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                        'Bypass', '-File', str(script), '-PlanPath', str(work / 'plan.json')],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            deadline = time.monotonic() + 8
            while not (work / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((work / 'ready').exists(), 'Installer did not acquire the parent process handle')
            self.assertEqual((target / 'a.py').read_text(), 'old', 'Files changed before application exit')
            self.assertFalse(marker.exists(), 'A second instance started before application exit')
            stop.write_text('exit')
            output, _ = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, output[-2000:])
            result = json.loads((work / 'result.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(result['ok'], not failure, result)
            self.assertEqual((target / 'a.py').read_text(), 'old' if failure else 'new')
            self.assertEqual((target / 'accounts.json').read_text(), 'PRIVATE')
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), 'Application was not restarted')
            parent.wait(timeout=5)

    def test_install_waits_for_parent_replaces_files_and_restarts(self):
        self.install()

    def test_failed_install_restores_previous_files_and_restarts_old_version(self):
        self.install(failure=True)


@unittest.skipUnless(os.name == 'nt', 'Tk startup prompt')
class UpdateDialogTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from app_update_ui import StartupUpdater
        self._gc_was_enabled = gc.isenabled()
        gc.disable()
        self.root = tk.Tk()
        self.root.withdraw()
        self.controller = StartupUpdater(self.root, Path(__file__).parent.parent, Mock())
        self.addCleanup(self.cleanup_tk)
        self.release = updater.Release('1.2.0', 'https://github.com/file', notes='New release')

    def cleanup_tk(self):
        # Tk interpreters retained by callback/mock cycles must be collected on the UI thread.
        self.root.destroy()
        self.controller = None
        self.root = None
        gc.collect()
        if self._gc_was_enabled:
            gc.enable()

    def test_offer_has_exact_actions_and_skip_never_downloads(self):
        with patch.object(self.controller, '_run') as run:
            self.controller._show_offer(self.release)
            self.assertEqual(self.controller.update_button.cget('text'), 'Обновить и перезапустить')
            self.assertEqual(self.controller.skip_button.cget('text'), 'Не обновлять')
            self.controller.skip_button.invoke()
            self.assertTrue(self.controller.cancelled.is_set())
            run.assert_not_called()
            self.controller.on_restart.assert_not_called()

    def test_update_downloads_only_after_click_and_cancel_discards_queued_payload(self):
        with patch.object(self.controller, '_run') as run:
            self.controller._show_offer(self.release)
            run.assert_not_called()
            self.controller.update_button.invoke()
            self.assertEqual(run.call_args.args[0], 'prepared')
            staged = Mock()
            self.controller.events.put(('prepared', staged))
            self.controller.skip_button.invoke()
            staged.discard.assert_called_once()
            self.controller.on_restart.assert_not_called()

    def test_check_failure_or_no_new_version_never_shows_dialog(self):
        for event in (('check_error', 'offline'), ('checked', None)):
            self.controller.events.put(event)
            self.controller._poll()
            self.assertIsNone(self.controller.dialog)

    def test_offer_waits_until_other_modal_window_closes(self):
        self.controller.events.put(('checked', self.release))
        with patch.object(self.root, 'grab_current', return_value=Mock()):
            self.controller._poll()
        self.assertIsNone(self.controller.dialog)
        self.assertFalse(self.controller.events.empty())
        self.controller._poll()
        self.assertIsNotNone(self.controller.dialog)

    def test_restart_is_requested_only_when_separate_installer_is_ready(self):
        self.controller._show_offer(self.release)
        staged, helper = Mock(), Mock()
        self.controller.events.put(('prepared', staged))
        with patch.object(self.controller, '_run') as run:
            self.controller._poll()
            run.assert_called_once_with('ready', __import__('app_update_ui').start_installer,
                                        staged, 'app_container.py')
        self.controller.on_restart.assert_not_called()
        self.assertEqual(self.controller.skip_button.cget('state'), 'disabled')
        self.controller.events.put(('ready', helper))
        self.controller._poll()
        self.controller.on_restart.assert_called_once()


if __name__ == '__main__':
    unittest.main()
