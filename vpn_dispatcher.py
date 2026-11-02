"""
Legion Multi-Bot - VPN & Multi-Port Proxy Dispatcher (v2.0)
Manages VPN subscriptions (VLESS, VMess, Trojan, Shadowsocks, SOCKS5),
generates multi-inbound Xray configs, maps dedicated local proxy ports to each slot,
and provides seamless 1-click subscription management with persistent storage.
"""

import os
import sys
import json
import base64
import urllib.parse
import urllib.request
import urllib.error
import subprocess
import threading
import atexit
import socket
import time
import re
import copy
import tempfile
import hashlib
import gzip
import io
import platform
from vpn_runtime import (atomic_json, process_identity, runtime_lock, process_details,
                         process_is_alive, terminate_verified_process)
from browser_lifecycle import BrowserJob, resume_browser as resume_process
from concurrent.futures import ThreadPoolExecutor, as_completed

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_CONFIG_FILE = os.path.join(CURRENT_DIR, "vpn_config.json")
RUNTIME_DIR = os.path.join(CURRENT_DIR, ".vpn_runtime")
CONFIG_FILE = os.path.join(RUNTIME_DIR, "vpn_config.json")
CORE_OWNER_FILE = os.path.join(RUNTIME_DIR, "xray_owner.json")
CORE_LOCK_FILE = os.path.join(RUNTIME_DIR, "xray.lock")
DEFAULT_CONFIG = {"subscription_url": "", "raw_nodes_text": "", "last_updated": "", "active_mode": "auto"}
XRAY_CONFIG_FILE = os.path.join(RUNTIME_DIR, "xray_multi_config.json")

BASE_LOCAL_PORT = 10810  # Ports 10810 .. 10858 reserved for slot proxies (up to 48 slots)
DEFAULT_MAX_SLOTS = 48
MAX_SUBSCRIPTION_BYTES = 4 * 1024 * 1024


class SubscriptionError(ValueError):
    pass


def subscription_device_id():
    """Use this Windows installation's existing identity; never create a new device."""
    if sys.platform != 'win32':
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value = str(winreg.QueryValueEx(key, 'MachineGuid')[0]).strip().strip('{}')
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
            return value
    except OSError:
        pass
    return None


def subscription_url(value):
    value = str(value or '').strip()
    if value.startswith('happ://add/'):
        value = value[len('happ://add/'):]
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or any(c.isspace() for c in value)):
        raise ValueError('Вставьте полную HTTP/HTTPS-ссылку подписки без пробелов.')
    return value


def subscription_notice(text):
    """Inspect provider errors, including base64 and percent-encoded node labels."""
    inspected = text
    try:
        compact = ''.join(text.split())
        inspected += '\n' + base64.urlsafe_b64decode(compact + '=' * (-len(compact) % 4)).decode('utf-8', errors='ignore')
    except (ValueError, UnicodeError):
        pass
    inspected = urllib.parse.unquote(inspected).lower()
    if any(word in inspected for word in ('достигли лимита', 'лимит устройств', 'device limit', 'max devices')):
        return 'Достигнут лимит устройств подписки. Освободите место в кабинете провайдера и повторите загрузку.'
    if any(word in inspected for word in ('app not supported', 'app not support', 'unsupported app')):
        return 'Провайдер вернул «App not supported» вместо серверов. Проверьте, разрешён ли доступ по этой ссылке для данного устройства.'
    if 'happ://crypt' in inspected:
        return 'Провайдер вернул зашифрованную подписку вместо открытых конфигураций. Для самостоятельной загрузки нужна HTTP/HTTPS-ссылка на JSON или список ключей.'
    return None
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
HAPP_IMPORT_FAILURE = (
    "Не получилось. Добавьте все серверы по отдельности из параметров "
    "каждого сервера вашего VPN-клиента."
)

# Candidate paths for Xray executable
XRAY_CANDIDATES = [
    r"C:\Program Files\FlyFrogLLC\Happ\core\xray.exe",
    os.path.join(CURRENT_DIR, "..", "bin", "xray.exe"),
    os.path.join(CURRENT_DIR, "xray.exe")
]

class VpnNode:
    """Represents a single VPN server / proxy node with parsed attributes."""
    def __init__(self, tag, protocol, address, port, raw_url="", outbound_config=None):
        self.tag = tag or f"{protocol.upper()} Node"
        self.protocol = protocol.lower()
        self.address = address
        self.port = port
        self.raw_url = raw_url
        self.outbound_config = outbound_config or {}

    def to_dict(self):
        return {
            "tag": self.tag,
            "protocol": self.protocol,
            "address": self.address,
            "port": self.port,
            "raw_url": self.raw_url,
            "outbound_config": self.outbound_config
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            tag=d.get("tag", ""),
            protocol=d.get("protocol", "vless"),
            address=d.get("address", ""),
            port=d.get("port", 443),
            raw_url=d.get("raw_url", ""),
            outbound_config=d.get("outbound_config", {})
        )

def _clean_tag(raw_tag, default_name):
    """Decodes percent-encoded tag and strips leading/trailing junk and newlines."""
    if not raw_tag:
        return default_name
    try:
        t = urllib.parse.unquote(raw_tag).strip()
        t = re.sub(r'[\r\n\t]+', ' ', t).strip()
        return t if t else default_name
    except Exception:
        return default_name

def is_dummy_placeholder(tag, host, port):
    """Detects dummy error/rejection placeholders injected by restrictive VPN providers (like HitVPN)."""
    try:
        clean_h = str(host or "").strip("[]").lower()
        p = int(port)
        if clean_h in ("0.0.0.0", "", "none", "null") or p <= 0 or p > 65535:
            return True
        if clean_h in ("127.0.0.1", "localhost", "::1") and p in (0, 1):
            return True
    except (ValueError, TypeError):
        return True

    tag_lower = (tag or "").lower()
    dummy_patterns = [
        "app not supported", "app not support", "unsupported app",
        "достигли лимита", "лимит устройств", "device limit",
        "invalid subscription", "блокировка", "заблокирован",
        "подписка истекла", "expired", "трафик исчерпан", "лимит трафика", "traffic limit",
        "обновите приложение", "update app"
    ]
    return any(p in tag_lower for p in dummy_patterns)

def parse_vless_node(url):
    """Parses standard vless:// URI into a VpnNode with Xray outbound config."""
    if not url.startswith("vless://"):
        return None
    try:
        tag = "VLESS Node"
        work_url = url
        if "#" in work_url:
            work_url, fragment = work_url.split("#", 1)
            tag = _clean_tag(fragment, "VLESS Node")

        clean_url = work_url.replace("vless://", "")
        if "@" not in clean_url:
            return None
        uuid, rest = clean_url.split("@", 1)

        query = {}
        if "?" in rest:
            rest, q_str = rest.split("?", 1)
            query = urllib.parse.parse_qs(q_str)

        if ":" not in rest:
            return None
        host, port_s = rest.rsplit(":", 1)
        port = int(port_s)
        clean_host = host.strip("[]")

        if is_dummy_placeholder(tag, clean_host, port):
            return None

        def q(k, default=""):
            return query.get(k, [default])[0]

        net_type = q("type", "tcp")
        security = q("security", "none")
        flow = q("flow", "")

        outbound = {
            "tag": tag,
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": clean_host,
                    "port": port,
                    "users": [{"id": uuid, "encryption": "none"}]
                }]
            },
            "streamSettings": {
                "network": net_type,
                "security": security
            }
        }

        if flow:
            outbound["settings"]["vnext"][0]["users"][0]["flow"] = flow

        if security == "reality":
            outbound["streamSettings"]["realitySettings"] = {
                "serverName": q("sni", clean_host),
                "fingerprint": q("fp", "chrome"),
                "show": False,
                "publicKey": q("pbk", ""),
                "shortId": q("sid", ""),
                "spiderX": q("spx", "")
            }
        elif security == "tls":
            outbound["streamSettings"]["tlsSettings"] = {
                "serverName": q("sni", clean_host),
                "fingerprint": q("fp", "chrome"),
                "allowInsecure": False
            }

        if net_type == "ws":
            outbound["streamSettings"]["wsSettings"] = {
                "path": q("path", "/"),
                "headers": {"Host": q("host", clean_host)}
            }
        elif net_type == "grpc":
            outbound["streamSettings"]["grpcSettings"] = {
                "serviceName": q("serviceName", q("path", "")),
                "multiMode": True
            }

        return VpnNode(tag, "vless", clean_host, port, raw_url=url, outbound_config=outbound)
    except Exception:
        return None

def parse_trojan_node(url):
    """Parses trojan:// URI into a VpnNode."""
    if not url.startswith("trojan://"):
        return None
    try:
        tag = "Trojan Node"
        work = url
        if "#" in work:
            work, fragment = work.split("#", 1)
            tag = _clean_tag(fragment, "Trojan Node")
        clean = work.replace("trojan://", "")
        if "@" not in clean:
            return None
        pwd, rest = clean.split("@", 1)
        pwd = urllib.parse.unquote(pwd)
        query = {}
        if "?" in rest:
            rest, q_str = rest.split("?", 1)
            query = urllib.parse.parse_qs(q_str)
        if ":" not in rest:
            return None
        host, port_s = rest.rsplit(":", 1)
        port = int(port_s)
        clean_host = host.strip("[]")

        if is_dummy_placeholder(tag, clean_host, port):
            return None

        sni = query.get("sni", [clean_host])[0]
        net_type = query.get("type", ["tcp"])[0]

        stream_settings = {
            "network": net_type,
            "security": "tls",
            "tlsSettings": {"serverName": sni, "allowInsecure": False}
        }
        if net_type == "ws":
            stream_settings["wsSettings"] = {
                "path": query.get("path", ["/"])[0],
                "headers": {"Host": query.get("host", [clean_host])[0]}
            }

        outbound = {
            "tag": tag,
            "protocol": "trojan",
            "settings": {
                "servers": [{"address": clean_host, "port": port, "password": pwd}]
            },
            "streamSettings": stream_settings
        }
        return VpnNode(tag, "trojan", clean_host, port, raw_url=url, outbound_config=outbound)
    except Exception:
        return None

def parse_vmess_node(url):
    """Parses vmess:// base64 JSON URI into a VpnNode."""
    if not url.startswith("vmess://"):
        return None
    try:
        work = url.replace("vmess://", "").strip()
        custom_tag = None
        if "#" in work:
            work, fragment = work.split("#", 1)
            custom_tag = _clean_tag(fragment, None)

        b64 = work.replace("-", "+").replace("_", "/")
        rem = len(b64) % 4
        if rem > 0:
            b64 += "=" * (4 - rem)
        raw_json = base64.b64decode(b64).decode("utf-8", errors="ignore")
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            return None

        tag = custom_tag or _clean_tag(data.get("ps", ""), "VMess Node")
        host = str(data.get("add", "")).strip("[]")
        port = int(data.get("port", 443))
        if is_dummy_placeholder(tag, host, port):
            return None
        uuid = data.get("id", "")
        net_type = data.get("net", "tcp")
        tls = data.get("tls", "none")
        sni = data.get("sni") or data.get("host") or host

        stream_settings = {
            "network": net_type,
            "security": "tls" if tls == "tls" else "none"
        }
        if tls == "tls":
            stream_settings["tlsSettings"] = {
                "serverName": sni,
                "allowInsecure": False
            }
        if net_type == "ws":
            stream_settings["wsSettings"] = {
                "path": data.get("path") or "/",
                "headers": {"Host": data.get("host", host)}
            }
        elif net_type == "grpc":
            stream_settings["grpcSettings"] = {
                "serviceName": data.get("path", ""),
                "multiMode": True
            }

        outbound = {
            "tag": tag,
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": host,
                    "port": port,
                    "users": [{"id": uuid, "alterId": 0, "security": "auto"}]
                }]
            },
            "streamSettings": stream_settings
        }
        return VpnNode(tag, "vmess", host, port, raw_url=url, outbound_config=outbound)
    except Exception:
        return None

def parse_ss_node(url):
    """Parses standard ss:// Shadowsocks URI into a VpnNode."""
    if not url.startswith("ss://"):
        return None
    try:
        tag = "Shadowsocks Node"
        work = url
        if "#" in work:
            work, fragment = work.split("#", 1)
            tag = _clean_tag(fragment, "Shadowsocks Node")

        clean = work.replace("ss://", "").strip()
        host, port, method, password = "", 443, "aes-256-gcm", ""
        if "@" in clean:
            user_part, addr_part = clean.split("@", 1)
            if "?" in addr_part:
                addr_part, _ = addr_part.split("?", 1)
            if ":" in addr_part:
                host, port_s = addr_part.rsplit(":", 1)
                port = int(port_s)

            u_b64 = user_part.replace("-", "+").replace("_", "/")
            rem = len(u_b64) % 4
            if rem > 0:
                u_b64 += "=" * (4 - rem)
            try:
                dec = base64.b64decode(u_b64).decode("utf-8", errors="ignore")
                if ":" in dec:
                    method, password = dec.split(":", 1)
                else:
                    password = dec
            except Exception:
                if ":" in user_part:
                    method, password = user_part.split(":", 1)
        else:
            c_b64 = clean.replace("-", "+").replace("_", "/")
            rem = len(c_b64) % 4
            if rem > 0:
                c_b64 += "=" * (4 - rem)
            dec = base64.b64decode(c_b64).decode("utf-8", errors="ignore")
            if "@" in dec:
                m_p, h_p = dec.split("@", 1)
                if "?" in h_p:
                    h_p, _ = h_p.split("?", 1)
                if ":" in m_p:
                    method, password = m_p.split(":", 1)
                if ":" in h_p:
                    host, port_s = h_p.rsplit(":", 1)
                    port = int(port_s)

        host = host.strip("[]")
        if not host or not password or is_dummy_placeholder(tag, host, port):
            return None

        outbound = {
            "tag": tag,
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": host,
                    "port": port,
                    "method": method,
                    "password": password
                }]
            }
        }
        return VpnNode(tag, "shadowsocks", host, port, raw_url=url, outbound_config=outbound)
    except Exception:
        return None

