"""Exercise bundled runtimes without installing them or using existing profiles."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import subprocess
import tempfile
import threading
import time


def check():
    import tkinter as tk
    from app_paths import APP_DIR, bundled_browser, bundled_node, bundled_xray
    from app_container import CHROME_EXE
    from browser_profiles import BrowserProfiles
    from chrome_mobile import ProtocolError
    from userscripts import UserscriptManager
    from unittest.mock import patch

    assert CHROME_EXE == bundled_browser()
    for name in (bundled_browser(), bundled_node(), bundled_xray()):
        assert name and Path(name).resolve().is_relative_to(APP_DIR)
        assert Path(name).is_file()
    root = tk.Tk()
    root.withdraw()
    root.update()
    root.destroy()
    node = subprocess.run([bundled_node(), '--version'], capture_output=True, text=True, timeout=20, creationflags=0x08000000)
    xray = subprocess.run([bundled_xray(), 'version'], capture_output=True, text=True, timeout=20, creationflags=0x08000000)
    assert node.returncode == xray.returncode == 0

    class Page(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b'<!doctype html><title>Legion portable check</title><p id="ready">ready</p>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory(prefix='legion-portable-check-') as directory:
        directory = Path(directory)
        manager = UserscriptManager(directory / 'scripts.json')
        compiled = manager.for_launch()
        assert compiled and compiled['private_state']
        with patch('userscripts.download_script', side_effect=OSError('offline portable check')):
            assert manager.for_launch()['digest'] == compiled['digest']
        server = ThreadingHTTPServer(('127.0.0.1', 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        profiles = BrowserProfiles(root=directory / 'profiles', userscripts=manager, allow_direct=True)
        try:
            with patch('userscripts.download_script', side_effect=OSError('offline portable check')):
                profiles.launch(CHROME_EXE, 'portable-check', 'DIRECT', [
                    '--headless=new', '--disable-gpu', f'http://127.0.0.1:{server.server_port}/'])
            browser = profiles._controllers['portable-check']
            ready = False
            for _ in range(100):
                # Startup can replace the initial blank page and its session.
                pages = browser.connection.call('Target.getTargets')['targetInfos']
                target = next((page['targetId'] for page in pages if page['type'] == 'page'
                               and page['url'].startswith(f'http://127.0.0.1:{server.server_port}/')), None)
                if target:
                    try:
                        session = browser.wait_target(target)
                        value = browser.connection.call('Runtime.evaluate', dict(
                            expression="document.getElementById('ready')?.textContent", returnByValue=True), session)
                        if value.get('result', {}).get('value') == 'ready':
                            ready = True
                            break
                    except ProtocolError:
                        pass
                time.sleep(0.1)
            assert ready, 'Bundled Chromium did not display the local test page'
        finally:
            profiles.shutdown()
            server.shutdown()
            server.server_close()
    return {'ok': True, 'tk': True, 'node': node.stdout.strip(),
            'xray': xray.stdout.splitlines()[0], 'bundled_browser': True,
            'script_auto_install': True, 'offline_script_cache': True,
            'local_browser_page': True, 'uses_system_python_or_node': False}
