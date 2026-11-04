"""JSON-line bridge for legacy launchers; keep an owned VPN alive for their browsers."""
import json
import os
from pathlib import Path
import sys
import time
import threading
import urllib.parse

from browser_profiles import BrowserProfiles
from proxy_manager import ProxyManager
from vpn_runtime import process_identity

from app_paths import DATA_DIR


def watch_parent(parent_pid):
    """A killed Node host cannot run cleanup; exiting releases our browser/core jobs."""
    parent = process_identity(parent_pid)
    own = process_identity(os.getpid())
    if not parent or not own or int(parent['created']) > int(own['created']):
        raise SystemExit('Launcher host already stopped')
    stopped = threading.Event()

    def monitor():
        while not stopped.wait(.25):
            if process_identity(parent_pid) != parent:
                os._exit(1)
                return

    threading.Thread(target=monitor, name='launcher-host-watch', daemon=True).start()
    return stopped


def find_chrome():
    for candidate in (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                      str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe')):
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError('Chrome executable was not found')


def launch_request(manager, profiles, request):
    key = request['key']
    slot = int(request.get('slot', 0))
    if slot < 0:
        raise ValueError('Invalid slot index')
    url = request.get('url', 'https://play.multidonate.ru/c/zakvielchannel/')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {'https', 'http'} or not parsed.hostname:
        raise ValueError('An HTTP or HTTPS target URL is required')
    arguments = request.get('arguments', [])
    if not isinstance(arguments, list) or not all(isinstance(x, str) for x in arguments):
        raise ValueError('Invalid browser arguments')
    # Validate isolation arguments before opening connections or starting processes.
    profiles.directory(key)
    proxy = manager.get_resilient_proxy_for_slot(profiles.proxy_slot(key, slot), profile_key=key)
    if not proxy or proxy == 'ERROR_ALL_DOWN':
        raise ConnectionError('VPN не подключен; запуск браузера заблокирован')
    session = profiles.launch(find_chrome(), key, proxy, [*arguments, url])
    return session


def main():
    parent_pid = next((int(arg.split('=', 1)[1]) for arg in sys.argv[1:]
                       if arg.startswith('--parent-pid=')), None)
    parent_watch = watch_parent(parent_pid) if parent_pid else None
    manager = ProxyManager(str(DATA_DIR))
    profiles = BrowserProfiles()
    sessions = []
    wait_for_browsers = False
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                action = request.get('action', 'launch')
                if action == 'finish':
                    wait_for_browsers = request.get('wait_for_browsers') is True
                    break
                elif action == 'userscript-status':
                    result = {'userscript': profiles.userscripts.status()}
                elif action == 'userscript-install':
                    result = {'userscript': profiles.install_userscript(request['url'], request.get('script_id'))}
                elif action == 'userscript-disable':
                    result = {'userscript': profiles.disable_userscript(request.get('script_id'))}
                elif action == 'launch':
                    session = launch_request(manager, profiles, request)
                    sessions.append(session)
                    result = {'pid': session['pid'], 'userscript': session.get('userscript')}
                else:
                    raise ValueError('Unknown launcher action')
                result.update(id=request.get('id'), ok=True)
            except Exception as exc:
                result = {'id': request.get('id') if isinstance(request, dict) else None,
                          'ok': False, 'error': getattr(exc, 'user_message', str(exc))}
            print(json.dumps(result, ensure_ascii=True), flush=True)
        # The quick launcher keeps its console open until its browser batch ends.
        # Ordinary stdin EOF means the manager closed, so its browsers must close too.
        while wait_for_browsers and any(process_identity(s['pid']) == s['identity'] for s in sessions):
            time.sleep(1)
    finally:
        try:
            try:
                profiles.shutdown()
            finally:
                manager.close()
        finally:
            if parent_watch:
                parent_watch.set()


if __name__ == '__main__':
    main()
