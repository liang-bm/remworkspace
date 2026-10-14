"""Explicit local tool connection and session selection; never starts inference."""
from __future__ import annotations

import json
import math
from datetime import datetime
import os
import threading
import time
from pathlib import Path
import tkinter as tk
from .soft_fields import SoftTtkEntry
from tkinter import filedialog, font as tkfont, ttk

from . import theme
from .dpi import pixels
from .theme_controller import set_color_roles
from .task_rings import elapsed_text, task_status, is_record_source, record_sync_connected
from .selection_feedback import CheckedTreeSelection, Tooltip, TopNotice
from .settings_card import SettingsCard
from .combobox_popup import AnchoredCombobox


CONNECTION_STATES = {
    "connected": "已连接", "ready": "已连接", "connecting": "连接中",
    "disconnected": "已断开", "failed": "连接失败", "error": "连接失败",
    "unsupported": "暂不支持", "unknown": "状态未知",
}
SESSION_STATES = {
    "running": "运行中", "started": "运行中", "idle": "空闲",
    "completed": "已结束", "failed": "失败", "cancelled": "已取消",
    "unknown": "状态未知", "waiting": "等待接入", "stopped": "已停止观察",
    "disconnected": "同步中断", "sync_lost": "同步中断",
}
LOCAL_RECORD_NOTICE = "本地会话记录同步；非实时运行清单；状态 / 时长未知时不猜测。"


def _is_record(row):
    return is_record_source(row)


def _known_record_directory(tool: dict, value: str, connection: dict | None = None) -> bool:
    """Recognize a storage path accidentally entered as a cwd filter.

    Only tool-specific default or selected locations qualify; a project merely
    named 'sessions' is not silently reinterpreted as another data source.
    """
    if not value:
        return False
    def normal(path):
        path = str(path).strip().strip('"').strip("'")
        return os.path.normcase(os.path.abspath(os.path.expanduser(path))).rstrip("\\/")
    candidate = normal(value)
    locations = [tool.get("default_location", "")]
    if connection and _is_record(connection):
        locations.append(connection.get("location", ""))
    for location in locations:
        if not location:
            continue
        root = normal(location)
        parent = normal(str(Path(location).parent))
        if candidate == root or candidate.startswith(root + os.sep):
            return True
        # A known product home (.codex/.claude), never a broad profile root.
        if candidate == parent and Path(location).parent.name.lower() in {".codex", ".claude", ".gemini", "antigravity"}:
            return True
    return False


def _state_text(row, state=None):
    state = str(row.get("state", "unknown")) if state is None else state
    label = SESSION_STATES.get(state, "状态未知")
    if _is_record(row):
        label = "回合已开始 · 未见结束" if row.get("state") in {"started", "running"} and state not in {"disconnected", "sync_lost", "stopped"} else label
        return "记录：" + label
    return label


def _public_value(value: object) -> str:
    """Only render explicitly selected public values, not a raw native payload."""
    if value in (None, "", [], {}):
        return "尚未上报"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)[:24000]


def _public_rows(value, keys):
    """Defense in depth for imported legacy records bypassing the native adapter."""
    if isinstance(value, str):
        return value[:12000]
    rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [{key: row[key] for key in keys if key in row and isinstance(row[key], (str, int, float, bool))}
            for row in rows[:60] if isinstance(row, dict)]


def session_detail_text(row: dict) -> str:
    """A shared read-only projection, excluding hidden reasoning/raw messages."""
    detail = row.get("session_detail")
    detail = detail if isinstance(detail, dict) else {}
    reported_usage = detail.get("usage")
    usage = {key: reported_usage[key] for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")
             if isinstance(reported_usage, dict) and type(reported_usage.get(key)) is int and reported_usage[key] >= 0}
    plan = _public_rows(detail.get("public_plan", detail.get("plan")), ("id", "title", "state", "outcome", "remaining"))
    tools = _public_rows(detail.get("tool_calls", detail.get("tools")), ("id", "name", "state", "category", "summary"))
    files = _public_rows(detail.get("files"), ("id", "path", "operation"))
    request = detail.get("request_summary") or detail.get("request")
    request = _public_rows(request, ("id", "text", "summary"))
    requests = _public_rows(detail.get("requests"), ("id", "text"))
    goal = detail.get("goal") if isinstance(detail.get("goal"), str) else None
    memory = detail.get("memory") if isinstance(detail.get("memory"), str) else None
    state, elapsed = task_status(row)
    lines = [str(row.get("name", row.get("id", ""))),
             f"状态：{_state_text(row, state)} · {'已记录时长' if _is_record(row) else '运行时长'}：{elapsed_text(elapsed)}",
             "工具：" + str(row.get("tool_name") or row.get("tool_id") or "手动接入"),
             "会话 ID：" + str(row.get("external_session_id") or row.get("id", "—")),
             "工作目录：" + str(row.get("workspace") or detail.get("workspace") or "未提供"),
             "数据来源：" + str(row.get("source") or "手动事件"),
             "公开目标：\n" + _public_value(goal),
             "公开记忆摘要（非内部思维）：\n" + _public_value(memory),
             "模型：" + _public_value(detail.get("model") or row.get("model")),
             "用量（" + ("回合" if detail.get("usage_scope") == "turn" else "累计快照") + "；原始计量，不估算）：\n" + _public_value(usage),
             "公开计划：\n" + _public_value(plan),
             "工具调用摘要：\n" + _public_value(tools),
             "工具上报的文件改动（不含正文）：\n" + _public_value(files),
             "请求摘要：\n" + _public_value(request),
             "公开请求历史（逐项 ID / 文本摘要）：\n" + _public_value(requests),
             "同步：" + str(detail.get("sync_state") or row.get("sync_state") or "按来源提供的状态显示")]
    if row.get("message"):
        lines.append("消息：" + str(row["message"])[:4000])
    if _is_record(row):
        lines.insert(1, LOCAL_RECORD_NOTICE)
        connected = record_sync_connected(row)
        lines.insert(2, "观察连接：" + ("已接入 · 本地事件增量同步（不是实时服务）" if connected else "未连接 · 保留历史记录"))
        activity = detail.get("last_activity")
        if type(activity) in {int, float} and math.isfinite(activity) and 0 < activity <= time.time() + 300:
            lines.insert(3, "最近已记录活动：" + datetime.fromtimestamp(activity).strftime("%Y-%m-%d %H:%M:%S") + "（不是进程在线证明）")
    return "\n".join(lines)


