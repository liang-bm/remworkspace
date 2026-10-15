"""Optional human-readable journey: goal, reported stages, results and hand-offs.

THESIS: Remember the goal, follow a stage, inspect its evidence.
OWN-WORLD: RemChat's quiet blue-white / blue-gray native desktop controls.
STORY: Goal -> reported stages -> results; instructions and hand-offs remain.
FORM: Selectable hop-grid. Arrows mean declared dependencies, never guesses.
"""

from __future__ import annotations

from datetime import datetime
import json
import tkinter as tk
from .font_source import native_font
from .scroll_surface import ScrollCanvas
from .soft_fields import SoftTtkEntry
from tkinter import font as tkfont, ttk
from .localized_dialogs import filedialog
import uuid

from ..text_editing import bind_text_editing
from ..clipboard import copy_text
from ..project_trace.presentation import event_timeline, phase_summary, visible_changes
from . import theme
from .theme_controller import set_color_roles

STATE_LABELS = {"pending": "待处理", "running": "进行中", "done": "已完成", "failed": "失败", "cancelled": "已取消"}
OP_LABELS = {"created": "新增", "modified": "修改", "deleted": "删除", "discovered": "首次发现", "baseline": "基线"}
EVENT_LABELS = {"goal": "原始目标", "goal_update": "目标更新", "request": "新指令", "checkpoint": "上下文交接", "memory": "记忆状态", "tool": "工具调用", "usage": "模型用量", "plan": "计划更新", "step": "阶段状态"}
TOOL_STATE_LABELS = {"started": "已开始", "completed": "已完成", "failed": "失败"}


