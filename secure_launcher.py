"""JSON-line bridge for legacy launchers; keep an owned VPN alive for their browsers."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse

from browser_profiles import BrowserProfiles
from proxy_manager import ProxyManager
from vpn_runtime import process_identity

BASE_DIR = Path(__file__).resolve().parent


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
    proxy = manager.get_working_proxy_for_slot(profiles.proxy_slot(key, slot), profile_key=key)
    if not proxy or proxy == 'ERROR_ALL_DOWN':
        raise ConnectionError('VPN не подключен; запуск браузера заблокирован')
    session = profiles.launch(find_chrome(), key, proxy, [*arguments, url])
    return session


def main():
    manager = ProxyManager(str(BASE_DIR))
    profiles = BrowserProfiles()
    sessions = []
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                action = request.get('action', 'launch')
                if action == 'userscript-status':
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
        # Node can exit after launching a batch. Do not tear its VPN down while
        # any of its browser processes still use that core.
        while any(process_identity(s['pid']) == s['identity'] for s in sessions):
            time.sleep(1)
    finally:
        profiles.close()
        manager.close()
        manager.vpn_dispatcher.stop_xray()


if __name__ == '__main__':
    main()
