"""Per-pixel desktop composition, not a near-black transparency colour key.

PNG/Tk frames keep straight RGBA. Only the Win32 DIB boundary uses premultiplied
BGRA. One DC/bitmap is reused; decoded frame bytes are bounded and cached.
"""
from __future__ import annotations

from collections import OrderedDict
import ctypes as c
from ctypes import wintypes as w
from io import BytesIO
import tkinter as tk

from PIL import Image


class _BitmapInfo(c.Structure):
    _fields_ = [('size', w.DWORD), ('width', w.LONG), ('height', w.LONG),
                ('planes', w.WORD), ('bits', w.WORD), ('compression', w.DWORD),
                ('image_size', w.DWORD), ('xppm', w.LONG), ('yppm', w.LONG),
                ('used', w.DWORD), ('important', w.DWORD), ('colour', w.DWORD)]


class _Blend(c.Structure):
    _fields_ = [('operation', w.BYTE), ('flags', w.BYTE),
                ('opacity', w.BYTE), ('alpha_format', w.BYTE)]


def premultiplied_bgra(image: Image.Image) -> bytes:
    """Do not save this representation as PNG or premultiply it a second time."""
    return image.convert('RGBA').convert('RGBa').tobytes('raw', 'BGRa')


class PetAlphaSurface:
    CACHE_BYTES = 32 * 1024 * 1024

    def __init__(self, root: tk.Misc):
        self.root = root
        self._user = c.WinDLL('user32', use_last_error=True)
        self._gdi = c.WinDLL('gdi32', use_last_error=True)
        self._user.GetAncestor.argtypes = [w.HWND, w.UINT]
        self._user.GetAncestor.restype = w.HWND
        self._user.GetWindowLongW.argtypes = [w.HWND, c.c_int]
        self._user.GetWindowLongW.restype = w.LONG
        self._user.SetWindowLongW.argtypes = [w.HWND, c.c_int, w.LONG]
        self._user.SetWindowLongW.restype = w.LONG
        self._user.UpdateLayeredWindow.argtypes = [w.HWND, w.HDC, c.POINTER(w.POINT),
            c.POINTER(w.SIZE), w.HDC, c.POINTER(w.POINT), w.DWORD, c.POINTER(_Blend), w.DWORD]
        self._user.UpdateLayeredWindow.restype = w.BOOL
        self._gdi.CreateCompatibleDC.argtypes = [w.HDC]
        self._gdi.CreateCompatibleDC.restype = w.HDC
        self._gdi.CreateDIBSection.argtypes = [w.HDC, c.POINTER(_BitmapInfo), w.UINT,
            c.POINTER(c.c_void_p), w.HANDLE, w.DWORD]
        self._gdi.CreateDIBSection.restype = w.HBITMAP
        self._gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]
        self._gdi.SelectObject.restype = w.HANDLE
        self._gdi.DeleteObject.argtypes = [w.HANDLE]
        self._gdi.DeleteDC.argtypes = [w.HDC]
        self._dc = self._gdi.CreateCompatibleDC(None)
        if not self._dc:
            raise c.WinError(c.get_last_error())
        self._bitmap = self._previous = None
        self._bits = c.c_void_p()
        self._size = None
        self._cache = OrderedDict()
        self._cached_bytes = 0
        self._frame = None
        self._closed = False
        self.using_per_pixel_alpha = True
        self.presentations = 0
        self.decodes = 0
        self._pending = None
        root.wm_attributes('-transparentcolor', '')
        root.update_idletasks()
        self._hwnd = self._user.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
        self._enable_layer()
        root.bind('<Map>', self._mapped, add=True)
        root.bind('<Destroy>', self._destroyed, add=True)

    def _enable_layer(self):
        style = self._user.GetWindowLongW(self._hwnd, -20)
        # SetLayeredWindowAttributes (used by Tk's transparentcolor) and ULW
        # cannot share a presentation. Microsoft requires resetting this bit.
        self._user.SetWindowLongW(self._hwnd, -20, style & ~0x00080000)
        self._user.SetWindowLongW(self._hwnd, -20, style | 0x00080000)

    def _surface(self, size):
        if size == self._size:
            return
        self._drop_bitmap()
        info = _BitmapInfo()
        info.size = 40
        info.width, info.height = size[0], -size[1]  # top-down, no vertical flip
        info.planes, info.bits = 1, 32
        self._bitmap = self._gdi.CreateDIBSection(self._dc, c.byref(info), 0,
                                                c.byref(self._bits), None, 0)
        if not self._bitmap or not self._bits.value:
            raise c.WinError(c.get_last_error())
        self._previous = self._gdi.SelectObject(self._dc, self._bitmap)
        self._size = size

    def _drop_bitmap(self):
        if self._bitmap:
            self._gdi.SelectObject(self._dc, self._previous)
            self._gdi.DeleteObject(self._bitmap)
        self._bitmap = self._previous = None
        self._size = None

    def present(self, frame):
        if self._closed:
            return
        self._frame = frame
        try:
            key = str(frame)
            prepared = self._cache.get(key)
            if prepared is None:
                png = frame.tk.call(str(frame), 'data', '-format', 'png')
                with Image.open(BytesIO(png)) as image:
                    prepared = image.size, premultiplied_bgra(image)
                self.decodes += 1
                self._cache[key] = prepared
                self._cached_bytes += len(prepared[1])
                while self._cached_bytes > self.CACHE_BYTES and len(self._cache) > 1:
                    _, previous = self._cache.popitem(last=False)
                    self._cached_bytes -= len(previous[1])
            else:
                self._cache.move_to_end(key)
            size, data = prepared
            self._surface(size)
            c.memmove(self._bits, data, len(data))
            bounds, origin, blend = w.SIZE(*size), w.POINT(0, 0), _Blend(0, 0, 255, 1)
            def update():
                return self._user.UpdateLayeredWindow(self._hwnd, None, None, c.byref(bounds),
                    self._dc, c.byref(origin), 0, c.byref(blend), 2)
            if not update():
                self._enable_layer()
                if not update():
                    raise c.WinError(c.get_last_error())
            self.presentations += 1
        except (OSError, ValueError, tk.TclError) as error:
            # A driver/session without layered support remains usable, but do
            # not silently report a successful alpha repair.
            print(f'[pet alpha] Native composition unavailable: {error}')
            self.using_per_pixel_alpha = False
            self.close()
            if self.root.winfo_exists():
                self.root.wm_attributes('-transparentcolor', '#010203')

    def _mapped(self, event):
        if event.widget is self.root and not self._closed and self._pending is None:
            self._pending = self.root.after_idle(self._restore)

    def _restore(self):
        self._pending = None
        if self._frame is not None:
            self.present(self._frame)

    def clear_cache(self):
        self._cache.clear()
        self._cached_bytes = 0

    def _destroyed(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pending is not None:
            self.root.after_cancel(self._pending)
            self._pending = None
        self._drop_bitmap()
        if self._dc:
            self._gdi.DeleteDC(self._dc)
            self._dc = None
        self.clear_cache()
        self._frame = None
