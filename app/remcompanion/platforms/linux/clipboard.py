"""Linux text selection through the Tk/X11 display connection."""
from __future__ import annotations

import tkinter as tk
import os
import shutil
import subprocess

MAX_CLIPBOARD_BYTES = 16 * 1024 * 1024

WAYLAND_TOOL_MISSING = "WSLg 复制需要 wl-clipboard。请在 Linux 终端运行 sudo apt install wl-clipboard，然后重试。"


def write_clipboard_text(widget, text: str) -> None:
    """Prefer native Wayland ownership; never execute clipboard text as code.

    WSLg 1.0.71 can abort Weston while converting long Tk/XWayland clipboard
    transfers. A system wl-copy supplies one valid UTF-8 stream instead. Do
    not silently retry the unsafe route if that native operation fails.
    """
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    executable = shutil.which("wl-copy") if wayland else None
    if executable:
        try:
            result = subprocess.run(
                [executable, "--type", "text/plain;charset=utf-8"],
                input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                # wl-copy retains a background selection owner. A captured
                # stderr pipe remains open in that owner and makes communicate
                # wait for clipboard replacement, not publication completion.
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise tk.TclError("Linux 剪贴板未响应，请检查 Wayland 会话后重试。") from error
        if result.returncode:
            raise tk.TclError("Linux 剪贴板写入失败，请检查 Wayland 会话后重试。")
        return
    if wayland and os.environ.get("WSL_DISTRO_NAME"):
        raise tk.TclError(WAYLAND_TOOL_MISSING)
    # Ordinary X11, or a non-WSLg Wayland desktop without wl-copy, retains
    # the existing Tk path. No Win32 bridge or Windows executable is loaded.
    widget.clipboard_clear()
    widget.clipboard_append(text)


def read_clipboard_text(widget) -> str:
    # Explicit selection retrieval preserves multi-byte boundaries in long
    # answers. Tk 8.6.13's clipboard-get path corrupts some chunk boundaries
    # on our WSL display, while selection-get returns the original UTF-8 text.
    # STRING is a legacy fallback, never a guessed re-decoding of user content.
    try:
        return widget.selection_get(selection="CLIPBOARD", type="UTF8_STRING")
    except tk.TclError:
        return widget.selection_get(selection="CLIPBOARD", type="STRING")