class ToolSessionsPanelMixin:
    """Uses IntegrationPanel's queued worker and existing task history."""

    def _build_tool_sessions(self, parent):
        self.tool_rows = []
        self.tool_connections = []
        self.discovered_sessions = []
        self._tool_connection_id = ""
        self._discovery_connection_id = ""
        self._endpoint_tool_id = ""
        self._tool_endpoint_drafts = {}
        self._tool_auto_generation = 0
        self._tool_auto_pending = None
        self._tool_auto_busy = False
        self._session_preview_active = False
        self._tool_ui_buttons = []
        self._tool_observed_sessions = set()
        self._tool_observation_states = {}
        self._tool_observation_known = False
        self._tool_notice = TopNotice(self)
        set_color_roles(parent, background="APP_BG")
        title = tk.Label(parent, text="本机会话", anchor="w", font=(theme.BODY_FONT, 15, "bold"))
        set_color_roles(title, background="APP_BG", foreground="INK")
        title.pack(fill="x", pady=(0, pixels(self, 8)))
        connection_body = self._tool_section(parent, "1  选择工具 · 自动读取")
        self._label(connection_body, "选择工具")
        self.tool_choice = AnchoredCombobox(connection_body, state="readonly", font=(theme.BODY_FONT, 10))
        self.tool_choice.pack(fill="x", pady=(0, 6))
        self.tool_choice.bind("<<ComboboxSelected>>", self._tool_choice_changed)
        self.tool_description = tk.StringVar(self, "正在读取本机工具支持列表。")
        self._tool_message(connection_body, self.tool_description)
        quick = tk.Frame(connection_body, bg=theme.WHITE)
        quick.pack(fill="x", pady=(0, 6))
        self.tool_retry_button = self._button(quick, "重新读取 / 重试", lambda: self._schedule_auto_tool_preview(preserve=True))
        self._tool_ui_buttons.append(self.tool_retry_button)
        self._archive_open = False
        self.tool_archive_toggle = self._button(connection_body, "存档位置与连接选项  ▸", self._toggle_archive_options)
        self.tool_archive_toggle.pack_configure(side="top", anchor="w", pady=(0, 8))
        self.tool_archive_options = tk.Frame(connection_body, bg=theme.WHITE)
        archive = self.tool_archive_options
        self._label(archive, "会话存档位置（留空使用默认位置）")
        location = tk.Frame(archive)
        set_color_roles(location, background="WHITE")
        location.pack(fill="x", pady=(0, 4))
        self.tool_endpoint = SoftTtkEntry(location, font=(theme.BODY_FONT, 10))
        self.tool_endpoint.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.tool_location_button = self._button(location, "选择存档目录", self._choose_tool_location)
        self.tool_location_hint = tk.StringVar(self, "这里是工具保存会话的目录，不是项目目录。")
        self._tool_message(archive, self.tool_location_hint)
        actions = tk.Frame(archive)
        set_color_roles(actions, background="WHITE")
        actions.pack(fill="x", pady=(0, 6))
        self.tool_connect_button = self._button(actions, "应用目录 / 重试连接", self._connect_tool, primary=True)
        self.tool_disconnect_button = self._button(actions, "断开所选连接", self._disconnect_tool)
        self.tool_records_button = self._button(actions, "重新读取本机存档", self._connect_local_records)
        self._flow_buttons(actions, (self.tool_connect_button, self.tool_disconnect_button, self.tool_records_button))
        self._tool_ui_buttons += [self.tool_connect_button, self.tool_disconnect_button, self.tool_records_button]
        self._label(archive, "已连接的工具")
        self.tool_connection_choice = AnchoredCombobox(archive, state="readonly", font=(theme.BODY_FONT, 10))
        self.tool_connection_choice.pack(fill="x", pady=(0, 6))
        self.tool_connection_choice.bind("<<ComboboxSelected>>", self._tool_connection_changed)
        self.tool_connection_detail = tk.StringVar(self, "")
        self._tool_message(archive, self.tool_connection_detail)
        self.tool_feedback = tk.StringVar(self, "本地连接初始化中…")
        self.tool_feedback_label = self._tool_message(connection_body, self.tool_feedback)
        filter_body = archive  # Optional project filter belongs to advanced paths.
        self._label(filter_body, "按项目工作目录筛选（可选，留空显示全部候选）")
        folder = tk.Frame(filter_body)
        set_color_roles(folder, background="WHITE")
        folder.pack(fill="x", pady=(0, 6))
        self.tool_workspace = SoftTtkEntry(folder, font=(theme.BODY_FONT, 10))
        self.tool_workspace.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.tool_workspace_button = self._button(folder, "选择项目", self._choose_tool_workspace)
        self.tool_clear_filter_button = self._button(folder, "全部会话", self._clear_tool_workspace)
        actions = tk.Frame(filter_body)
        set_color_roles(actions, background="WHITE")
        actions.pack(fill="x", pady=(0, pixels(self, 12)))
        self.tool_discover_button = self._button(actions, "发现 / 刷新会话", self._discover_tool_sessions)
        self._flow_buttons(actions, (self.tool_discover_button,))
        self._tool_ui_buttons.append(self.tool_discover_button)
        selection_body = self._tool_section(parent, "2  勾选会话，再加入观察")
        self.tool_selection_count = tk.StringVar(self, "已选 0 个 · 选中不等于已加入观察")
        self.tool_selection_count_label = self._tool_message(selection_body, self.tool_selection_count)
        set_color_roles(self.tool_selection_count_label, foreground="INK")
        selection_actions = tk.Frame(selection_body)
        set_color_roles(selection_actions, background="WHITE")
        selection_actions.pack(fill="x", pady=(0, pixels(self, 8)))
        self.tool_select_all_button = self._button(selection_actions, "全选当前列表", lambda: self.tool_session_selection.select_all())
        self.tool_clear_selection_button = self._button(selection_actions, "取消选择", lambda: self.tool_session_selection.clear())
        self.tool_track_button = self._button(selection_actions, "加入观察（0）", self._track_tool_sessions, primary=True)
        self._flow_buttons(selection_actions, (self.tool_select_all_button, self.tool_clear_selection_button, self.tool_track_button))
        self._tool_ui_buttons += [self.tool_select_all_button, self.tool_clear_selection_button, self.tool_track_button]
        host = tk.Frame(selection_body)
        set_color_roles(host, background="WHITE")
        host.pack(fill="x", pady=(pixels(self, 4), pixels(self, 12)))
        self._tool_session_font = tkfont.Font(root=self, family=theme.BODY_FONT, size=10)
        self._tool_session_heading_font = tkfont.Font(root=self, family=theme.BODY_FONT, size=10, weight="bold")
        self._tool_session_style_pending = None
        self.tool_session_tree = ttk.Treeview(host, columns=("choose", "name", "observing", "state", "workspace"),
            show="headings", selectmode="extended", height=6, style="ToolSessions.Treeview")
        self._style_tool_session_tree()
        # ThemeController switches native Windows <-> clam. ttk styles belong
        # to a theme, so reapply metrics after its deferred theme-change event.
        self.tool_session_tree.bind("<<ThemeChanged>>", self._schedule_tool_session_style, add=True)
        self.tool_session_tree.bind("<Map>", self._schedule_tool_session_style, add=True)
        self.tool_session_tree.bind("<Destroy>", self._clear_tool_session_style, add=True)
        self.tool_session_tree.heading("choose", text="☐")
        self.tool_session_tree.column("choose", width=pixels(self, 44), minwidth=pixels(self, 40), anchor="center", stretch=False)
        for key, title, width in (("name", "会话 / ID", 250), ("observing", "观察", 110),
                                  ("state", "状态", 140), ("workspace", "工作目录", 220)):
            self.tool_session_tree.heading(key, text=title)
            self.tool_session_tree.column(key, width=pixels(self, width), minwidth=pixels(self, 70), stretch=True)
        vertical = ttk.Scrollbar(host, orient="vertical", command=self.tool_session_tree.yview)
        horizontal = ttk.Scrollbar(host, orient="horizontal", command=self.tool_session_tree.xview)
        self.tool_session_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        host.grid_columnconfigure(0, weight=1)
        self.tool_session_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tool_session_tree.bind("<<TreeviewSelect>>", self._tool_session_selected)
        self.tool_session_tree.bind("<Return>", lambda _event: self._track_tool_sessions())
        self.tool_session_selection = CheckedTreeSelection(
            self.tool_session_tree, self.tool_session_tree.get_children, enabled=lambda: not self.busy and not self._tool_auto_busy)
        self.tool_session_summary = tk.StringVar(self, "尚未读取会话")
        self.tool_session_summary_label = self._tool_message(selection_body, self.tool_session_summary)
        self.tool_session_summary_label.pack_configure(pady=(pixels(self, 4), pixels(self, 12)))
        advanced_host = tk.Frame(parent)
        set_color_roles(advanced_host, background="APP_BG")
        advanced_host.pack(fill="x", pady=(pixels(self, 4), pixels(self, 12)))
        self._tool_advanced_open = False
        self.tool_advanced_toggle = tk.Button(advanced_host, text="高级服务接入  ▸", command=self._toggle_tool_advanced,
                                             font=(theme.BODY_FONT, 10), relief="flat", bd=0,
                                             anchor="w", padx=pixels(self, 4), pady=pixels(self, 8),
                                             takefocus=True, highlightthickness=1)
        set_color_roles(self.tool_advanced_toggle, background="APP_BG", foreground="MUTED", activebackground="ACCENT_SOFT",
                        activeforeground="INK", highlightbackground="APP_BG", highlightcolor="ACCENT")
        self.tool_advanced_toggle.pack(fill="x")
        self.tool_advanced_card = SettingsCard(advanced_host, surface_role="WHITE", background_role="APP_BG",
                                               shadow=True, inset=12, radius=16)
        advanced = self.tool_advanced_card.body
        self.tool_native_button = self._button(advanced, "连接已有 Codex 服务", self._connect_native_service)
        self.tool_native_button.pack_configure(side="top", anchor="w", pady=(0, pixels(self, 6)))
        self._tool_message(advanced, tk.StringVar(self, "服务接口只列该服务加载的会话；通常选择工具即可自动读取本机存档。"))
        self._tool_ui_buttons += [self.tool_native_button, self.tool_location_button, self.tool_workspace_button, self.tool_clear_filter_button]
        for widget, explanation in (
            (self.tool_connect_button, "连接这个工具并列出本机主会话；不会自动加入观察，也不会启动模型。"),
            (self.tool_disconnect_button, "停止同步这个工具的连接，不取消外部工具中正在运行的任务。"),
            (self.tool_records_button, "重新读取所选工具的本地会话存档，不发送聊天内容，也不消耗模型 Token。"),
            (self.tool_location_button, "选择 .codex、.claude 等工具保存会话的目录；项目目录在下方筛选。"),
            (self.tool_workspace_button, "只显示在这个项目工作目录中的会话；不会改变工具的会话存档位置。"),
            (self.tool_clear_filter_button, "清空项目目录筛选并刷新候选会话；不会全选或自动加入观察。"),
            (self.tool_discover_button, "刷新候选会话。刷新不会选中或加入新的会话。"),
            (self.tool_select_all_button, "勾选当前显示的候选，不会选中以后新发现的会话，也不会自动加入观察。"),
            (self.tool_clear_selection_button, "取消当前勾选，不停止已加入的观察，不删除会话记录。"),
            (self.tool_track_button, "把当前勾选、尚未观察的会话加入监工；每次最多新增 20 个。加入成功后顶部提示，行内显示“已加入观察”。"),
            (self.tool_native_button, "只读连接已有 Codex 服务。它可能只包含已加载会话，全部存档请用上方本地连接。"),
            (self.tool_advanced_toggle, "展开已有 Codex 服务的只读连接选项。通常使用上方本地存档连接即可。"),
        ):
            Tooltip(widget, explanation)

    def _tool_section(self, parent, title):
        heading = tk.Label(parent, text=title, anchor="w", font=(theme.BODY_FONT, 11, "bold"))
        set_color_roles(heading, background="APP_BG", foreground="MUTED")
        heading.pack(fill="x", padx=pixels(self, 4), pady=(pixels(self, 12), pixels(self, 8)))
        card = SettingsCard(parent, surface_role="WHITE", background_role="APP_BG", shadow=True, inset=12, radius=16)
        card.pack(fill="x", pady=(0, pixels(self, 12)))
        return card.body

    def _toggle_tool_advanced(self):
        self._tool_advanced_open = not self._tool_advanced_open
        self.tool_advanced_toggle.configure(text="高级服务接入  ▾" if self._tool_advanced_open else "高级服务接入  ▸")
        if self._tool_advanced_open:
            self.tool_advanced_card.pack(fill="x", pady=(pixels(self, 4), pixels(self, 8)))
        else:
            self.tool_advanced_card.pack_forget()

    def _style_tool_session_tree(self):
        """Give this table font-sized rows without changing other task lists."""
        tree = self.tool_session_tree
        if not tree.winfo_exists():
            return
        style = ttk.Style(tree)
        row_padding = pixels(tree, 6)
        rowheight = self._tool_session_font.metrics("linespace") + 2 * row_padding
        # style.configure itself emits ThemeChanged. Compare first so the
        # deferred callback settles instead of continually queuing itself.
        if (str(style.lookup("ToolSessions.Treeview", "font")) != str(self._tool_session_font)
                or int(style.lookup("ToolSessions.Treeview", "rowheight") or 0) != rowheight):
            style.configure("ToolSessions.Treeview", font=self._tool_session_font, rowheight=rowheight)
        heading_padding = (pixels(tree, 8), row_padding)
        current_padding = tuple(int(str(value)) for value in tree.tk.splitlist(
            style.lookup("ToolSessions.Treeview.Heading", "padding")))
        if (str(style.lookup("ToolSessions.Treeview.Heading", "font")) != str(self._tool_session_heading_font)
                or current_padding != heading_padding):
            style.configure("ToolSessions.Treeview.Heading", font=self._tool_session_heading_font,
                            padding=heading_padding)
        # Colors and selected/focus states deliberately inherit Treeview's
        # semantic palette, which the shared theme controller owns.

    def _schedule_tool_session_style(self, _event=None):
        if self._tool_session_style_pending is None:
            def apply():
                self._tool_session_style_pending = None
                self._style_tool_session_tree()
            self._tool_session_style_pending = self.tool_session_tree.after_idle(apply)

    def _clear_tool_session_style(self, event):
        if event.widget is self.tool_session_tree and self._tool_session_style_pending is not None:
            self.tool_session_tree.after_cancel(self._tool_session_style_pending)
            self._tool_session_style_pending = None

    def _tool_message(self, parent, variable):
        label = tk.Label(parent, textvariable=variable, anchor="w", justify="left", font=(theme.BODY_FONT, 10))
        set_color_roles(label, background="WHITE", foreground="MUTED")
        label.pack(fill="x", pady=(2, 8))
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(120, event.width - 4)))
        return label

    def _selected_tool(self):
        index = self.tool_choice.current()
        return self.tool_rows[index] if 0 <= index < len(self.tool_rows) else None

    def _selected_connection(self):
        index = self.tool_connection_choice.current()
        return self.tool_connections[index] if 0 <= index < len(self.tool_connections) else None

    def _tool_choice_changed(self, _event=None):
        row = self._selected_tool()
        if row:
            if row["id"] != self._endpoint_tool_id:
                if self._endpoint_tool_id:
                    self._tool_endpoint_drafts[self._endpoint_tool_id] = self.tool_endpoint.get()
                self.tool_endpoint.configure(state="normal")
                self.tool_endpoint.delete(0, "end")
                self.tool_endpoint.insert(0, self._tool_endpoint_drafts.get(row["id"], ""))
                self._endpoint_tool_id = row["id"]
            label = "本机存档 · 只读" if row.get("supported") else "暂不支持会话发现"
            self.tool_description.set(label)
            default = row.get("default_location") or "由该工具独立适配器检测"
            self.tool_location_hint.set("默认存档：" + default)
        self._tool_controls()
        if _event is not None:
            if row and row.get("supported"):
                self._schedule_auto_tool_preview()
            else:
                self._stop_auto_tool_preview()
                self.tool_feedback.set("此工具暂不支持自动读取。可选择 Codex / Claude Code / Antigravity，或展开高级手动接入。")
                self._tool_controls()

    def _toggle_archive_options(self):
        self._archive_open = not self._archive_open
        self.tool_archive_toggle.configure(text="存档位置与连接选项  ▾" if self._archive_open else "存档位置与连接选项  ▸")
        if self._archive_open:
            self.tool_archive_options.pack(fill="x", pady=(0, 8), before=self.tool_feedback_label)
        else:
            self.tool_archive_options.pack_forget()

    def preview_sessions(self, tool_id=None):
        """Workspace entry: reuse drafts, preview only, preserve checked rows."""
        if self.closed or self.busy or self._tool_auto_busy:
            return
        current = self._selected_tool()
        index = next((i for i, row in enumerate(self.tool_rows) if row["id"] == tool_id and row.get("supported")), None)
        if index is None and (not current or not current.get("supported")):
            saved = self.manager.list_tool_connections()
            preferred = next((row.get("tool_id") for row in saved if row.get("state") == "connected"), "codex")
            index = next((i for i, row in enumerate(self.tool_rows) if row["id"] == preferred and row.get("supported")), None)
        same_tool = index is None or bool(current and self.tool_rows[index]["id"] == current["id"])
        if index is not None:
            self.tool_choice.current(index)
            self._tool_choice_changed()
        self._schedule_auto_tool_preview(preserve=same_tool)

    def _schedule_auto_tool_preview(self, *, preserve=False):
        """Coalesce real user switches; stale workers cannot repaint a later tool."""
        selected = self._selected_tool()
        if self.closed or self.busy or not selected or not selected.get("supported"):
            return
        self._tool_auto_generation += 1
        generation = self._tool_auto_generation
        if self._tool_auto_pending is not None:
            self.after_cancel(self._tool_auto_pending)
        self._tool_auto_busy = True
        self._tool_auto_restore = ({str(row["id"]) for row in self._selected_discovered_sessions()}, self.tool_session_tree.yview()) if preserve else (set(), (0, 1))
        if not preserve:
            self._discovery_connection_id = ""
            self.discovered_sessions = []
            self.tool_session_tree.delete(*self.tool_session_tree.get_children())
        self.tool_feedback.set("正在后台读取主会话…")
        self._tool_controls()

        def start():
            self._tool_auto_pending = None
            row = self._selected_tool()
            if not row or not row.get("supported") or generation != self._tool_auto_generation or self.closed:
                return
            tool_id, endpoint, workspace = row["id"], self.tool_endpoint.get().strip(), self.tool_workspace.get().strip()
            def run():
                try:
                    connections = self.manager.list_tool_connections()
                    matching = [item for item in connections if item.get("tool_id") == tool_id]
                    connection = next((item for item in matching if item.get("state") == "connected" and item.get("endpoint", "") == endpoint), None)
                    if connection is None and not endpoint:
                        connection = next((item for item in matching if item.get("state") == "connected"), None)
                    if connection is None:
                        # Connection lifetime belongs to the app-owned native
                        # service. A preview/read retry is never permission to
                        # undo Disconnect or start an advanced proxy transport.
                        saved = next((item for item in matching if item.get("endpoint", "") == endpoint), None)
                        if saved is None and not endpoint and matching:
                            saved = matching[0]
                        if saved and (saved.get("autoconnect") is False or saved.get("state") == "disconnected"):
                            self.messages.put(("tool_auto", (generation, tool_id, saved, [], "")))
                            return
                        raise ValueError("后台尚未连接这个存档；找到默认存档后会自动更新。自定义目录需点击‘应用目录 / 重试连接’确认。")
                    rows = self.manager.discover_tool_sessions(connection["id"], workspace=workspace)
                    self.messages.put(("tool_auto", (generation, tool_id, connection, rows, "")))
                except Exception as error:
                    self.messages.put(("tool_auto", (generation, tool_id, None, [], str(error))))
            threading.Thread(target=run, name="remchat-auto-session-preview", daemon=True).start()
        self._tool_auto_pending = self.after(150, start)

    def _accept_auto_tool_preview(self, payload):
        generation, tool_id, connection, rows, error = payload
        selected = self._selected_tool()
        if self.closed or generation != self._tool_auto_generation or not selected or selected["id"] != tool_id:
            return
        self._tool_auto_busy = False
        if error:
            self._tool_failed(error + "；点击‘重新读取 / 重试’，或展开‘存档位置与连接选项’检查目录。")
            return
        if connection.get("autoconnect") is False or connection.get("state") == "disconnected":
            self._tool_connection_id = str(connection["id"])
            self._discovery_connection_id = ""
            self.discovered_sessions = []
            self.tool_session_tree.delete(*self.tool_session_tree.get_children())
            self._refresh_tool_sessions()
            notice = "已手动断开；后台不会自动重连。展开‘存档位置与连接选项’，点击‘应用目录 / 重试连接’恢复。"
            self.tool_feedback.set(notice)
            self.tool_session_summary.set("尚未连接；已有观察记录保留，不取消外部任务。")
            set_color_roles(self.tool_feedback_label, foreground="MUTED")
            self.status.set(notice)
            self._tool_controls()
            return
        if not self.tool_endpoint.get().strip() and connection.get("endpoint"):
            # Reflect the saved custom archive actually reused by the worker;
            # never replace an explicit per-tool draft with a default path.
            self.tool_endpoint.configure(state="normal")
            self.tool_endpoint.insert(0, str(connection["endpoint"]))
            self._tool_endpoint_drafts[tool_id] = str(connection["endpoint"])
        self._tool_connection_id = str(connection["id"])
        self._refresh_tool_sessions()
        self._discovery_connection_id = str(connection["id"])
        self.discovered_sessions = rows
        self.tool_session_tree.delete(*self.tool_session_tree.get_children())
        selected_ids, yview = self._tool_auto_restore
        for index, session in enumerate(rows):
            self.tool_session_tree.insert("", "end", iid=f"session-{index}", text=str(session.get("name", "会话")),
                values=("☐", str(session.get("name", "会话")), "待核对", _state_text(session), session.get("workspace") or "未提供"))
            if str(session["id"]) in selected_ids:
                self.tool_session_tree.selection_add(f"session-{index}")
        self.tool_session_tree.yview_moveto(yview[0])
        self._refresh_observed_sessions()
        self.tool_session_summary.set(f"{len(rows)} 个主会话" if rows else "没有匹配的主会话")
        self.tool_feedback.set("本地记录已连接 · 非实时任务状态")
        set_color_roles(self.tool_feedback_label, foreground="MUTED")
        self.status.set(self.tool_session_summary.get())
        self._tool_controls()

    def _stop_auto_tool_preview(self):
        self._tool_auto_generation += 1
        self._tool_auto_busy = False
        if self._tool_auto_pending is not None:
            try:
                self.after_cancel(self._tool_auto_pending)
            except tk.TclError:
                pass
            self._tool_auto_pending = None

    def _tool_connection_changed(self, _event=None):
        row = self._selected_connection()
        identifier = str(row.get("id", "")) if row else ""
        if identifier != self._tool_connection_id:
            self._tool_connection_id = identifier
            self._discovery_connection_id = ""
            self.discovered_sessions = []
            self.tool_session_tree.delete(*self.tool_session_tree.get_children())
            self.tool_session_summary.set("尚未读取会话")
            if row:
                tool_index = next((i for i, tool in enumerate(self.tool_rows) if tool["id"] == row.get("tool_id")), None)
                if tool_index is not None:
                    self.tool_choice.current(tool_index)
                    self._tool_choice_changed()
                    self.tool_endpoint.configure(state="normal")
                    self.tool_endpoint.delete(0, "end")
                    self.tool_endpoint.insert(0, row.get("endpoint", ""))
        if row:
            state = CONNECTION_STATES.get(str(row.get("state", "unknown")), "状态未知")
            notice = "本机记录 · 非实时服务\n" if _is_record(row) else ""
            location = "\n存档位置：" + str(row["location"]) if row.get("location") else ""
            self.tool_connection_detail.set(notice + f"{row.get('name', row.get('tool_id', '工具'))} · {state} · {row.get('source', '')}{location}\n{row.get('message', '')}")
            if not self._tool_auto_busy:
                self.tool_feedback.set(f"{row.get('name', row.get('tool_id', '工具'))} · {state} · " + ("本地记录（非实时任务状态）" if _is_record(row) else "只读已有服务"))
                set_color_roles(self.tool_feedback_label, foreground="ERROR" if row.get("state") in {"failed", "error"} else "MUTED")
            self.tool_discover_button.configure(text="发现 / 刷新记录" if _is_record(row) else "发现 / 刷新会话")
        self._tool_controls()

    def _tool_controls(self):
        if not hasattr(self, "tool_choice"):
            return
        tool, connection = self._selected_tool(), self._selected_connection()
        busy = self.busy or self._tool_auto_busy
        ready = connection is not None and connection.get("state") in {"connected", "ready"}
        self.tool_connect_button.configure(state="normal" if tool and tool.get("supported") and not busy else "disabled")
        self.tool_retry_button.configure(state="normal" if tool and tool.get("supported") and not busy else "disabled")
        self.tool_records_button.configure(state="normal" if tool and tool.get("supported") and not busy else "disabled")
        for button in (self.tool_location_button, self.tool_workspace_button, self.tool_clear_filter_button):
            button.configure(state="disabled" if busy else "normal")
        self.tool_native_button.configure(state="normal" if tool and tool.get("id") == "codex" and not busy else "disabled")
        self.tool_endpoint.configure(state="disabled" if busy else "normal")
        self.tool_workspace.configure(state="disabled" if busy else "normal")
        self.tool_disconnect_button.configure(state="normal" if connection and not busy else "disabled")
        self.tool_discover_button.configure(state="normal" if ready and not busy else "disabled")
        selected = self._selected_discovered_sessions()
        pending = [row for row in selected if str(row.get("id", "")) not in self._tool_observed_sessions]
        valid = ready and connection.get("id") == self._discovery_connection_id
        self.tool_track_button.configure(state="normal" if valid and self._tool_observation_known and 1 <= len(pending) <= 20 and not busy else "disabled",
                                         text=f"加入观察（{len(pending)}）" if pending or not selected else f"已加入观察（{len(selected)}）")
        count = f"已选 {len(selected)} 个"
        if selected:
            count += f" · 已加入 {len(selected) - len(pending)} 个 · 待加入 {len(pending)} 个"
        else:
            count += " · 选中不等于已加入观察"
        if len(pending) > 20:
            count += "\n每次最多新增 20 个，请减少勾选、分批加入。"
        if self.discovered_sessions and not self._tool_observation_known:
            count = f"已选 {len(selected)} 个 · 观察状态暂不可读取，请刷新后再加入。"
        self.tool_selection_count.set(count)
        self.tool_select_all_button.configure(state="normal" if self.discovered_sessions and not busy else "disabled")
        self.tool_clear_selection_button.configure(state="normal" if selected and not busy else "disabled")
        self.tool_session_selection.sync()
        self.tool_choice.configure(state="disabled" if self.busy else "readonly")
        self.tool_connection_choice.configure(state="disabled" if busy else "readonly")

    def _selected_discovered_sessions(self):
        rows = []
        for item in self.tool_session_tree.selection():
            try:
                index = int(item.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            if 0 <= index < len(self.discovered_sessions):
                rows.append(self.discovered_sessions[index])
        return rows

    def _refresh_observed_sessions(self):
        """Only persisted monitoring rows qualify as already added, not selection."""
        try:
            tasks = self.manager.list_tasks()
        except (OSError, ValueError, RuntimeError):
            self._tool_observation_known = False
            for item in self.tool_session_tree.get_children():
                self.tool_session_tree.set(item, "observing", "待核对")
            return
        connection = self._discovery_connection_id
        self._tool_observation_states = {
            str(row.get("external_session_id", "")): bool(row.get("monitoring", True))
            for row in tasks if row.get("connection_id") == connection and row.get("external_session_id")
        }
        self._tool_observed_sessions = {identifier for identifier, active in self._tool_observation_states.items() if active}
        self._tool_observation_known = True
        for index, row in enumerate(self.discovered_sessions):
            item = f"session-{index}"
            if self.tool_session_tree.exists(item):
                state = self._tool_observation_states.get(str(row.get("id", "")))
                self.tool_session_tree.set(item, "observing", "已加入观察" if state else "已停止观察" if state is False else "未加入")

    def _refresh_tool_sessions(self):
        if not hasattr(self, "tool_choice"):
            return
        if not hasattr(self.manager, "tool_catalog"):
            self.tool_description.set("当前安装尚不支持原生会话连接；高级手动接入仍可用。")
            self._tool_controls()
            return
        selected = self._selected_tool()
        identifier = selected.get("id") if selected else None
        try:
            tools = self.manager.tool_catalog()
            connections = self.manager.list_tool_connections()
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            self.tool_description.set("本机会话接入不可用；聊天与高级手动接入仍可用。" + str(error))
            self.tool_connect_button.configure(state="disabled")
            self.tool_records_button.configure(state="disabled")
            self.tool_discover_button.configure(state="disabled")
            self.tool_track_button.configure(state="disabled")
            return
        self.tool_rows = tools
        self.tool_choice.set_values([str(row.get("name", row["id"])) + ("" if row.get("supported") else " · 暂不支持") for row in self.tool_rows])
        tool_index = next((i for i, row in enumerate(self.tool_rows) if row["id"] == identifier), 0)
        if self.tool_rows:
            self.tool_choice.current(tool_index)
        current = self._tool_connection_id
        self.tool_connections = connections
        self.tool_connection_choice.set_values([f"{row.get('name', row.get('tool_id', '工具'))} · {'本地记录（非实时）' if _is_record(row) else CONNECTION_STATES.get(row.get('state'), '状态未知')} · {str(row['id'])[:8]}" for row in self.tool_connections])
        connection_index = next((i for i, row in enumerate(self.tool_connections) if row["id"] == current and row.get("tool_id") == identifier),
                                next((i for i, row in enumerate(self.tool_connections) if row.get("tool_id") == identifier), -1))
        if connection_index >= 0:
            self.tool_connection_choice.current(connection_index)
        else:
            self.tool_connection_choice.set("")
        self._tool_choice_changed()
        self._tool_connection_changed()
        self._refresh_observed_sessions()
        self._tool_controls()

    def _tool_failed(self, message):
        self.tool_feedback.set("操作失败：" + str(message) + "\n请检查连接地址或本机工具，再重试。已有观察记录保留，不会被判定为完成。")
        set_color_roles(self.tool_feedback_label, foreground="ERROR")
        self._tool_notice.show("操作未完成：" + str(message)[:180], error=True)
        self._refresh_observed_sessions()
        self._tool_controls()

    def _connect_tool(self):
        row = self._selected_tool()
        if self.busy or not row or not row.get("supported"):
            return
        self._connect_source(str(row["id"]), self.tool_endpoint.get().strip())

    def _connect_source(self, identifier, endpoint, *, reset_filter=True):
        if self.busy:
            return
        if reset_filter:
            self.tool_workspace.delete(0, "end")
        self.tool_feedback.set("正在连接本机工具…")
        def complete(connection):
            self._tool_connection_id = str(connection["id"])
            self._discovery_connection_id = ""
            self.discovered_sessions = []
            self.tool_session_tree.delete(*self.tool_session_tree.get_children())
            tool_index = next((i for i, tool in enumerate(self.tool_rows) if tool["id"] == identifier), None)
            if tool_index is not None:
                self.tool_choice.current(tool_index)
            self._refresh_tool_sessions()
            if connection.get("state") not in {"connected", "ready"}:
                self._tool_failed(connection.get("message") or "连接未建立。")
            else:
                self.status.set("已连接，正在列出会话；不会自动观察或调用模型。")
                self._discover_tool_sessions()
        self._work("正在连接本机工具…", lambda: self.manager.connect_tool(identifier, endpoint=endpoint), complete, on_error=self._tool_failed)
        self._tool_controls()

    def _disconnect_tool(self):
        row = self._selected_connection()
        if self.busy or not row:
            return
        self._stop_auto_tool_preview()
        identifier = str(row["id"])
        def complete(_value):
            self._discovery_connection_id = ""
            self.discovered_sessions = []
            self.tool_session_tree.delete(*self.tool_session_tree.get_children())
            self.tool_feedback.set("已断开连接；不会取消外部会话。同步中断不表示任务完成。")
            self.tool_session_summary.set("重新连接并发现会话后，可继续选择观察。")
            self._refresh_tool_sessions()
            self.status.set("工具连接已断开；外部会话继续运行，不推断任务完成。")
        self._work("正在断开工具连接…", lambda: self.manager.disconnect_tool(identifier), complete, on_error=self._tool_failed)
        self._tool_controls()

    def _connect_local_records(self):
        row = self._selected_tool()
        if self.busy or not row or not row.get("supported"):
            return
        endpoint = self.tool_endpoint.get().strip()
        if row["id"] == "codex" and (endpoint == "native" or endpoint.startswith("native:") or endpoint.endswith(".sock")):
            endpoint = ""
        self._connect_source(str(row["id"]), endpoint)

    def _connect_native_service(self):
        row = self._selected_tool()
        if not self.busy and row and row.get("id") == "codex":
            self._connect_source("codex", "native")

    def _choose_tool_location(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self, title="选择工具保存会话的存档目录", mustexist=True)
        if path:
            self.tool_endpoint.delete(0, "end")
            self.tool_endpoint.insert(0, path)

    def _clear_tool_workspace(self):
        if self.busy:
            return
        self.tool_workspace.delete(0, "end")
        self._discover_tool_sessions()

    def _choose_tool_workspace(self):
        path = filedialog.askdirectory(parent=self, title="按项目文件夹筛选会话", mustexist=True)
        if path:
            self.tool_workspace.delete(0, "end")
            self.tool_workspace.insert(0, path)

    def _discover_tool_sessions(self):
        row = self._selected_connection()
        if self.busy or not row or row.get("state") not in {"connected", "ready"}:
            return
        identifier, workspace = str(row["id"]), self.tool_workspace.get().strip()
        tool = next((item for item in self.tool_rows if item["id"] == row.get("tool_id")), {})
        if _known_record_directory(tool, workspace, row):
            # Recover the exact screenshot workflow: the user selected a
            # storage directory in what used to be an ambiguous filter field.
            self.tool_endpoint.delete(0, "end")
            self.tool_endpoint.insert(0, workspace)
            self.tool_workspace.delete(0, "end")
            self.tool_feedback.set("这个目录是会话存档，不是项目工作目录。已改为从该存档连接并列出会话。")
            self._connect_source(str(row["tool_id"]), workspace)
            return
        self.tool_session_summary.set("正在读取本机会话；只发现，不会自动观察。")
        def complete(rows):
            self._discovery_connection_id = identifier
            self.discovered_sessions = rows
            self.tool_session_tree.delete(*self.tool_session_tree.get_children())
            for index, session in enumerate(rows):
                self.tool_session_tree.insert("", "end", iid=f"session-{index}", text=str(session.get('name', '会话')),
                    values=("☐", str(session.get('name', '会话')), "待核对", _state_text(session),
                            session.get("workspace") or "未提供"))
            self._refresh_observed_sessions()
            record = _is_record(row) or any(_is_record(session) for session in rows)
            notice = LOCAL_RECORD_NOTICE + "\n" if record else ""
            scope = "主会话（子 Agent / 自动审查不独立列出）" if row.get("tool_id") == "codex" else "会话记录" if record else "会话"
            self.tool_session_summary.set(notice + (f"发现 {len(rows)} 个{scope}。勾选左侧方框，再点击“加入观察”。支持全选当前列表、空格勾选，以及 Ctrl / Shift 多选。" if rows else "没有发现符合条件的会话。可清空文件夹筛选、检查来源目录，再刷新。"))
            if not rows and row.get("tool_id") == "codex" and not record:
                empty_message = "这个 Codex 服务未加载会话，不代表本机没有存档。\n请点“重新读取本机存档”，默认读取 .codex\\sessions；选择要观察的会话即可。"
                self.tool_feedback.set(empty_message)
                self.tool_session_summary.set(empty_message)
                set_color_roles(self.tool_feedback_label, foreground="ROSE")
            self.status.set(f"发现完毕 · {len(rows)} 个{'会话记录' if record else '服务会话'}（已列出本次发现的全部主会话）；仅观察你明确选中的会话。")
            self._tool_controls()
        self._work("正在发现本机会话…", lambda: self.manager.discover_tool_sessions(identifier, workspace=workspace), complete, on_error=self._tool_failed)
        self._tool_controls()

    def _tool_session_selected(self, _event=None):
        selected = self._selected_discovered_sessions()
        if selected:
            row = selected[0]
            notice = LOCAL_RECORD_NOTICE + "\n" if _is_record(row) else ""
            child_hint = f"\n附属子 Agent / 审查活动：{row.get('child_count', 0)}（元数据；在项目过程按需展开）" if row.get("session_kind") == "user" else ""
            metadata_hint = ("\n仅列表摘要 · 正文和 Token 用量未提供，不记为零。" if row.get("metadata_only") else "")
            if row.get("discovery_message"):
                metadata_hint += "\n" + str(row["discovery_message"])
            self.tool_session_summary.set(notice + f"已选 {len(selected)} 个会话\n会话 ID：{row.get('id', '')}\n工作目录：{row.get('workspace') or '未提供'}\n来源：{row.get('source') or '本机工具'}" + child_hint + metadata_hint)
        else:
            self.tool_session_summary.set("尚未选择会话" if self.discovered_sessions else "尚未发现会话")
        self._tool_controls()

    def _track_tool_sessions(self):
        row = self._selected_connection()
        if self.busy or self._tool_auto_busy or not row or row.get("state") not in {"connected", "ready"} or row.get("id") != self._discovery_connection_id:
            return
        selected = self._selected_discovered_sessions()
        self._refresh_observed_sessions()
        if not self._tool_observation_known:
            self._tool_notice.show("观察状态暂不可读取，请刷新列表后重试。", error=True)
            self._tool_controls()
            return
        identifiers = list(dict.fromkeys(str(session["id"]) for session in selected
                                        if str(session["id"]) not in self._tool_observed_sessions))
        if not identifiers:
            self.tool_session_summary.set("选中的会话均已加入观察，无需重复添加。" if selected else "请先勾选至少一个会话。")
            self.tool_session_tree.focus_set()
            return
        if len(identifiers) > 20:
            self.tool_session_summary.set("每次最多新增 20 个会话，请减少勾选并分批加入；没有自动截断选择。")
            self._tool_notice.show("每次最多新增 20 个会话，请减少勾选、分批加入。", error=True)
            return
        connection_id = str(row["id"])
        def complete(tasks):
            result = tasks if isinstance(tasks, (list, tuple)) else []
            actual = {str(task.get("external_session_id", "")) for task in result if isinstance(task, dict)
                      and task.get("connection_id") == connection_id and task.get("monitoring", True)} & set(identifiers)
            if not actual:
                self._tool_failed("没有会话加入观察，请刷新列表后重试。")
                return
            partial = len(actual) != len(identifiers)
            message = f"已加入观察 {len(actual)} 个会话。" + (f"另有 {len(identifiers) - len(actual)} 个未加入，请刷新后重试。" if partial else "")
            names = [str(session.get("name") or session["id"]) for session in selected if str(session["id"]) in actual]
            self._tool_notice.show(f"已加入观察 · {len(actual)} 个会话\n" + "、".join(names[:2])[:100] + (" 等" if len(names) > 2 else ""), error=partial)
            self.tool_feedback.set(message)
            self.tool_session_summary.set(message)
            self.status.set(message)
            set_color_roles(self.tool_feedback_label, foreground="ERROR" if partial else "SUCCESS")
            self._refresh_observed_sessions()
            self._tool_controls()
            self.refresh()
        self._work("正在加入选中的会话…", lambda: self.manager.track_tool_sessions(connection_id, identifiers), complete, on_error=self._tool_failed)
        self._tool_controls()
