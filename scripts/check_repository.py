"""Reject private state and recognizable credentials before commit/push or release.

Diagnostics contain paths and rule names only; matched values are never printed.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

PRIVATE_DIRS = {
    '.vpn_runtime', 'bots', 'profiles', 'user data', 'dist', 'build',
    'build-cache', 'runtime', 'extensions', 'node_modules', '.venv', 'venv',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
}
PRIVATE_NAMES = {
    'accounts.json', 'vpn_config.json', 'userscript_config.json',
    'userscript_config.json.lock', 'launcher_config.json', 'proxies.txt',
    'cookies', 'cookies-journal', 'login data', 'login data-journal',
    'history', 'history-journal', 'local state', 'preferences',
}
PRIVATE_SUFFIXES = {
    '.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3', '.log',
    '.pyc', '.pyo', '.exe', '.dll', '.zip', '.7z', '.rar', '.bak', '.tmp',
}
SECRETS = {
    'private key': re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
    'GitHub token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b'),
    'AWS access key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'Google API key': re.compile(rb'\bAIza[0-9A-Za-z_-]{35}\b'),
}


def path_rule(name):
    path = PurePosixPath(name.lower())
    if PRIVATE_DIRS.intersection(path.parts):
        return 'private state or generated directory'
    if path.name in PRIVATE_NAMES or (path.name.startswith('.env') and path.name != '.env.example'):
        return 'private configuration'
    if path.suffix in PRIVATE_SUFFIXES:
        return 'private or generated file type'
    if str(path) == 'stealth_shield/user_bot_script.js':
        return 'downloaded user script'
    return None


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def entries(root, mode):
    if mode == 'history':
        seen = set()
        for commit in git(root, 'rev-list', '--all').splitlines():
            for item in git(root, 'ls-tree', '-r', '-z', '--full-tree', commit.decode()).split(b'\0'):
                if not item:
                    continue
                metadata, raw_name = item.split(b'\t', 1)
                _, kind, oid = metadata.split()
                entry = (oid, raw_name)
                if kind == b'blob' and entry not in seen:
                    seen.add(entry)
                    yield raw_name.decode('utf-8'), git(root, 'cat-file', 'blob', oid.decode())
    elif mode == 'staged':
        for item in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
            if item:
                metadata, raw_name = item.split(b'\t', 1)
                oid = metadata.split()[1].decode()
                yield raw_name.decode('utf-8'), git(root, 'cat-file', 'blob', oid)
    else:
        for raw_name in git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0'):
            if raw_name:
                name = raw_name.decode('utf-8')
                file = root / name
                if file.is_file():
                    yield name, file.read_bytes()


def check(root, mode):
    violations = set()
    count = 0
    for name, content in entries(root, mode):
        count += 1
        rule = path_rule(name)
        # Published source releases once contained this exact empty template.
        # Allow only that inert historical template, never a new indexed config.
        if mode == 'history' and name == 'vpn_config.json':
            try:
                if json.loads(content) == dict(subscription_url='', raw_nodes_text='', last_updated='', active_mode='auto'):
                    rule = None
            except (ValueError, UnicodeError):
                pass
        if rule:
            violations.add((name, rule))
        for label, pattern in SECRETS.items():
            if pattern.search(content):
                violations.add((name, label))
    return count, sorted(violations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--staged', action='store_true', help='Inspect Git index contents')
    group.add_argument('--history', action='store_true', help='Inspect all referenced Git history')
    args = parser.parse_args()
    root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
    count, violations = check(root, 'history' if args.history else 'staged' if args.staged else 'worktree')
    for name, rule in violations[:20]:
        print(f'BLOCKED: {name}: {rule}')
    if violations:
        print(f'Repository privacy check failed: {len(violations)} finding(s). Keep private data outside the repository.')
        return 1
    print(f'Repository privacy check passed: {count} files/blobs inspected.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'Repository privacy check could not complete: {type(error).__name__}', file=sys.stderr)
        sys.exit(2)
