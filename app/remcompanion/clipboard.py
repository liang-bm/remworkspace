"""Compatibility route for platform-specific clipboard access."""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox
from .platforms import native_module

_native = native_module("clipboard")
MAX_CLIPBOARD_BYTES = _native.MAX_CLIPBOARD_BYTES
read_clipboard_text = _native.read_clipboard_text
write_clipboard_text = _native.write_clipboard_text


def copy_text(widget: tk.Misc, text: str) -> bool:
    """Copy through the native adapter; failure cannot look like success.

    Callers must only delete a cut selection or show a copied status after
    this returns True. User content goes to the clipboard, never a shell.
    """
    try:
        write_clipboard_text(widget, text)
    except tk.TclError as error:
        parent = widget.winfo_toplevel()
        translate = getattr(parent, "_remchat_translator", lambda value: value)
        messagebox.showerror(translate("复制未完成"), translate(str(error)), parent=parent)
        return False
    return True


def _read_windows_text() -> str:
    """Explicit legacy diagnostic entry; ordinary Linux UI never loads Win32."""
    return native_module("clipboard", platform="windows")._read_windows_text()
