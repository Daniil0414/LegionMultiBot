"""Writable application directory and read-only resources for source and portable builds."""
from pathlib import Path
import shutil
import sys

RESOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else RESOURCE_DIR


def bundled_node():
    executable = APP_DIR / 'runtime' / 'node' / 'node.exe'
    return str(executable) if executable.is_file() else shutil.which('node')


def bundled_browser():
    executable = APP_DIR / 'runtime' / 'browser' / 'chrome-win' / 'chrome.exe'
    return str(executable) if executable.is_file() else None


def bundled_xray():
    return str(APP_DIR / 'runtime' / 'xray' / 'xray.exe')
