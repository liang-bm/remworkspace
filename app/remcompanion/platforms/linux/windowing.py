"""Linux window boundary; no Win32 imports or native Windows attributes."""
from __future__ import annotations
from .x11_shape import create_pet_silhouette
from .instance import acquire_single_instance


def show_startup_error(log_path) -> None:
    """The Linux launcher reports the traceback; keep the crash-log location visible."""
    import sys
    print(f"RemChat startup failed. Log: {log_path}", file=sys.stderr)


def enable_dpi_awareness() -> None:
    """Tk/WSLg handles desktop scaling; no Windows DPI API applies."""


def configure_tk_scaling(root) -> bool:
    """Align this interpreter's widget pixels with the desktop Xft font DPI."""
    import ctypes as c
    from ctypes.util import find_library
    import math
    library=find_library('X11')
    if not library:return False
    display=None
    try:
        xlib=c.CDLL(library)
        xlib.XOpenDisplay.argtypes=[c.c_char_p];xlib.XOpenDisplay.restype=c.c_void_p
        xlib.XGetDefault.argtypes=[c.c_void_p,c.c_char_p,c.c_char_p];xlib.XGetDefault.restype=c.c_char_p
        xlib.XCloseDisplay.argtypes=[c.c_void_p]
        display=xlib.XOpenDisplay(root.winfo_screen().encode())
        if not display:return False
        value=xlib.XGetDefault(display,b'Xft',b'dpi')
        if not value:return False
        dpi=float(value.decode('ascii'))
        if not math.isfinite(dpi) or dpi<=0:return False
        root.tk.call('tk','scaling',dpi/72.0)
        root._remchat_xft_dpi=dpi
        return True
    except (OSError,ValueError,AttributeError):return False
    finally:
        if display:xlib.XCloseDisplay(display)


def configure_pet_transparency(root, color: str) -> None:
    """Silhouette presentation runs after the actual frame is available."""


def remove_pet_frame(root) -> None:
    """The common pet already uses override-redirect and zero widget borders."""


def configure_overlay_transparency(root, color: str) -> None:
    """Tk X11 has no transparent-colour window attribute."""


def enable_overlay_rounding(root) -> None:
    """Rounding is drawn by common Tk canvas, not DWM."""


def work_area_at(rect, fallback):
    """Preserve the existing screen geometry fallback on X11/WSLg."""
    return fallback


def monitor_rects(root):
    """Read-only RandR query; fall back to Tk's complete virtual desktop."""
    import re
    import subprocess
    from ...window_geometry import WindowRect
    rectangles = []
    try:
        result = subprocess.run(["xrandr", "--listactivemonitors"], capture_output=True,
                                text=True, timeout=2, check=False)
        for line in result.stdout.splitlines()[1:]:
            match = re.search(r"(\d+)(?:/\d+)?x(\d+)(?:/\d+)?([+-]\d+)([+-]\d+)", line)
            if match:
                width, height, left, top = map(int, match.groups())
                rect = WindowRect(left, top, left + width, top + height)
                if width > 0 and height > 0 and rect not in rectangles:
                    rectangles.append(rect)
    except (OSError, subprocess.SubprocessError):
        pass
    if rectangles:
        return tuple(rectangles)
    x, y = root.winfo_vrootx(), root.winfo_vrooty()
    return (WindowRect(x, y, x + root.winfo_vrootwidth(), y + root.winfo_vrootheight()),)


def place_focus_window(window, rect):
    """Place only the owned override-redirect window, including negative x/y."""
    import ctypes as c
    from ctypes.util import find_library
    window.geometry(f"{rect.width}x{rect.height}+0+0")
    window.update_idletasks()
    library = find_library("X11")
    if not library:
        window.geometry(f"{rect.width}x{rect.height}{rect.left:+d}{rect.top:+d}")
        return
    xlib = c.CDLL(library)
    xlib.XOpenDisplay.argtypes = [c.c_char_p]; xlib.XOpenDisplay.restype = c.c_void_p
    xlib.XMoveResizeWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint]
    xlib.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong),
                               c.POINTER(c.POINTER(c.c_ulong)), c.POINTER(c.c_uint)]
    xlib.XFree.argtypes = [c.c_void_p]
    xlib.XFlush.argtypes = [c.c_void_p]
    xlib.XCloseDisplay.argtypes = [c.c_void_p]
    display = xlib.XOpenDisplay(window.winfo_screen().encode())
    if not display:
        raise OSError("无法连接桌面显示服务")
    try:
        root_id, parent_id = c.c_ulong(), c.c_ulong()
        children, count = c.POINTER(c.c_ulong)(), c.c_uint()
        target = window.winfo_id()
        # Tk's override-redirect Toplevel still has an outer wrapper. Moving
        # its client merely offsets/clips content inside a stationary wrapper.
        # wm_frame() is the client on this Tk/XWayland; query the real parent.
        if xlib.XQueryTree(display, target, c.byref(root_id), c.byref(parent_id),
                          c.byref(children), c.byref(count)):
            if parent_id.value and parent_id.value != root_id.value:
                target = parent_id.value
        if children:
            xlib.XFree(children)
        xlib.XMoveResizeWindow(display, target, rect.left, rect.top, rect.width, rect.height)
        xlib.XFlush(display)
    finally:
        xlib.XCloseDisplay(display)
