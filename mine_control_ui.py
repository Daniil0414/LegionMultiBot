"""Mine controls embedded as an owned frameless overlay above the multibot's grid."""
import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    GA_ROOT = 2
    GWLP_HWNDPARENT = -8
    _get_ancestor = ctypes.windll.user32.GetAncestor
    _get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _get_ancestor.restype = wintypes.HWND
    _set_window_long = getattr(ctypes.windll.user32, 'SetWindowLongPtrW', ctypes.windll.user32.SetWindowLongW)
    _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _set_window_long.restype = ctypes.c_void_p


ERROR_TEXT = {
    'unsupported': 'Версия скрипта не поддерживает управление автошахтой.',
    'not_ready': 'Шамиль Клиент ещё не готов или не запущен на этой странице.',
    'unavailable': 'Окно или скрипт недоступны.',
    'not_confirmed': 'Скрипт не подтвердил переключение автошахты.',
    'superseded': 'Отменено следующей командой.',
}
DELAY_OPTIONS = {'0 сек': (0, 0), '1–5 сек': (1, 5), '1–10 сек': (1, 10)}


class MineControlPanel(tk.Toplevel):
    def __init__(self, master, profiles, account_names=None):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg='#111827', highlightbackground='#334155', highlightthickness=1)

        self.profiles = profiles
        self.account_names = account_names or (lambda: {})
        self._messages = queue.Queue()
        self._progress = queue.SimpleQueue()
        self._operation = 0
        self._busy = False
        self._swarm_active = False
        self._periodic_timer = None
        self._poll_id = None
        self._layout_id = None
        self._visible = False
        self._position = (18, 18)
        self._drag_offset = None

        top = master.winfo_toplevel()
        self.transient(top)
        if os.name == 'nt':
            try:
                root_hwnd = _get_ancestor(top.winfo_id(), GA_ROOT)
                t_hwnd = _get_ancestor(self.winfo_id(), GA_ROOT)
                if root_hwnd and t_hwnd:
                    _set_window_long(t_hwnd, GWLP_HWNDPARENT, root_hwnd)
            except Exception:
                logging.getLogger('legion.launch').debug('Failed to configure Win32 owner for MineControlPanel')

        self._host_binding = master.bind('<Configure>', self._on_container_configure, add='+')
        self._top_binding = top.bind('<Configure>', self._on_container_configure, add='+') if top is not master else None

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
        delay_row = tk.Frame(body, bg='#111827')
        delay_row.pack(fill='x', pady=(0, 12))
        tk.Label(delay_row, text='Задержка отправки в шахту\nмежду окнами:', bg='#111827', fg='#cbd5e1',
                 font=('Segoe UI', 9), justify='left', anchor='w').pack(side='left')
        self.delay = tk.StringVar(self, '1–5 сек')
        self.delay_picker = ttk.Combobox(delay_row, textvariable=self.delay, values=list(DELAY_OPTIONS),
                                          state='readonly', width=10)
        self.delay_picker.pack(side='right')
        self.auto_watch = tk.BooleanVar(self, True)
        self.auto_watch_check = tk.Checkbutton(
            body, text='Проверять статус раз в минуту (автоподхват)',
            variable=self.auto_watch, command=self._on_auto_watch_toggled,
            bg='#111827', fg='#cbd5e1', selectcolor='#1e293b',
            activebackground='#111827', activeforeground='#f8fafc',
            font=('Segoe UI', 9), anchor='w', cursor='hand2')
        self.auto_watch_check.pack(fill='x', pady=(0, 10))
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

    def winfo_x(self):
        try:
            return self.winfo_rootx() - self.master.winfo_rootx()
        except Exception:
            return self._position[0]

    def winfo_y(self):
        try:
            return self.winfo_rooty() - self.master.winfo_rooty()
        except Exception:
            return self._position[1]

    def _bounds(self):
        x, y = self.winfo_x(), self.winfo_y()
        return x, y, x + self.winfo_width(), y + self.winfo_height()

    def _on_container_configure(self, event):
        if event.widget in (self.master, self.master.winfo_toplevel()) and self._visible:
            self._sync_position()

    def _queue_layout(self):
        if self._visible and self._layout_id is None:
            self._layout_id = self.after_idle(self._layout)

    def _layout(self):
        self._layout_id = None
        if not self._visible or not self.winfo_exists():
            return
        self.update_idletasks()
        self._sync_position()

    def _sync_position(self):
        if not self._visible or not self.winfo_exists():
            return
        host = self.master
        try:
            host_w, host_h = host.winfo_width(), host.winfo_height()
            host_x, host_y = host.winfo_rootx(), host.winfo_rooty()
        except tk.TclError:
            return

        panel_w = max(self._base_width, self.winfo_reqwidth())
        panel_h = self.winfo_reqheight()

        if host_w > 0:
            panel_w = min(panel_w, host_w)
        if host_h > 0:
            panel_h = min(panel_h, host_h)

        max_x = max(0, host_w - panel_w)
        max_y = max(0, host_h - panel_h)

        rel_x = max(0, min(self._position[0], max_x))
        rel_y = max(0, min(self._position[1], max_y))
        self._position = (rel_x, rel_y)

        self.geometry(f"{panel_w}x{panel_h}+{host_x + rel_x}+{host_y + rel_y}")

    def _start_drag(self, event):
        self._drag_offset = (event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty())
        return 'break'

    def _drag(self, event):
        if self._drag_offset is not None and self._visible:
            host = self.master
            try:
                host_w, host_h = host.winfo_width(), host.winfo_height()
                host_x, host_y = host.winfo_rootx(), host.winfo_rooty()
                panel_w, panel_h = self.winfo_width(), self.winfo_height()
            except tk.TclError:
                return 'break'

            target_screen_x = event.x_root - self._drag_offset[0]
            target_screen_y = event.y_root - self._drag_offset[1]

            rel_x = target_screen_x - host_x
            rel_y = target_screen_y - host_y

            max_x = max(0, host_w - panel_w)
            max_y = max(0, host_h - panel_h)

            clamped_rel_x = max(0, min(rel_x, max_x))
            clamped_rel_y = max(0, min(rel_y, max_y))

            self._position = (clamped_rel_x, clamped_rel_y)
            self.geometry(f"+{host_x + clamped_rel_x}+{host_y + clamped_rel_y}")
        return 'break'

    def _end_drag(self, event=None):
        self._drag_offset = None
        return 'break'

    def _set_action(self, action, enabled=True):
        self._next_action = action
        starting = action == 'start'
        self.toggle_button.config(
            text='Запустить всех в шахту' if starting else 'Стоп',
            bg='#15803d' if starting else '#7f1d1d',
            activebackground='#166534' if starting else '#991b1b',
            state='normal' if enabled else 'disabled')

    def show(self):
        self._visible = True
        self.deiconify()
        self.lift()
        self.update_idletasks()
        self._sync_position()

    def hide(self):
        self._visible = False
        self._end_drag()
        self.withdraw()

    def submit(self, action, auto=False):
        if action not in ('start', 'pause'):
            raise ValueError('Unknown mine command')
        if action == 'start':
            self._swarm_active = True
        else:
            self._swarm_active = False
            self._cancel_periodic_check()
        self._operation += 1
        operation, requested_at = self._operation, time.perf_counter_ns()
        cancel = getattr(self.profiles, 'cancel_mine_commands', None)
        if cancel:
            cancel(before=requested_at)
        delay_range = DELAY_OPTIONS[self.delay.get()]
        profile_order = list(self.account_names())
        self._busy = True
        self.delay_picker.configure(state='disabled')
        # Stop stays available while a start batch is being delivered.
        self._set_action('pause', enabled=action == 'start')
        self.details.pack_forget()
        self._queue_layout()
        if auto:
            self.status.set('Автоподхват роя: запускаю автошахту…')
        else:
            self.status.set('Запускаю автошахту во всех окнах…' if action == 'start'
                            else 'Ставлю автошахту всех окон на паузу…')

        def work():
            try:
                results = self.profiles.command_mine_all(action, requested_at=requested_at,
                    delay_range=delay_range, profile_order=profile_order,
                    progress=lambda state: self._progress.put((operation, state)))
                self._messages.put((operation, action, results, None))
            except Exception:
                logging.getLogger('legion.launch').exception('Swarm mine command failed')
                self._messages.put((operation, action, [], 'Не удалось отправить команду. Попробуйте ещё раз.'))

        threading.Thread(target=work, daemon=True, name='swarm-mine-control').start()
        if self._poll_id is None:
            self._poll_id = self.after(80, self._poll)

    def _poll(self):
        self._poll_id = None
        while not self._progress.empty():
            operation, state = self._progress.get_nowait()
            if operation == self._operation and self._busy:
                text = f"Ответили: {state['completed']} из {state['total']} окон."
                if state['delay']:
                    text += f" Следующее окно через {state['delay']} с."
                self.status.set(text)
        while True:
            try:
                operation, action, results, error = self._messages.get_nowait()
            except queue.Empty:
                break
            if operation != self._operation:
                continue
            if action == 'periodic_status':
                if not self._swarm_active or not self.auto_watch.get() or self._busy:
                    continue
                paused = [r for r in results if r.get('ok') and r.get('state') == 'paused']
                running = [r for r in results if r.get('ok') and r.get('state') == 'running']
                has_not_ready = any(r.get('error') == 'not_ready' for r in results)
                if paused:
                    names = self.account_names()
                    paused_names = ", ".join(names.get(p.get('key'), p.get('key', 'Окно')) for p in paused)
                    logging.getLogger('legion.launch').info(
                        'Auto-swarm detected %d paused window(s) (%s), relaunching into mine...',
                        len(paused), paused_names)
                    self.submit('start', auto=True)
                else:
                    if running:
                        self.status.set(f"Рой активен: {len(running)} из {len(results)} окон копают. Автопроверка: раз в мин.")
                    self._schedule_periodic_check(15000 if has_not_ready else 60000)
                continue
            self._busy = False
            self.delay_picker.configure(state='readonly')
            if error:
                # Delivery may have partially succeeded; keep a stop retry available.
                self._set_action('pause')
                self.status.set(error)
            elif not results:
                self._set_action('start')
                self._swarm_active = False
                self._cancel_periodic_check()
                self.status.set('Нет открытых окон. Сначала запустите аккаунты.')
            else:
                successes = sum(result.get('ok') is True for result in results)
                failures = [result for result in results if result.get('ok') is not True]
                if action == 'start':
                    uncertain = any(item.get('error') in ('unavailable', 'not_confirmed') for item in failures)
                    self._set_action('pause' if successes or uncertain else 'start')
                    if successes or uncertain:
                        self._swarm_active = True
                        self._schedule_periodic_check(60000)
                    else:
                        self._swarm_active = False
                        self._cancel_periodic_check()
                else:
                    self._swarm_active = False
                    self._cancel_periodic_check()
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

    def _cancel_periodic_check(self):
        if self._periodic_timer is not None:
            self.after_cancel(self._periodic_timer)
            self._periodic_timer = None

    def _schedule_periodic_check(self, delay_ms=60000):
        self._cancel_periodic_check()
        if self._swarm_active and self.auto_watch.get():
            self._periodic_timer = self.after(delay_ms, self._periodic_check)

    def _periodic_check(self):
        self._periodic_timer = None
        if not self._swarm_active or not self.auto_watch.get() or self._busy:
            if self._swarm_active and self.auto_watch.get():
                self._schedule_periodic_check(10000)
            return
        op = self._operation

        def work():
            try:
                results = self.profiles.command_mine_all('status', delay_range=(0, 0))
                self._messages.put((op, 'periodic_status', results, None))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True, name='swarm-mine-status').start()
        if self._poll_id is None:
            self._poll_id = self.after(80, self._poll)

    def _on_auto_watch_toggled(self):
        if self._swarm_active and self.auto_watch.get():
            self._schedule_periodic_check(2000)
        elif not self.auto_watch.get():
            self._cancel_periodic_check()

    def destroy(self):
        self._cancel_periodic_check()
        self._end_drag()
        try:
            self.master.unbind('<Configure>', self._host_binding)
        except Exception:
            pass
        if self._top_binding:
            try:
                self.master.winfo_toplevel().unbind('<Configure>', self._top_binding)
            except Exception:
                pass
        if self._layout_id is not None:
            self.after_cancel(self._layout_id)
            self._layout_id = None
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()
        self.status = self.delay = None