def parse_singbox_node(obj):
    """Parses a sing-box outbound JSON dictionary or JSON string into a VpnNode with Xray config."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj.strip())
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None

    proto = str(obj.get("type", "")).lower()
    tag = obj.get("tag") or f"{proto.upper()} Node"
    server = str(obj.get("server", "")).strip("[]")
    try:
        port = int(obj.get("server_port", 443))
    except (ValueError, TypeError):
        port = 443

    if not server or not proto:
        return None

    if is_dummy_placeholder(tag, server, port):
        return None

    tls_info = obj.get("tls", {}) if isinstance(obj.get("tls"), dict) else {}
    reality_info = tls_info.get("reality", {}) if isinstance(tls_info.get("reality"), dict) else {}
    transport = obj.get("transport", {}) if isinstance(obj.get("transport"), dict) else {}
    net_type = transport.get("type", "tcp").lower()

    security = "none"
    if reality_info.get("enabled"):
        security = "reality"
    elif tls_info.get("enabled"):
        security = "tls"

    sni = tls_info.get("server_name") or server

    # Unified streamSettings generation for all Sing-box protocols
    stream_settings = {"network": net_type, "security": security}
    if security == "reality":
        stream_settings["realitySettings"] = {
            "serverName": sni,
            "fingerprint": tls_info.get("fingerprint", "chrome"),
            "show": False,
            "publicKey": reality_info.get("public_key", ""),
            "shortId": reality_info.get("short_id", "")
        }
    elif security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": tls_info.get("fingerprint", "chrome"),
            "allowInsecure": False
        }

    if net_type == "ws":
        stream_settings["wsSettings"] = {
            "path": transport.get("path") or "/",
            "headers": transport.get("headers") or {"Host": sni}
        }
    elif net_type == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": transport.get("service_name", ""),
            "multiMode": True
        }

    if proto == "vless":
        uuid = obj.get("uuid", "")
        flow = obj.get("flow", "")
        u_obj = {"id": uuid, "encryption": "none"}
        if flow:
            u_obj["flow"] = flow
        outbound = {
            "tag": tag,
            "protocol": "vless",
            "settings": {"vnext": [{"address": server, "port": port, "users": [u_obj]}]},
            "streamSettings": stream_settings
        }
        return VpnNode(tag, "vless", server, port, raw_url=json.dumps(obj), outbound_config=outbound)

    elif proto == "vmess":
        uuid = obj.get("uuid", "")
        security_cipher = obj.get("security", "auto")
        outbound = {
            "tag": tag,
            "protocol": "vmess",
            "settings": {"vnext": [{"address": server, "port": port, "users": [{"id": uuid, "alterId": 0, "security": security_cipher}]}]},
            "streamSettings": stream_settings
        }
        return VpnNode(tag, "vmess", server, port, raw_url=json.dumps(obj), outbound_config=outbound)

    elif proto == "trojan":
        password = obj.get("password", "")
        outbound = {
            "tag": tag,
            "protocol": "trojan",
            "settings": {"servers": [{"address": server, "port": port, "password": password}]},
            "streamSettings": stream_settings
        }
        return VpnNode(tag, "trojan", server, port, raw_url=json.dumps(obj), outbound_config=outbound)

    elif proto in ("shadowsocks", "ss"):
        method = obj.get("method", "aes-256-gcm")
        password = obj.get("password", "")
        outbound = {
            "tag": tag,
            "protocol": "shadowsocks",
            "settings": {"servers": [{"address": server, "port": port, "method": method, "password": password}]}
        }
        if security != "none" or net_type != "tcp":
            outbound["streamSettings"] = stream_settings
        return VpnNode(tag, "shadowsocks", server, port, raw_url=json.dumps(obj), outbound_config=outbound)

    return None

def parse_xray_json_node(obj):
    """Parses a full Xray JSON config export (like Happ's 'copyServerJson' with 'remarks' and 'outbounds') into a VpnNode."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj.strip())
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None

    outbounds = obj.get("outbounds")
    if not isinstance(outbounds, list):
        return None

    remarks = obj.get("remarks")
    proxy_ob = None
    for ob in outbounds:
        if not isinstance(ob, dict):
            continue
        proto = str(ob.get("protocol", "")).lower()
        tag_val = str(ob.get("tag", "")).lower()
        if proto in ("vless", "vmess", "trojan", "shadowsocks", "socks", "http") and tag_val not in ("direct", "block", "freedom", "blackhole"):
            proxy_ob = ob
            break

    if not proxy_ob:
        return None

    proto = str(proxy_ob.get("protocol", "")).lower()
    tag = remarks or proxy_ob.get("tag") or f"{proto.upper()} Node"

    host = ""
    port = 443

    settings = proxy_ob.get("settings", {})
    if proto in ("vless", "vmess"):
        vnext = settings.get("vnext", [])
        if vnext and isinstance(vnext, list) and isinstance(vnext[0], dict):
            host = str(vnext[0].get("address", "")).strip("[]")
            try:
                port = int(vnext[0].get("port", 443))
            except (ValueError, TypeError):
                port = 443
    elif proto in ("trojan", "shadowsocks", "socks", "http"):
        servers = settings.get("servers", [])
        if servers and isinstance(servers, list) and isinstance(servers[0], dict):
            host = str(servers[0].get("address", "")).strip("[]")
            try:
                port = int(servers[0].get("port", 443))
            except (ValueError, TypeError):
                port = 443

    if not host or is_dummy_placeholder(tag, host, port):
        return None

    ob_copy = dict(proxy_ob)
    ob_copy["tag"] = tag
    return VpnNode(tag, proto, host, port, raw_url=json.dumps(obj), outbound_config=ob_copy)

def _extract_json_blocks(text):
    """Finds and extracts valid top-level JSON objects { ... } from raw text."""
    results = []
    idx = 0
    n = len(text)
    while idx < n:
        start = text.find('{', idx)
        if start == -1:
            break
        depth = 0
        in_str = False
        escape = False
        end = -1
        for i in range(start, n):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if not in_str:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end != -1:
            results.append((start, end, text[start:end]))
            idx = end
        else:
            idx = start + 1
    return results

def parse_raw_nodes(content):
    """Parses any text block containing VLESS, VMess, Trojan, SS, SOCKS5, HTTP links, or sing-box JSON.
    Handles MIME multi-line Base64 payloads and custom proxy schemes."""
    if not content:
        return []
    nodes = []

    # 1. Direct JSON array or root Sing-box config {"outbounds": [...]}
    stripped = content.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            loaded_json = json.loads(stripped)
            if isinstance(loaded_json, list):
                for item in loaded_json:
                    sb_node = parse_xray_json_node(item) or parse_singbox_node(item)
                    if sb_node:
                        nodes.append(sb_node)
                if nodes:
                    return nodes
        except Exception:
            pass
    elif stripped.startswith("{") and stripped.endswith("}"):
        try:
            loaded_json = json.loads(stripped)
            if isinstance(loaded_json, dict):
                # 1. Full Xray JSON config from Happ (with "remarks" and "outbounds")
                xray_node = parse_xray_json_node(loaded_json)
                if xray_node:
                    return [xray_node]

                # 2. Check for Sing-box / Xray list under "outbounds"
                if isinstance(loaded_json.get("outbounds"), list):
                    for item in loaded_json["outbounds"]:
                        node_cand = parse_xray_json_node(item) or parse_singbox_node(item)
                        if node_cand:
                            nodes.append(node_cand)
                    if nodes:
                        return nodes

                # 3. Standalone Sing-box outbound
                sb_node = parse_singbox_node(loaded_json)
                if sb_node:
                    return [sb_node]
        except Exception:
            pass

    # 2. Extract multi-line JSON blocks ({ ... }) first so line-splitting doesn't break them
    work_text = stripped
    json_blocks = _extract_json_blocks(work_text)
    if json_blocks:
        char_list = list(work_text)
        for start, end, j_str in json_blocks:
            node_cand = parse_xray_json_node(j_str) or parse_singbox_node(j_str)
            if node_cand:
                nodes.append(node_cand)
                # Replace parsed JSON block with spaces to preserve line layout for surrounding text
                char_list[start:end] = " " * (end - start)
        work_text = "".join(char_list)

    lines = work_text.splitlines()

    # Detect if content is encoded in base64 (single or multiline wrapped at 64/76 chars)
    has_scheme = any("://" in line for line in lines[:5])
    if not has_scheme and lines:
        try:
            compact_b64 = "".join(line.strip() for line in lines).replace("-", "+").replace("_", "/")
            rem = len(compact_b64) % 4
            if rem > 0:
                compact_b64 += "=" * (4 - rem)
            decoded = base64.b64decode(compact_b64).decode("utf-8", errors="ignore")
            if "://" in decoded or "{" in decoded:
                # Recursively parse the decoded base64 payload to catch nested JSON or URLs
                b64_nodes = parse_raw_nodes(decoded)
                if b64_nodes:
                    nodes.extend(b64_nodes)
                    return nodes
                lines = decoded.splitlines()
        except Exception:
            pass

    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue
        node = None
        if line.startswith("{") and line.endswith("}"):
            node = parse_xray_json_node(line) or parse_singbox_node(line)
        elif line.startswith("vless://"):
            node = parse_vless_node(line)
        elif line.startswith("trojan://"):
            node = parse_trojan_node(line)
        elif line.startswith("vmess://"):
            node = parse_vmess_node(line)
        elif line.startswith("ss://"):
            node = parse_ss_node(line)
        elif line.startswith(("socks5://", "socks5h://", "socks://", "http://", "https://")):
            try:
                parsed = urllib.parse.urlparse(line)
                h = (parsed.hostname or "").strip("[]")
                is_socks = "socks" in parsed.scheme.lower()
                p = parsed.port or (1080 if is_socks else 443 if parsed.scheme == 'https' else 80)
                tag = f"{parsed.scheme.upper()} {h}:{p}"

                if not h or is_dummy_placeholder(tag, h, p):
                    continue

                # Build valid Xray outbound so SOCKS5/HTTP proxies don't leak direct IP via 'freedom'
                server_obj = {"address": h, "port": p}
                if parsed.username or parsed.password:
                    server_obj["users"] = [{
                        "user": urllib.parse.unquote(parsed.username or ""),
                        "pass": urllib.parse.unquote(parsed.password or "")
                    }]

                outbound = {
                    "tag": tag,
                    "protocol": "socks" if is_socks else "http",
                    "settings": {
                        "servers": [server_obj]
                    }
                }
                if parsed.scheme == 'https':
                    outbound['streamSettings'] = {
                        'security': 'tls', 'tlsSettings': {'serverName': h, 'allowInsecure': False}}
                node = VpnNode(tag, parsed.scheme, h, p, raw_url=line, outbound_config=outbound)
            except Exception:
                pass

        if node:
            nodes.append(node)
    return nodes

def _node_identity(node):
    outbound = copy.deepcopy(node.outbound_config)
    outbound.pop("tag", None)
    return json.dumps(outbound, sort_keys=True, ensure_ascii=False, allow_nan=False)


def parse_happ_configs(configs):
    """Flatten every proxy outbound, preserving transport/auth and removing duplicates."""
    nodes, seen = [], set()
    supported = {"vless", "vmess", "trojan", "shadowsocks", "socks", "http"}
    for config in configs:
        outbounds = config.get("outbounds", [])
        proxies = [ob for ob in outbounds if isinstance(ob, dict)
                   and ob.get("protocol", "").lower() not in {"freedom", "blackhole", "dns"}]
        for position, outbound in enumerate(proxies, 1):
            if outbound.get("protocol", "").lower() not in supported:
                raise ValueError("Unsupported Happ outbound protocol")
            if (outbound.get("proxySettings", {}).get("tag")
                    or outbound.get("streamSettings", {}).get("sockopt", {}).get("dialerProxy")):
                raise ValueError("Linked Happ outbounds require manual configuration")
            name = config.get("remarks") or "Happ"
            if len(proxies) > 1:
                name += f" / {outbound.get('tag') or position}"
            node = parse_xray_json_node({"remarks": name, "outbounds": [outbound]})
            if node is None:
                raise ValueError("Invalid Happ outbound")
            identity = _node_identity(node)
            if identity not in seen:
                seen.add(identity)
                nodes.append(node)
    return nodes


def parse_subscription_nodes(content):
    """Preserve every proxy outbound in full subscription JSON and Base64 lists."""
    text = content.strip()
    for _ in range(3):
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            value = None
        configs = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        if any(isinstance(config, dict) and any(isinstance(outbound, dict) and 'protocol' in outbound
                   for outbound in config.get('outbounds', [])) for config in configs):
            try:
                return parse_happ_configs([config for config in configs if isinstance(config, dict)])
            except ValueError as exc:
                raise SubscriptionError('JSON подписки содержит неподдерживаемые или связанные маршруты; неполный список серверов не применяется.') from exc
        if value is None and '://' not in text:
            try:
                compact = ''.join(text.split())
                decoded = base64.urlsafe_b64decode(compact + '=' * (-len(compact) % 4)).decode('utf-8-sig').strip()
                if decoded != text and ('://' in decoded or decoded.startswith(('{', '['))):
                    text = decoded
                    continue
            except (ValueError, UnicodeError):
                pass
        nodes = parse_raw_nodes(text)
        if nodes:
            return nodes
        try:
            compact = ''.join(text.split())
            decoded = base64.urlsafe_b64decode(compact + '=' * (-len(compact) % 4)).decode('utf-8-sig')
        except (ValueError, UnicodeError):
            break
        if decoded == text:
            break
        text = decoded.strip()
    return []


class VpnDispatcher:
    """Stable per-slot routes with isolated health probes and owned-process cleanup."""
    def __init__(self):
        self.lock = threading.RLock()
        self._filter_lock = threading.Lock()
        self._filter_epoch = 0
        self._nodes_revision = 0
        self._health_revision = -1
        self._health_checked_at = 0
        self.config = {"subscription_url": "", "raw_nodes_text": "",
                       "last_updated": "", "active_mode": "auto"}
        self.nodes = []
        self.alive_nodes = []
        self.alive_indices = set()
        self.xray_proc = None
        self._launcher_identity = process_identity(os.getpid())
        self._owned_identity = None
        self._external_identity = None
        self._active_fingerprint = None
        self.last_error = ""
        self.xray_bin = self._find_xray_binary()
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        self.load_config()
        atexit.register(self.stop_xray)

    @property
    def is_running(self):
        if self.xray_proc is not None:
            return self.xray_proc.poll() is None
        return bool(self._external_identity and
                    process_identity(self._external_identity['pid']) == self._external_identity)

    def load_config(self):
        with self.lock:
            source = CONFIG_FILE if os.path.exists(CONFIG_FILE) else LEGACY_CONFIG_FILE
            if os.path.exists(source):
                try:
                    with open(source, encoding='utf-8') as stream:
                        loaded = json.load(stream)
                    if not isinstance(loaded, dict):
                        raise ValueError('VPN config must be an object')
                    if not isinstance(loaded.get('raw_nodes_text', ''), str):
                        raise ValueError('VPN node configuration must be text')
                except (OSError, ValueError) as exc:
                    self.last_error = f'Не удалось прочитать настройки VPN: {exc}'
                    return
                self.config.update(loaded)
                self.nodes = parse_raw_nodes(self.config.get('raw_nodes_text', ''))
                if source == LEGACY_CONFIG_FILE and self.config.get('raw_nodes_text'):
                    self.save_config()
                    atomic_json(LEGACY_CONFIG_FILE, DEFAULT_CONFIG)

    def save_config(self):
        with self.lock:
            atomic_json(CONFIG_FILE, self.config)

    def _apply_nodes(self, nodes, raw_text, subscription_url=None):
        with self.lock:
            old = (self.nodes, self.alive_nodes, self.alive_indices, self.config,
                   self._health_revision, self._health_checked_at, self._nodes_revision)
            self.nodes = list(nodes)
            self.alive_nodes, self.alive_indices = [], set()
            self._nodes_revision += 1
            self._health_revision, self._health_checked_at = -1, 0
            self.config = dict(self.config, raw_nodes_text=raw_text,
                               last_updated=time.strftime('%Y-%m-%d %H:%M:%S'))
            if subscription_url is not None:
                self.config['subscription_url'] = subscription_url
            try:
                if not self.start_xray():
                    raise RuntimeError(self.last_error or 'Xray не запустился')
                self.save_config()
            except Exception as exc:
                (self.nodes, self.alive_nodes, self.alive_indices, self.config,
                 self._health_revision, self._health_checked_at, self._nodes_revision) = old
                # Restore the previous route configuration if a new core exited at startup.
                if self.nodes and (not self.is_running or
                                   self._active_fingerprint != self._fingerprint(self.generate_xray_config())):
                    self.start_xray()
                return False, f'Настройки не применены: {exc}'
        return True, f'Применено серверов: {len(nodes)}. Проверка доступности не удаляет ключи.'

    def set_custom_nodes_text(self, text, source_url=None):
        try:
            parsed = parse_subscription_nodes(text)
        except SubscriptionError as exc:
            return False, str(exc)
        if not parsed:
            return False, 'Не удалось распознать серверы VPN'
        if source_url is not None:
            try:
                source_url = subscription_url(source_url)
            except ValueError:
                return False, 'Не удалось сохранить некорректную ссылку подписки.'
        return self._apply_nodes(parsed, text, subscription_url=source_url)

    def open_subscription_in_happ(self, value):
        """Let the installed Happ handle its subscription and existing device identity."""
        try:
            url = subscription_url(value)
        except ValueError:
            return False, 'Вставьте полную HTTP/HTTPS-ссылку подписки без пробелов.'
        try:
            os.startfile('happ://add/' + url)
        except (OSError, AttributeError):
            return False, 'Не удалось открыть Happ. Откройте его и добавьте ссылку через буфер обмена.'
        with self.lock:
            previous = self.config
            self.config = dict(self.config, happ_subscription_url=url)
            try:
                self.save_config()
            except Exception:
                self.config = previous
                return True, 'Happ открыт, но ссылка не сохранена локально. Дождитесь загрузки серверов и нажмите «Получить JSON из Happ».'
        return True, 'Ссылка передана в Happ. Дождитесь загрузки серверов и нажмите «Получить JSON из Happ».'

    def export_happ_json(self):
        """Read local Happ JSON without saving configuration or touching Xray."""
        try:
            from happ_memory import read_happ_configs
            nodes = parse_happ_configs(read_happ_configs())
            if not nodes:
                return False, 'В Happ не найдены поддерживаемые серверы. Откройте список серверов и повторите.', ''
            raw = json.dumps([{'remarks': node.tag, 'outbounds': [node.outbound_config]}
                              for node in nodes], ensure_ascii=False, indent=2)
            return True, (f'Получено серверов из Happ: {len(nodes)}. JSON загружен в поле; '
                          'для применения закройте окна ботов и нажмите «Применить и закрыть».'), raw
        except Exception as exc:
            messages = {
                'Open exactly one Happ client with its server list first.':
                    'Откройте ровно один Happ со списком серверов и повторите получение JSON.',
                'Could not read complete Happ configurations.':
                    'Не удалось полностью прочитать JSON из Happ. Откройте список серверов и повторите.',
                'Run this script with 64-bit Python on Windows.':
                    'Для чтения Happ нужен 64-битный Python на Windows.',
            }
            if isinstance(exc, OSError) and getattr(exc, 'winerror', None) == 5:
                message = 'Нет доступа к процессу Happ. Запустите Happ и мультибот с одинаковыми правами.'
            else:
                # Only known reader errors are shown verbatim; never expose keys.
                message = messages.get(str(exc), f'Не удалось получить JSON из Happ ({type(exc).__name__}). Откройте Happ со списком серверов и повторите.')
            return False, message, ''

    def import_happ_servers(self):
        try:
            from happ_memory import read_happ_configs
            imported = parse_happ_configs(read_happ_configs())
            if not imported:
                return False, HAPP_IMPORT_FAILURE
            with self.lock:
                known = {_node_identity(node) for node in self.nodes}
                added = [node for node in imported if _node_identity(node) not in known]
                if not added:
                    return True, f'Все серверы из Happ уже добавлены ({len(imported)}).'
                merged = list(self.nodes) + added
                raw = json.dumps([{'remarks': n.tag, 'outbounds': [n.outbound_config]}
                                  for n in merged], ensure_ascii=False, indent=2)
                return self._apply_nodes(merged, raw)
        except Exception:
            return False, HAPP_IMPORT_FAILURE

    def ensure_alive_pool(self, timeout=2.0):
        with self.lock:
            if self._health_revision == self._nodes_revision and time.monotonic() - self._health_checked_at < 30:
                return len(self.alive_nodes)
        return self.filter_alive_nodes(timeout=timeout)[0]

    def get_nodes_snapshot(self):
        # Health results never change the order used by live slot ports.
        with self.lock:
            return list(self.nodes)

    def get_all_nodes_snapshot(self):
        return self.get_nodes_snapshot()

    def clear_all_nodes(self):
        with self.lock:
            shared_core = bool(self._external_identity and self.is_running)
            owned_core = self.xray_proc is not None
            previous = (self.nodes, self.alive_nodes, self.alive_indices, self.config,
                        self._nodes_revision, self._health_revision, self._health_checked_at)
            self.nodes, self.alive_nodes, self.alive_indices = [], [], set()
            self._nodes_revision += 1
            self._health_revision = -1
            self._health_checked_at = 0
            self.config = dict(DEFAULT_CONFIG)
            try:
                self.save_config()
            except Exception as exc:
                (self.nodes, self.alive_nodes, self.alive_indices, self.config,
                 self._nodes_revision, self._health_revision, self._health_checked_at) = previous
                return False, f'Не удалось сохранить очистку серверов: {exc}'
            # Detach from an adopted core; stop_xray only terminates our own child.
            self.stop_xray()
            self.last_error = ''
        if shared_core:
            return True, 'Список серверов и подписка очищены. Общее ядро другого запускателя продолжает работать; его порты остаются заняты.'
        if owned_core:
            return True, 'Список серверов и подписка очищены. Ядро этого запускателя остановлено.'
        return True, 'Список серверов и настройки подписки очищены.'

    @staticmethod
    def _fingerprint(cfg):
        return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()

    def generate_xray_config(self, max_slots=DEFAULT_MAX_SLOTS, test_all_nodes=False, ports=None):
        with self.lock:
            nodes = list(self.nodes)
        if not nodes:
            return None
        count = len(nodes) if test_all_nodes else max(max_slots, len(nodes))
        if count > 65536 - BASE_LOCAL_PORT and ports is None:
            raise ValueError('Too many VPN servers for the local port range')
        if ports is not None and len(ports) != count:
            raise ValueError('Probe port count does not match node count')
        inbounds, rules = [], []
        # Xray uses the FIRST outbound for unmatched traffic.
        outbounds = [{'tag': 'block-all', 'protocol': 'blackhole'}]
        for i in range(count):
            port = ports[i] if ports is not None else BASE_LOCAL_PORT + i
            node = nodes[i % len(nodes)]
            in_tag, out_tag = f'in-slot-{i}', f'out-slot-{i}'
            inbounds.append({'tag': in_tag, 'port': port, 'listen': '127.0.0.1',
                             'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}})
            outbound = copy.deepcopy(node.outbound_config) or {'protocol': 'blackhole'}
            outbound['tag'] = out_tag
            outbounds.append(outbound)
            rules.append({'type': 'field', 'inboundTag': [in_tag], 'outboundTag': out_tag})
        return {'log': {'loglevel': 'warning'}, 'inbounds': inbounds, 'outbounds': outbounds,
                'routing': {'domainStrategy': 'AsIs', 'rules': rules}}

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        finally:
            job = getattr(process, '_legion_job', None)
            if job is not None:
                process._legion_job = None
                job.close()

    @staticmethod
    def _ports_ready(process, ports, timeout=5):
        deadline = time.monotonic() + timeout
        remaining = set(ports)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            for port in list(remaining):
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.05):
                        remaining.remove(port)
                except OSError:
                    pass
            if not remaining:
                time.sleep(0.1)
                return process.poll() is None
            time.sleep(0.05)
        return False

    @staticmethod
    def _check_ports_free(cfg):
        # Never mistake an unrelated listener for a ready Xray inbound.
        reservations = []
        try:
            for inbound in cfg['inbounds']:
                sock = socket.socket()
                reservations.append(sock)
                if sys.platform == 'win32':
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(('127.0.0.1', inbound['port']))
        finally:
            for sock in reservations:
                sock.close()

    def _spawn(self, path, cfg):
        self._check_ports_free(cfg)
        log_path = os.path.splitext(path)[0] + '.log'
        job = BrowserJob() if sys.platform == 'win32' else None
        process = None
        try:
            with open(log_path, 'ab') as log:
                process = subprocess.Popen([self.xray_bin, '-config', path], stdout=log, stderr=log,
                                           creationflags=CREATE_NO_WINDOW | (0x4 if job else 0))
            if job is not None:
                process._legion_job = job
                job.attach(process)
                resume_process(process)
            if not self._ports_ready(process, [x['port'] for x in cfg['inbounds']]):
                raise RuntimeError('Xray завершился или не открыл локальные порты; см. журнал ядра')
            return process
        except BaseException:
            self._terminate(process)
            if process is None and job is not None:
                job.close()
            raise

    def _managed_proxy_browsers_alive(self):
        root = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'LegionMultiBot', 'Bots')
        if not os.path.isdir(root):
            return False
        for entry in os.scandir(root):
            if not entry.is_dir():
                continue
            path = os.path.join(entry.path, '.legion-session.json')
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding='utf-8') as stream:
                    record = json.load(stream)
                proxy = urllib.parse.urlsplit(str(record.get('proxy', '')))
                if proxy.hostname != '127.0.0.1' or not proxy.port or proxy.port < BASE_LOCAL_PORT:
                    continue
                identity = record.get('identity') or {}
                pid = identity.get('pid')
                if pid:
                    current = process_identity(pid)
                    if current == identity or (current is None and process_is_alive(pid)):
                        return True
            except (OSError, ValueError, TypeError):
                raise RuntimeError('Не удалось проверить открытые профили; восстановление ядра отложено')
        return False

    def _recover_orphan_core(self, owner):
        """Reclaim only our exact config/process when its launcher has exited."""
        identity = owner.get('identity') or {}
        expected = os.path.normcase(os.path.abspath(self.xray_bin))
        if identity.get('executable') != expected or process_identity(identity.get('pid')) != identity:
            return False
        try:
            details = process_details(identity['pid'])
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            return False
        arguments = details.get('arguments', []) if details else []
        if (len(arguments) != 3 or arguments[1] != '-config'
                or os.path.normcase(os.path.abspath(arguments[0])) != expected
                or os.path.normcase(os.path.abspath(arguments[2])) != os.path.normcase(os.path.abspath(XRAY_CONFIG_FILE))):
            return False
        launcher = owner.get('launcher')
        if launcher:
            current = process_identity(launcher['pid'])
            if current == launcher or (current is None and process_is_alive(launcher['pid'])):
                return False
        else:
            parent_pid = details.get('parent_pid')
            parent = process_identity(parent_pid) if parent_pid else None
            if parent:
                if int(parent['created']) <= int(identity['created']):
                    return False
            elif not parent_pid or process_is_alive(parent_pid):
                return False
        if self._managed_proxy_browsers_alive():
            return False
        terminate_verified_process(identity)
        if process_identity(identity['pid']) == identity:
            raise RuntimeError('Старое ядро не завершилось; запуск нового ядра отменён')
        os.unlink(CORE_OWNER_FILE)
        self._external_identity = None
        self._active_fingerprint = None
        return True

    def start_xray(self, force=False):
        with self.lock:
            if not self.nodes or not self.xray_bin:
                self.last_error = 'Не настроены серверы VPN или не найден Xray'
                return False
            cfg = self.generate_xray_config()
            fingerprint = self._fingerprint(cfg)
            if self.is_running and self._active_fingerprint == fingerprint and not force:
                return True
            spawned = False
            try:
                with runtime_lock(CORE_LOCK_FILE):
                    owner = None
                    if os.path.exists(CORE_OWNER_FILE):
                        with open(CORE_OWNER_FILE, encoding='utf-8') as stream:
                            owner = json.load(stream)
                    identity = owner.get('identity') if owner else None
                    live_owner = bool(identity and process_identity(identity['pid']) == identity)
                    own = bool(self.xray_proc and self.xray_proc.poll() is None and
                               identity and identity['pid'] == self.xray_proc.pid)
                    if live_owner and not own and self._recover_orphan_core(owner):
                        live_owner = False
                    if live_owner and not own:
                        if owner.get('fingerprint') != fingerprint or force:
                            raise RuntimeError('Ядро используется другим запускателем; изменение маршрутов отклонено')
                        expected = os.path.normcase(os.path.abspath(self.xray_bin))
                        if identity['executable'] != expected:
                            raise RuntimeError('Владелец портов не соответствует настроенному Xray')
                        self._external_identity = identity
                        self._active_fingerprint = fingerprint
                        self.last_error = ''
                        return True
                    pending = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=RUNTIME_DIR, suffix='.json', delete=False) as stream:
                            pending = stream.name
                        atomic_json(pending, cfg)
                        result = subprocess.run([self.xray_bin, '-test', '-config', pending],
                                                capture_output=True, timeout=10,
                                                creationflags=CREATE_NO_WINDOW)
                        if result.returncode:
                            raise RuntimeError(f'Проверка конфигурации Xray: код {result.returncode}')
                        self._stop_owned()
                        self._check_ports_free(cfg)
                        atomic_json(XRAY_CONFIG_FILE, cfg)
                        self.xray_proc = self._spawn(XRAY_CONFIG_FILE, cfg)
                        spawned = True
                        identity = process_identity(self.xray_proc.pid)
                        if not identity:
                            self._terminate(self.xray_proc)
                            self.xray_proc = None
                            raise RuntimeError('Не удалось подтвердить процесс Xray')
                        self._owned_identity = identity
                        atomic_json(CORE_OWNER_FILE, {'identity': identity, 'fingerprint': fingerprint,
                                                    'launcher': self._launcher_identity})
                        self._external_identity = None
                        self._active_fingerprint = fingerprint
                        self.last_error = ''
                        return True
                    finally:
                        if pending and os.path.exists(pending):
                            os.unlink(pending)
            except Exception as exc:
                self.last_error = str(exc)
                if spawned:
                    self._stop_owned()
                if self.xray_proc and self.xray_proc.poll() is not None:
                    self.xray_proc = None
                return False

    def _stop_owned(self):
        process = self.xray_proc
        identity = self._owned_identity
        if process is not None:
            self._terminate(process)
            self.xray_proc = None
            try:
                with open(CORE_OWNER_FILE, encoding='utf-8') as stream:
                    owner = json.load(stream)
                if identity and owner.get('identity') == identity:
                    os.unlink(CORE_OWNER_FILE)
            except (OSError, ValueError):
                pass
        self._external_identity = None
        self._owned_identity = None
        self._active_fingerprint = None

    def stop_xray(self):
        # No port scanning, taskkill, or termination of adopted/foreign processes.
        with self.lock:
            try:
                with runtime_lock(CORE_LOCK_FILE):
                    self._stop_owned()
            except (OSError, TimeoutError):
                if self.xray_proc:
                    self._terminate(self.xray_proc)
                    self.xray_proc = None

    def filter_alive_nodes(self, timeout=3.0, max_workers=8, progress_callback=None):
        epoch = self._filter_epoch
        with self._filter_lock:
            with self.lock:
                if self._filter_epoch != epoch and self._health_revision == self._nodes_revision:
                    return len(self.alive_nodes), len(self.nodes)
                nodes = list(self.nodes)
                revision = self._nodes_revision
            if not nodes or not self.xray_bin:
                return 0, len(nodes)
            reservations, ports, pending, process = [], [], None, None
            try:
                for _ in nodes:
                    sock = socket.socket()
                    reservations.append(sock)
                    sock.bind(('127.0.0.1', 0))
                    ports.append(sock.getsockname()[1])
                # Health checks get a separate process and ephemeral ports.
                with self.lock:
                    if revision != self._nodes_revision:
                        return 0, len(self.nodes)
                    cfg = self.generate_xray_config(test_all_nodes=True, ports=ports)
                with tempfile.NamedTemporaryFile(dir=RUNTIME_DIR, suffix='.json', delete=False) as stream:
                    pending = stream.name
                atomic_json(pending, cfg)
                for sock in reservations:
                    sock.close()
                reservations.clear()
                process = self._spawn(pending, cfg)
                alive = set()
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(self.check_port_alive, port, timeout): i
                               for i, port in enumerate(ports)}
                    completed = 0
                    for future in as_completed(futures):
                        completed += 1
                        if future.result():
                            alive.add(futures[future])
                        if progress_callback and (completed % 4 == 0 or completed == len(nodes)):
                            progress_callback(f'Проверено {completed}/{len(nodes)}; доступно: {len(alive)}')
                with self.lock:
                    if revision != self._nodes_revision:
                        return 0, len(self.nodes)
                    self.alive_indices = alive
                    self.alive_nodes = [nodes[i] for i in sorted(alive)]
                    self._health_revision = revision
                    self._health_checked_at = time.monotonic()
                    self._filter_epoch += 1
                return len(alive), len(nodes)
            except Exception as exc:
                self.last_error = f'Проверка VPN не завершена: {exc}'
                return 0, len(nodes)
            finally:
                self._terminate(process)
                for sock in reservations:
                    sock.close()
                if pending:
                    for path in (pending, os.path.splitext(pending)[0] + '.log'):
                        try:
                            os.unlink(path)
                        except OSError:
                            pass

    def purge_dead_nodes(self):
        # A failed probe is temporary; saved credentials require explicit manual editing.
        return False, 'Автоматическое удаление отключено. Недоступные серверы сохранены для повторной проверки.'

    def check_port_alive(self, port, timeout=2.5):
        try:
            from proxy_network import fetch_proxy_ip
            fetch_proxy_ip(f'socks5://127.0.0.1:{port}', timeout=timeout)
            return True
        except Exception:
            return False

    def get_slot_info(self, slot_idx):
        with self.lock:
            nodes = list(self.nodes)
        if not nodes:
            return None, None, None
        index = slot_idx % max(DEFAULT_MAX_SLOTS, len(nodes))
        port = BASE_LOCAL_PORT + index
        node = nodes[index % len(nodes)]
        tag = node.tag if len(node.tag) <= 16 else node.tag[:14] + '…'
        return (f'socks5://127.0.0.1:{port}', tag,
                f'Слот #{slot_idx+1} [Порт {port}]\nСервер: {node.tag}\nПротокол: {node.protocol.upper()}')

    def ping_all_slots(self, count=DEFAULT_MAX_SLOTS):
        def probe(i):
            proxy, tag, _ = self.get_slot_info(i)
            port = urllib.parse.urlsplit(proxy).port if proxy else None
            return {'slot': i+1, 'port': port, 'tag': tag or 'Нет сервера',
                    'proxy': proxy, 'alive': bool(port and self.check_port_alive(port))}
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(probe, range(count)))

    def _find_xray_binary(self):
        for cand in XRAY_CANDIDATES:
            if os.path.exists(cand):
                return os.path.abspath(cand)
        try:
            res = subprocess.check_output(["where", "xray.exe"], text=True, timeout=5, creationflags=CREATE_NO_WINDOW)
            first = res.splitlines()[0].strip()
            if os.path.exists(first):
                return first
        except Exception:
            pass
        return None

    def _read_subscription_nodes(self, url=None):
        """Fetch the provider directly, independently of Happ, Xray and running bots."""
        with self.lock:
            value = self.config.get('subscription_url', '') if url is None else url

        try:
            sub_url = subscription_url(value)
        except ValueError:
            raise SubscriptionError('Вставьте полную HTTP/HTTPS-ссылку подписки без пробелов.')
        device_id = subscription_device_id()

        user_agents = [
            "Happ/4.3.0",
            "v2rayN/6.23",
            "sing-box/1.9.0",
        ]

        last_err = None
        detected_dummy_msg = None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for ua in user_agents:
            try:
                headers = {
                    "User-Agent": ua,
                    "Accept": "*/*",
                    "X-Device-OS": platform.system(),
                    "X-Ver-OS": platform.version(),
                    "X-Device-Model": 'Windows PC' if sys.platform == 'win32' else platform.machine(),
                }
                if device_id:
                    headers['X-Hwid'] = device_id

                req = urllib.request.Request(sub_url, headers=headers)
                with opener.open(req, timeout=7) as resp:
                    data = resp.read(MAX_SUBSCRIPTION_BYTES + 1)
                    if len(data) > MAX_SUBSCRIPTION_BYTES:
                        raise SubscriptionError('Ответ подписки превышает допустимый размер (4 МБ).')
                    if data.startswith(b"\x1f\x8b"):
                        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                            data = compressed.read(MAX_SUBSCRIPTION_BYTES + 1)
                        if len(data) > MAX_SUBSCRIPTION_BYTES:
                            raise SubscriptionError('Ответ подписки превышает допустимый размер (4 МБ).')
                    text = data.decode('utf-8-sig')
                    if str(resp.headers.get('X-Hwid-Max-Devices-Reached', '')).lower() in ('true', '1'):
                        raise SubscriptionError('Достигнут лимит устройств подписки. Освободите место в кабинете провайдера и повторите загрузку.')
                    if str(resp.headers.get('X-Hwid-Not-Supported', '')).lower() in ('true', '1'):
                        raise SubscriptionError('Провайдер не принял ID этого компьютера. Проверьте доступ устройства в кабинете провайдера.')
                    notice = subscription_notice(text)
                    if notice:
                        detected_dummy_msg = notice
                        if 'лимит устройств' in notice:
                            raise SubscriptionError(notice)
                        continue

                    parsed = parse_subscription_nodes(text)
                    if parsed:
                        return parsed, sub_url
            except SubscriptionError:
                raise
            except Exception as e:
                # urllib errors can contain the private subscription URL.
                last_err = f'HTTP {e.code}' if isinstance(e, urllib.error.HTTPError) else type(e).__name__

        if detected_dummy_msg:
            raise SubscriptionError(detected_dummy_msg)
        raise SubscriptionError(f"Не удалось загрузить серверы ({last_err or 'В ответе не найдено поддерживаемых серверов'})")

    def export_subscription_json(self, url=None):
        """Return every server as JSON without changing the active VPN configuration."""
        try:
            nodes, _ = self._read_subscription_nodes(url)
            raw = json.dumps([{'remarks': node.tag, 'outbounds': [node.outbound_config]}
                              for node in nodes], ensure_ascii=False, indent=2)
            return True, f'Получено серверов по ссылке: {len(nodes)}. JSON загружен в поле; Happ не требуется.', raw
        except SubscriptionError as exc:
            return False, str(exc), ''

    def fetch_subscription(self, url=None):
        """Compatibility entry point for callers that explicitly apply a subscription."""
        try:
            nodes, source_url = self._read_subscription_nodes(url)
            raw = json.dumps([{'remarks': node.tag, 'outbounds': [node.outbound_config]}
                              for node in nodes], ensure_ascii=False, indent=2)
            return self._apply_nodes(nodes, raw, subscription_url=source_url)
        except SubscriptionError as exc:
            return False, str(exc)
