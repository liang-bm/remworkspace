"""Clickable calendar countdown and a native, scrollable event editor.

THESIS: A quiet date reminder belongs in the chat header's unused center.
OWN-WORLD: Existing blue-grey / blue-white, round blue focus and selection.
STORY: Read the days; click once to edit all events and choose the shown one.
FIRST VIEWPORT: Two compact lines between focus and the original voice controls.
FORM: A local extension of the existing native header, no new visual identity.
"""
from __future__ import annotations

from datetime import date
import tkinter as tk

from ..countdown_days import CountdownDaysStore, CountdownEvent
from . import theme
from .diary_widgets import _button, _entry, _frame, _label
from .dpi import pixels, display_scale
from .font_source import native_font
from .rounded_controls import rounded_box
from .scroll_surface import ScrollCanvas
from .theme_controller import set_color_roles


class CountdownDaysChip(tk.Canvas):
    def __init__(self, parent, project_root):
        super().__init__(parent, width=1, height=pixels(parent, 48), bd=0,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self._remchat_theme_fixed = True
        self._remchat_locale_exclude = True  # event titles are user content
        self.store = CountdownDaysStore(project_root)
        self.error = ""
        self.editor = None
        self._job = None
        self._closed = False
        self._hover = False
        self._font = native_font(self, family=theme.BODY_FONT, size=9)
        self._strong = native_font(self, family=theme.BODY_FONT, size=10, weight="bold")
        try:
            self.store.load()
        except (OSError, ValueError) as error:
            self.error = str(error)
        for event in ("<Configure>", "<Map>", "<FocusIn>", "<FocusOut>", "<<ThemeChanged>>"):
            self.bind(event, self.draw, add=True)
        self.bind("<Enter>", lambda _event: self._set_hover(True))
        self.bind("<Leave>", lambda _event: self._set_hover(False))
        self.bind("<Button-1>", self.open)
        self.bind("<Return>", self.open)
        self.bind("<space>", self.open)
        self.bind("<Destroy>", self._destroy, add=True)
        self._job = self.after(30_000, self._refresh_date)

    def _set_hover(self, active):
        self._hover = active
        self.draw()

    def _fit(self, text, font, width):
        if width <= 0:
            return ""
        if font.measure(text) <= width:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if font.measure(text[:middle] + "…") <= width:
                low = middle
            else:
                high = middle - 1
        return text[:low] + "…" if font.measure("…") <= width else ""

    def draw(self, _event=None):
        if self._closed or not self.winfo_exists():
            return
        colors = theme.palette()
        self.configure(bg=colors["WHITE"])
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < pixels(self, 32) or height < 10:
            return
        focused = self.focus_get() is self
        translator = getattr(self.winfo_toplevel(), "_remchat_translator", None)
        t = translator or (lambda value, **fields: value.format(**fields))
        event = self.store.current()
        if self.error:
            title, subtitle = t("倒计日"), t("点击查看读取问题")
        elif event is None:
            title, subtitle = t("＋ 添加倒计日"), t("点击设置日期")
        else:
            days = event.days()
            title = t("距离 {title}", title=event.title) if days > 0 else event.title
            subtitle = (t("还有 {days} 天", days=days) if days > 0 else t("就是今天") if days == 0
                        else t("已过 {days} 天", days=-days))
            if len(self.store.events) > 1:
                subtitle += t(" · 共 {count} 项", count=len(self.store.events))
        pad = pixels(self, 12)
        box_width = min(width, max(pixels(self, 160), min(pixels(self, 340),
                            max(self._strong.measure(title), self._font.measure(subtitle))+2*pad)))
        left = (width-box_width)/2
        rounded_box(self, (left+1, 1, left+box_width-1, height-1), radius=pixels(self, 10),
                    fill=colors["ACCENT_SOFT"] if self._hover or focused else colors["WHITE"],
                    outline=colors["ACCENT"] if focused else colors["LINE"], width=1)
        self.create_text(left+pad, height / 2 - self._font.metrics("linespace") * .52,
                         anchor="w", text=self._fit(title, self._strong, box_width - 2 * pad),
                         font=self._strong, fill=colors["INK"])
        self.create_text(left+pad, height / 2 + self._font.metrics("linespace") * .65,
                         anchor="w", text=self._fit(subtitle, self._font, box_width - 2 * pad),
                         font=self._font, fill=colors["ACCENT"] if theme.get_mode() == "dark" else colors["ACCENT_DEEP"])

    def _refresh_date(self):
        self._job = None
        if self._closed:
            return
        if self.winfo_ismapped():
            self.draw()
        self._job = self.after(30_000, self._refresh_date)

    def open(self, _event=None):
        if self._closed:
            return "break"
        if self.editor is not None and self.editor.window.winfo_exists():
            self.editor.window.lift()
        else:
            self.editor = CountdownDaysEditor(self)
        return "break"

    def _destroy(self, event):
        if event.widget is not self:
            return
        self.close()

    def close(self):
        self._closed = True
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        if self.editor is not None:
            self.editor.close()
            self.editor = None
        self._font = self._strong = None


class CountdownDaysEditor:
    def __init__(self, chip):
        self.chip = chip
        self.rows = []
        self.window = tk.Toplevel(chip.winfo_toplevel())
        window = self.window
        window.withdraw()
        window.title("设置倒计日")
        window.transient(chip.winfo_toplevel())
        scale = display_scale(window)
        width = min(round(720 * scale), round(window.winfo_screenwidth() * .9))
        height = min(round(520 * scale), round(window.winfo_screenheight() * .82))
        window.geometry(f"{width}x{height}")
        window.minsize(min(width, pixels(window, 530)), min(height, pixels(window, 320)))
        set_color_roles(window, background="WHITE")
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda _event: self.close())
        window.bind("<FocusIn>", self._focus_visible, add=True)
        window.bind("<MouseWheel>", self._wheel, add=True)
        window.bind("<Button-4>", self._wheel, add=True)
        window.bind("<Button-5>", self._wheel, add=True)
        shell = _frame(window)
        shell.pack(fill="both", expand=True, padx=pixels(window, 20), pady=pixels(window, 18))
        _label(shell, "倒计日", size=16, bold=True).pack(anchor="w", pady=(0, pixels(window, 14)))
        header = _frame(shell)
        header.pack(fill="x")
        _label(header, "事项名称").pack(side="left")
        _label(header, "目标日期  YYYY-MM-DD", muted=True).pack(side="right", padx=pixels(window, 100))
        footer = _frame(shell)
        footer.pack(side="bottom", fill="x", pady=(pixels(window, 12), 0))
        self.status = _label(shell, "", muted=True)
        self.status.pack(side="bottom", fill="x", pady=(pixels(window, 8), 0))
        self.add_button = _button(footer, "＋ 添加事项", self.add)
        self.add_button.pack(side="left")
        self.save_button = _button(footer, "保存", self.save, primary=True)
        self.save_button.pack(side="right")
        _button(footer, "取消", self.close).pack(side="right", padx=pixels(window, 8))
        host = _frame(shell)
        host.pack(fill="both", expand=True, pady=(pixels(window, 10), 0))
        scrollbar = tk.Scrollbar(host, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.canvas = ScrollCanvas(host, bd=0, highlightthickness=0, yscrollcommand=scrollbar.set)
        set_color_roles(self.canvas, background="WHITE")
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.canvas.yview)
        self.body = _frame(self.canvas)
        self.body_id = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.body_id, width=event.width), add=True)
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")), add=True)
        self.selected = tk.StringVar(window, chip.store.selected_key)
        try:
            chip.store.load()
            chip.error = ""
            self.selected.set(chip.store.selected_key)
        except (OSError, ValueError) as error:
            chip.error = str(error)
            self.status.configure(text=chip.error)
            self.add_button.configure(state="disabled")
            self.save_button.configure(state="disabled")
        for event in chip.store.events:
            self.add(event)
        if not self.rows and not chip.error:
            self.add()
        window.deiconify()
        window.lift()
        if self.rows:
            self.rows[0][4].focus_set()

    def _focus_visible(self, event):
        widget = event.widget
        while widget is not None and getattr(widget, "master", None) is not self.body:
            widget = getattr(widget, "master", None)
        if widget is None:
            return
        top = widget.winfo_y()
        bottom = top + widget.winfo_height()
        visible = self.canvas.canvasy(0)
        height = max(1, self.canvas.winfo_height())
        content_height = max(1, self.body.winfo_height())
        if top < visible:
            self.canvas.yview_moveto(top / content_height)
        elif bottom > visible + height:
            self.canvas.yview_moveto((bottom - height) / content_height)

    def _wheel(self, event):
        widget = event.widget
        while widget is not None and widget is not self.canvas:
            widget = getattr(widget, "master", None)
        if widget is None:
            return
        if getattr(event, "num", None) in (4, 5):
            units = -1 if event.num == 4 else 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
        self.canvas.yview_scroll(units, "units")
        return "break"

    def add(self, event=None):
        key = event.key if event is not None else CountdownEvent.create("新事项", date.today().isoformat()).key
        row = _frame(self.body)
        row.pack(fill="x", pady=(0, pixels(row, 10)))
        row.columnconfigure(0, weight=1)
        title = tk.StringVar(self.window, event.title if event is not None else "")
        target = tk.StringVar(self.window, event.target.isoformat() if event is not None else date.today().isoformat())
        title_entry = _entry(row, title, width=14)
        title_entry.grid(row=0, column=0, sticky="ew", padx=(0, pixels(row, 8)))
        _entry(row, target, width=12).grid(row=0, column=1, sticky="ew", padx=(0, pixels(row, 8)))
        select = _button(row, "展示", lambda: self.select(key))
        select.grid(row=0, column=2, padx=(0, pixels(row, 6)))
        _button(row, "删除", lambda: self.remove(key)).grid(row=0, column=3)
        title_entry.bind("<Return>", lambda _event: self.save())
        self.rows.append((key, title, target, row, title_entry, select))
        self._style_selection()
        if event is None:
            self.canvas.after_idle(lambda: self.canvas.yview_moveto(1) if self.canvas.winfo_exists() else None)
            title_entry.focus_set()

    def select(self, key):
        self.selected.set(key)
        self._style_selection()

    def _style_selection(self):
        for key, _title, _date, _row, _entry_widget, button in self.rows:
            selected = key == self.selected.get()
            button.configure(text="已展示" if selected else "展示")
            set_color_roles(button, background="ACCENT_DEEP" if selected else "ACCENT_SOFT",
                            foreground="ON_ACCENT" if selected else "INK")

    def remove(self, key):
        row = next((row for row in self.rows if row[0] == key), None)
        if row is not None:
            row[3].destroy()
            self.rows.remove(row)
        if self.selected.get() == key:
            self.selected.set("")
        self._style_selection()

    def save(self):
        if self.chip.error:
            return "break"
        events = []
        try:
            for key, title, target, _row, entry, _button_widget in self.rows:
                try:
                    events.append(CountdownEvent.create(title.get(), target.get(), key=key))
                except ValueError:
                    entry.focus_set()
                    raise
            self.chip.store.replace(events, self.selected.get())
        except (OSError, ValueError) as error:
            set_color_roles(self.status, foreground="ERROR")
            self.status.configure(text=str(error))
            return "break"
        self.chip.draw()
        self.close()
        return "break"

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()
        self.rows.clear()
        if self.chip.editor is self:
            self.chip.editor = None
