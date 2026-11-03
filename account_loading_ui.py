"""A modal, blurred loading layer owned by the accounts dialog."""
import logging
import os
import queue
import struct
import tkinter as tk


def blurred_snapshot(widget):
    """Capture only this dialog and soften it with GDI; no image dependency."""
    if os.name != 'nt':
        return None
    import ctypes as c
    from ctypes import wintypes as w
    user, gdi = c.WinDLL('user32'), c.WinDLL('gdi32')
    def bind(lib, name, result, *args):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, args
        return function
    ancestor = bind(user, 'GetAncestor', w.HWND, w.HWND, w.UINT)
    client_rect = bind(user, 'GetClientRect', w.BOOL, w.HWND, c.POINTER(w.RECT))
    get_dc = bind(user, 'GetDC', w.HDC, w.HWND)
    release_dc = bind(user, 'ReleaseDC', c.c_int, w.HWND, w.HDC)
    print_window = bind(user, 'PrintWindow', w.BOOL, w.HWND, w.HDC, w.UINT)
    create_dc = bind(gdi, 'CreateCompatibleDC', w.HDC, w.HDC)
    delete_dc = bind(gdi, 'DeleteDC', w.BOOL, w.HDC)
    select = bind(gdi, 'SelectObject', w.HANDLE, w.HDC, w.HANDLE)
    delete_object = bind(gdi, 'DeleteObject', w.BOOL, w.HANDLE)
    create_bitmap = bind(gdi, 'CreateCompatibleBitmap', w.HANDLE, w.HDC, c.c_int, c.c_int)
    create_dib = bind(gdi, 'CreateDIBSection', w.HANDLE, w.HDC, c.c_void_p, w.UINT,
                      c.POINTER(c.c_void_p), w.HANDLE, w.DWORD)
    stretch_mode = bind(gdi, 'SetStretchBltMode', c.c_int, w.HDC, c.c_int)
    brush_origin = bind(gdi, 'SetBrushOrgEx', w.BOOL, w.HDC, c.c_int, c.c_int, c.c_void_p)
    stretch = bind(gdi, 'StretchBlt', w.BOOL, w.HDC, c.c_int, c.c_int, c.c_int, c.c_int,
                   w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.DWORD)
    hwnd = ancestor(widget.winfo_id(), 2)
    bounds = w.RECT()
    if not client_rect(hwnd, c.byref(bounds)):
        return None
    width, height = bounds.right, bounds.bottom
    if width < 2 or height < 2:
        return None
    dc = full = small = bitmap = small_bitmap = old = old_small = None
    try:
        dc = get_dc(hwnd)
        full, small = create_dc(dc), create_dc(dc)
        header = c.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, width, -height,
            1, 32, 0, width * height * 4, 0, 0, 0, 0))
        bits = c.c_void_p()
        bitmap = create_dib(dc, header, 0, c.byref(bits), None, 0)
        sw, sh = max(1, width // 12), max(1, height // 12)
        small_bitmap = create_bitmap(dc, sw, sh)
        if not all((dc, full, small, bitmap, small_bitmap, bits.value)):
            return None
        old, old_small = select(full, bitmap), select(small, small_bitmap)
        if not print_window(hwnd, full, 3):
            return None
        for target in (full, small):
            stretch_mode(target, 4)  # HALFTONE, followed by its required brush origin.
            brush_origin(target, 0, 0, None)
        if not stretch(small, 0, 0, sw, sh, full, 0, 0, width, height, 0x00CC0020):
            return None
        if not stretch(full, 0, 0, width, height, small, 0, 0, sw, sh, 0x00CC0020):
            return None
        bgra = c.string_at(bits, width * height * 4)
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        pixels = bytes(rgb).translate(bytes(int(value * .42) for value in range(256)))
        return tk.PhotoImage(master=widget, data=f'P6\n{width} {height}\n255\n'.encode() + pixels, format='PPM')
    finally:
        for target, previous in ((full, old), (small, old_small)):
            if target:
                if previous:
                    select(target, previous)
                delete_dc(target)
        for image in (bitmap, small_bitmap):
            if image:
                delete_object(image)
        if dc:
            release_dc(hwnd, dc)


class AccountLoadingOverlay(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg='#080d18')
        self.busy = False
        self._task = self._timer = self._snapshot = None
        self._previous_grab = None
        self._states = []
        self._messages = queue.SimpleQueue()
        self._finished = None
        self._phase = 0
        self.canvas = tk.Canvas(self, bg='#080d18', highlightthickness=0, takefocus=True)
        self.canvas.pack(fill='both', expand=True)
        self._background = self.canvas.create_image(0, 0, anchor='nw')
        self.card = tk.Frame(self.canvas, bg='#131d33', padx=28, pady=22,
                             highlightthickness=1, highlightbackground='#334155')
        self._card = self.canvas.create_window(0, 0, window=self.card)
        self.spinner = tk.Canvas(self.card, bg='#131d33', width=42, height=42, highlightthickness=0)
        self.spinner.pack(pady=(0, 12))
        self._arc = self.spinner.create_arc(5, 5, 37, 37, style='arc', width=3, outline='#38bdf8', extent=260)
        self.title = tk.StringVar(self)
        self.detail = tk.StringVar(self)
        tk.Label(self.card, textvariable=self.title, bg='#131d33', fg='#f8fafc',
                 font=('Segoe UI', 13, 'bold'), wraplength=360).pack()
        tk.Label(self.card, textvariable=self.detail, bg='#131d33', fg='#94a3b8',
                 font=('Segoe UI', 10), wraplength=360, justify='center').pack(pady=(8, 0))
        self.canvas.bind('<Configure>', lambda event: self.canvas.coords(self._card, event.width / 2, event.height / 2))
        self.canvas.bind('<Key>', lambda event: 'break')
        self.canvas.bind('<MouseWheel>', lambda event: 'break')

    def start(self, starter, title, detail='', finished=None):
        if self.busy:
            return False
        self.busy = True
        self.title.set(title)
        self.detail.set(detail)
        self._finished = finished
        self.master.update_idletasks()
        try:
            self._snapshot = blurred_snapshot(self.master)
        except (OSError, tk.TclError):
            logging.getLogger('legion.launch').debug('Dialog blur unavailable', exc_info=True)
        self.canvas.itemconfigure(self._background, image=self._snapshot or '')
        def disable(widget):
            if widget is self:
                return
            if 'state' in widget.keys():
                self._states.append((widget, str(widget['state'])))
                widget.configure(state='disabled')
            for child in widget.winfo_children():
                disable(child)
        disable(self.master)
        self._previous_grab = self.grab_current()
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self.grab_set()
        self.canvas.focus_set()
        self.update_idletasks()
        try:
            self._task = starter(self._messages.put)
        except Exception as error:
            self._complete(None, error)
            return False
        if self._task is None:
            self._complete(None, None)
        else:
            self._timer = self.after(60, self._poll)
        return True

    def _poll(self):
        self._timer = None
        while not self._messages.empty():
            title, detail = self._messages.get_nowait()
            self.title.set(title)
            self.detail.set(detail)
        if self._task.done():
            try:
                result = self._task.result()
            except Exception as error:
                self._complete(None, error)
            else:
                self._complete(result, None)
            return
        self._phase = (self._phase + 25) % 360
        self.spinner.itemconfigure(self._arc, start=self._phase)
        self._timer = self.after(60, self._poll)

    def _complete(self, result, error):
        finished, self._finished = self._finished, None
        self.hide()
        if finished:
            finished(result, error)

    def hide(self, restore_grab=True):
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        self.busy = False
        self.place_forget()
        if self.grab_current() is self:
            self.grab_release()
        if restore_grab and self._previous_grab is not None:
            try:
                if self._previous_grab.winfo_exists():
                    self._previous_grab.grab_set()
            except tk.TclError:
                pass
        self._previous_grab = None
        for widget, state in self._states:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        self._states.clear()
        self.canvas.itemconfigure(self._background, image='')
        self._snapshot = self._task = self._finished = None
        self._messages = queue.SimpleQueue()

    def destroy(self):
        self.hide(restore_grab=False)
        super().destroy()
        # Release Tcl variables on the UI thread, including when a worker still
        # holds its progress queue after the dialog has been destroyed.
        self.title = self.detail = None
        self.canvas = self.card = self.spinner = None
        self.master = None
