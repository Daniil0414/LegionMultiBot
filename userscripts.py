"""GitHub userscripts shared by every managed Chrome profile."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

from vpn_runtime import atomic_json, runtime_lock
from app_paths import data_directory

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SCRIPT_URL = 'https://github.com/Daniil0414/LegionAutoMiner/blob/main/mine-bot.user.js'
MAX_BYTES = 4 * 1024 * 1024


def github_raw_url(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Укажите ссылку на файл скрипта в GitHub.')
    parsed = urllib.parse.urlsplit(value.strip())
    if (parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'raw.githubusercontent.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Укажите HTTPS-ссылку на файл скрипта в GitHub.')
    parts = parsed.path.strip('/').split('/')
    if parsed.hostname == 'github.com':
        if len(parts) < 5 or parts[2] not in ('blob', 'raw'):
            raise ValueError('Откройте файл скрипта на GitHub и скопируйте его ссылку (или Raw).')
        parts.pop(2)
    if len(parts) < 4 or any(part in ('', '.', '..') for part in parts):
        raise ValueError('Нужна ссылка на файл скрипта, а не на репозиторий GitHub.')
    return 'https://raw.githubusercontent.com/' + '/'.join(parts)


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        github_raw_url(newurl)  # Reject redirects outside GitHub, including HTTP.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_script(url):
    request = urllib.request.Request(url + '?legion_update=' + str(time.time_ns()),
        headers={'Cache-Control': 'no-cache', 'User-Agent': 'LegionMultiBot/1.0'})
    with urllib.request.build_opener(GitHubRedirect()).open(request, timeout=15) as response:
        github_raw_url(response.geturl())
        if response.headers.get_content_type() == 'text/html':
            raise ValueError('GitHub вернул HTML вместо JavaScript. Проверьте ссылку на файл.')
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Скрипт больше 4 МБ.')
    return data.decode('utf-8-sig')


def match_regex(pattern):
    if pattern == '<all_urls>':
        return r'^https?://'
    match = re.fullmatch(r'(\*|https?)://([^/]+)(/.*)', pattern)
    if not match:
        raise ValueError('Неподдерживаемый @match: ' + pattern)
    scheme, host, path = match.groups()
    if '*' in host and host != '*' and not (host.startswith('*.') and '*' not in host[2:]):
        raise ValueError('Неподдерживаемый @match: ' + pattern)
    scheme = 'https?' if scheme == '*' else scheme
    host = (r'[^/]+' if host == '*' else r'(?:[^/.]+\.)*' + re.escape(host[2:])
            if host.startswith('*.') else re.escape(host))
    return '^' + scheme + '://' + host + r'(?::\d+)?' + re.escape(path).replace(r'\*', '.*') + '$'


def compile_script(source):
    if not source.strip() or source.lstrip().startswith('<') or '\0' in source:
        raise ValueError('Файл не содержит JavaScript.')
    meta = {}
    block = re.search(r'(?m)^\s*//\s*==UserScript==\s*\r?\n(.*?)^\s*//\s*==/UserScript==', source, re.S | re.M)
    if block:
        for key, value in re.findall(r'^[ \t]*//[ \t]*@([\w-]+)[ \t]*([^\r\n]*)', block[1], re.M):
            meta.setdefault(key, []).append(value.strip())
    unsupported = [grant for grant in meta.get('grant', []) if grant != 'none']
    if unsupported or any(key in meta for key in ('require', 'resource')):
        raise ValueError('Встроенный запуск поддерживает @grant none без @require и @resource.')
    run_at = meta.get('run-at', ['document-idle'])[-1]
    if run_at not in ('document-start', 'document-end', 'document-idle'):
        raise ValueError('Неподдерживаемый @run-at: ' + run_at)
    def glob(value):
        if value.startswith('/') and value.endswith('/'):
            raise ValueError('Регулярные выражения в @include/@exclude не поддерживаются.')
        return '^' + re.escape(value).replace(r'\*', '.*') + '$'
    from userscript_privacy import adapt_bot, supported_bot
    private_state = supported_bot(meta)
    adapted = adapt_bot(source) if private_state else None
    settings = dict(matches=[match_regex(p) for p in meta.get('match', [])],
        includes=[glob(p) for p in meta.get('include', [])],
        excludes=[glob(p) for p in meta.get('exclude', [])] +
                 [match_regex(p) for p in meta.get('exclude-match', [])],
        noframes='noframes' in meta, runAt=run_at, privateState=private_state,
        digest=hashlib.sha256(source.encode('utf-8')).hexdigest(),
        guard='userscript-' + secrets.token_urlsafe(24))
    template = (BASE_DIR / 'userscript_runtime.js').read_text(encoding='utf-8')
    helper = (BASE_DIR / 'userscript_privacy.js').read_text(encoding='utf-8') if private_state else 'null'
    bootstrap = template.replace('/*__SETTINGS__*/', json.dumps(settings), 1)
    bootstrap = bootstrap.replace('/*__PRIVACY_RUNTIME__*/null', helper, 1)
    bootstrap = bootstrap.replace('/*__SOURCE__*/', adapted['code'] if adapted else source, 1)
    return dict(bootstrap=bootstrap, digest=settings['digest'],
                name=meta.get('name', ['Скрипт бота'])[-1], version=meta.get('version', [''])[-1],
                private_state=private_state, privacy_changes=adapted['stats'] if adapted else {},
                mine_control=bool(adapted and (adapted['stats'].get('mineControls', 0) >= 1 or supported_bot(meta))))


class UserscriptManager:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_directory() / 'userscript_config.json'
        self._lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {'version': 2, 'scripts': [dict(
                id='legion-auto-miner', name='LegionAutoMiner',
                script_url=github_raw_url(DEFAULT_SCRIPT_URL),
                installed=True, pending_install=True, auto_update_on_launch=True,
            )]}
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Некорректный userscript_config.json; восстановите его из копии.')
        if 'scripts' not in value:
            # Migrate the previous single-script config without dropping its cache or state.
            value = {'version': 2, 'scripts': [dict(value, id='legacy')] if value.get('script_url') else []}
        if not isinstance(value['scripts'], list) or any(not isinstance(item, dict) or not item.get('id') for item in value['scripts']):
            raise ValueError('Некорректный список скриптов в userscript_config.json.')
        for entry in value['scripts']:
            if 'private_required' not in entry:
                entry['private_required'] = bool(re.search(
                    r'(?m)^\s*//\s*@namespace\s+https://github\.com/Daniil0414/LegionAutoMiner/?\s*$',
                    entry.get('source', '')))
        return value

    @staticmethod
    def _entry_status(config):
        return {key: config.get(key, default) for key, default in dict(
            id='', script_url='', installed=False, name='', version='', last_updated='', last_error='',
            using_cached=False, auto_update_on_launch=True).items()}

    @classmethod
    def _status(cls, config):
        entries = [cls._entry_status(entry) for entry in config['scripts']]
        status = cls._entry_status(config['scripts'][0] if entries else {})
        enabled = [entry for entry in entries if entry['installed']]
        status.update(scripts=entries, installed=bool(enabled), enabled_count=len(enabled),
            using_cached=any(entry['using_cached'] for entry in enabled),
            last_error='; '.join(entry['last_error'] for entry in enabled if entry['last_error']))
        return status

    def status(self):
        # Writers replace the entire file atomically; never block the GUI on a download.
        return self._status(self._read())

    @staticmethod
    def _download(url, entry):
        source = download_script(url)
        prepared = compile_script(source)
        if entry.get('private_required') and not prepared['private_state']:
            raise ValueError('Обновление бота изменило формат приватного состояния; используется сохранённая версия.')
        return dict(entry, script_url=url, source=source, installed=True, pending_install=False, auto_update_on_launch=True,
            private_required=prepared['private_state'],
            name=prepared['name'], version=prepared['version'], digest=prepared['digest'],
            last_updated=datetime.now(timezone.utc).isoformat(), last_error='', using_cached=False)

    @staticmethod
    def _bundle(config):
        scripts = []
        for entry in config['scripts']:
            if not entry.get('installed') or entry.get('pending_install'):
                continue
            source = entry.get('source', '')
            if not source or hashlib.sha256(source.encode('utf-8')).hexdigest() != entry.get('digest'):
                raise RuntimeError('Сохранённая версия скрипта отсутствует или повреждена: ' + entry.get('name', entry['id']))
            scripts.append((entry['id'], compile_script(source)))
        if not scripts:
            return None
        if len(scripts) == 1:
            return scripts[0][1]
        digest = hashlib.sha256(json.dumps([(key, script['digest']) for key, script in scripts]).encode('utf-8')).hexdigest()
        return dict(bootstrap=';\n'.join(script['bootstrap'] for _, script in scripts), digest=digest,
                    private_state=any(script.get('private_state') for _, script in scripts),
                    mine_control=any(script.get('mine_control')
                                     for _, script in scripts))

    def current(self):
        return self._bundle(self._read())

    def install(self, url, script_id=None):
        with self._lock, runtime_lock(str(self.path) + '.lock', timeout=90):
            config = self._read()
            url = github_raw_url(url)
            entries = config['scripts']
            if script_id is not None:
                entry = next((item for item in entries if item['id'] == script_id), None)
                if entry is None:
                    raise ValueError('Скрипт не найден. Обновите список.')
                if any(item['script_url'] == url and item['id'] != script_id for item in entries):
                    raise ValueError('Этот скрипт уже добавлен.')
            else:
                entry = next((item for item in entries if item.get('script_url') == url), None)
            if entry is None:
                entry = {'id': secrets.token_urlsafe(12)}
                entries.append(entry)
            updated = self._download(url, entry)
            config['scripts'] = [updated if item['id'] == entry['id'] else item for item in entries]
            prepared = self._bundle(config)
            atomic_json(str(self.path), config)
            return dict(prepared, selected_id=updated['id'])

    def disable(self, script_id=None):
        with self._lock, runtime_lock(str(self.path) + '.lock', timeout=90):
            config = self._read()
            entries = [entry for entry in config['scripts'] if script_id is None or entry['id'] == script_id]
            if script_id is not None and not entries:
                raise ValueError('Скрипт не найден. Обновите список.')
            for entry in entries:
                entry.update(installed=False, last_error='', using_cached=False)
            prepared = self._bundle(config)
            atomic_json(str(self.path), config)
            return prepared

    def for_launch(self):
        with self._lock, runtime_lock(str(self.path) + '.lock', timeout=90):
            config = self._read()
            changed = False
            for index, entry in enumerate(config['scripts']):
                if not entry.get('installed'):
                    continue
                changed = True
                try:
                    config['scripts'][index] = self._download(entry['script_url'], entry)
                except Exception as exc:
                    if entry.get('pending_install'):
                        # A first download has no cache; fail before opening the game and retry next launch.
                        raise
                    # Validate each cached script independently before allowing a launch.
                    self._bundle({'scripts': [entry]})
                    entry.update(last_error=str(exc), using_cached=True)
            prepared = self._bundle(config)
            if changed:
                atomic_json(str(self.path), config)
            return prepared
