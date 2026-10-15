"""Local usage dashboard: recorded facts, not an account-wide billing claim.

THESIS: inspect dated evidence without turning missing history into zero.
OWN-WORLD: RemChat's native blue-white / blue-grey tokens and system type.
STORY: filter a tool/model, compare recorded usage with observed deltas, pick a day.
FIRST VIEWPORT: compact filters, separate figures, then the complete year grid.
FORM: an operating dashboard inside the existing world; no new identity or assets.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import queue
import threading
import time
import tkinter as tk
from .font_source import native_font
from .soft_fields import SoftTtkEntry
from tkinter import font as tkfont, ttk
from urllib.parse import urlsplit
import webbrowser
from PIL import Image, ImageDraw, ImageTk

from . import theme
from .theme_controller import set_color_roles
from .selection_feedback import Tooltip
from .dpi import pixels
from .settings_card import SettingsCard
from .rounded_controls import rounded_box
from ..clipboard import copy_text
from ..i18n import weekday_labels
from .locale_controller import translator_for


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")
FIELD_LABELS = {"input_tokens": "输入", "output_tokens": "输出", "cached_input_tokens": "缓存输入", "total_tokens": "总 Token"}
OFFICIAL_PAGES = {"DeepSeek 用量（用户截图地址，需登录）": "https://platform.deepseek.com/usage",
                  "ChatGPT 页面（用户提供，需登录）": "https://chatgpt.com/u/"}


def _number(value):
    return value if type(value) is int and value >= 0 else None


def format_count(value):
    value = _number(value)
    return f"{value:,}" if value is not None else "—"


def axis_count(value):
    """Compact chart ticks only; tables/tooltips retain exact integers."""
    if value >= 10 ** 15:
        digits = str(value)
        return f"{digits[0]}.{digits[1:3]}e{len(digits)-1}"
    for divisor, unit in ((10 ** 12, "T"), (10 ** 9, "B"), (10 ** 6, "M"), (10 ** 3, "K")):
        if value >= divisor:
            return f"{value / divisor:.1f}{unit}"
    return str(value)


def sum_known(rows, key, scope=None):
    """Sum only reported contributors; an entirely missing field stays None."""
    values = []
    for row in rows:
        source = row.get(scope) if scope else row
        value = _number(source.get(key)) if isinstance(source, dict) else None
        if value is not None:
            values.append(value)
    return sum(values) if values else None


def calendar_position(day):
    first = date(day.year, 1, 1)
    offset = first.weekday() + (day - first).days
    return offset // 7, day.weekday()


def _blend(left, right, fraction):
    values = []
    for offset in (1, 3, 5):
        a, b = int(left[offset:offset+2], 16), int(right[offset:offset+2], 16)
        values.append(round(a + (b-a)*fraction))
    return "#" + "".join(f"{value:02x}" for value in values)


class UsagePanel(tk.Frame):
    """Reads small aggregate queries off Tk; never owns or alters the manager.

    UsagePanel(parent, manager, theme_controller=None, ledger=None) is enough.
    A parent notebook may call refresh() on selection; Map also refreshes a
    stale visible panel. close() is idempotent and is called on Destroy.
    """

    def __init__(self, parent, manager, theme_controller=None, *, ledger=None, today=None, opener=None, **kwargs):
        super().__init__(parent, bg=theme.palette()["WHITE"], **kwargs)
        self.manager, self.theme_controller, self.ledger = manager, theme_controller, ledger
        self._today = today or date.today
        self._opener = opener or webbrowser.open_new_tab
        self.closed = False
        self.busy = False
        self._generation = 0
        self._requests = queue.Queue(maxsize=1)
        self._results = queue.Queue(maxsize=2)
        self._stop = threading.Event()
        self._summary = {}
        self._daily = []
        self._model_rows = []
        self._day_lookup = {}
        self._loaded_at = 0.0
        self._query = None
        self._pending_query = None
        self._remchat_retranslate = self._language_changed
        self._mode = theme.get_mode()
        self._layout_width = 0
        self._cell_bounds = {}
        self._selected = self._today()
        self._hovered = None
        self._tool_values = [""]
        self._model_values = [""]
        self._axis_font = tkfont.Font(root=self, family=theme.BODY_FONT, size=8)
        self._table_font = tkfont.Font(root=self, family=theme.BODY_FONT, size=9)
        self._table_heading_font = tkfont.Font(root=self, family=theme.BODY_FONT, size=9, weight="bold")
        self.status = tk.StringVar(self, "尚未读取本地账本")
        self.period = tk.StringVar(self, "")
        self.coverage = tk.StringVar(self, "未知不是 0；这里只展示本机已记录计量，不代表账号全部历史或账单。")
        self.hover_text = tk.StringVar(self, "")
        self.year = tk.StringVar(self, str(self._today().year))
        self.start = tk.StringVar(self, date(self._today().year, 1, 1).isoformat())
        self.end = tk.StringVar(self, date(self._today().year, 12, 31).isoformat())
        self.view = tk.StringVar(self, "累计")
        self.metric = tk.StringVar(self, "已记录 Token")
        self.trend_metric = tk.StringVar(self, "已记录 Token")
        self.trend_mode = tk.StringVar(self, "按周")
        self._build()
        self._worker_thread = threading.Thread(target=self._worker, name="remchat-usage-read", daemon=True)
        self._worker_thread.start()
        self._after = self.after(80, self._drain)
        self.bind("<Destroy>", self._destroyed, add=True)
        self.bind("<Map>", self._mapped, add=True)

    def _label(self, parent, text="", *, variable=None, size=10, bold=False, role="INK", wrap=True):
        label = tk.Label(parent, text=text, textvariable=variable, font=(theme.BODY_FONT, size, "bold" if bold else "normal"), anchor="w", justify="left")
        set_color_roles(label, background="WHITE", foreground=role)
        if wrap:
            def rewrap(event):
                width=max(pixels(label,80),event.width)
                if label.winfo_pixels(label.cget('wraplength'))!=width:
                    label.configure(wraplength=width)
            label.bind("<Configure>",rewrap)
        return label

    def _button(self, parent, text, command, primary=False):
        button = tk.Button(parent, text=text, command=command, font=(theme.BODY_FONT, 9), relief="flat", bd=0,
                           padx=12, pady=6, cursor="hand2", highlightthickness=1, takefocus=True)
        set_color_roles(button, background="ACCENT_DEEP" if primary else "WHITE", foreground="ON_ACCENT" if primary else "INK",
                        activebackground="ACCENT_HOVER" if primary else "ACCENT_SOFT", activeforeground="ON_ACCENT" if primary else "INK",
                        disabledforeground="MUTED", highlightbackground="LINE", highlightcolor="ACCENT")
        return button

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        from .scroll_surface import ScrollCanvas
        self.scroll = ScrollCanvas(self, bd=0, highlightthickness=0)
        set_color_roles(self.scroll, background="WHITE")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.scroll.yview)
        self.scroll.configure(yscrollcommand=vertical.set)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        self.body = tk.Frame(self.scroll)
        set_color_roles(self.body, background="WHITE")
        self._body_window = self.scroll.create_window(0, 0, window=self.body, anchor="nw")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.bind("<Configure>",self._update_scroll_region)
        self.scroll.bind("<Configure>", self._resize)
        title = self._label(self.body, "用量", size=17, bold=True)
        title.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
        self._label(self.body, "本地账本 · 已记录计量与观察增量分开显示", role="MUTED", size=9).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.filters = tk.Frame(self.body)
        set_color_roles(self.filters, background="WHITE")
        self.filters.grid(row=2, column=0, sticky="ew", padx=18)
        self.filter_groups = []
        def field(label, widget_factory):
            host = tk.Frame(self.filters)
            set_color_roles(host, background="WHITE")
            self._label(host, label, size=9, role="MUTED").pack(anchor="w", pady=(0, 3))
            widget = widget_factory(host)
            widget.pack(fill="x")
            self.filter_groups.append(host)
            widget.bind("<FocusIn>", self._focus_visible, add=True)
            return widget
        self.tool_choice = field("工具", lambda host: ttk.Combobox(host, state="readonly", values=["全部工具"], font=(theme.BODY_FONT, 9), width=15))
        self.tool_choice.current(0)
        self.model_choice = field("模型", lambda host: ttk.Combobox(host, state="readonly", values=["全部模型"], font=(theme.BODY_FONT, 9), width=20))
        self.model_choice.current(0)
        self.year_entry = field("年份", lambda host: ttk.Spinbox(host, from_=1, to=9999, textvariable=self.year, width=7, font=(theme.BODY_FONT, 9), command=self._year_changed))
        self.tool_choice.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.model_choice.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.year_entry.bind("<Return>", lambda _event: self._year_changed())
        self.date_filters = tk.Frame(self.body)
        set_color_roles(self.date_filters, background="WHITE")
        self.date_filters.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 8))
        self.date_widgets = []
        for label, variable in (("起始日", self.start), ("截止日", self.end)):
            host = tk.Frame(self.date_filters)
            set_color_roles(host, background="WHITE")
            self._label(host, label, size=9, role="MUTED").pack(anchor="w")
            entry = SoftTtkEntry(host, textvariable=variable, width=12, font=(theme.BODY_FONT, 9))
            entry.pack(fill="x", pady=(3, 0))
            entry.bind("<Return>", lambda _event: self.refresh())
            entry.bind("<FocusIn>", self._focus_visible)
            self.date_widgets.append(host)
        self.refresh_button = self._button(self.date_filters, "应用 / 刷新", self.refresh, primary=True)
        self.status_label = self._label(self.body, variable=self.status, size=9, role="MUTED")
        self.status_label.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 8))
        self.mode_bar = tk.Frame(self.body)
        set_color_roles(self.mode_bar, background="WHITE")
        self.mode_bar.grid(row=5, column=0, sticky="ew", padx=18, pady=(3, 4))
        self.mode_buttons = []
        for index, text in enumerate(("日", "周", "累计")):
            button = self._button(self.mode_bar, text, lambda value=text: self._set_view(value))
            button.grid(row=0, column=index, padx=(0, 4))
            self.mode_buttons.append(button)
        self._label(self.body, variable=self.period, size=9, role="MUTED").grid(row=6, column=0, sticky="ew", padx=18, pady=(0, 4))
        self.metrics = tk.Frame(self.body)
        set_color_roles(self.metrics, background="WHITE")
        self.metrics.grid(row=7, column=0, sticky="ew", padx=18, pady=(0, 8))
        self.metric_hosts, self.metric_values = [], {}
        self._metric_measure_font = tkfont.Font(root=self, family=theme.BODY_FONT, size=20, weight="bold")
        self._metric_fonts = {}
        self._metric_texts = {key: "—" for key in ("actual", "requests", "observed", "cost")}
        for key, label in (("actual", "已记录 Token"), ("requests", "响应计量"), ("observed", "观察增量 Token"), ("cost", "费用")):
            host = tk.Frame(self.metrics)
            set_color_roles(host, background="WHITE")
            self._label(host, label, size=9, role="MUTED").pack(anchor="w")
            # Figures are not prose: never let Tk wrap their digits vertically.
            value = self._label(host, "—", size=20, bold=True, wrap=False)
            value_font = native_font(self,font=value.cget("font"))
            self._metric_fonts[key] = value_font
            value.configure(font=value_font)
            value.configure(cursor="hand2", takefocus=True)
            Tooltip(value, lambda name=key: (self._metric_texts[name] + "\n点击复制完整数值。"
                                             if self._metric_texts[name] != "—" else "此项尚未提供计量，不是 0。"))
            value.bind("<Button-1>", lambda _event, name=key: self._copy_metric(name))
            value.bind("<Return>", lambda _event, name=key: self._copy_metric(name))
            value.bind("<Control-c>", lambda _event, name=key: self._copy_metric(name))
            value.pack(fill="x", anchor="w", pady=(3, 0))
            self.metric_values[key] = value
            self.metric_hosts.append(host)
        self.coverage_label = self._label(self.body, variable=self.coverage, size=9, role="MUTED")
        self.coverage_label.grid(row=8, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.calendar_bar = tk.Frame(self.body)
        set_color_roles(self.calendar_bar, background="WHITE")
        self.calendar_bar.grid(row=9, column=0, sticky="ew", padx=18, pady=(6, 5))
        self.calendar_title = self._label(self.calendar_bar, "全年 Token 日历", size=11, bold=True, wrap=False)
        self.calendar_title.pack(side="left")
        self.metric_choice = ttk.Combobox(self.calendar_bar, state="readonly", textvariable=self.metric,
                                         values=["已记录 Token", "观察增量（估计日）"], font=(theme.BODY_FONT, 9), width=23)
        self.metric_choice.pack(side="right")
        self.metric_choice.bind("<<ComboboxSelected>>", lambda _event: self._draw_calendar())
        self.calendar_host = tk.Frame(self.body)
        set_color_roles(self.calendar_host, background="WHITE")
        self.calendar_host.grid(row=10, column=0, sticky="ew", padx=18)
        self.calendar_host.grid_columnconfigure(1, weight=1)
        self.weekdays = tk.Canvas(self.calendar_host, width=26, bd=0, highlightthickness=0)
        set_color_roles(self.weekdays, background="WHITE")
        self.weekdays.grid(row=0, column=0, sticky="ns")
        self.calendar = tk.Canvas(self.calendar_host, height=171, bd=0, highlightthickness=0, takefocus=True)
        set_color_roles(self.calendar, background="WHITE", highlightbackground="LINE", highlightcolor="ACCENT")
        self.calendar.grid(row=0, column=1, sticky="ew")
        horizontal = ttk.Scrollbar(self.calendar_host, orient="horizontal", command=self.calendar.xview)
        horizontal.grid(row=1, column=1, sticky="ew")
        self.calendar.configure(xscrollcommand=horizontal.set)
        self.calendar.bind("<Motion>", self._calendar_hover)
        self.calendar.bind("<Leave>", lambda _event: self.hover_text.set(self._day_hint(self._selected) if self._selected else ""))
        self.calendar.bind("<Button-1>", self._calendar_click)
        self.calendar.bind("<Configure>", lambda _event: self._draw_calendar())
        for key, step in (("Left", -7), ("Right", 7), ("Up", -1), ("Down", 1)):
            self.calendar.bind("<"+key+">", lambda _event, value=step: self._move_day(value))
        self.calendar.bind("<Home>", lambda _event: self._calendar_edge(False))
        self.calendar.bind("<End>", lambda _event: self._calendar_edge(True))
        self.calendar.bind("<Return>", lambda _event: self._show_day())
        self.calendar.bind("<FocusIn>", self._focus_visible, add=True)
        self.legend = self._label(self.body, "", size=9, role="MUTED")
        info=self._button(self.calendar_bar,'?',lambda:None)
        info.pack(side='right',padx=6)
        help_tip=Tooltip(info,lambda:'颜色越亮，用量越高。灰色为未采集，空心为已计量 0。\n点击日期查看明细，方向键按日/周移动，Enter 查看。\n观察增量以观察区间末日显示，不等同于真实使用日期。')
        info.configure(command=lambda:(help_tip.close(),help_tip._show()))
        self._label(self.body, variable=self.hover_text, size=9, role="MUTED").grid(row=12, column=0, sticky="ew", padx=18, pady=(0, 10))
        self.trend_section = SettingsCard(self.body, inset=16, shadow=False, background_role="WHITE")
        self.trend_section.grid(row=13, column=0, rowspan=3, sticky="ew", padx=18, pady=(pixels(self, 12), pixels(self, 20)))
        self.trend_bar = tk.Frame(self.trend_section.body)
        set_color_roles(self.trend_bar, background="WHITE")
        self.trend_bar.pack(fill="x", pady=(0, pixels(self, 12)))
        self.trend_title = self._label(self.trend_bar,"用量趋势",size=11,bold=True,wrap=False)
        self.trend_title.grid(row=0,column=0,sticky="w")
        self.trend_metric_choice = ttk.Combobox(self.trend_bar,state="readonly",textvariable=self.trend_metric,
                                               values=["已记录 Token","观察增量（估计日）"],font=(theme.BODY_FONT,9),width=22)
        self.trend_granularity = ttk.Combobox(self.trend_bar,state="readonly",textvariable=self.trend_mode,
                                             values=["按日","按周"],font=(theme.BODY_FONT,9),width=8)
        self.trend_bar.grid_columnconfigure(1,weight=1)
        self.trend_metric_choice.grid(row=0,column=1,sticky="e",padx=6)
        self.trend_granularity.grid(row=0,column=2,sticky="e")
        for widget in (self.trend_metric_choice,self.trend_granularity):
            widget.bind("<<ComboboxSelected>>",lambda _event:self._draw_trend())
            widget.bind("<FocusIn>",self._focus_visible,add=True)
        self.trend = tk.Canvas(self.trend_section.body,height=pixels(self, 185),bd=0,highlightthickness=1)
        set_color_roles(self.trend,background="WHITE",highlightbackground="LINE",highlightcolor="ACCENT")
        self.trend.pack(fill="x")
        self.trend.bind("<Configure>",lambda _event:self._draw_trend())
        self._label(self.trend_section.body,"趋势仅汇总筛选内已知部分。未上报日/周断开，不补 0；观察增量仍是估计日期。",size=9,role="MUTED").pack(fill="x",pady=(pixels(self, 10),0))
        self.model_section = SettingsCard(self.body, inset=16, shadow=False, background_role="WHITE")
        self.model_section.grid(row=16, column=0, rowspan=3, sticky="ew", padx=18, pady=(0, pixels(self, 20)))
        self.model_heading = self._label(self.model_section.body,"模型分组 · 最多 32 个",size=11,bold=True)
        self.model_heading.pack(fill="x",pady=(0,pixels(self, 12)))
        model_host = tk.Frame(self.model_section.body)
        set_color_roles(model_host,background="WHITE")
        model_host.pack(fill="x")
        model_host.grid_columnconfigure(0,weight=1)
        self._configure_table_style()
        self.model_table = ttk.Treeview(model_host,columns=("model","actual","observed","responses"),show="headings",height=6,selectmode="browse",style="Usage.Treeview")
        for key,label,width in (("model","模型",240),("actual","已记录 Token",140),("observed","观察增量 Token",150),("responses","响应计量",110)):
            self.model_table.heading(key,text=label,anchor="w" if key=="model" else "e")
            self.model_table.column(key,width=width,minwidth=90,stretch=False,anchor="w" if key=="model" else "e")
        self.model_table.grid(row=0,column=0,sticky="ew")
        model_vertical = ttk.Scrollbar(model_host,orient="vertical",command=self.model_table.yview)
        model_vertical.grid(row=0,column=1,sticky="ns")
        model_horizontal = ttk.Scrollbar(model_host,orient="horizontal",command=self.model_table.xview)
        model_horizontal.grid(row=1,column=0,sticky="ew")
        self.model_table.configure(yscrollcommand=model_vertical.set,xscrollcommand=model_horizontal.set)
        self.model_table.bind("<FocusIn>",self._focus_visible,add=True)
        self.model_table.bind("<<ThemeChanged>>",lambda _event:self._configure_table_style(),add=True)
        self.model_note = self._label(self.model_section.body,"按名称读取最多 32 个模型；完整模型名可横向滚动，更多模型可用上方筛选查看。",size=9,role="MUTED")
        self.model_note.pack(fill="x",pady=(pixels(self, 10),0))
        self.detail_section = SettingsCard(self.body, inset=16, shadow=False, background_role="WHITE")
        self.detail_section.grid(row=19, column=0, rowspan=2, sticky="ew", padx=18, pady=(0, pixels(self, 12)))
        self.day_heading = self._label(self.detail_section.body, "日期明细", size=11, bold=True)
        self.day_heading.pack(fill="x",pady=(0,pixels(self, 12)))
        detail_host = tk.Frame(self.detail_section.body)
        set_color_roles(detail_host, background="WHITE")
        detail_host.pack(fill="x")
        detail_host.grid_columnconfigure(0, weight=1)
        self.detail = tk.Text(detail_host, height=7, wrap="word", font=(theme.BODY_FONT, 9), bd=0, padx=8, pady=6,
                              highlightthickness=1, takefocus=True, state="disabled")
        set_color_roles(self.detail, background="APP_BG", foreground="INK", highlightbackground="LINE", highlightcolor="ACCENT", selectbackground="ACCENT_DEEP", selectforeground="ON_ACCENT")
        self.detail.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_host, orient="vertical", command=self.detail.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.bind("<FocusIn>", self._focus_visible, add=True)
        self.baseline_label = self._label(self.body, "未分配日期基线：—（不计入日历、不叠加到上方）", size=9, role="MUTED")
        self.baseline_label.grid(row=21, column=0, sticky="ew", padx=18, pady=(12, 6))
        self._label(self.body, "费用/配额未上报时不估价。响应计量不是用户消息数，也不是完整请求总量。", size=9, role="MUTED").grid(row=22, column=0, sticky="ew", padx=18, pady=(0, 8))
        official = tk.Frame(self.body)
        set_color_roles(official, background="WHITE")
        official.grid(row=23, column=0, sticky="ew", padx=18, pady=(0, 3))
        official.grid_columnconfigure(0, weight=1)
        self.official_choice = ttk.Combobox(official, values=list(OFFICIAL_PAGES), state="readonly", font=(theme.BODY_FONT,9), width=30)
        self.official_choice.current(0)
        self.official_choice.grid(row=0, column=0, sticky="ew")
        self.link_button = self._button(official, "打开官方页面", lambda: self._open_official(self.official_choice.get()))
        self.link_button.grid(row=0, column=1, padx=(6,0))
        self._label(self.body, "仅打开用户提供的官方地址，均需登录。未登录验证统计；不会读取 Cookie/账号或自动采集网页用量。", size=9, role="MUTED").grid(row=24, column=0, sticky="ew", padx=18, pady=(0, 16))
        for widget in (self.refresh_button, *self.mode_buttons, self.metric_choice, self.link_button, self.official_choice):
            widget.bind("<FocusIn>", self._focus_visible, add=True)
        self._bind_scroll(self.body)
        self._set_view("累计")

    def _bind_scroll(self, parent):
        for widget in (parent, *parent.winfo_children()):
            if widget is not self.calendar and not isinstance(widget, (tk.Text, ttk.Treeview, ttk.Combobox, ttk.Spinbox)):
                widget.bind("<MouseWheel>", self._wheel, add=True)
            if widget is not parent:
                self._bind_scroll(widget)

    def _wheel(self, event):
        self.scroll.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _focus_visible(self, event):
        if self.closed:
            return
        widget = event.widget
        self.update_idletasks()
        top = widget.winfo_rooty()-self.body.winfo_rooty()
        bottom = top+widget.winfo_height()
        height = max(1, self.body.winfo_height())
        view_top = self.scroll.canvasy(0)
        view_bottom = view_top+self.scroll.winfo_height()
        if top < view_top:
            self.scroll.yview_moveto(top/height)
        elif bottom > view_bottom:
            self.scroll.yview_moveto(max(0, bottom-self.scroll.winfo_height())/height)

    def _resize(self, event):
        width=max(1,event.width)
        if int(self.scroll.itemcget(self._body_window,'width'))!=width:
            self.scroll.itemconfigure(self._body_window,width=width)
        if getattr(self,'_last_resize_width',None)==width:
            return
        self._last_resize_width=width
        self._layout_metrics(event.width)
        wide = event.width >= pixels(self, 660)
        if self._layout_width == (wide, event.width // 40):
            return
        self._layout_width = (wide, event.width // 40)
        columns = 3 if wide else 2
        for index, host in enumerate(self.filter_groups):
            host.grid(row=index//columns, column=index%columns, sticky="ew", padx=(0, 8), pady=(0, 5))
        for column in range(3):
            self.filters.grid_columnconfigure(column, weight=1 if column < columns else 0)
        for index, host in enumerate(self.date_widgets):
            host.grid(row=0, column=index, sticky="ew", padx=(0, 8))
            self.date_filters.grid_columnconfigure(index, weight=1)
        self.refresh_button.grid(row=0 if wide else 1, column=2 if wide else 0,
                                 columnspan=1 if wide else 2, sticky="ew", pady=(0 if wide else 6, 0))
        self.calendar_title.pack_forget()
        self.metric_choice.pack_forget()
        if wide:
            self.calendar_title.pack(side="left")
            self.metric_choice.pack(side="right")
        else:
            self.calendar_title.pack(anchor="w")
            self.metric_choice.pack(fill="x", pady=(4, 0))
        self.trend_metric_choice.grid(row=0 if wide else 1,column=1 if wide else 0,
                                      columnspan=1 if wide else 2,sticky="e" if wide else "ew",padx=6 if wide else 0,pady=0 if wide else (4,0))
        self.trend_granularity.grid(row=0 if wide else 1,column=2,sticky="e",pady=0 if wide else (4,0))
        self.link_button.configure(wraplength=max(100, event.width-70))

    def _update_scroll_region(self,_event=None):
        bounds=self.scroll.bbox('all')
        if bounds!=getattr(self,'_last_scroll_bounds',None):
            self.scroll.configure(scrollregion=bounds)
            self._last_scroll_bounds=bounds

    def _configure_table_style(self):
        # A point-sized font scales with DPI; ttk's default 20px row does not.
        # Prepare only this table's styles in every theme so light/dark changes
        # cannot lose the measured geometry before the widget is remapped.
        style = ttk.Style(self)
        settings = {
            "Usage.Treeview": {"configure": {"font": self._table_font,
                "rowheight": self._table_font.metrics("linespace") + pixels(self, 12)}},
            "Usage.Treeview.Heading": {"configure": {"font": self._table_heading_font}},
        }
        for name in style.theme_names():
            style.theme_settings(name, settings)

    def _layout_metrics(self, width):
        # Font measurements already include Tk/DPI scaling. Fixed pixel cutoffs
        # previously put four enlarged totals into columns narrower than a digit.
        available = max(1, width - 36)
        needed = max(self._metric_measure_font.measure(text) + 12
                     for text in self._metric_texts.values())
        columns = next((count for count in (4, 2, 1) if available / count >= needed), 1)
        self._metric_columns = columns
        cell_width = max(1, int(available / columns) - 12)
        for index, (key, label) in enumerate(self.metric_values.items()):
            self.metric_hosts[index].grid(row=index//columns, column=index%columns,
                                          sticky="ew", padx=(0, 8), pady=(0, 7))
            numeric_font = self._metric_fonts[key]
            text = self._metric_texts[key]
            label.configure(text=text)
            numeric_font.configure(size=20)
            for size in range(20, 10, -1):
                numeric_font.configure(size=size)
                if numeric_font.measure(text) <= cell_width:
                    break
            if numeric_font.measure(text) > cell_width:
                # Extremely large counts still have a legible single-line
                # representation; hover/click exposes/copies the exact integer.
                digits = text.replace(",", "")
                if digits.isdigit() and len(digits) > 4:
                    label.configure(text=f"{digits[0]}.{digits[1:4]} ×10^{len(digits)-1}")
        for column in range(4):
            self.metrics.grid_columnconfigure(column, weight=1 if column < columns else 0,
                                              minsize=0, uniform="metric" if column < columns else "")

    def _copy_metric(self, key):
        text = self._metric_texts[key]
        if text != "—" and copy_text(self, text):
            self.status.set("完整数值已复制。缩略显示不改变统计数据。")
        return "break"

    def _year_changed(self):
        try:
            year = int(self.year.get())
            first, last = date(year, 1, 1), date(year, 12, 31)
        except (ValueError, OverflowError):
            self._error("年份应为 1–9999。修改后点击刷新。")
            return
        self.start.set(first.isoformat())
        self.end.set(last.isoformat())
        self.refresh()

    def _filter_value(self, choice, values):
        index = choice.current()
        return values[index] if 0 <= index < len(values) else ""

    def refresh(self):
        if self.closed:
            return
        try:
            year = int(self.year.get())
            first, last = date(year, 1, 1), date(year, 12, 31)
            start, end = date.fromisoformat(self.start.get()), date.fromisoformat(self.end.get())
            if not first <= start <= end <= last:
                raise ValueError("起止日必须位于所选年份内，且起始日不晚于截止日。")
        except (ValueError, OverflowError) as error:
            self._generation += 1  # A pending result cannot erase this error.
            self.busy = False
            self.refresh_button.configure(state="normal")
            self._error("日期筛选无效："+str(error)+" 请使用 YYYY-MM-DD。")
            return
        query = {"year":year, "start":start, "end":end,
                 "tool_id":self._filter_value(self.tool_choice,self._tool_values),
                 "model":self._filter_value(self.model_choice,self._model_values)}
        self._generation += 1
        self._pending_query = query
        self.busy = True
        self.refresh_button.configure(state="disabled")
        self.status.set("正在读取本地汇总… 不联网、不调用模型。")
        self._replace_queue(self._requests, (self._generation, query))

    @staticmethod
    def _replace_queue(target, value):
        try:
            target.put_nowait(value)
        except queue.Full:
            try:
                target.get_nowait()
            except queue.Empty:
                pass
            target.put_nowait(value)

    def _worker(self):
        while not self._stop.is_set():
            try:
                request = self._requests.get(timeout=.2)
            except queue.Empty:
                continue
            if request is None:
                return
            generation, query = request
            try:
                backend = self.ledger or self.manager
                summary = backend.summary if self.ledger is not None else backend.usage_summary
                calendar = backend.calendar if self.ledger is not None else backend.usage_calendar
                filters = {"tool_id":query["tool_id"], "model":query["model"]}
                # Calendar supplies the selected year's facts. Summary is only
                # used for bounded choices/undated history, not period totals.
                result = {"summary":summary(days=30, **filters), "daily":calendar(query["year"], **filters), "query":query}
                available = result["summary"].get("models",[])
                models = sorted({value for value in available if isinstance(value,str) and len(value)<=200}) if isinstance(available,list) else []
                if query["model"]:
                    models = [query["model"]]
                grouped = []
                for model_name in models[:32]:
                    if self._stop.is_set():
                        return
                    if generation != self._generation:
                        break
                    daily = result["daily"] if query["model"]==model_name else calendar(query["year"],tool_id=query["tool_id"],model=model_name)
                    ranged = [row for row in daily if query["start"].isoformat() <= row["date"] <= query["end"].isoformat()]
                    grouped.append({"model":model_name,"actual":sum_known(ranged,"total_tokens","actual"),
                                    "observed":sum_known(ranged,"total_tokens","observed"),"responses":sum_known(ranged,"request_count")})
                result["model_rows"],result["model_count"] = grouped,len(models)
                payload = (generation, result, None)
            except Exception as error:
                payload = (generation, None, str(error)[:500])
            if not self._stop.is_set():
                self._replace_queue(self._results, payload)

    def _drain(self):
        if self.closed:
            return
        try:
            while True:
                generation, data, error = self._results.get_nowait()
                if generation != self._generation:
                    continue
                self.busy = False
                self.refresh_button.configure(state="normal")
                if error is not None:
                    self._error("本地账本读取失败："+error+"。可点击刷新重试。")
                else:
                    self._render(data)
        except queue.Empty:
            pass
        if theme.get_mode() != self._mode:
            self._mode = theme.get_mode()
            self._draw_calendar()
            self._draw_trend()
            self._set_view(self.view.get())
        self._after = self.after(80, self._drain)

    def _error(self, message):
        self.status.set(message+(" 下方保留上次成功读取的数据。" if self._daily else ""))
        set_color_roles(self.status_label, foreground="ERROR")

    def _render(self, data):
        if not isinstance(data.get("summary"), dict) or not isinstance(data.get("daily"), list):
            self._error("本地汇总格式不可用。点击刷新重试。")
            return
        self._summary = data["summary"]
        self._daily = [row for row in data["daily"] if isinstance(row, dict) and isinstance(row.get("date"), str)]
        self._model_rows = data.get("model_rows",[])
        self._day_lookup = {row["date"]:row for row in self._daily}
        self._query = data["query"]
        self._loaded_at = time.monotonic()
        for choice, key, caption in ((self.tool_choice, "tools", "全部工具"), (self.model_choice, "models", "全部模型")):
            selected = self._query["tool_id" if key == "tools" else "model"]
            old = self._tool_values if key == "tools" else self._model_values
            options = sorted(set(old[1:]) | {value for value in self._summary.get(key, []) if isinstance(value,str) and len(value)<=200})
            values = [""]+options
            if key == "tools":
                self._tool_values = values
            else:
                self._model_values = values
            choice.configure(values=[caption]+options)
            choice.current(values.index(selected) if selected in values else 0)
        self._selected = max(self._query["start"], min(self._selected, self._query["end"]))
        self.calendar_title.configure(text=f"Token 活动 · {self._query['year']}")
        set_color_roles(self.status_label, foreground="MUTED")
        self.status.set("本地汇总已更新。只覆盖已记录来源；空白历史不补造。")
        baseline = self._summary.get("baseline") or {}
        tokens = baseline.get("tokens") or {}
        self.baseline_label.configure(text="未分配日期基线："+format_count(tokens.get("total_tokens"))+" Token · "+format_count(baseline.get("measurements"))+" 条计量\n工具/模型筛选生效；日期筛选不适用。不计入日历，也不叠加到上方数字。")
        self._set_view(self.view.get())
        self._draw_calendar()
        self._draw_trend()
        self._render_models(data.get("model_count",0))
        self._show_day()

    def _render_models(self, count):
        selected = {self.model_table.set(item,"model") for item in self.model_table.selection()}
        horizontal, vertical = self.model_table.xview()[0], self.model_table.yview()[0]
        self.model_table.delete(*self.model_table.get_children())
        padding = pixels(self, 24)
        self._configure_table_style()
        for key,field,base in (("model","model",220),("actual","actual",140),
                               ("observed","observed",150),("responses","responses",110)):
            values = [row[field] if key=="model" else format_count(row[field]) for row in self._model_rows]
            heading = self.model_table.heading(key,"text")
            width = max([pixels(self,base),self._table_heading_font.measure(heading)+padding]
                        + [self._table_font.measure(value)+padding for value in values])
            self.model_table.column(key,width=width,minwidth=pixels(self,72))
        for index,row in enumerate(self._model_rows):
            item = self.model_table.insert("","end",iid="usage-model-"+str(index),values=(row["model"],format_count(row["actual"]),format_count(row["observed"]),format_count(row["responses"])))
            if row["model"] in selected:
                self.model_table.selection_add(item)
        self.model_table.xview_moveto(horizontal)
        self.model_table.yview_moveto(vertical)
        self.model_heading.configure(text=f"模型分组 · {len(self._model_rows)}/{count} 个可选模型")
        self.model_note.configure(text=("仅列前 32 个模型 · 可用模型筛选" if count > 32 else "") + "  —：未上报")

    def _draw_trend(self):
        if self.closed:
            return
        self.trend.delete("all")
        self._trend_series = []
        if not self._query:
            return
        scope = "observed" if self.trend_metric.get().startswith("观察") else "actual"
        weekly = self.trend_mode.get()=="按周"
        groups = {}
        for row in self._daily:
            if not self._query["start"].isoformat() <= row["date"] <= self._query["end"].isoformat():
                continue
            day = date.fromisoformat(row["date"])
            bucket = day-timedelta(days=day.weekday()) if weekly else day
            groups.setdefault(bucket,[]).append(row)
        self._trend_series = [(day,sum_known(rows,"total_tokens",scope)) for day,rows in sorted(groups.items())]
        colors = theme.palette()
        values = [value for _day,value in self._trend_series if value is not None]
        peak = max(values,default=0)
        ticks = sorted({0,peak//2,peak})
        font = self._axis_font
        line = font.metrics("linespace")
        width = max(pixels(self, 180),self.trend.winfo_width())
        title = "日期（周起始 / 系统本地日）" if weekly else "日期（系统本地日）"
        title_width = max(1,width-pixels(self,24))
        title_lines = max(1,math.ceil(font.measure(title)/title_width))
        height = max(pixels(self,185), pixels(self,96)+(title_lines+4)*line)
        self.trend.configure(height=height)
        left = max(font.measure(axis_count(value)) for value in ticks)+pixels(self,16)
        right = max(left+pixels(self,20),width-pixels(self,16))
        top = pixels(self,16)+line
        bottom = height-(title_lines+1)*line-pixels(self,24)
        for value in ticks:
            y = bottom-(bottom-top)*value/max(1,peak)
            self.trend.create_line(left,y,right,y,fill=colors["LINE"],tags=("theme-fixed",))
            self.trend.create_text(left-pixels(self,6),y,text=axis_count(value),anchor="e",fill=colors["MUTED"],font=font,tags=("theme-fixed",))
        self.trend.create_text(pixels(self,8),pixels(self,6),text="Token",anchor="nw",fill=colors["MUTED"],font=font,tags=("theme-fixed",))
        self.trend.create_line(left,top,left,bottom,fill=colors["LINE"],tags=("theme-fixed",))
        previous = None
        count = len(self._trend_series)
        for index,(day,value) in enumerate(self._trend_series):
            x = left+(right-left)*index/max(1,count-1)
            if value is None:
                previous = None
                continue
            y = bottom-(bottom-top)*value/max(1,peak)
            if previous:
                self.trend.create_line(*previous,x,y,fill=colors["ACCENT"],width=2,tags=("theme-fixed","usage-series"))
            radius = pixels(self,3)
            self.trend.create_oval(x-radius,y-radius,x+radius,y+radius,fill=colors["ACCENT"],outline=colors["ACCENT"],tags=("theme-fixed","usage-point"))
            previous = (x,y)
        indices = {0,max(0,count-1)}
        if count and right-left > font.measure(self._trend_series[0][0].isoformat())*3+pixels(self,24):
            indices.add((count-1)//2)
        for index in sorted(indices):
            if count:
                x = (left+right)/2 if count==1 else left+(right-left)*index/(count-1)
                anchor = "center" if count==1 else "w" if index==0 else "e" if index==count-1 else "center"
                self.trend.create_text(x,bottom+pixels(self,6),text=self._trend_series[index][0].isoformat(),anchor="n"+anchor if anchor!="center" else "n",fill=colors["MUTED"],font=font,tags=("theme-fixed",))
        self.trend.create_text(width/2,bottom+line+pixels(self,14),anchor="n",width=title_width,text=title,fill=colors["MUTED"],font=font,tags=("theme-fixed",))
        if not values:
            self.trend.create_text((left+right)/2,(top+bottom)/2,width=right-left,text="此范围未上报计量，不补 0",fill=colors["MUTED"],font=(theme.BODY_FONT,9),tags=("theme-fixed",))

    def _set_view(self, value):
        self.view.set(value)
        for button in self.mode_buttons:
            active = button.cget("text") == value
            set_color_roles(button, background="ACCENT_SOFT" if active else "WHITE", foreground="INK")
        if not self._query:
            return
        start, end = self._query["start"], self._query["end"]
        if value == "日":
            start = end = self._selected
        elif value == "周":
            monday = self._selected-timedelta(days=self._selected.weekday())
            start = max(start,monday)
            end = date.fromordinal(min(end.toordinal(),monday.toordinal()+6))
        rows = [row for row in self._daily if start.isoformat() <= row["date"] <= end.isoformat()]
        actual = sum_known(rows,"total_tokens","actual")
        observed = sum_known(rows,"total_tokens","observed")
        requests = sum_known(rows,"request_count")
        for key, number in (("actual",actual),("observed",observed),("requests",requests)):
            self._metric_texts[key] = format_count(number)
        # No prices/fee formulas. A period-specific official fee is not supplied
        # by the ledger API, so do not attribute an unrelated amount to a filter.
        self.metric_values["cost"].configure(text="—")
        self._layout_metrics(self.scroll.winfo_width())
        self.period.set(f"{value} · {start.isoformat()} → {end.isoformat()} · 数字仅为已知部分")
        known = sum(isinstance(row.get("actual"),dict) for row in rows)
        intervals = sum(isinstance(row.get("observed"),dict) for row in rows)
        self.coverage.set(f"已计量 {known} 天 · 观察增量 {intervals} 天")

    def _draw_calendar(self):
        if self.closed:
            return
        signature=(self.calendar.winfo_width(),theme.get_mode(),self.metric.get(),
                   repr(self._query),self._selected,id(self._daily))
        if getattr(self,'_calendar_draw_signature',None)==signature:
            return
        self._calendar_draw_signature=signature
        self.calendar.delete("all")
        self.weekdays.delete("all")
        self._cell_bounds = {}
        if not self._query:
            return
        year = self._query["year"]
        first, last = date(year,1,1), date(year,12,31)
        weeks = calendar_position(last)[0]+1
        font = self._axis_font
        left = pixels(self,6)
        budget = max(8, (max(1,self.calendar.winfo_width())-left-pixels(self,4))//weeks)
        gap = min(pixels(self,3), max(1,budget//4))
        # These dots contain no text. Fit the complete year even when a high
        # DPI window has only a narrow content pane; retain keyboard targets.
        cell = max(6, min(pixels(self,18),budget-gap))
        top = font.metrics("linespace")+pixels(self,10)
        pitch = cell+gap
        colors = theme.palette()
        width,height=left+weeks*pitch+pixels(self,4),top+7*pitch+pixels(self,5)
        bitmap=Image.new('RGB',(width,height),colors['WHITE'])
        draw=ImageDraw.Draw(bitmap)
        scope = "observed" if self.metric.get().startswith("观察") else "actual"
        values = [_number((row.get(scope) or {}).get("total_tokens")) for row in self._daily]
        peak = max((value for value in values if value is not None), default=0)
        self._calendar_peak = peak
        self._calendar_weeks = weeks
        for index, label in enumerate(weekday_labels(translator_for(self).language, compact=True)):
            if pitch < font.metrics('linespace') and index not in (0,2,4):
                continue
            self.weekdays.create_text(pixels(self,13),top+index*pitch+cell/2,text=label,fill=colors["MUTED"],font=font,tags=("theme-fixed",))
        months = set()
        day = first
        while day <= last:
            week, weekday = calendar_position(day)
            x,y = left+week*pitch,top+weekday*pitch
            row = self._day_lookup.get(day.isoformat(),{})
            tokens = row.get(scope) or {}
            value = _number(tokens.get("total_tokens"))
            outside = not self._query["start"] <= day <= self._query["end"]
            fill = colors["SIDEBAR"] if value is None else colors["WHITE"] if value == 0 else _blend(
                '#164563' if theme.get_mode()=='dark' else '#C9E9FA',
                '#80C7F5' if theme.get_mode()=='dark' else '#267EB5',value/(peak or 1))
            if outside:
                fill = colors["APP_BG"]
            selected = day == self._selected
            outline = colors["ACCENT"] if selected else colors['LINE'] if value==0 else ''
            # One grid bitmap, not 365 GDI Bezier paths during each native
            # paint. Hit rectangles/date coordinates remain individual facts.
            draw.rounded_rectangle((x,y,x+cell,y+cell),radius=min(pixels(self,4),cell//3),
                                   fill=fill,outline=outline or None,width=2 if selected else 1)
            self._cell_bounds[day.isoformat()] = (x,y,x+cell,y+cell)
            if day.month not in months:
                months.add(day.month)
                self.calendar.create_text(x,pixels(self,4),text=translator_for(self)('{month}月', month=day.month),anchor="nw",fill=colors["MUTED"],font=font,tags=("theme-fixed",))
            if day == last:
                break
            day += timedelta(days=1)
        self._calendar_photo=ImageTk.PhotoImage(bitmap,master=self)
        background=self.calendar.create_image(0,0,image=self._calendar_photo,anchor='nw',tags='theme-fixed')
        self.calendar.tag_lower(background)
        self.calendar.configure(height=height,scrollregion=(0,0,width,height))
        self.weekdays.configure(width=pixels(self,26),height=height)
        self.legend.configure(text='')

    def _language_changed(self):
        self._draw_calendar()
        self._draw_trend()

    def _hit_day(self, event):
        x,y = self.calendar.canvasx(event.x),self.calendar.canvasy(event.y)
        for key,(left,top,right,bottom) in self._cell_bounds.items():
            if left <= x <= right and top <= y <= bottom:
                return date.fromisoformat(key)
        return None

    def _day_hint(self, day):
        row = self._day_lookup.get(day.isoformat(),{})
        actual, observed = row.get("actual") or {},row.get("observed") or {}
        return f"{day.isoformat()} · 已记录 {format_count(actual.get('total_tokens'))} · 观察增量 {format_count(observed.get('total_tokens'))} Token · 响应计量 {format_count(row.get('request_count'))}"

    def _calendar_hover(self, event):
        day = self._hit_day(event)
        if day and day != self._hovered:
            self._hovered = day
            self.hover_text.set(self._day_hint(day))

    def _calendar_click(self, event):
        day = self._hit_day(event)
        if day:
            self.calendar.focus_set()
            self._choose_day(day)

    def _choose_day(self, day):
        if not self._query:
            return "break"
        first,last = self._query["start"],self._query["end"]
        if not first <= day <= last:
            self.status.set("该日期不在起止日筛选内；调整筛选后可查看。")
            return "break"
        self._selected = max(first,min(day,last))
        self._draw_calendar()
        self._set_view(self.view.get())
        self._show_day()
        self.hover_text.set(self._day_hint(self._selected))
        bounds = self._cell_bounds.get(self._selected.isoformat())
        if bounds:
            view_left = self.calendar.canvasx(0)
            view_right = view_left+self.calendar.winfo_width()
            total_width = float(self.calendar.cget("scrollregion").split()[2])
            if bounds[0] < view_left:
                self.calendar.xview_moveto(max(0,bounds[0]-28)/total_width)
            elif bounds[2] > view_right:
                self.calendar.xview_moveto(max(0,bounds[2]-self.calendar.winfo_width()+8)/total_width)
        return "break"

    def _move_day(self, days):
        if self._query:
            first,last = self._query["start"],self._query["end"]
            ordinal = max(first.toordinal(),min(self._selected.toordinal()+days,last.toordinal()))
            return self._choose_day(date.fromordinal(ordinal))
        return "break"

    def _calendar_edge(self, last):
        if self._query:
            return self._choose_day(self._query["end"] if last else self._query["start"])
        return "break"

    def _show_day(self):
        row = self._day_lookup.get(self._selected.isoformat(),{})
        self.day_heading.configure(text=self._selected.isoformat()+" · 日期明细")
        actual,observed = row.get("actual") or {},row.get("observed") or {}
        lines = ["已记录："+" · ".join(FIELD_LABELS[key]+" "+format_count(actual.get(key)) for key in TOKEN_FIELDS),
                 "观察增量："+" · ".join(FIELD_LABELS[key]+" "+format_count(observed.get(key)) for key in TOKEN_FIELDS),
                 "响应计量："+format_count(row.get("request_count"))+"（非用户消息数 / 完整请求数）"]
        if isinstance(row.get("observed"),dict):
            def stamp(value):
                if type(value) in {int,float} and math.isfinite(value):
                    try:
                        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
                    except (OSError,ValueError,OverflowError):
                        pass
                return "未知"
            lines.append("观察区间："+stamp(row.get("observed_start"))+" → "+stamp(row.get("observed_end"))+"；此日期为区间末日，不是实际调用日。")
        if not actual and not observed:
            lines.append("该日期未上报计量，不表示消耗为 0。连接并选定有公开计量的会话后，本地账本才会记录可读来源。")
        lines.extend(("缓存输入单独列出，不再次叠加进总 Token。", "费用：未上报，不根据 Token 推算。未分配日期基线不进入此日期。"))
        position = self.detail.yview()[0]
        self.detail.configure(state="normal")
        self.detail.delete("1.0","end")
        self.detail.insert("1.0","\n".join(lines))
        self.detail.configure(state="disabled")
        self.detail.yview_moveto(position if getattr(self,"_detail_date",None)==self._selected else 0)
        self._detail_date = self._selected

    def _open_official(self, label):
        url = OFFICIAL_PAGES.get(label)
        parsed = urlsplit(url or "")
        allowed = {("chatgpt.com","/u/"),("platform.deepseek.com","/usage")}
        if not url or parsed.scheme != "https" or (parsed.hostname,parsed.path) not in allowed or parsed.query or parsed.fragment or parsed.username:
            self._error("该地址不在明确允许的官方 HTTPS 页面列表中。")
            return
        try:
            self._opener(url)
        except Exception:
            self._error("浏览器未能打开。请手动访问用户提供的官方页面。")

    def _mapped(self, event):
        if event.widget is self and not self.closed and not self.busy and time.monotonic()-self._loaded_at > 15:
            self.refresh()

    def _destroyed(self, event):
        if event.widget is self:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._stop.set()
        self._replace_queue(self._requests,None)
        try:
            self.after_cancel(self._after)
        except tk.TclError:
            pass
