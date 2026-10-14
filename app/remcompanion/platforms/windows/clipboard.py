"""Windows CF_UNICODETEXT clipboard access; owns and releases native handles."""
from __future__ import annotations

import tkinter as tk

MAX_CLIPBOARD_BYTES = 16 * 1024 * 1024  # UI safety budget, not a language limit.


def write_clipboard_text(widget: tk.Misc, text: str) -> None:
    """Keep the existing delayed Windows Tk clipboard publication behavior."""
    widget.clipboard_clear()
    widget.clipboard_append(text)


def read_clipboard_text(widget: tk.Misc) -> str:
    # Flush delayed Tk clipboard publication before asking Windows for it.
    widget.update_idletasks()
    try:
        return _read_windows_text()
    except (OSError, ValueError) as error:
        raise tk.TclError(str(error)) from error


def _read_windows_text() -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    if not user32.OpenClipboard(None):
        raise OSError("剪贴板暂时被占用，请稍后重试")
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT; may synthesize ANSI.
        if not handle:
            raise ValueError("剪贴板中没有可粘贴的文本")
        size = kernel32.GlobalSize(handle)
        if not 2 <= size <= MAX_CLIPBOARD_BYTES or size % 2:
            raise ValueError("剪贴板文本格式无效或超过 16 MiB")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise OSError("剪贴板文本暂时无法读取，请重试")
        try:
            # Never read past the owned allocation or keep its handle afterward.
            text = ctypes.wstring_at(pointer, size // 2)
            if "\0" not in text:
                raise ValueError("剪贴板文本缺少结束标记")
            return text.split("\0", 1)[0].replace("\r\n", "\n")
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
