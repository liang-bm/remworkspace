"""THESIS: one Token activity surface, measured figures and readable trends.
OWN-WORLD: RemChat's soft blue-grey/blue-white, rounded incumbent surfaces.
STORY: choose a period/model, compare usage, inspect a date without guessing.
FIRST VIEWPORT: filters, three figures, daily bars, composition and model rank.
FORM: the user's DeepSeek dashboard reference within the existing Tk UI.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import tkinter as tk
from .scroll_surface import ScrollCanvas
from tkinter import font as tkfont, ttk

from . import theme
from .combobox_popup import AnchoredCombobox
from .dpi import pixels
from .settings_card import SettingsCard
from .rounded_controls import rounded_box
from .selection_feedback import Tooltip
from .theme_controller import set_color_roles
from .token_activity_data import build_activity
from .locale_controller import translator_for
from ..i18n import weekday_labels

PERIODS = {"近 7 天": 7, "近 30 天": 30, "近 90 天": 90, "近一年": 365}


def number(value):
    return f"{value:,}" if type(value) is int else "—"


def compact(value):
    if value >= 1_000_000_000_000_000:
        exponent = len(str(value)) - 1
        return f"{value // 10 ** (exponent - 2) / 100:.1f}e{exponent}"
    for divisor, unit in ((10 ** 12, "T"), (10 ** 9, "B"), (10 ** 6, "M"), (10 ** 3, "K")):
        if value >= divisor:
            return f"{value / divisor:.1f}{unit}"
    return str(value)


def text_on(background):
    def luminance(color):
        values = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        return sum((v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) * weight
                   for v, weight in zip(values, (.2126, .7152, .0722)))
    base = luminance(background)
    def contrast(color):
        other = luminance(color)
        return (max(base, other) + .05) / (min(base, other) + .05)
    return max((theme.palette()["INK"], theme.palette()["APP_BG"], theme.palette()["ON_ACCENT"]), key=contrast)


class TokenActivity(tk.Frame):
    """Persistent widgets; renders only changed local evidence, never reads logs."""

    def __init__(self, parent, *, translator=lambda value: value, select_day=None, wide=True):
        super().__init__(parent, width=1)
        set_color_roles(self, background="APP_BG")
        self.tr, self.select_day = translator, select_day
        self._usage, self._session, self._day, self._read_at = None, None, "", None
        self._end, self._view, self._index = "", "trend", 0
        self._signature, self._job, self._dialog, self._close_dialog = None, None, None, None
        self._clone, self.data, self.draw_count = None, None, 0
        self._geometry, self._rendering = {}, False
        self._usage_revision, self._data_key, self._layout_key = 0, None, None
        self._remchat_retranslate = self._language_changed
        self._content_key, self._view_key = None, None
        self._metric_font = tkfont.Font(self, family=theme.BODY_FONT, size=22, weight="bold")
        self._axis_font = tkfont.Font(self, family=theme.BODY_FONT, size=9)
        self.period, self.model = tk.StringVar(self, "近 30 天"), tk.StringVar(self, "全部模型")
        self._labels, self._hit_regions = [], []
        self.columnconfigure(0, weight=1)
        self.filters = self._frame(self)
        self.filters.grid(row=0, column=0, sticky="ew", pady=(4, 8))
        self.range_group = self._frame(self.filters)
        self._label(self.range_group, "时间范围", muted=True).pack(side="left", padx=(0, 8))
        self.range_choice = AnchoredCombobox(self.range_group, textvariable=self.period, values=list(PERIODS), state="readonly", width=10)
        self.range_choice.pack(side="left", fill="x", expand=True)
        self.range_choice.bind("<<ComboboxSelected>>", self._filter_changed)
        self.model_group = self._frame(self.filters)
        self._label(self.model_group, "模型", muted=True).pack(side="left", padx=(0, 8))
        self.model_choice = AnchoredCombobox(self.model_group, textvariable=self.model, values=["全部模型"], state="readonly", width=14)
        self.model_choice.pack(side="left", fill="x", expand=True)
        self.model_choice.bind("<<ComboboxSelected>>", self._schedule)
        self.period_line = self._label(self, "选择一个主会话后查看用量", muted=True)
        self.period_line.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.nav = self._frame(self)
        self.nav.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.nav_buttons = [self._button(self.nav, "上一段", lambda: self._shift(-1)),
                            self._button(self.nav, "今天", self._today),
                            self._button(self.nav, "下一段", lambda: self._shift(1))]
        if wide:
            self.nav_buttons.append(self._button(self.nav, "展开分析", self._open_wide))
        self.figures = self._frame(self)
        self.figures.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.stats = []
        for title, note in (("实际 Token", "有日期的原生计量"), ("计量记录", "不是 API 请求次数"), ("缓存输入", "已包含在输入 Token 中")):
            card = SettingsCard(self.figures, inset=12, radius=16)
            label = self._label(card.body, title, muted=True, surface=True)
            label.pack(fill="x")
            value = self._label(card.body, "—", size=22, bold=True, surface=True)
            value.configure(takefocus=True)
            value._full_value = "—"
            Tooltip(value, lambda label=value: label._full_value)
            value.pack(fill="x", pady=(6, 4))
            detail = self._label(card.body, note, size=9, muted=True, surface=True)
            detail.pack(fill="x")
            self.stats.append((card, value, detail))
        self.chart_card = SettingsCard(self, inset=16, radius=16)
        self.chart_card.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        head = self.chart_head = self._frame(self.chart_card.body, surface=True)
        head.pack(fill="x")
        head.columnconfigure(0, weight=1)
        self.chart_title = self._label(head, "每日 Token", bold=True, surface=True)
        self.view_buttons = [self._button(head, "趋势", lambda: self._set_view("trend"), surface=True),
                             self._button(head, "日历", lambda: self._set_view("calendar"), surface=True)]
        self.trend = self._canvas(self.chart_card.body, 190)
        self.trend.pack(fill="x", pady=(8, 0))
        self.chart_hint = self._label(self.chart_card.body, "指向或点击日期查看；方向键选择，Enter 查看明细。", size=9, muted=True, surface=True)
        self.chart_hint.pack(fill="x", pady=(8, 0))
        self.trend.bind("<Motion>", self._hover)
        self.trend.bind("<Button-1>", self._pick)
        self.trend.bind("<Left>", lambda event: self._move(-1))
        self.trend.bind("<Right>", lambda event: self._move(1))
        self.trend.bind("<Up>", lambda event: self._move(-7))
        self.trend.bind("<Down>", lambda event: self._move(7))
        self.trend.bind("<Return>", self._show_day)
        self.trend.bind("<Prior>", lambda event: self._shift(-1))
        self.trend.bind("<Next>", lambda event: self._shift(1))
        self.trend.bind("<Home>", lambda event: self._today())
        self.comparison = self._frame(self)
        self.comparison.grid(row=5, column=0, sticky="ew")
        self.series_card, self.models_card = (SettingsCard(self.comparison, inset=16, radius=16) for _ in range(2))
        self._label(self.series_card.body, "输入 / 输出趋势", bold=True, surface=True).pack(fill="x")
        self._label(self.series_card.body, "实线：输入   虚线：输出", size=9, muted=True, surface=True).pack(fill="x", pady=(6, 0))
        self.series = self._canvas(self.series_card.body, 160)
        self.series.pack(fill="x", pady=(8, 0))
        self._label(self.models_card.body, "模型用量", bold=True, surface=True).pack(fill="x")
        self._label(self.models_card.body, "按每条原生计量的模型归属 · 前 6 项", size=9, muted=True, surface=True).pack(fill="x", pady=(6, 0))
        self.models = self._canvas(self.models_card.body, 160)
        self.models.pack(fill="x", pady=(8, 0))
        self.details_button = self._button(self, "统计口径与累计快照  ▾", self._toggle_details)
        self.details_button.grid(row=6, column=0, sticky="w", pady=(12, 4))
        self.details = self._label(self, "", size=9, muted=True)
        self.details.grid(row=7, column=0, sticky="ew", pady=(4, 8))
        self.details.grid_remove()
        self.bind("<Configure>", self._schedule, add=True)
        self.bind("<Map>", self._schedule, add=True)
        self.bind("<<ThemeChanged>>", self._theme_changed, add=True)
        self.bind("<Destroy>", self._destroyed, add=True)
        self._schedule()

    def _frame(self, parent, surface=False):
        frame = tk.Frame(parent, width=1)
        set_color_roles(frame, background="WHITE" if surface else "APP_BG")
        return frame

    def _label(self, parent, text, *, size=10, bold=False, muted=False, surface=False):
        label = tk.Label(parent, text=self.tr(text), anchor="w", justify="left", wraplength=200,
                         font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
        set_color_roles(label, background="WHITE" if surface else "APP_BG", foreground="MUTED" if muted else "INK")
        def wrap(event):
            width = max(60, event.width)
            if int(label.cget("wraplength")) != width:
                label.configure(wraplength=width)
        label.bind("<Configure>", wrap)
        self._labels.append(label)
        return label

    def _button(self, parent, text, command, surface=False):
        button = tk.Button(parent, text=self.tr(text), command=command, relief="flat", bd=0, padx=8, pady=6,
                           cursor="hand2", takefocus=True, highlightthickness=1, font=(theme.BODY_FONT, 9))
        set_color_roles(button, background="WHITE" if surface else "ACCENT_SOFT", foreground="INK",
                        activebackground="ACCENT_SOFT", activeforeground="INK", disabledforeground="MUTED",
                        highlightbackground="LINE", highlightcolor="ACCENT")
        return button

    def _canvas(self, parent, height):
        canvas = tk.Canvas(parent, width=1, height=pixels(self, height), bd=0, highlightthickness=1, takefocus=True)
        set_color_roles(canvas, background="WHITE", highlightbackground="WHITE", highlightcolor="ACCENT")
        canvas.bind("<Configure>", self._schedule)
        return canvas

    def set_data(self, usage, session_key=None, day="", read_at=None):
        if self._session != session_key:
            self.model_choice.dismiss_popup()
            self.range_choice.dismiss_popup()
            self._end, self._index = "", 0
            self.model.set("全部模型")
        self._usage, self._session, self._day, self._read_at = usage, session_key, day, read_at
        # Data arrives from the observer, not from a geometry callback. One
        # projection per delivery/filter is enough for any number of resizes.
        self._usage_revision += 1
        self._schedule()
        if self._clone and self._clone.winfo_exists():
            self._clone.set_data(usage, session_key, day, read_at)

    def _schedule(self, _event=None):
        if _event is not None and _event.type == tk.EventType.Configure:
            size = (_event.width, _event.height)
            if self._geometry.get(_event.widget) == size:
                return  # Scrolling a Canvas window changes its position only.
            self._geometry[_event.widget] = size
        if self._job is None:
            # Single-flight frame work, not a trailing debounce: a continuous
            # drag must not postpone all painting until the pointer stops.
            self._job = self.after(16, self._render)

    def _theme_changed(self, _event=None):
        self.range_choice.dismiss_popup()
        self.model_choice.dismiss_popup()
        self._schedule()

    def _language_changed(self):
        self._signature = None
        self._schedule()

    def _filter_changed(self, _event=None):
        self._end = ""
        self._schedule()

    def _set_view(self, view):
        self._view = view
        self._schedule()

    def _shift(self, direction):
        if self.data and not self._day:
            end = date.fromisoformat(self.data["period"]["end"])
            ordinal = end.toordinal() + direction * self.data["period"]["days"]
            self._end = date.fromordinal(max(self.data["period"]["days"], min(date.max.toordinal(), ordinal))).isoformat()
            self._schedule()
        return "break"

    def _today(self):
        self._end = ""
        if self._day and self.select_day:
            self.select_day("")
        self._schedule()
        return "break"

    def _render(self):
        self._job = None
        if not self.winfo_exists() or not self.winfo_ismapped():
            return
        if self._rendering:
            self._schedule()
            return
        self._rendering = True
        try:
            self._render_frame()
        finally:
            self._rendering = False

    def _render_frame(self):
        width = max(180, self.winfo_width())
        data_key = (self._usage_revision, self.period.get(), self._day or self._end,
                    self.model.get(), date.today())
        if data_key != self._data_key:
            self.data = build_activity(self._usage or {}, days=1 if self._day else PERIODS.get(self.period.get(), 30),
                                       end_day=self._day or self._end, model="" if self.model.get() == "全部模型" else self.model.get())
            self._data_key = data_key
        data = self.data
        choices = ["全部模型", *data["available_models"]]
        self.model_choice.set_values(choices)
        if self.model.get() not in choices:
            self.model.set("全部模型")
            data = build_activity(self._usage or {}, days=1 if self._day else PERIODS.get(self.period.get(), 30), end_day=self._day or self._end)
            self.data = data
            self._data_key = (self._usage_revision, self.period.get(), self._day or self._end,
                              self.model.get(), date.today())
        if self._day:
            self.range_choice.dismiss_popup()
        state = "disabled" if self._day else "readonly"
        if str(self.range_choice.cget("state")) != state:
            self.range_choice.configure(state=state)
        for button in (self.nav_buttons[0], self.nav_buttons[2]):
            state = "disabled" if self._day or not self._usage else "normal"
            if str(button.cget("state")) != state:
                button.configure(state=state)
        stamp = datetime.fromtimestamp(self._read_at).strftime("%H:%M:%S") if self._read_at else "—"
        period = data["period"]
        period_text = self.tr(f"{period['start']} — {period['end']} · 更新 {stamp}") + self.tr(" · 日期筛选中" if self._day else " · 随观察更新")
        if self.period_line.cget("text") != period_text:
            self.period_line.configure(text=period_text)
        geometry = tuple((canvas.winfo_width(), canvas.winfo_height()) for canvas in (self.trend, self.series, self.models))
        signature = (data, width, geometry, theme.get_mode(), self._view, self._usage is None,
                     translator_for(self).language)
        if signature == self._signature:
            return
        self._signature = signature
        self.draw_count += 1
        view_key = (self._view, theme.get_mode())
        if self._view_key != view_key:
            for view, button in zip(("trend", "calendar"), self.view_buttons):
                set_color_roles(button, background="ACCENT_SOFT" if self._view == view else "WHITE",
                                foreground="INK", activebackground="ACCENT_SOFT", activeforeground="INK",
                                highlightbackground="ACCENT" if self._view == view else "LINE", highlightcolor="ACCENT")
                button.configure(relief="sunken" if self._view == view else "flat")
            self._view_key = view_key
        actual = data["totals"]["actual"]
        values = [actual.get("total_tokens"), data["measurements"] if data["measurements"] else None, actual.get("cached_input_tokens")]
        notes = [f"已计量 {data['measured_days']}/{period['days']} 天", "仅已采集响应的计量记录", "缓存是输入的一部分，不重复相加"]
        content_key = (tuple(values), tuple(notes))
        if content_key != self._content_key:
            for (_, label, detail), value, note in zip(self.stats, values, notes):
                label._full_value, label._numeric_value = number(value), value
                label.configure(text=label._full_value)
                detail.configure(text=self.tr(note))
            self._content_key = content_key
        layout_key = (width, tuple(values), theme.get_mode())
        if layout_key != self._layout_key:
            self._layout(width)
            self._layout_key = layout_key
        self._draw_daily()
        self._draw_series()
        self._draw_models()
        baseline, observed = data["totals"]["baseline"], data["totals"]["observed"]
        lines = ["统计范围：所选主会话的近期已采集记录，非账号完整历史。",
                 "输入 " + number(actual.get("input_tokens")) + " · 输出 " + number(actual.get("output_tokens")),
                 "观察增量 " + number(observed.get("total_tokens")) + " Token（不是实际日计量，不与上方混加）。",
                 "未分配初值 " + number(baseline.get("total_tokens")) + " Token（不归入日期或模型）。"]
        snapshot = data.get("cumulative_snapshot")
        if snapshot:
            lines.append("会话累计快照 " + number(snapshot.get("tokens", {}).get("total_tokens")) + " Token（独立参考，不与图表相加）。")
        lines.extend(data.get("notices", []))
        detail_text = self.tr("\n".join(lines))
        if self.details.cget("text") != detail_text:
            self.details.configure(text=detail_text)
        # Let Tk finish geometry naturally. Pumping update_idletasks here
        # re-enters this render via Configure and also runs unrelated pages.

    def _layout(self, width):
        gap = pixels(self, 8)
        wide = width >= pixels(self, 640)
        for index, group in enumerate((self.range_group, self.model_group)):
            group.grid(row=0 if wide else index, column=index if wide else 0, sticky="ew", padx=(0, gap if wide and index == 0 else 0), pady=(0, 0 if wide else gap))
            self.filters.columnconfigure(index, weight=1 if wide or index == 0 else 0)
        used = row = column = 0
        for button in self.nav_buttons:
            size = button.winfo_reqwidth() + gap
            if used and used + size > width:
                row, column, used = row + 1, 0, 0
            button.grid(row=row, column=column, sticky="w", padx=(0, gap), pady=(0, gap if row else 0))
            used, column = used + size, column + 1
        columns = 3 if width >= pixels(self, 560) else 1
        for index, (card, value, _detail) in enumerate(self.stats):
            card.grid(row=0 if columns == 3 else index, column=index if columns == 3 else 0, sticky="new", padx=(0, gap if columns == 3 and index < 2 else 0), pady=(0, gap))
            self.figures.columnconfigure(index, weight=1 if columns == 3 or index == 0 else 0)
            available = max(80, (width - (columns - 1) * gap) // columns - pixels(self, 28))
            font = self._metric_font
            font.configure(size=22)
            while font.measure(value._full_value) > available and font.cget("size") > 12:
                font.configure(size=font.cget("size") - 1)
            text = compact(value._numeric_value) if font.measure(value._full_value) > available and value._numeric_value is not None else value._full_value
            value.configure(text=text)
            value.configure(font=(theme.BODY_FONT, font.cget("size"), "bold"))
            card.refresh()
        header_stacked = width < pixels(self, 400)
        self.chart_title.grid(row=0, column=0, columnspan=3 if header_stacked else 1, sticky="ew",
                              pady=(0, gap if header_stacked else 0))
        for index, button in enumerate(self.view_buttons):
            button.grid(row=1 if header_stacked else 0, column=index if header_stacked else index + 1,
                        sticky="w", padx=(0 if header_stacked and index == 0 else gap, 0))
        self.chart_card.refresh()
        for index, card in enumerate((self.series_card, self.models_card)):
            card.grid(row=0 if wide else index, column=index if wide else 0, sticky="nsew", padx=(0, gap if wide and index == 0 else 0), pady=(0, gap))
            self.comparison.columnconfigure(index, weight=1 if wide or index == 0 else 0)
            card.refresh()

    def _empty(self, canvas, text):
        canvas.create_text(pixels(self, 8), pixels(self, 38), anchor="nw", width=max(100, canvas.winfo_width() - pixels(self, 16)),
                           text=self.tr(text), fill=theme.palette()["MUTED"], font=(theme.BODY_FONT, 10), tags="theme-fixed")

    def _axes(self, canvas, maximum):
        colors = theme.palette()
        axis_font = self._axis_font
        left = max(pixels(self, 49), axis_font.measure(compact(maximum)) + pixels(self, 12))
        right = max(left + pixels(self, 20), canvas.winfo_width() - pixels(self, 12))
        top, bottom = pixels(self, 12), canvas.winfo_height() - pixels(self, 28)
        fractions = (0,) if maximum == 0 else (0, 1) if maximum == 1 else (0, .5, 1)
        for fraction in fractions:
            y = bottom - (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill=colors["LINE"], tags="theme-fixed")
            tick = 0 if fraction == 0 else maximum // 2 if fraction == .5 else maximum
            canvas.create_text(left - pixels(self, 6), y, anchor="e", text=compact(tick), fill=colors["MUTED"], font=(theme.BODY_FONT, 9), tags="theme-fixed")
        daily = self.data["daily"]
        dates = [f"{int(row['date'][5:7])}/{int(row['date'][8:10])}" for row in daily]
        indices = {0, len(daily) - 1}
        if right - left > max(axis_font.measure(text) for text in dates) * 3 + pixels(self, 24):
            indices.add(len(daily) // 2)
        for index in sorted(indices):
            x, anchor = (left, "w") if index == 0 else (right, "e") if index == len(daily) - 1 else ((left + right) / 2, "center")
            canvas.create_text(x, bottom + pixels(self, 14), anchor=anchor, text=dates[index], fill=colors["MUTED"], font=(theme.BODY_FONT, 9), tags="theme-fixed")
        return left, top, right, bottom

    def _draw_daily(self):
        canvas, colors = self.trend, theme.palette()
        canvas.delete("all")
        self._hit_regions = []
        rows = self.data["daily"]
        if self._view == "calendar":
            self._draw_calendar(rows)
            return
        canvas.configure(height=pixels(self, 190))
        values = [row["tokens"].get("total_tokens") for row in rows]
        known = [value for value in values if type(value) is int]
        if not known:
            self._empty(canvas, "尚无此范围的原生实际计量。\n可切换时间范围或模型；未上报不是零。")
            self.chart_hint.configure(text=self.tr("日历模式仍可查看日期；统计口径里保留独立累计快照。"))
            return
        maximum = max(known)
        left, top, right, bottom = self._axes(canvas, maximum)
        step = (right - left) / len(rows)
        for index, value in enumerate(values):
            x = left + step * index
            self._hit_regions.append((x, top, x + step, bottom, index))
            if value is None:
                canvas.create_line(x + step / 2, bottom - pixels(self, 4), x + step / 2, bottom, fill=colors["SUBTLE"], dash=(1, 2), tags="theme-fixed")
            elif value == 0:
                canvas.create_oval(x + step / 2 - 2, bottom - 4, x + step / 2 + 2, bottom, outline=colors["ACCENT"], tags="theme-fixed")
            else:
                canvas.create_rectangle(x + step * .2, bottom - (bottom - top) * value / maximum, x + step * .8, bottom, fill=colors["ACCENT" if theme.get_mode() == "dark" else "ACCENT_DEEP"], outline="", tags="theme-fixed")
        self.chart_hint.configure(text=self.tr("指向 / 点击日期看数值；方向键 + Enter 查看明细。空缺不连接为零。"))

    def _draw_calendar(self, rows):
        canvas, colors = self.trend, theme.palette()
        width = max(160, canvas.winfo_width() - pixels(self, 12))
        if len(rows) > 35:
            self._draw_year(rows, width)
            return
        cell, height = width / 7, pixels(self, 30)
        canvas.configure(height=math.ceil(len(rows) / 7) * height + pixels(self, 28))
        for column, text in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            canvas.create_text(pixels(self, 6) + cell * (column + .5), pixels(self, 10), text=text, fill=colors["MUTED"], font=(theme.BODY_FONT, 9), tags="theme-fixed")
        offset = date.fromisoformat(rows[0]["date"]).weekday()
        canvas.configure(height=math.ceil((len(rows) + offset) / 7) * height + pixels(self, 28))
        maximum = max((row["tokens"].get("total_tokens") or 0 for row in rows), default=0)
        for index, row in enumerate(rows):
            position = index + offset
            x, y = pixels(self, 6) + position % 7 * cell, pixels(self, 24) + position // 7 * height
            value = row["tokens"].get("total_tokens")
            color = colors["WHITE"] if value is None else colors["SIDEBAR"] if value == 0 else colors["ACCENT_SOFT"]
            if value and maximum:
                fraction = value / maximum
                lo, hi = colors["ACCENT_SOFT"], colors["ACCENT" if theme.get_mode() == "dark" else "ACCENT_DEEP"]
                color = "#" + "".join(f"{round(int(lo[k:k+2], 16) + (int(hi[k:k+2], 16)-int(lo[k:k+2], 16))*fraction):02x}" for k in (1, 3, 5))
            rounded_box(canvas,(x, y, x + cell - 4, y + height - 4),radius=pixels(self,5),
                        fill=color,outline=colors['LINE'] if value==0 else '',tags='theme-fixed')
            foreground = text_on(color)
            canvas.create_text(x + (cell - 4) / 2, y + (height - 4) / 2, text="0" if value == 0 else str(date.fromisoformat(row["date"]).day), fill=foreground, font=(theme.BODY_FONT, 9), tags="theme-fixed")
            self._hit_regions.append((x, y, x + cell - 4, y + height - 4, index))
        self.chart_hint.configure(text='')

    def _draw_year(self, rows, width):
        canvas, colors = self.trend, theme.palette()
        first = date.fromisoformat(rows[0]["date"])
        offset = first.weekday()
        columns = math.ceil((len(rows) + offset) / 7)
        left, top, height = pixels(self, 22), pixels(self, 28), pixels(self, 20)
        cell = (width - left) / columns
        canvas.configure(height=top + height * 7 + pixels(self, 8))
        maximum = max((row["tokens"].get("total_tokens") or 0 for row in rows), default=0)
        previous_month, previous_label = None, -100
        days = weekday_labels(translator_for(self).language, compact=True)
        for weekday in (0, 2, 4, 6):
            text = days[weekday]
            canvas.create_text(4, top + (weekday + .5) * height, text=text, anchor="w", fill=colors["MUTED"], font=(theme.BODY_FONT, 9), tags="theme-fixed")
        for index, row in enumerate(rows):
            day = date.fromisoformat(row["date"])
            position = index + offset
            x, y = left + position // 7 * cell, top + position % 7 * height
            if day.month != previous_month and x - previous_label > pixels(self, 35):
                canvas.create_text(x, 10, anchor="w", text=self.tr(f"{day.month}月"), fill=colors["MUTED"], font=(theme.BODY_FONT, 9), tags="theme-fixed")
                previous_label = x
            previous_month = day.month
            value = row["tokens"].get("total_tokens")
            lo, hi = colors["ACCENT_SOFT"], colors["ACCENT" if theme.get_mode() == "dark" else "ACCENT_DEEP"]
            fraction = value / maximum if value and maximum else 0
            color = colors["WHITE"] if value is None else colors["SIDEBAR"] if value == 0 else "#" + "".join(f"{round(int(lo[k:k+2], 16) + (int(hi[k:k+2], 16)-int(lo[k:k+2], 16))*fraction):02x}" for k in (1, 3, 5))
            right, bottom = x + max(2, cell - 3), y + height - 3
            rounded_box(canvas,(x,y,right,bottom),radius=pixels(self,4),fill=color,
                        outline=colors['LINE'] if value==0 else '',tags='theme-fixed')
            self._hit_regions.append((x, y, right, bottom, index))
        self.chart_hint.configure(text='')

    def _draw_series(self):
        canvas, colors = self.series, theme.palette()
        canvas.delete("all")
        rows = self.data["daily"]
        values = [value for row in rows for field in ("input_tokens", "output_tokens") if type(value := row["tokens"].get(field)) is int]
        if not values:
            self._empty(canvas, "输入 / 输出尚未上报。\n不以总 Token 反推拆分。")
            return
        maximum = max(values)
        left, top, right, bottom = self._axes(canvas, maximum)
        for field, color, dash in (("input_tokens", colors["ACCENT" if theme.get_mode() == "dark" else "ACCENT_DEEP"], ()),
                                  ("output_tokens", colors["ROSE"], (5, 3))):
            previous = None
            for index, row in enumerate(rows):
                value = row["tokens"].get(field)
                if value is None:
                    previous = None
                    continue
                x, y = left + (right - left) * (index + .5) / len(rows), bottom - (bottom - top) * value / (maximum or 1)
                if previous:
                    canvas.create_line(*previous, x, y, fill=color, dash=dash, width=2, tags="theme-fixed")
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color, tags="theme-fixed")
                previous = (x, y)

    def _draw_models(self):
        canvas, colors = self.models, theme.palette()
        canvas.delete("all")
        rows = self.data["models"][:6]
        if not rows:
            canvas.configure(height=pixels(self, 160))
            self._empty(canvas, "暂无模型归属的实际计量。\n不会把全会话累计归到当前模型。")
            return
        font = self._axis_font
        width, y = max(100, canvas.winfo_width() - pixels(self, 12)), pixels(self, 6)
        maximum = max((row["tokens"].get("total_tokens") or 0 for row in rows), default=1) or 1
        for row in rows:
            label = canvas.create_text(6, y, anchor="nw", width=width, text=row["model"], fill=colors["INK"], font=font, tags="theme-fixed")
            y = canvas.bbox(label)[3] + pixels(self, 6)
            value = row["tokens"].get("total_tokens")
            count = number(value)
            if font.measure(count) > width * .45 and value is not None:
                count = compact(value)
            end = max(20, width - font.measure(count) - pixels(self, 12))
            canvas.create_rectangle(6, y, end, y + pixels(self, 5), fill=colors["SIDEBAR"], outline="", tags="theme-fixed")
            if value:
                canvas.create_rectangle(6, y, 6 + (end - 6) * value / maximum, y + pixels(self, 5), fill=colors["ACCENT" if theme.get_mode() == "dark" else "ACCENT_DEEP"], outline="", tags="theme-fixed")
            canvas.create_text(width, y + pixels(self, 3), anchor="e", text=count, fill=colors["INK"], font=font, tags="theme-fixed")
            y += pixels(self, 24)
        canvas.configure(height=max(pixels(self, 160), y))

    def _hover(self, event):
        for left, top, right, bottom, index in self._hit_regions:
            if left <= event.x <= right and top <= event.y <= bottom:
                self._index = index
                self._day_hint()
                break

    def _pick(self, event):
        self._hover(event)
        self.trend.focus_set()
        self._day_hint()

    def _move(self, direction):
        if self.data:
            self._index = max(0, min(len(self.data["daily"]) - 1, self._index + direction))
            self._day_hint()
        return "break"

    def _day_hint(self):
        if self.data:
            self._index = min(self._index, len(self.data["daily"]) - 1)
            row = self.data["daily"][self._index]
            tokens = row["tokens"]
            self.chart_hint.configure(text=self.tr(row["date"] + " · Token " + number(tokens.get("total_tokens")) +
                                     " · 输入 " + number(tokens.get("input_tokens")) + " · 输出 " + number(tokens.get("output_tokens")) +
                                     " · 缓存 " + number(tokens.get("cached_input_tokens")) + " · 计量 " + number(row.get("measurements") or None)))
            self.trend.delete("selection")
            region = next((box for box in self._hit_regions if box[4] == self._index), None)
            if region:
                self.trend.create_rectangle(*region[:4], outline=theme.palette()["ACCENT"], width=2, tags=("selection", "theme-fixed"))

    def _show_day(self, _event=None):
        self._day_hint()
        if self.select_day and self.data:
            self.select_day(self.data["daily"][self._index]["date"])
        return "break"

    def _toggle_details(self):
        visible = bool(self.details.grid_info())
        self.details.grid_remove() if visible else self.details.grid()
        self.details_button.configure(text=self.tr("统计口径与累计快照  " + ("▾" if visible else "▴")))

    def _open_wide(self):
        if self._dialog and self._dialog.winfo_exists():
            self._dialog.lift()
            return
        window = self._dialog = tk.Toplevel(self)
        window.title(self.tr("Token 活动"))
        window.geometry("1000x820")
        window.minsize(480, 400)
        set_color_roles(window, background="APP_BG")
        viewport = ScrollCanvas(window, bd=0, highlightthickness=0)
        set_color_roles(viewport, background="APP_BG")
        viewport.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(window, command=viewport.yview)
        scroll.pack(side="right", fill="y")
        viewport.configure(yscrollcommand=scroll.set)
        clone = self._clone = TokenActivity(viewport, translator=self.tr, select_day=self.select_day, wide=False)
        item = viewport.create_window(16, 16, window=clone, anchor="nw")
        clone_size = None
        clone_bounds = None
        def resize(event):
            width = max(180, event.width - 32)
            if int(viewport.itemcget(item, "width")) != width:
                viewport.itemconfigure(item, width=width)
        def update_bounds(event):
            nonlocal clone_size, clone_bounds
            size = (event.width, event.height)
            if size == clone_size:
                return
            clone_size = size
            bounds = viewport.bbox("all")
            if bounds != clone_bounds:
                viewport.configure(scrollregion=bounds)
                clone_bounds = bounds
        viewport.bind("<Configure>", resize)
        clone.bind("<Configure>", update_bounds, add=True)
        wheel_tag = "token-analysis-" + str(id(window))
        def wheel(event):
            if not isinstance(event.widget, ttk.Combobox) and event.delta:
                viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"
        window.bind_class(wheel_tag, "<MouseWheel>", wheel)
        def tag(widget):
            widget.bindtags((wheel_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                tag(child)
        tag(clone)
        def close():
            window.unbind_class(wheel_tag, "<MouseWheel>")
            window.destroy()
            self._dialog = self._clone = self._close_dialog = None
        self._close_dialog = close
        def reveal(event):
            widget = event.widget
            if not str(widget).startswith(str(clone) + "."):
                return
            top = widget.winfo_rooty() - viewport.winfo_rooty() + viewport.canvasy(0)
            bottom = top + widget.winfo_height()
            visible_top = viewport.canvasy(0)
            visible_bottom = visible_top + viewport.winfo_height()
            bounds = viewport.bbox("all")
            if bounds and (top < visible_top or bottom > visible_bottom):
                target = top - 12 if top < visible_top else bottom + 12 - viewport.winfo_height()
                viewport.yview_moveto(max(0, target) / max(1, bounds[3]))
        window.bind("<FocusIn>", reveal, add=True)
        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda event: close())
        clone.period.set(self.period.get())
        clone.set_data(self._usage, self._session, self._day, self._read_at)
        clone.model.set(self.model.get())

    def _destroyed(self, event):
        if event.widget is self:
            if self._job is not None:
                self.after_cancel(self._job)
                self._job = None
            if self._close_dialog:
                self._close_dialog()
