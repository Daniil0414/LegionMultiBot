from contextlib import nullcontext
import os
import sys
import json
import time
import socket
import subprocess
import threading
import re
import queue
import logging
from logging.handlers import RotatingFileHandler
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from vpn_dispatcher import VpnDispatcher, BASE_LOCAL_PORT, HAPP_IMPORT_FAILURE, subscription_url as normalize_subscription_url
from browser_profiles import BrowserProfiles, LaunchCancelled
from mine_control_ui import MineControlPanel
from account_loading_ui import AccountLoadingOverlay
from embedded_login_ui import EmbeddedLoginView
from browser_focus import DockedBrowserFocus
from proxy_manager import ProxyManager, ProxyEntry, parse_proxy_entry, ProxySelectionCancelled
from proxy_network import fetch_proxy_ip
from vpn_runtime import process_identity
from app_update_ui import StartupUpdater
from app_paths import APP_DIR, DATA_DIR, bundled_browser

CURRENT_DIR = str(APP_DIR)
LAUNCH_LOG = logging.getLogger('legion.launch')


def configure_launch_logging():
    if LAUNCH_LOG.handlers:
        return
    directory = os.path.join(DATA_DIR, '.vpn_runtime')
    os.makedirs(directory, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(directory, 'browser_launch.log'),
                                  maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LAUNCH_LOG.addHandler(handler)
    LAUNCH_LOG.setLevel(logging.INFO)
from userscripts import DEFAULT_SCRIPT_URL


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ERROR_ALREADY_EXISTS = 183
APP_MUTEX_NAME = "Local\\LegionMultiBoxerApp_SingleInstance_Mutex"
APP_TITLE = "📱 Legion Multi-Bot"
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND

# Win32 Window Styles & Flags
SW_HIDE = 0
SW_NORMAL = 1
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_ASYNCWINDOWPOS = 0x4000

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000
WS_CLIPCHILDREN = 0x02000000
WS_CLIPSIBLINGS = 0x04000000

WS_EX_CLIENTEDGE = 0x00000200
WS_EX_WINDOWEDGE = 0x00000100
WS_EX_STATICEDGE = 0x00020000

WM_CLOSE = 0x0010

WM_SETREDRAW = 0x000B
RDW_INVALIDATE = 0x0001
RDW_ALLCHILDREN = 0x0080
RDW_UPDATENOW = 0x0100
GA_ROOT = 2

VK_ESCAPE = 0x1B
VK_LBUTTON = 0x01
WM_COMMAND = 0x0111
WM_SYSCOMMAND = 0x0112
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
GWL_WNDPROC = -4
SC_MOVE = 0xF010
HTCAPTION = 0x0002
# chrome/app/chrome_command_ids.h: 33002 reloads; 33000 navigates Back.
IDC_RELOAD = 33002

# Measurements
CHROME_TITLEBAR_OFFSET = 34  # Temporary fallback until Chrome creates its content window
SLOT_HEADER_HEIGHT = 22      # Ultra-compact 22px native header strip inside each slot
MAX_DOCK_SETTLE_ATTEMPTS = 8


def chrome_content_insets(hwnd):
    """Measure browser UI around the native page without querying page JavaScript."""
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
        return None
    candidates = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(child, _):
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(child, name, len(name))
        # Check the child's own visibility: the parent can be hidden between pages.
        if name.value == 'Chrome_RenderWidgetHostHWND' and user32.GetWindowLongW(child, GWL_STYLE) & 0x10000000:
            content = wintypes.RECT()
            if user32.GetWindowRect(child, ctypes.byref(content)):
                width, height = content.right - content.left, content.bottom - content.top
                insets = (content.left - bounds.left, content.top - bounds.top,
                          bounds.right - content.right, bounds.bottom - content.bottom)
                if width > 1 and height > 1 and min(insets) >= 0:
                    candidates.append((width * height, insets))
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    return max(candidates)[1] if candidates else None

# 1. Enable System DPI Awareness for crisp fonts without DWM cross-monitor dragging elastic lag
def init_windows_dpi_awareness():
    try:
        # System DPI Aware (1) avoids Per-Monitor V2 DWM virtualization drag lag across multi-monitors
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

# 2. Locate Chrome executable
def find_chrome():
    portable = bundled_browser()
    if portable:
        return portable
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

# 3. Detect all connected monitors via Win32 API
class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('rcMonitor', wintypes.RECT),
        ('rcWork', wintypes.RECT),
        ('dwFlags', wintypes.DWORD)
    ]

