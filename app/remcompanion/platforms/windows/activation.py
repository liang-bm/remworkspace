"""Windows 单实例激活事件：再次双击时唤醒已有 Rem Companion。"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
import tkinter as tk


EVENT_NAME = "Local\\RemCompanionActivate"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0


def signal_existing_instance() -> bool:
    """向已运行实例发送一次激活信号。"""

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenEventW.argtypes = [
        ctypes.c_uint32,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


class ActivationListener:
    """首个实例持有命名事件，并在 Tk 主线程轮询处理。"""

    def __init__(
        self,
        root: tk.Misc,
        callback: Callable[[], None],
    ) -> None:
        self.root = root
        self.callback = callback
        self.handle: int | None = None
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateEventW.restype = ctypes.c_void_p
        self.handle = kernel32.CreateEventW(
            None,
            False,
            False,
            EVENT_NAME,
        )
        self.root.after(160, self._poll)

    def _poll(self) -> None:
        if self.handle:
            result = ctypes.windll.kernel32.WaitForSingleObject(
                self.handle,
                0,
            )
            if result == WAIT_OBJECT_0:
                self.callback()
        try:
            self.root.after(160, self._poll)
        except tk.TclError:
            pass

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
