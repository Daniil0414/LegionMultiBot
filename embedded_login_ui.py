"""A browser login workspace and its OAuth popups, all children of the main UI."""
import ctypes
from ctypes import wintypes as w
import time
import tkinter as tk


native = ctypes.WinDLL('user32', use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
for name, result, args in (
    ('EnumWindows', w.BOOL, [EnumProc, w.LPARAM]),
    ('GetWindowThreadProcessId', w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
    ('GetClassNameW', ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
    ('GetWindowLongW', w.LONG, [w.HWND, ctypes.c_int]),
    ('SetWindowLongW', w.LONG, [w.HWND, ctypes.c_int, w.LONG]),
    ('GetParent', w.HWND, [w.HWND]), ('SetParent', w.HWND, [w.HWND, w.HWND]),
    ('IsWindow', w.BOOL, [w.HWND]), ('IsWindowVisible', w.BOOL, [w.HWND]),
    ('IsIconic', w.BOOL, [w.HWND]),
    ('GetWindowRect', w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)]),
    ('SetWindowPos', w.BOOL, [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]),
    ('ShowWindow', w.BOOL, [w.HWND, ctypes.c_int]),
    ('PostMessageW', w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
    ('RedrawWindow', w.BOOL, [w.HWND, ctypes.POINTER(w.RECT), w.HANDLE, w.UINT]),
):
    function = getattr(native, name)
    function.restype, function.argtypes = result, args


def browser_windows(pid):
    result = []
    @EnumProc
    def visit(hwnd, _):
        owner = w.DWORD()
        native.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and native.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(128)
            native.GetClassNameW(hwnd, name, len(name))
            if (name.value == 'Chrome_WidgetWin_1'
                    and native.GetWindowLongW(hwnd, -16) & 0x00C00000 == 0x00C00000):
                result.append(hwnd)
        return True
    native.EnumWindows(visit, 0)
    return result


class EmbeddedLoginView(tk.Frame):
    def __init__(self, master, finished):
        super().__init__(master, bg='#0b1120')
        self.finished = finished
        self.key = None
        self.pid = None
        self.visible = False
        self._windows = []
        self._primary = self._active = None
        self._timer = None
        self._ready = self._failed = self._cancelled = None
        self._opened = False
        bar = tk.Frame(self, bg='#131d33', padx=12, pady=9)
        bar.pack(fill='x')
        tk.Button(bar, text='← Вернуться к сетке', command=lambda: self.finished(self),
                  bg='#2563eb', fg='white', activebackground='#1d4ed8', activeforeground='white',
                  relief='flat', padx=12, pady=6, cursor='hand2', font=('Segoe UI', 10, 'bold')).pack(side='right')
        self.heading = tk.Label(bar, bg='#131d33', fg='#f8fafc', font=('Segoe UI', 12, 'bold'))
        self.heading.pack(side='left')
        tk.Label(self, text='Войдите через Twitch в окне ниже. После входа нажмите «Вернуться к сетке».',
                 bg='#0b1120', fg='#94a3b8', font=('Segoe UI', 9), anchor='w', padx=12, pady=7).pack(fill='x')
        self.viewport = tk.Frame(self, bg='#0b1120')
        self.viewport.pack(fill='both', expand=True)
        self.viewport.bind('<Configure>', lambda event: self._fit_active())

    @property
    def browser_hwnds(self):
        result = []
        for hwnd in self._windows:
            owner = w.DWORD()
            if native.IsWindow(hwnd):
                native.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == self.pid:
                    result.append(hwnd)
        return tuple(result)

    @property
    def primary_hwnd(self):
        if self._primary in self._windows and native.IsWindow(self._primary):
            return self._primary
        return next(iter(self.browser_hwnds), None)

    def owns(self, hwnd):
        return hwnd in self.browser_hwnds

    def show(self, key, name, pid, hwnd=None, ready=None, failed=None, cancelled=None, timeout=15):
        self._stop_poll()
        self.key, self.pid = key, pid
        self._windows = []
        self._primary = self._active = None
        self._opened = False
        self._ready, self._failed, self._cancelled = ready, failed, cancelled
        self._deadline = time.monotonic() + timeout
        self.heading.configure(text=f'Вход в аккаунт · {name}')
        self.visible = True
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self.update_idletasks()
        if hwnd and native.IsWindow(hwnd):
            owner = w.DWORD()
            native.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                try:
                    self._adopt(hwnd)
                except OSError as error:
                    self._fail(error)
                    return
                self._primary = hwnd
        self._poll()

    def _adopt(self, hwnd):
        parent = self.viewport.winfo_id()
        native.ShowWindow(hwnd, 0)
        for widget in (self, self.viewport):
            handle = widget.winfo_id()
            style = native.GetWindowLongW(handle, -16)
            native.SetWindowLongW(handle, -16, style | 0x02000000 | 0x04000000)
        style = native.GetWindowLongW(hwnd, -16)
        style = ((style & ~0x80000000) | 0x40000000 | 0x02000000 | 0x04000000) & ~0x00C40000
        native.SetWindowLongW(hwnd, -16, style)
        extended = native.GetWindowLongW(hwnd, -20)
        native.SetWindowLongW(hwnd, -20, extended & ~(0x00000200 | 0x00000100 | 0x00020000 | 0x00040000))
        native.SetParent(hwnd, parent)
        if native.GetParent(hwnd) != parent:
            native.PostMessageW(hwnd, 0x0010, 0, 0)
            raise OSError('Не удалось встроить Chrome в окно мультибота.')
        if native.IsIconic(hwnd):
            native.ShowWindow(hwnd, 9)
        native.SetWindowPos(hwnd, None, 0, 0, max(100, self.viewport.winfo_width()),
                            max(100, self.viewport.winfo_height()), 0x0004 | 0x0010 | 0x0020 | 0x0040)
        if hwnd not in self._windows:
            self._windows.append(hwnd)

    def _activate(self, hwnd):
        for other in self.browser_hwnds:
            if other != hwnd:
                native.ShowWindow(other, 0)
        self._active = hwnd
        self._fit_active()

    def _fit_active(self):
        if self._active and native.IsWindow(self._active) and self.visible:
            native.SetWindowPos(self._active, None, 0, 0, max(100, self.viewport.winfo_width()),
                                max(100, self.viewport.winfo_height()), 0x0004 | 0x0010 | 0x0040 | 0x4000)

    def _poll(self):
        self._timer = None
        if not self.visible:
            return
        if self._cancelled and self._cancelled():
            self._fail(RuntimeError('Открытие окна отменено.'))
            return
        try:
            self._windows = list(self.browser_hwnds)
            for hwnd in tuple(self._windows):
                if native.GetParent(hwnd) != self.viewport.winfo_id():
                    self._adopt(hwnd)
                    if hwnd == self._active:
                        self._fit_active()
            new = [hwnd for hwnd in browser_windows(self.pid) if hwnd not in self._windows]
            for hwnd in new:
                try:
                    self._adopt(hwnd)
                except OSError:
                    if native.IsWindow(hwnd):
                        raise
            new = [hwnd for hwnd in new if hwnd in self._windows and native.IsWindow(hwnd)]
            alive = self.browser_hwnds
            if self._primary is None and new:
                self._primary = new[0]
            if self._opened and new:
                self._activate(new[0])
            elif not self._active or not native.IsWindow(self._active):
                if alive:
                    self._activate(self.primary_hwnd)
            if alive and not self._opened:
                self._opened = True
                callback, self._ready = self._ready, None
                if callback:
                    callback(self.primary_hwnd)
            elif not alive:
                if self._opened:
                    self.finished(self)
                    return
                if time.monotonic() >= self._deadline:
                    self._fail(TimeoutError('Chrome запущен, но окно входа не найдено. Повторите попытку.'))
                    return
        except OSError as error:
            if self._opened:
                self.finished(self)
            else:
                self._fail(error)
            return
        if self.visible:
            self._timer = self.after(100, self._poll)

    def _fail(self, error):
        callback, self._failed = self._failed, None
        windows = self.browser_hwnds
        self.release()
        for hwnd in windows:
            native.PostMessageW(hwnd, 0x0010, 0, 0)
        if callback:
            callback(error)

    def release(self):
        """Release ownership for reparenting directly to a grid slot."""
        primary, windows = self.primary_hwnd, self.browser_hwnds
        self._stop_poll()
        self.visible = False
        self.place_forget()
        self._windows = []
        self._primary = self._active = self.pid = None
        self._ready = self._failed = self._cancelled = None
        native.RedrawWindow(self.master.winfo_id(), None, None, 0x0001 | 0x0004 | 0x0080)
        return primary, windows

    def _stop_poll(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None

    def destroy(self):
        self._stop_poll()
        super().destroy()
        self.finished = self._ready = self._failed = self._cancelled = None
        self.heading = self.viewport = self.master = None
