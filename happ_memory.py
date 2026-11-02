"""Read complete named JSON configs from the running Happ process (Windows x64).

The process is opened read-only. No memory dumps or network requests are made.
A snapshot can include cached/editor variants; the importer deduplicates outbounds.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re

CHUNK = 4 * 1024 * 1024
OVERLAP = 1024 * 1024
CONFIG_START = re.compile(
    r'\{\s*"(?:burstObservatory|observatory|dns|log|inbounds|outbounds|'
    r'remarks|routing|route|meta)"\s*:'
)


def windows_api():
    if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError('Run this script with 64-bit Python on Windows.')
    api = C.WinDLL('kernel32', use_last_error=True)

    class MemoryInfo(C.Structure):
        _fields_ = [
            ('BaseAddress', C.c_void_p), ('AllocationBase', C.c_void_p),
            ('AllocationProtect', W.DWORD), ('RegionSize', C.c_size_t),
            ('State', W.DWORD), ('Protect', W.DWORD), ('Type', W.DWORD),
        ]

    class ProcessEntry(C.Structure):
        _fields_ = [
            ('dwSize', W.DWORD), ('cntUsage', W.DWORD),
            ('th32ProcessID', W.DWORD), ('th32DefaultHeapID', C.c_size_t),
            ('th32ModuleID', W.DWORD), ('cntThreads', W.DWORD),
            ('th32ParentProcessID', W.DWORD), ('pcPriClassBase', W.LONG),
            ('dwFlags', W.DWORD), ('szExeFile', W.WCHAR * 260),
        ]

    signatures = {
        'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
        'CloseHandle': ([W.HANDLE], W.BOOL),
        'CreateToolhelp32Snapshot': ([W.DWORD, W.DWORD], W.HANDLE),
        'Process32FirstW': ([W.HANDLE, C.POINTER(ProcessEntry)], W.BOOL),
        'Process32NextW': ([W.HANDLE, C.POINTER(ProcessEntry)], W.BOOL),
        'QueryFullProcessImageNameW': (
            [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
        'VirtualQueryEx': (
            [W.HANDLE, C.c_void_p, C.POINTER(MemoryInfo), C.c_size_t], C.c_size_t),
        'ReadProcessMemory': (
            [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)],
            W.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = args
        function.restype = result
    return api, MemoryInfo, ProcessEntry


def find_happ(api, process_entry):
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    result = []
    try:
        entry = process_entry()
        entry.dwSize = C.sizeof(entry)
        ok = api.Process32FirstW(snapshot, C.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == 'happ.exe':
                result.append(entry.th32ProcessID)
            ok = api.Process32NextW(snapshot, C.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    return result


def inspect_chunk(data, found, decoder):
    for encoding in ('utf-8', 'utf-16-le'):
        if '"outbounds"'.encode(encoding) not in data:
            continue
        text = data.decode(encoding, errors='replace')
        for match in CONFIG_START.finditer(text):
            try:
                config, end = decoder.raw_decode(text, match.start())
            except (ValueError, RecursionError):
                continue
            if not isinstance(config, dict):
                continue
            name = config.get('remarks')
            outbounds = config.get('outbounds')
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(outbounds, list) or not outbounds:
                continue
            if not all(isinstance(x, dict) and ('protocol' in x or 'type' in x)
                       for x in outbounds):
                continue
            if '\ufffd' in text[match.start():end]:
                continue
            canonical = json.dumps(config, sort_keys=True, ensure_ascii=False,
                                   allow_nan=False).encode('utf-8')
            digest = hashlib.sha256(canonical).hexdigest()
            found.setdefault(digest, config)


def scan_process(api, memory_info, pid):
    # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ; no write/debug rights.
    handle = api.OpenProcess(0x410, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    found = {}
    scanned = 0
    partial_reads = 0
    decoder = json.JSONDecoder()
    try:
        name = C.create_unicode_buffer(32768)
        length = W.DWORD(len(name))
        if not api.QueryFullProcessImageNameW(handle, 0, name, C.byref(length)):
            raise C.WinError(C.get_last_error())
        if Path(name.value).name.casefold() != 'happ.exe':
            raise RuntimeError('The selected process is not Happ.exe.')
        address = 0
        while True:
            info = memory_info()
            if not api.VirtualQueryEx(handle, C.c_void_p(address), C.byref(info),
                                      C.sizeof(info)):
                break
            base = info.BaseAddress or 0
            size = info.RegionSize
            # Committed private pages with readable protection, without GUARD.
            readable = (info.State == 0x1000 and info.Type == 0x20000
                        and not info.Protect & 0x101 and info.Protect & 0xee)
            if readable:
                overlap = b''
                for offset in range(0, size, CHUNK):
                    count = min(CHUNK, size - offset)
                    buffer = C.create_string_buffer(count)
                    received = C.c_size_t()
                    api.ReadProcessMemory(handle, C.c_void_p(base + offset),
                                          buffer, count, C.byref(received))
                    data = buffer.raw[:received.value]
                    scanned += len(data)
                    if received.value != count:
                        partial_reads += 1
                    if data:
                        inspect_chunk(overlap + data, found, decoder)
                    overlap = (overlap + data)[-OVERLAP:] if len(data) == count else b''
            address = base + size
            if address <= base:
                break
    finally:
        api.CloseHandle(handle)
    return found, scanned, partial_reads


def read_happ_configs():
    """Return in-memory configs without writing files or changing Happ."""
    api, memory_info, process_entry = windows_api()
    pids = find_happ(api, process_entry)
    if len(pids) != 1:
        raise RuntimeError("Open exactly one Happ client with its server list first.")
    found, scanned, partial_reads = scan_process(api, memory_info, pids[0])
    if not found or partial_reads:
        raise RuntimeError("Could not read complete Happ configurations.")
    return list(found.values())
