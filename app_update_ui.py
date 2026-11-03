"""Startup update prompt. Network and disk work never run on the Tk thread."""
import logging
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from app_updater import (UpdateCancelled, check_for_update, installation_target,
                         prepare_update, start_installer)
from app_version import APP_VERSION

LOG = logging.getLogger('legion.launch')


class StartupUpdater:
    def __init__(self, root, source_dir, on_restart, entry_point='app_container.py'):
        self.root = root
        self.source_dir = Path(source_dir)
        self.on_restart = on_restart
        self.entry_point = entry_point
        self.events = queue.SimpleQueue()
        self._result_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.dialog = None
        self.prepared = None
        self.installing = False
        self.closed = False
        self.started = False
        self.root.bind('<Destroy>', self._destroyed, add='+')

    def start(self):
        if self.started or self.closed:
            return
        self.started = True
        self._run('checked', check_for_update)
        self.root.after(100, self._poll)

    def _run(self, event, function, *args):
        def worker():
            try:
                result = function(*args)
                with self._result_lock:
                    if self.closed:
                        if event == 'prepared':
                            result.discard()
                        return
                    self.events.put((event, result))
            except UpdateCancelled:
                self.events.put(('cancelled', None))
            except Exception as exc:
                LOG.warning('Application update %s failed: %s', event, exc)
                self.events.put(('check_error' if event == 'checked' else 'error', str(exc)))
        threading.Thread(target=worker, name='legion-update', daemon=True).start()

    def _poll(self):
        if self.closed or getattr(self.root, '_shutting_down', False):
            self._close()
            return
        while not self.events.empty():
            event, value = self.events.get()
            if event in ('check_error', 'cancelled') or (event == 'checked' and value is None):
                return
            if event == 'checked':
                # Do not steal a grab from the VPN settings or another modal window.
                if self.root.grab_current() is not None:
                    self.events.put((event, value))
                    break
                self._show_offer(value)
            elif event == 'progress' and self.dialog is not None:
                downloaded, total = value
                megabytes = downloaded / (1024 * 1024)
                if total:
                    self.progress.configure(mode='determinate', maximum=total, value=downloaded)
                    self.status.configure(text=f'Скачивание: {megabytes:.1f} / {total / (1024 * 1024):.1f} МБ')
                else:
                    self.status.configure(text=f'Скачивание: {megabytes:.1f} МБ')
            elif event == 'prepared':
                if self.cancelled.is_set():
                    value.discard()
                    return
                self.prepared = value
                self.installing = True
                self.skip_button.configure(state='disabled')
                self.status.configure(text='Подготовка перезапуска…')
                self._run('ready', start_installer, value, self.entry_point)
            elif event == 'ready':
                self.status.configure(text='Закрытие приложения для установки обновления…')
                try:
                    self.on_restart()
                except Exception:
                    # A waiting helper must never install at a later, unrelated application exit.
                    value.terminate()
                    value.wait(timeout=5)
                    self._show_error('Не удалось закрыть приложение для перезапуска.')
                else:
                    return
            elif event == 'error':
                self._show_error(value)
        if not self.closed:
            self.root.after(100, self._poll)

    def _show_offer(self, release):
        dialog = self.dialog = tk.Toplevel(self.root)
        dialog.title('Доступно обновление Legion Multi-Bot')
        dialog.configure(bg='#0d1424')
        dialog.resizable(False, False)
        dialog.transient(self.root)
        content = tk.Frame(dialog, bg='#0d1424', padx=24, pady=20)
        content.pack(fill='both', expand=True)
        tk.Label(content, text='Вышла новая версия на GitHub', bg='#0d1424', fg='#ffffff',
                 font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        tk.Label(content, text=f'Текущая версия: {APP_VERSION}    Новая версия: {release.version}',
                 bg='#0d1424', fg='#60a5fa', font=('Segoe UI', 10)).pack(anchor='w', pady=(10, 8))
        tk.Label(content, text='Для установки приложение и его игровые окна будут закрыты.\n'
                 'Аккаунты, профили и настройки сохранятся.', justify='left',
                 bg='#0d1424', fg='#cbd5e1', font=('Segoe UI', 10)).pack(anchor='w')
        if release.notes.strip():
            notes = tk.Text(content, width=66, height=6, wrap='word', bg='#131d33', fg='#cbd5e1',
                            relief='flat', padx=10, pady=8, font=('Segoe UI', 9))
            notes.insert('1.0', release.notes[:1800])
            notes.configure(state='disabled')
            notes.pack(fill='x', pady=(12, 0))
        self.status = tk.Label(content, text='', bg='#0d1424', fg='#94a3b8', font=('Segoe UI', 9),
                               wraplength=510, justify='left')
        self.status.pack(anchor='w', pady=(12, 4))
        self.progress = ttk.Progressbar(content, mode='determinate', length=510)
        self.progress.pack(fill='x')
        buttons = tk.Frame(content, bg='#0d1424')
        buttons.pack(fill='x', pady=(16, 0))
        self.update_button = tk.Button(buttons, text='Обновить и перезапустить',
                                       command=lambda: self._download(release), bg='#166534', fg='white',
                                       relief='flat', padx=14, pady=9, font=('Segoe UI', 10, 'bold'))
        self.update_button.pack(side='left')
        self.skip_button = tk.Button(buttons, text='Не обновлять', command=self._skip,
                                     bg='#334155', fg='white', relief='flat', padx=14, pady=9,
                                     font=('Segoe UI', 10))
        self.skip_button.pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW', self._skip)
        dialog.bind('<Escape>', lambda event: self._skip())
        dialog.update_idletasks()
        x = max(0, self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2)
        y = max(0, self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f'+{x}+{y}')
        dialog.grab_set()
        self.skip_button.focus_set()

    def _download(self, release):
        self.update_button.configure(state='disabled')
        self.status.configure(text='Скачивание обновления… Можно отменить кнопкой «Не обновлять».')
        last_report = 0

        def progress(downloaded, total):
            nonlocal last_report
            now = time.monotonic()
            if now - last_report >= 0.15 or downloaded == total:
                self.events.put(('progress', (downloaded, total)))
                last_report = now

        def prepare():
            return prepare_update(release, installation_target(self.source_dir), progress, self.cancelled)

        self._run('prepared', prepare)

    def _show_error(self, error):
        if self.prepared:
            self.prepared.discard()
            self.prepared = None
        self.installing = False
        self.progress.configure(value=0)
        self.status.configure(text='Обновление не установлено. Можно повторить попытку или продолжить работу.')
        self.update_button.configure(state='normal')
        self.skip_button.configure(state='normal')
        messagebox.showerror('Ошибка обновления', error, parent=self.dialog)

    def _skip(self):
        if self.installing:
            return
        self._close()
        if self.prepared:
            self.prepared.discard()
        if self.dialog:
            self.dialog.destroy()
            self.dialog = None

    def _destroyed(self, event):
        if event.widget is self.root:
            self._close()

    def _close(self):
        with self._result_lock:
            self.closed = True
            self.cancelled.set()
            while not self.events.empty():
                event, value = self.events.get()
                if event == 'prepared':
                    value.discard()
