"""X11-only pet silhouette; never changes the Tk connection or Windows path.

Shape is binary clipping, not ARGB compositing. Keep every nonzero alpha pixel,
including disconnected gesture artwork. Checked XCB requests make a stale
window an ordinary failure instead of invoking Xlib's process-wide handler.
"""
from __future__ import annotations

from collections.abc import Callable
import ctypes as c
from ctypes.util import find_library
import io
import tkinter as tk
from weakref import WeakKeyDictionary


class _Cookie(c.Structure):
    _fields_ = [("sequence", c.c_uint)]


class _Rectangle(c.Structure):
    _fields_ = [("x", c.c_int16), ("y", c.c_int16),
                ("width", c.c_uint16), ("height", c.c_uint16)]


class _Version(c.Structure):
    _fields_ = [("response_type", c.c_uint8), ("pad", c.c_uint8),
                ("sequence", c.c_uint16), ("length", c.c_uint32),
                ("major", c.c_uint16), ("minor", c.c_uint16)]


def _opaque_rectangles(alpha: bytes, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Merge identical opaque scanline runs vertically, preserving all islands."""
    if not 0 < width <= 32767 or not 0 < height <= 32767 or len(alpha) != width * height:
        raise ValueError("Image size does not fit an X11 shape")
    result: list[list[int]] = []
    previous: dict[tuple[int, int], int] = {}
    for y in range(height):
        current = {}
        x = 0
        while x < width:
            if not alpha[y * width + x]:
                x += 1
                continue
            start = x
            while x < width and alpha[y * width + x]:
                x += 1
            run = (start, x - start)
            index = previous.get(run)
            if index is None:
                index = len(result)
                result.append([start, y, x - start, 1])
            else:
                result[index][3] += 1
            current[run] = index
        previous = current
    return [tuple(rectangle) for rectangle in result]


class _ShapeConnection:
    """Own only one XCB connection, with no global native error-handler edits."""
    def __init__(self, display: str) -> None:
        self.connection = None
        library, shape_library, libc = (find_library(name) for name in ("xcb", "xcb-shape", "c"))
        if not all((library, shape_library, libc)):
            raise OSError("libxcb/libxcb-shape/libc unavailable")
        self.xcb, self.shape, self.libc = map(c.CDLL, (library, shape_library, libc))
        self.xcb.xcb_connect.argtypes = [c.c_char_p, c.POINTER(c.c_int)]
        self.xcb.xcb_connect.restype = c.c_void_p
        self.xcb.xcb_disconnect.argtypes = [c.c_void_p]
        self.xcb.xcb_disconnect.restype = None
        self.xcb.xcb_connection_has_error.argtypes = [c.c_void_p]
        self.xcb.xcb_connection_has_error.restype = c.c_int
        self.xcb.xcb_request_check.argtypes = [c.c_void_p, _Cookie]
        self.xcb.xcb_request_check.restype = c.c_void_p
        self.libc.free.argtypes = [c.c_void_p]
        self.libc.free.restype = None
        self.shape.xcb_shape_query_version.argtypes = [c.c_void_p]
        self.shape.xcb_shape_query_version.restype = _Cookie
        self.shape.xcb_shape_query_version_reply.argtypes = [c.c_void_p, _Cookie, c.POINTER(c.c_void_p)]
        self.shape.xcb_shape_query_version_reply.restype = c.POINTER(_Version)
        self.shape.xcb_shape_rectangles_checked.argtypes = [
            c.c_void_p, c.c_uint8, c.c_uint8, c.c_uint8, c.c_uint32,
            c.c_int16, c.c_int16, c.c_uint32, c.POINTER(_Rectangle),
        ]
        self.shape.xcb_shape_rectangles_checked.restype = _Cookie
        self.shape.xcb_shape_mask_checked.argtypes = [
            c.c_void_p, c.c_uint8, c.c_uint8, c.c_uint32, c.c_int16, c.c_int16, c.c_uint32,
        ]
        self.shape.xcb_shape_mask_checked.restype = _Cookie
        self.connection = self.xcb.xcb_connect(display.encode(), None)
        try:
            if not self.connection or self.xcb.xcb_connection_has_error(self.connection):
                raise OSError("Cannot connect to this Tk X11 display")
            error = c.c_void_p()
            cookie = self.shape.xcb_shape_query_version(self.connection)
            reply = self.shape.xcb_shape_query_version_reply(self.connection, cookie, c.byref(error))
            try:
                if error or not reply:
                    raise OSError("X11 Shape extension unavailable")
                self.version = (reply.contents.major, reply.contents.minor)
                if self.version < (1, 1):
                    raise OSError("X11 Shape 1.1 required for the input silhouette")
            finally:
                if error:
                    self.libc.free(error)
                if reply:
                    self.libc.free(reply)
        except Exception:
            self.close()
            raise

    def apply(self, window: int, rectangles: c.Array) -> None:
        if not self.connection:
            raise OSError("X11 shape connection is closed")
        # ShapeSet=0, UnSorted=0: vertical merging does not imply YX ordering.
        cookies = [self.shape.xcb_shape_rectangles_checked(
            self.connection, 0, kind, 0, window, 0, 0, len(rectangles), rectangles,
        ) for kind in (0, 2)]  # ShapeBounding, ShapeInput
        self._check(cookies)

    def restore_rectangle(self, window: int) -> None:
        """None bitmap removes client shapes; do not leave the last frame frozen."""
        if not self.connection:
            return
        self._check([self.shape.xcb_shape_mask_checked(
            self.connection, 0, kind, window, 0, 0, 0,
        ) for kind in (0, 2)])

    def _check(self, cookies: list[_Cookie]) -> None:
        failures = []
        for cookie in cookies:
            error = self.xcb.xcb_request_check(self.connection, cookie)
            if error:
                failures.append(c.cast(error, c.POINTER(c.c_uint8))[1])
                self.libc.free(error)
        if failures or self.xcb.xcb_connection_has_error(self.connection):
            raise OSError(f"X11 shape request failed: {failures or 'connection lost'}")

    def close(self) -> None:
        if self.connection:
            self.xcb.xcb_disconnect(self.connection)
            self.connection = None


class PetSilhouette:
    """Coalesce frame presentation, remap repair and resource cleanup on Tk's thread."""
    def __init__(self, root: tk.Tk, *, log: Callable[[str], None] = print) -> None:
        from PIL import Image
        self.root, self.log, self._image_reader = root, log, Image
        self._native = _ShapeConnection(root.winfo_screen())
        self._cache: WeakKeyDictionary = WeakKeyDictionary()
        self._frame: tk.PhotoImage | None = None
        self._pending: str | None = None
        self._last_applied: tuple | None = None
        self._closed = False
        root.bind("<Map>", self._on_map, add="+")
        root.bind("<Destroy>", self._on_destroy, add="+")

    def present(self, frame: tk.PhotoImage) -> None:
        if self._closed:
            return
        self._frame = frame
        if self._pending is None:
            self._pending = self.root.after_idle(self._apply)

    def clear_cache(self) -> None:
        self._cache.clear()
        self._last_applied = None

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self.root and self._frame is not None:
            self._last_applied = None
            self.present(self._frame)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self.close()

    def _rectangles(self, frame: tk.PhotoImage) -> c.Array:
        rectangles = self._cache.get(frame)
        if rectangles is None:
            png = frame.tk.call(str(frame), "data", "-format", "png")
            with self._image_reader.open(io.BytesIO(png)) as image:
                alpha = image.convert("RGBA").getchannel("A")
                values = _opaque_rectangles(alpha.tobytes(), *image.size)
            rectangles = (_Rectangle * len(values))(*(_Rectangle(*value) for value in values))
            self._cache[frame] = rectangles
        return rectangles

    def _apply(self) -> None:
        self._pending = None
        if self._closed or self._frame is None or not self.root.winfo_ismapped():
            return
        try:
            client = self.root.winfo_id()
            wrapper = int(self.root.wm_frame(), 0)
            key = (str(self._frame), client, wrapper)
            if key == self._last_applied:
                return
            rectangles = self._rectangles(self._frame)
            for window in {client, wrapper}:
                self._native.apply(window, rectangles)
            self._last_applied = key
        except (OSError, tk.TclError, ValueError) as error:
            self.log(f"[透明窗口] X11 轮廓暂不可用，尝试恢复矩形并停用本次同步: {error}")
            try:
                for window in {self.root.winfo_id(), int(self.root.wm_frame(), 0)}:
                    self._native.restore_rectangle(window)
            except (OSError, tk.TclError, ValueError) as restore_error:
                self.log(f"[透明窗口] 当前显示已断开或窗口已关闭，无法确认矩形恢复: {restore_error}")
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pending is not None:
            try:
                self.root.after_cancel(self._pending)
            except tk.TclError:
                pass
            self._pending = None
        self._cache.clear()
        self._frame = None
        self._native.close()


def create_pet_silhouette(root: tk.Tk) -> PetSilhouette | None:
    """The platform selector prevents loading this implementation on Windows."""
    if root.tk.call("tk", "windowingsystem") != "x11":
        return None
    try:
        silhouette = PetSilhouette(root)
    except (OSError, ImportError, AttributeError, tk.TclError) as error:
        print(f"[透明窗口] X11 轮廓未启用，保留普通窗口: {error}")
        return None
    print("[透明窗口] 已启用 X11 宠物轮廓与透明区域点击穿透")
    return silhouette
