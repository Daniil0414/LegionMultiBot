"""Writable application directory and read-only resources for source and portable builds."""
from pathlib import Path
import os
import shutil
import sys

RESOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else RESOURCE_DIR


def data_directory(frozen=None):
    """Keep development state outside the source checkout; preserve portable layouts."""
    frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else frozen
    override = os.environ.get('LEGION_DATA_DIR')
    local = Path(os.environ.get('LOCALAPPDATA') or
                 (Path.home() / 'AppData/Local' if os.name == 'nt' else Path.home() / '.local/share'))
    directory = Path(override).expanduser() if override else (
        APP_DIR if frozen else local / 'LegionMultiBot' / 'Development')
    directory = directory.resolve()
    if not frozen and directory.is_relative_to(RESOURCE_DIR.resolve()):
        raise ValueError('LEGION_DATA_DIR must be outside the source repository')
    return directory


def profile_directory(frozen=None):
    frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else frozen
    if not frozen or os.environ.get('LEGION_DATA_DIR'):
        return data_directory(frozen) / 'Bots'
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    return local / 'LegionMultiBot' / 'Bots'


DATA_DIR = data_directory()


def bundled_node():
    executable = APP_DIR / 'runtime' / 'node' / 'node.exe'
    return str(executable) if executable.is_file() else shutil.which('node')


def bundled_browser():
    executable = APP_DIR / 'runtime' / 'browser' / 'chrome-win' / 'chrome.exe'
    return str(executable) if executable.is_file() else None


def bundled_xray():
    return str(APP_DIR / 'runtime' / 'xray' / 'xray.exe')
