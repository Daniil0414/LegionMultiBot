"""Windows process ownership: no global Chrome termination or profile deletion."""
import ctypes
from ctypes import wintypes
import os
import time


def _kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    return api


class BrowserJob:
    """Kill only this browser's process tree if its launcher exits unexpectedly."""
    def __init__(self):
        api = self.api = _kernel()
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', ctypes.c_uint64 * 6),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        self.handle = api.CreateJobObjectW(None, None)  # Non-inherited and unnamed.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no resource limits.
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            try:
                self.api.TerminateJobObject(handle, 0)
                accounting = (ctypes.c_uint64 * 6)()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if not self.api.QueryInformationJobObject(handle, 1, accounting, ctypes.sizeof(accounting), None):
                        break
                    if ctypes.cast(accounting, ctypes.POINTER(wintypes.DWORD))[10] == 0:
                        break
                    time.sleep(.02)
            finally:
                self.api.CloseHandle(handle)


def resume_browser(process):
    """Resume the single primary thread after assigning a suspended child to its job."""
    api = _kernel()
    class ThreadEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                    ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    api.Thread32Next.argtypes = api.Thread32First.argtypes
    api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenThread.restype = wintypes.HANDLE
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    snapshot = api.CreateToolhelp32Snapshot(4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = api.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == process.pid:
                thread = api.OpenThread(2, False, entry.th32ThreadID)
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if api.ResumeThread(thread) == 0xffffffff:
                        raise ctypes.WinError(ctypes.get_last_error())
                    return
                finally:
                    api.CloseHandle(thread)
            found = api.Thread32Next(snapshot, ctypes.byref(entry))
        raise RuntimeError('Unable to find the suspended browser thread')
    finally:
        api.CloseHandle(snapshot)


def process_exists(pid):
    """Treat access denied as alive; never claim an unknown owner is abandoned."""
    api = _kernel()
    handle = api.OpenProcess(0x100000, False, int(pid))
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID.
    try:
        return api.WaitForSingleObject(handle, 0) != 0
    finally:
        api.CloseHandle(handle)


def close_abandoned_browser(identity, timeout=5):
    """Close a verified abandoned profile's root; PID reuse cannot affect another app."""
    api = _kernel()
    handle = api.OpenProcess(0x1000 | 0x100000 | 1, False, identity['pid'])
    if not handle:
        if not process_exists(identity['pid']):
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        size = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)) or not api.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        if created != identity['created'] or os.path.normcase(os.path.abspath(name.value)) != identity['executable']:
            raise RuntimeError('Browser process identity changed during recovery')
        user = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        @callback_type
        def close_window(hwnd, unused):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == identity['pid']:
                user.PostMessageW(hwnd, 0x0010, 0, 0)  # Normal close, including hidden windows.
            return True
        user.EnumWindows(close_window, 0)
        if api.WaitForSingleObject(handle, int(timeout * 1000)) == 0:
            return
        # A hung abandoned browser must not keep its profile locked forever.
        if not api.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if api.WaitForSingleObject(handle, 5000) != 0:
            raise RuntimeError('Abandoned browser did not exit')
    finally:
        api.CloseHandle(handle)
