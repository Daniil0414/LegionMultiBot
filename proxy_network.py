"""Verify an HTTPS response through an explicit proxy, without direct fallback."""
import ipaddress
import json
import logging
import subprocess
import sys
import urllib.parse

NETWORK_LOG = logging.getLogger('legion.launch.network')
NETWORK_LOG.addHandler(logging.NullHandler())


def fetch_proxy_ip(proxy_url, timeout=4):
    parsed = urllib.parse.urlsplit(str(proxy_url))
    if parsed.scheme not in {'socks5', 'socks5h', 'http', 'https'} or not parsed.hostname:
        raise ValueError('An explicit SOCKS5 or HTTP proxy is required')
    # SOCKS5h sends destination DNS queries through the proxy as well.
    proxy = str(proxy_url)
    if parsed.scheme == 'socks5':
        proxy = 'socks5h:' + proxy[len('socks5:'):]
    errors = []
    target = f'{parsed.scheme}://{parsed.hostname}:{parsed.port}'

    def record_failure(message):
        errors.append(message)
        NETWORK_LOG.warning('VPN HTTPS failure proxy=%s timeout=%s reason=%s', target, timeout, message)

    for name, url, as_json in (('api.ipify.org', 'https://api.ipify.org?format=json', True),
                               ('icanhazip.com', 'https://icanhazip.com', False)):
        try:
            result = subprocess.run(
                ['curl.exe', '--silent', '--show-error', '--fail', '--max-time', str(timeout),
                 '--connect-timeout', str(min(timeout, 6)), '--noproxy', '', '--proxy', proxy, url],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=timeout + 2, creationflags=0x08000000 if sys.platform == 'win32' else 0)
            if result.returncode:
                detail = getattr(result, 'stderr', '').strip().replace(str(proxy_url), '<proxy>').replace(proxy, '<proxy>')
                record_failure(f'{name}: curl ({result.returncode}) {detail[:180]}'.strip())
                continue
            value = str(json.loads(result.stdout)['ip']) if as_json else result.stdout.strip()
            if not ipaddress.ip_address(value).is_global:
                raise ValueError('Proxy did not return a public IP address')
            return value
        except subprocess.TimeoutExpired:
            record_failure(f'{name}: тайм-аут {timeout} с')
        except FileNotFoundError as exc:
            raise ConnectionError('Не найден curl.exe для проверки HTTPS через VPN.') from exc
        except (ValueError, KeyError, TypeError):
            record_failure(f'{name}: ответ не содержит публичного IP')
    raise ConnectionError('HTTPS через прокси не прошёл. ' + '; '.join(errors))