class ProjectProcessPanel:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.trace = owner.integration_manager.project_trace
        self.root = tk.Toplevel(owner.root)
        self.root.geometry("1080x800")
        self.root.minsize(760, 640)
        set_color_roles(self.root, background="WHITE")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.selected_step = self.selected_change = self.selected_event = ""
        self.view_revision = None
        self._title_cache = {}
        self.key = self.after_id = self.record_dialog = None
        self.data = self.trace.snapshot()
        self.view_steps = []
        self.active_revision = 0
        self.include_noise = tk.BooleanVar(value=False)
        self.file_query = tk.StringVar(value="")
        self.event_query = tk.StringVar(value="")
        toolbar = self._frame(self.root)
        toolbar.pack(fill="x", padx=18, pady=(14, 8))
        self.status = self._label(toolbar, bold=True)
        self.status.pack(side="left", expand=True, fill="x")
        self.record_button = owner._setting_button(toolbar, "记录阶段", self._record)
        self.record_button.pack(side="right", padx=(8, 0))
        self.more_button = owner._setting_button(toolbar, "更多 ▾", self._more)
        self.more_button.pack(side="right")
        self.notice = self._label(self.root, muted=True)
        self.notice.pack(fill="x", padx=18, pady=(0, 8))
        goal_shell = self._frame(self.root)
        goal_shell.pack(fill="x", padx=18)
        self.goal_label = self._label(goal_shell, bold=True)
        self.goal_label.pack(side="left", anchor="n", padx=(0, 12))
        self.goal_text = self._text(goal_shell, height=2)
        goal_scroll = ttk.Scrollbar(goal_shell, command=self.goal_text.yview)
        goal_scroll.pack(side="right", fill="y")
        self.goal_text.configure(yscrollcommand=goal_scroll.set, state="disabled")
        self.goal_text.pack(fill="x", expand=True)
        self.original_button = owner._setting_button(self.root, "查看最早目标", self._show_original_goal)
        self.original_button.pack(anchor="w", padx=18, pady=(2, 0))
        self.progress = self._label(self.root)
        self.progress.pack(fill="x", padx=18, pady=(8, 10))
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.phase_page, self.events_page, self.session_page = self._frame(self.tabs), self._frame(self.tabs), self._frame(self.tabs)
        self.tabs.add(self.phase_page, text="阶段流程")
        self.tabs.add(self.events_page, text="指令与交接")
        self.tabs.add(self.session_page, text="记忆 · 工具 · 用量")
        revisions = self._frame(self.phase_page)
        revisions.pack(fill="x", pady=(10, 6))
        self.revision_label = self._label(revisions)
        self.revision_label.pack(side="left", padx=(0, 8))
        self.revision_choice = ttk.Combobox(revisions, state="readonly", width=28)
        self.revision_choice.pack(side="left")
        self.revision_choice.bind("<<ComboboxSelected>>", self._select_revision)
        self.revision_note = self._label(revisions, muted=True)
        self.revision_note.pack(side="left", padx=12, fill="x", expand=True)
        flow = self._frame(self.phase_page)
        flow.pack(fill="x")
        self.canvas = ScrollCanvas(flow, height=190, bd=0, highlightthickness=1, takefocus=True)
        set_color_roles(self.canvas, background="WHITE", highlightbackground="LINE", highlightcolor="ACCENT_DEEP")
        scroll = ttk.Scrollbar(flow, command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw_steps())
        self.canvas.bind("<Button-1>", self._click_step)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(1, "units"))
        self.canvas.bind("<Right>", lambda _event: self._move_step(1))
        self.canvas.bind("<Left>", lambda _event: self._move_step(-1))
        self.step_info = self._label(self.phase_page, muted=True)
        self.step_info.pack(fill="x", pady=(6, 8))
        filters = self._frame(self.phase_page)
        filters.pack(fill="x", pady=(0, 8))
        self.search_label = self._label(filters)
        self.search_label.pack(side="left", padx=(0, 8))
        self.search = SoftTtkEntry(filters, textvariable=self.file_query, width=24)
        self.search.pack(side="left", fill="x", expand=True)
        self.noise_toggle = ttk.Checkbutton(filters, variable=self.include_noise, command=self._populate_files)
        self.noise_toggle.pack(side="left", padx=12)
        self.file_count = self._label(filters, muted=True)
        # Compact toolbar labels must reserve their natural width, rather than
        # shrink into multiple lines when a translated entry expands.
        for label in (self.search_label, self.file_count):
            label.unbind("<Configure>")
            label.configure(wraplength=0)
        self.file_count.pack(side="right")
        self.file_query.trace_add("write", lambda *_args: self._populate_files())
        lower = self._panes(self.phase_page)
        files = self._frame(lower)
        self.files = ttk.Treeview(files, columns=("kind",), show="tree headings", selectmode="browse", style="Journey.Treeview")
        self.files.column("#0", width=240, minwidth=160)
        self.files.column("kind", width=64, minwidth=64, stretch=False)
        self._scroll_list(files, self.files)
        self.files.bind("<<TreeviewSelect>>", self._select_change)
        detail_shell = self._frame(lower)
        self.detail = self._text(detail_shell)
        self._scroll_text(detail_shell, self.detail)
        lower.add(files, minsize=220, width=340)
        lower.add(detail_shell, minsize=280)
        self.event_notice = self._label(self.events_page, muted=True)
        self.event_notice.pack(fill="x", pady=(10, 8))
        event_filters = self._frame(self.events_page)
        event_filters.pack(fill="x", pady=(0, 8))
        self.event_search_label = self._label(event_filters)
        self.event_search_label.pack(side="left", padx=(0, 8))
        SoftTtkEntry(event_filters, textvariable=self.event_query).pack(side="left", fill="x", expand=True)
        self.event_filter = ttk.Combobox(event_filters, state="readonly", width=14)
        self.event_filter.pack(side="right", padx=(12, 0))
        self.event_filter.bind("<<ComboboxSelected>>", lambda _event: self._populate_events())
        self.event_query.trace_add("write", lambda *_args: self._populate_events())
        event_split = self._panes(self.events_page)
        event_shell = self._frame(event_split)
        self.events = ttk.Treeview(event_shell, columns=("time", "kind"), show="tree headings", selectmode="browse", style="Journey.Treeview")
        self.events.column("#0", width=230, minwidth=100)
        self.events.column("time", width=66, minwidth=60, stretch=False)
        self.events.column("kind", width=100, minwidth=80, stretch=False)
        self._scroll_list(event_shell, self.events)
        self.events.bind("<<TreeviewSelect>>", self._select_event)
        event_detail_shell = self._frame(event_split)
        self.event_detail = self._text(event_detail_shell)
        self._scroll_text(event_detail_shell, self.event_detail)
        event_split.add(event_shell, minsize=280, width=440)
        event_split.add(event_detail_shell, minsize=280)
        self.session_notice = self._label(self.session_page, muted=True)
        self.session_notice.pack(fill="x", pady=(10, 8))
        self.memory_label = self._label(self.session_page, bold=True)
        self.memory_label.pack(fill="x", pady=(0, 4))
        memory_shell = self._frame(self.session_page)
        memory_shell.pack(fill="x")
        self.memory_detail = self._text(memory_shell, height=6)
        self._scroll_text(memory_shell, self.memory_detail)
        self.usage_label = self._label(self.session_page)
        self.usage_label.pack(fill="x", pady=10)
        runtime_split = self._panes(self.session_page)
        runtime_shell = self._frame(runtime_split)
        self.runtime_events = ttk.Treeview(runtime_shell, columns=("kind",), show="tree headings", selectmode="browse", style="Journey.Treeview")
        self.runtime_events.column("#0", width=240, minwidth=140)
        self.runtime_events.column("kind", width=92, minwidth=80, stretch=False)
        self._scroll_list(runtime_shell, self.runtime_events)
        self.runtime_events.bind("<<TreeviewSelect>>", self._select_runtime_event)
        runtime_detail_shell = self._frame(runtime_split)
        self.runtime_detail = self._text(runtime_detail_shell)
        self._scroll_text(runtime_detail_shell, self.runtime_detail)
        runtime_split.add(runtime_shell, minsize=220, width=340)
        runtime_split.add(runtime_detail_shell, minsize=280)
        self.selected_runtime_event = ""
        self._sync_font_metrics()
        self.refresh(force=True)
        self.after_id = self.root.after(1000, self._poll)
        owner._schedule_theme_retouch()
        self.root.bind("<Configure>", self._adapt_layout, add="+")

    def _adapt_layout(self, event):
        if event.widget is self.root:
            compact = self.root.winfo_height() < 740
            height = 106 if compact else 190
            if int(self.canvas.cget("height")) != height:
                self.canvas.configure(height=height)
            goal_lines = 1 if compact else 2
            if int(self.goal_text.cget("height")) != goal_lines:
                self.goal_text.configure(height=goal_lines)

    def _sync_font_metrics(self):
        title = native_font(self.root,font=("Microsoft YaHei UI",10,"bold"))
        state = native_font(self.root,font=("Microsoft YaHei UI",9))
        self._node_height = 24 + 2 * title.metrics("linespace") + state.metrics("linespace") + 12
        self._node_pitch = self._node_height + 16
        self._node_state_y = self._node_height - 12 - state.metrics("linespace") / 2
        ttk.Style(self.root).configure("Journey.Treeview", rowheight=max(30, title.metrics("linespace") + 12))

    @staticmethod
    def _frame(parent):
        widget = tk.Frame(parent)
        set_color_roles(widget, background="WHITE")
        return widget

    @staticmethod
    def _label(parent, *, muted=False, bold=False):
        widget = tk.Label(parent, anchor="w", justify="left", font=("Microsoft YaHei UI", 9 if muted else 10, "bold" if bold else "normal"))
        set_color_roles(widget, background="WHITE", foreground="MUTED" if muted else "INK")
        widget._remchat_locale_exclude = True
        widget.bind("<Configure>", lambda event: widget.configure(wraplength=max(80, event.width)))
        return widget

    @staticmethod
    def _text(parent, *, height=6):
        widget = tk.Text(parent, wrap="word", bd=0, padx=12, pady=8, height=height, font=("Microsoft YaHei UI", 10), spacing1=3, spacing3=6)
        set_color_roles(widget, background="WHITE", foreground="INK", insertbackground="INK", selectbackground="ACCENT_SOFT", selectforeground="INK")
        bind_text_editing(widget, read_only=True)
        return widget

    @staticmethod
    def _panes(parent):
        widget = tk.PanedWindow(parent, orient="horizontal", bd=0, sashwidth=6)
        set_color_roles(widget, background="LINE")
        widget.pack(fill="both", expand=True)
        return widget

    @staticmethod
    def _scroll_list(parent, widget):
        scroll = ttk.Scrollbar(parent, command=widget.yview)
        scroll.pack(side="right", fill="y")
        widget.configure(yscrollcommand=scroll.set)
        widget.pack(fill="both", expand=True)

    @classmethod
    def _scroll_text(cls, parent, widget):
        widget.configure(state="disabled")
        cls._scroll_list(parent, widget)

    @staticmethod
    def _write(widget, text):
        if widget.get("1.0", "end-1c") != text:
            scroll = widget.yview()
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")
            if scroll:
                widget.yview_moveto(scroll[0])

    @staticmethod
    def _stamp(value, *, date=False):
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S" if date else "%H:%M:%S")
        except (TypeError, ValueError, OverflowError, OSError):
            return "—"

    def _poll(self):
        self.after_id = None
        if self.root.winfo_exists():
            if self.root.state() != "withdrawn":
                self.refresh()
            self.after_id = self.root.after(1000, self._poll)

    def refresh(self, *, force=False):
        data = self.trace.snapshot()
        key = (data.get("version"), self.owner.translator.language, theme.get_mode())
        if not force and key == self.key:
            return
        self.key, self.data = key, data
        tr = self.owner.translator
        self.root.title(tr("项目过程视图"))
        self.status.configure(text=tr("项目旅程 · 观察中" if data["enabled"] else "项目旅程 · 观察已关闭"))
        self.record_button.configure(text=tr("记录阶段"), state="normal" if data["enabled"] else "disabled")
        self.more_button.configure(text=tr("更多 ▾"))
        message = tr("本地追踪，不额外调用模型。箭头仅代表声明的依赖；文件观察不等于 AI 修改。")
        if data.get("coverage") == "partial":
            message += " " + tr("扫描未覆盖整个项目，不据此确认删除。")
        elif data.get("coverage") == "initializing":
            message += " " + tr("正在建立基线，已有文件不计为新增。")
        if data.get("error"):
            message += "\n" + tr("观察错误：{error}", error=data["error"])
        self.notice.configure(text=message)
        self.goal_label.configure(text=tr("最新目标"))
        summary = phase_summary(data)
        self._write(self.goal_text, (summary.get("active_goal") or summary.get("goal") or {}).get("title") or tr("尚未记录目标。发送下一条真实请求，或在“记录阶段”中补录；不会从文件名猜测任务。"))
        self.original_button.configure(text=tr("查看最早目标 · 补充指令不会覆盖它"), state="normal" if summary.get("goal") else "disabled")
        counts = summary["counts"]
        current = "、".join(row["title"] for row in summary["current"])
        current_short = current[:60] + ("…" if len(current) > 60 else "")
        if counts["steps"]:
            progress = tr("完成 {done}/{total} · 进行中 {running} · 待处理 {pending} · 失败 {failed} · 已取消 {cancelled}", **dict(counts, total=counts["steps"]))
            progress += "\n" + (tr("当前：{title}", title=current_short) if current else tr("当前没有上报中的阶段；完成状态以工具或手工记录为准。"))
        else:
            progress = tr("尚未提供阶段计划 · 导入计划或记录一个子任务后，即可按阶段查看进展。")
        self.progress.configure(text=progress)
        self.tabs.tab(self.phase_page, text=tr("阶段流程"))
        self.tabs.tab(self.events_page, text=tr("指令与交接"))
        self.tabs.tab(self.session_page, text=tr("记忆 · 工具 · 用量"))
        self.search_label.configure(text=tr("搜索文件"))
        self.noise_toggle.configure(text=tr("显示基线与生成物"))
        self.files.heading("#0", text=tr("重要文件变化"))
        self.files.heading("kind", text=tr("变化"))
        self._refresh_revisions()
        if self.selected_step not in {"", "@files"} and not any(row["id"] == self.selected_step for row in self.view_steps):
            self.selected_step = ""
        if not self.selected_step and self.view_steps:
            self.selected_step = next((row for row in self.view_steps if row["state"] == "running"), self.view_steps[0])["id"]
        self._draw_steps()
        self._show_step()
        self._populate_files()
        self._populate_events()
        self._populate_session()

    def _refresh_revisions(self):
        tr = self.owner.translator
        history = self.data.get("plan_history", [])
        self._revision_ids = [None] + [row["revision"] for row in reversed(history)]
        if self.view_revision not in self._revision_ids:
            self.view_revision = None
        labels = [tr("当前计划 · 第 {revision} 版", revision=self.data.get("plan_revision", 0))]
        labels.extend(tr("历史 · 第 {revision} 版 · {time}", revision=row["revision"], time=self._stamp(row["archived_at"])) for row in reversed(history))
        self.revision_label.configure(text=tr("计划版本"))
        self.revision_choice.configure(values=labels)
        self.revision_choice.current(self._revision_ids.index(self.view_revision))
        archived = next((row for row in history if row["revision"] == self.view_revision), None)
        self.view_steps = archived["steps"] if archived else self.data.get("steps", [])
        self.active_revision = self.view_revision if archived else self.data.get("plan_revision", 0)
        note = tr("只读历史；观察仍跟随当前计划。" if archived else "点击阶段看结果与文件；左右键切换。")
        if self.data.get("history_truncated", 0):
            note += tr(" 旧版本已移出 {count} 个。", count=self.data["history_truncated"])
        self.revision_note.configure(text=note)

    def _select_revision(self, _event=None):
        index = self.revision_choice.current()
        if 0 <= index < len(self._revision_ids):
            self.view_revision = self._revision_ids[index]
            self.selected_step = self.selected_change = ""
            self.canvas.yview_moveto(0)
            self.refresh(force=True)

    def _draw_steps(self):
        self._sync_font_metrics()
        canvas, tr, colors = self.canvas, self.owner.translator, theme.palette()
        canvas.delete("all")
        canvas.configure(bg=colors["WHITE"], highlightbackground=colors["LINE"])
        steps = self.view_steps + [{"id": "@files", "title": tr("全部重要变化"), "state": "observed", "depends_on": []}]
        width = max(400, canvas.winfo_width())
        columns = max(1, min(4, int(width // 260)))
        cell = (width - 24) / columns
        node_height, pitch = self._node_height, self._node_pitch
        positions = {step["id"]: (12 + (index % columns) * cell, 12 + (index // columns) * pitch) for index, step in enumerate(steps)}
        self._step_positions = positions
        for step in steps:
            x, y = positions[step["id"]]
            for dependency in step.get("depends_on", []):
                if dependency not in positions:
                    continue
                dx, dy = positions[dependency]
                if dy == y:
                    canvas.create_line(dx + cell - 12, dy + node_height / 2, x, y + node_height / 2, fill=colors["MUTED"], arrow="last")
                else:
                    canvas.create_line(dx + cell / 2, dy + node_height, dx + cell / 2, dy + node_height + 8, x + cell / 2, y - 6, x + cell / 2, y,
                                       fill=colors["MUTED"], arrow="last")
        title_font = native_font(canvas,font=("Microsoft YaHei UI",10,"bold"))
        measured, filtered = {}, visible_changes(self.data)
        for index, step in enumerate(steps):
            x, y = positions[step["id"]]
            state, identifier = step["state"], step["id"]
            selected, tag = identifier == self.selected_step, "step:" + identifier
            fill = colors["ACCENT_SOFT"] if state == "running" or selected else colors["WHITE"]
            canvas.create_rectangle(x, y, x + cell - 12, y + node_height, fill=fill, outline=colors["ACCENT_DEEP"] if selected else colors["LINE"], width=2 if selected else 1, tags=tag)
            title_width = max(40, cell - 44 - title_font.measure("00  "))
            title_key = (step["title"], title_width, title_font.metrics("linespace"))
            title = self._title_cache.get(title_key) or self._short_title(step["title"], title_font, title_width)
            measured[title_key] = title
            canvas.create_text(x + 12, y + 12, anchor="nw", width=cell - 44, text=(f"{index + 1:02}  " if identifier != "@files" else "") + title,
                               fill=colors["INK"], font=("Microsoft YaHei UI", 10, "bold"), tags=tag)
            foreground = colors["ERROR"] if state == "failed" else colors["SUCCESS"] if state == "done" else colors["MUTED"]
            canvas.create_text(x + 12, y + self._node_state_y, anchor="w", text=tr(STATE_LABELS.get(state, "未归入阶段 / 全部")), fill=foreground, font=("Microsoft YaHei UI", 9), tags=tag)
            changes = [row for row in filtered if row.get("plan_revision", 0) == self.active_revision and (identifier == "@files" or row.get("step_id") == identifier or identifier in row.get("during_steps", []))]
            if identifier != "@files":
                canvas.create_text(x + cell - 24, y + self._node_state_y, anchor="e", text=tr("{count} 项变化", count=len(changes)), fill=colors["MUTED"], font=("Microsoft YaHei UI", 9), tags=tag)
        self._title_cache = measured
        canvas.configure(scrollregion=(0, 0, width, 24 + ((len(steps) + columns - 1) // columns) * pitch))

    @staticmethod
    def _short_title(title, font, width):
        lines = [""]
        for character in title.replace("\n", " "):
            if font.measure(lines[-1] + character) > width:
                if len(lines) == 2:
                    while lines[-1] and font.measure(lines[-1] + "…") > width:
                        lines[-1] = lines[-1][:-1]
                    lines[-1] += "…"
                    break
                lines.append("")
            lines[-1] += character
        return "\n".join(lines)

    def _click_step(self, event):
        self.canvas.focus_set()
        for item in reversed(self.canvas.find_overlapping(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y), self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))):
            for tag in self.canvas.gettags(item):
                if tag.startswith("step:"):
                    self.selected_step, self.selected_change = tag[5:], ""
                    self.refresh(force=True)
                    return

    def _move_step(self, offset):
        ids = [step["id"] for step in self.view_steps] + ["@files"]
        index = ids.index(self.selected_step) if self.selected_step in ids else 0
        self.selected_step = ids[max(0, min(len(ids) - 1, index + offset))]
        self.selected_change = ""
        self.refresh(force=True)
        y = self._step_positions[self.selected_step][1]
        if y < self.canvas.canvasy(0) or y + self._node_height > self.canvas.canvasy(0) + self.canvas.winfo_height():
            region = self.canvas.bbox("all")
            self.canvas.yview_moveto(max(0, y - 12) / max(1, region[3] + 12))
        return "break"

    def _show_step(self):
        step = next((row for row in self.view_steps if row["id"] == self.selected_step), None)
        self.step_info.configure(text=self.owner.translator("阶段结果在右侧；◦ 本地观察仅时间相关，↗ 为工具指定关联。" if step else "未归入阶段的变化也会保留；无计划时不猜测 AI 的后续步骤。"))

    def _populate_files(self):
        if not hasattr(self, "files"):
            return
        scroll = self.files.yview()
        self.files.delete(*self.files.get_children())
        self.visible_changes = {}
        rows = visible_changes(self.data, include_discovered=self.include_noise.get(), include_generated=self.include_noise.get())
        query = self.file_query.get().strip().casefold()
        for change in reversed(rows):
            if change.get("plan_revision", 0) != self.active_revision:
                continue
            if self.selected_step not in {"", "@files"} and change.get("step_id") != self.selected_step and self.selected_step not in change.get("during_steps", []):
                continue
            if query and query not in change["path"].casefold():
                continue
            self.visible_changes[change["id"]] = change
            marker = "◦ " if change.get("evidence") == "observed" else "↗ "
            self.files.insert("", "end", iid=change["id"], text=marker + change["path"], values=(self.owner.translator(OP_LABELS.get(change["operation"], change["operation"])),))
        self.file_count.configure(text=self.owner.translator("{count} 项 · 隐藏噪声 {hidden}", count=len(self.visible_changes), hidden=len(self.data.get("changes", [])) - len(rows)))
        if self.selected_change in self.visible_changes:
            self.files.selection_set(self.selected_change)
            self._render_detail(self.visible_changes[self.selected_change])
        else:
            self.selected_change = ""
            self._set_detail(self._stage_detail())
        if scroll:
            self.files.yview_moveto(scroll[0])

    def _stage_detail(self):
        tr = self.owner.translator
        step = next((row for row in self.view_steps if row["id"] == self.selected_step), None)
        if not step:
            return tr("重要变化优先\n\n默认排除运行环境、构建产物、缓存、日志及首次发现的旧文件。原记录仍保留，可显式显示或导出。\n\n选择阶段看完成结果，选择文件看真实差异。没有工具上报的阶段，不自动归因或编造计划。")
        parts = [step["title"], tr(STATE_LABELS[step["state"]]) + " · " + self._stamp(step.get("updated_at"), date=True),
                 "\n" + tr("完成了什么"), step.get("outcome") or tr("未上报阶段结果；状态不等于具体完成内容。"),
                 "\n" + tr("还剩什么"), step.get("remaining") or tr("未单独上报剩余事项；请结合计划中未完成的阶段。")]
        if step.get("message"):
            parts += ["\n" + tr("阶段记录"), step["message"]]
        parts += ["\n" + tr("证据与依赖"), tr("来源：{origin} · 前置步骤：{steps}", origin=step.get("source", "—"), steps=", ".join(step.get("depends_on", [])) or "—"),
                  tr("左侧显示此阶段关联的文件；本地观察只是同一时段发生。完整记录可复制。")]
        return "\n".join(parts)

    def _select_change(self, _event=None):
        selection = self.files.selection()
        if selection and selection[0] in self.visible_changes:
            self.selected_change = selection[0]
            self._render_detail(self.visible_changes[selection[0]])

    def _render_detail(self, row):
        tr = self.owner.translator
        evidence = tr("本地观察；修改者未知" if row.get("evidence") == "observed" else "工具上报；未独立确认执行者")
        parts = [row["path"], f"{tr(OP_LABELS.get(row['operation'], row['operation']))} · {self._stamp(row.get('timestamp'), date=True)}", evidence,
                 tr("发生时计划版本：{revision}", revision=row.get("plan_revision", 0)), tr("来源：{origin}", origin=row.get("source", "—"))]
        if row.get("during_steps"):
            parts.append(tr("发生于这些步骤期间（仅时间关联）：{steps}", steps=", ".join(row["during_steps"])))
        if row.get("step_id"):
            parts.append(tr("工具关联步骤：{step}", step=row["step_id"]))
        parts.append("\n" + tr("静态引用（不代表因果；仅解析部分本地 import）"))
        parts.extend("→ " + path for path in row.get("references", []))
        if not row.get("references"):
            parts.append(tr("未解析到本地引用"))
        parts.append("\n" + tr("文本差异（仅本次运行内存预览）"))
        parts.append(self.trace.detail(row["id"]) or tr("没有差异快照：可能是二进制、过大、超出缓存范围或工具上报。"))
        self._set_detail("\n".join(parts))

    def _set_detail(self, text):
        self._write(self.detail, text)

    def _populate_events(self):
        if not hasattr(self, "events"):
            return
        tr = self.owner.translator
        self.events.heading("#0", text=tr("指令 / 阶段 / 交接"))
        self.events.heading("time", text=tr("时间"))
        self.events.heading("kind", text=tr("类型"))
        self.event_notice.configure(text=tr("按真实接收顺序保存，不推断内部思考。交接只展示上报的摘要、保留项和省略项。"))
        self.event_search_label.configure(text=tr("历史输入速查"))
        selected_filter = max(0, self.event_filter.current())
        self.event_filter.configure(values=[tr(item) for item in ("全部事件", "用户输入", "阶段与计划", "记忆与交接")])
        self.event_filter.current(selected_filter)
        scroll = self.events.yview()
        self.events.delete(*self.events.get_children())
        self.visible_events = {}
        kinds = [None, {"goal", "goal_update", "request"}, {"step", "plan"}, {"memory", "checkpoint"}][selected_filter]
        query = self.event_query.get().strip().casefold()
        for row in reversed(event_timeline(self.data)):
            title = row.get("text") or row.get("summary") or row.get("title") or tr("计划第 {revision} 版 · {count} 个阶段", revision=row.get("plan_revision", 0), count=row.get("step_count", 0))
            if row["kind"] == "tool":
                title = row.get("name", "") + " · " + self._tool_state(row)
            elif row["kind"] == "usage":
                title = row.get("model", "") + " · " + self._token_text(row)
            if kinds is not None and row["kind"] not in kinds:
                continue
            if query and query not in title.casefold():
                continue
            self.visible_events[row["id"]] = row
            self.events.insert("", "end", iid=row["id"], text=" ".join(title.split()), values=(self._stamp(row.get("timestamp")), tr(EVENT_LABELS.get(row["kind"], row["kind"]))))
        if self.selected_event == "@original-goal" and self.data.get("goal"):
            self._render_event(self.data["goal"])
        elif self.selected_event in self.visible_events:
            self.events.selection_set(self.selected_event)
            self._render_event(self.visible_events[self.selected_event])
        else:
            checkpoint = phase_summary(self.data).get("checkpoint")
            if checkpoint:
                self._render_event(checkpoint)
            else:
                self._write(self.event_detail, tr("尚未上报上下文交接\n\n这里不会声称看见 CLI 的隐藏上下文或内部压缩。工具可以发送 checkpoint，或由你在“记录阶段”中填写实际交接摘要。\n\n新指令独立保留，不覆盖最早的目标。点击左侧事件查看原文、来源及当时的计划版本。"))
        if scroll:
            self.events.yview_moveto(scroll[0])

    def _select_event(self, _event=None):
        selection = self.events.selection()
        if selection and selection[0] in self.visible_events:
            self.selected_event = selection[0]
            self._render_event(self.visible_events[selection[0]])

    def _render_event(self, row, target=None):
        tr, kind = self.owner.translator, row.get("kind", "checkpoint")
        parts = [tr(EVENT_LABELS.get(kind, kind)), self._stamp(row.get("timestamp"), date=True),
                 tr("来源：{origin} · 计划第 {revision} 版", origin=row.get("source", "—"), revision=row.get("plan_revision", 0)), ""]
        if kind in {"checkpoint", "memory"}:
            parts += [tr("交接摘要" if kind == "checkpoint" else "已提供的记忆摘要"), row.get("summary", ""), "\n" + tr("明确保留"), "\n".join("• " + item for item in row.get("retained", [])) or tr("未提供清单，不代表没有保留内容。"),
                      "\n" + tr("明确省略 / 未保留"), "\n".join("• " + item for item in row.get("omitted", [])) or tr("未提供清单，不推测压缩丢失内容。")]
        elif kind == "tool":
            parts += [row.get("name", ""), f"{row.get('category', 'other')} · {self._tool_state(row)}", row.get("summary") or tr("未提供调用摘要"),
                      "\n" + tr("调用 ID：{identifier}", identifier=row.get("call_id") or "—"), tr("仅记录工具实际上报或应用自身的调用；不保存原始参数，不因模型提到工具就计为执行。")]
        elif kind == "usage":
            scope = tr("单次调用" if row.get("scope") == "turn" else "宿主累计快照")
            parts += [row.get("model", ""), scope, self._token_text(row), "\n" + tr("用量 ID：{identifier}", identifier=row.get("usage_id") or "—"),
                      tr("未上报字段显示未知。累计快照与单次调用分开，不混加；token 不是费用金额。")]
        elif kind == "request":
            parts += [row.get("text", ""), "\n" + tr("发展方向 / 原因"), row.get("reason") or tr("未单独说明；不会自动推断指令改变了哪些阶段。")]
        elif kind == "step":
            parts += [row.get("title", ""), tr(STATE_LABELS.get(row.get("state"), "待处理")), "\n" + tr("完成结果"), row.get("outcome") or tr("未上报"),
                      "\n" + tr("剩余事项"), row.get("remaining") or tr("未上报"), "\n" + row.get("message", "")]
        elif kind == "plan":
            parts += [tr("计划第 {revision} 版 · {count} 个阶段", revision=row.get("plan_revision", 0), count=row.get("step_count", 0)), tr("在阶段流程页选择对应历史版本。")]
        else:
            parts += [row.get("title", "")]
        if self.data.get("timeline_truncated", 0):
            parts += ["\n" + tr("最近 500 条事件之外已移出 {count} 条；原始目标独立保留。", count=self.data["timeline_truncated"])]
        self._write(target or self.event_detail, "\n".join(parts))

    def _show_original_goal(self):
        if self.data.get("goal"):
            self.selected_event = "@original-goal"
            self.tabs.select(self.events_page)
            self._render_event(self.data["goal"])

    def _token_text(self, row):
        tr = self.owner.translator
        def value(key):
            return str(row[key]) if isinstance(row.get(key), int) and not isinstance(row[key], bool) else tr("未知")
        return tr("输入 {input} · 输出 {output} · 合计 {total} token", input=value("input_tokens"), output=value("output_tokens"), total=value("total_tokens"))

    def _tool_state(self, row):
        if row.get("source") == "RemChat" and row.get("category") == "skill" and row.get("state") == "completed":
            return self.owner.translator("参考已注入")
        return self.owner.translator(TOOL_STATE_LABELS.get(row.get("state"), "未知"))

    def _populate_session(self):
        tr, summary = self.owner.translator, phase_summary(self.data)
        self.session_notice.configure(text=tr("同一会话任务的已上报状态。外部工具须显式接入；未知不显示为零，不读取私有日志。"))
        self.memory_label.configure(text=tr("当前已提供的记忆 / 上下文"))
        memory = summary.get("latest_memory")
        if memory:
            self._render_event(memory, self.memory_detail)
        else:
            self._write(self.memory_detail, tr("尚未上报记忆状态。RemChat 在真实聊天时记录本轮注入的记忆摘要；外部智能体需主动提供 memory。这里不展示隐藏思考，也不假定已读到它的完整上下文。"))
        usage = summary.get("usage", {})
        rows = usage.get("turns", []) + usage.get("cumulative", [])
        if rows:
            latest = max(rows, key=lambda row: row.get("timestamp", 0))
            label = tr("最近模型：{model} · {scope}\n{tokens}", model=latest["model"], scope=tr("单次调用" if latest.get("scope") == "turn" else "累计快照"), tokens=self._token_text(latest))
        else:
            label = tr("模型 / token：尚未上报。追踪功能不发起额外推理；不从字数估算用量。")
        self.usage_label.configure(text=label)
        self.runtime_events.heading("#0", text=tr("工具与模型调用"))
        self.runtime_events.heading("kind", text=tr("状态 / 范围"))
        scroll = self.runtime_events.yview()
        self.runtime_events.delete(*self.runtime_events.get_children())
        self.runtime_rows = {}
        for row in reversed(event_timeline(self.data)):
            if row["kind"] not in {"tool", "usage"}:
                continue
            self.runtime_rows[row["id"]] = row
            title = f"{self._stamp(row.get('timestamp'))}  " + (row.get("name", "") if row["kind"] == "tool" else row.get("model", ""))
            state = self._tool_state(row) if row["kind"] == "tool" else tr("单次" if row.get("scope") == "turn" else "累计")
            self.runtime_events.insert("", "end", iid=row["id"], text=title, values=(state,))
        if self.selected_runtime_event in self.runtime_rows:
            self.runtime_events.selection_set(self.selected_runtime_event)
            self._render_event(self.runtime_rows[self.selected_runtime_event], self.runtime_detail)
        else:
            self._write(self.runtime_detail, tr("尚未选择调用记录。\n\nMCP/Skill 的安装、启用和实际调用是三件事。没有执行事件就不计为已调用。外部 CLI 可使用同一 project-events 协议上报，不要求额外询问模型。"))
        if scroll:
            self.runtime_events.yview_moveto(scroll[0])

    def _select_runtime_event(self, _event=None):
        selection = self.runtime_events.selection()
        if selection and selection[0] in self.runtime_rows:
            self.selected_runtime_event = selection[0]
            self._render_event(self.runtime_rows[selection[0]], self.runtime_detail)

    def _more(self):
        menu, tr = tk.Menu(self.root, tearoff=False), self.owner.translator
        enabled = "normal" if self.data["enabled"] else "disabled"
        menu.add_command(label=tr("导入计划或旅程"), command=self._import_plan, state=enabled)
        menu.add_command(label=tr("复制接入信息"), command=self._copy_connection, state=enabled)
        menu.add_command(label=tr("关联监工任务到此会话"), command=self._bind_task, state=enabled)
        menu.add_command(label=tr("导出记录"), command=self._export)
        menu.add_separator()
        menu.add_command(label=tr("关闭观察" if self.data["enabled"] else "开启观察"), command=self._toggle)
        colors = theme.palette()
        menu.configure(background=colors["WHITE"], foreground=colors["INK"], activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"])
        try:
            menu.tk_popup(self.more_button.winfo_rootx(), self.more_button.winfo_rooty() + self.more_button.winfo_height())
        finally:
            menu.grab_release()

    def _bind_task(self):
        """An explicit association, not automatic detection of a CLI session."""
        manager, tr = self.owner.integration_manager, self.owner.translator
        tasks = manager.list_tasks()
        if not tasks:
            self.owner._set_status("尚无监工任务；请先在设置的任务监工页创建任务。")
            return
        window = tk.Toplevel(self.root)
        window.title(tr("关联监工任务到此会话"))
        window.geometry("610x350")
        window.minsize(520, 330)
        window.transient(self.root)
        set_color_roles(window, background="WHITE")
        form = self._frame(window)
        form.pack(fill="both", expand=True, padx=18, pady=18)
        notice = self._label(form)
        notice.configure(text=tr("关联后，真实开始/完成/失败事件将进入此项目的阶段流程。不是自动接入 CLI；工具调用与用量仍需主动上报。"))
        notice.pack(fill="x", pady=(0, 12))
        choice = ttk.Combobox(form, state="readonly", values=[f"{row.get('name', row['id'])} · {row.get('state', 'unknown')} · {row['id'][:8]}" for row in tasks])
        choice.pack(fill="x")
        choice.current(0)
        label = self._label(form)
        label.configure(text=tr("宿主会话 ID（可选，不是 RemChat 观察 ID）"))
        label.pack(fill="x", pady=(12, 4))
        host_session = SoftTtkEntry(form)
        host_session.pack(fill="x")
        feedback = self._label(form, muted=True)
        feedback.pack(fill="x", pady=12)
        def bind():
            try:
                manager.bind_task_project(tasks[choice.current()]["id"], host_session.get().strip())
                self.owner._set_status("已关联到当前观察会话；不会补造旧的开始或完成事件。")
                window.destroy()
            except (OSError, ValueError, RuntimeError) as error:
                feedback.configure(text=str(error))
                set_color_roles(feedback, foreground="ERROR")
        self.owner._setting_button(form, "关联此任务", bind).pack(side="right")
        self.owner._setting_button(form, "取消", window.destroy).pack(side="right", padx=8)
        window.bind("<Escape>", lambda _event: window.destroy())
        self.owner._schedule_theme_retouch()

    def _record(self):
        if not self.trace.snapshot()["enabled"]:
            return
        if self.record_dialog is not None and self.record_dialog.winfo_exists():
            self.record_dialog.lift()
            return
        tr = self.owner.translator
        window = self.record_dialog = tk.Toplevel(self.root)
        window.title(tr("记录阶段 · 不调用模型"))
        window.geometry("660x610")
        window.minsize(560, 520)
        window.transient(self.root)
        set_color_roles(window, background="WHITE")
        session = self.trace.snapshot()["session_id"]
        kinds = ["step", "request", "goal_update", "checkpoint", "memory"] + ([] if self.data.get("goal") else ["goal"])
        names = {"step": "阶段结果", "request": "补充指令", "goal_update": "更新最新目标", "checkpoint": "上下文交接", "memory": "已提供的记忆摘要", "goal": "原始目标"}
        form = self._frame(window)
        form.pack(fill="both", expand=True, padx=18, pady=16)
        title = self._label(form, bold=True)
        title.configure(text=tr("记录真实完成结果，不推测 AI 的思考。"))
        title.pack(fill="x", pady=(0, 12))
        kind_choice = ttk.Combobox(form, state="readonly", values=[tr(names[item]) for item in kinds])
        kind_choice.pack(fill="x")
        kind_choice.current(kinds.index("step" if self.view_steps else "goal" if "goal" in kinds else "request"))
        step_shell = self._frame(form)
        step_label = self._label(step_shell)
        step_label.configure(text=tr("已有阶段（或填写新的子任务名称）"))
        step_label.pack(fill="x", pady=(8, 4))
        steps = self.trace.snapshot().get("steps", [])
        stage_choice = ttk.Combobox(step_shell, values=[row["title"] for row in steps])
        stage_choice.pack(fill="x")
        selected = next((index for index, row in enumerate(steps) if row["id"] == self.selected_step), -1)
        if selected >= 0:
            stage_choice.current(selected)
        state_choice = ttk.Combobox(step_shell, state="readonly", values=[tr(STATE_LABELS[key]) for key in STATE_LABELS])
        state_choice.pack(fill="x", pady=(6, 0))
        state_choice.current(2)
        body_label = self._label(form)
        body_label.pack(fill="x", pady=(10, 4))
        def editor(height):
            widget = tk.Text(form, wrap="word", height=height, undo=True, font=("Microsoft YaHei UI", 10), padx=8, pady=6)
            set_color_roles(widget, background="WHITE", foreground="INK", insertbackground="INK")
            bind_text_editing(widget)
            return widget
        body = editor(5)
        body.pack(fill="both", expand=True)
        extra_label, extra = self._label(form), editor(2)
        extra_label.pack(fill="x", pady=(8, 4))
        extra.pack(fill="x")
        omitted_label, omitted = self._label(form), editor(2)
        footer = self._frame(form)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        feedback = self._label(form, muted=True)
        feedback.pack(side="bottom", fill="x", pady=6)
        def update_form(_event=None):
            kind = kinds[kind_choice.current()]
            if kind == "step":
                step_shell.pack(fill="x", before=body_label)
            else:
                step_shell.pack_forget()
            body_label.configure(text=tr({"step": "完成结果 / 阶段记录", "request": "新的发展指令", "checkpoint": "实际交接摘要（不是内部思考）", "memory": "你提供的记忆摘要（不会改写宿主上下文）", "goal_update": "当前最新的任务目标", "goal": "最早的任务目标"}[kind]))
            extra_label.configure(text=tr({"step": "还剩什么（可选）", "request": "原因 / 发展方向（可选）", "checkpoint": "明确保留的内容（每行一项，可选）", "memory": "明确保留的内容（每行一项，可选）", "goal_update": "保存为最新目标，不覆盖最早的目标", "goal": "原始目标保存后不会被后续指令覆盖"}[kind]))
            if kind in {"goal", "goal_update"}:
                extra.pack_forget()
            else:
                extra.pack(fill="x", after=extra_label)
            if kind in {"checkpoint", "memory"}:
                omitted_label.configure(text=tr("明确省略的内容（每行一项，可选）"))
                omitted_label.pack(fill="x", after=extra, pady=(8, 4))
                omitted.pack(fill="x", after=omitted_label)
            else:
                omitted_label.pack_forget()
                omitted.pack_forget()
        def save():
            kind = kinds[kind_choice.current()]
            text, additional = body.get("1.0", "end-1c").strip(), extra.get("1.0", "end-1c").strip()
            payload = {"kind": kind, "session_id": session, "source": "manual_user"}
            try:
                if kind == "step":
                    name = stage_choice.get().strip()
                    if not name:
                        raise ValueError(tr("请先选择阶段或填写子任务名称。"))
                    index = stage_choice.current()
                    identifier = steps[index]["id"] if 0 <= index < len(steps) else "manual-" + uuid.uuid4().hex[:16]
                    payload.update(step_id=identifier, title=name, state=list(STATE_LABELS)[state_choice.current()], outcome=text, remaining=additional)
                elif kind == "request":
                    payload.update(text=text, reason=additional)
                elif kind in {"checkpoint", "memory"}:
                    payload.update(summary=text, retained=[item.strip() for item in additional.splitlines() if item.strip()], omitted=[item.strip() for item in omitted.get("1.0", "end-1c").splitlines() if item.strip()])
                else:
                    payload.update(title=text)
                self.trace.accept(payload)
                self.view_revision = None
                self.refresh(force=True)
                window.destroy()
            except (OSError, ValueError) as error:
                feedback.configure(text=str(error))
                set_color_roles(feedback, foreground="ERROR")
        self.owner._setting_button(footer, "取消", window.destroy).pack(side="right")
        self.owner._setting_button(footer, "保存记录", save).pack(side="right", padx=8)
        kind_choice.bind("<<ComboboxSelected>>", update_form)
        window.bind("<Escape>", lambda _event: window.destroy())
        update_form()
        self.owner._schedule_theme_retouch()
        body.focus_set()

    def _toggle(self):
        self.owner._toggle_project_process()
        self.refresh(force=True)

    def _import_plan(self):
        chosen = filedialog.askopenfilename(parent=self.root, title=self.owner.translator("导入计划或旅程"), filetypes=[("JSON", "*.json")])
        if not chosen:
            return
        try:
            with open(chosen, "rb") as stream:
                body = stream.read(65537)
            if len(body) > 65536:
                raise ValueError("计划文件不能超过 64 KiB")
            value = json.loads(body.decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("计划文件应为 JSON 对象")
            session = self.trace.snapshot()["session_id"]
            events = []
            if "kind" in value:
                events.append(dict(value, session_id=session, source="manual_import"))
            else:
                goal = value.get("goal")
                if goal:
                    if not isinstance(goal, (str, dict)):
                        raise ValueError("goal 应为目标文本或对象")
                    events.append({"kind": "goal", "title": goal if isinstance(goal, str) else goal.get("title")})
                if "steps" in value:
                    events.append({"kind": "plan", "steps": value["steps"]})
                for field, kind in (("requests", "request"), ("checkpoints", "checkpoint")):
                    rows = value.get(field, [])
                    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                        raise ValueError(f"{field} 应为事件对象数组")
                    events.extend(dict(row, kind=kind) for row in rows)
                events = [dict(row, session_id=session, source="manual_import") for row in events]
            if not events:
                raise ValueError("需要 steps、goal 或真实事件记录")
            self.trace.accept_many(events)
            self.view_revision = None
            self.selected_step = self.selected_change = ""
            self.refresh(force=True)
        except (OSError, ValueError, TypeError) as error:
            self.owner._set_status(str(error), error=True)

    def _copy_connection(self):
        from ..integrations.monitor_runtime import export_runtime
        try:
            info = self.owner.integration_manager.start_receiver()
            session = self.trace.snapshot()["session_id"]
            exported = getattr(self, "_sender_export", None)
            if exported is None or exported[0] != session:
                directory = self.trace.directory / "exports" / session / ("sender-" + uuid.uuid4().hex[:12])
                export_runtime(directory)
                self._sender_export = (session, directory)
            directory = self._sender_export[1]
            payload = {"url": info["url"].replace("/events", "/project-events"), "session_id": session, "observation_session_id": session,
                       "token_path": info["token_path"], "workspace": self.data["workspace"], "root": str(self.owner.service.config.root),
                       "script": str(directory / "remcompanion/project_trace/send.py"),
                       "argv": ["python", str(directory / "remcompanion/project_trace/send.py"), "--root", str(self.owner.service.config.root), "--session", session, "--event", "YOUR-EVENT.json", "--quiet"],
                       "note": "标准库 helper，不启动模型；观察 ID 不同于外部 CLI 的宿主会话 ID。"}
            if copy_text(self.root, json.dumps(payload, ensure_ascii=False, indent=2)):
                self.owner._set_status("已导出独立 helper 并复制接入信息；仅含令牌文件路径，不含令牌内容。")
        except (OSError, ValueError, RuntimeError) as error:
            self.owner._set_status(str(error), error=True)

    def _export(self):
        chosen = filedialog.asksaveasfilename(parent=self.root, title=self.owner.translator("导出记录"), defaultextension=".json", initialfile="project-process.json")
        if chosen:
            try:
                self.trace.export(chosen)
                self.owner._set_status("已导出元数据；工具标题、说明与项目路径可能敏感，分享前请检查")
            except OSError as error:
                self.owner._set_status(str(error), error=True)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.refresh(force=True)

    def hide(self):
        self.root.withdraw()

    def close(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.root.destroy()
