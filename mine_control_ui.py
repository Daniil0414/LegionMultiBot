"""Floating native controls for the mine toggle in all managed Shamil windows."""
import logging
import queue
import threading
import time
import tkinter as tk


ERROR_TEXT = {
    'unsupported': 'Версия скрипта не поддерживает управление автошахтой.',
    'not_ready': 'Шамиль Клиент ещё не готов или не запущен на этой странице.',
    'unavailable': 'Окно или скрипт недоступны.',
    'not_confirmed': 'Скрипт не подтвердил переключение автошахты.',
    'superseded': 'Отменено следующей командой.',
}


class MineControlOverlay(tk.Toplevel):
    def __init__(self, master, profiles, account_names=None):
        super().__init__(master)
        self.withdraw()
        self.profiles = profiles
        self.account_names = account_names or (lambda: {})
        self._messages = queue.Queue()
        self._operation = 0
        self._busy = False
        self._poll_id = None
        self.title('Шамиль Клиент — управление роем')
        self.configure(bg='#111827')
        self.resizable(False, False)
        self.transient(master)
        self.attributes('-topmost', True)
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.geometry(f'+{max(0, master.winfo_rootx() + 28)}+{max(0, master.winfo_rooty() + 72)}')

        body = tk.Frame(self, bg='#111827', padx=16, pady=14)
        body.pack(fill='both', expand=True)
        tk.Label(body, text='Управление роем', bg='#111827', fg='#f8fafc',
                 font=('Segoe UI', 13, 'bold')).pack(anchor='w')
        tk.Label(body, text='Все открытые окна, включая другие страницы сетки',
                 bg='#111827', fg='#94a3b8', font=('Segoe UI', 9), wraplength=330,
                 justify='left').pack(anchor='w', pady=(4, 12))
        self.start_button = tk.Button(body, text='⛏ Отправить рой в шахту',
            command=lambda: self.submit('start'), bg='#15803d', fg='white',
            activebackground='#166534', activeforeground='white', font=('Segoe UI', 11, 'bold'),
            relief='flat', cursor='hand2', padx=12, pady=10)
        self.start_button.pack(fill='x')
        self.stop_button = tk.Button(body, text='⏸ Стоп — пауза автошахты',
            command=lambda: self.submit('pause'), bg='#7f1d1d', fg='white',
            activebackground='#991b1b', activeforeground='white', font=('Segoe UI', 10, 'bold'),
            relief='flat', cursor='hand2', padx=12, pady=9)
        self.stop_button.pack(fill='x', pady=(8, 12))
        self.status = tk.StringVar(self, 'Готово. Закрытые аккаунты не запускаются.')
        tk.Label(body, textvariable=self.status, bg='#111827', fg='#cbd5e1',
                 font=('Segoe UI', 9), wraplength=330, justify='left').pack(anchor='w')
        self.details = tk.Text(body, height=5, width=43, wrap='word', bg='#0b1220',
                               fg='#fca5a5', relief='flat', font=('Segoe UI', 9), state='disabled')

    def show(self):
        self.deiconify()
        self.lift()

    def submit(self, action):
        if action not in ('start', 'pause'):
            raise ValueError('Unknown mine command')
        self._operation += 1
        operation, requested_at = self._operation, time.monotonic_ns()
        self._busy = True
        self.start_button.config(state='disabled')
        # Stop stays available while a start batch is being delivered.
        self.stop_button.config(state='normal' if action == 'start' else 'disabled')
        self.details.pack_forget()
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
            self.start_button.config(state='normal')
            self.stop_button.config(state='normal')
            if error:
                self.status.set(error)
            elif not results:
                self.status.set('Нет открытых окон. Сначала запустите аккаунты.')
            else:
                successes = sum(result.get('ok') is True for result in results)
                failures = [result for result in results if result.get('ok') is not True]
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
        if self._busy:
            self._poll_id = self.after(80, self._poll)

    def destroy(self):
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()
