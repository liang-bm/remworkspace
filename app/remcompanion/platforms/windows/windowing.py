"""Windows-native window styles, monitor geometry, DPI and instance mutex."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import tkinter as tk
from ...window_geometry import WindowRect


def show_startup_error(log_path) -> None:
    """Windowed EXEs still report startup failures when no console is visible."""
    ctypes.windll.user32.MessageBoxW(
        0, f"RemChat 启动失败。\n完整日志：{log_path}", "RemChat", 0x10,
    )


def enable_dpi_awareness() -> None:
    """在创建 Tk 根窗口前启用 DPI 感知，避免高分屏字体被系统二次缩小。"""

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def configure_pet_transparency(root: tk.Misc, color: str) -> None:
    root.wm_attributes("-transparentcolor", color)


def configure_overlay_transparency(root: tk.Misc, color: str) -> None:
    root.wm_attributes("-transparentcolor", color)
    try:
        root.wm_attributes("-toolwindow", True)
    except tk.TclError:
        pass


def create_pet_silhouette(root: tk.Misc):
    """Windows uses its own transparency colour; never load X11 native code."""
    return None


def remove_pet_frame(root: tk.Misc) -> None:
    """清除 Tk/DWM 给透明窗口补上的矩形边框与圆角。"""

    try:
        root.update_idletasks()
        hwnd = root.winfo_id()
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, -16)
        ex_style = user32.GetWindowLongW(hwnd, -20)
        style &= ~(0x00C00000 | 0x00040000 | 0x00800000 | 0x00400000)
        ex_style &= ~(0x00000100 | 0x00000200 | 0x00000001)
        ex_style |= 0x00000080  # WS_EX_TOOLWINDOW：不显示任务栏按钮。
        user32.SetWindowLongW(hwnd, -16, style)
        user32.SetWindowLongW(hwnd, -20, ex_style)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            0x0020 | 0x0002 | 0x0001 | 0x0004,
        )

        # Windows 11：显式禁用 DWM 边框色与圆角。
        border_color = ctypes.c_uint(0xFFFFFFFE)
        corner_preference = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            34,
            ctypes.byref(border_color),
            ctypes.sizeof(border_color),
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            33,
            ctypes.byref(corner_preference),
            ctypes.sizeof(corner_preference),
        )
    except (AttributeError, OSError, tk.TclError):
        # 旧版 Windows 没有对应 DWM 属性时，透明色仍可正常工作。
        pass

def enable_overlay_rounding(root: tk.Misc) -> None:
    try:
        hwnd = root.winfo_id()
        preference = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            33,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
    except (AttributeError, OSError):
        pass

def work_area_at(rect: WindowRect, fallback: WindowRect) -> WindowRect:
    """跟随独立宠物时使用它所在显示器的工作区，含任务栏和负坐标屏幕。"""

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        # windll 的函数对象会共享；用 void* 避免约束到这里的局部结构类型，
        # 否则 pet_tracker 自己的等价 MonitorInfo 会被 ctypes 拒绝。
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        point = wintypes.POINT(rect.left + rect.width // 2, rect.top + rect.height // 2)
        monitor = user32.MonitorFromPoint(point, 2)  # nearest monitor
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return WindowRect(info.rcWork.left, info.rcWork.top,
                              info.rcWork.right, info.rcWork.bottom)
    except (AttributeError, OSError):
        pass
    return fallback


def monitor_rects(root: tk.Misc) -> tuple[WindowRect, ...]:
    """Full display bounds, not taskbar-reduced work areas."""
    rectangles = []
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def collect(_monitor, _dc, bounds, _data):
        rect = bounds.contents
        value = WindowRect(rect.left, rect.top, rect.right, rect.bottom)
        if value.width and value.height and value not in rectangles:
            rectangles.append(value)
        return True

    callback = callback_type(collect)
    user.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, callback_type, wintypes.LPARAM]
    user.EnumDisplayMonitors.restype = wintypes.BOOL
    if not user.EnumDisplayMonitors(None, None, callback, 0) or not rectangles:
        return (WindowRect(0, 0, root.winfo_screenwidth(), root.winfo_screenheight()),)
    return tuple(rectangles)


def place_focus_window(window: tk.Misc, rect: WindowRect) -> None:
    """Exact virtual coordinates; Tk negative offsets mean right anchoring."""
    window.geometry(f"{rect.width}x{rect.height}+0+0")
    window.update_idletasks()
    user = ctypes.windll.user32
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.SetWindowPos.restype = wintypes.BOOL
    hwnd = user.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    if not user.SetWindowPos(hwnd, -1, rect.left, rect.top, rect.width, rect.height,
                             0x0010 | 0x0040 | 0x0200):  # no activate / show / no owner z-order
        raise OSError("无法摆放专注幕布")


def acquire_single_instance(
    name: str = "Local\\RemCompanionUI",
) -> object | None:
    """防止 Codex 兼容入口和独立 EXE 同时打开两套聊天界面。"""

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle
