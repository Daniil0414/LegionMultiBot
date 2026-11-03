"""Mine controls embedded in the multibot's grid, above its managed windows."""
import logging
import os
import queue
import threading
import time
import tkinter as tk


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    _redraw_window = ctypes.WinDLL('user32', use_last_error=True).RedrawWindow
    _redraw_window.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.HRGN, wintypes.UINT]
    _redraw_window.restype = wintypes.BOOL
    RDW_INVALIDATE, RDW_ERASE, RDW_ALLCHILDREN = 0x0001, 0x0004, 0x0080


ERROR_TEXT = {
    'unsupported': 'Версия скрипта не поддерживает управление автошахтой.',
    'not_ready': 'Шамиль Клиент ещё не готов или не запущен на этой странице.',
    'unavailable': 'Окно или скрипт недоступны.',
    'not_confirmed': 'Скрипт не подтвердил переключение автошахты.',
    'superseded': 'Отменено следующей командой.',
}


class MineControlPanel(tk.Frame):
    def __init__(self, master, profiles, account_names=None):
        super().__init__(master, bg='#111827', highlightbackground='#334155', highlightthickness=1)
        self.profiles = profiles
        self.account_names = account_names or (lambda: {})
        self._messages = queue.Queue()
        self._operation = 0
        self._busy = False
        self._poll_id = None
        self._layout_id = None
        self._paint_id = None
        self._damage = None
        self._visible = False
        self._position = (18, 18)
        self._drag_offset = None
        self._host_binding = master.bind('<Configure>', self._host_resized, add='+')

        self.header = tk.Frame(self, bg='#1e293b', cursor='fleur', padx=12, pady=6)
        self.header.pack(fill='x')
        title = tk.Label(self.header, text='Управление роем', bg='#1e293b', fg='#f8fafc',
                         font=('Segoe UI', 11, 'bold'), cursor='fleur')
        title.pack(side='left')
        tk.Button(self.header, text='✕', command=self.hide, bg='#1e293b', fg='#94a3b8',
                  activebackground='#334155', activeforeground='white', relief='flat',
                  cursor='hand2', font=('Segoe UI', 10), padx=5).pack(side='right')
        for widget in (self.header, title):
            widget.bind('<ButtonPress-1>', self._start_drag)
            widget.bind('<B1-Motion>', self._drag)
            widget.bind('<ButtonRelease-1>', self._end_drag)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', self._end_drag)
        self.bind('<Unmap>', self._end_drag)

        body = tk.Frame(self, bg='#111827', padx=16, pady=12)
        body.pack(fill='both', expand=True)
        tk.Label(body, text='Все открытые окна, включая другие страницы сетки',
                 bg='#111827', fg='#94a3b8', font=('Segoe UI', 9), wraplength=330,
                 justify='left').pack(anchor='w', pady=(4, 12))
        self.toggle_button = tk.Button(body, command=lambda: self.submit(self._next_action),
            fg='white', activeforeground='white', font=('Segoe UI', 11, 'bold'),
            relief='flat', cursor='hand2', padx=12, pady=10)
        self.toggle_button.pack(fill='x', pady=(0, 12))
        self._set_action('start')
        self.status = tk.StringVar(self, 'Готово. Закрытые аккаунты не запускаются.')
        tk.Label(body, textvariable=self.status, bg='#111827', fg='#cbd5e1',
                 font=('Segoe UI', 9), wraplength=330, justify='left').pack(anchor='w')
        self.details = tk.Text(body, height=5, width=43, wrap='word', bg='#0b1220',
                               fg='#fca5a5', relief='flat', font=('Segoe UI', 9), state='disabled')
        self.update_idletasks()
        self._base_width = self.winfo_reqwidth()

    def _host_resized(self, event):
        if event.widget is self.master:
            self._queue_layout()

    def _queue_layout(self):
        if self._visible and self._layout_id is None:
            self._layout_id = self.after_idle(self._layout)

    def _layout(self):
        self._layout_id = None
        if not self._visible:
            return
        host_w, host_h = self.master.winfo_width(), self.master.winfo_height()
        width = min(max(self._base_width, self.winfo_reqwidth()), host_w)
        height = min(self.winfo_reqheight(), host_h)
        x = max(0, min(self._position[0], host_w - width))
        y = max(0, min(self._position[1], host_h - height))
        self._position = (x, y)
        old = self._bounds() if self.winfo_ismapped() else None
        self.place(x=x, y=y, width=width, height=height)
        self.lift()
        new = (x, y, x + width, y + height)
        if old != new:
            self._queue_repaint(old or new, new)

    def _bounds(self):
        x, y = self.winfo_x(), self.winfo_y()
        return x, y, x + self.winfo_width(), y + self.winfo_height()

    def _queue_repaint(self, old, new):
        regions = [old, new] + ([self._damage] if self._damage else [])
        self._damage = (min(r[0] for r in regions), min(r[1] for r in regions),
                        max(r[2] for r in regions), max(r[3] for r in regions))
        if self._paint_id is None:
            # The place manager first commits the native move. Repair its exposed
            # area in the next drawing idle, even while mouse messages stay busy.
            self._paint_id = self.after_idle(self._repaint)

    def _repaint(self):
        self._paint_id = None
        region, self._damage = self._damage, None
        if region is None:
            return
        host = self.master
        if os.name == 'nt':
            native_rect = wintypes.RECT(*region)
            # Invalidate foreign Chrome descendants too, without synchronously
            # waiting on their UI threads. ALLCHILDREN crosses clipping layers.
            _redraw_window(host.winfo_id(), ctypes.byref(native_rect), None,
                           RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN)
        hx, hy = host.winfo_rootx(), host.winfo_rooty()

        def expose(widget):
            if not widget.winfo_ismapped():
                return
            wx, wy = widget.winfo_rootx() - hx, widget.winfo_rooty() - hy
            left, top = max(region[0], wx), max(region[1], wy)
            right = min(region[2], wx + widget.winfo_width())
            bottom = min(region[3], wy + widget.winfo_height())
            if right <= left or bottom <= top:
                return
            # Tk ordinarily receives Expose through low-priority WM_PAINT.
            # Deliver its normal paint event now; drawing remains in Tk's idle
            # renderer. Walk Tk widgets only, never a foreign browser process.
            widget.event_generate('<Expose>', x=left - wx, y=top - wy,
                                  width=right - left, height=bottom - top, when='now')
            for child in widget.winfo_children():
                expose(child)

        expose(host)

    def _start_drag(self, event):
        self._drag_offset = (event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty())
        self.grab_set()
        return 'break'

    def _drag(self, event):
        if self._drag_offset is not None:
            self._position = (event.x_root - self.master.winfo_rootx() - self._drag_offset[0],
                              event.y_root - self.master.winfo_rooty() - self._drag_offset[1])
            self._queue_layout()
        return 'break'

    def _end_drag(self, event=None):
        self._drag_offset = None
        if self.grab_current() is self:
            self.grab_release()
        return 'break'

    def _set_action(self, action, enabled=True):
        self._next_action = action
        starting = action == 'start'
        self.toggle_button.config(
            text='Запустить автошахту для всех' if starting else 'Стоп',
            bg='#15803d' if starting else '#7f1d1d',
            activebackground='#166534' if starting else '#991b1b',
            state='normal' if enabled else 'disabled')

    def show(self):
        self._visible = True
        self.update_idletasks()
        self._layout()

    def hide(self):
        old = self._bounds() if self.winfo_ismapped() else None
        self._visible = False
        self._end_drag()
        self.place_forget()
        if old is not None:
            self._queue_repaint(old, old)

    def submit(self, action):
        if action not in ('start', 'pause'):
            raise ValueError('Unknown mine command')
        self._operation += 1
        operation, requested_at = self._operation, time.monotonic_ns()
        self._busy = True
        # Stop stays available while a start batch is being delivered.
        self._set_action('pause', enabled=action == 'start')
        self.details.pack_forget()
        self._queue_layout()
        self.status.set('Запускаю автошахту во всех окнах…' if action == 'start'
                        else 'Ставлю автошахту всех окон на паузу…')

        def work():
            try:
                results = self.profiles.command_mine_all(action, requested_at=requested_at)
                self._messages.put((operation, action, results, None))
            except Exception:
                logging.getLogger('legion.launch').exception('Swarm mine command failed')
                self._messages.put((operation, action, [], 'Не удалось отправить команду. Попробуйте ещё раз.'))

        threading.Thread(target=work, daemon=True, name='swarm-mine-control').start()
        if self._poll_id is None:
            self._poll_id = self.after(80, self._poll)

    def _poll(self):
        self._poll_id = None
        while True:
            try:
                operation, action, results, error = self._messages.get_nowait()
            except queue.Empty:
                break
            if operation != self._operation:
                continue
            self._busy = False
            if error:
                # Delivery may have partially succeeded; keep a stop retry available.
                self._set_action('pause')
                self.status.set(error)
            elif not results:
                self._set_action('start')
                self.status.set('Нет открытых окон. Сначала запустите аккаунты.')
            else:
                successes = sum(result.get('ok') is True for result in results)
                failures = [result for result in results if result.get('ok') is not True]
                if action == 'start':
                    uncertain = any(item.get('error') in ('unavailable', 'not_confirmed') for item in failures)
                    self._set_action('pause' if successes or uncertain else 'start')
                else:
                    self._set_action('pause' if failures else 'start')
                state = 'Копание включено' if action == 'start' else 'Автошахта на паузе'
                self.status.set(f'{state}: {successes} из {len(results)} окон.'
                                + (f' Не выполнено: {len(failures)}.' if failures else ''))
                if failures:
                    names = self.account_names()
                    lines = [f"{names.get(item.get('key'), item.get('key', 'Окно'))}: "
                             + ERROR_TEXT.get(item.get('error'), ERROR_TEXT['unavailable']) for item in failures]
                    self.details.config(state='normal')
                    self.details.delete('1.0', 'end')
                    self.details.insert('1.0', '\n'.join(lines))
                    self.details.config(state='disabled')
                    self.details.pack(fill='x', pady=(10, 0))
            self._queue_layout()
        if self._busy:
            self._poll_id = self.after(80, self._poll)

    def destroy(self):
        old = self._bounds() if self.winfo_ismapped() else None
        self._end_drag()
        self.master.unbind('<Configure>', self._host_binding)
        if self._layout_id is not None:
            self.after_cancel(self._layout_id)
            self._layout_id = None
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self._paint_id is not None:
            self.after_cancel(self._paint_id)
            self._paint_id = None
        if old is not None:
            self.place_forget()
            if self._damage:
                old = (min(old[0], self._damage[0]), min(old[1], self._damage[1]),
                       max(old[2], self._damage[2]), max(old[3], self._damage[3]))
            self._damage = old
        if self._damage:
            self._repaint()
        super().destroy()
