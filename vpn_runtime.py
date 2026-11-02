"""Small OS helpers for ownership-safe sharing of the local Xray core."""
import contextlib
import ctypes
import json
import os
import sys
import tempfile
import time
import subprocess


def atomic_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         suffix='.json', delete=False) as stream:
            pending = stream.name
            os.chmod(pending, 0o600)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if pending and os.path.exists(pending):
            os.unlink(pending)


def process_identity(pid):
    """Identify a live process by PID, creation time and executable (PID reuse safe)."""
    if sys.platform != 'win32':
        try:
            with open('/proc/%s/stat' % int(pid)) as stream:
                fields = stream.read().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return None
            return {'pid': int(pid), 'created': fields[19],
                    'executable': os.path.realpath('/proc/%s/exe' % int(pid))}
        except (OSError, ValueError, IndexError):
            return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        size = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return {'pid': int(pid), 'created': str(created),
                'executable': os.path.normcase(os.path.abspath(name.value))}
    finally:
        kernel.CloseHandle(handle)


def process_is_alive(pid):
    """An unreadable process is alive until the OS proves otherwise."""
    if not pid or int(pid) <= 0:
        return True
    if sys.platform == 'win32':
        from browser_lifecycle import process_exists
        return process_exists(pid)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_details(pid):
    """Read a process's parent and exact argument vector for legacy recovery."""
    if sys.platform != 'win32':
        try:
            with open('/proc/%s/stat' % int(pid)) as stream:
                fields = stream.read().rsplit(')', 1)[1].split()
            with open('/proc/%s/cmdline' % int(pid), 'rb') as stream:
                arguments = [os.fsdecode(part) for part in stream.read().split(b'\0') if part]
            return {'parent_pid': int(fields[1]), 'arguments': arguments}
        except (OSError, ValueError, IndexError):
            return None
    script = ('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
              f'$p = Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}"; '
              'if ($p) { @{parent_pid=$p.ParentProcessId; command=$p.CommandLine} | ConvertTo-Json -Compress }')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', timeout=8, creationflags=0x08000000)
    if result.returncode or not result.stdout.strip():
        return None
    from ctypes import wintypes
    details = json.loads(result.stdout)
    command = details.get('command')
    if not command:
        return None
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not arguments:
        return None
    try:
        return {'parent_pid': int(details['parent_pid']),
                'arguments': [arguments[index] for index in range(count.value)]}
    finally:
        kernel.LocalFree(arguments)


def terminate_verified_process(identity, timeout=5):
    """Stop the recorded process through a verified handle, never by PID alone."""
    if sys.platform != 'win32':
        import signal
        descriptor = os.pidfd_open(identity['pid'])
        try:
            if process_identity(identity['pid']) != identity:
                raise RuntimeError('Process identity changed during recovery')
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while process_identity(identity['pid']) == identity:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Recovered process did not exit')
                time.sleep(.02)
        finally:
            os.close(descriptor)
        return
    from browser_lifecycle import _kernel
    from ctypes import wintypes
    api = _kernel()
    handle = api.OpenProcess(0x1000 | 0x100000 | 1, False, identity['pid'])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        size = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if (not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))
                or not api.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))):
            raise ctypes.WinError(ctypes.get_last_error())
        created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        executable = os.path.normcase(os.path.abspath(name.value))
        if created != identity['created'] or executable != identity['executable']:
            raise RuntimeError('Process identity changed during recovery')
        if not api.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if api.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            raise TimeoutError('Recovered process did not exit')
    finally:
        api.CloseHandle(handle)


@contextlib.contextmanager
def runtime_lock(path, timeout=15):
    """Serialize lifecycle changes across GUI and command line launchers."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'a+b') as stream:
        if os.path.getsize(path) == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('VPN core is busy in another launcher')
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
