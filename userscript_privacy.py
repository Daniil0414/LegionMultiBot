"""Private userscript settings in the profile directory, outside site storage."""
import functools
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import urllib.parse

from vpn_runtime import atomic_json, runtime_lock
from app_paths import bundled_node

BASE_DIR = Path(__file__).resolve().parent
PRIVATE_KEYS = frozenset(('lg_funded_drills_keys', 'lg_my_funded_drills_count',
    'lg_my_funded_drills_coins', 'lg_hero_role', 'lg_mining_cap_day', 'lg_last_friendcoin_date'))


def private_key(key):
    return isinstance(key, str) and len(key) <= 128 and (
        key in PRIVATE_KEYS or re.fullmatch(r'lg_(?:bot|quest|unlocked_sk)_[A-Za-z0-9_-]+', key) is not None)


def supported_bot(meta):
    return any(value.rstrip('/') == 'https://github.com/Daniil0414/LegionAutoMiner'
               for value in meta.get('namespace', []))


@functools.lru_cache(maxsize=4)
def adapt_bot(source):
    node = bundled_node()
    if not node:
        raise RuntimeError('Для защищённого хранения состояния бота необходим Node.js.')
    result = subprocess.run([node, str(BASE_DIR / 'userscript_transform.js')],
        input=source, capture_output=True, text=True, encoding='utf-8', timeout=20,
        creationflags=0x08000000 if sys.platform == 'win32' else 0)
    if result.returncode:
        raise ValueError('Не удалось подготовить приватное состояние бота: ' + result.stderr[:500])
    return json.loads(result.stdout)


class PrivateScriptStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _origin(origin):
        if not isinstance(origin, str) or len(origin) > 2048:
            raise ValueError('Invalid userscript origin')
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
                parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Invalid userscript origin')

    @staticmethod
    def _values(values):
        if not isinstance(values, dict) or len(values) > 256:
            raise ValueError('Invalid private userscript settings')
        if any(not private_key(k) or not isinstance(v, str) or len(v) > 65536 for k, v in values.items()):
            raise ValueError('Invalid private userscript setting')
        if sum(len(k) + len(v) for k, v in values.items()) > 262144:
            raise ValueError('Private userscript settings exceed the limit')

    def request(self, origin, request):
        self._origin(origin)
        if not isinstance(request, dict):
            raise ValueError('Invalid private storage request')
        with self._lock, runtime_lock(str(self.path) + '.lock'):
            try:
                state = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                state = {'version': 1, 'origins': {}}
            if (not isinstance(state, dict) or state.get('version') != 1 or
                    not isinstance(state.get('origins'), dict) or len(state['origins']) > 128):
                raise ValueError('Private userscript settings file is damaged')
            for saved_origin, values in state['origins'].items():
                self._origin(saved_origin)
                self._values(values)
            previous = state['origins'].get(origin, {})
            values = dict(previous)
            action = request.get('action')
            if action == 'load':
                legacy = request.get('legacy', {})
                self._values(legacy)
                for key, value in legacy.items():
                    values.setdefault(key, value)
            elif action in ('set', 'remove'):
                key = request.get('key')
                if not private_key(key):
                    raise ValueError('Only bot settings may use private storage')
                if action == 'set':
                    values[key] = request.get('value')
                else:
                    values.pop(key, None)
            else:
                raise ValueError('Unknown private storage action')
            self._values(values)
            if values != previous:
                if origin not in state['origins'] and len(state['origins']) >= 128:
                    raise ValueError('Too many private storage origins')
                state['origins'][origin] = values
                atomic_json(str(self.path), state)
            return {'values': values} if action == 'load' else {}


def bridge_source(config):
    """This script runs only in a named isolated world, never the page world."""
    return r'''(() => {
      const c = CONFIG;
      if (!/^https?:$/.test(location.protocol)) return;
      const send = window[c.binding].bind(window);
      const dispatch = EventTarget.prototype.dispatchEvent.bind(window);
      const stringify = JSON.stringify, parse = JSON.parse, EventClass = CustomEvent;
      if (typeof window[c.receiver] === 'function') { dispatch(new EventClass(c.ready)); return; }
      window[c.receiver] = packet => dispatch(new EventClass(c.reply, {detail: stringify(packet)}));
      window.addEventListener(c.request, event => {
        if (typeof event.detail !== 'string' || event.detail.length > 1048576) return;
        event.preventDefault();
        try { send(stringify({origin: location.origin, request: parse(event.detail)})); }
        catch (_) {}
      });
      dispatch(new EventClass(c.ready));
    })();'''.replace('CONFIG', json.dumps(config), 1)
