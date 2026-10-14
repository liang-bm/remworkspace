"""THESIS: Choose a tool/day/session, then follow its actual public journey.
OWN-WORLD: Existing RemChat blue-white / blue-gray, native labelled controls.
STORY: Read the actual goal and project instructions, prompts and measured use.
FIRST VIEWPORT: Tool | date | session; context cards; prompt archive / usage.
FORM: User-specified desktop diagram, no concept roll or new visual world.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from .font_source import native_font
from .soft_fields import SoftTtkEntry
from tkinter import ttk
from tkinter import font as tkfont

from ..project_trace.session_journey import SessionJourney, local_day, public
from ..text_editing import bind_text_editing
from . import theme
from .theme_controller import set_color_roles
from .selection_feedback import Tooltip
from .token_activity import TokenActivity
from .combobox_popup import AnchoredCombobox
from .settings_card import SettingsCard
from .dpi import pixels
from .scroll_surface import ScrollCanvas

CATEGORIES = {"command": "命令", "mcp": "MCP", "skill": "Skill", "other": "工具"}
METER_SECTIONS = {"activity": "Token 活动", "tools": "工具 / MCP / Skill", "subagents": "附属子 Agent 活动"}


class SessionProcessPanel(tk.Frame):
    """Render manager-owned native observations; Tk never scans or owns sync."""
    def __init__(self, parent, owner):
        super().__init__(parent)
        set_color_roles(self, background="APP_BG")
        self.owner, self.manager = owner, owner.integration_manager
        translator = getattr(owner, "translator", lambda value: value)
        self.tr = getattr(translator, "display", translator)
        self.service = SessionJourney(self.manager, getattr(owner.service, "memory", None))
        self.closed, self.paused = False, True
        self._stop = threading.Event()
        self._requests, self._results = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
        self._events = queue.Queue(maxsize=1)
        self._busy = False
        self._worker_thread = None
        self._generation = 0
        self._after = self._auto = self._resize_after = self._search_after = None
        self._tools = self.service.tools()  # Catalog metadata only, no scan.
        self._sessions, self._data = [], None
        available = {row["id"] for row in self._tools if row.get("supported")}
        saved = self.manager.list_tool_connections()
        preferred = next((row.get("tool_id") for row in saved if row.get("tool_id") in available and row.get("state") == "connected"), None)
        self._key, self._tool_id, self._mode = "", preferred or ("codex" if "codex" in available else "remchat"), theme.get_mode()
        self._prompt_rows = []
        self._prompt_newest_first = False
        self._header_pairs = []
        self._layout_key = None
        self._instruction_rows = []
        self._dialogs = []
        self._meter_preferences_path = Path(self.manager.root) / "data" / "ui" / "session-process.json"
        self._meter_visible = self._read_meter_preferences()
        self._meter_settings_open = False
        self.day = tk.StringVar(self, "全部日期")
        self.follow_latest = tk.BooleanVar(self, True)
        self.query = tk.StringVar(self, "")
        self.status = tk.StringVar(self, "本地同步初始化中")
        self._sync_status = {}
        self._build()
        self._unsubscribe = self.manager.subscribe(self._native_event)
        self.bind("<Destroy>", self._destroyed, add="+")

    def _frame(self, parent):
        widget = tk.Frame(parent)
        set_color_roles(widget, background=getattr(parent, "_remchat_color_roles", {}).get("background", "APP_BG"))
        return widget

    def _label(self, parent, text="", *, bold=False, muted=False, variable=None):
        widget = tk.Label(parent, text=self.tr(text), textvariable=variable, anchor="w", justify="left",
                          wraplength=420, font=(theme.BODY_FONT, 10, "bold" if bold else "normal"))
        set_color_roles(widget, background=getattr(parent, "_remchat_color_roles", {}).get("background", "APP_BG"), foreground="MUTED" if muted else "INK")
        return widget

    def _button(self, parent, text, command):
        widget = tk.Button(parent, text=self.tr(text), command=command, font=(theme.BODY_FONT, 10),
                           relief="flat", padx=10, pady=5, takefocus=True, highlightthickness=1)
        set_color_roles(widget, background="ACCENT_SOFT", foreground="INK", activebackground="ACCENT_SOFT",
                        activeforeground="INK", disabledforeground="MUTED", highlightbackground="LINE", highlightcolor="ACCENT")
        return widget

    def _text(self, parent, height):
        widget = tk.Text(parent, width=1, height=height, wrap="word", padx=8, pady=6, font=(theme.BODY_FONT, 10), takefocus=True)
        set_color_roles(widget, background="APP_BG" if getattr(parent, "_remchat_color_roles", {}).get("background") == "WHITE" else "WHITE",
                        foreground="INK", insertbackground="INK", selectbackground="ACCENT_SOFT", selectforeground="INK",
                        highlightbackground="LINE", highlightcolor="ACCENT")
        widget.configure(relief="flat", highlightthickness=1)
        bind_text_editing(widget)
        widget.configure(state="disabled")
        return widget

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.viewport = ScrollCanvas(self, highlightthickness=0, bd=0)
        set_color_roles(self.viewport, background="APP_BG")
        self.viewport.grid(row=0, column=0, sticky="nsew")
        page_scroll = ttk.Scrollbar(self, command=self.viewport.yview)
        page_scroll.grid(row=0, column=1, sticky="ns")
        self.viewport.configure(yscrollcommand=page_scroll.set)
        self.body = self._frame(self.viewport)
        self._body_item = self.viewport.create_window((0, 0), window=self.body, anchor="nw")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(3, weight=1)
        self.viewport.bind("<Configure>", self._resize)
        self.body.bind("<Configure>", self._resize)
        self.viewport.bind("<MouseWheel>", lambda event: self._wheel(event, self.viewport))
        top = self._frame(self.body)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        for index, weight in enumerate((2, 1, 4)):
            top.columnconfigure(index, weight=weight)
        self._choice_labels = []
        self._top = top
        for column, text in enumerate(("1  选择工具", "2  日期（可选）", "3  选择主会话")):
            label = self._label(top, text, bold=True)
            label.grid(row=0, column=column, sticky="w", padx=(0, 8))
            self._choice_labels.append(label)
        self.tool_choice = AnchoredCombobox(top, state="readonly", values=[row["name"] + (" · 暂不支持" if not row.get("supported") else "") for row in self._tools], width=14)
        self.tool_choice.current(next((i for i, row in enumerate(self._tools) if row["id"] == self._tool_id), 0))
        self.tool_choice.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.date_choice = AnchoredCombobox(top, textvariable=self.day, values=["全部日期", date.today().isoformat()], width=15)
        self.date_choice.grid(row=1, column=1, sticky="ew", padx=(0, 8))
        self.session_choice = AnchoredCombobox(top, state="readonly", values=[], width=28)
        self.session_choice.grid(row=1, column=2, sticky="ew", padx=(0, 8))
        self.refresh_button = self._button(top, "重新读取", lambda: self.refresh(force=True))
        self.refresh_button.grid(row=1, column=3)
        self.tool_choice.bind("<<ComboboxSelected>>", self._tool_changed)
        self.session_choice.bind("<<ComboboxSelected>>", self._session_changed)
        self.date_choice.bind("<<ComboboxSelected>>", self._date_changed)
        self.date_choice.bind("<Return>", self._date_changed)
        help_bar = self._frame(top)
        self._help_bar = help_bar
        help_bar.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        help_bar.columnconfigure(0, weight=1)
        follow_controls = self._frame(help_bar)
        follow_controls.grid(row=0, column=1, padx=(8, 0))
        self.follow_control = tk.Checkbutton(follow_controls, text=self.tr("跟随最新"), variable=self.follow_latest,
                                           command=self._follow_changed, font=(theme.BODY_FONT, 10), takefocus=True,
                                           anchor="w", padx=4, pady=5)
        set_color_roles(self.follow_control, background="APP_BG", foreground="INK", activebackground="APP_BG",
                        activeforeground="INK", selectcolor="WHITE", highlightbackground="LINE", highlightcolor="ACCENT")
        self.follow_control.pack(side="left", padx=(0, 8))
        self.manage_button = self._button(follow_controls, "管理追踪", self._open_management)
        self.manage_button.pack(side="left")
        self.sync_label = self._label(help_bar, "本地自动同步正在初始化…", muted=True)
        self.sync_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        Tooltip(self.sync_label, self._sync_tip)
        self.status_label = self._label(self.body, variable=self.status, muted=True)
        self.status_label.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.context_cards = self._frame(self.body)
        self.context_cards.grid(row=2, column=0, sticky="ew", padx=pixels(self, 12), pady=(pixels(self, 8), pixels(self, 16)))
        for column in (0, 1):
            self.context_cards.columnconfigure(column, weight=1, uniform="project-context")
        self.goal_card = SettingsCard(self.context_cards, inset=12, radius=12, shadow=False)
        self.goal_card.grid(row=0, column=0, sticky="new", padx=(0, pixels(self, 8)))
        goal = self.goal_card.body
        goal.columnconfigure(0, weight=1)
        self._label(goal, "当前目标", bold=True).grid(row=0, column=0, sticky="w")
        self.goal_label = self._text(goal, 3)
        self.goal_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=pixels(self, 8))
        goal_scroll = ttk.Scrollbar(goal, command=self.goal_label.yview)
        goal_scroll.grid(row=1, column=2, sticky="ns", pady=pixels(self, 8))
        self.goal_label.configure(yscrollcommand=goal_scroll.set)
        self._put(self.goal_label, "尚未选择会话")
        self.context_label = self._label(goal, "", muted=True)
        self.context_label.grid(row=2, column=0, sticky="ew")
        self.instructions_card = SettingsCard(self.context_cards, inset=12, radius=12, shadow=False)
        self.instructions_card.grid(row=0, column=1, sticky="new")
        instructions = self.instructions_card.body
        instructions.columnconfigure(0, weight=1)
        self._label(instructions, "项目指令文件", bold=True).grid(row=0, column=0, sticky="w")
        self.instructions_text = self._text(instructions, 3)
        self.instructions_text.grid(row=1, column=0, columnspan=2, sticky="ew", pady=pixels(self, 8))
        self._put(self.instructions_text, "尚未选择会话")
        self.instructions_label = self._label(instructions, "", muted=True)
        self.instructions_label.grid(row=2, column=0, sticky="ew")
        self.instructions_button = self._button(instructions, "查看 / 编辑", self._show_instructions)
        self.instructions_button.grid(row=2, column=1, padx=(pixels(self, 8), 0))
        self.instructions_button.configure(state="disabled")
        self._header_pairs.append((self.instructions_label, self.instructions_button))
        self.lower = self._frame(self.body)
        self.lower.grid(row=3, column=0, sticky="nsew", padx=pixels(self, 12))
        self.lower.columnconfigure(0, weight=1, uniform="session-process-lower")
        self.lower.columnconfigure(1, weight=1, uniform="session-process-lower")
        self.lower.rowconfigure(0, weight=1)
        self.archive_card = SettingsCard(self.lower, inset=12, radius=12, shadow=False)
        self.archive_card.grid(row=0, column=0, sticky="new", padx=(0, pixels(self, 12)))
        self.archive, self.meter = self.archive_card.body, self._frame(self.lower)
        self.meter.grid(row=0, column=1, sticky="nsew")
        self.archive.columnconfigure(0, weight=1)
        # Reading regions keep their own usable height rather than stretching
        # to the height of every expanded chart in the adjacent column.
        self.archive.rowconfigure(2, weight=0)
        self.archive.rowconfigure(3, weight=0)
        prompt_head = self._frame(self.archive)
        prompt_head.grid(row=0, column=0, sticky="ew")
        prompt_head.columnconfigure(0, weight=1)
        prompt_title = self._label(prompt_head, "提示词存档 · 仅公开用户输入", bold=True)
        prompt_title.grid(row=0, column=0, sticky="ew")
        self.prompt_order_button = self._button(prompt_head, "时间正序 ↑", self._toggle_prompt_order)
        self.prompt_order_button.grid(row=0, column=1, padx=(8, 0))
        self._header_pairs.append((prompt_title, self.prompt_order_button))
        search = SoftTtkEntry(self.archive, textvariable=self.query, width=1, font=(theme.BODY_FONT, 10))
        search.grid(row=1, column=0, sticky="ew", pady=5)
        search.bind("<KeyRelease>", self._search_changed)
        self.prompt_list = tk.Listbox(self.archive, width=1, height=7, exportselection=False, font=(theme.BODY_FONT, 10), takefocus=True)
        set_color_roles(self.prompt_list, background="WHITE", foreground="INK", selectbackground="ACCENT_SOFT", selectforeground="INK")
        self.prompt_list.grid(row=2, column=0, sticky="nsew")
        self.prompt_list_scroll = ttk.Scrollbar(self.archive, command=self.prompt_list.yview)
        self.prompt_list_scroll.grid(row=2, column=1, sticky="ns", padx=(4, 0))
        self.prompt_list.configure(yscrollcommand=self.prompt_list_scroll.set)
        self.prompt_list.bind("<<ListboxSelect>>", self._select_prompt)
        self.prompt_text = self._text(self.archive, 10)
        self.prompt_text.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        prompt_scroll = ttk.Scrollbar(self.archive, command=self.prompt_text.yview)
        prompt_scroll.grid(row=3, column=1, sticky="ns", padx=(4, 0), pady=(4, 0))
        self.prompt_text.configure(yscrollcommand=prompt_scroll.set)
        self.meter.columnconfigure(0, weight=1)
        meter_head = self._frame(self.meter)
        meter_head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        meter_head.columnconfigure(0, weight=1)
        meter_title = self._label(meter_head, "用量与调用", bold=True)
        meter_title.grid(row=0, column=0, sticky="ew")
        self.meter_settings_button = self._button(meter_head, "显示设置", self._toggle_meter_settings)
        self.meter_settings_button.grid(row=0, column=1, padx=(8, 0))
        self._header_pairs.append((meter_title, self.meter_settings_button))
        self.meter_settings = self._frame(self.meter)
        self.meter_settings.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._label(self.meter_settings, "只调整显示，不停止观察或删除记录。", muted=True).pack(fill="x")
        self._meter_vars = {}
        for key, title in METER_SECTIONS.items():
            variable = tk.BooleanVar(self, self._meter_visible[key])
            self._meter_vars[key] = variable
            control = tk.Checkbutton(self.meter_settings, text=self.tr("显示" + title), variable=variable,
                                     command=lambda section=key: self._set_meter_visible(section, self._meter_vars[section].get()),
                                     anchor="w", font=(theme.BODY_FONT, 10), takefocus=True, padx=4, pady=3)
            set_color_roles(control, background="APP_BG", foreground="INK", activebackground="APP_BG",
                            activeforeground="INK", selectcolor="WHITE", highlightbackground="LINE", highlightcolor="ACCENT")
            control.pack(fill="x")
        self.meter_preference_status = self._label(self.meter_settings, "设置自动保存；收起后仍可从标题展开。", muted=True)
        self.meter_preference_status.pack(fill="x", pady=(4, 0))
        self.meter_settings.grid_remove()
        self._meter_bodies, self._meter_toggles, self._meter_titles = {}, {}, {}
        activity = self._meter_section("activity", 2)
        self.token_activity = TokenActivity(activity, translator=self.tr, select_day=self._choose_activity_day)
        self.token_activity.pack(fill="x", pady=(4, 8))
        tools = self._meter_section("tools", 3)
        self._label(tools, "前5项 · 点击或按 Enter 查看调用详情", muted=True).pack(fill="x")
        self.tool_bars = tk.Canvas(tools, width=1, height=110, highlightthickness=1, takefocus=True, cursor="hand2")
        set_color_roles(self.tool_bars, background="APP_BG", highlightbackground="APP_BG", highlightcolor="ACCENT")
        self.tool_bars.pack(fill="x", pady=(4, 0))
        self.tool_bars.bind("<Button-1>", lambda _event: self._show_tools())
        self.tool_bars.bind("<Return>", lambda _event: self._show_tools())
        self.tool_bars.bind("<space>", lambda _event: self._show_tools())
        self.tool_bars.bind("<MouseWheel>", lambda event: self._wheel(event, self.viewport))
        Tooltip(self.tool_bars, self._tool_bars_tip)
        subagents = self._meter_section("subagents", 4)
        self._label(subagents, "只显示关联元数据，不读取子 Agent 正文；不混入主会话输入和用量。", muted=True).pack(fill="x", pady=(2, 4))
        subagent_body = self._frame(subagents)
        subagent_body.pack(fill="both", expand=True)
        self.subagent_text = self._text(subagent_body, 5)
        self.subagent_text.pack(side="left", fill="both", expand=True)
        subagent_scroll = ttk.Scrollbar(subagent_body, command=self.subagent_text.yview)
        subagent_scroll.pack(side="right", fill="y")
        self.subagent_text.configure(yscrollcommand=subagent_scroll.set)
        footer = self._frame(self.body)
        self._footer = footer
        footer.grid(row=4, column=0, sticky="ew", padx=pixels(self, 12), pady=pixels(self, 12))
        self.observe_button = self._button(footer, "观察管理", self._open_management)
        self.observe_button.pack(side="left")
        self.record_button = self._button(footer, "记录阶段", self._record)
        self.record_button.pack(side="left", padx=6)
        self.import_button = self._button(footer, "导入计划", self._import_plan)
        self.import_button.pack(side="left")
        tools_button = self._button(footer, "调用详情", self._show_tools)
        tools_button.pack(side="left", padx=6)
        self._footer_buttons = (self.observe_button, self.record_button, self.import_button, tools_button)
        self.record_button.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self.bind("<Configure>", self._resize, add="+")
        self._outer_tag = "session-process-scroll-" + str(id(self))
        self.bind_class(self._outer_tag, "<MouseWheel>", self._outer_wheel)
        self.bind_class(self._outer_tag, "<FocusIn>", self._reveal_focus)
        def tag_children(widget):
            widget.bindtags((self._outer_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                tag_children(child)
        tag_children(self.body)
        self._apply_meter_visibility()
        self._draw_meter_gear()

    def _read_meter_preferences(self):
        defaults = {key: key != "subagents" for key in METER_SECTIONS}
        try:
            if self._meter_preferences_path.stat().st_size > 4096:
                return defaults
            value = json.loads(self._meter_preferences_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("version") == 1:
                if type(value.get("activity")) is not bool:
                    defaults["activity"] = bool(value.get("summary", True) or value.get("heatmap", True))
                for key in defaults:
                    if type(value.get(key)) is bool:
                        defaults[key] = value[key]
        except (OSError, ValueError, UnicodeError):
            pass  # An optional UI preference cannot block read-only browsing.
        return defaults

    def _meter_section(self, key, row):
        section = self._frame(self.meter)
        section.grid(row=row, column=0, sticky="ew", pady=(4, 6))
        section.columnconfigure(0, weight=1)
        header = self._frame(section)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title = self._label(header, METER_SECTIONS[key], bold=True)
        title.grid(row=0, column=0, sticky="ew")
        toggle = self._button(header, "收起", lambda: self._set_meter_visible(key, not self._meter_visible[key]))
        toggle.grid(row=0, column=1, padx=(8, 0))
        body = self._frame(section)
        body.grid(row=1, column=0, sticky="ew")
        self._meter_bodies[key], self._meter_toggles[key] = body, toggle
        self._meter_titles[key] = title
        self._header_pairs.append((title, toggle))
        title.configure(wraplength=120)
        return body

    def _toggle_meter_settings(self):
        self._meter_settings_open = not self._meter_settings_open
        if self._meter_settings_open:
            self.meter_settings.grid()
        else:
            self.meter_settings.grid_remove()
        self.meter_settings_button.configure(text=self.tr("收起设置" if self._meter_settings_open else "显示设置"))
        self._resize()

    def _apply_meter_visibility(self):
        for key, body in self._meter_bodies.items():
            visible = self._meter_visible[key]
            if visible:
                body.grid()
            else:
                body.grid_remove()
            self._meter_toggles[key].configure(text=self.tr("收起" if visible else "已收起 · 展开"))
            self._meter_vars[key].set(visible)
        self._resize()

    def _set_meter_visible(self, key, visible):
        self._meter_visible[key] = bool(visible)
        self._apply_meter_visibility()
        self._save_meter_preferences()

    def _save_meter_preferences(self):
        temporary = None
        try:
            existing = {}
            try:
                if self._meter_preferences_path.stat().st_size > 4096:
                    raise ValueError("UI preferences exceed the safe read limit")
                existing = json.loads(self._meter_preferences_path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict) or existing.get("version", 1) != 1:
                    raise ValueError("Unsupported UI preferences; preserve the original file")
            except FileNotFoundError:
                pass
            directory = self._meter_preferences_path.parent
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix="session-process-", suffix=".tmp", dir=directory)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({**existing, "version": 1, **self._meter_visible}, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._meter_preferences_path)
            self.meter_preference_status.configure(text=self.tr("显示设置已保存；不会改变观察状态。"))
            set_color_roles(self.meter_preference_status, foreground="MUTED")
        except (OSError, ValueError, UnicodeError):
            self.meter_preference_status.configure(text=self.tr("设置保存失败，本次显示仍有效；可再次切换重试。"))
            set_color_roles(self.meter_preference_status, foreground="ERROR")
            self.meter_settings.grid()
            self._meter_settings_open = True
            self.meter_settings_button.configure(text=self.tr("收起设置"))
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _draw_meter_gear(self):
        # Draw a native, theme-colored cog; the adjacent label is the accessible action.
        if getattr(self, "_meter_gear_mode", None) == theme.get_mode():
            return
        icon = tk.PhotoImage(master=self, width=20, height=20)
        color = theme.palette()["ACCENT" if theme.get_mode() == "dark" else "ACCENT_DEEP"]
        for y in range(20):
            for x in range(20):
                dx, dy = x - 9.5, y - 9.5
                radius = math.hypot(dx, dy)
                outer = 9 if math.cos(8 * math.atan2(dy, dx)) > .3 else 7
                if 3 <= radius <= outer:
                    icon.put(color, (x, y))
        self._meter_gear = icon
        self._meter_gear_mode = theme.get_mode()
        self.meter_settings_button.configure(image=icon, compound="left")

    @staticmethod
    def _replace(target, value):
        try:
            target.put_nowait(value)
        except queue.Full:
            try:
                target.get_nowait()
            except queue.Empty:
                pass
            try:
                target.put_nowait(value)
            except queue.Full:
                pass  # Concurrent native events already queued a refresh.

    def _tool_changed(self, _event=None):
        index = self.tool_choice.current()
        if 0 <= index < len(self._tools):
            self._tool_id, self._key = self._tools[index]["id"], ""
            self.follow_latest.set(True)
            self.day.set("全部日期")
            self._sync_status = {}
            self._sessions = []
            self.session_choice.set_values([])
            self.session_choice.set("")
            self._data = None
            self._render_empty()
            if not self._tools[index].get("supported"):
                self.pause()
                self.status.set(self.tr("此工具暂不支持自动读取。点击‘管理追踪’检查支持工具或使用高级手动接入。"))
                self.sync_label.configure(text=self.tr("未提供可读取的本地会话接口。"))
                return
            self.refresh(force=True)

    def _session_changed(self, _event=None):
        index = self.session_choice.current()
        if 0 <= index < len(self._sessions):
            self._key = self._sessions[index]["key"]
            self.follow_latest.set(False)
            self._data = None
            self._render_empty()
            self.refresh()

    def _date_changed(self, _event=None):
        self.follow_latest.set(False)
        self.refresh()

    def _follow_changed(self):
        if self.follow_latest.get():
            self._key = ""
            self.day.set("全部日期")
        self.refresh(force=True)

    def _native_event(self, event):
        # Subscription callbacks may run in any worker. No Tk calls here.
        item = event.get("task") if event.get("type") == "task" else event.get("connection")
        tool = event.get("tool_id") or (item.get("tool_id") if isinstance(item, dict) else None)
        if self.closed or tool != self._tool_id:
            return
        if event.get("type") in {"tool_catalog_changed", "tool_snapshot_changed", "native_sync_state", "tool_connection", "task", "usage_changed"}:
            self._replace(self._events, True)

    def refresh(self, force=False, *, background=False):
        if self.closed:
            return
        self.paused = False
        if not background:
            self._resize()
        try:
            day = self.day.get().strip()
            day = "" if day in {"", "全部日期"} else date.fromisoformat(day).isoformat()
        except ValueError:
            self._generation += 1
            self._busy = False
            self._error("日期无效，请输入 YYYY-MM-DD 或选择全部日期。")
            return
        if self._worker_thread is None:
            self._worker_thread = threading.Thread(target=self._worker, name="remchat-session-journey", daemon=True)
            self._worker_thread.start()
        self._generation += 1
        request = {"tool_id": self._tool_id, "key": self._key, "day": day,
                   "list": bool(force or self.follow_latest.get() or not self._sessions),
                   "follow": self.follow_latest.get()}
        self._busy = True
        self.refresh_button.configure(state="disabled")
        if not background:
            self.status.set(self.tr("正在读取本机会话…只读存档，不调用模型。"))
            set_color_roles(self.status_label, foreground="MUTED")
        self._replace(self._requests, (self._generation, request))
        if self._after is None:
            self._after = self.after(80, self._drain)
        self._cancel("_auto")

    def _worker(self):
        try:
            while not self._stop.is_set():
                try:
                    item = self._requests.get(timeout=.2)
                except queue.Empty:
                    continue
                if item is None:
                    break
                generation, request = item
                if self.paused or generation != self._generation:
                    continue
                try:
                    tools = self.service.tools()
                    if self.paused or generation != self._generation:
                        continue
                    sessions = self.service.sessions(request["tool_id"]) if request["list"] else None
                    key = request["key"]
                    if request["follow"] and sessions is not None:
                        key = ""
                    if sessions is not None and key and not any(row["key"] == key for row in sessions):
                        key = ""
                    if not key and request["tool_id"] == "remchat" and sessions:
                        current = str(self.owner.service.current_conversation_id)
                        key = next((row["key"] for row in sessions if row["id"] == current), "")
                    if not key and sessions and request["tool_id"] != "remchat":
                        # Adapters order by their native recent catalog. Prefer a
                        # reported activity/creation timestamp where it exists;
                        # missing timestamps keep the adapter's order, not a guess.
                        def recency(row):
                            values = [row.get(name) for name in ("last_activity", "updated_at", "created_at")]
                            return max((value for value in values if type(value) in {int, float} and math.isfinite(value)), default=0)
                        key = max(sessions, key=recency)["key"]
                    detail = self.service.snapshot(key, request["day"]) if key else None
                    selected = detail.get("session", {}) if detail else {}
                    observing = any(row.get("tool_id") == selected.get("tool_id") and row.get("external_session_id") == selected.get("id")
                                    and row.get("connection_id") == selected.get("connection_id") and row.get("monitoring", True)
                                    for row in self.manager.list_tasks()) if selected.get("connection_id") else False
                    sync = self.manager.native_sync_status(request["tool_id"]) if request["tool_id"] not in {"remchat", "project_trace"} else {}
                    payload = (generation, {"tools": tools, "sessions": sessions, "detail": detail, "key": key,
                                            "query": request, "observing": observing, "sync": sync}, None)
                except Exception as error:
                    payload = (generation, None, public(str(error), 500))
                if not self._stop.is_set() and not self.paused:
                    self._replace(self._results, payload)
        finally:
            self.service.close()  # Release this preview's watch; the app-owned sync keeps running.

    def _drain(self):
        self._after = None
        if self.closed or self.paused:
            return
        try:
            while True:
                generation, result, error = self._results.get_nowait()
                if generation != self._generation:
                    continue
                self._busy = False
                self.refresh_button.configure(state="normal")
                if error:
                    self._error("读取失败：" + error + "；后台会重试，也可进入‘管理追踪’检查存档。")
                else:
                    self._render(result)
                self._cancel("_auto")
                # Keep empty catalogs alive too. Native events usually arrive
                # sooner; this fallback also refreshes RemChat's own history.
                self._auto = self.after(5000, lambda: self.refresh(force=True, background=True))
        except queue.Empty:
            pass
        if not self._busy:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            else:
                self.refresh(force=True, background=True)
        if self._mode != theme.get_mode():
            self._mode = theme.get_mode()
            self._draw_all()
        if self._after is None:
            self._after = self.after(80, self._drain)

    def _error(self, message):
        self.refresh_button.configure(state="normal")
        self.status.set(self.tr(message) + (self.tr(" 下方保留上次成功数据及原筛选日期。") if self._data else ""))
        set_color_roles(self.status_label, foreground="ERROR")

    def _sync_tip(self):
        state = self._sync_status
        return self.tr("只读本机存档；不代表宿主进程正在执行。") + "\n" + public(state.get("location"), 1000) + (
            "\n" + public(state.get("last_error"), 600) if state.get("last_error") else "")

    def _render_sync(self, state):
        self._sync_status = dict(state or {})
        mode = self.tr("跟随最新" if self.follow_latest.get() else "浏览历史 · 跟随已暂停")
        if not state:
            self.sync_label.configure(text=mode + self.tr(" · 本地同步初始化中"))
            set_color_roles(self.sync_label, foreground="MUTED")
            return
        phase = {"reading": "后台读取中", "synced": "本地自动同步", "error": "同步异常 · 后台重试",
                 "disconnected": "已断开 · 在管理追踪中连接", "unavailable": "未找到本地存档"}.get(state.get("state"), "正在初始化")
        stamp = state.get("last_success")
        succeeded = self.tr("尚无成功读取")
        if type(stamp) in {int, float} and math.isfinite(stamp) and stamp > 0:
            try:
                succeeded = datetime.fromtimestamp(stamp).strftime("%H:%M:%S")
            except (OverflowError, OSError, ValueError):
                succeeded = self.tr("时间不可读")
        parts = [mode, self.tr(phase), self.tr("最近成功 ") + succeeded,
                 self.tr(f"{state.get('session_count', 0)} 个主会话")]
        if state.get("last_error"):
            parts.append(public(state["last_error"], 180))
        self.sync_label.configure(text=" · ".join(parts))
        set_color_roles(self.sync_label, foreground="ERROR" if state.get("state") == "error" else "MUTED")

    def _render(self, result):
        previous = self._data
        self._tools = result["tools"]
        self.tool_choice.set_values([row["name"] + (" · 暂不支持" if not row.get("supported") else "") for row in self._tools])
        index = next((index for index, row in enumerate(self._tools) if row["id"] == self._tool_id), -1)
        if index >= 0:
            self.tool_choice.current(index)
        if result["sessions"] is not None:
            self._sessions = result["sessions"]
            self.session_choice.set_values([row.get("name", "会话") + " · " + row["id"][:10] for row in self._sessions])
        self._key = result["key"]
        index = next((index for index, row in enumerate(self._sessions) if row["key"] == self._key), -1)
        if index >= 0:
            self.session_choice.current(index)
        else:
            self.session_choice.set("")
        self._data = result["detail"]
        self._render_sync(result.get("sync", {}))
        set_color_roles(self.status_label, foreground="MUTED")
        if not self._data:
            self._render_empty()
            self.status.set(self.tr(f"{len(self._sessions)} 个主会话"))
            return
        data = self._data
        self._put(self.goal_label, data["goal"] or self.tr("无可用目标"))
        self.context_label.configure(text=self.tr(data.get("goal_source") or ("已上报目标" if data["goal"] else "所选记录未提供原生目标")))
        self._render_instructions()
        self.date_choice.set_values(["全部日期", *data["known_dates"], *([] if date.today().isoformat() in data["known_dates"] else [date.today().isoformat()])])
        viewing = self.tr("已加入桌面追踪：" if result.get("observing") else "自动读取 · 未加入桌面追踪：")
        self.status.set(viewing + data["session"].get("name", "") + " · " + data["session"].get("id", "")[:12])
        if data.get("metadata_only"):
            self.status.set(viewing + data["session"].get("name", "") +
                            self.tr(" · 仅本地列表摘要；正文和 Token 用量未提供，不记为零。"))
        writable = data["session"]["kind"] == "trace" and data["trace_enabled"]
        self.record_button.configure(state="normal" if writable else "disabled")
        self.import_button.configure(state="normal" if writable else "disabled")
        # Poll/read timestamps are not changed content. Avoid resetting charts,
        # focus or the stage scroll position on each successful background read.
        substantive = lambda row: {key: value for key, value in (row or {}).items() if key not in {"read_at", "sync_status", "native_sync"}}
        if substantive(previous) == substantive(data):
            return
        self._render_prompts()
        self._draw_all()

    def _render_empty(self):
        self._put(self.goal_label, self.tr("尚未选择会话"))
        self.context_label.configure(text="")
        self._render_instructions()
        self.record_button.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self._render_prompts()
        self._draw_all()

    def _render_prompts(self):
        selected = self.prompt_list.curselection()
        identity = lambda row: (row["id"], row["timestamp"], row["text"])
        selected_id = identity(self._prompt_rows[selected[0]]) if selected and selected[0] < len(self._prompt_rows) else None
        old_view = self.prompt_list.yview()
        top_index = self.prompt_list.nearest(0) if self._prompt_rows else 0
        top_id = identity(self._prompt_rows[top_index]) if top_index < len(self._prompt_rows) else None
        query = self.query.get().strip().casefold()
        context = (self._key, self.day.get(), query, self._prompt_newest_first)
        same_context = context == getattr(self, "_prompt_context", None)
        rows = [row for row in (self._data["requests"] if self._data else []) if not query or query in row["text"].casefold()]
        dated = sorted((row for row in rows if row["timestamp"] is not None),
                       key=lambda row: (row["timestamp"], row["id"]), reverse=self._prompt_newest_first)
        # Missing dates stay at the end in their original record order. Never
        # assign file timestamps or reorder the underlying evidence itself.
        updated = dated + [row for row in rows if row["timestamp"] is None]
        # A five-second polling result is not a navigation request. Do not
        # delete/reinsert unchanged content or scroll back to the selected row.
        if same_context and updated == self._prompt_rows:
            return
        self._prompt_context = context
        self._prompt_rows = updated
        self.prompt_list.delete(0, "end")
        for row in self._prompt_rows:
            day = local_day(row["timestamp"]) or "日期未知"
            self.prompt_list.insert("end", day + " · " + " ".join(row["text"].split())[:65])
        if self._prompt_rows:
            index = next((index for index, row in enumerate(self._prompt_rows) if identity(row) == selected_id), 0)
            self.prompt_list.selection_set(index)
            if same_context:
                anchor = next((i for i, row in enumerate(updated) if identity(row) == top_id), None)
                if anchor is not None:
                    self.prompt_list.yview(anchor)
                elif old_view:
                    self.prompt_list.yview_moveto(old_view[0])
            else:
                self.prompt_list.see(index)
            self._select_prompt()
        else:
            self._put(self.prompt_text, "没有匹配的公开用户输入。可能未采集到这段历史；不是用户没有发言。")

    def _toggle_prompt_order(self):
        self._prompt_newest_first = not self._prompt_newest_first
        self.prompt_order_button.configure(text=self.tr("时间倒序 ↓" if self._prompt_newest_first else "时间正序 ↑"))
        self._render_prompts()  # Local display only; no rescan or model call.

    def _select_prompt(self, _event=None):
        selected = self.prompt_list.curselection()
        if selected and selected[0] < len(self._prompt_rows):
            row = self._prompt_rows[selected[0]]
            identifier = (self._key, row["id"])
            preserve = identifier == getattr(self, "_prompt_display_id", None)
            self._prompt_display_id = identifier
            self._put(self.prompt_text, (local_day(row["timestamp"]) or "日期未知") + "\n" + row["text"], preserve_scroll=preserve)

    @staticmethod
    def _put(widget, text, *, preserve_scroll=True):
        if widget.get("1.0", "end-1c") == text:
            return
        position = widget.yview()[0] if preserve_scroll else 0
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        widget.yview_moveto(position)

    def _search_changed(self, _event=None):
        self._cancel("_search_after")
        self._search_after = self.after(120, self._search_ready)

    def _search_ready(self):
        self._search_after = None
        if not self.closed and not self.paused:
            self._render_prompts()

    def _resize(self, _event=None):
        if self.closed or self.paused:
            return
        if _event is not None:
            # Scrolling moves embedded native windows. Position-only Configure
            # events must not reflow the page or recreate every chart.
            size = (_event.width, _event.height)
            previous = getattr(_event.widget, "_project_layout_size", None)
            _event.widget._project_layout_size = size
            if size == previous:
                return
        if self._resize_after is None:
            self._resize_after = self.after(16, self._resize_ready)

    def _resize_ready(self):
        self._resize_after = None
        if self.closed or self.paused:
            return
        page_width = max(250, self.viewport.winfo_width())
        gap = pixels(self, 12)
        minimum_column = max(pixels(self,300),native_font(self,font=(theme.BODY_FONT,10)).measure("时间范围 近 30 天 模型 全部模型")+pixels(self,64))
        narrow = page_width < minimum_column * 2 + gap * 3
        key = (page_width, self.viewport.winfo_height(), self.body.winfo_reqheight(),
               self.meter.winfo_width(), self.context_cards.winfo_width(), theme.get_mode(), pixels(self, 100))
        if key == self._layout_key:
            return
        self._layout_key = key
        if int(self.viewport.itemcget(self._body_item, "width")) != page_width:
            self.viewport.itemconfigure(self._body_item, width=page_width)
        self._layout_choices(page_width < pixels(self, 650))
        self._layout_footer(page_width - 26)
        width = max(250, page_width - gap * 2)
        self.status_label.configure(wraplength=width)
        self.sync_label.configure(wraplength=width)
        self.meter.grid_configure(row=1 if narrow else 0, column=0 if narrow else 1, padx=(0, 0), pady=(8 if narrow else 0, 0))
        self.archive_card.grid_configure(columnspan=2 if narrow else 1, padx=(0, 0 if narrow else gap))
        self.meter.grid_configure(columnspan=2 if narrow else 1)
        self.goal_card.grid_configure(columnspan=2 if narrow else 1, padx=(0, 0 if narrow else gap))
        self.instructions_card.grid_configure(row=1 if narrow else 0, column=0 if narrow else 1,
                                             columnspan=2 if narrow else 1, pady=(gap if narrow else 0, 0))
        self.context_label.configure(wraplength=max(100, self.goal_card.body.winfo_width() - gap * 2))
        # Let Tk settle geometry on its next native pass. Forcing idle tasks
        # here reenters SettingsCard/TokenActivity while this layout is active.
        meter_width = max(180, self.meter.winfo_width() - 8)
        header_labels = {label for label, _control in self._header_pairs}
        def wrap_meter(widget):
            if widget is self.token_activity:
                return  # Its cards wrap to their own measured body widths.
            if isinstance(widget, tk.Label) and widget not in header_labels:
                if int(widget.cget("wraplength")) != meter_width:
                    widget.configure(wraplength=meter_width)
            for child in widget.winfo_children():
                wrap_meter(child)
        wrap_meter(self.meter)
        self._fit_headers()
        # Natural content height is preserved; charts/editors are not squeezed
        # to fit one viewport. Position-only events from yview are ignored.
        visible_top = self.viewport.canvasy(0)
        total = max(self.body.winfo_reqheight(), self.viewport.winfo_height())
        self.viewport.configure(scrollregion=(0, 0, page_width, total))
        self.viewport.yview_moveto(max(0, visible_top / max(1, total)))
        # TokenActivity and cards own their geometry painting. Only this small
        # plot needs a width-dependent redraw, not another full data render.
        self._draw_usage()

    def _layout_choices(self, stacked):
        for index, (label, choice) in enumerate(zip(self._choice_labels, (self.tool_choice, self.date_choice, self.session_choice))):
            label.grid_configure(row=index * 2 if stacked else 0, column=0 if stacked else index,
                                 columnspan=4 if stacked else 1, sticky="ew", padx=(0, 0 if stacked else 8),
                                 pady=(6 if stacked and index else 0, 0))
            choice.grid_configure(row=index * 2 + 1 if stacked else 1, column=0 if stacked else index,
                                  columnspan=4 if stacked else 1, padx=(0, 0 if stacked else 8))
        self.refresh_button.grid_configure(row=6 if stacked else 1, column=0 if stacked else 3,
                                           columnspan=4 if stacked else 1, sticky="ew", pady=(6 if stacked else 0, 0))
        self._help_bar.grid_configure(row=7 if stacked else 2)

    def _layout_footer(self, width):
        key = (width, tuple(button.winfo_reqwidth() for button in self._footer_buttons))
        if key == getattr(self, "_footer_layout_key", None):
            return
        self._footer_layout_key = key
        for button in self._footer_buttons:
            button.pack_forget()
        column = row = used = 0
        for button in self._footer_buttons:
            size = button.winfo_reqwidth() + 6
            if used and used + size > width:
                row, column, used = row + 1, 0, 0
            button.grid(row=row, column=column, sticky="w", padx=(0, 6), pady=(4 if row else 0, 0))
            column, used = column + 1, used + size

    def _fit_headers(self):
        # A heading's wrap width must exclude its adjacent actions. Setting it
        # to the whole meter width forces Tk's grid to grow beyond the viewport.
        grouped = {}
        for label, control in self._header_pairs:
            grouped.setdefault(control, []).append(label)
        for control, labels in grouped.items():
            available = max(1, control.master.winfo_width())
            gap = pixels(self, 12)
            stacked = available - control.winfo_reqwidth() - gap < pixels(self, 140)
            control.grid_configure(row=len(labels) if stacked else 0, column=0 if stacked else 1,
                                   rowspan=1 if stacked else len(labels), sticky="w" if stacked else "",
                                   padx=(0 if stacked else 8, 0), pady=(4 if stacked else 0, 0))
            for label in labels:
                length = max(pixels(self, 80), available - (0 if stacked else control.winfo_reqwidth() + gap))
                if int(label.cget("wraplength")) != length:
                    label.configure(wraplength=length)

    def _outer_wheel(self, event):
        if isinstance(event.widget, (tk.Text, tk.Listbox, tk.Canvas, ttk.Combobox, ttk.Scrollbar)):
            return None  # Keep each editor/canvas/native selector's own input.
        return self._wheel(event, self.viewport)

    def _reveal_focus(self, event):
        if self.closed or self.paused:
            return
        y = event.widget.winfo_rooty() - self.body.winfo_rooty()
        top, bottom = self.viewport.canvasy(0), self.viewport.canvasy(self.viewport.winfo_height())
        if y < top or y + event.widget.winfo_height() > bottom:
            region = self.viewport.cget("scrollregion").split()
            if not region:
                return
            total = max(1, float(region[-1]))
            self.viewport.yview_moveto(max(0, (y - 10) / total))

    def _draw_all(self):
        self._draw_meter_gear()
        self._draw_usage()
        self._draw_subagents()
        self._resize()

    def _choose_activity_day(self, day):
        self.day.set(day or "全部日期")
        self.follow_latest.set(False)
        self.refresh()

    def _draw_subagents(self):
        rows = self._data.get("subagents", []) if self._data else []
        count = self._data.get("child_count", len(rows)) if self._data else 0
        self._meter_titles["subagents"].configure(text=self.tr(f"附属子 Agent 活动 · {count} 条"))
        lines = [f"已关联 {count} 条；本区域展示前 {min(50, len(rows))} 条元数据。"] if count > 50 else []
        for row in rows[:50]:
            role = "自动审查" if row.get("session_kind") == "guardian_review" else "子 Agent"
            lines.append(public(row.get("name") or row.get("id"), 160) + " · " + role
                         + "\n模型：" + public(row.get("model") or "未上报", 100) + " · 状态未上报，不推测运行时长。")
        self._put(self.subagent_text, "\n\n".join(lines) or "尚无原生父子关联记录；没有关联记录不代表没有子 Agent 调用。")

    def _draw_usage(self):
        data = self._data
        delivery = (data["usage"] if data else None,
                    (data["session"].get("key"), data["session"].get("id")) if data else None,
                    data["day"] if data else "", data.get("read_at") if data else None)
        if delivery != getattr(self, "_token_delivery", None):
            self._token_delivery = delivery
            self.token_activity.set_data(*delivery)
        key = (data["tools"] if data else None, self.tool_bars.winfo_width(), theme.get_mode(), pixels(self, 100))
        if key == getattr(self, "_tool_draw_key", None):
            return
        self._tool_draw_key = key
        self.tool_bars.delete("all")
        colors = theme.palette()
        if not data:
            return
        calls = data["tools"]
        if calls["count"] is None:
            item = self.tool_bars.create_text(8, 8, anchor="nw", width=max(80, self.tool_bars.winfo_width() - 16), text=self.tr("没有可验证调用记录；MCP / Skill 数量未知。配置目录不是调用证据。"), fill=colors["MUTED"], font=(theme.BODY_FONT, 10))
            self.tool_bars.configure(height=self.tool_bars.bbox(item)[3] + 8)
            return
        max_count = max((row["count"] for row in calls["rows"]), default=1) or 1
        font = native_font(self,font=(theme.BODY_FONT,9))
        width, y = max(80, self.tool_bars.winfo_width() - 16), 8
        for row in calls["rows"][:5]:
            title = self.tr(CATEGORIES[row["category"]] + " · ") + row["name"]
            label = self.tool_bars.create_text(8, y, anchor="nw", width=width, text=title, fill=colors["INK"], font=(theme.BODY_FONT, 9))
            bottom = self.tool_bars.bbox(label)[3]
            count = f'{row["count"]:,}'
            bar_width = max(8, width - font.measure(count) - 14)
            center = bottom + font.metrics("linespace") / 2 + 3
            self.tool_bars.create_rectangle(8, center - 4, 8 + bar_width, center + 4, fill=colors["WHITE"], outline="")
            self.tool_bars.create_rectangle(8, center - 4, 8 + bar_width * row["count"] / max_count, center + 4, fill=colors["ACCENT"], outline="")
            self.tool_bars.create_text(8 + width, center, anchor="e", text=count, fill=colors["INK"], font=(theme.BODY_FONT, 9))
            y = center + font.metrics("linespace") / 2 + 12
        self.tool_bars.configure(height=max(48, math.ceil(y)))

    def _tool_bars_tip(self):
        rows = self._data["tools"]["rows"][:5] if self._data else []
        return "\n".join(self.tr(CATEGORIES[row["category"]] + " · ") + row["name"] + f' · {row["count"]:,}' for row in rows)


    def _render_instructions(self):
        data = (self._data or {}).get("project_instructions", {})
        self._instruction_rows = data.get("files", [])
        rows = [row for row in self._instruction_rows if row.get("status") == "ok"]
        if rows:
            preview = "\n".join(public(row.get("relative_path") or row.get("name"), 300) for row in rows[:3])
            if len(rows) > 3:
                preview += self.tr(f"\n另有 {len(rows) - 3} 个文件 · 查看全部")
            note = self.tr(f"{len(rows)} 个本地文件")
        elif self._data:
            preview = self.tr("未发现项目指令文件。" if data.get("status") == "no_files" else "项目目录或指令文件不可读取；不推测路径。")
            note = self.tr({"no_files": "所选工作目录没有相应文件", "unsupported_tool": "此来源不提供本地项目指令",
                            "unknown_working_directory": "未提供工作目录", "remote_working_directory": "远程目录；不猜测本机映射",
                            "non_local_working_directory": "目录不是本机绝对路径", "working_directory_unavailable": "工作目录不可访问",
                            "linked_path": "链接路径不自动读取", "broad_working_directory": "家目录或盘根不自动扫描"}.get(data.get("status"), "文件读取不完整 · 请查看详情"))
        else:
            preview, note = self.tr("尚未选择会话"), ""
        self._put(self.instructions_text, preview)
        self.instructions_label.configure(text=note)
        self.instructions_button.configure(state="normal" if self._instruction_rows else "disabled")

    def _show_instructions(self):
        if not self._instruction_rows:
            return
        # Capture the selected session's files. Background follow-latest must
        # never retarget an already opened editor to a different project.
        rows = [dict(row) for row in self._instruction_rows]
        window = tk.Toplevel(self)
        self._dialogs.append(window)
        window.title(self.tr("所选项目 · 指令文件"))
        window.geometry("760x560")
        set_color_roles(window, background="APP_BG")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        choice = AnchoredCombobox(window, state="readonly", width=30,
                                  values=[row.get("relative_path") or row["name"] for row in rows])
        choice.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        open_button = self._button(window, "VS Code 编辑原文件", lambda: launch_editor())
        open_button.grid(row=0, column=1, padx=(0, 12), pady=(12, 8))
        note = self._label(window, "本地项目文件 · 非完整系统提示词", muted=True)
        note.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        view = self._text(window, 16)
        view.grid(row=2, column=0, sticky="nsew", padx=(12, 0))
        scroll = ttk.Scrollbar(window, command=view.yview)
        scroll.grid(row=2, column=1, sticky="ns", padx=(0, 12))
        view.configure(yscrollcommand=scroll.set)
        status = self._label(window, "", muted=True)
        status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=12)
        window.bind("<Configure>", lambda event: note.configure(wraplength=max(80, event.width - 24)) if event.widget is window else None)

        def select(_event=None):
            index = choice.current()
            if 0 <= index < len(rows):
                row = rows[index]
                self._put(view, public(row.get("text"), 131072) or self.tr("文件不可读取：") + public(row.get("status"), 200), preserve_scroll=False)
                status.configure(text=public(row.get("path"), 4000))
                open_button.configure(state="normal" if row.get("editable") else "disabled")

        def launch_editor():
            index = choice.current()
            if not 0 <= index < len(rows) or not rows[index].get("editable"):
                return
            selected = dict(rows[index])
            replies = queue.Queue(maxsize=1)
            timer = [None]
            open_button.configure(state="disabled")
            status.configure(text=self.tr("正在打开原文件…"))

            def work():
                try:
                    from ..companion_preferences import open_in_vscode
                    path = Path(selected["path"])
                    for component in (*path.parents, path):
                        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
                            raise ValueError("文件路径已被替换为链接；请重新读取。")
                    if not path.is_file() or path.resolve(strict=True) != path or path.stat().st_size > 131072:
                        raise ValueError("文件路径或大小已变化；请重新读取。")
                    open_in_vscode(path)
                    replies.put_nowait(None)
                except (OSError, ValueError, RuntimeError) as error:
                    replies.put_nowait(public(str(error), 400))

            def poll():
                timer[0] = None
                if self.closed or not window.winfo_exists():
                    return
                try:
                    error = replies.get_nowait()
                except queue.Empty:
                    timer[0] = window.after(80, poll)
                    return
                status.configure(text=self.tr("未打开：") + error if error else self.tr("原文件已打开 · 预览为打开时的内容"))
                set_color_roles(status, foreground="ERROR" if error else "MUTED")
                select_state = rows[choice.current()].get("editable") if choice.current() >= 0 else False
                open_button.configure(state="normal" if select_state else "disabled")

            threading.Thread(target=work, name="remchat-project-instruction-editor", daemon=True).start()
            timer[0] = window.after(80, poll)

            def cancel(event):
                if event.widget is window and timer[0] is not None:
                    try:
                        window.after_cancel(timer[0])
                    except tk.TclError:
                        pass
                    timer[0] = None

            window.bind("<Destroy>", cancel, add=True)

        choice.bind("<<ComboboxSelected>>", select)
        choice.current(0)
        select()
        window.bind("<Escape>", lambda _event: window.destroy())

    def _show_tools(self):
        if self._data:
            tools = self._data["tools"]
            text = tools["coverage"] + "\n\n" + ("已验证去重调用数：" + str(tools["count"]) if tools["count"] is not None else "调用数量未上报，不能当0")
            text += "\nSkill 文件参考读取证据：" + str(tools.get("reference_count", 0)) + "（不是额外工具执行）"
            text += "\n\n" + ("\n".join(CATEGORIES[row["category"]] + " · " + row["name"] + " · " + str(row["count"]) for row in tools["rows"]) or "没有当前范围可验证的调用名称。")
            references = [row for row in tools["calls"] if row.get("evidence_kind") == "skill_file_read"]
            if references:
                text += "\n\n参考读取凭据（只读元数据）\n" + "\n".join(row["name"] + " · " + row["state"] + " · " + row["id"] + "\n" + row.get("summary", "") for row in references)
            self._evidence_window("所选范围公开调用详情（近期有界）", text)

    def _evidence_window(self, title, text):
        window = tk.Toplevel(self)
        self._dialogs.append(window)
        window.title(self.tr(title))
        window.geometry("650x450")
        set_color_roles(window, background="APP_BG")
        body = self._text(window, 12)
        body.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        scroll = ttk.Scrollbar(window, command=body.yview)
        scroll.pack(side="right", fill="y")
        body.configure(yscrollcommand=scroll.set)
        self._put(body, text)
        window.bind("<Escape>", lambda _event: window.destroy())

    def _open_management(self):
        callback = getattr(self.owner, "_show_project_management", None)
        if callback:
            callback()  # Navigation only; never toggle tracing or select sessions.

    def _record(self):
        if not self._data or self._data["session"]["kind"] != "trace" or not self._data["trace_enabled"]:
            return
        from .project_process import ProjectProcessPanel
        # Reuse the mature explicit record dialog without constructing its old file dashboard.
        bridge = ProjectProcessPanel.__new__(ProjectProcessPanel)
        bridge.owner, bridge.trace, bridge.root = self.owner, self.manager.project_trace, self.winfo_toplevel()
        bridge.data = bridge.trace.snapshot()
        bridge.record_dialog, bridge.view_revision = None, None
        bridge.view_steps, bridge.selected_step = bridge.data["steps"], ""
        bridge.refresh = lambda force=False: self.refresh(force=force)
        bridge._record()
        if bridge.record_dialog:
            self._dialogs.append(bridge.record_dialog)

    def _import_plan(self):
        if not self._data or self._data["session"]["kind"] != "trace" or not self._data["trace_enabled"]:
            return
        from .project_process import ProjectProcessPanel
        bridge = ProjectProcessPanel.__new__(ProjectProcessPanel)
        bridge.owner, bridge.trace, bridge.root = self.owner, self.manager.project_trace, self.winfo_toplevel()
        bridge.data = bridge.trace.snapshot()
        bridge.refresh = lambda force=False: self.refresh(force=force)
        bridge._import_plan()  # Existing explicit validated/size-bounded import contract.

    @staticmethod
    def _wheel(event, canvas):
        if event.delta:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _cancel(self, attribute):
        identifier = getattr(self, attribute)
        if identifier is not None:
            try:
                self.after_cancel(identifier)
            except tk.TclError:
                pass
            setattr(self, attribute, None)

    def pause(self):
        if self.closed:
            return
        self.paused = True
        self._busy = False
        self._generation += 1
        for attribute in ("_after", "_auto", "_resize_after", "_search_after"):
            self._cancel(attribute)
        self.refresh_button.configure(state="normal")

    def _destroyed(self, event):
        if event.widget is self:
            self.close()

    def close(self):
        if self.closed:
            return
        self.pause()
        self.closed = True
        self._unsubscribe()
        self.unbind_class(self._outer_tag, "<MouseWheel>")
        self.unbind_class(self._outer_tag, "<FocusIn>")
        self._stop.set()
        self._replace(self._requests, None)
        for dialog in self._dialogs:
            try:
                dialog.destroy()
            except tk.TclError:
                pass
        self._dialogs.clear()
        if self._worker_thread is None:
            self.service.close()
