"""Calendar-led local journal: original Markdown and a shared safe preview.

THESIS: Find a day, then write; management never competes with the page.
OWN-WORLD: Existing RemChat blue-white / blue-grey, rounded shared surfaces.
STORY: Time and optional tags orient; editor, preview and save state stay clear.
FIRST VIEWPORT: Month + entries on the left, a full-height editor on the right.
FORM: The approved local calendar/list/editor brief extends Plaza's identity.
"""
from __future__ import annotations

import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import queue
import tkinter as tk
from .font_source import native_font
import tkinter.font as tkfont
from tkinter import ttk
from .localized_dialogs import filedialog, messagebox
from uuid import uuid4

from ..plaza.diary import (DiaryEntry, DiaryError, DiaryStore, MOODS,
                           export_collection, export_markdown, normalize_tags)
from ..text_editing import bind_text_editing
from . import theme
from .combobox_popup import AnchoredCombobox
from .dpi import pixels
from .diary_preview import DiaryPreview
from .diary_widgets import _frame, _label, _entry, _button, _Toolbar
from .settings_card import SettingsCard
from .theme_controller import set_color_roles
from .locale_controller import translator_for


_SAVE_ERRORS = (DiaryError, OSError, sqlite3.Error, UnicodeError)


def _busy(error):
    return isinstance(error, sqlite3.OperationalError) and getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _error_text(error):
    return "日记文件正忙，草稿已保留；请稍后保存" if _busy(error) else str(error)


