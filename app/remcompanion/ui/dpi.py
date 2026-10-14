"""把设计用的 96-DPI 像素转换为当前 Tk 窗口的物理像素。"""

import tkinter as tk


def display_scale(widget: tk.Misc) -> float:
    return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)


def pixels(widget: tk.Misc, value: int) -> int:
    return round(value * display_scale(widget))
