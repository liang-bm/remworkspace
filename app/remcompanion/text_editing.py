"""Tk 文本控件统一的复制、剪切、粘贴与右键菜单支持。"""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk

from .clipboard import copy_text, read_clipboard_text


def _selected_text(widget: tk.Text) -> str:
    """读取当前选区；没有选区时返回空字符串。"""

    try:
        return widget.get("sel.first", "sel.last")
    except tk.TclError:
        return ""


def bind_text_editing(
    widget: tk.Text,
    *,
    read_only: bool = False,
) -> None:
    """为 Text 显式绑定 Windows 常用快捷键和中文右键菜单。

    Tk 在不同打包环境、输入法和控件 ``state`` 下的默认 class binding
    行为并不完全一致。这里不用依赖隐式默认值，使主窗口与悬浮窗中的
    Ctrl+C / Ctrl+V / Ctrl+X / Ctrl+A 表现保持一致。
    """

    menu = tk.Menu(widget, tearoff=False)

    def copy_selection(_event: tk.Event | None = None) -> str:
        selected = _selected_text(widget)
        if selected:
            copy_text(widget, selected)
        return "break"

    def select_all(_event: tk.Event | None = None) -> str:
        widget.tag_add("sel", "1.0", "end-1c")
        widget.mark_set("insert", "end-1c")
        widget.see("insert")
        return "break"

    def copy_all() -> None:
        content = widget.get("1.0", "end-1c")
        if content:
            copy_text(widget, content)

    def delete_selection() -> None:
        if _selected_text(widget):
            widget.delete("sel.first", "sel.last")

    def cut_selection(_event: tk.Event | None = None) -> str:
        if read_only:
            return "break"
        selected = _selected_text(widget)
        if selected and copy_text(widget, selected):
            widget.delete("sel.first", "sel.last")
        return "break"

    def paste_clipboard(_event: tk.Event | None = None) -> str:
        if read_only:
            return "break"
        try:
            clipboard = read_clipboard_text(widget)
        except tk.TclError:
            return "break"
        delete_selection()
        widget.insert("insert", clipboard)
        widget.see("insert")
        return "break"

    def show_menu(event: tk.Event) -> str:
        # 右键落在已有选区外时，把插入光标移到鼠标位置；保留已有选区，
        # 这样用户可以先拖选一段回复，再右键复制。
        if not _selected_text(widget):
            widget.mark_set("insert", f"@{event.x},{event.y}")
        # Stable numeric IDs survive translated menu labels.
        menu.entryconfigure(0, state="normal" if _selected_text(widget) else "disabled")
        if not read_only:
            menu.entryconfigure(
                1,
                state="normal" if _selected_text(widget) else "disabled",
            )
            try:
                read_clipboard_text(widget)
            except tk.TclError:
                paste_state = "disabled"
            else:
                paste_state = "normal"
            menu.entryconfigure(2, state=paste_state)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    menu.add_command(label="复制", command=copy_selection)
    if read_only:
        menu.add_command(label="复制全部", command=copy_all)
    if not read_only:
        menu.add_command(label="剪切", command=cut_selection)
        menu.add_command(label="粘贴", command=paste_clipboard)
    menu.add_separator()
    menu.add_command(label="全选", command=select_all)

    for sequence in ("<Control-c>", "<Control-C>"):
        widget.bind(sequence, copy_selection)
    widget.bind("<<Copy>>", copy_selection)
    for sequence in ("<Control-a>", "<Control-A>"):
        widget.bind(sequence, select_all)
    if not read_only:
        widget.bind("<<Cut>>", cut_selection)
        widget.bind("<<Paste>>", paste_clipboard)
        for sequence in ("<Control-x>", "<Control-X>"):
            widget.bind(sequence, cut_selection)
        for sequence in ("<Control-v>", "<Control-V>"):
            widget.bind(sequence, paste_clipboard)
    widget.bind("<Button-3>", show_menu)

    # 保留 Python 引用，避免 Tk 菜单对象随局部作用域结束而提前回收。
    widget._rem_text_menu = menu  # type: ignore[attr-defined]


def bind_copy_label(
    widget: tk.Label,
    text_provider: Callable[[], str] | None = None,
) -> None:
    """让不可选中的聊天 Label 可以通过右键复制整条消息。"""

    menu = tk.Menu(widget, tearoff=False)

    def copy_message() -> None:
        text = (
            text_provider()
            if text_provider is not None
            else str(widget.cget("text"))
        )
        copy_text(widget, text)

    def show_menu(event: tk.Event) -> str:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    menu.add_command(label="复制整条消息", command=copy_message)
    widget.bind("<Button-3>", show_menu)
    widget._rem_text_menu = menu  # type: ignore[attr-defined]