class DiaryPanel(tk.Frame):
    """Independent Plaza module. Call ``on_hide`` before removing the page."""
    def __init__(self, parent, project_root: Path, *, on_changed=None):
        super().__init__(parent)
        set_color_roles(self, background="APP_BG")
        self.project_root = Path(project_root)
        self.on_changed = on_changed
        self._current = None
        self._dirty = self._loading = self._closed = False
        self._autosave_job = self._search_job = self._calendar_job = None
        self._selected_day = None
        self._month = date.today().replace(day=1)
        self._calendar_cells = []
        self._rows = {}
        self._store_error = ""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remchat-diary")
        self._results = queue.Queue()
        self._query_serial = 0
        self._query_future = None
        self._poll_job = None
        self._batch_busy = False
        self._edit_serial = 0
        self._save_future = None
        self._save_inflight = False
        self._save_retries = 0
        self._navigation_serial = 0
        self._responsive_job = None
        self._drawer_narrow = False
        self._drawer_page = "editor"
        self._properties_open = False
        self._calendar_navigation = False
        try:
            self.store = DiaryStore(self.project_root)
        except _SAVE_ERRORS as error:
            self.store = None
            self._store_error = str(error)
        self.search = tk.StringVar(self)
        self.tag_filter = tk.StringVar(self, "全部标签")
        self.mood_filter = tk.StringVar(self, "全部心情")
        self.trash_mode = tk.BooleanVar(self, False)
        self.title_var = tk.StringVar(self)
        self.day_var = tk.StringVar(self, date.today().isoformat())
        self.tags_var = tk.StringVar(self)
        self.mood_var = tk.StringVar(self)
        self.status = tk.StringVar(self, "尚未写入")
        self.list_status = tk.StringVar(self)
        self._build()
        self.bind("<Configure>", self._schedule_responsive, add=True)
        self.status.trace_add("write", self._status_changed)
        for variable in (self.title_var, self.day_var, self.tags_var, self.mood_var):
            variable.trace_add("write", self._metadata_changed)
        for variable in (self.search, self.tag_filter, self.mood_filter):
            variable.trace_add("write", self._search_changed)
        self.bind("<Destroy>", self._destroyed, add=True)
        self.bind("<<ThemeChanged>>", lambda _event: self.refresh_theme(), add=True)
        self._refresh_list()
        self._poll_job = self.after(40, self._poll_results)
        self._clear_editor()
        if self._store_error:
            self.status.set("日记库无法打开：" + self._store_error)

    def _build(self):
        self.columnconfigure(0, weight=0, minsize=pixels(self, 250))
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.sidebar_card = SettingsCard(self, stretch=True, inset=14)
        self.sidebar_card.configure(width=pixels(self, 250))
        self.sidebar_card.grid(row=0, column=0, sticky="nsew", padx=(pixels(self, 10), pixels(self, 8)), pady=pixels(self, 10))
        left = self.sidebar_card.body
        left.columnconfigure(0, weight=1)
        left.rowconfigure(6, weight=1)
        month_row = self.month_row = _frame(left)
        month_row.grid(row=0, column=0, sticky="ew")
        month_row.columnconfigure(1, weight=1)
        self.previous_month = _button(month_row, "‹", lambda: self._move_month(-1))
        self.previous_month.grid(row=0, column=0)
        self.month_label = _label(month_row, bold=True, size=12)
        self.month_label.grid(row=0, column=1, sticky="ew", padx=8)
        self.next_month = _button(month_row, "›", lambda: self._move_month(1))
        self.next_month.grid(row=0, column=2)
        self.drawer_calendar_button = _button(month_row, "日历", self._toggle_calendar_navigation)
        self.drawer_calendar_button.grid(row=0, column=3, padx=(8, 0))
        self.drawer_calendar_button.grid_remove()
        self.drawer_back_button = _button(month_row, "写作", self._show_editor)
        self.drawer_back_button.grid(row=0, column=4, padx=(8, 0))
        self.drawer_back_button.grid_remove()
        self.calendar = tk.Canvas(left, width=1, height=pixels(self, 184), bd=0,
                                  highlightthickness=0, takefocus=True)
        set_color_roles(self.calendar, background="WHITE")
        self.calendar.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        self.calendar.bind("<Configure>", self._schedule_calendar)
        self.calendar.bind("<Button-1>", self._calendar_click)
        self.calendar.bind("<Left>", lambda _event: self._calendar_key(-1))
        self.calendar.bind("<Right>", lambda _event: self._calendar_key(1))
        self.calendar.bind("<Up>", lambda _event: self._calendar_key(-7))
        self.calendar.bind("<Down>", lambda _event: self._calendar_key(7))
        quick = self.calendar_quick = _Toolbar(left)
        quick.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        quick.add("今天", self._today)
        quick.add("全部日期", self._all_days)
        self.month_summary = _label(left, muted=True, size=9)
        self.month_summary.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        search_row = self.search_row = _frame(left)
        search_row.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        search_row.columnconfigure(0, weight=1)
        self.search_label = _label(search_row, "搜索日记", bold=True)
        self.search_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.search_entry = _entry(search_row, self.search, width=1)
        self.search_entry.grid(row=1, column=0, sticky="ew", ipady=5)
        filters = self.filters = _frame(left)
        filters.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        filters.columnconfigure(0, weight=1)
        filters.columnconfigure(1, weight=1)
        self.tag_choice = AnchoredCombobox(filters, state="readonly", textvariable=self.tag_filter, width=1)
        self.tag_choice.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.mood_choice = AnchoredCombobox(filters, state="readonly", textvariable=self.mood_filter,
                                            values=("全部心情",) + MOODS[1:], width=1)
        self.mood_choice.grid(row=0, column=1, sticky="ew")
        list_frame = self.list_frame = _frame(left)
        list_frame.grid(row=6, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.entries = ttk.Treeview(list_frame, columns=("title",), show="tree", selectmode="browse", height=6)
        self.entries.column("#0", width=pixels(self, 70), minwidth=pixels(self, 58), stretch=False)
        self.entries.column("title", width=pixels(self, 150), minwidth=1, stretch=True)
        self.entries.configure(displaycolumns=("title",), show="tree")
        self.entries.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, command=self.entries.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.entries.configure(yscrollcommand=scroll.set)
        self.entries.bind("<<TreeviewSelect>>", self._selected)
        self.entries.bind("<Return>", self._selected)
        self.entries.bind("<ButtonRelease-1>", self._selected, add=True)
        result = _label(left, muted=True, size=9)
        result.configure(textvariable=self.list_status)
        result.grid(row=7, column=0, sticky="ew", pady=(6, 8))
        trash = self.trash_checkbox = tk.Checkbutton(left, text="回收站", variable=self.trash_mode, command=self._toggle_trash,
                               font=(theme.BODY_FONT, 10), relief="flat", bd=0)
        set_color_roles(trash, background="WHITE", foreground="INK", selectcolor="WHITE",
                        activebackground="WHITE", activeforeground="INK")
        trash.grid(row=8, column=0, sticky="w")
        self.drawer_status = _label(left, muted=True, size=9)
        self.drawer_status.configure(textvariable=self.status, justify="left")
        self.drawer_status.grid(row=9, column=0, sticky="ew", pady=(6, 0))
        self.drawer_status.grid_remove()
        self.drawer_status.bind("<Configure>", lambda event: self.drawer_status.configure(wraplength=max(80, event.width)))
        left.bind("<Configure>", self._sidebar_layout)
        self.editor_card = SettingsCard(self, stretch=True, inset=16)
        self.editor_card.grid(row=0, column=1, sticky="nsew", padx=(0, pixels(self, 10)), pady=pixels(self, 10))
        right = self.editor_card.body
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)
        self.right_body = right
        actions = self.actions = _Toolbar(right)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.navigation_button = actions.add("记录", self._show_navigation)
        self.new_button = actions.add("新日记", self.new_entry, primary=True)
        self.save_button = actions.add("保存", self.flush_pending)
        self.properties_button = actions.add("属性", self._toggle_properties)
        self.preview_button = actions.add("预览", self._toggle_preview)
        self.file_button = actions.add("导入 / 导出", self._file_menu)
        self.delete_button = actions.add("移入回收站", self._trash_current)
        self.title_entry = _entry(right, self.title_var, width=1)
        self.title_entry.configure(font=(theme.BODY_FONT, 17, "bold"))
        self.title_entry.grid(row=1, column=0, sticky="ew", ipady=pixels(self, 6), pady=(0, 12))
        metadata = self.metadata = _frame(right)
        metadata.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        metadata.columnconfigure(1, weight=1)
        _label(metadata, "日期").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.day_entry = _entry(metadata, self.day_var, width=12)
        self.day_entry.grid(row=0, column=1, sticky="w", ipady=4)
        self.mood_label = _label(metadata, "心情（可选）")
        self.mood_label.grid(row=0, column=2, sticky="w", padx=(12, 8))
        self.mood_entry = AnchoredCombobox(metadata, state="readonly", textvariable=self.mood_var, values=MOODS, width=6)
        self.mood_entry.grid(row=0, column=3, sticky="e")
        self.tag_label = _label(metadata, "标签")
        self.tag_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.tag_entry = _entry(metadata, self.tags_var, width=1)
        self.tag_entry.grid(row=1, column=1, columnspan=3, sticky="ew", ipady=4, pady=(8, 0))
        metadata.bind("<Configure>", self._metadata_layout)
        formats = self.formats = _Toolbar(right)
        formats.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        formats.add("粗体", lambda: self._wrap("**", "**"))
        formats.add("斜体", lambda: self._wrap("*", "*"))
        formats.add("标题", lambda: self._line_prefix("## "))
        formats.add("列表", lambda: self._line_prefix("- "))
        formats.add("待办", lambda: self._line_prefix("- [ ] "))
        formats.add("代码", lambda: self._wrap("```\n", "\n```"))
        self.notebook = self._make_editor_container(right)
        self.notebook.grid(row=4, column=0, sticky="nsew")
        editor_frame = _frame(self.notebook)
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)
        self.editor = tk.Text(editor_frame, width=1, height=1, wrap="word", undo=True,
                              relief="flat", bd=0, padx=pixels(self, 14), pady=pixels(self, 14),
                              font=(theme.BODY_FONT, 11), spacing3=5, autoseparators=True, maxundo=200)
        self.editor._remchat_locale_exclude = True
        set_color_roles(self.editor, background="APP_BG", foreground="INK", insertbackground="INK",
                        selectbackground="ACCENT_SOFT", selectforeground="INK")
        self.editor.grid(row=0, column=0, sticky="nsew")
        editor_scroll = ttk.Scrollbar(editor_frame, command=self.editor.yview)
        editor_scroll.grid(row=0, column=1, sticky="ns")
        self.editor.configure(yscrollcommand=editor_scroll.set)
        self.editor.bind("<<Modified>>", self._text_changed)
        self.editor.bind("<Control-s>", lambda _event: self._save_key())
        self.editor.bind("<Control-b>", lambda _event: self._wrap("**", "**"))
        self.editor.bind("<Control-i>", lambda _event: self._wrap("*", "*"))
        bind_text_editing(self.editor)
        self.preview = DiaryPreview(self.notebook)
        self.notebook.add(editor_frame, text="编辑 Markdown")
        self.notebook.add(self.preview, text="预览")
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        status = _label(right, muted=True, size=9)
        status.configure(textvariable=self.status, justify="left")
        status.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        status.bind("<Configure>", lambda event: status.configure(wraplength=max(80, event.width)))
        self._apply_list_style()
        self._schedule_responsive()

    def _make_editor_container(self, parent):
        return ttk.Notebook(parent)

    def _apply_list_style(self):
        font = native_font(self,font=(theme.BODY_FONT,10))
        style = "Diary" + str(id(self)) + ".Treeview"
        ttk.Style(self).configure(style, rowheight=max(pixels(self, 34), font.metrics("linespace") + pixels(self, 12)),
                                  font=(theme.BODY_FONT, 10))
        self.entries.configure(style=style)

    def _metadata_layout(self, event):
        day_label = native_font(self,font=(theme.BODY_FONT,10)).measure("日期")
        required = day_label + self.day_entry.winfo_reqwidth() + self.mood_label.winfo_reqwidth() + self.mood_entry.winfo_reqwidth() + pixels(self, 38)
        narrow = event.width < required
        if getattr(self, "_narrow_metadata", None) == narrow:
            return
        self._narrow_metadata = narrow
        self.mood_label.grid(row=1 if narrow else 0, column=0 if narrow else 2,
                             sticky="w", padx=(0 if narrow else 12, 8), pady=(8 if narrow else 0, 0))
        self.mood_entry.grid(row=1 if narrow else 0, column=1 if narrow else 3,
                             sticky="w" if narrow else "e", pady=(8 if narrow else 0, 0))
        self.tag_label.grid(row=2 if narrow else 1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.tag_entry.grid(row=2 if narrow else 1, column=1, columnspan=3, sticky="ew", ipady=4, pady=(8, 0))

    def _sidebar_layout(self, event):
        if self._drawer_narrow:
            self._layout_drawer_navigation()
            return
        compact = event.height < pixels(self, 540)
        if getattr(self, "_compact_sidebar", None) == compact:
            return
        self._compact_sidebar = compact
        self.calendar.configure(height=pixels(self, 116 if compact else 184))
        for widget in (self.calendar_quick, self.month_summary, self.search_label, self.trash_checkbox):
            widget.grid_remove() if compact else widget.grid()
        self._schedule_calendar()

    def _schedule_responsive(self, _event=None):
        if self._responsive_job is None and not self._closed:
            self._responsive_job = self.after_idle(self._responsive_layout)

    def _responsive_layout(self):
        self._responsive_job = None
        if self._closed or not self.winfo_exists():
            return
        width, height = self.winfo_width(), self.winfo_height()
        narrow = width < pixels(self, 850)
        short = height < pixels(self, 500)
        self._drawer_narrow = narrow
        self.actions.show(self.navigation_button, narrow)
        self.actions.show(self.properties_button, narrow and short)
        self.actions.show(self.preview_button, narrow and short)
        self.actions.show(self.delete_button, not (narrow and short))
        self.new_button.configure(text="新建" if narrow and short else "新日记")
        self.file_button.configure(text="更多" if narrow and short else "导入 / 导出")
        self.properties_button.configure(text="写作" if self._properties_open else "属性")
        self.editor_card._inset = 8 if narrow and short else 16
        self.editor_card.refresh()
        self.title_entry.configure(font=(theme.BODY_FONT, 12 if narrow and short else 17, "bold"))
        self.title_entry.grid_configure(ipady=pixels(self, 3 if narrow and short else 6), pady=(0, 6 if narrow and short else 12))
        if narrow:
            self.columnconfigure(0, weight=1, minsize=0)
            self.columnconfigure(1, weight=0, minsize=0)
            if self._drawer_page == "navigation":
                self.editor_card.grid_remove()
                self.sidebar_card.grid(row=0, column=0, columnspan=2, sticky="nsew",
                                       padx=pixels(self, 10), pady=pixels(self, 10))
                self._layout_drawer_navigation()
            else:
                self.sidebar_card.grid_remove()
                self.editor_card.grid(row=0, column=0, columnspan=2, sticky="nsew",
                                      padx=pixels(self, 10), pady=pixels(self, 10))
        else:
            self.columnconfigure(0, weight=0, minsize=pixels(self, 250))
            self.columnconfigure(1, weight=1, minsize=0)
            self.sidebar_card.grid(row=0, column=0, columnspan=1, sticky="nsew",
                                   padx=(pixels(self, 10), pixels(self, 8)), pady=pixels(self, 10))
            self.editor_card.grid(row=0, column=1, columnspan=1, sticky="nsew",
                                  padx=(0, pixels(self, 10)), pady=pixels(self, 10))
            self.drawer_back_button.grid_remove()
            self.drawer_calendar_button.grid_remove()
            self.previous_month.grid()
            self.next_month.grid()
            self.calendar.grid()
            self.search_row.grid()
            self.filters.grid()
            self.list_frame.grid()
            self.sidebar_card.body.rowconfigure(1, weight=0)
            self.sidebar_card.body.rowconfigure(6, weight=1)
            self.search_row.columnconfigure(0, weight=1)
            self.search_row.columnconfigure(1, weight=0)
            self.search_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
            self.search_entry.grid(row=1, column=0, sticky="ew", ipady=5)
            self._compact_sidebar = None
            self._sidebar_layout(type("Size", (), {"height": self.sidebar_card.body.winfo_height()})())
        compact_editor = narrow and short
        compact_style = "DiaryCompact" + str(id(self)) + ".TNotebook"
        style = ttk.Style(self)
        style_key = (style.theme_use(), compact_editor)
        if getattr(self, "_notebook_style_key", None) != style_key:
            # ttk style edits emit ThemeChanged for descendants. Publish the
            # key first so that callback cannot schedule an endless restyle.
            self._notebook_style_key = style_key
            if compact_editor:
                style.layout(compact_style + ".Tab", [])
            self.notebook.configure(style=compact_style if compact_editor else "TNotebook")
        self.editor.configure(padx=pixels(self, 10 if compact_editor else 14),
                              pady=pixels(self, 8 if compact_editor else 14))
        self.metadata.grid() if (not compact_editor or self._properties_open) else self.metadata.grid_remove()
        self.formats.grid() if not compact_editor else self.formats.grid_remove()
        self.notebook.grid_remove() if (compact_editor and self._properties_open) else self.notebook.grid()
        self._status_changed()
        self._schedule_calendar()

    def _layout_drawer_navigation(self):
        left = self.sidebar_card.body
        calendar_mode = self._calendar_navigation
        self.drawer_calendar_button.configure(text="记录" if calendar_mode else "日历")
        self.drawer_calendar_button.grid()
        self.drawer_back_button.grid()
        for widget in (self.calendar_quick, self.month_summary, self.search_label, self.trash_checkbox):
            widget.grid_remove()
        self.previous_month.grid() if calendar_mode else self.previous_month.grid_remove()
        self.next_month.grid() if calendar_mode else self.next_month.grid_remove()
        self.calendar.grid() if calendar_mode else self.calendar.grid_remove()
        for widget in (self.search_row, self.filters, self.list_frame):
            widget.grid_remove() if calendar_mode else widget.grid()
        left.rowconfigure(1, weight=1 if calendar_mode else 0)
        left.rowconfigure(6, weight=0 if calendar_mode else 1)
        if calendar_mode:
            remaining = left.winfo_height() - self.month_row.winfo_reqheight() - pixels(self, 34)
            self.calendar.configure(height=max(pixels(self, 70), remaining))
        else:
            self.search_row.columnconfigure(0, weight=0)
            self.search_row.columnconfigure(1, weight=1)
            self.search_label.grid(row=0, column=0, sticky="w", pady=0, padx=(0, 8))
            self.search_entry.grid(row=0, column=1, sticky="ew", ipady=5)
        self._status_changed()
        self._schedule_calendar()

    def _show_navigation(self):
        self._drawer_page = "navigation"
        self._navigation_serial += 1
        self._properties_open = False
        self._schedule_responsive()

    def _show_editor(self):
        self._drawer_page = "editor"
        self._navigation_serial += 1
        self._properties_open = False
        self._schedule_responsive()

    def _toggle_properties(self):
        self._properties_open = not self._properties_open
        self._navigation_serial += 1
        self._schedule_responsive()

    def _toggle_preview(self):
        self._properties_open = False
        self.notebook.select(0 if self.notebook.index(self.notebook.select()) == 1 else 1)
        self._schedule_responsive()

    def _toggle_calendar_navigation(self):
        self._calendar_navigation = not self._calendar_navigation
        self._layout_drawer_navigation()

    def _status_changed(self, *_arguments):
        if not hasattr(self, "drawer_status"):
            return
        show = (self._drawer_narrow and self._drawer_page == "navigation"
                and (self._dirty or self.status.get().startswith(("未保存", "正在保存", "无法"))))
        self.drawer_status.grid() if show else self.drawer_status.grid_remove()

    def _clear_editor(self, *, day=None):
        self._navigation_serial += 1
        self._loading = True
        self._current = None
        self.title_var.set("")
        self.day_var.set(day or date.today().isoformat())
        self.tags_var.set("")
        self.mood_var.set("")
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self._dirty = False
        self._loading = False
        self.preview.set_text("")
        self.status.set("开始写点什么吧" if self.store else "日记库无法打开：" + self._store_error)
        self.delete_button.configure(state="disabled")
        self._metadata_state(False)

    def _metadata_state(self, readonly):
        for widget in (self.title_entry, self.day_entry, self.tag_entry):
            widget.configure(state="readonly" if readonly else "normal")
        self.mood_entry.configure(state="disabled" if readonly else "readonly")
        self.save_button.configure(state="disabled" if readonly else "normal")

    def _load_entry(self, entry):
        self._navigation_serial += 1
        self._loading = True
        self._current = entry
        if self.entries.exists(entry.id):
            self.entries.selection_set(entry.id)
        else:
            self.entries.selection_remove(self.entries.selection())
        self.title_var.set(entry.title)
        self.day_var.set(entry.day)
        self.tags_var.set(", ".join(entry.tags))
        self.mood_var.set(entry.mood)
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", entry.body)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.editor.configure(state="disabled" if entry.deleted_at else "normal")
        self._dirty = False
        self._loading = False
        self.status.set("已移入回收站" if entry.deleted_at else "已保存 · " + entry.updated_at[:19].replace("T", " "))
        self.delete_button.configure(text="恢复日记" if entry.deleted_at else "移入回收站", state="normal")
        self._metadata_state(bool(entry.deleted_at))
        self.preview.set_text(entry.body)
        self._show_editor()

    def new_entry(self):
        if not self.flush_pending():
            return False
        self.entries.selection_remove(self.entries.selection())
        self._clear_editor(day=self._selected_day)
        self.notebook.select(0)
        self.title_entry.focus_set()
        self._show_editor()
        return True

    def _metadata_changed(self, *_arguments):
        if not self._loading and not self._closed:
            self._mark_dirty()

    def _text_changed(self, _event=None):
        if self.editor.edit_modified():
            self.editor.edit_modified(False)
            if not self._loading:
                self._mark_dirty()

    def _mark_dirty(self):
        if self._current and self._current.deleted_at:
            return
        self._dirty = True
        self._edit_serial += 1
        self._save_retries = 0
        self.status.set("待保存…")
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
        self._autosave_job = self.after(700, self._autosave)

    def _autosave(self):
        self._autosave_job = None
        if not self._dirty or self._save_inflight or self.store is None:
            return
        try:
            draft = self._draft()
            if self._current is None and not (draft.title.strip() or draft.body.strip() or draft.tags or draft.mood):
                self._dirty = False
                self.status.set("尚未写入")
                return
        except _SAVE_ERRORS as error:
            self.status.set("未保存 · " + str(error))
            return
        original_id = self._current.id if self._current else None
        expected = self._current.revision if self._current else 0
        serial = self._edit_serial
        self._save_inflight = True
        self.status.set("保存中…")
        def save():
            try:
                entry = self.store.save(day=draft.day, title=draft.title, body=draft.body, tags=draft.tags,
                                        mood=draft.mood, entry_id=original_id, expected_revision=expected, rich=draft.rich)
                self._results.put(("save", serial, original_id, entry, None))
            except _SAVE_ERRORS as error:
                self._results.put(("save", serial, original_id, None, error))
        self._save_future = self._executor.submit(save)

    def _draft(self):
        current = self._current
        return DiaryEntry(id=current.id if current else uuid4().hex, day=self.day_var.get(),
                          title=self.title_var.get(), body=self.editor.get("1.0", "end-1c"),
                          tags=normalize_tags(self.tags_var.get()), mood=self.mood_var.get(),
                          revision=current.revision if current else 0)

    def flush_pending(self) -> bool:
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self._save_inflight:
            if self._save_future is not None and self._save_future.cancel():
                self._save_inflight = False
            else:
                self.status.set("正在保存，请稍后再切换")
                return False
        if not self._dirty:
            return True
        if self.store is None:
            self.status.set("日记库无法打开，草稿保留在编辑器；可先导出")
            return False
        try:
            draft = self._draft()
            # No empty record is silently created on merely opening a day.
            if self._current is None and not (draft.title.strip() or draft.body.strip() or draft.tags or draft.mood):
                self._dirty = False
                self.status.set("尚未写入")
                return True
            entry = self.store.save(day=draft.day, title=draft.title, body=draft.body,
                                    tags=draft.tags, mood=draft.mood,
                                    entry_id=self._current.id if self._current else None,
                                    expected_revision=self._current.revision if self._current else 0, rich=draft.rich)
        except _SAVE_ERRORS as error:
            self.status.set("未保存 · " + _error_text(error))
            return False
        self._current = entry
        self._dirty = False
        self.status.set("已保存 · " + entry.updated_at[11:19])
        self.delete_button.configure(text="移入回收站", state="normal")
        self._refresh_list(keep=entry.id)
        if self.on_changed:
            self.on_changed()
        return True

    def _save_key(self):
        self.flush_pending()
        return "break"

    def _selected(self, _event=None):
        if (_event is not None and _event.type == tk.EventType.ButtonRelease
                and not self.entries.identify_row(_event.y)):
            return
        selection = self.entries.selection()
        if self._loading or not selection:
            return
        entry_id = selection[0]
        if self._current and self._current.id == entry_id:
            if self._drawer_narrow and self._drawer_page == "navigation":
                self._show_editor()
            return
        if not self.flush_pending():
            self._loading = True
            if self._current and self.entries.exists(self._current.id):
                self.entries.selection_set(self._current.id)
            else:
                self.entries.selection_remove(selection)
            self._loading = False
            return
        try:
            entry = self.store.get(entry_id) if self.store else None
        except _SAVE_ERRORS as error:
            self.status.set("无法打开日记 · " + str(error))
            return
        if entry:
            self._load_entry(entry)

    def _search_changed(self, *_arguments):
        if self._loading or self._closed:
            return
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(180, self._refresh_list)

    def _refresh_list(self, *, keep=None):
        self._search_job = None
        if self.store is None or self._closed:
            self.list_status.set("日记库暂不可用")
            return
        self._query_serial += 1
        serial = self._query_serial
        options = dict(text=self.search.get(), day=self._selected_day,
                       tag="" if self.tag_filter.get() == "全部标签" else self.tag_filter.get(),
                       mood="" if self.mood_filter.get() == "全部心情" else self.mood_filter.get(),
                       deleted=self.trash_mode.get(), summaries=True)
        options["cancelled"] = lambda: self._closed or serial != self._query_serial
        self.list_status.set("读取中…")
        def read():
            try:
                rows = self.store.query(**options)
                tags = self.store.tags(deleted=options["deleted"])
                self._results.put(("query", serial, keep, rows, tags, None))
            except _SAVE_ERRORS as error:
                self._results.put(("query", serial, keep, None, None, error))
        if self._query_future is not None:
            self._query_future.cancel()
        self._query_future = self._executor.submit(read)
        self._schedule_calendar()

    def _publish_list(self, rows, tags, keep):
        self._rows = {row.id: row for row in rows}
        old_scroll = self.entries.yview()[0] if self.entries.get_children() else 0
        self._loading = True
        self.entries.delete(*self.entries.get_children())
        for row in rows:
            title = row.title.strip() or "无标题"
            self.entries.insert("", "end", iid=row.id, text=row.day[5:], values=(title,))
        # A queued search result may arrive after the user switched entries or
        # began a new draft. The live editor wins over request-time selection.
        selected = self._current.id if self._current else None
        if selected and selected in self._rows:
            self.entries.selection_set(selected)
        self.entries.yview_moveto(old_scroll)
        self.tag_choice.configure(values=("全部标签",) + tags)
        self._loading = False
        prefix = "回收站 · " if self.trash_mode.get() else ""
        self.list_status.set(prefix + (f"{len(rows):,} 篇" if rows else "没有匹配的日记"))
        self._schedule_calendar()

    def _poll_results(self):
        self._poll_job = None
        if self._closed:
            return
        try:
            while True:
                result = self._results.get_nowait()
                if result[0] == "query":
                    _kind, serial, keep, rows, tags, error = result
                    if serial != self._query_serial:
                        continue
                    if error:
                        self.list_status.set("无法读取 · " + _error_text(error))
                    else:
                        self._publish_list(rows, tags, keep)
                elif result[0] == "save":
                    _kind, serial, original_id, entry, error = result
                    self._save_inflight = False
                    if error:
                        if _busy(error) and self._save_retries < 3:
                            self._save_retries += 1
                            self.status.set("日记文件正忙，稍后重试…")
                            if self._autosave_job is None:
                                self._autosave_job = self.after(250, self._autosave)
                        else:
                            self.status.set("未保存 · " + _error_text(error))
                        continue
                    self._save_retries = 0
                    current_id = self._current.id if self._current else None
                    if current_id != original_id:
                        continue
                    self._current = entry
                    if serial == self._edit_serial:
                        self._dirty = False
                        self.status.set("已保存 · " + entry.updated_at[11:19])
                    else:
                        self.status.set("待保存…")
                        if self._autosave_job is None:
                            self._autosave_job = self.after(200, self._autosave)
                    self.delete_button.configure(text="移入回收站", state="normal")
                    self._refresh_list(keep=entry.id)
                    if self.on_changed:
                        self.on_changed()
                elif result[0] == "export":
                    self._batch_busy = False
                    _kind, count, folder, error = result
                    self.status.set("导出失败 · " + error if error else f"已导出 {count} 篇到 {folder}")
                elif result[0] == 'journal-search' and hasattr(self, '_publish_search'):
                    self._publish_search(*result[1:])
                elif result[0] == 'timeline' and hasattr(self, '_publish_timeline'):
                    _kind, serial, records, error = result
                    if serial==self._query_serial:
                        if error: self.status.set('时间线无法读取 · '+_error_text(error))
                        else: self._publish_timeline(records)
                elif result[0] == 'media' and hasattr(self, '_publish_media'):
                    self._publish_media(*result[1:])
                elif result[0] == 'thumb' and hasattr(self, '_publish_thumb'):
                    self._publish_thumb(*result[1:])
                elif result[0] == "import":
                    self._batch_busy = False
                    _kind, succeeded, errors, last, context = result
                    self._refresh_list()
                    self.status.set(f"已导入 {succeeded} 篇" + (f" · {len(errors)} 个文件未导入" if errors else ""))
                    # Typing while the import runs is never replaced by the
                    # imported last item. It remains an independent draft.
                    if (last and not self._dirty and not self.trash_mode.get()
                            and context == (self._navigation_serial, self._edit_serial)):
                        self._load_entry(last)
                    if succeeded and self.on_changed:
                        self.on_changed()
                    if errors:
                        messagebox.showerror("部分文件未导入", "\n".join(errors[:12]), parent=self)
        except queue.Empty:
            pass
        self._poll_job = self.after(40, self._poll_results)

    def _move_month(self, amount):
        month = self._month.month - 1 + amount
        year = self._month.year + month // 12
        if not 1 <= year <= 9999:
            return
        self._month = date(year, month % 12 + 1, 1)
        self._schedule_calendar()

    def _today(self):
        self._month = date.today().replace(day=1)
        self._selected_day = date.today().isoformat()
        self._refresh_list()

    def _all_days(self):
        self._selected_day = None
        self._refresh_list()

    def _schedule_calendar(self, _event=None):
        if self._calendar_job is None and not self._closed:
            self._calendar_job = self.after_idle(self._draw_calendar)

    def _draw_calendar(self):
        self._calendar_job = None
        if not self.winfo_exists():
            return
        self.month_label.configure(text=("日记记录" if self._drawer_narrow and not self._calendar_navigation
                                         else f"{self._month.year} 年 {self._month.month} 月"))
        try:
            counts = self.store.month_counts(self._month.year, self._month.month, deleted=self.trash_mode.get()) if self.store else {}
        except _SAVE_ERRORS:
            counts = None
        if self.store is None:
            counts = None
        self.month_summary.configure(text=(f"本月 {sum(counts.values())} 篇 · {len(counts)} 天"
                                           if counts is not None else "本月记录暂不可读取"))
        known_counts = counts or {}
        self.calendar.delete("all")
        colors = theme.palette()
        width = max(1, self.calendar.winfo_width())
        height = max(1, self.calendar.winfo_height())
        rows = calendar.Calendar(firstweekday=0).monthdayscalendar(self._month.year, self._month.month)
        column_width, row_height = width / 7, height / (len(rows) + 1)
        self._calendar_cells = []
        from ..i18n import weekday_labels
        for column, label in enumerate(weekday_labels(translator_for(self).language, compact=True)):
            self.calendar.create_text((column + .5) * column_width, row_height / 2, text=label,
                                      font=(theme.BODY_FONT, 9), fill=colors["MUTED"])
        for row, week in enumerate(rows, 1):
            for column, day in enumerate(week):
                if not day:
                    continue
                value = f"{self._month.year:04d}-{self._month.month:02d}-{day:02d}"
                x1, y1 = column * column_width + 2, row * row_height + 1
                x2, y2 = (column + 1) * column_width - 2, (row + 1) * row_height - 1
                fill = colors["ACCENT_SOFT"] if value in known_counts else colors["WHITE"]
                if value == self._selected_day:
                    fill = colors["ACCENT_DEEP"]
                radius = max(1, min(pixels(self, 5), (x2 - x1) / 3))
                points = (x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,
                          x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1)
                self.calendar.create_polygon(points, smooth=True, splinesteps=12, fill=fill, outline="")
                self.calendar.create_text((x1+x2)/2, (y1+y2)/2-2, text=str(day), font=(theme.BODY_FONT, 9),
                                         fill=colors["ON_ACCENT"] if value == self._selected_day else colors["INK"])
                if value in known_counts:
                    cx, cy = (x1+x2)/2, y2-4
                    self.calendar.create_oval(cx-1.5, cy-1.5, cx+1.5, cy+1.5,
                                              fill=colors["ON_ACCENT"] if value == self._selected_day else colors["ACCENT_DEEP"], outline="")
                if value == date.today().isoformat():
                    self.calendar.create_line(x1+radius, y2-1, x2-radius, y2-1, fill=colors["ACCENT"], width=1)
                self._calendar_cells.append((x1, y1, x2, y2, value))

    def _calendar_click(self, event):
        self.calendar.focus_set()
        for x1, y1, x2, y2, value in self._calendar_cells:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self._selected_day = None if self._selected_day == value else value
                self._refresh_list()
                if self._drawer_narrow:
                    self._calendar_navigation = False
                    self._layout_drawer_navigation()
                break

    def _calendar_key(self, amount):
        value = date.fromisoformat(self._selected_day) if self._selected_day else self._month
        try:
            value += timedelta(days=amount)
        except OverflowError:
            return "break"
        self._selected_day = value.isoformat()
        self._month = value.replace(day=1)
        self._refresh_list()
        return "break"

    def _toggle_trash(self):
        if not self.flush_pending():
            self.trash_mode.set(not self.trash_mode.get())
            return
        self._refresh_list()
        self._clear_editor(day=self._selected_day)
        if self._drawer_narrow:
            self._show_navigation()

    def _trash_current(self):
        if not self.store or not self._current or not self.flush_pending():
            return
        deleted = not bool(self._current.deleted_at)
        try:
            self.store.set_deleted(self._current.id, deleted=deleted, expected_revision=self._current.revision)
        except _SAVE_ERRORS as error:
            self.status.set("未改变 · " + str(error))
            return
        self._clear_editor(day=self._selected_day)
        self._refresh_list()
        self.status.set("已移入回收站，可恢复" if deleted else "已恢复日记")
        if self.on_changed:
            self.on_changed()

    def _tab_changed(self, _event=None):
        self.preview_button.configure(text="编辑" if self.notebook.index(self.notebook.select()) == 1 else "预览")
        if self.notebook.index(self.notebook.select()) == 1:
            self.preview.set_text(self.editor.get("1.0", "end-1c"))

    def _wrap(self, before, after):
        if self.editor.cget("state") != "normal":
            return "break"
        try:
            start, end = self.editor.index("sel.first"), self.editor.index("sel.last")
        except tk.TclError:
            start = end = self.editor.index("insert")
        self.editor.edit_separator()
        self.editor.insert(end, after)
        self.editor.insert(start, before)
        self.editor.edit_separator()
        self.editor.focus_set()
        self.editor.see("insert")
        return "break"

    def _line_prefix(self, prefix):
        if self.editor.cget("state") == "normal":
            self.editor.insert("insert linestart", prefix)
            self.editor.focus_set()
        return "break"

    def _import(self):
        if not self.store or self._batch_busy or not self.flush_pending():
            return
        paths = filedialog.askopenfilenames(parent=self, title="导入 Markdown 日记", filetypes=(("Markdown", "*.md *.markdown"),))
        if not paths:
            return
        self._batch_busy = True
        self.status.set("正在导入…")
        context = (self._navigation_serial, self._edit_serial)
        def import_files():
            succeeded, errors, last = 0, [], None
            for path in paths:
                if self._closed:
                    break
                try:
                    last = self.store.import_markdown(Path(path))
                    succeeded += 1
                except _SAVE_ERRORS as error:
                    errors.append(Path(path).name + "：" + str(error))
            self._results.put(("import", succeeded, errors, last, context))
        self._executor.submit(import_files)

    def _file_menu(self):
        menu = tk.Menu(self, tearoff=False, font=(theme.BODY_FONT, 10))
        menu.add_command(label="导入 Markdown", command=self._import)
        menu.add_command(label="导出当前（含草稿）", command=self._export)
        menu.add_command(label="导出全部", command=self._export_all)
        menu.add_command(label="恢复日记" if self._current and self._current.deleted_at else "移入回收站",
                         command=self._trash_current, state="normal" if self._current else "disabled")
        formats = tk.Menu(menu, tearoff=False, font=(theme.BODY_FONT, 10))
        for label, command in (("粗体", lambda: self._wrap("**", "**")), ("斜体", lambda: self._wrap("*", "*")),
                               ("标题", lambda: self._line_prefix("## ")), ("列表", lambda: self._line_prefix("- ")),
                               ("待办", lambda: self._line_prefix("- [ ] ")), ("代码", lambda: self._wrap("```\n", "\n```"))):
            formats.add_command(label=label, command=command)
        menu.add_cascade(label="Markdown 格式", menu=formats)
        menu.add_separator()
        menu.add_command(label="今天", command=self._today)
        menu.add_command(label="全部日期", command=self._all_days)
        menu.add_checkbutton(label="回收站", variable=self.trash_mode, command=self._toggle_trash)
        try:
            menu.tk_popup(self.file_button.winfo_rootx(), self.file_button.winfo_rooty() + self.file_button.winfo_height())
        finally:
            menu.grab_release()

    def _export(self):
        try:
            entry = self._draft()
        except _SAVE_ERRORS as error:
            self.status.set("无法导出草稿 · " + str(error))
            return
        path = filedialog.asksaveasfilename(parent=self, title="导出当前 Markdown（含草稿）", defaultextension=".md",
                                           initialfile=entry.day + ".md", filetypes=(("Markdown", "*.md"),))
        if not path:
            return
        try:
            export_markdown(entry, Path(path), overwrite=True)  # Native Save dialog confirms replacing a file.
        except _SAVE_ERRORS as error:
            self.status.set("导出失败 · " + str(error))
            return
        self.status.set("已导出当前 Markdown" + (" · 草稿尚未写入日记库" if self._dirty else ""))

    def _export_all(self):
        if not self.store or self._batch_busy or not self.flush_pending():
            return
        directory = filedialog.askdirectory(parent=self, title="导出全部日记到新文件夹")
        if not directory:
            return
        deleted = self.trash_mode.get()
        self._batch_busy = True
        self.status.set("正在导出…")
        def export_files():
            try:
                # Trash remains separate and is not mixed into exports.
                count = 0
                def entries():
                    nonlocal count
                    for entry in self.store.iter_entries(deleted=deleted, cancelled=lambda: self._closed):
                        count += 1
                        yield entry
                folder = export_collection(entries(), Path(directory), cancelled=lambda: self._closed)
                self._results.put(("export", count, folder.name, None))
            except _SAVE_ERRORS as error:
                self._results.put(("export", 0, "", str(error)))
        self._executor.submit(export_files)

    def on_hide(self) -> bool:
        return self.flush_pending()

    def close(self) -> bool:
        """App-owned shutdown after a successful flush; no queued IO continues."""
        if self._closed:
            return True
        if not self.flush_pending():
            return False
        self._stop_jobs()
        return True

    def on_show(self):
        self._refresh_list()
        self.refresh_theme()

    def refresh_theme(self):
        if not self._closed:
            self._apply_list_style()
            self._schedule_calendar()
            self.preview.refresh_theme()
            self._schedule_responsive()

    def _destroyed(self, event):
        if event.widget is self:
            self._stop_jobs()

    def _stop_jobs(self):
        self._closed = True
        for job in (self._autosave_job, self._search_job, self._calendar_job, self._poll_job, self._responsive_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._autosave_job = self._search_job = self._calendar_job = self._poll_job = None
        self._responsive_job = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self.store is not None:
            self.store.close()

    def destroy(self):
        # Parent must honour on_hide()'s False to let the user recover a failed
        # save. Direct callers still get the final successful draft flush.
        if not self._closed and self.winfo_exists():
            self.flush_pending()
        super().destroy()
