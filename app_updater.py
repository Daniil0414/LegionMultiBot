"""GitHub release downloads and a separate Windows installer; no user state is replaced."""
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from app_version import APP_VERSION

REPOSITORY = 'Daniil0414/LegionMultiBot'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
SOURCE_ASSET = 'legion-multibot.zip'
WINDOWS_ASSET = 'LegionMultiBot-windows.zip'
DOWNLOAD_HOSTS = frozenset(('github.com', 'api.github.com', 'codeload.github.com',
                          'release-assets.githubusercontent.com', 'objects.githubusercontent.com'))
PRIVATE_NAMES = frozenset(('accounts.json', 'vpn_config.json', 'userscript_config.json',
                           'userscript_config.json.lock', 'launcher_config.json', 'proxies.txt'))
PRIVATE_DIRS = frozenset(('.git', '.vpn_runtime', '.app_update', 'profiles', 'bots', 'extensions', '__pycache__'))
MAX_ARCHIVE_SIZE = 1024 * 1024 * 1024
MAX_EXPANDED_SIZE = 3 * MAX_ARCHIVE_SIZE


class UpdateCancelled(Exception):
    pass


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match:
        raise ValueError(f'Неподдерживаемая версия: {value}')
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    notes: str = ''
    digest: str = ''
    size: int = 0
    kind: str = 'source'
    wrapped: bool = False


def select_release(data, current=APP_VERSION, portable=False):
    if data.get('draft') or data.get('prerelease'):
        return None
    tag = data.get('tag_name', '')
    try:
        version = version_tuple(tag)
    except ValueError:
        return None
    if version <= version_tuple(current):
        return None
    asset_name = WINDOWS_ASSET if portable else SOURCE_ASSET
    for asset in data.get('assets', []):
        if asset.get('name') != asset_name or asset.get('state') != 'uploaded':
            continue
        expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_name}'
        if asset.get('browser_download_url') != expected:
            raise ValueError('Адрес архива обновления не принадлежит репозиторию приложения.')
        digest = asset.get('digest') or ''
        if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
            raise ValueError('GitHub не предоставил SHA-256 архива обновления.')
        size = asset.get('size', 0)
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE_SIZE:
            raise ValueError('Некорректный размер архива обновления.')
        return Release(tag.lstrip('v'), expected, str(data.get('body') or '')[:4000],
                       digest.split(':', 1)[1].lower(), size, 'portable' if portable else 'source')
    if portable:
        return None  # Never install Python sources over a frozen Windows application.
    return Release(tag.lstrip('v'), f'https://api.github.com/repos/{REPOSITORY}/zipball/{tag}',
                   str(data.get('body') or '')[:4000], wrapped=True)


def validate_download_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in DOWNLOAD_HOSTS or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Обновления можно скачивать только по HTTPS с GitHub.')


class GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        validate_download_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def open_github(url, timeout=15):
    validate_download_url(url)
    request = urllib.request.Request(url, headers={
        'User-Agent': f'LegionMultiBot/{APP_VERSION}', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    response = urllib.request.build_opener(GitHubRedirects()).open(request, timeout=timeout)
    try:
        validate_download_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def check_for_update(current=APP_VERSION, portable=None):
    if portable is None:
        portable = bool(getattr(sys, 'frozen', False))
    try:
        with open_github(API_URL, timeout=10) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError('Слишком большой ответ GitHub.')
        return select_release(json.loads(body), current, portable)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def download_release(release, destination, progress=None, cancelled=None):
    cancelled = cancelled or threading.Event()
    limit = MAX_ARCHIVE_SIZE if release.kind == 'portable' else 64 * 1024 * 1024
    received, checksum, started = 0, hashlib.sha256(), time.monotonic()
    with open_github(release.url) as response, Path(destination).open('wb') as output:
        total = int(response.headers.get('Content-Length', '0')) or release.size
        if total < 0 or total > limit:
            raise ValueError('Слишком большой архив обновления.')
        while True:
            if cancelled.is_set():
                raise UpdateCancelled()
            if time.monotonic() - started > 1800:
                raise TimeoutError('Превышено время скачивания обновления.')
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                raise ValueError('Слишком большой архив обновления.')
            checksum.update(chunk)
            output.write(chunk)
            if progress:
                progress(received, total)
    if not received or (total and received != total) or (release.size and received != release.size):
        raise ValueError('Архив обновления скачан не полностью.')
    if release.digest and checksum.hexdigest() != release.digest:
        raise ValueError('SHA-256 архива обновления не совпадает с данными GitHub.')


def safe_member(name):
    if '\\' in name or ':' in name or name.startswith('/'):
        raise ValueError(f'Недопустимый путь в архиве: {name}')
    parts = name.rstrip('/').split('/')
    reserved = r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?'
    if any(not p or p in ('.', '..') or p.endswith((' ', '.')) or
           any(ord(c) < 32 or c in '<>"|?*' for c in p) or re.fullmatch(reserved, p)
           for p in parts):
        raise ValueError(f'Недопустимый путь в архиве: {name}')
    return PurePosixPath(*parts)


def is_program_file(path, portable=False):
    parts = tuple(part.lower() for part in path.parts)
    if any(part in PRIVATE_DIRS for part in parts) or parts[-1] in PRIVATE_NAMES:
        return False
    if portable:
        return (parts[0] == 'app' and len(parts) > 1) or (len(parts) == 1 and
                (parts[0].endswith(('.bat', '.md')) or parts[0] == 'sha256sums.txt'))
    if len(parts) == 1:
        return path.suffix.lower() in ('.py', '.js', '.bat', '.md') and not parts[0].endswith('.user.js')
    return parts[0] in ('public', 'vendor', 'stealth_shield', 'tests')


def validate_target(root, relative):
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Файл обновления выходит за папку приложения: {relative}')
    for item in (target, *target.parents):
        if item == root.parent:
            break
        if item.is_symlink() or (item.exists() and
                getattr(item.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f'Папка обновления содержит ссылку: {relative}')
    return target


def extract_release(archive_path, payload, target, release):
    payload, target = Path(payload), Path(target).resolve()
    payload.mkdir(parents=True, exist_ok=True)
    names, expanded, wrapper = set(), 0, None
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist()) > 30000:
            raise ValueError('Слишком много файлов в архиве обновления.')
        portable_wrapper = release.kind == 'portable' and all(
            entry.orig_filename.split('/')[0] == 'LegionMultiBot' for entry in archive.infolist())
        for entry in archive.infolist():
            # ZipInfo normalizes backslashes on Windows; validate the original spelling as well.
            relative = safe_member(entry.orig_filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Ссылки в архиве обновления запрещены.')
            if release.wrapped or portable_wrapper:
                if wrapper is None:
                    wrapper = relative.parts[0]
                if relative.parts[0] != wrapper:
                    raise ValueError('Некорректная структура архива GitHub.')
                if len(relative.parts) == 1:
                    continue
                relative = PurePosixPath(*relative.parts[1:])
            if entry.is_dir() or not is_program_file(relative, release.kind == 'portable'):
                continue
            key = str(relative).casefold()
            if key in names:
                raise ValueError(f'Повторяющийся файл в архиве: {relative}')
            names.add(key)
            expanded += entry.file_size
            if expanded > MAX_EXPANDED_SIZE:
                raise ValueError('Распакованное обновление слишком большое.')
            validate_target(target, relative)
            destination = payload.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open('wb') as output:
                shutil.copyfileobj(source, output, 256 * 1024)
    if release.kind == 'portable':
        executable = payload / 'app' / 'LegionMultiBot.exe'
        if not executable.is_file():
            raise ValueError('В обновлении нет приложения app/LegionMultiBot.exe.')
        with executable.open('rb') as source:
            if source.read(2) != b'MZ':
                raise ValueError('Повреждён файл приложения app/LegionMultiBot.exe.')
    else:
        required = ('app_container.py', 'app_updater.py', 'app_update_ui.py', 'app_version.py')
        if any(not (payload / name).is_file() for name in required):
            raise ValueError('Архив не содержит полную версию приложения с поддержкой обновлений.')
        version_source = (payload / 'app_version.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(version_source)
        versions = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'APP_VERSION' for t in node.targets)]
        if len(versions) != 1 or version_tuple(versions[0]) != version_tuple(release.version):
            raise ValueError('Версия файлов обновления не совпадает с версией релиза.')
        for source in payload.rglob('*.py'):
            compile(source.read_bytes(), str(source), 'exec')
    return payload


@dataclass
class PreparedUpdate:
    directory: Path
    payload: Path
    target: Path

    def discard(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def prepare_update(release, target, progress=None, cancelled=None):
    directory = Path(tempfile.mkdtemp(prefix='legion-update-'))
    prepared = PreparedUpdate(directory, directory / 'payload', Path(target).resolve())
    try:
        archive = directory / 'release.zip'
        download_release(release, archive, progress, cancelled)
        if cancelled and cancelled.is_set():
            raise UpdateCancelled()
        extract_release(archive, prepared.payload, prepared.target, release)
        archive.unlink()
        return prepared
    except BaseException:
        prepared.discard()
        raise


# Runs independently of Python/its frozen executable, after the parent and its mutex exit.
# All commands receive literal paths; no downloaded strings are evaluated as shell code.
INSTALLER_SCRIPT = r'''
param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$work = Split-Path -Parent $PlanPath
$changed = New-Object System.Collections.Generic.List[object]
$parentExited = $false
$installed = $false
function Assert-Target([string]$relative) {
    $root = [IO.Path]::GetFullPath($plan.target).TrimEnd('\')
    $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Update path is outside the application directory.'
    }
    $current = $path
    while ($current.Length -ge $root.Length) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Update path is a link.' }
        }
        $current = Split-Path -Parent $current
        if (-not $current) { break }
    }
    return $path
}
try {
    $parent = [Diagnostics.Process]::GetProcessById([int]$plan.parent_pid)
    $parent.Handle | Out-Null
    [IO.File]::WriteAllText((Join-Path $work 'ready'), 'ready')
    if (-not $parent.WaitForExit(180000)) { throw 'Application did not close in time. No files were changed.' }
    $parentExited = $true
    $files = @(Get-ChildItem -LiteralPath $plan.payload -Recurse -File -Force)
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($plan.payload.Length).TrimStart('\')
        $destination = Assert-Target $relative
        $backup = Join-Path (Join-Path $work 'backup') $relative
        $existed = Test-Path -LiteralPath $destination
        if ($existed) {
            [IO.Directory]::CreateDirectory((Split-Path -Parent $backup)) | Out-Null
            [IO.File]::Copy($destination, $backup, $true)
        }
        $temporary = $destination + '.legion-update-' + [Guid]::NewGuid().ToString('N')
        $changed.Add(@{ path=$destination; backup=$backup; existed=$existed; temporary=$temporary })
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        [IO.File]::Copy($file.FullName, $temporary, $true)
        if ($existed) { [IO.File]::Replace($temporary, $destination, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $destination) }
    }
    [IO.File]::WriteAllText((Join-Path $work 'result.json'), '{"ok":true}')
    $installed = $true
} catch {
    $failure = $_.Exception.Message
    $rollbackErrors = @()
    for ($index = $changed.Count - 1; $index -ge 0; $index--) {
        $entry = $changed[$index]
        try {
            if ($entry.existed) { [IO.File]::Copy($entry.backup, $entry.path, $true) }
            elseif (Test-Path -LiteralPath $entry.path) { [IO.File]::Delete($entry.path) }
            if (Test-Path -LiteralPath $entry.temporary) { [IO.File]::Delete($entry.temporary) }
        } catch { $rollbackErrors += $_.Exception.Message }
    }
    if ($rollbackErrors.Count) { $failure += "`nRollback: " + ($rollbackErrors -join '; ') }
    @{ ok=$false; error=$failure } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'result.json') -Encoding UTF8
    if ($plan.notify) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show("Не удалось установить обновление.`n$failure`nРезервная копия: $work", 'Legion Multi-Bot') | Out-Null
    }
    if (-not $parentExited -or $rollbackErrors.Count) { exit 1 }
}
try {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $plan.restart_exe
    $start.Arguments = $plan.restart_arguments
    $start.WorkingDirectory = $plan.target
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    [Diagnostics.Process]::Start($start) | Out-Null
} catch {
    if ($plan.notify) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show("Запустите приложение вручную.`n$($_.Exception.Message)", 'Legion Multi-Bot') | Out-Null
    }
    exit 1
}
if ($installed) {
    # Delete only this installer's own generated staging directory under the system temp folder.
    $resolvedWork = [IO.Path]::GetFullPath($work).TrimEnd('\')
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Split-Path -Parent $resolvedWork) -eq $tempRoot -and
        (Split-Path -Leaf $resolvedWork) -like 'legion-update-*') {
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force -ErrorAction SilentlyContinue
    }
}
'''


def installation_target(source_dir):
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve()
        if executable.parent.name.lower() != 'app' or executable.name.lower() != 'legionmultibot.exe':
            raise ValueError('Неизвестное расположение portable-приложения.')
        return executable.parent.parent
    return Path(source_dir).resolve()


def start_installer(prepared, entry_point='app_container.py'):
    if os.name != 'nt':
        raise OSError('Автоматическая установка доступна в Windows.')
    arguments = [] if getattr(sys, 'frozen', False) else [str(prepared.target / entry_point)]
    plan = dict(parent_pid=os.getpid(), payload=str(prepared.payload), target=str(prepared.target),
                restart_exe=sys.executable, restart_arguments=subprocess.list2cmdline(arguments), notify=True)
    plan_path = prepared.directory / 'plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    script = prepared.directory / 'install.ps1'
    script.write_text(INSTALLER_SCRIPT, encoding='utf-8-sig')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    process = subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                '-WindowStyle', 'Hidden', '-File', str(script), '-PlanPath', str(plan_path)],
                               creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(prepared.directory),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (prepared.directory / 'ready').is_file() and process.poll() is None:
            return process
        if process.poll() is not None:
            break
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError('Не удалось запустить установщик. Текущая версия сохранена.')
