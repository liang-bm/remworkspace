"""用 Tk 的实际换行结果调整只读消息，不按字数猜测行高。"""

from __future__ import annotations

import math
import tkinter as tk
from .font_source import native_font
import tkinter.font as tkfont
from collections.abc import Iterable


def fit_message_text(widget: tk.Text, viewport_width: int) -> None:
    """设置阅读宽度，再测量所有显示行（包括长单词与显式空行）。"""
    fit_message_batch((widget,), viewport_width)


def fit_message_batch(widgets: Iterable[tk.Text], viewport_width: int) -> None:
    """Reflow widths together; one geometry pass instead of one per message."""
    measured = []
    for widget in widgets:
        if not widget.winfo_exists():
            continue
        if hasattr(widget, "fit"):
            widget.fit(viewport_width)
            continue
        font = native_font(widget,font=widget.cget("font"))
        unit_width = max(1, font.measure("0"))
        available = max(100, viewport_width - 64)
        reading_width = min(available, round(viewport_width * 0.82), unit_width * 76)
        padding = 2 * (int(widget.cget("padx")) + int(widget.cget("highlightthickness")))
        widget.configure(width=max(8, (reading_width - padding) // unit_width))
        measured.append((widget, max(1, font.metrics("linespace"))))
    if not measured:
        return
    measured[0][0].update_idletasks()
    for widget, line_height in measured:
        # -update measures all display lines, even outside the visible region.
        pixels = int(widget.tk.call(widget._w, "count", "-update", "-ypixels", "1.0", "end"))
        widget.configure(height=max(1, math.ceil(pixels / line_height)))
