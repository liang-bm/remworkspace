"""Windows-only external Codex pet window discovery."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from ...window_geometry import WindowRect, WindowCandidate


class CodexPetTracker:
    """通过顶层窗口特征寻找 Codex 的小型透明宠物窗口。"""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    MONITOR_DEFAULTTONEAREST = 2

    def __init__(self) -> None:
        self._last_handle: int | None = None
        self._available = True
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        # 显式声明指针宽度，避免 64 位 Windows 上窗口句柄被默认 int 截断。
        self.user32.EnumWindows.argtypes = [
            ctypes.c_void_p,
            wintypes.LPARAM,
        ]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.MonitorFromWindow.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
        ]
        self.user32.MonitorFromWindow.restype = wintypes.HANDLE
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.user32.SetProcessDPIAware()

    def _window_text(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def _process_path(self, hwnd: int) -> str:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid.value,
        )
        if not process:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(
                process,
                0,
                buffer,
                ctypes.byref(size),
            )
            return buffer.value if ok else ""
        finally:
            self.kernel32.CloseHandle(process)

    def _rect(self, hwnd: int) -> WindowRect | None:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return WindowRect(rect.left, rect.top, rect.right, rect.bottom)

    def _candidate(self, hwnd: int) -> WindowCandidate | None:
        if not self.user32.IsWindowVisible(hwnd) or self.user32.IsIconic(hwnd):
            return None
        rect = self._rect(hwnd)
        if rect is None or rect.width < 120 or rect.height < 120:
            return None
        if rect.width > 1000 or rect.height > 900:
            return None
        process_path = self._process_path(hwnd)
        if "codex" not in process_path.lower():
            return None
        title = self._window_text(hwnd)
        class_name = self._class_name(hwnd)
        ex_style = self.user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)

        score = 100
        if class_name.startswith("Chrome_WidgetWin"):
            score += 25
        if not title:
            score += 28
        if ex_style & self.WS_EX_TOOLWINDOW:
            score += 20
        if ex_style & self.WS_EX_LAYERED:
            score += 15
        if 180 <= rect.width <= 700 and 180 <= rect.height <= 700:
            score += 20
        score -= int((rect.width * rect.height) / 100_000)
        return WindowCandidate(
            handle=hwnd,
            rect=rect,
            title=title,
            class_name=class_name,
            process_path=process_path,
            ex_style=ex_style,
            score=score,
        )

    def find(self) -> WindowCandidate | None:
        candidates: list[WindowCandidate] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )

        @callback_type
        def callback(hwnd, _lparam):
            candidate = self._candidate(int(hwnd))
            if candidate is not None:
                candidates.append(candidate)
            return True

        self.user32.EnumWindows(callback, 0)
        if not candidates:
            self._last_handle = None
            return None
        candidates.sort(
            key=lambda item: (
                item.handle == self._last_handle,
                item.score,
            ),
            reverse=True,
        )
        selected = candidates[0]
        self._last_handle = selected.handle
        return selected

    def work_area_for(self, hwnd: int) -> WindowRect | None:
        if not self._available:
            return None

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitor = self.user32.MonitorFromWindow(
            hwnd,
            self.MONITOR_DEFAULTTONEAREST,
        )
        if not monitor:
            return None
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return WindowRect(
            info.rcWork.left,
            info.rcWork.top,
            info.rcWork.right,
            info.rcWork.bottom,
        )
