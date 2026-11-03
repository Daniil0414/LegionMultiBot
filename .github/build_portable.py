"""Build and verify a complete Windows x64 distribution from public components."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'accounts.json', 'userscript_config.json', 'launcher_config.json',
             'vpn_config.json', 'userscript-private.json', 'Cookies', 'Login Data',
             'History', 'Local State', '.vpn_runtime', '.git'}


def fetch(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'LegionMultiBot-Portable-Builder'})
    with urllib.request.urlopen(request, timeout=90) as response, target.open('wb') as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def download(component, cache):
    path = cache / (component['name'] + '.zip')
    if not path.exists():
        fetch(component['url'], path)
    digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != component['sha256']:
        raise ValueError('Component checksum mismatch: ' + component['name'])
    return component['name'], path


def copy_member(archive, name, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name) as source, target.open('wb') as output:
        shutil.copyfileobj(source, output)


def build(cache, output):
    if os.name != 'nt':
        raise RuntimeError('The portable Windows application must be built on Windows.')
    lock = json.loads((ROOT / '.github' / 'portable-components.json').read_text())
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        archives = dict(executor.map(lambda item: download(item, cache), lock['components']))
    with tempfile.TemporaryDirectory(prefix='legion-build-', dir=output) as temporary:
        temporary = Path(temporary)
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                   '--onedir', '--windowed', '--name', 'LegionMultiBot',
                   '--paths', str(ROOT), '--distpath', str(temporary / 'binary'),
                   '--workpath', str(temporary / 'work'), '--specpath', str(temporary),
                   '--hidden-import', 'happ_memory']
        for name in ('userscript_runtime.js', 'userscript_privacy.js', 'userscript_transform.js',
                     'vendor/acorn.js', 'vendor/acorn.LICENSE'):
            command += ['--add-data', str(ROOT / name) + os.pathsep + str(Path(name).parent)]
        command.append(str(ROOT / 'portable_entry.py'))
        subprocess.run(command, cwd=ROOT, check=True)
        distribution = temporary / 'LegionMultiBot'
        app = distribution / 'app'
        app.parent.mkdir(parents=True)
        shutil.move(str(temporary / 'binary' / 'LegionMultiBot'), app)
        runtime = app / 'runtime'
        components = {item['name']: item for item in lock['components']}
        node_prefix = 'node-' + components['node']['version'] + '-win-x64/'
        with zipfile.ZipFile(archives['node']) as archive:
            for name in ('node.exe', 'LICENSE'):
                copy_member(archive, node_prefix + name, runtime / 'node' / name)
        with zipfile.ZipFile(archives['chromium']) as archive:
            for member in archive.infolist():
                if member.is_dir() or Path(member.filename).name in {'interactive_ui_tests.exe', 'setup.exe'}:
                    continue
                target = (runtime / 'browser' / member.filename).resolve()
                if not target.is_relative_to((runtime / 'browser').resolve()):
                    raise ValueError('External archive contains an unsafe path')
                copy_member(archive, member.filename, target)
        with zipfile.ZipFile(archives['xray']) as archive:
            for name in ('xray.exe', 'geoip.dat', 'geosite.dat', 'wintun.dll', 'LICENSE', 'LICENSE-Wintun'):
                copy_member(archive, name, runtime / 'xray' / name)
        licenses = app / 'licenses'
        licenses.mkdir()
        for name in ('LICENSE.txt', 'LICENSE'):
            candidate = Path(sys.base_prefix) / name
            if candidate.is_file():
                shutil.copyfile(candidate, licenses / 'Python-LICENSE.txt')
                break
        fetch('https://raw.githubusercontent.com/chromium/chromium/main/LICENSE', licenses / 'Chromium-LICENSE')
        fetch('https://raw.githubusercontent.com/pyinstaller/pyinstaller/v' + lock['pyinstaller'] + '/COPYING.txt', licenses / 'PyInstaller-COPYING.txt')
        (app / 'COMPONENTS.json').write_text(json.dumps(lock, indent=2), encoding='utf-8')
        (licenses / 'README.txt').write_text(
            'Python and PyInstaller licenses are in this directory.\n'
            'Node.js: ../runtime/node/LICENSE\n'
            'Xray and Wintun: ../runtime/xray/LICENSE and LICENSE-Wintun\n'
            'Chromium third-party notices: chrome://credits in the included browser.\n'
            'Acorn: ../_internal/vendor/acorn.LICENSE\n'
            'Xray source: https://github.com/XTLS/Xray-core/tree/' + components['xray']['version'] + '\n', encoding='utf-8')
        shutil.copyfile(ROOT / 'README.md', distribution / 'README.md')
        (distribution / 'Запустить_Мультибот.bat').write_bytes((ROOT / 'Запустить_Мультибот.bat').read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        test_report = temporary / 'self-test.json'
        environment = dict(os.environ)
        windows = Path(environment.get('SystemRoot', r'C:\Windows'))
        environment['PATH'] = os.pathsep.join(map(str, (windows, windows / 'System32', windows / 'System32/WindowsPowerShell/v1.0', windows / 'System32/Wbem')))
        for name in ('PYTHONHOME', 'PYTHONPATH', 'NODE_PATH', 'TCL_LIBRARY', 'TK_LIBRARY'):
            environment.pop(name, None)
        process = subprocess.Popen([str(app / 'LegionMultiBot.exe'), '--portable-self-test', str(test_report)], env=environment, cwd=distribution)
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            raise RuntimeError('Portable application self-test timed out')
        result = json.loads(test_report.read_text()) if test_report.exists() else {'ok': False, 'error': 'Self-test did not create its report'}
        if process.returncode or not result.get('ok'):
            raise RuntimeError('Portable self-test failed: ' + json.dumps(result))
        (output / 'portable-self-test.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        paths = sorted(path for path in distribution.rglob('*') if path.is_file())
        for path in paths:
            if any(part in FORBIDDEN for part in path.relative_to(distribution).parts):
                raise RuntimeError('Private or generated state appeared in release: ' + path.name)
        assert [path.name for path in distribution.glob('*.bat')] == ['Запустить_Мультибот.bat']
        archive_path = output / 'LegionMultiBot-windows.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in paths:
                archive.write(path, 'LegionMultiBot/' + path.relative_to(distribution).as_posix())
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        (output / 'SHA256SUMS.txt').write_text(digest + '  ' + archive_path.name + '\n', encoding='ascii')
        print(json.dumps({'portable_release': str(archive_path), 'bytes': archive_path.stat().st_size,
                          'self_test': result, 'private_files': 0}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'build-cache')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    arguments = parser.parse_args()
    try:
        build(arguments.cache.resolve(), arguments.output.resolve())
    except Exception:
        details = traceback.format_exc().replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::error title=Portable build failed::' + details, flush=True)
        raise
