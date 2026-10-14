"""可扩展的功能广场入口，不默认启动任何游戏。

THESIS: 先看清有哪些功能，再明确选择进入；不让游戏替代功能目录。
OWN-WORLD: 继承 RemChat 蓝灰/蓝白主题；圆角功能块、柔和投影、原生操作。
STORY: 浏览功能块、阅读说明、按“进入”；每个入口只调用对应功能。
FIRST VIEWPORT: 上方简短说明，下方自适应一至三列功能块；入口在块底部。
FORM: 用户指定的 WorkBuddy 式功能目录，扩展既有界面，不更换视觉系统。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import tkinter as tk
from .scroll_surface import ScrollCanvas

from . import theme
from .dpi import pixels
from .theme_controller import set_color_roles


@dataclass(frozen=True)
class _Feature:
    key: str
    title: str
    category: str
    description: str


# A new feature adds one entry here and its handler in PlazaPanel, not a new tab.
FEATURES = (
    _Feature("gomoku", "五子棋", "休闲 · 本地算法",
             "22×22 棋盘，和雷姆下一盘。默认离线对弈；也可以选择让当前聊天模型参与。"),
    _Feature("garden", "像素花园", "休闲 · 本地存档",
             "走进小农场，耕地、播种、浇水和收获。作物按真实时间成长，离开后也会继续。"),
    _Feature("words", "单词卡片", "学习 · 复习词卡",
             "用本地正式词典复习释义和例句，随手记下自己的理解。笔记与复习进度独立保留。"),
    _Feature("diary", "日记", "记录 · Markdown",
             "写下今天，按日期、标签与心情回看。自动保存，支持预览、导入导出和回收站。"),
    _Feature("process", "项目过程", "工作 · 会话追踪",
             "先接入工具并选择主会话，再回看阶段、输入、Token 与调用。观察展示和会话管理在同一工作区。"),
    _Feature("companion", "主动陪伴", "陪伴 · 提醒设置",
             "按自己的节奏设置休息和陪伴提醒。由你决定是否开启、提醒频率与安静时段。"),
)


@dataclass
class _Card:
    canvas: tk.Canvas
    content: tk.Frame
    window: int
    labels: list[tk.Label]
    action: tk.Canvas
    button: tk.Button
    hovered: bool = False
    focused: bool = False


class PlazaHub(tk.Frame):
    """Native, scrollable catalog. ``on_enter(key)`` owns feature navigation."""

    def __init__(self, parent: tk.Misc, on_enter: Callable[[str], None]) -> None:
        super().__init__(parent, bd=0)
        set_color_roles(self, background="APP_BG")
        self._on_enter = on_enter
        self._columns = 0
        self._layout_job: str | None = None
        self._render_job: str | None = None
        self._focus_job: str | None = None
        self._cards: list[_Card] = []
        self.buttons: dict[str, tk.Button] = {}

        self.canvas = ScrollCanvas(self, highlightthickness=0, bd=0,
                                takefocus=True, yscrollincrement=pixels(self, 24))
        set_color_roles(self.canvas, background="APP_BG")
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                 relief="flat", bd=0)
        set_color_roles(scrollbar, background="LINE", troughcolor="APP_BG",
                        activebackground="ACCENT_DEEP")
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body = tk.Frame(self.canvas, bd=0)
        set_color_roles(self.body, background="APP_BG")
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")

        self.intro = self._label(self.body, "选择一个功能，点击“进入”开始。", role="MUTED")
        self.intro.pack(fill="x", pady=(0, pixels(self, 20)))
        self.grid_body = tk.Frame(self.body, bd=0)
        set_color_roles(self.grid_body, background="APP_BG")
        self.grid_body.pack(fill="x")
        for feature in FEATURES:
            self._build_card(feature)

        self.body.bind("<Configure>", self._update_region, add="+")
        self.canvas.bind("<Configure>", self._schedule_layout, add="+")
        self.bind("<<ThemeChanged>>", self._schedule_layout, add="+")
        self.bind("<Map>", self._schedule_layout, add="+")
        self.bind("<Destroy>", self._destroy, add="+")
        self._bind_scroll(self)
        self._schedule_layout()

    def _label(self, parent: tk.Misc, text: str, *, role: str = "INK",
               size: int = 11, bold: bool = False) -> tk.Label:
        label = tk.Label(parent, text=text, anchor="w", justify="left", bd=0,
                         font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
        set_color_roles(label, background="WHITE" if parent is not self.body else "APP_BG",
                        foreground=role)
        return label

    def _build_card(self, feature: _Feature) -> None:
        surface = tk.Canvas(self.grid_body, width=pixels(self, 280),
                            height=pixels(self, 240), bd=0, highlightthickness=0)
        # We redraw these themed shapes explicitly; the shared controller still
        # owns the canvas background and all native child-widget colors.
        set_color_roles(surface, background="APP_BG")
        content = tk.Frame(surface, bd=0)
        set_color_roles(content, background="WHITE")
        inset = pixels(self, 24)
        window = surface.create_window(inset, inset, window=content, anchor="nw")
        title = self._label(content, feature.title, size=17, bold=True)
        title.pack(fill="x", pady=(0, pixels(self, 8)))
        category = self._label(content, feature.category, role="MUTED", size=10)
        category.pack(fill="x", pady=(0, pixels(self, 16)))
        description = self._label(content, feature.description, role="MUTED")
        description.pack(fill="both", expand=True, pady=(0, pixels(self, 20)))
        action = tk.Canvas(content, width=pixels(self, 100), height=pixels(self, 44),
                           highlightthickness=0, bd=0, cursor="hand2")
        set_color_roles(action, background="WHITE")
        action.pack(anchor="e")
        button = tk.Button(action, text="进入", command=lambda: self._on_enter(feature.key),
                           font=(theme.BODY_FONT, 10, "bold"), bd=0, relief="flat",
                           padx=0, pady=0, cursor="hand2", highlightthickness=0, takefocus=True)
        set_color_roles(button, background="ACCENT_DEEP", foreground="ON_ACCENT",
                        activebackground="ACCENT_HOVER", activeforeground="ON_ACCENT")
        action.create_window(pixels(self, 50), pixels(self, 22), window=button,
                             width=pixels(self, 72), height=pixels(self, 28))
        card = _Card(surface, content, window, [title, category, description], action, button)
        surface.bind("<Configure>", self._schedule_render, add="+")
        content.bind("<Configure>", self._schedule_render, add="+")
        for widget in (surface, content, title, category, description, action, button):
            widget.bind("<Enter>", lambda _, item=card: self._hover(item, True), add="+")
            widget.bind("<Leave>", lambda _, item=card: self._hover(item, False), add="+")
        action.bind("<Button-1>", lambda _: self._invoke(button), add="+")
        button.bind("<Return>", lambda _: self._invoke(button), add="+")
        button.bind("<FocusIn>", lambda _: self._focus_card(card, True), add="+")
        button.bind("<FocusOut>", lambda _: self._focus_card(card, False), add="+")
        self._cards.append(card)
        self.buttons[feature.key] = button

    @staticmethod
    def _invoke(button: tk.Button) -> str:
        button.invoke()
        return "break"

    def _schedule_layout(self, _event: tk.Event | None = None) -> None:
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._layout)

    def _layout(self) -> None:
        self._layout_job = None
        width = max(1, self.canvas.winfo_width())
        self.canvas.itemconfigure(self._window, width=width)
        gap = pixels(self, 16)
        columns = max(1, min(3, (width + gap) // (pixels(self, 280) + gap)))
        if columns != self._columns:
            for column in range(3):
                self.grid_body.columnconfigure(column, weight=int(column < columns),
                                               minsize=0, uniform="features" if column < columns else "")
            self._columns = columns
        card_width = max(1, (width - gap * columns) // columns)
        wrap = max(1, card_width - 2 * pixels(self, 24))
        self.intro.configure(wraplength=max(1, width))
        for index, card in enumerate(self._cards):
            row, column = divmod(index, columns)
            card.canvas.grid(row=row, column=column, sticky="nsew",
                             padx=(0, gap), pady=(0, gap))
            for label in card.labels:
                label.configure(wraplength=wrap)
        self._schedule_render()
        self._update_region()

    def _schedule_render(self, _event: tk.Event | None = None) -> None:
        if self._render_job is None:
            self._render_job = self.after_idle(self._render_cards)

    def _render_cards(self) -> None:
        self._render_job = None
        for card in self._cards:
            width = card.canvas.winfo_width()
            if width <= 1:
                continue
            inset = pixels(self, 24)
            card.canvas.itemconfigure(card.window, width=max(1, width - 2 * inset))
            card.canvas.configure(height=card.content.winfo_reqheight() + 2 * inset + pixels(self, 8))
            height = card.canvas.winfo_height()
            card.canvas.itemconfigure(card.window, height=max(1, height - 2 * inset - pixels(self, 8)))
            self._paint_card(card)
        self._update_region()

    @staticmethod
    def _blend(background: str, foreground: str, amount: float) -> str:
        values = [round(int(background[i:i + 2], 16) * (1 - amount)
                        + int(foreground[i:i + 2], 16) * amount) for i in (1, 3, 5)]
        return "#" + "".join(f"{value:02x}" for value in values)

    @staticmethod
    def _rounded(canvas: tk.Canvas, bounds: tuple[float, float, float, float],
                 radius: int, **options) -> int:
        x1, y1, x2, y2 = bounds
        radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        points = (x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1)
        return canvas.create_polygon(points, smooth=True, splinesteps=24,
                                     tags=("surface-shape", "theme-fixed"), **options)

    def _paint_card(self, card: _Card) -> None:
        colors = theme.palette()
        canvas = card.canvas
        canvas.delete("surface-shape")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        edge, drop, radius = pixels(self, 2), pixels(self, 8), pixels(self, 16)
        depth = 0.14 if card.hovered else 0.09
        self._rounded(canvas, (edge, pixels(self, 6), width - edge, height), radius,
                      fill=self._blend(colors["APP_BG"], colors["INK"], depth / 2), outline="")
        self._rounded(canvas, (edge, pixels(self, 3), width - edge, height - pixels(self, 3)), radius,
                      fill=self._blend(colors["APP_BG"], colors["INK"], depth), outline="")
        self._rounded(canvas, (edge, 0, width - edge, height - drop), radius,
                      fill=colors["WHITE"], outline="")
        canvas.tag_lower("surface-shape", card.window)
        action = card.action
        action.delete("surface-shape")
        fill = "ACCENT_HOVER" if card.hovered else "ACCENT_DEEP"
        set_color_roles(card.button, background=fill)
        self._rounded(action, (pixels(self, 2), pixels(self, 2), pixels(self, 98), pixels(self, 42)),
                      pixels(self, 12), fill=colors[fill],
                      outline=colors["ACCENT"] if card.focused else "", width=pixels(self, 2))
        action.tag_lower("surface-shape")

    def _hover(self, card: _Card, enabled: bool) -> None:
        card.hovered = enabled
        self._paint_card(card)

    def _update_region(self, _event: tk.Event | None = None) -> None:
        height = max(self.body.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))

    def _bind_scroll(self, widget: tk.Misc) -> None:
        # Subtree bindings only: scrolling this catalog never captures chat or a
        # separate dictionary window. Native Tab/Shift-Tab and Space stay intact.
        widget.bind("<MouseWheel>", self._wheel, add="+")
        widget.bind("<Button-4>", lambda _: self._scroll(-1), add="+")
        widget.bind("<Button-5>", lambda _: self._scroll(1), add="+")
        widget.bind("<Prior>", lambda _: self._scroll(-1, "pages"), add="+")
        widget.bind("<Next>", lambda _: self._scroll(1, "pages"), add="+")
        widget.bind("<Home>", lambda _: self._scroll_end(0), add="+")
        widget.bind("<End>", lambda _: self._scroll_end(1), add="+")
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _wheel(self, event: tk.Event) -> str:
        delta = int(event.delta)
        return self._scroll(-max(1, delta // 120) if delta > 0 else max(1, -delta // 120))

    def _scroll(self, amount: int, what: str = "units") -> str:
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(amount, what)
        return "break"

    def _scroll_end(self, fraction: int) -> str:
        self.canvas.yview_moveto(fraction)
        return "break"

    def _focus_card(self, card: _Card, enabled: bool) -> None:
        card.focused = enabled
        self._paint_card(card)
        if not enabled:
            return
        if self._focus_job is not None:
            self.after_cancel(self._focus_job)
        self._focus_job = self.after_idle(lambda: self._ensure_visible(card))

    def _ensure_visible(self, card: _Card) -> None:
        self._focus_job = None
        if not self.winfo_ismapped():
            return
        top = self.canvas.canvasy(0)
        y = card.canvas.winfo_y() + self.grid_body.winfo_y()
        bottom = y + card.canvas.winfo_height()
        viewport = self.canvas.winfo_height()
        target = y if y < top else bottom - viewport if bottom > top + viewport else None
        if target is not None:
            height = max(self.body.winfo_reqheight(), viewport)
            self.canvas.yview_moveto(max(0, target) / height)

    def _destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            for name in ("_layout_job", "_render_job", "_focus_job"):
                identifier = getattr(self, name)
                if identifier is not None:
                    self.after_cancel(identifier)
                    setattr(self, name, None)
