"""气泡的排版尺寸：内容交给 Tk 测量，外壳只负责留白和屏幕边界。"""

from __future__ import annotations

from dataclasses import dataclass
from ..platforms import native_module
import tkinter as tk

from ..pet_tracker import WindowRect


@dataclass(frozen=True)
class OverlayMetrics:
    """同一套尺寸用于绘制和测量，避免回复区与输入区互相覆盖。"""

    margin_x: int = 22
    header_height: int = 36
    reply_top: int = 42
    input_height: int = 58
    reply_input_gap: int = 12
    bottom_space: int = 43
    reply_padding: int = 14
    collapsed_width: int = 174
    collapsed_height: int = 68
    screen_margin: int = 16

    @property
    def chrome_height(self) -> int:
        """文本区之外的高度；变化时只在这里调整。"""
        return (
            self.reply_top
            + self.reply_input_gap
            + self.input_height
            + self.bottom_space
        )


def measure_text_height(widget: tk.Text) -> int:
    """取真实字体、换行和段间距的总像素高度，而非用字符数猜行数。

    ``-update`` 强制 Tk 为视口之外的 display line 也更新布局。仅调用
    ``winfo_reqheight`` 或读取文本行数会遗漏中文自动折行及长段落。
    """

    widget.update_idletasks()
    pixels = widget.tk.call(
        widget._w, "count", "-update", "-ypixels", "1.0", "end"
    )
    return max(1, int(pixels))


def fit_overlay_height(
    content_height: int,
    *,
    preferred_height: int,
    work_area_height: int,
    metrics: OverlayMetrics,
) -> int:
    """短回复保留原大小，长回复扩高；屏幕放不下的部分留给滚动。"""

    available = max(1, work_area_height - 2 * metrics.screen_margin)
    # 最大高度取工作区的 72%，为宠物及用户正在工作的应用保留空间。
    # 它是 UI 空间分配而非文本长度经验公式；文字本身由 Tk 精确测量。
    maximum = min(available, max(preferred_height, round(work_area_height * 0.72)))
    desired = max(preferred_height, content_height + metrics.chrome_height)
    return min(maximum, desired)


def work_area_at(rect: WindowRect, fallback: WindowRect) -> WindowRect:
    """Native monitor query is isolated from common layout measurements."""
    return native_module("windowing").work_area_at(rect, fallback)