def get_system_monitors():
    monitors = []
    def cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi))
        r = mi.rcMonitor
        monitors.append({
            "x": r.left,
            "y": r.top,
            "width": r.right - r.left,
            "height": r.bottom - r.top,
            "primary": bool(mi.dwFlags == 1)
        })
        return 1

    cb_func = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.c_void_p, wintypes.LPARAM)(cb)
    user32.EnumDisplayMonitors(0, 0, cb_func, 0)
    monitors.sort(key=lambda m: (not m["primary"], m["x"]))
    return monitors if monitors else [{"x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True}]

# 4. Accounts Management (Clean standalone Bot Profiles)
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        accounts = []
        for item in data:
            key = item.get("id") or item.get("key")
            if not key:
                continue
            name = item.get("name") or key
            accounts.append({
                "key": key,
                "name": name,
                "gaia": "",
                "email": "",
                "has_tm": True,
                "is_default": False,
                "created_at": item.get("created_at", "")
            })
        return accounts
    except Exception as e:
        print(f"Error loading accounts: {e}")
        return []

def save_accounts(accounts):
    try:
        data = []
        for acc in accounts:
            data.append({
                "id": acc["key"],
                "name": acc["name"],
                "created_at": acc.get("created_at", "")
            })
        os.makedirs(os.path.dirname(ACCOUNTS_FILE), exist_ok=True)
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving accounts: {e}")

def get_chrome_profiles():
    return load_accounts()

MAX_SLOTS = 16
SLOT_TITLES = [f"📱 #{i+1}" for i in range(MAX_SLOTS)]

GRID_CONFIGS = {
    "6x2": {
        "label": "📱 Сетка 6x2 (12 шт)",
        "cols": 6,
        "rows": 2,
        "slots": 12,
        "default_zoom": "80%"
    },
    "8x2": {
        "label": "📱 Сетка 8x2 (16 шт)",
        "cols": 8,
        "rows": 2,
        "slots": 16,
        "default_zoom": "65%"
    },
    "4x2": {
        "label": "📱 Сетка 4x2 (8 шт)",
        "cols": 4,
        "rows": 2,
        "slots": 8,
        "default_zoom": "85%"
    },
    "6x1": {
        "label": "📱 6 в ряд (6 шт)",
        "cols": 6,
        "rows": 1,
        "slots": 6,
        "default_zoom": "85%"
    }
}





def get_happ_active_server():
    """Reads the active Happ VPN server name directly from Windows Registry."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Happ\OrganizationDefaults\Preferences") as key:
            try:
                name, _ = winreg.QueryValueEx(key, "lastServerName")
                if name:
                    return str(name).strip()
            except OSError:
                pass
    except Exception:
        pass
    return None

def format_proxy_badge_info(proxy_str, is_narrow=False, vpn_dispatcher=None):
    """Formats a clean, human-readable VPN/Proxy badge for slot headers.
    Returns: (text, bg_color, fg_color, tooltip)
    """
    if not proxy_str or proxy_str == "ERROR_ALL_DOWN":
        return "🛑 Оффлайн", "#450a0a", "#f87171", "VPN / Прокси отключен"

    entry = parse_proxy_entry(proxy_str) if isinstance(proxy_str, str) else proxy_str
    if not entry:
        return "🛡️ VPN", "#064e3b", "#34d399", "VPN Shield"

    # Dedicated ports retain their original node assignment after health checks.
    node_count = len(vpn_dispatcher.get_nodes_snapshot()) if vpn_dispatcher else 0
    if entry.host in ("127.0.0.1", "localhost", "::1") and (BASE_LOCAL_PORT <= entry.port < BASE_LOCAL_PORT + max(48, node_count)):
        slot_idx = entry.port - BASE_LOCAL_PORT
        nodes = vpn_dispatcher.get_nodes_snapshot() if vpn_dispatcher else []
        if nodes:
            node = nodes[slot_idx % len(nodes)]
            raw_tag = node.tag
            # Clean up common affixes to keep badge compact & readable
            clean = raw_tag.replace(" - АВТО", "").replace(" Node", "").strip()
            max_len = 11 if is_narrow else 15
            if len(clean) > max_len:
                clean = clean[:max_len - 1] + "…"
            badge_text = f"🛡️ {clean}"
            tooltip = f"Слот #{slot_idx+1} [Порт 127.0.0.1:{entry.port}]\nСервер: {node.tag}\nУзел: {node.address}:{node.port} ({node.protocol.upper()})"
            return badge_text, "#064e3b", "#34d399", tooltip
        else:
            return f"🛡️ Порт {entry.port}", "#064e3b", "#34d399", f"Локальный порт 127.0.0.1:{entry.port}"

    # 2. Check if this is Happ (port 10808)
    is_happ = (entry.host in ("127.0.0.1", "localhost", "::1")) and (entry.port in (10808, 10809))
    if is_happ:
        happ_server = get_happ_active_server()
        if happ_server:
            clean = happ_server
            if clean.startswith("Автовыбор"):
                clean = "Автовыбор ⚡"
            elif len(clean) > 13:
                clean = clean[:11] + "…"
            
            badge_text = f"🛡️ {clean}" if is_narrow else f"🛡️ Happ: {clean}"
            tooltip = f"Happ VPN (SOCKS5 127.0.0.1:10808)\nСервер: {happ_server}"
            return badge_text, "#064e3b", "#34d399", tooltip
        else:
            badge_text = "🛡️ Happ:10808" if not is_narrow else "🛡️ Happ"
            return badge_text, "#064e3b", "#34d399", "Happ VPN (SOCKS5 127.0.0.1:10808)"
    else:
        # Custom external proxy from proxies.txt
        h = entry.host
        if len(h) > 11:
            h = h[:9] + "…"
        badge_text = f"🛡️ {h}:{entry.port}"
        tooltip = f"Прокси: {entry.clean}"
        return badge_text, "#0f2b3e", "#38bdf8", tooltip


class HighQualityMultiBoxerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.withdraw()
        self.use_vpn = self.choose_network_mode()
        if self.use_vpn is None:
            self.destroy()
            raise SystemExit(0)
        
        # Start in maximized window mode (оконный режим на полный экран)
        self.geometry("1680x960+50+50")
        self.minsize(880, 540)
        self.configure(bg="#000000")
        try:
            self.state('zoomed')
        except Exception:
            pass

        # System monitors
        self.monitors = get_system_monitors()
        self.current_monitor_idx = 0

        # Accounts (Clean standalone Bot Profiles)
        self.bot_profiles = load_accounts()
        
        # Grid Configuration (Default: 6x2 = 12 smartphones, 2 pages for 21 bots)
        self.current_grid_key = "6x2"
        self.page_size = GRID_CONFIGS[self.current_grid_key]["slots"] # 12
        self.total_pages = max(1, (len(self.bot_profiles) + self.page_size - 1) // self.page_size)
        self.current_page = 1

        # Network & Proxy Manager (Happ + Multi-Port + Fallback)
        self.proxy_manager = ProxyManager(str(DATA_DIR), start_monitor=self.use_vpn)
        self.kill_switch_var = tk.BooleanVar(value=self.use_vpn)
        self.browser_profiles = BrowserProfiles(allow_direct=not self.use_vpn)
        self._closing_browsers = False
        self._shutting_down = False
        self._mine_overlay = None
        self._mine_auto_shown = False
        self._accounts_dialog = None
        self._login_task = None
        self._login_view = None
        self.protocol('WM_DELETE_WINDOW', self.shutdown_app)
        self.browser_sessions = {}
        self._session_check_busy = False
        self._happ_import_busy = False
        self._network_settings_busy = False
        self._pending_happ_json = None
        self._pending_subscription_url = None
        self._happ_import_buttons = []
        self.happ_status_var = tk.StringVar(
            value="Вставьте ссылку подписки и нажмите «Получить JSON по ссылке». Happ не требуется."
        )

        # Profile HWND mapping: { profile_key: hwnd }
        self.profile_hwnds = {}
        self._docked_hwnds = set()
        self._chrome_insets = {}
        self._vpn_failed_keys = set()
        self._launch_errors = {}
        self.is_launching = False
        self.slot_urls = {} # Tracks slot mode: slot_idx -> "game" (default) or "2ip"

        # Full screen slot state (None = grid, 0..15 = specific slot expanded)
        self.fullscreen_slot = None

        # Auto flip state
        self.auto_flip_sec = 0
        self.auto_flip_remaining = 0

        # Resize debounce & window tracking
        self._resize_timer = None
        self._last_win_w = 0
        self._last_win_h = 0
        self._last_known_happ_server = get_happ_active_server() if self.use_vpn else None

        # Elevate Python process priority so UI and mouse dragging are never starved by background bots
        try:
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00008000) # ABOVE_NORMAL_PRIORITY_CLASS
        except Exception:
            pass

        self.setup_ui()
        self.bind_events()
        self._browser_focus = DockedBrowserFocus(self, self._browser_hwnds_for_focus)
        self.update_page_nav()
        self.deiconify()

        # Apply WS_CLIPCHILDREN to eliminate background repaint lag
        self.apply_clip_children_styles()
        self.after(100, self.apply_clip_children_styles)

        # Initial layout computation after event loop starts
        self.after(50, self.reposition_all_active_windows)

        self._app_updater = StartupUpdater(self, CURRENT_DIR, self.shutdown_app)
        self.after(250, self._app_updater.start)

        # Periodic check for active Happ server updates
        if self.use_vpn:
            self.after(3000, self.periodic_vpn_check)

        # First launch check: if user has no VPN servers configured yet, auto-open setup dialog
        if not self.use_vpn:
            return
        if not self.proxy_manager.vpn_dispatcher.nodes:
            self.after(600, self.open_proxy_settings)
        else:
            # Auto-filter alive nodes in background immediately on startup
            def _bg_startup_filter():
                try:
                    self.proxy_manager.vpn_dispatcher.ensure_alive_pool(timeout=2.0)
                    self.after(0, self.update_all_slot_proxy_badges)
                except Exception:
                    pass
            threading.Thread(target=_bg_startup_filter, daemon=True).start()

    def choose_network_mode(self):
        """Choose once per application run, before any VPN work starts."""
        dialog = tk.Toplevel(self)
        dialog.title("Legion Multi-Bot — подключение")
        dialog.configure(bg="#0d1424")
        dialog.resizable(False, False)
        width, height = 510, 260
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        choice = None

        def select(use_vpn):
            nonlocal choice
            choice = use_vpn
            dialog.destroy()

        tk.Label(dialog, text="Выберите режим работы", font=("Segoe UI", 16, "bold"),
                 bg="#0d1424", fg="#ffffff").pack(pady=(24, 12))
        tk.Label(dialog, text="С VPN — запуск через настроенные прокси.\n"
                 "Без VPN — прямое подключение без прокси программы.",
                 font=("Segoe UI", 10), bg="#0d1424", fg="#cbd5e1").pack()
        buttons = tk.Frame(dialog, bg="#0d1424")
        buttons.pack(pady=18)
        for label, mode, color in (("С VPN", True, "#0369a1"), ("Без VPN", False, "#047857")):
            tk.Button(buttons, text=label, command=lambda value=mode: select(value),
                      font=("Segoe UI", 12, "bold"), width=16, bg=color, fg="#ffffff",
                      relief="flat", pady=7).pack(side="left", padx=8)
        tk.Label(dialog, text="Системный VPN при необходимости отключите отдельно.",
                 font=("Segoe UI", 9), bg="#0d1424", fg="#94a3b8").pack()
        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.wait_visibility()
        dialog.grab_set()
        self.wait_window(dialog)
        return choice

    def get_launch_proxy(self, key, slot_idx, status_callback=None):
        if not self.use_vpn:
            return 'DIRECT'
        generation = self.browser_profiles.generation
        def status(message):
            if status_callback:
                status_callback(message)
            self.after(0, lambda: self.status_lbl.config(text=message))
        try:
            return self.proxy_manager.get_resilient_proxy_for_slot(
                self.browser_profiles.proxy_slot(key, slot_idx), profile_key=key,
                status_callback=status,
                cancel_callback=lambda: self._closing_browsers or generation != self.browser_profiles.generation)
        except ProxySelectionCancelled:
            raise LaunchCancelled()

    def update_active_bot_status_bar(self):
        if not self.is_launching:
            self.status_lbl.config(text="" if self.use_vpn else "Без VPN — прямое подключение")

    def _animate_status_spinner(self):
        self._status_spinner_timer = None
        if self._shutting_down:
            return
        probe_lock = getattr(self.proxy_manager.vpn_dispatcher, '_filter_lock', None)
        # This indicator belongs to the server-pool check, not general activity.
        busy = self.use_vpn and probe_lock is not None and probe_lock.locked()
        try:
            if busy:
                self._status_spinner_angle = (self._status_spinner_angle + 30) % 360
                self.status_spinner.itemconfigure(self._status_spinner_arc,
                                                  state='normal', start=self._status_spinner_angle)
            else:
                self.status_spinner.itemconfigure(self._status_spinner_arc, state='hidden')
            self._status_spinner_timer = self.after(80 if busy else 250, self._animate_status_spinner)
        except tk.TclError:
            return  # The window may have been destroyed during shutdown.

    def update_slot_proxy_badge(self, slot_idx, proxy_str=None):
        if slot_idx >= len(self.slot_cards):
            return
        badge = self.slot_cards[slot_idx].get("proxy_badge")
        if not badge:
            return
        global_idx = (self.current_page - 1) * self.page_size + slot_idx
        profile = self.bot_profiles[global_idx] if global_idx < len(self.bot_profiles) else None
        if not profile:
            badge.config(text="", bg="#131d33")
            badge.unbind('<Enter>')
            badge.unbind('<Leave>')
            return
        session = self.browser_sessions.get(profile['key']) if profile else None
        hwnd = self.profile_hwnds.get(profile['key']) if profile else None
        owner = wintypes.DWORD()
        if hwnd and user32.IsWindow(hwnd):
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        verified = bool(session and session['pid'] == owner.value and session.get('verified_ip'))
        if not self.use_vpn:
            values = ('Без VPN', '#064e3b', '#6ee7b7', 'Прямое подключение; прокси программы отключены')
        elif proxy_str == 'ERROR_ALL_DOWN' or (session and session.get('network_failed')) or (profile and profile.get('key') in self._vpn_failed_keys):
            values = ('🛑 Нет VPN', '#450a0a', '#f87171', 'Проверка прокси не пройдена; запуск заблокирован')
        elif not verified:
            is_compact = self.current_grid_key in ('6x2', '8x2')
            values = ('⚪ VPN' if is_compact else 'Не проверен', '#1e293b', '#94a3b8', 'Нет подтверждённого окна с назначенным прокси')
        else:
            is_compact = self.current_grid_key in ('6x2', '8x2')
            route_status = self.proxy_manager.failover_status(session['proxy'])
            active_proxy = route_status.get('proxy') if route_status else session['proxy']
            values = format_proxy_badge_info(
                active_proxy, is_narrow=is_compact,
                vpn_dispatcher=self.proxy_manager.vpn_dispatcher)
            values = (*values[:3], values[3] + f"\nHTTPS через прокси: {session['verified_ip']}\nПроцесс окна: {session['pid']}")
            if route_status:
                values = (*values[:3], values[3] + ('\nРезерв VPN готов' if route_status['standby_ready'] else '\nПоиск отдельного резерва VPN'))
        text_value, background, foreground, tooltip = values
        badge.config(text=text_value, bg=background, fg=foreground)
        badge.bind('<Enter>', lambda event, msg=tooltip: self.status_lbl.config(text=' | '.join(msg.splitlines())))
        badge.bind('<Leave>', lambda event: self.update_active_bot_status_bar())

    def update_all_slot_proxy_badges(self):
        for i in range(len(self.slot_cards)):
            self.update_slot_proxy_badge(i)

    def periodic_vpn_check(self, schedule_next=True):
        """Periodically checks if Happ server or multi-VPN state changed and keeps UI badges in sync."""
        if not self.use_vpn:
            return
        try:
            vd = self.proxy_manager.vpn_dispatcher
            nodes = vd.get_nodes_snapshot()
            if nodes and (vd.is_running or vd.xray_bin):
                self.update_all_slot_proxy_badges()
            else:
                curr_server = get_happ_active_server()
                if curr_server != self._last_known_happ_server:
                    self._last_known_happ_server = curr_server
                    self.update_all_slot_proxy_badges()
        except Exception:
            pass

        self._check_browser_routes_async()
        self._check_active_slots_health()
        if schedule_next:
            if hasattr(self, "_vpn_periodic_timer") and self._vpn_periodic_timer:
                try:
                    self.after_cancel(self._vpn_periodic_timer)
                except Exception:
                    pass
            self._vpn_periodic_timer = self.after(3500, self.periodic_vpn_check)

    def _check_browser_routes_async(self):
        if not self.use_vpn or self._session_check_busy:
            return
        now = time.monotonic()
        stale = [(key, session) for key, session in self.browser_sessions.items()
                 if now - session.get('verified_at', 0) >=
                 (6 if self.proxy_manager.failover_status(session['proxy']) is not None else 30)]
        if not stale:
            return
        self._session_check_busy = True

        def worker():
            def probe(item):
                key, session = item
                try:
                    route = self.proxy_manager.failover_status(session['proxy'])
                    if route is not None:
                        return key, session, route['ip']
                    return key, session, fetch_proxy_ip(session['proxy'])
                except Exception:
                    return key, session, None
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(probe, stale))

            def finish():
                self._session_check_busy = False
                for key, session, ip in results:
                    if self.browser_sessions.get(key) is session:
                        session['verified_at'] = time.monotonic()
                        session['network_failed'] = not bool(ip)
                        if ip:
                            session['verified_ip'] = ip
                        else:
                            session.pop('verified_ip', None)
                self.update_all_slot_proxy_badges()
            try:
                self.after(0, finish)
            except RuntimeError:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _check_active_slots_health(self):
        """Monitors active docked browser windows and automatically recovers crashed sessions."""
        if self._closing_browsers or self.is_launching:
            return

        start_idx = (self.current_page - 1) * self.page_size
        items = self.bot_profiles[start_idx:start_idx + self.page_size]
        curr_keys = {p["key"]: (slot_i, p) for slot_i, p in enumerate(items)}

        now = time.monotonic()
        if not hasattr(self, '_recovery_timestamps'):
            self._recovery_timestamps = {}
        if not hasattr(self, '_recovering_keys'):
            self._recovering_keys = set()

        for key, session in list(self.browser_sessions.items()):
            if key in self._recovering_keys:
                continue
            hwnd = self.profile_hwnds.get(key)
            is_window_alive = bool(hwnd and user32.IsWindow(hwnd))
            pid = session.get('pid') or (session.get('identity', {}).get('pid') if isinstance(session.get('identity'), dict) else None)
            is_proc_alive = bool(pid and process_identity(pid) == session.get('identity'))

            if not is_window_alive or not is_proc_alive:
                self.profile_hwnds.pop(key, None)
                self.browser_sessions.pop(key, None)
                if hwnd:
                    self._docked_hwnds.discard(hwnd)

                if key in curr_keys:
                    slot_idx, p = curr_keys[key]
                    history = [t for t in self._recovery_timestamps.get(key, []) if now - t < 300]
                    if len(history) < 3:
                        history.append(now)
                        self._recovery_timestamps[key] = history
                        self._recovering_keys.add(key)
                        p_name = p['gaia'] or p['name']
                        self.status_lbl.config(text=f"Восстановление окна Chrome: {p_name}...")
                        if slot_idx < len(self.slot_cards):
                            self.slot_cards[slot_idx]["bot_lbl"].config(
                                text=f"• {p_name} [Восстановление...]", fg="#fbbf24"
                            )
                        cur_mode = self.slot_urls.get(key, "game")
                        target_url = "https://2ip.io" if cur_mode == "2ip" else "https://play.multidonate.ru/c/zakvielchannel/"
                        global_idx = start_idx + slot_idx

                        def _recover_worker(k=key, s_idx=slot_idx, g_idx=global_idx, t_url=target_url):
                            try:
                                self.run_launch_single(
                                    k, s_idx, proxy_slot_idx=g_idx, target_url=t_url,
                                    generation=self.browser_profiles.generation
                                )
                            finally:
                                self._recovering_keys.discard(k)

                        threading.Thread(target=_recover_worker, daemon=True).start()
                    else:
                        self._mark_slot_browser_failed(
                            slot_idx, key, "Окно браузера аварийно завершилось. Нажмите ↻ для повторного запуска."
                        )

    def apply_clip_children_styles(self):
        """Enforces WS_CLIPCHILDREN and WS_CLIPSIBLINGS on all parent Tkinter windows
        so Windows GDI NEVER erases background under Direct3D/Chrome windows during drag."""
        try:
            self.update_idletasks()
            root_child = self.winfo_id()
            root_top = user32.GetAncestor(root_child, GA_ROOT)

            hwnds = [root_top, root_child]
            if hasattr(self, 'slots_frame'):
                hwnds.append(self.slots_frame.winfo_id())

            if hasattr(self, 'slot_cards'):
                for card in self.slot_cards:
                    if "box" in card:
                        hwnds.append(card["box"].winfo_id())
                    if "game_area" in card:
                        hwnds.append(card["game_area"].winfo_id())

            for h in hwnds:
                if h and user32.IsWindow(h):
                    cur_st = user32.GetWindowLongW(h, GWL_STYLE)
                    if not (cur_st & WS_CLIPCHILDREN):
                        user32.SetWindowLongW(h, GWL_STYLE, cur_st | WS_CLIPCHILDREN | WS_CLIPSIBLINGS)
        except Exception as e:
            print(f"Error applying WS_CLIPCHILDREN: {e}")

    def setup_ui(self):
        # 1. Single-Row Ultra-Compact Header Bar (Everything in ONE clean row!)
        self.header_bar = tk.Frame(self, bg="#0d1424", padx=4, pady=2)
        self.header_bar.pack(fill="x", side="top")

        # --- LEFT CONTROLS ---
        # Prev Page
        self.btn_prev = tk.Button(
            self.header_bar, text="◀", command=self.prev_page,
            font=("Segoe UI", 8, "bold"), bg="#1e293b", fg="#ffffff",
            activebackground="#334155", relief="flat", padx=4, pady=1
        )
        self.btn_prev.pack(side="left")

        # Page Pills
        self.pills_frame = tk.Frame(self.header_bar, bg="#0d1424")
        self.pills_frame.pack(side="left", padx=2)

        # Next Page
        self.btn_next = tk.Button(
            self.header_bar, text="▶", command=self.next_page,
            font=("Segoe UI", 8, "bold"), bg="#1e293b", fg="#ffffff",
            activebackground="#334155", relief="flat", padx=4, pady=1
        )
        self.btn_next.pack(side="left")

        # Exit Fullscreen Button in top header (appears when a slot is full screen)
        self.btn_exit_fs = tk.Button(
            self.header_bar, text="🗗 Вернуться к сетке (Esc)", command=self.exit_fullscreen_slot,
            font=("Segoe UI", 8, "bold"), bg="#2563eb", fg="#ffffff",
            activebackground="#1d4ed8", relief="flat", padx=6, pady=1
        )

        # Separator
        tk.Label(self.header_bar, text="|", bg="#0d1424", fg="#334155").pack(side="left", padx=2)

        # Dynamic Grid Switcher
        self.btn_grid = tk.Button(
            self.header_bar, text=GRID_CONFIGS[self.current_grid_key]["label"], command=self.cycle_grid_mode,
            font=("Segoe UI", 8, "bold"), bg="#0f2b3e", fg="#38bdf8",
            activebackground="#0369a1", activeforeground="#ffffff", relief="flat", padx=5, pady=1, cursor="hand2"
        )
        self.btn_grid.pack(side="left", padx=2)

        # VPN Settings Button
        self.btn_proxy_settings = tk.Button(
            self.header_bar, text="⚙ Настройки VPN", command=self.open_proxy_settings,
            font=("Segoe UI", 8), bg="#1e293b", fg="#cbd5e1",
            activebackground="#334155", activeforeground="#ffffff", relief="flat", padx=5, pady=1, cursor="hand2"
        )
        self.btn_proxy_settings.pack(side="left", padx=2)
        if not self.use_vpn:
            self.btn_proxy_settings.config(state="disabled")

        # Accounts Management Button
        self.btn_accounts = tk.Button(
            self.header_bar, text=f"👥 Аккаунты ({len(self.bot_profiles)})", command=self.open_accounts_dialog,
            font=("Segoe UI", 8, "bold"), bg="#065f46", fg="#34d399",
            activebackground="#047857", activeforeground="#ffffff", relief="flat", padx=6, pady=1, cursor="hand2"
        )
        self.btn_accounts.pack(side="left", padx=2)

        # A single GitHub userscript is shared by every managed profile.
        self.btn_userscript = tk.Button(
            self.header_bar, text="🤖 Скрипт бота", command=self.open_userscript_dialog,
            font=("Segoe UI", 8, "bold"), bg="#581c87", fg="#d8b4fe",
            activebackground="#6b21a8", activeforeground="#ffffff", relief="flat", padx=6, pady=1, cursor="hand2"
        )
        self.btn_userscript.pack(side="left", padx=2)

        self.btn_swarm = tk.Button(
            self.header_bar, text="⛏ Рой", command=self.open_mine_control,
            font=("Segoe UI", 8, "bold"), bg="#14532d", fg="#bbf7d0",
            activebackground="#166534", activeforeground="#ffffff", relief="flat", padx=6, pady=1, cursor="hand2"
        )
        self.btn_swarm.pack(side="left", padx=2)

        # Separator
        tk.Label(self.header_bar, text="|", bg="#0d1424", fg="#334155").pack(side="left", padx=2)

        # Auto Flip
        tk.Label(self.header_bar, text="Листать:", bg="#0d1424", fg="#94a3b8", font=("Segoe UI", 8)).pack(side="left", padx=1)
        self.auto_flip_var = tk.IntVar(value=0)
        self.flip_combo_var = tk.StringVar(value="Выкл")
        flip_combo = ttk.Combobox(
            self.header_bar, textvariable=self.flip_combo_var,
            values=["Выкл", "15с", "30с", "60с"], width=4, state="readonly", font=("Segoe UI", 8)
        )
        flip_combo.pack(side="left", padx=1)
        def _on_flip_change(e=None):
            m = {"Выкл": 0, "15с": 15, "30с": 30, "60с": 60}
            self.auto_flip_var.set(m.get(self.flip_combo_var.get(), 0))
            self.on_auto_flip_change()
        flip_combo.bind("<<ComboboxSelected>>", _on_flip_change)

        self.flip_countdown_lbl = tk.Label(self.header_bar, text="", font=("JetBrains Mono", 8, "bold"), bg="#0d1424", fg="#fbbf24")
        self.flip_countdown_lbl.pack(side="left", padx=1)

        # Browser zoom state
        self.zoom_var = tk.StringVar(value="100%")

        # Checkboxes
        self.anti_throttle_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.header_bar, text="⚡ Не спать", variable=self.anti_throttle_var, bg="#0d1424", fg="#38bdf8", selectcolor="#1e293b", activebackground="#0d1424", font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)

        self.mute_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.header_bar, text="🔇 Без звука", variable=self.mute_var, bg="#0d1424", fg="#cbd5e1", selectcolor="#1e293b", activebackground="#0d1424", font=("Segoe UI", 8)).pack(side="left", padx=2)

        # --- RIGHT ACTION BUTTONS ---
        self.btn_close_all = tk.Button(
            self.header_bar, text="🛑 Закрыть ВСЕХ", command=self.close_all_bots,
            bg="#1e293b", fg="#f87171", font=("Segoe UI", 8),
            activebackground="#ef4444", activeforeground="#ffffff", relief="flat", padx=5, pady=1
        )
        self.btn_close_all.pack(side="right", padx=(2, 0))

        self.btn_launch_all = tk.Button(
            self.header_bar, text=f"🚀 Запустить всех ботов ({len(self.bot_profiles)})", command=self.launch_all_thread,
            bg="#7c3aed", fg="#ffffff", font=("Segoe UI", 8, "bold"),
            activebackground="#6d28d9", relief="flat", padx=6, pady=1
        )
        self.btn_launch_all.pack(side="right", padx=2)

        self.btn_launch_page = tk.Button(
            self.header_bar, text=f"🪟 Запустить страницу ботов #{self.current_page}", command=self.launch_current_page_thread,
            bg="#2563eb", fg="#ffffff", font=("Segoe UI", 8, "bold"),
            activebackground="#1d4ed8", relief="flat", padx=6, pady=1
        )
        self.btn_launch_page.pack(side="right", padx=2)

        # Status / Tooltip display in middle space
        # Content must never change the header/grid geometry: hover can otherwise
        # move its own badge away from the cursor and oscillate Enter/Leave.
        self.status_spinner = tk.Canvas(self.header_bar, width=16, height=16,
                                         bg="#0d1424", highlightthickness=0, borderwidth=0)
        self.status_spinner.pack(side="left", padx=(4, 0))
        self._status_spinner_arc = self.status_spinner.create_arc(
            2, 2, 14, 14, start=0, extent=270, style=tk.ARC,
            outline="#38bdf8", width=2, state='hidden')
        self._status_spinner_angle = 0
        self._status_spinner_timer = self.after(0, self._animate_status_spinner)
        self.status_lbl = tk.Label(self.header_bar, text="", font=("Segoe UI", 8), bg="#0d1424", fg="#93c5fd",
                                   anchor="w", width=1, height=1, wraplength=0)
        self.status_lbl.pack(side="left", fill="x", expand=True, padx=4)


        # 2. Seamless Master Container (ZERO PADDING, ZERO GAP)
        self.slots_frame = tk.Frame(self, bg="#000000", borderwidth=0, highlightthickness=0)
        self.slots_frame.pack(fill="both", expand=True, side="bottom")

        # Up to MAX_SLOTS (16) Dedicated Slots with NATIVE Integrated Headers
        self.slot_cards = []
        for i in range(MAX_SLOTS):
            slot_box = tk.Frame(self.slots_frame, bg="#090d16", borderwidth=0, highlightthickness=0)

            # Native 22px Slim Header Strip at top of slot
            hdr = tk.Frame(slot_box, bg="#131d33", height=SLOT_HEADER_HEIGHT, padx=4)
            hdr.pack(fill="x", side="top")
            hdr.pack_propagate(False)

            # Right-side buttons packed FIRST so they are NEVER cut off in narrow slots!
            btn_fs = tk.Button(
                hdr, text="⛶", command=lambda idx=i: self.toggle_fullscreen_slot(idx),
                font=("Segoe UI Symbol", 8, "bold"), bg="#1e293b", fg="#ffffff",
                activebackground="#2563eb", activeforeground="#ffffff",
                relief="flat", padx=4, pady=0, cursor="hand2"
            )
            btn_fs.pack(side="right", padx=(1, 0))
            btn_fs.bind("<Enter>", lambda e, b=btn_fs: b.config(bg="#334155") if b['bg'] != '#2563eb' else None)
            btn_fs.bind("<Leave>", lambda e, b=btn_fs: b.config(bg="#1e293b") if b['bg'] != '#2563eb' else None)

            btn_reload = tk.Button(
                hdr, text="↻", command=lambda idx=i: self.reload_slot(idx),
                font=("Segoe UI Symbol", 9, "bold"), bg="#1e293b", fg="#38bdf8",
                activebackground="#0f172a", activeforeground="#60a5fa",
                relief="flat", padx=3, pady=0, cursor="hand2"
            )
            btn_reload.pack(side="right", padx=(1, 0))
            btn_reload.bind("<Enter>", lambda e, b=btn_reload: b.config(bg="#334155", fg="#67e8f9") if b['state'] == 'normal' else None)
            btn_reload.bind("<Leave>", lambda e, b=btn_reload: b.config(bg="#1e293b", fg="#38bdf8") if b['state'] == 'normal' else None)

            btn_ip = tk.Button(
                hdr, text="🌐", command=lambda idx=i: self.toggle_slot_url(idx),
                font=("Segoe UI Symbol", 8), bg="#1e293b", fg="#a78bfa",
                activebackground="#0f172a", activeforeground="#c4b5fd",
                relief="flat", padx=3, pady=0, cursor="hand2"
            )
            btn_ip.pack(side="right", padx=(1, 0))
            btn_ip.bind("<Enter>", lambda e, b=btn_ip: b.config(bg="#334155") if b['state'] == 'normal' else None)
            btn_ip.bind("<Leave>", lambda e, b=btn_ip: b.config(bg="#1e293b") if b['state'] == 'normal' else None)

            # Slot title
            title_lbl = tk.Label(hdr, text=SLOT_TITLES[i], font=("Segoe UI", 8, "bold"), bg="#131d33", fg="#60a5fa")
            title_lbl.pack(side="left")

            # Server & VPN badge right next to the title
            proxy_badge = tk.Label(
                hdr, text="🛡️ VPN", font=("Segoe UI", 7, "bold"),
                bg="#064e3b", fg="#34d399", padx=2, pady=0, cursor="hand2"
            )
            proxy_badge.pack(side="left", padx=(1, 2))

            # Bot name takes remaining space in the middle
            bot_lbl = tk.Label(hdr, text="", font=("Segoe UI", 8), bg="#131d33", fg="#ffffff", anchor="w")
            bot_lbl.pack(side="left", fill="x", expand=True, padx=(2, 0))

            # Single click on VPN badge for quick IP check dialog
            proxy_badge.bind("<Button-1>", lambda e, idx=i: self.check_slot_ip_quick(idx))

            # Double click header to toggle full screen
            hdr.bind("<Double-Button-1>", lambda e, idx=i: self.toggle_fullscreen_slot(idx))
            title_lbl.bind("<Double-Button-1>", lambda e, idx=i: self.toggle_fullscreen_slot(idx))
            bot_lbl.bind("<Double-Button-1>", lambda e, idx=i: self.toggle_fullscreen_slot(idx))

            # Native Game Container (Clips off Chrome's white titlebar completely!)
            game_area = tk.Frame(slot_box, bg="#000000", borderwidth=0, highlightthickness=0)
            game_area.pack(fill="both", expand=True, side="bottom")

            placeholder = tk.Label(game_area, text=f"{SLOT_TITLES[i]}\n(Нажмите 'Запустить')", font=("Segoe UI", 9), bg="#000000", fg="#475569")
            placeholder.place(relx=0.5, rely=0.5, anchor="center")

            self.slot_cards.append({
                "box": slot_box,
                "hdr": hdr,
                "title_lbl": title_lbl,
                "bot_lbl": bot_lbl,
                "proxy_badge": proxy_badge,
                "btn_fs": btn_fs,
                "btn_reload": btn_reload,
                "btn_ip": btn_ip,
                "game_area": game_area,
                "placeholder": placeholder,
                "slot_idx": i
            })

    def cycle_grid_mode(self):
        keys = list(GRID_CONFIGS.keys())
        curr_idx = keys.index(self.current_grid_key) if self.current_grid_key in keys else 0
        next_key = keys[(curr_idx + 1) % len(keys)]
        self.set_grid_mode(next_key)

    def set_grid_mode(self, key):
        if key not in GRID_CONFIGS:
            return
        self.current_grid_key = key
        cfg = GRID_CONFIGS[key]
        self.page_size = cfg["slots"]
        self.total_pages = max(1, (len(self.bot_profiles) + self.page_size - 1) // self.page_size)
        self.current_page = min(self.current_page, self.total_pages)
        self.btn_grid.config(text=cfg["label"])
        self.zoom_var.set(cfg["default_zoom"])
        self.status_lbl.config(text=f"📱 Режим: {cfg['label']}")
        self.update_page_nav()
        self.reposition_all_active_windows()

    def has_live_browser_sessions(self):
        for session in self.browser_sessions.values():
            pid = session.get('pid')
            if not pid:
                continue
            identity = process_identity(pid)
            if identity and (not session.get('identity') or session['identity'] == identity):
                return True
        return False

    def import_from_happ(self):
        self._load_server_json(self.proxy_manager.vpn_dispatcher.export_happ_json,
                               'Читаем загруженные JSON-конфигурации из Happ…', 'Экспорт из Happ')

    def import_from_subscription(self, value):
        try:
            url = normalize_subscription_url(value)
        except ValueError:
            self.happ_status_var.set('Вставьте полную HTTP/HTTPS-ссылку подписки без пробелов.')
            return
        self._load_server_json(lambda: self.proxy_manager.vpn_dispatcher.export_subscription_json(url),
                               'Получаем JSON серверов по ссылке…', 'Получение JSON по ссылке', source_url=url)

    def _load_server_json(self, loader, progress, title, source_url=None):
        if not self.use_vpn or self._happ_import_busy or getattr(self, '_network_settings_busy', False):
            return
        self._happ_import_busy = True
        self.happ_status_var.set(progress)
        self._happ_import_buttons = [button for button in self._happ_import_buttons
                                     if button.winfo_exists()]
        for button in self._happ_import_buttons:
            button.config(state="disabled")
        results = queue.Queue()

        def _worker():
            try:
                result = loader()
            except Exception:
                result = (False, 'Не удалось получить JSON серверов. Проверьте источник и повторите.', '')
            results.put(result)

        def _poll_result():
            try:
                ok, message, raw_text = results.get_nowait()
            except queue.Empty:
                self.after(100, _poll_result)
                return
            self._happ_import_busy = False
            for button in self._happ_import_buttons:
                if button.winfo_exists():
                    button.config(state="normal")
            self.happ_status_var.set(message)
            if ok:
                self._pending_happ_json = raw_text
                self._pending_subscription_url = source_url
                refresh = getattr(self, "_happ_settings_refresh", None)
                if refresh:
                    refresh(raw_text)
            else:
                parent = getattr(self, "_happ_settings_dialog", None)
                if parent is None or not parent.winfo_exists():
                    parent = self
                messagebox.showerror(title, message, parent=parent)

        threading.Thread(target=_worker, daemon=True).start()
        self.after(100, _poll_result)

    def open_proxy_settings(self):
        if not self.use_vpn:
            messagebox.showinfo("Без VPN", "Выбран режим прямого подключения.\n"
                                "Для работы с VPN перезапустите программу и выберите «С VPN».", parent=self)
            return
        existing = getattr(self, '_happ_settings_dialog', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        dlg = tk.Toplevel(self)
        dlg.title("🌐 Сеть, Подписка и Мульти-VPN (Xray Core)")
        height = min(900, max(560, self.winfo_screenheight() - 80))
        dlg.geometry(f"800x{height}")
        dlg.minsize(620, 560)
        dlg.configure(bg="#0b1120")
        dlg.transient(self)
        dlg.grab_set()
        self._happ_settings_dialog = dlg

        vd = self.proxy_manager.vpn_dispatcher

        # Top Header
        hdr = tk.Frame(dlg, bg="#131d33", pady=10, padx=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🌐 Диспетчер VPN & Мульти-прокси (Свой IP для каждого окна)", font=("Segoe UI", 11, "bold"), bg="#131d33", fg="#38bdf8").pack(anchor="w")
        tk.Label(hdr, text="Каждое окно браузера получает собственный IP-адрес через локальные порты Xray.", font=("Segoe UI", 8), bg="#131d33", fg="#94a3b8").pack(anchor="w")

        body = tk.Frame(dlg, bg="#0b1120")
        body.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(body, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        canvas = tk.Canvas(body, bg="#0b1120", highlightthickness=0, yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=canvas.yview)
        content = tk.Frame(canvas, bg="#0b1120", padx=14, pady=10)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind('<Configure>', lambda event: canvas.config(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content_window, width=event.width))

        def _scroll_settings(event):
            if not isinstance(event.widget, tk.Text) and event.delta:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
                return 'break'
        dlg.bind('<MouseWheel>', _scroll_settings)

        subscription_card = tk.LabelFrame(
            content, text=" JSON серверов по ссылке подписки ", font=("Segoe UI", 9, "bold"),
            bg="#131d33", fg="#38bdf8", padx=10, pady=8)
        subscription_card.pack(fill="x", pady=(0, 8))
        saved_url = getattr(self, '_pending_subscription_url', None) or vd.config.get('subscription_url') or vd.config.get('happ_subscription_url', '')
        subscription_var = tk.StringVar(value=saved_url)
        subscription_status = self.happ_status_var
        subscription_row = tk.Frame(subscription_card, bg="#131d33")
        subscription_row.pack(fill="x")
        subscription_entry = tk.Entry(
            subscription_row, textvariable=subscription_var, show="•", bg="#070c18", fg="#cbd5e1",
            insertbackground="#ffffff", relief="flat", font=("Consolas", 10), exportselection=False)
        subscription_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))

        def _paste_subscription(event=None, replace_all=False):
            try:
                text = subscription_entry.clipboard_get().strip()
            except tk.TclError:
                subscription_status.set("В буфере обмена нет текста. Сначала скопируйте ссылку подписки.")
                return 'break'
            if not text:
                subscription_status.set("Буфер обмена пуст. Сначала скопируйте ссылку подписки.")
                return 'break'
            if replace_all:
                subscription_entry.delete(0, tk.END)
            elif subscription_entry.selection_present():
                subscription_entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            subscription_entry.insert(tk.INSERT, text)
            subscription_entry.focus_set()
            subscription_entry.xview(tk.INSERT)
            subscription_status.set("Ссылка вставлена из буфера обмена.")
            return 'break'

        def _select_subscription():
            subscription_entry.selection_range(0, tk.END)
            subscription_entry.icursor(tk.END)
            subscription_entry.focus_set()
            return 'break'

        def _subscription_shortcut(event):
            # Windows virtual-key codes do not depend on the keyboard layout.
            if event.keycode == 0x56 or event.keysym.lower() == 'v':
                return _paste_subscription(event)
            if event.keycode == 0x41 or event.keysym.lower() == 'a':
                return _select_subscription()

        subscription_entry.bind('<Control-KeyPress>', _subscription_shortcut)
        subscription_entry.bind('<Shift-Insert>', _paste_subscription)
        subscription_entry.bind('<<Paste>>', _paste_subscription)
        paste_menu = tk.Menu(subscription_entry, tearoff=False)
        paste_menu.add_command(label="Вставить ссылку", command=lambda: _paste_subscription(replace_all=True))
        paste_menu.add_command(label="Выделить всё", command=_select_subscription)

        def _show_subscription_menu(event):
            try:
                paste_menu.tk_popup(event.x_root, event.y_root)
            finally:
                paste_menu.grab_release()
            return 'break'

        subscription_entry.bind('<Button-3>', _show_subscription_menu)
        tk.Button(
            subscription_row, text="Вставить", command=lambda: _paste_subscription(replace_all=True),
            bg="#334155", fg="#ffffff", font=("Segoe UI", 9), relief="flat", padx=8, pady=5
        ).pack(side="left", padx=(0, 6))
        show_subscription = tk.BooleanVar(value=False)
        tk.Checkbutton(
            subscription_card, text="Показать ссылку", variable=show_subscription,
            command=lambda: subscription_entry.config(show="" if show_subscription.get() else "•"),
            bg="#131d33", fg="#94a3b8", selectcolor="#070c18", activebackground="#131d33",
            activeforeground="#cbd5e1", font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(subscription_card, textvariable=subscription_status, bg="#131d33", fg="#cbd5e1",
                 font=("Segoe UI", 8), wraplength=690, justify="left", anchor="w").pack(fill="x", pady=(3, 0))
        tk.Label(subscription_card, text="Программа получает список серверов напрямую по ссылке. Happ не нужен и не открывается. Если провайдер требует ID устройства, передаётся постоянный ID этого компьютера Windows.",
                 bg="#131d33", fg="#94a3b8", font=("Segoe UI", 8), wraplength=690,
                 justify="left", anchor="w").pack(fill="x", pady=(4, 0))

        open_subscription_button = tk.Button(
            subscription_row, text="Получить JSON по ссылке", command=lambda: self.import_from_subscription(subscription_var.get()),
            bg="#0369a1", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=5)
        open_subscription_button.pack(side="right")
        self._happ_import_buttons.append(open_subscription_button)
        subscription_entry.bind('<Return>', lambda event: self.import_from_subscription(subscription_var.get()))
        # ---------------- ИНСТРУКЦИЯ ПО НАСТРОЙКЕ VPN ----------------
        instr_card = tk.LabelFrame(
            content,
            text=" 📋 Инструкция по настройке VPN ",
            font=("Segoe UI", 9, "bold"),
            bg="#131d33",
            fg="#38bdf8",
            padx=12,
            pady=8
        )
        instr_card.pack(fill="x", pady=(0, 8), before=subscription_card)

        steps = [
            ("1", "Вставьте ссылку подписки и нажмите «Получить JSON по ссылке». Установка Happ не нужна."),
            ("2", "Список серверов появится в поле JSON. Текущие VPN-маршруты при получении не меняются."),
            ("3", "Получение JSON заполняет поле. Для применения закройте ботов и нажмите «Применить и закрыть»."),
        ]
        for num, text in steps:
            row = tk.Frame(instr_card, bg="#131d33")
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{num}.", font=("Segoe UI", 9, "bold"), bg="#131d33", fg="#38bdf8", width=3, anchor="w").pack(side="left")
            tk.Label(row, text=text, font=("Segoe UI", 9), bg="#131d33", fg="#e2e8f0", anchor="w", justify="left", wraplength=660).pack(side="left", fill="x")

        happ_button = tk.Button(
            instr_card, text="Получить JSON из открытого Happ", command=self.import_from_happ,
            state="disabled" if self._happ_import_busy else "normal",
            bg="#047857", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=5)
        happ_button.pack(anchor="w", pady=(8, 0))
        self._happ_import_buttons.append(happ_button)

        tk.Label(content, text="ИЛИ", font=("Segoe UI", 11, "bold"),
                 bg="#0b1120", fg="#94a3b8").pack(fill="x", pady=(0, 8), before=subscription_card)

        # ---------------- СПИСОК СЕРВЕРОВ И КЛЮЧЕЙ ----------------
        keys_card = tk.LabelFrame(content, text=" Список серверов (Happ / vless://, sing-box JSON, trojan://, vmess://, ss://) ", font=("Segoe UI", 9, "bold"), bg="#131d33", fg="#34d399", padx=10, pady=8)
        keys_card.pack(fill="x", pady=(0, 8))

        keys_text = tk.Text(keys_card, bg="#070c18", fg="#cbd5e1", insertbackground="#ffffff", font=("Consolas", 8), height=4, relief="flat", padx=6, pady=4)
        keys_text.pack(fill="x", pady=(2, 6))
        raw_saved = getattr(self, '_pending_happ_json', None)
        if raw_saved is None:
            raw_saved = vd.config.get("raw_nodes_text", "")
        if raw_saved:
            keys_text.insert("1.0", raw_saved)

        keys_btn_row = tk.Frame(keys_card, bg="#131d33")
        keys_btn_row.pack(fill="x")

        # ---------------- 3. ACTIVE POOL & SLOTS DISTRIBUTION ----------------
        pool_card = tk.LabelFrame(content, text=" Маршруты VPN для окон ", font=("Segoe UI", 9, "bold"), bg="#131d33", fg="#a78bfa", padx=10, pady=8)
        pool_card.pack(fill="both", expand=True, pady=(0, 8))

        pool_status_lbl = tk.Label(pool_card, text="", font=("Segoe UI", 8, "bold"), bg="#131d33", fg="#34d399")
        pool_status_lbl.pack(anchor="w")

        pool_text = tk.Text(pool_card, bg="#070c18", fg="#93c5fd", font=("Consolas", 8), height=7, relief="flat", padx=6, pady=4)
        pool_text.pack(fill="both", expand=True, pady=6)

        def _refresh_pool_display():
            try:
                if not dlg.winfo_exists():
                    return
                pool_text.delete("1.0", tk.END)
                nodes_copy = vd.get_nodes_snapshot()
                node_count = len(nodes_copy)
                all_count = len(vd.nodes)

                if not nodes_copy:
                    pool_status_lbl.config(
                        text="⚪ В пуле: 0 серверов — получите JSON по ссылке и примените список",
                        fg="#94a3b8"
                    )
                    pool_text.insert(tk.END, "⚠️ Серверы не загружены.\nВставьте ссылку подписки, получите JSON и нажмите «Применить и закрыть».\n")
                    return

                if vd._health_revision == vd._nodes_revision:
                    pool_status_lbl.config(
                        text=f"Доступно по последней проверке: {len(vd.alive_nodes)} из {all_count}. Все ключи сохранены.",
                        fg="#34d399" if vd.alive_nodes else "#f87171"
                    )
                else:
                    pool_status_lbl.config(
                        text=f"Сохранено серверов: {node_count}. Доступность ещё не проверена.",
                        fg="#94a3b8"
                    )

                for i in range(max(12, len(self.bot_profiles))):
                    node = nodes_copy[i % node_count]
                    port = BASE_LOCAL_PORT + (i % max(48, node_count))
                    clean_tag = node.tag
                    pool_text.insert(tk.END, f"Слот #{i+1:02d} -> 127.0.0.1:{port} -> 🛡️ [{clean_tag}] ({node.protocol.upper()} -> {node.address}:{node.port})\n")
            except Exception:
                pass

        _refresh_pool_display()

        def _refresh_happ_dialog(raw_text=None):
            if dlg.winfo_exists():
                pending_url = getattr(self, '_pending_subscription_url', None)
                if pending_url:
                    subscription_var.set(pending_url)
                if raw_text is None:
                    raw_text = getattr(self, '_pending_happ_json', None)
                if raw_text is None:
                    raw_text = vd.config.get("raw_nodes_text", "")
                keys_text.delete("1.0", tk.END)
                keys_text.insert("1.0", raw_text)
                _refresh_pool_display()

        self._happ_settings_refresh = _refresh_happ_dialog

        def _apply_custom_keys():
            if self._happ_import_busy or getattr(self, '_network_settings_busy', False):
                self.happ_status_var.set("Дождитесь завершения получения JSON из Happ.")
                return
            if self.has_live_browser_sessions():
                self.happ_status_var.set("JSON остаётся в поле. Для применения новых серверов сначала закройте окна ботов.")
                return
            txt = keys_text.get("1.0", tk.END).strip()
            if not txt:
                messagebox.showwarning("Внимание", "Текстовое поле пусто!", parent=dlg)
                return
            self._network_settings_busy = True
            source_url = getattr(self, '_pending_subscription_url', None)
            def _worker():
                try:
                    if source_url:
                        ok, msg = vd.set_custom_nodes_text(txt, source_url=source_url)
                    else:
                        ok, msg = vd.set_custom_nodes_text(txt)
                except Exception:
                    ok, msg = False, 'Не удалось применить серверы. Проверьте настройки Xray и повторите.'
                def _done():
                    self._network_settings_busy = False
                    try:
                        if not dlg.winfo_exists():
                            return
                        _refresh_pool_display()
                        self.periodic_vpn_check(schedule_next=False)
                        self.update_all_slot_proxy_badges()
                        if ok:
                            self._pending_happ_json = None
                            self._pending_subscription_url = None
                            self.happ_status_var.set(msg)
                            dlg.destroy()
                        else:
                            messagebox.showerror("Ошибка", msg, parent=dlg)
                    except Exception:
                        pass
                self.after(0, _done)
            threading.Thread(target=_worker, daemon=True).start()

        def _open_proxies_file():
            try:
                os.startfile(self.proxy_manager.proxies_file)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось открыть файл: {e}", parent=dlg)

        tk.Button(keys_btn_row, text="📄 Открыть proxies.txt", command=_open_proxies_file, bg="#334155", fg="#cbd5e1", font=("Segoe UI", 8), relief="flat", padx=8, pady=2, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(keys_btn_row, text="🗑️ Очистить поле", command=lambda: keys_text.delete("1.0", tk.END), bg="#334155", fg="#cbd5e1", font=("Segoe UI", 8), relief="flat", padx=8, pady=2, cursor="hand2").pack(side="left")

        # Pool action buttons row
        pool_btn_row = tk.Frame(pool_card, bg="#131d33")
        pool_btn_row.pack(fill="x")

        def _filter_nodes_async():
            pool_text.delete("1.0", tk.END)
            pool_text.insert(tk.END, "🔍 Проверка доступности серверов без изменения маршрутов окон...\n")
            def _worker():
                alive, total = vd.filter_alive_nodes(
                    timeout=4.0,
                    max_workers=16,
                    progress_callback=lambda m: self.after(0, lambda msg=m: [
                        pool_text.insert(tk.END, msg + "\n"),
                        pool_text.see(tk.END)
                    ])
                )
                def _update_ui():
                    try:
                        if not dlg.winfo_exists():
                            return
                        _refresh_pool_display()
                        self.periodic_vpn_check(schedule_next=False)
                        self.update_all_slot_proxy_badges()
                        messagebox.showinfo(
                            "Проверка завершена",
                            f"Проверено {total} серверов.\n\n"
                            f"✅ Рабочих серверов: {alive}\n"
                            f"Не ответили: {total - alive}\n\n"
                            "Все ключи сохранены. Маршруты открытых окон сохранены.",
                            parent=dlg
                        )
                    except Exception:
                        pass
                self.after(0, _update_ui)
            threading.Thread(target=_worker, daemon=True).start()

        def _ping_all_ports_async():
            pool_text.delete("1.0", tk.END)
            pool_text.insert(tk.END, "Проверяем HTTPS через VPN каждого окна...\n")
            def _worker():
                if not vd.is_running:
                    vd.start_xray()
                    time.sleep(0.3)
                slots = vd.ping_all_slots(count=max(12, len(self.bot_profiles)))
                def _update_ui():
                    try:
                        if not dlg.winfo_exists():
                            return
                        pool_text.delete("1.0", tk.END)
                        for s in slots:
                            status_icon = "🟢 HTTPS OK" if s["alive"] else "🔴 VPN НЕДОСТУПЕН"
                            pool_text.insert(tk.END, f"Слот #{s['slot']:02d} -> 127.0.0.1:{s['port']} -> {status_icon} -> 🛡️ [{s['tag']}]\n")
                    except Exception:
                        pass
                self.after(0, _update_ui)
            threading.Thread(target=_worker, daemon=True).start()

        def _restart_xray_action():
            ok = vd.start_xray(force=True)
            if ok:
                _refresh_pool_display()
                self.periodic_vpn_check(schedule_next=False)
                self.update_all_slot_proxy_badges()
                messagebox.showinfo("Ядро Xray", f"Ядро Xray успешно перезапущено с {len(vd.get_nodes_snapshot())} серверами!", parent=dlg)
            else:
                messagebox.showwarning("Ядро Xray", vd.last_error or "Не удалось запустить Xray", parent=dlg)

        tk.Button(pool_btn_row, text="🔄 Проверить VPN всех окон", command=_ping_all_ports_async, bg="#2563eb", fg="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=2, cursor="hand2").pack(side="left", padx=(0, 6))
        def _clear_all_servers_action():
            if self._happ_import_busy or getattr(self, '_network_settings_busy', False):
                self.happ_status_var.set("Дождитесь завершения получения JSON из Happ.")
                return
            if not messagebox.askyesno(
                "Удаление всех серверов",
                "Вы действительно хотите удалить ВСЕ серверы из пула?\n\n"
                "• Все сохраненные ключи серверов будут очищены\n"
                "• Подписка будет сброшена\n"
                "• Ядро этого запускателя будет остановлено\n"
                "• Общее ядро другого запускателя продолжит работу; его порты останутся заняты",
                parent=dlg
            ):
                return
            ok, msg = vd.clear_all_nodes()
            if not ok:
                messagebox.showwarning("Удаление серверов", msg, parent=dlg)
                return
            keys_text.delete("1.0", tk.END)
            self._pending_happ_json = None
            self._pending_subscription_url = None
            subscription_var.set('')
            _refresh_pool_display()
            self.periodic_vpn_check(schedule_next=False)
            self.update_all_slot_proxy_badges()
            messagebox.showinfo("Готово", msg, parent=dlg)

        tk.Button(pool_btn_row, text="🔄 Перезапустить ядро Xray", command=_restart_xray_action, bg="#7c3aed", fg="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=2, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(pool_btn_row, text="🗑️ Удалить все сервера", command=_clear_all_servers_action, bg="#dc2626", fg="#ffffff", activebackground="#991b1b", activeforeground="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=2, cursor="hand2").pack(side="left")

        def _on_finish_and_close():
            if self._happ_import_busy or getattr(self, '_network_settings_busy', False):
                subscription_status.set("Дождитесь завершения получения JSON или применения серверов.")
                return
            txt = keys_text.get("1.0", tk.END).strip()
            current_saved = vd.config.get("raw_nodes_text", "").strip()
            self._pending_happ_json = txt if txt != current_saved else None
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _on_finish_and_close)
        settings_actions = tk.Frame(content, bg="#0b1120")
        settings_actions.pack(fill="x")
        settings_actions.columnconfigure((0, 1), weight=1, uniform="settings_actions")
        tk.Button(
            settings_actions, text="Применить и закрыть", command=_apply_custom_keys,
            bg="#059669", fg="#ffffff", activebackground="#047857", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat", pady=5, cursor="hand2"
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Button(
            settings_actions, text="Не применять и закрыть", command=_on_finish_and_close,
            bg="#2563eb", fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat", pady=5, cursor="hand2"
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def close_all_bots(self):
        """Closes all bot Chrome processes to instantly free memory and CPU."""
        if not messagebox.askyesno("Подтверждение", "Закрыть все открытые окна ботов Chrome?"):
            return
        self.status_lbl.config(text="🛑 Закрытие ботов...")
        self._closing_browsers = True
        self.browser_profiles.cancel_pending()
        done = threading.Event()
        def close_worker():
            try:
                self.browser_profiles.close()
            except Exception:
                LAUNCH_LOG.exception('Browser cleanup failed')
            finally:
                try:
                    self.proxy_manager.release_all_profiles()
                except Exception:
                    LAUNCH_LOG.exception('VPN allocation cleanup failed')
                done.set()
        threading.Thread(target=close_worker, daemon=False).start()
        def finish():
            if not done.is_set():
                self.after(100, finish)
                return
            self.browser_sessions.clear()
            self.profile_hwnds.clear()
            self._docked_hwnds.clear()
            self._chrome_insets.clear()
            self._closing_browsers = False
            self.reposition_all_active_windows()
            self.status_lbl.config(text="✅ Все боты закрыты")
        self.after(100, finish)

    def shutdown_app(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        login_view = getattr(self, '_login_view', None)
        if login_view is not None:
            login_view.release()
        login_task = getattr(self, '_login_task', None)
        if login_task is not None and not login_task.done():
            login_task.set_exception(LaunchCancelled('Приложение закрывается.'))
        overlay = getattr(self, '_mine_overlay', None)
        if overlay is not None:
            overlay.hide()
        self._browser_focus.close()
        if self._status_spinner_timer is not None:
            self.after_cancel(self._status_spinner_timer)
            self._status_spinner_timer = None
        self._closing_browsers = True
        self.browser_profiles.cancel_pending()
        self.withdraw()
        done = threading.Event()
        def worker():
            try:
                for cleanup in (self.browser_profiles.shutdown, self.proxy_manager.close):
                    try:
                        cleanup()
                    except Exception:
                        LAUNCH_LOG.exception('Application shutdown cleanup failed')
            finally:
                done.set()
        threading.Thread(target=worker, daemon=False).start()
        def finish():
            if done.is_set():
                self.destroy()
            else:
                self.after(100, finish)
        self.after(100, finish)

    def reload_slot(self, slot_idx):
        """Reloads the web page in-place inside the docked Chrome window without spawning new windows."""
        start_idx = (self.current_page - 1) * self.page_size
        idx = start_idx + slot_idx
        if idx >= len(self.bot_profiles):
            return

        card = self.slot_cards[slot_idx]
        btn_rel = card["btn_reload"]
        btn_rel.config(state="disabled")

        p = self.bot_profiles[idx]
        key = p["key"]
        p_name = p['gaia'] or p['name']
        hwnd = self.profile_hwnds.get(key)

        self.status_lbl.config(text=f"🔄 Перезагрузка: {p_name}...")
        card["bot_lbl"].config(text=f"• {p_name} [Перезагрузка...]")

        # Dynamic visual spinner animation on the reload button
        spin_frames = ["◐", "◓", "◑", "◒"]
        anim_step = 0
        def _animate_reload():
            nonlocal anim_step
            if anim_step < 8:
                frame = spin_frames[anim_step % len(spin_frames)]
                btn_rel.config(text=frame, fg="#fbbf24")
                anim_step += 1
                self.after(140, _animate_reload)
            else:
                btn_rel.config(text="↻", fg="#38bdf8", state="normal", bg="#1e293b")
                cur_start = (self.current_page - 1) * self.page_size
                if cur_start + slot_idx == idx:
                    display_name = p_name
                    if self.current_grid_key == "8x2" and len(display_name) > 11:
                        display_name = display_name[:9] + "…"
                    suffix = " [2ip]" if self.slot_urls.get(key, "game") == "2ip" else ""
                    card["bot_lbl"].config(text=f"• {display_name}{suffix}")
                    self.update_slot_proxy_badge(slot_idx)
                self.status_lbl.config(text=f"✅ {p_name} перезагружен")

        _animate_reload()

        if hwnd and user32.IsWindow(hwnd):
            # Reload only the current page. Do not navigate history or broadcast
            # duplicate F5 presses to the renderer and its child windows.
            user32.PostMessageW(hwnd, WM_COMMAND, IDC_RELOAD, 0)

            self.after(100, lambda: self._restore_slot_docking(key, slot_idx, hwnd))
        else:
            # Bot process wasn't running or was closed: launch and dock it in background
            cur_mode = self.slot_urls.get(key, "game")
            t_url = "https://2ip.io" if cur_mode == "2ip" else "https://play.multidonate.ru/c/zakvielchannel/"
            threading.Thread(target=self.run_launch_single, args=(key, slot_idx, idx, t_url),
                             kwargs={'generation': self.browser_profiles.generation}, daemon=True).start()

    def _restore_slot_docking(self, key, slot_idx, hwnd):
        # Reload/navigation exits DOM fullscreen asynchronously and can restore
        # Chrome's old native bounds. Never reveal a slot after a page switch.
        current_idx = (self.current_page - 1) * self.page_size + slot_idx
        if (current_idx < len(self.bot_profiles)
                and self.bot_profiles[current_idx]['key'] == key
                and self.profile_hwnds.get(key) == hwnd
                and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd)):
            self.dock_hwnd_to_slot(hwnd, slot_idx, _settle_attempt=1)

    def toggle_slot_url(self, slot_idx):
        """Toggles between Legion game and 2ip.io inside the docked Chrome window."""
        start_idx = (self.current_page - 1) * self.page_size
        idx = start_idx + slot_idx
        if idx >= len(self.bot_profiles):
            return

        card = self.slot_cards[slot_idx]
        btn_ip = card.get("btn_ip")
        if btn_ip:
            btn_ip.config(state="disabled")

        p = self.bot_profiles[idx]
        key = p["key"]
        p_name = p['gaia'] or p['name']
        hwnd = self.profile_hwnds.get(key)

        cur_mode = self.slot_urls.get(key, "game")
        if cur_mode == "game":
            new_mode = "2ip"
            target_url = "https://2ip.io"
            new_btn_text = "🎮"
            new_btn_fg = "#34d399"
            status_text = f"🌐 Открытие 2ip.io: {p_name} (#{slot_idx+1})..."
            lbl_suffix = " [2ip]"
        else:
            new_mode = "game"
            target_url = "https://play.multidonate.ru/c/zakvielchannel/"
            new_btn_text = "🌐"
            new_btn_fg = "#a78bfa"
            status_text = f"🎮 Возврат в игру: {p_name} (#{slot_idx+1})..."
            lbl_suffix = ""

        self.status_lbl.config(text=status_text)

        display_name = p_name
        if self.current_grid_key == "8x2" and len(display_name) > 11:
            display_name = display_name[:9] + "…"
        card["bot_lbl"].config(text=f"• {display_name} [Переход...]")

        generation = self.browser_profiles.generation

        def _finish_switch(success, error, had_window):
            if btn_ip:
                btn_ip.config(state="normal")
            if success:
                self.slot_urls[key] = new_mode
                self._launch_errors.pop(key, None)
            current_idx = (self.current_page - 1) * self.page_size + slot_idx
            if current_idx >= len(self.bot_profiles) or self.bot_profiles[current_idx]['key'] != key:
                return
            if success:
                if btn_ip:
                    btn_ip.config(text=new_btn_text, fg=new_btn_fg)
                card["bot_lbl"].config(text=f"• {display_name}{lbl_suffix}", fg="#ffffff")
                self.status_lbl.config(text=f"Открыто: {'2ip.io' if new_mode == '2ip' else 'игра'} — {p_name}")
                self.update_slot_proxy_badge(slot_idx)
                current_hwnd = self.profile_hwnds.get(key)
                self.after(100, lambda: self._restore_slot_docking(key, slot_idx, current_hwnd))
            elif had_window:
                old_suffix = " [2ip]" if cur_mode == "2ip" else ""
                card["bot_lbl"].config(text=f"• {display_name}{old_suffix}", fg="#ffffff")
                if error:
                    self.status_lbl.config(text=f"Не удалось открыть {'2ip.io' if new_mode == '2ip' else 'игру'}: {error}")

        def _do_switch():
            success, error, had_window = False, None, False
            try:
                had_window = bool(hwnd and user32.IsWindow(hwnd))
                if had_window:
                    self.browser_profiles.navigate_app(key, target_url, generation=generation)
                    success = True
                else:
                    success = self.run_launch_single(key, slot_idx, proxy_slot_idx=idx,
                                                     target_url=target_url, generation=generation)
            except LaunchCancelled:
                pass
            except Exception as exc:
                LAUNCH_LOG.exception('Browser navigation failed')
                error = getattr(exc, 'user_message', str(exc))
            finally:
                try:
                    self.after(0, lambda: _finish_switch(success, error, had_window))
                except (RuntimeError, tk.TclError):
                    pass  # The manager was closed during the navigation.

        threading.Thread(target=_do_switch, daemon=True).start()

    def check_slot_ip_quick(self, slot_idx):
        if not self.use_vpn:
            self.toggle_slot_url(slot_idx)
            return
        idx = (self.current_page - 1) * self.page_size + slot_idx
        if idx >= len(self.bot_profiles):
            return
        profile = self.bot_profiles[idx]
        session = self.browser_sessions.get(profile['key'])
        if not session:
            messagebox.showwarning('Проверка IP', 'Нет запущенного окна с проверенным прокси.', parent=self)
            return
        self.status_lbl.config(text=f'Проверка HTTPS через прокси окна #{slot_idx+1}...')

        def query():
            try:
                ip = fetch_proxy_ip(session['proxy'])
                session['verified_ip'] = ip
                session['verified_at'] = time.monotonic()
                session.pop('network_failed', None)
                message = f"IP через прокси окна #{slot_idx+1}: {ip}\nПрямой резерв отключён."
            except Exception:
                session.pop('verified_ip', None)
                session['network_failed'] = True
                message = 'Прокси не отвечает. Прямой резерв отключён; проверьте настройки сети.'
            self.after(0, lambda: [self.update_slot_proxy_badge(slot_idx),
                                   messagebox.showinfo('Проверка IP окна', message, parent=self)])
        threading.Thread(target=query, daemon=True).start()

    def toggle_fullscreen_slot(self, slot_idx):
        if self.fullscreen_slot == slot_idx:
            self.exit_fullscreen_slot()
        else:
            self.enter_fullscreen_slot(slot_idx)

    def enter_fullscreen_slot(self, slot_idx):
        self.fullscreen_slot = slot_idx
        try:
            self.btn_exit_fs.pack(side="left", after=self.btn_next, padx=3)
        except Exception:
            self.btn_exit_fs.pack(side="left", padx=3)

        self.reposition_all_active_windows()
        self.after(100, self.check_fullscreen_hotkey)

    def exit_fullscreen_slot(self):
        if self.fullscreen_slot is None:
            return
        self.fullscreen_slot = None
        self.btn_exit_fs.pack_forget()
        self.reposition_all_active_windows()

    def check_fullscreen_hotkey(self):
        """Polls physical Escape key state so Esc exits full screen even when Chrome has input focus."""
        if self.fullscreen_slot is None:
            return
        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            self.exit_fullscreen_slot()
            return
        self.after(100, self.check_fullscreen_hotkey)

    def dock_hwnd_to_slot(self, hwnd, slot_idx, width=None, height=None, _settle_attempt=0):
        """Dock only Chrome's page area, clipping tabs, toolbar and window borders.
        Optimized to NEVER re-dock, re-parent, or trigger SWP_FRAMECHANGED on already docked windows."""
        login_view = getattr(self, '_login_view', None)
        if login_view is not None and login_view.owns(hwnd):
            return
        if not hwnd or not user32.IsWindow(hwnd) or slot_idx >= len(self.slot_cards):
            return

        card = self.slot_cards[slot_idx]
        game_area = card["game_area"]
        parent_hwnd = game_area.winfo_id()

        if width is not None and height is not None:
            w = max(50, int(width))
            h = max(50, int(height))
        else:
            w = game_area.winfo_width()
            h = game_area.winfo_height()
            if w <= 50 or h <= 50:
                game_area.update_idletasks()
                w = max(50, game_area.winfo_width())
                h = max(50, game_area.winfo_height())

        measured = chrome_content_insets(hwnd)
        if measured is not None:
            self._chrome_insets[hwnd] = measured
        left, top, right, bottom = self._chrome_insets.get(hwnd, (0, CHROME_TITLEBAR_OFFSET, 0, 0))
        offset_y = -top
        total_h = h + top + bottom

        offset_x = -left
        total_w = w + left + right

        current_parent = user32.GetParent(hwnd)
        is_already_docked = (hwnd in self._docked_hwnds) and (current_parent == parent_hwnd)

        if not is_already_docked:
            # 1. Clean window styles: WS_CHILD, WS_CLIPCHILDREN, WS_CLIPSIBLINGS, remove captions, borders
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            target_style = ((style & ~WS_POPUP) | WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS) & ~(WS_CAPTION | WS_THICKFRAME | WS_BORDER)
            if style != target_style:
                user32.SetWindowLongW(hwnd, GWL_STYLE, target_style)

            # 2. Clean extended styles: remove clientedge, windowedge, staticedge
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            target_ex_style = ex_style & ~(WS_EX_CLIENTEDGE | WS_EX_WINDOWEDGE | WS_EX_STATICEDGE)
            if ex_style != target_ex_style:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, target_ex_style)

            # 3. Reparent strictly into this slot's individual game_area frame
            user32.SetParent(hwnd, parent_hwnd)
            self._docked_hwnds.add(hwnd)

            # 4. First-time positioning with SWP_FRAMECHANGED (called ONCE only upon initial docking)
            user32.SetWindowPos(
                hwnd, 0,
                offset_x, offset_y, total_w, total_h,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
            )
        else:
            # Repeated Configure/settle callbacks must not resize a Chrome that
            # is already aligned: even identical native moves can repaint it.
            bounds = wintypes.RECT()
            origin = wintypes.POINT()
            aligned = bool(
                user32.GetWindowRect(hwnd, ctypes.byref(bounds))
                and user32.ClientToScreen(parent_hwnd, ctypes.byref(origin))
                and (bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top)
                == (origin.x + offset_x, origin.y + offset_y, total_w, total_h))
            if not aligned or not user32.IsWindowVisible(hwnd):
                user32.SetWindowPos(
                    hwnd, 0,
                    offset_x, offset_y, total_w, total_h,
                    SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS
                )

        key = next((k for k, candidate in self.profile_hwnds.items() if candidate == hwnd), None)
        if key:
            self.browser_profiles.fit(key, w, h, zoom=self.get_zoom_factor())

        # Chrome lays out its native content asynchronously after SetParent/frame
        # changes. Remeasure briefly; never reparent or reveal a background slot.
        if (_settle_attempt < MAX_DOCK_SETTLE_ATTEMPTS
                and (not is_already_docked or measured is None or _settle_attempt > 0)):
            def settle():
                if (user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd)
                        and user32.GetParent(hwnd) == parent_hwnd
                        and slot_idx < len(self.slot_cards)
                        and self.slot_cards[slot_idx]['game_area'] is game_area):
                    self.dock_hwnd_to_slot(hwnd, slot_idx, _settle_attempt=_settle_attempt + 1)
            self.after(100 if _settle_attempt < 5 else 500, settle)

    def reposition_all_active_windows(self):
        """Calculates exact geometry for all slots and repositions active Chrome windows."""
        self.slots_frame.update_idletasks()
        total_w = self.slots_frame.winfo_width()
        total_h = self.slots_frame.winfo_height()

        if total_w < 100 or total_h < 100:
            total_w = max(960, self.winfo_width())
            total_h = max(500, self.winfo_height() - 34)

        start_idx = (self.current_page - 1) * self.page_size
        items = self.bot_profiles[start_idx:start_idx + self.page_size]

        if self.fullscreen_slot is not None and self.fullscreen_slot < len(self.slot_cards):
            # ---------------- FULL SCREEN MODE FOR SINGLE SLOT ----------------
            fs_idx = self.fullscreen_slot
            for i in range(len(self.slot_cards)):
                card = self.slot_cards[i]
                box = card["box"]

                if i == fs_idx:
                    box.place(x=0, y=0, width=total_w, height=total_h)
                    card["btn_fs"].config(text="🗗 Вернуться в сетку (Esc)", bg="#2563eb", fg="#ffffff")

                    p = items[i] if i < len(items) else None
                    if p:
                        p_name = p['gaia'] or p['name']
                        card["bot_lbl"].config(text=f"• {p_name} [ПОЛНЫЙ ЭКРАН]")
                        self.update_slot_proxy_badge(i)
                        hwnd = self.profile_hwnds.get(p["key"])
                        if hwnd and user32.IsWindow(hwnd):
                            self.dock_hwnd_to_slot(hwnd, i, width=total_w, height=total_h - SLOT_HEADER_HEIGHT)
                            user32.ShowWindow(hwnd, SW_SHOW)
                else:
                    box.place_forget()
                    p = items[i] if i < len(items) else None
                    if p:
                        hwnd = self.profile_hwnds.get(p["key"])
                        if hwnd and user32.IsWindow(hwnd):
                            user32.ShowWindow(hwnd, SW_HIDE)
        else:
            cfg = GRID_CONFIGS.get(self.current_grid_key, GRID_CONFIGS["6x2"])
            cols = cfg["cols"]
            rows = cfg["rows"]
            slots_count = cfg["slots"]

            col_w = total_w // cols
            row_h = total_h // rows

            for i in range(len(self.slot_cards)):
                card = self.slot_cards[i]
                box = card["box"]

                if i < slots_count:
                    col = i % cols
                    row = i // cols
                    x = col * col_w
                    y = row * row_h
                    w = (total_w - x) if col == cols - 1 else col_w
                    h = (total_h - y) if row == rows - 1 else row_h

                    box.place(x=x, y=y, width=w, height=h)
                    card["btn_fs"].config(text="⛶", bg="#1e293b", fg="#ffffff")

                    p = items[i] if i < len(items) else None
                    if p:
                        p_name = p['gaia'] or p['name']
                        display_name = p_name
                        if self.current_grid_key == "8x2" and len(display_name) > 11:
                            display_name = display_name[:9] + "…"
                        if p["key"] in self._vpn_failed_keys:
                            card["bot_lbl"].config(text=f"• {display_name} — ВПН не подключился", fg="#ef4444")
                        else:
                            card["bot_lbl"].config(text=f"• {display_name}", fg="#ffffff")
                        self.update_slot_proxy_badge(i)
                        hwnd = self.profile_hwnds.get(p["key"])
                        if hwnd and user32.IsWindow(hwnd):
                            self.dock_hwnd_to_slot(hwnd, i, width=w, height=h - SLOT_HEADER_HEIGHT)
                            user32.ShowWindow(hwnd, SW_SHOW)
                    else:
                        card["bot_lbl"].config(text="")
                        if "proxy_badge" in card:
                            card["proxy_badge"].config(text="", bg="#131d33")
                else:
                    box.place_forget()

        panel = getattr(self, '_mine_overlay', None)
        if panel is not None and panel.winfo_ismapped():
            panel.lift()
        login_view = getattr(self, '_login_view', None)
        if login_view is not None and login_view.visible:
            login_view.lift()

    def move_to_monitor(self, monitor_idx):
        if monitor_idx >= len(self.monitors):
            return
        m = self.monitors[monitor_idx]
        self.current_monitor_idx = monitor_idx

        is_zoomed = False
        try:
            is_zoomed = (self.state() == 'zoomed')
        except Exception:
            pass

        if is_zoomed:
            self.state('normal')

        target_w = m["width"] - 40
        target_h = m["height"] - 80
        target_x = m["x"] + 20
        target_y = m["y"] + 40
        self.geometry(f"{target_w}x{target_h}+{target_x}+{target_y}")

        if is_zoomed:
            self.after(100, lambda: self.state('zoomed'))

        self.after(200, self.reposition_all_active_windows)

    def toggle_maximize(self):
        try:
            if self.state() == 'zoomed':
                self.state('normal')
            else:
                self.state('zoomed')
        except Exception:
            pass
        self.after(200, self.reposition_all_active_windows)

    def update_page_nav(self):
        for w in self.pills_frame.winfo_children():
            w.destroy()

        for p in range(1, self.total_pages + 1):
            start = (p - 1) * self.page_size + 1
            end = min(p * self.page_size, len(self.bot_profiles))
            is_active = p == self.current_page
            bg_col = "#2563eb" if is_active else "#18233c"
            fg_col = "#ffffff" if is_active else "#94a3b8"

            btn = tk.Button(
                self.pills_frame,
                text=f"Стр {p} ({start}-{end})",
                command=lambda page=p: self.switch_page(page),
                font=("Segoe UI", 8, "bold" if is_active else "normal"),
                bg=bg_col,
                fg=fg_col,
                activebackground="#1d4ed8",
                relief="flat",
                padx=5,
                pady=1
            )
            btn.pack(side="left", padx=1)

        nav_enabled = not self.is_launching
        self.btn_prev.config(state="normal" if (self.current_page > 1 and nav_enabled) else "disabled")
        self.btn_next.config(state="normal" if (self.current_page < self.total_pages and nav_enabled) else "disabled")

        if self.is_launching:
            self.btn_launch_page.config(state="disabled")
        else:
            self.btn_launch_page.config(state="normal", text=f"🪟 Запустить страницу ботов #{self.current_page}")

        start_idx = (self.current_page - 1) * self.page_size
        page_items = self.bot_profiles[start_idx:start_idx + self.page_size]
        if not self.is_launching:
            self.status_lbl.config(text="")

        for i in range(len(self.slot_cards)):
            card = self.slot_cards[i]
            if i < len(page_items):
                p = page_items[i]
                p_name = p['gaia'] or p['name']
                display_name = p_name
                if self.current_grid_key == "8x2" and len(display_name) > 11:
                    display_name = display_name[:9] + "…"
                card["title_lbl"].config(text=f"#{i+1}")
                cur_mode = self.slot_urls.get(p["key"], "game")
                lbl_suffix = " [2ip]" if cur_mode == "2ip" else ""
                if p["key"] in self._vpn_failed_keys:
                    card["bot_lbl"].config(text=f"• {display_name} — ВПН не подключился", fg="#ef4444")
                    card["placeholder"].config(text=f"Слот #{i+1}\n{p_name}\n\n🛑 ВПН не подключился", fg="#ef4444")
                else:
                    card["bot_lbl"].config(text=f"• {display_name}{lbl_suffix}", fg="#ffffff")
                    card["placeholder"].config(text=f"Слот #{i+1}\n{p_name}", fg="#475569")
                if "btn_ip" in card:
                    if cur_mode == "2ip":
                        card["btn_ip"].config(text="🎮", fg="#34d399")
                    else:
                        card["btn_ip"].config(text="🌐", fg="#a78bfa")
                self.update_slot_proxy_badge(i)
            else:
                card["title_lbl"].config(text=f"#{i+1}")
                card["bot_lbl"].config(text="", fg="#ffffff")
                card["placeholder"].config(text=f"Слот #{i+1}\n(Свободно — нажмите «👥 Аккаунты»)")
                if "proxy_badge" in card:
                    card["proxy_badge"].config(text="", bg="#131d33")

    def prev_page(self):
        if self.is_launching:
            return
        if self.current_page > 1:
            self.switch_page(self.current_page - 1)

    def next_page(self):
        if self.is_launching:
            return
        if self.current_page < self.total_pages:
            self.switch_page(self.current_page + 1)

    def safe_switch_page(self, page):
        """Thread-safe synchronous page switch with UI synchronization."""
        done_evt = threading.Event()
        def _do():
            try:
                self.switch_page(page, force=True)
            finally:
                done_evt.set()
        self.after(0, _do)
        done_evt.wait(timeout=3.0)

    def switch_page(self, new_page, force=False):
        """Seamless page switch: zero flicker, zero background exposure, atomic Z-order transition."""
        if self.is_launching and not force:
            return
        if not force and new_page == self.current_page:
            return
        if new_page < 1 or new_page > self.total_pages:
            return

        old_page = self.current_page
        self.current_page = new_page

        if self.fullscreen_slot is not None:
            self.fullscreen_slot = None
            self.btn_exit_fs.pack_forget()

        self.update_page_nav()

        # Compute exact geometries
        total_w = self.slots_frame.winfo_width()
        total_h = self.slots_frame.winfo_height()
        if total_w < 100 or total_h < 100:
            total_w = max(960, self.winfo_width())
            total_h = max(500, self.winfo_height() - 34)

        cfg = GRID_CONFIGS.get(self.current_grid_key, GRID_CONFIGS["6x2"])
        cols = cfg["cols"]
        rows = cfg["rows"]
        slots_count = cfg["slots"]

        col_w = total_w // cols
        row_h = total_h // rows

        old_start = (old_page - 1) * self.page_size
        old_items = self.bot_profiles[old_start:old_start + self.page_size]

        new_start = (new_page - 1) * self.page_size
        new_items = self.bot_profiles[new_start:new_start + self.page_size]

        container_hwnd = self.slots_frame.winfo_id()
        user32.SendMessageW(container_hwnd, WM_SETREDRAW, 0, 0)

        try:
            for i in range(len(self.slot_cards)):
                card = self.slot_cards[i]
                if i < slots_count:
                    col = i % cols
                    row = i // cols
                    x = col * col_w
                    y = row * row_h
                    w = (total_w - x) if col == cols - 1 else col_w
                    h = (total_h - y) if row == rows - 1 else row_h

                    card["box"].place(x=x, y=y, width=w, height=h)
                    card["btn_fs"].config(text="⛶", bg="#1e293b", fg="#ffffff")

                    game_w = w
                    game_h = h - SLOT_HEADER_HEIGHT

                    old_p = old_items[i] if i < len(old_items) else None
                    old_hwnd = self.profile_hwnds.get(old_p["key"]) if old_p else None
                    if old_hwnd and not user32.IsWindow(old_hwnd):
                        old_hwnd = None

                    new_p = new_items[i] if i < len(new_items) else None
                    new_hwnd = self.profile_hwnds.get(new_p["key"]) if new_p else None
                    if new_hwnd and not user32.IsWindow(new_hwnd):
                        new_hwnd = None

                    if new_hwnd:
                        # 1. Dock window if not docked yet
                        self.dock_hwnd_to_slot(new_hwnd, i, width=game_w, height=game_h)
                        # 2. Seamlessly bring new window to top over slot (zero background flash!)
                        user32.SetWindowPos(
                            new_hwnd, 0,
                            0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS
                        )
                        # 3. Hide old window underneath (no background or placeholder exposure!)
                        if old_hwnd and old_hwnd != new_hwnd:
                            user32.ShowWindow(old_hwnd, SW_HIDE)
                    else:
                        if old_hwnd:
                            user32.ShowWindow(old_hwnd, SW_HIDE)
                else:
                    card["box"].place_forget()
                    old_p = old_items[i] if i < len(old_items) else None
                    old_hwnd = self.profile_hwnds.get(old_p["key"]) if old_p else None
                    if old_hwnd and user32.IsWindow(old_hwnd):
                        user32.ShowWindow(old_hwnd, SW_HIDE)

            # Ensure any other running bot windows not on the new page are hidden
            new_keys = {p["key"] for p in new_items}
            for p in self.bot_profiles:
                if p["key"] not in new_keys:
                    h = self.profile_hwnds.get(p["key"])
                    if h and user32.IsWindow(h) and user32.IsWindowVisible(h):
                        user32.ShowWindow(h, SW_HIDE)

        finally:
            user32.SendMessageW(container_hwnd, WM_SETREDRAW, 1, 0)
            user32.RedrawWindow(container_hwnd, 0, 0, RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW)

    def sync_accounts_ui(self):
        total = len(self.bot_profiles)
        self.btn_accounts.config(text=f"👥 Аккаунты ({total})")
        self.btn_launch_all.config(
            text=f"🚀 Запустить всех ботов ({total})",
            state="normal" if total > 0 else "disabled"
        )
        self.total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        self.current_page = min(self.current_page, self.total_pages)
        self.update_page_nav()
        self.switch_page(self.current_page)
        self.reposition_all_active_windows()

    def launch_account_for_login(self, key, progress=None):
        if self._closing_browsers:
            return
        active = getattr(self, '_login_task', None)
        if active is not None and not active.done():
            return active
        task = self._login_task = Future()
        generation = self.browser_profiles.generation
        keys = [p["key"] for p in self.bot_profiles]
        idx = keys.index(key) if key in keys else 0
        p_name = self.bot_profiles[idx]["name"] if idx < len(self.bot_profiles) else key

        connection = "VPN" if self.use_vpn else "прямого подключения"
        self.status_lbl.config(text=f"🌐 Подготовка {connection} для {p_name}...")
        muted = self.mute_var.get()

        def report(title, detail):
            if progress:
                progress((title, detail))

        def _worker():
            if not task.set_running_or_notify_cancel():
                return
            try:
                context = self.proxy_manager.profile_launch(key) if self.use_vpn else nullcontext()
                with context:
                    _open()
            except Exception as error:
                if not task.done():
                    task.set_exception(error)
                LAUNCH_LOG.exception('Login VPN lifecycle failed')

        def _open():
            try:
                # 1. Get working proxy inside worker thread so UI never freezes
                report('Проверка VPN' if self.use_vpn else 'Открытие браузера', p_name)
                if progress:
                    proxy_arg = self.get_launch_proxy(key, idx,
                        status_callback=lambda text: report('Проверка VPN', text))
                else:
                    proxy_arg = self.get_launch_proxy(key, idx)
                if proxy_arg == "ERROR_ALL_DOWN" or not proxy_arg:
                    raise RuntimeError('VPN не подключён: ни один сервер не прошёл проверку.')

                report('Открытие браузера', f'{p_name} · Подготовка профиля и окна входа…')

                target_url = "https://play.multidonate.ru/c/zakvielchannel/"
                args = [
                    CHROME_EXE,
                    f'--app={target_url}',
                    "--window-size=1050,850",
                    "--disable-breakpad",
                    "--disable-sync",
                    "--no-pings",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-features=TreesInViz,TreeAnimationsInViz,AutofillServerCommunication,CalculateNativeWinOcclusion",
                    "--disable-gpu-watchdog",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding"
                ]
                if muted:
                    args.append("--mute-audio")

                session = self.browser_profiles.launch(CHROME_EXE, key, proxy_arg, args[1:], generation=generation)
                if self.use_vpn:
                    self.proxy_manager.track_profile_process(key, session['identity'])
                self.browser_sessions[key] = session
                present = getattr(self, '_present_login_browser', None)
                if present:
                    self.after(0, lambda: present(key, session, task, generation))
                else:
                    self.after(0, lambda: self.status_lbl.config(text=f"🌐 Открыто окно авторизации: {p_name}. Войдите в Twitch..."))
                    show_controls = getattr(self, '_show_mine_control_after_game_open', None)
                    if show_controls:
                        self.after(0, show_controls)
                    if not task.done():
                        task.set_result(session)
            except Exception as e:
                if not task.done():
                    task.set_exception(e)
                if not self._closing_browsers:
                    try:
                        self.after(0, lambda msg=str(e): self.status_lbl.config(text=f'Не удалось открыть {p_name}: {msg}'))
                        if progress is None and not isinstance(e, LaunchCancelled):
                            self.after(0, lambda msg=str(e): messagebox.showerror("Ошибка", f"Не удалось открыть окно: {msg}"))
                    except (RuntimeError, tk.TclError):
                        pass  # The application can be closing while a worker finishes.

        try:
            threading.Thread(target=_worker, daemon=True).start()
        except Exception as error:
            task.set_exception(error)
        return task

    def _browser_hwnds_for_focus(self):
        handles = set(self.profile_hwnds.values())
        view = getattr(self, '_login_view', None)
        if view is not None:
            handles.update(view.browser_hwnds)
        return handles

    def _present_login_browser(self, key, session, task, generation):
        if task.done():
            return
        def cancelled():
            return (self._shutting_down or self._closing_browsers
                    or generation != self.browser_profiles.generation)
        def failed(error):
            if not task.done():
                task.set_exception(LaunchCancelled() if cancelled() else error)
        if cancelled():
            failed(LaunchCancelled())
            return
        if self._login_view is not None and self._login_view.visible:
            self._finish_login_browser(self._login_view)
        if self._login_view is None:
            self._login_view = EmbeddedLoginView(self.slots_frame, self._finish_login_browser)
        def ready(hwnd):
            if cancelled():
                failed(LaunchCancelled())
                return
            self.profile_hwnds[key] = hwnd
            self._show_mine_control_after_game_open()
            self._login_view.lift()
            self.status_lbl.config(text='Войдите через Twitch во встроенном окне, затем вернитесь к сетке.')
            session['embedded_hwnd'] = hwnd
            if not task.done():
                task.set_result(session)
            self.after(120, lambda: self._browser_focus.clicked(hwnd) if user32.IsWindow(hwnd) else None)
        name = next((p['name'] for p in self.bot_profiles if p['key'] == key), key)
        try:
            self._login_view.show(key, name, session['pid'], hwnd=self.profile_hwnds.get(key),
                                  ready=ready, failed=failed, cancelled=cancelled)
        except (OSError, tk.TclError) as error:
            failed(error)

    def _finish_login_browser(self, view):
        key = view.key
        hwnd, windows = view.release()
        for popup in windows:
            if popup != hwnd and user32.IsWindow(popup):
                user32.PostMessageW(popup, WM_CLOSE, 0, 0)
        if self._shutting_down or self._closing_browsers:
            return
        keys = [profile['key'] for profile in self.bot_profiles]
        if key in keys and hwnd and user32.IsWindow(hwnd):
            self.profile_hwnds[key] = hwnd
            self.slot_urls[key] = 'game'
            self.fullscreen_slot = None
            self.btn_exit_fs.pack_forget()
            self.switch_page(keys.index(key) // self.page_size + 1, force=True)
            self.reposition_all_active_windows()
        else:
            self.profile_hwnds.pop(key, None)
            if hwnd and user32.IsWindow(hwnd):
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


    def open_mine_control(self):
        if self._mine_overlay is None or not self._mine_overlay.winfo_exists():
            self._mine_overlay = MineControlPanel(self.slots_frame, self.browser_profiles,
                account_names=lambda: {p['key']: p.get('gaia') or p.get('name') or p['key']
                                       for p in self.bot_profiles})
        self._mine_overlay.show()

    def _show_mine_control_after_game_open(self):
        if not self._mine_auto_shown and not self._shutting_down:
            self.open_mine_control()
            self._mine_auto_shown = True

    def open_userscript_dialog(self):
        try:
            cfg = self.browser_profiles.userscripts.status()
        except Exception as exc:
            cfg = {"scripts": [], "last_error": str(exc)}
        dlg = tk.Toplevel(self)
        dlg.title("Скрипты из GitHub — все профили")
        dlg.geometry("740x500")
        dlg.minsize(620, 470)
        dlg.configure(bg="#0b1120")
        dlg.transient(self)
        body = tk.Frame(dlg, bg="#0b1120", padx=18, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Скрипты для всех окон", font=("Segoe UI", 13, "bold"),
                 bg="#0b1120", fg="#e2e8f0").pack(anchor="w")
        tk.Label(body, text="Каждый включённый скрипт обновляется при запуске профиля. Чтобы сохранить текущий и установить второй, нажмите «Добавить ещё». Изменение скриптов перезагружает открытые окна.",
                 wraplength=680, justify="left", bg="#0b1120", fg="#94a3b8").pack(anchor="w", pady=12)
        picker = ttk.Combobox(body, state="readonly")
        picker.pack(fill="x", pady=5)
        url_var = tk.StringVar()
        entry = tk.Entry(body, textvariable=url_var, font=("Consolas", 10))
        entry.pack(fill="x", pady=8)
        status = tk.Label(body, wraplength=680, justify="left", bg="#0b1120", fg="#94a3b8")
        status.pack(anchor="w", pady=8)
        state = {"config": cfg, "id": (cfg.get('scripts') or [{}])[0].get('id')}

        def select_script(event=None):
            scripts = state['config'].get('scripts', [])
            index = picker.current() - 1
            selected = scripts[index] if index >= 0 else None
            state['id'] = selected['id'] if selected else None
            url_var.set(selected['script_url'] if selected else (DEFAULT_SCRIPT_URL if not scripts else ''))
            text = 'Новый скрипт: вставьте ссылку на файл GitHub или Raw.'
            if selected:
                text = (f"{selected.get('name', '')} {selected.get('version', '')}\n"
                        f"{'Включён во всех профилях' if selected['installed'] else 'Отключён'}; обновлён: {selected.get('last_updated', '')}")
                if selected.get('last_error'):
                    text += '\nИспользуется сохранённая версия. Ошибка: ' + selected['last_error']
            elif state['config'].get('last_error') and not scripts:
                text += '\n' + state['config']['last_error']
            status.config(text=text, fg="#fb7185" if (selected or state['config']).get('last_error') else "#94a3b8")
            disable_btn.config(state="normal" if selected and selected['installed'] else "disabled")
            install_btn.config(text="Установить / обновить выбранный" if selected else "Добавить скрипт во все окна")

        def display(config):
            state['config'] = config
            selected_id = config.get('selected_id') or state['id']
            scripts = config.get('scripts', [])
            picker.config(values=['＋ Новый скрипт'] + [
                f"{i + 1}. {'●' if item['installed'] else '○'} {item['name']} {item['version']}" for i, item in enumerate(scripts)])
            index = next((i + 1 for i, item in enumerate(scripts) if item['id'] == selected_id), 0)
            picker.current(index)
            select_script()

        def add_another():
            picker.current(0)
            select_script()
            entry.focus_set()

        def perform(disable=False):
            url, script_id = url_var.get().strip(), state['id']
            if disable and script_id is None:
                return
            for button in (install_btn, disable_btn, add_btn):
                button.config(state="disabled")
            picker.config(state="disabled")
            entry.config(state="disabled")
            status.config(text="Отключение выбранного скрипта..." if disable else "Загрузка с GitHub и применение ко всем окнам...", fg="#94a3b8")

            def worker():
                try:
                    config = (self.browser_profiles.disable_userscript(script_id) if disable else
                              self.browser_profiles.install_userscript(url, script_id))
                    callback = lambda: display(config)
                except Exception as exc:
                    callback = lambda msg=str(exc): status.config(text=msg, fg="#fb7185")
                def finish():
                    if dlg.winfo_exists():
                        picker.config(state="readonly")
                        entry.config(state="normal")
                        install_btn.config(state="normal")
                        add_btn.config(state="normal")
                        selected = next((item for item in state['config'].get('scripts', []) if item['id'] == state['id']), {})
                        disable_btn.config(state="normal" if selected.get('installed') else "disabled")
                        callback()
                self.after(0, finish)
            threading.Thread(target=worker, daemon=True).start()

        install_btn = tk.Button(body, command=perform)
        install_btn.pack(fill="x", pady=5)
        add_btn = tk.Button(body, text="＋ Добавить ещё", command=add_another)
        add_btn.pack(fill="x", pady=5)
        disable_btn = tk.Button(body, text="Отключить выбранный скрипт", command=lambda: perform(True))
        disable_btn.pack(fill="x", pady=5)
        tk.Button(body, text="Закрыть", command=dlg.destroy).pack(fill="x", pady=5)
        picker.bind('<<ComboboxSelected>>', select_script)
        display(cfg)

    def userscript_launch_note(self):
        config = self.browser_profiles.userscripts.status()
        if config.get('using_cached'):
            return 'Скрипт: сохранённая версия. Ошибка обновления: ' + config.get('last_error', '')
        return ''

    def open_accounts_dialog(self):
        existing = getattr(self, '_accounts_dialog', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return existing
        dlg = tk.Toplevel(self)
        self._accounts_dialog = dlg
        def forget_dialog(event):
            if event.widget is dlg and getattr(self, '_accounts_dialog', None) is dlg:
                self._accounts_dialog = None
        dlg.bind('<Destroy>', forget_dialog, add='+')
        dlg.title("👥 Управление аккаунтами ботов")
        dlg.geometry("680x560")
        dlg.minsize(560, 420)
        dlg.configure(bg="#0b1120")
        dlg.transient(self)
        dlg.grab_set()

        # Top Header
        hdr = tk.Frame(dlg, bg="#131d33", pady=10, padx=14)
        hdr.pack(fill="x")

        hdr_top = tk.Frame(hdr, bg="#131d33")
        hdr_top.pack(fill="x")

        tk.Label(hdr_top, text="👥 Список аккаунтов мультибота", font=("Segoe UI", 11, "bold"), bg="#131d33", fg="#38bdf8").pack(side="left")
        lbl_count = tk.Label(hdr_top, text=f"Всего: {len(self.bot_profiles)}", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#34d399", padx=8, pady=2)
        lbl_count.pack(side="right")

        tk.Label(hdr, text="Каждый аккаунт изолирован: авторизация в Twitch/Легион сохраняется навсегда.", font=("Segoe UI", 8), bg="#131d33", fg="#94a3b8").pack(anchor="w", pady=(2, 0))

        # Top action bar
        act_bar = tk.Frame(dlg, bg="#0b1120", padx=14, pady=8)
        act_bar.pack(fill="x")

        # Scrollable container
        list_container = tk.Frame(dlg, bg="#0b1120", padx=14, pady=4)
        list_container.pack(fill="both", expand=True)

        canvas = tk.Canvas(list_container, bg="#0b1120", highlightthickness=0)
        scrollbar = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#0b1120")

        scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        def _on_canvas_configure(e):
            canvas.itemconfig(scroll_window, width=e.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        scroll_frame.bind("<Configure>", _on_frame_configure)

        def _on_mousewheel(e):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _clean_dialog():
            if loading.busy:
                dlg.bell()
                return
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _clean_dialog)

        def _begin_login(key, created_name=None):
            def finished(session, error):
                if getattr(self, '_login_task', None) is not None and self._login_task.done():
                    self._login_task = None
                if error is not None:
                    if not isinstance(error, LaunchCancelled):
                        messagebox.showerror('Ошибка входа', f'Не удалось открыть окно:\n{error}', parent=dlg)
                elif session is not None and session.get('embedded_hwnd'):
                    _clean_dialog()
                elif session is not None and created_name:
                    messagebox.showinfo('Аккаунт создан',
                        f'Аккаунт «{created_name}» создан.\nОткрыто окно браузера для входа через Twitch.', parent=dlg)
            name = next((p['name'] for p in self.bot_profiles if p['key'] == key), key)
            return loading.start(lambda progress: self.launch_account_for_login(key, progress=progress),
                'Проверка VPN' if self.use_vpn else 'Открытие браузера', name, finished=finished)

        def _render_list():
            for w in scroll_frame.winfo_children():
                w.destroy()

            lbl_count.config(text=f"Всего: {len(self.bot_profiles)}")

            if not self.bot_profiles:
                empty_card = tk.Frame(scroll_frame, bg="#131d33", padx=20, pady=30)
                empty_card.pack(fill="x", pady=20)
                tk.Label(
                    empty_card,
                    text="Пока нет добавленных аккаунтов.\n\nНажмите зеленую кнопку «➕ Добавить аккаунт» вверху,\nчтобы создать чистый профиль и войти в игру через Twitch.",
                    font=("Segoe UI", 10), bg="#131d33", fg="#94a3b8", justify="center"
                ).pack()
                return

            for idx, p in enumerate(self.bot_profiles):
                key = p["key"]
                name = p["name"]

                row = tk.Frame(scroll_frame, bg="#131d33", padx=10, pady=6)
                row.pack(fill="x", pady=3)

                # Number badge
                tk.Label(row, text=f"#{idx+1:02d}", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#38bdf8", padx=6, pady=2).pack(side="left", padx=(0, 8))

                # Name label
                name_lbl = tk.Label(row, text=name, font=("Segoe UI", 10, "bold"), bg="#131d33", fg="#ffffff", anchor="w")
                name_lbl.pack(side="left", fill="x", expand=True)

                # Delete
                def _delete(k=key, n=name):
                    if messagebox.askyesno("Удаление аккаунта", f"Вы действительно хотите удалить аккаунт «{n}»?\nВсе сохраненные данные входа будут удалены.", parent=dlg):
                        h = self.profile_hwnds.get(k)
                        if h and user32.IsWindow(h):
                            user32.PostMessageW(h, 0x0010, 0, 0)
                        self.profile_hwnds.pop(k, None)
                        self.browser_sessions.pop(k, None)
                        self.proxy_manager.release_profile(k)
                        self.bot_profiles = [x for x in self.bot_profiles if x["key"] != k]
                        save_accounts(self.bot_profiles)
                        self.sync_accounts_ui()
                        _render_list()

                btn_del = tk.Button(row, text="🗑️ Удалить", command=_delete, bg="#334155", fg="#f87171", activebackground="#dc2626", activeforeground="#ffffff", font=("Segoe UI", 8), relief="flat", padx=6, pady=2, cursor="hand2")
                btn_del.pack(side="right", padx=(4, 0))

                # Rename
                def _rename(k=key, cur_n=name):
                    new_n = simpledialog.askstring("Переименовать", "Введите новое имя аккаунта:", initialvalue=cur_n, parent=dlg)
                    if new_n and new_n.strip() and new_n.strip() != cur_n:
                        for x in self.bot_profiles:
                            if x["key"] == k:
                                x["name"] = new_n.strip()
                        save_accounts(self.bot_profiles)
                        self.sync_accounts_ui()
                        _render_list()

                btn_ren = tk.Button(row, text="✏️ Переименовать", command=_rename, bg="#1e293b", fg="#cbd5e1", activebackground="#334155", activeforeground="#ffffff", font=("Segoe UI", 8), relief="flat", padx=6, pady=2, cursor="hand2")
                btn_ren.pack(side="right", padx=(4, 0))

                # Login / open window
                def _login(k=key):
                    _begin_login(k)

                btn_log = tk.Button(row, text="🔑 Войти / Проверить", command=_login, bg="#059669", fg="#ffffff", activebackground="#047857", activeforeground="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=2, cursor="hand2")
                btn_log.pack(side="right", padx=(4, 0))

        def _add_new_account():
            default_n = f"Аккаунт {len(self.bot_profiles) + 1}"
            name = simpledialog.askstring("Новый аккаунт", "Введите имя или никнейм для нового аккаунта:", initialvalue=default_n, parent=dlg)
            if not name or not name.strip():
                return
            name = name.strip()

            existing_keys = {p["key"] for p in self.bot_profiles}
            num = 1
            while f"bot_{num}" in existing_keys:
                num += 1
            new_key = f"bot_{num}"

            # Prepare clean directory
            self.browser_profiles.prepare(new_key)

            new_acc = {
                "key": new_key,
                "name": name,
                "gaia": "",
                "email": "",
                "has_tm": True,
                "is_default": False,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self.bot_profiles.append(new_acc)
            save_accounts(self.bot_profiles)
            self.sync_accounts_ui()
            _render_list()

            # Open clean Chrome window immediately for login!
            _begin_login(new_key, created_name=name)

        btn_add = tk.Button(
            act_bar, text="➕ Добавить аккаунт", command=_add_new_account,
            bg="#059669", fg="#ffffff", activebackground="#047857", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=5, cursor="hand2"
        )
        btn_add.pack(side="left")

        # Bottom close button
        btn_close = tk.Button(
            dlg, text="Готово / Закрыть", command=_clean_dialog,
            bg="#2563eb", fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat", pady=5, cursor="hand2"
        )
        btn_close.pack(fill="x", padx=14, pady=(4, 10))

        _render_list()
        loading = AccountLoadingOverlay(dlg)
        dlg._login_loading = loading
        return dlg


    def get_zoom_factor(self):
        val = self.zoom_var.get().replace("%", "").strip()
        try:
            factor = float(val) / 100.0
            return max(0.5, min(1.5, factor))
        except Exception:
            return 0.85

    def _mark_slot_vpn_failed(self, slot_idx, key):
        reason = self.proxy_manager.vpn_dispatcher.last_error or 'Проверка HTTPS через прокси не прошла.'
        self._show_slot_launch_error(slot_idx, key, reason, vpn=True)

    def _mark_slot_browser_failed(self, slot_idx, key, reason):
        self._show_slot_launch_error(slot_idx, key, reason, vpn=False)

    def _show_slot_launch_error(self, slot_idx, key, reason, vpn=False):
        self._launch_errors[key] = reason
        if vpn:
            self._vpn_failed_keys.add(key)
        else:
            self._vpn_failed_keys.discard(key)
        heading = 'ВПН не подключился' if vpn else 'Ошибка запуска Chrome'
        if slot_idx < len(self.slot_cards):
            card = self.slot_cards[slot_idx]
            start_idx = (self.current_page - 1) * self.page_size
            global_idx = start_idx + slot_idx
            p_name = ""
            if global_idx < len(self.bot_profiles):
                p = self.bot_profiles[global_idx]
                p_name = p['gaia'] or p['name']
            display_name = p_name
            if self.current_grid_key == "8x2" and len(display_name) > 10:
                display_name = display_name[:8] + "…"
            text = f"• {display_name} — {heading}" if display_name else f"• {heading}"
            card["bot_lbl"].config(text=text, fg="#ef4444")
            if "placeholder" in card:
                card["placeholder"].config(
                    text=f"Слот #{slot_idx+1}\n{p_name}\n\n{heading}\n{reason[:220]}",
                    fg="#ef4444"
                )
            self.update_slot_proxy_badge(slot_idx, 'ERROR_ALL_DOWN' if vpn else None)

    def run_launch_single(self, key, slot_idx, proxy_slot_idx=None, target_url=None, generation=None):
        context = self.proxy_manager.profile_launch(key) if self.use_vpn else nullcontext()
        with context:
            return self._run_launch_single(key, slot_idx, proxy_slot_idx, target_url, generation)

    def _run_launch_single(self, key, slot_idx, proxy_slot_idx=None, target_url=None, generation=None):
        if self._closing_browsers:
            return False
        generation = self.browser_profiles.generation if generation is None else generation
        if proxy_slot_idx is None:
            proxy_slot_idx = slot_idx

        existing = set()
        def cb_snap(hwnd, lp):
            existing.add(hwnd)
            return 1
        user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(cb_snap), 0)

        connection = "Выбор рабочего VPN" if self.use_vpn else "Запуск без VPN"
        self.after(0, lambda: self.status_lbl.config(text=f"🔍 Слот #{slot_idx+1}: {connection}..."))
        try:
            proxy_arg = self.get_launch_proxy(key, proxy_slot_idx)
        except LaunchCancelled:
            return False
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda idx=slot_idx, k=key, msg=message: self._mark_slot_browser_failed(idx, k, msg))
            self.after(0, lambda msg=message: self.status_lbl.config(text=f'Слот #{slot_idx+1}: {msg}'))
            return False
        if not proxy_arg or proxy_arg == "ERROR_ALL_DOWN":
            self.after(0, lambda idx=slot_idx, k=key: self._mark_slot_vpn_failed(idx, k))
            self.after(0, lambda: self.status_lbl.config(text=f"🛑 Слот #{slot_idx+1}: ВПН не подключился"))
            return False

        self.after(0, lambda: self.update_slot_proxy_badge(slot_idx, proxy_arg))

        if target_url is None:
            cur_mode = self.slot_urls.get(key, "game")
            target_url = "https://2ip.io" if cur_mode == "2ip" else "https://play.multidonate.ru/c/zakvielchannel/"

        args = [
            CHROME_EXE,
            f"--app={target_url}",
            "--window-size=390,844",
            "--disable-breakpad",
            "--disable-sync",
            "--no-pings",
            "--disable-dev-shm-usage",
            "--disk-cache-size=10485760",
            "--media-cache-size=10485760",
            "--disable-features=TreesInViz,TreeAnimationsInViz,AutofillServerCommunication,CalculateNativeWinOcclusion",
            "--disable-gpu-watchdog",
        ]

        anti_throttle = getattr(self, 'anti_throttle_var', None)
        if anti_throttle is None or anti_throttle.get():
            args.extend([
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ])

        if self.mute_var.get():
            args.append("--mute-audio")

        try:
            self.after(0, lambda: self.status_lbl.config(text=f"Подготовка отдельного профиля: {key}..."))
            session = self.browser_profiles.launch(CHROME_EXE, key, proxy_arg, args[1:], generation=generation)
            if self.use_vpn:
                self.proxy_manager.track_profile_process(key, session['identity'])
            browser_pid = session['pid']
            notice = self.userscript_launch_note()
            if notice:
                LAUNCH_LOG.warning('%s', notice)
                self.after(0, lambda msg=notice: self.status_lbl.config(text=msg))
            session['verified_ip'] = self.proxy_manager._route_ips.get(proxy_arg)
            session['verified_at'] = time.monotonic()
            # Set Chrome bot process to BELOW_NORMAL so 21 bots never lag the mouse or UI
            try:
                hProc = kernel32.OpenProcess(0x0200, False, browser_pid) # PROCESS_SET_INFORMATION
                if hProc:
                    kernel32.SetPriorityClass(hProc, 0x00004000) # BELOW_NORMAL_PRIORITY_CLASS
                    kernel32.CloseHandle(hProc)
            except Exception:
                pass
        except LaunchCancelled:
            return False
        except Exception as e:
            LAUNCH_LOG.exception('Browser launch failed')
            message = getattr(e, 'user_message', str(e))
            self.after(0, lambda idx=slot_idx, k=key, msg=message: self._mark_slot_browser_failed(idx, k, msg))
            self.after(0, lambda msg=message: self.status_lbl.config(text=f"Слот #{slot_idx+1}: {msg}"))
            return False

        new_hwnd = self.profile_hwnds.get(key) if session.get('reused_window') else None
        if new_hwnd and not user32.IsWindow(new_hwnd):
            new_hwnd = None
        for _ in range(45):
            if generation != self.browser_profiles.generation:
                return False
            if new_hwnd:
                break
            time.sleep(0.2)
            def cb_find(hwnd, lp):
                nonlocal new_hwnd
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if (hwnd not in existing or session.get('reused_window')) and user32.IsWindowVisible(hwnd) and owner.value == browser_pid:
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, buf, 256)
                    if buf.value == "Chrome_WidgetWin_1":
                        new_hwnd = hwnd
                        return 0
                return 1
            user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(cb_find), 0)
            if new_hwnd:
                break

        if new_hwnd:
            self._vpn_failed_keys.discard(key)
            self._launch_errors.pop(key, None)
            self.browser_sessions[key] = session
            self.profile_hwnds[key] = new_hwnd
            self.after(0, lambda: self.update_slot_proxy_badge(slot_idx))
            def _dock_if_current():
                start_i = (self.current_page - 1) * self.page_size
                curr_keys = [p["key"] for p in self.bot_profiles[start_i:start_i + self.page_size]]
                if key in curr_keys:
                    target_slot = curr_keys.index(key)
                    self.dock_hwnd_to_slot(new_hwnd, target_slot)
                else:
                    user32.ShowWindow(new_hwnd, SW_HIDE)
                if target_url.startswith('https://play.multidonate.ru/') and user32.IsWindow(new_hwnd):
                    self._show_mine_control_after_game_open()
            self.after(200, _dock_if_current)
        else:
            message = 'Chrome запущен, но его окно не найдено. Повторите запуск; подробности в журнале browser_launch.log.'
            LAUNCH_LOG.error('Chrome process was started, but no visible app window was found')
            self.after(0, lambda: self._mark_slot_browser_failed(slot_idx, key, message))
        return bool(new_hwnd)

    def launch_current_page_thread(self):
        if self.is_launching or self._closing_browsers:
            return

        start_idx = (self.current_page - 1) * self.page_size
        items = self.bot_profiles[start_idx:start_idx + self.page_size]
        if not items:
            messagebox.showinfo("Аккаунты", "На этой странице нет аккаунтов.\nНажмите «👥 Аккаунты», чтобы добавить новых ботов.", parent=self)
            self.open_accounts_dialog()
            return

        self.is_launching = True
        self.btn_launch_page.config(state="disabled", text="⏳ Запуск страницы...")
        self.btn_launch_all.config(state="disabled")
        self.btn_prev.config(state="disabled")
        self.btn_next.config(state="disabled")
        threading.Thread(target=self.run_launch_current_page,
                         kwargs={'generation': self.browser_profiles.generation}, daemon=True).start()

    def run_launch_current_page(self, generation=None):
        generation = self.browser_profiles.generation if generation is None else generation

        vd = self.proxy_manager.vpn_dispatcher
        if self.use_vpn and vd.nodes and not vd.alive_nodes:
            self.after(0, lambda: self.status_lbl.config(text="🔍 Быстрый отсев нерабочих серверов VPN перед запуском..."))
            vd.filter_alive_nodes(
                timeout=4.0,
                max_workers=16,
                progress_callback=lambda m: self.after(0, lambda msg=m: self.status_lbl.config(text=msg))
            )
            self.after(0, self.update_all_slot_proxy_badges)

        start_idx = (self.current_page - 1) * self.page_size
        items = self.bot_profiles[start_idx:start_idx + self.page_size]

        for i, p in enumerate(items):
            if generation != self.browser_profiles.generation:
                break
            key = p["key"]
            p_name = p['gaia'] or p['name']
            bot_global_idx = start_idx + i
            hwnd = self.profile_hwnds.get(key)
            if not hwnd or not user32.IsWindow(hwnd):
                self.after(0, lambda m=f"🚀 Запуск: {p_name} (#{i+1})...": self.status_lbl.config(text=m))
                self.run_launch_single(key, i, proxy_slot_idx=bot_global_idx, generation=generation)
                time.sleep(1.0)
            else:
                self.after(0, lambda h=hwnd, idx=i: [
                    self.dock_hwnd_to_slot(h, idx),
                    user32.ShowWindow(h, SW_SHOW)
                ])
                time.sleep(0.2)

        self.is_launching = False
        def _finish_page():
            self.btn_launch_page.config(state="normal", text=f"🪟 Запустить страницу ботов #{self.current_page}")
            self.btn_launch_all.config(state="normal", text=f"🚀 Запустить всех ботов ({len(self.bot_profiles)})")
            self.btn_prev.config(state="normal" if self.current_page > 1 else "disabled")
            self.btn_next.config(state="normal" if self.current_page < self.total_pages else "disabled")
            started = sum(bool(self.browser_sessions.get(item['key'])) for item in items)
            self.status_lbl.config(text=f"Страница {self.current_page}: проверенных окон {started}/{len(items)}")
            notice = self.userscript_launch_note()
            if notice:
                self.status_lbl.config(text=f"Страница {self.current_page}: проверенных окон {started}/{len(items)}. {notice}")
            failures = [f"{item['name']}: {self._launch_errors[item['key']]}" for item in items if item['key'] in self._launch_errors]
            if failures:
                messagebox.showerror('Не удалось открыть окна', '\n\n'.join(failures), parent=self)

        self.after(0, _finish_page)

    def launch_all_thread(self):
        if self.is_launching or self._closing_browsers:
            return

        total = len(self.bot_profiles)
        if total == 0:
            messagebox.showinfo("Аккаунты", "Список аккаунтов пуст!\n\nНажмите кнопку «👥 Аккаунты» вверху, чтобы добавить ботов.", parent=self)
            self.open_accounts_dialog()
            return

        if not messagebox.askyesno("Подтверждение", f"Запустить всех {total} ботов?\nОни откроются внутри приложения и распределятся по страницам."):
            return
        self.is_launching = True
        self.btn_launch_all.config(state="disabled", text="⏳ Запуск всех ботов...")
        self.btn_launch_page.config(state="disabled")
        self.btn_prev.config(state="disabled")
        self.btn_next.config(state="disabled")
        threading.Thread(target=self.run_launch_all,
                         kwargs={'generation': self.browser_profiles.generation}, daemon=True).start()

    def run_launch_all(self, generation=None):
        generation = self.browser_profiles.generation if generation is None else generation

        total_bots = len(self.bot_profiles)
        vd = self.proxy_manager.vpn_dispatcher
        if self.use_vpn and vd.nodes and not vd.alive_nodes:
            self.after(0, lambda: self.status_lbl.config(text="🔍 Быстрый отсев нерабочих серверов VPN перед запуском..."))
            vd.filter_alive_nodes(
                timeout=4.0,
                max_workers=16,
                progress_callback=lambda m: self.after(0, lambda msg=m: self.status_lbl.config(text=msg))
            )
            self.after(0, self.update_all_slot_proxy_badges)

        for p_idx in range(1, self.total_pages + 1):
            if generation != self.browser_profiles.generation:
                break
            # 1. СНАЧАЛА визуально перелистываем страницу на p_idx
            self.safe_switch_page(p_idx)
            time.sleep(0.4) # Плавная пауза для завершения перерисовки сетки

            start = (p_idx - 1) * self.page_size
            items = self.bot_profiles[start:start + self.page_size]

            # 2. Поочередно запускаем каждого бота на текущей видимой странице
            for i, p in enumerate(items):
                if generation != self.browser_profiles.generation:
                    break
                key = p["key"]
                p_name = p['gaia'] or p['name']
                bot_num = start + i + 1
                bot_global_idx = start + i

                hwnd = self.profile_hwnds.get(key)
                if not hwnd or not user32.IsWindow(hwnd):
                    msg = f"🚀 [{bot_num}/{total_bots}] Запуск: {p_name} (Стр {p_idx}, Слот #{i+1})..."
                    self.after(0, lambda m=msg: self.status_lbl.config(text=m))

                    self.run_launch_single(key, i, proxy_slot_idx=bot_global_idx, generation=generation)
                    time.sleep(1.0) # Даем окну прикрепиться и странице загрузиться
                else:
                    msg = f"⚡ [{bot_num}/{total_bots}] Уже работает: {p_name} (Стр {p_idx}, Слот #{i+1})"
                    self.after(0, lambda m=msg, h=hwnd, idx=i: [
                        self.status_lbl.config(text=m),
                        self.dock_hwnd_to_slot(h, idx),
                        user32.ShowWindow(h, SW_SHOW)
                    ])
                    time.sleep(0.2)

            # 3. Страница полностью заполнена: делаем паузу перед перелистыванием на следующую
            if p_idx < self.total_pages:
                self.after(0, lambda p=p_idx: self.status_lbl.config(
                    text=f"✅ Страница {p} заполнена! Переход на страницу {p + 1}..."
                ))
                time.sleep(1.5)

        # 4. Все страницы запущены! Короткая пауза для осмотра последней страницы и возврат на Стр 1
        self.after(0, lambda: self.status_lbl.config(
            text="✅ Все страницы запущены! Возврат на Страницу 1..."
        ))
        time.sleep(1.5)
        self.safe_switch_page(1)

        # 5. Восстановление кнопок интерфейса и уведомление пользователя
        self.is_launching = False
        def _finish_all():
            self.btn_launch_all.config(state="normal", text=f"🚀 Запустить всех ботов ({total_bots})")
            self.btn_launch_page.config(state="normal", text=f"🪟 Запустить страницу ботов #{self.current_page}")
            self.btn_prev.config(state="normal" if self.current_page > 1 else "disabled")
            self.btn_next.config(state="normal" if self.current_page < self.total_pages else "disabled")
            started = sum(bool(self.browser_sessions.get(item['key'])) for item in self.bot_profiles)
            message = f"Окон с проверенным прокси: {started}/{total_bots}."
            notice = self.userscript_launch_note()
            if notice:
                message += '\n' + notice
            failures = [f"{item['name']}: {self._launch_errors[item['key']]}" for item in self.bot_profiles if item['key'] in self._launch_errors]
            if failures:
                message += '\n\n' + '\n\n'.join(failures)
            self.status_lbl.config(text=message)
            messagebox.showinfo("Результат запуска", message, parent=self)

        self.after(0, _finish_all)

    def on_auto_flip_change(self):
        sec = self.auto_flip_var.get()
        self.auto_flip_sec = sec
        if sec <= 0:
            self.flip_countdown_lbl.config(text="")
            return

        self.auto_flip_remaining = sec
        self.flip_countdown_lbl.config(text=f"⏱️ {self.auto_flip_remaining}с")
        self.tick_auto_flip()

    def tick_auto_flip(self):
        if self.auto_flip_sec <= 0:
            return

        self.auto_flip_remaining -= 1
        if self.auto_flip_remaining <= 0:
            self.auto_flip_remaining = self.auto_flip_sec
            if not self.is_launching:
                next_page = self.current_page + 1
                if next_page > self.total_pages:
                    next_page = 1
                self.switch_page(next_page)

        self.flip_countdown_lbl.config(text=f"⏱️ {self.auto_flip_remaining}с")
        self.after(1000, self.tick_auto_flip)

    def bind_events(self):
        # Bind configure strictly on slots_frame so dragging generates ZERO Python events!
        self.slots_frame.bind("<Configure>", self.on_slots_frame_configure)

        # Allow dragging from empty spaces of header bar, status label, pills frame, countdown
        self.header_bar.bind("<ButtonPress-1>", self.start_window_drag)
        self.status_lbl.bind("<ButtonPress-1>", self.start_window_drag)
        self.pills_frame.bind("<ButtonPress-1>", self.start_window_drag)
        self.flip_countdown_lbl.bind("<ButtonPress-1>", self.start_window_drag)

        # Double click empty header to maximize / restore
        self.header_bar.bind("<Double-Button-1>", lambda e: self.toggle_maximize())
        self.status_lbl.bind("<Double-Button-1>", lambda e: self.toggle_maximize())

        self.bind("<Escape>", lambda e: self.exit_fullscreen_slot())
        self.bind("<Left>", lambda e: self.prev_page())
        self.bind("<Right>", lambda e: self.next_page())
        for num in range(1, 10):
            self.bind(f"<Key-{num}>", lambda e, n=num: self.switch_page(n))

    def start_window_drag(self, event):
        """Allows dragging the entire window directly by clicking empty space on the header bar."""
        if isinstance(event.widget, (tk.Button, ttk.Combobox, tk.Radiobutton, tk.Checkbutton)):
            return

        root_top = user32.GetAncestor(self.winfo_id(), GA_ROOT)
        user32.ReleaseCapture()
        user32.PostMessageW(root_top, WM_SYSCOMMAND, 0xF012, 0) # WM_SYSCOMMAND, SC_MOVE + HTCAPTION

    def on_slots_frame_configure(self, event):
        """Fires ONLY when slots_frame size actually changes (resize/maximize).
        NEVER fires when merely dragging or moving the window across screens!"""
        new_w = event.width
        new_h = event.height

        if new_w == getattr(self, '_last_slots_w', 0) and new_h == getattr(self, '_last_slots_h', 0):
            return

        self._last_slots_w = new_w
        self._last_slots_h = new_h

        if self._resize_timer:
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(80, self.reposition_all_active_windows)

def main():
    configure_launch_logging()
    init_windows_dpi_awareness()

    # Single-instance check: prevent duplicate windows from launching
    mutex = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    app = HighQualityMultiBoxerApp()
    try:
        app.mainloop()
    finally:
        try:
            app.browser_profiles.shutdown()
        finally:
            try:
                app.proxy_manager.close()
            finally:
                kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
