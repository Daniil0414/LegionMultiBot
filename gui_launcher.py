import os
import sys
import json
import time
import subprocess
import threading
import logging
import tkinter as tk
from tkinter import ttk, messagebox
from proxy_manager import ProxyManager
from browser_profiles import BrowserProfiles
from app_update_ui import StartupUpdater
from app_paths import DATA_DIR

# Locate Chrome
def find_chrome():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "chrome.exe"

CHROME_EXE = find_chrome()

# Load Profiles from Chrome Local State
def get_chrome_profiles():
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    local_state_path = os.path.join(local_app_data, r"Google\Chrome\User Data\Local State")
    if not os.path.exists(local_state_path):
        return []

    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        info_cache = data.get("profile", {}).get("info_cache", {})
        profiles = []
        for key, p in info_cache.items():
            profile_dir = os.path.join(local_app_data, r"Google\Chrome\User Data", key)
            ext_dir = os.path.join(profile_dir, "Extensions")
            has_tm = False
            if os.path.exists(ext_dir):
                exts = os.listdir(ext_dir)
                has_tm = "dhdgffkkebhmkfjojejmpbldmpobfkfo" in exts or "gcalnhmpgdcmnakmmamnnemloikljcch" in exts

            profiles.append({
                "key": key,
                "name": p.get("name") or key,
                "gaia": p.get("gaia_name") or p.get("gaia_given_name") or "",
                "email": p.get("user_name") or "",
                "has_tm": has_tm,
                "is_default": key == "Default",
                "active_time": p.get("active_time", 0)
            })

        # Sort: Default first, then by active_time desc
        profiles.sort(key=lambda x: (not x["is_default"], -x["active_time"]))
        return profiles
    except Exception as e:
        print(f"Error reading profiles: {e}")
        return []

class ProfilePagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("⛏️ Legion Multi-Bot Pager (по 6 окон)")
        self.geometry("820x640")
        self.minsize(700, 520)
        self.configure(bg="#0b0f19")

        self.proxy_manager = ProxyManager(str(DATA_DIR))
        self.browser_profiles = BrowserProfiles()
        self._shutting_down = False
        self.protocol('WM_DELETE_WINDOW', self.shutdown_app)
        self.all_profiles = get_chrome_profiles()
        self.bot_profiles = [p for p in self.all_profiles if not p["is_default"]]
        self.page_size = 6
        self.total_pages = max(1, (len(self.bot_profiles) + self.page_size - 1) // self.page_size)
        self.current_page = 1
        self.is_running = False

        self.build_ui()
        self.render_page()

        self._app_updater = StartupUpdater(self, os.path.dirname(os.path.abspath(__file__)),
                                          self.shutdown_app, entry_point='gui_launcher.py')
        self.after(250, self._app_updater.start)

    def shutdown_app(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self.browser_profiles.cancel_pending()
        self.withdraw()
        done = threading.Event()

        def worker():
            try:
                for cleanup in (self.browser_profiles.shutdown, self.proxy_manager.close):
                    try:
                        cleanup()
                    except Exception:
                        logging.getLogger(__name__).exception('Application shutdown cleanup failed')
            finally:
                done.set()

        threading.Thread(target=worker, daemon=False).start()

        def finish():
            if done.is_set():
                self.destroy()
            else:
                self.after(100, finish)
        self.after(100, finish)

    def build_ui(self):
        # Header
        top_frame = tk.Frame(self, bg="#131d33", padx=16, pady=10)
        top_frame.pack(fill="x", padx=12, pady=(12, 6))

        title_lbl = tk.Label(top_frame, text="⛏️ Legion Multi-Bot: Менеджер страниц", font=("Segoe UI", 14, "bold"), bg="#131d33", fg="#60a5fa")
        title_lbl.pack(anchor="w")
        sub_lbl = tk.Label(top_frame, text=f"Всего ботов: {len(self.bot_profiles)} | Листание по 6 окон в сетке 3x2 на мониторе", font=("Segoe UI", 9), bg="#131d33", fg="#94a3b8")
        sub_lbl.pack(anchor="w")

        # Pager Bar
        pager_bar = tk.Frame(self, bg="#182442", padx=12, pady=8)
        pager_bar.pack(fill="x", padx=12, pady=4)

        self.btn_prev = tk.Button(pager_bar, text="◀ Предыдущая", command=self.prev_page, font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#ffffff", relief="flat", padx=10)
        self.btn_prev.pack(side="left")

        self.page_indicator = tk.Label(pager_bar, text="", font=("Segoe UI", 11, "bold"), bg="#182442", fg="#38bdf8")
        self.page_indicator.pack(side="left", expand=True)

        self.btn_next = tk.Button(pager_bar, text="Следующая ▶", command=self.next_page, font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#ffffff", relief="flat", padx=10)
        self.btn_next.pack(side="right")

        # Settings
        cfg_frame = tk.Frame(self, bg="#0f172a", padx=12, pady=6)
        cfg_frame.pack(fill="x", padx=12, pady=4)

        tk.Label(cfg_frame, text="URL:", bg="#0f172a", fg="#cbd5e1").pack(side="left")
        self.url_var = tk.StringVar(value="https://play.multidonate.ru/c/zakvielchannel/")
        tk.Entry(cfg_frame, textvariable=self.url_var, bg="#0d1424", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 9), width=35).pack(side="left", padx=6)

        tk.Label(cfg_frame, text="Задержка (с):", bg="#0f172a", fg="#cbd5e1").pack(side="left", padx=(10, 2))
        self.delay_var = tk.DoubleVar(value=1.2)
        tk.Spinbox(cfg_frame, from_=0.4, to=3.0, increment=0.2, textvariable=self.delay_var, width=4, bg="#0d1424", fg="#ffffff").pack(side="left")

        self.anti_throttle = tk.BooleanVar(value=True)
        tk.Checkbutton(cfg_frame, text="⚡ Анти-сон", variable=self.anti_throttle, bg="#0f172a", fg="#38bdf8", selectcolor="#0d1424").pack(side="left", padx=8)

        self.mute_var = tk.BooleanVar(value=True)
        tk.Checkbutton(cfg_frame, text="🔇 Без звука", variable=self.mute_var, bg="#0f172a", fg="#cbd5e1", selectcolor="#0d1424").pack(side="left", padx=4)

        # 3x2 Grid Slots Container
        self.slots_container = tk.Frame(self, bg="#0b0f19")
        self.slots_container.pack(fill="both", expand=True, padx=12, pady=6)

        # Bottom Action Bar
        bottom_frame = tk.Frame(self, bg="#131d33", padx=14, pady=10)
        bottom_frame.pack(fill="x", padx=12, pady=(4, 12))

        self.status_lbl = tk.Label(bottom_frame, text="Готов к переключению страниц", font=("Segoe UI", 9), bg="#131d33", fg="#94a3b8")
        self.status_lbl.pack(side="left")

        self.btn_launch_all = tk.Button(
            bottom_frame,
            text="🚀 Запустить ВСЕХ 21 ботов",
            command=self.launch_all_thread,
            font=("Segoe UI", 9, "bold"),
            bg="#7c3aed",
            fg="#ffffff",
            relief="flat",
            padx=12,
            pady=4
        )
        self.btn_launch_all.pack(side="right", padx=(8, 0))

        self.btn_launch_page = tk.Button(
            bottom_frame,
            text="🪟 Запустить эту страницу (6 окон 3x2)",
            command=self.launch_page_thread,
            font=("Segoe UI", 10, "bold"),
            bg="#2563eb",
            fg="#ffffff",
            relief="flat",
            padx=14,
            pady=4
        )
        self.btn_launch_page.pack(side="right")

        # Key bindings: Left/Right arrows
        self.bind("<Left>", lambda e: self.prev_page())
        self.bind("<Right>", lambda e: self.next_page())

    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.render_page()

    def next_page(self):
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.render_page()

    def render_page(self):
        start = (self.current_page - 1) * self.page_size + 1
        end = min(self.current_page * self.page_size, len(self.bot_profiles))
        self.page_indicator.config(text=f"Страница {self.current_page} из {self.total_pages}  (Боты {start}–{end})")
        self.btn_prev.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.config(state="normal" if self.current_page < self.total_pages else "disabled")
        self.btn_launch_page.config(text=f"🪟 Запустить Страницу #{self.current_page} (6 окон 3x2)")

        # Clear slots
        for w in self.slots_container.winfo_children():
            w.destroy()

        start_idx = (self.current_page - 1) * self.page_size
        page_items = self.bot_profiles[start_idx:start_idx + self.page_size]

        slot_names = [
            "СЛОТ #1 (Верх-Лево)", "СЛОТ #2 (Верх-Центр)", "СЛОТ #3 (Верх-Право)",
            "СЛОТ #4 (Низ-Лево)",  "СЛОТ #5 (Низ-Центр)",  "СЛОТ #6 (Низ-Право)"
        ]

        # 3 columns x 2 rows
        for i in range(6):
            r = i // 3
            c = i % 3

            card = tk.Frame(self.slots_container, bg="#16223d", relief="solid", borderwidth=1, padx=8, pady=6)
            card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            self.slots_container.grid_columnconfigure(c, weight=1)
            self.slots_container.grid_rowconfigure(r, weight=1)

            header_row = tk.Frame(card, bg="#16223d")
            header_row.pack(fill="x")
            tk.Label(header_row, text=slot_names[i], font=("Segoe UI", 8, "bold"), bg="#16223d", fg="#60a5fa").pack(side="left")

            if i < len(page_items):
                p = page_items[i]
                name_lbl = tk.Label(card, text=f"{p['gaia'] or p['name']}", font=("Segoe UI", 11, "bold"), bg="#16223d", fg="#ffffff")
                name_lbl.pack(anchor="w", pady=(4, 0))

                email_lbl = tk.Label(card, text=f"{p['email'] or p['key']}", font=("Segoe UI", 8), bg="#16223d", fg="#94a3b8")
                email_lbl.pack(anchor="w")

                bot_bar = tk.Frame(card, bg="#16223d")
                bot_bar.pack(fill="x", pady=(6, 0))

                tm_text = "🐒 TM готов" if p["has_tm"] else "⚠️ Нет TM"
                tm_fg = "#34d399" if p["has_tm"] else "#f59e0b"
                tk.Label(bot_bar, text=tm_text, font=("Segoe UI", 8, "bold"), bg="#16223d", fg=tm_fg).pack(side="left")

                key_closure = p["key"]
                slot_closure = i
                btn_one = tk.Button(bot_bar, text="Открыть ↗", command=lambda k=key_closure, s=slot_closure: self.launch_slot(k, s), font=("Segoe UI", 7, "bold"), bg="#1e293b", fg="#cbd5e1", relief="flat")
                btn_one.pack(side="right")
            else:
                tk.Label(card, text="Слот свободен", font=("Segoe UI", 9), bg="#16223d", fg="#475569").pack(pady=20)

    def _secure_launch(self, key, slot_idx, args):
        if self._shutting_down:
            raise RuntimeError('Application is shutting down')
        generation = self.browser_profiles.generation
        keys = [p['key'] for p in self.bot_profiles]
        index = keys.index(key) if key in keys else slot_idx
        proxy = self.proxy_manager.get_resilient_proxy_for_slot(self.browser_profiles.proxy_slot(key, index), profile_key=key)
        if not proxy or proxy == 'ERROR_ALL_DOWN':
            raise ConnectionError('VPN не подключен; запуск заблокирован')
        arguments = [a for a in args[1:] if not a.startswith('--profile-directory=')]
        return self.browser_profiles.launch(CHROME_EXE, key, proxy, arguments, generation=generation)

    def launch_slot(self, key, slot_idx):
        threading.Thread(target=self._launch_slot, args=(key, slot_idx), daemon=True).start()

    def _launch_slot(self, key, slot_idx):
        cols, rows = 3, 2
        col = slot_idx % cols
        row = slot_idx // cols
        win_w = 1920 // cols
        win_h = 1040 // rows
        x = col * win_w
        y = row * win_h

        args = [
            CHROME_EXE,
            f"--profile-directory={key}",
            "--new-window",
            f"--window-position={x},{y}",
            f"--window-size={win_w},{win_h}"
        ]
        if self.anti_throttle.get():
            args.extend([
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=CalculateNativeWinOcclusion"
            ])
        if self.mute_var.get():
            args.append("--mute-audio")
        args.append(self.url_var.get().strip())

        try:
            self._secure_launch(key, slot_idx, args)
            self.status_lbl.config(text=f"Слот #{slot_idx+1} ({key}) запущен!")
        except Exception as e:
            self.status_lbl.config(text=f"Ошибка: {e}")

    def launch_page_thread(self):
        if self.is_running:
            return
        self.is_running = True
        self.btn_launch_page.config(state="disabled", text="⏳ Запуск...")
        threading.Thread(target=self.run_launch_page, daemon=True).start()

    def run_launch_page(self):
        start_idx = (self.current_page - 1) * self.page_size
        items = self.bot_profiles[start_idx:start_idx + self.page_size]
        delay = max(0.2, self.delay_var.get())

        cols, rows = 3, 2
        win_w = 1920 // cols
        win_h = 1040 // rows

        launched = 0
        for i, p in enumerate(items):
            self.status_lbl.config(text=f"Запуск слота #{i+1} из {len(items)}: {p['gaia'] or p['name']}...")
            col = i % cols
            row = i // cols
            x = col * win_w
            y = row * win_h

            args = [
                CHROME_EXE,
                f"--profile-directory={p['key']}",
                "--new-window",
                f"--window-position={x},{y}",
                f"--window-size={win_w},{win_h}"
            ]
            if self.anti_throttle.get():
                args.extend([
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-features=CalculateNativeWinOcclusion"
                ])
            if self.mute_var.get():
                args.append("--mute-audio")
            args.append(self.url_var.get().strip())

            try:
                self._secure_launch(p['key'], start_idx + i, args)
                launched += 1
            except Exception as e:
                print(f"Launch error {p['key']}: {e}")

            if i < len(items) - 1:
                time.sleep(delay)

        self.status_lbl.config(text=f"Страница #{self.current_page}: проверенных запусков {launched}/{len(items)}")
        self.btn_launch_page.config(state="normal", text=f"🪟 Запустить Страницу #{self.current_page} (6 окон 3x2)")
        self.is_running = False

    def launch_all_thread(self):
        if self.is_running:
            return
        if not messagebox.askyesno("Подтверждение", f"Запустить ВСЕХ {len(self.bot_profiles)} ботов в фоне?"):
            return
        self.is_running = True
        self.btn_launch_all.config(state="disabled", text="⏳ Запуск фермы...")
        threading.Thread(target=self.run_launch_all, daemon=True).start()

    def run_launch_all(self):
        delay = max(0.2, self.delay_var.get())
        total = len(self.bot_profiles)

        launched = 0
        # Launch all profiles
        for i, p in enumerate(self.bot_profiles):
            slot = i % self.page_size
            col = slot % 3
            row = slot // 3
            x = col * (1920 // 3)
            y = row * (1040 // 2)

            self.status_lbl.config(text=f"Запуск {i+1} из {total}: {p['gaia'] or p['name']}...")
            args = [
                CHROME_EXE,
                f"--profile-directory={p['key']}",
                "--new-window",
                f"--window-position={x},{y}",
                f"--window-size={1920 // 3},{1040 // 2}"
            ]
            if self.anti_throttle.get():
                args.extend([
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-features=CalculateNativeWinOcclusion"
                ])
            if self.mute_var.get():
                args.append("--mute-audio")
            args.append(self.url_var.get().strip())

            try:
                self._secure_launch(p['key'], i, args)
                launched += 1
            except Exception as e:
                print(f"Error: {e}")

            if i < total - 1:
                time.sleep(delay)

        self.status_lbl.config(text=f"Проверенных запусков: {launched}/{total}")
        self.btn_launch_all.config(state="normal", text="🚀 Запустить ВСЕХ 21 ботов")
        self.is_running = False

if __name__ == "__main__":
    app = ProfilePagerApp()
    try:
        app.mainloop()
    finally:
        try:
            app.browser_profiles.shutdown()
        finally:
            app.proxy_manager.close()
