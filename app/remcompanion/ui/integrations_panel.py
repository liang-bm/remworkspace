"""接入与监工工作台。

网络、子进程和文件读取放在工作线程；所有 Tk 更新和通知通过队列回到主线程。
未知配置导入后默认停用，工具参数始终可见，只有明确的按钮操作才执行工具。
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from .scroll_surface import ScrollCanvas
from .soft_fields import SoftTtkEntry
from tkinter import ttk
from .localized_dialogs import filedialog, messagebox
from typing import Any

from ..integrations import IntegrationManager
from ..clipboard import copy_text
from .integration_draft import IntegrationDraftMixin
from .mcp_panel import MCPPanelMixin
from .application_panel import ApplicationPanelMixin
from .skill_panel import SkillPanelMixin
from .tool_sessions_panel import ToolSessionsPanelMixin
from .resource_rows import ResourceRows
from .usage_panel import UsagePanel
from .locale_controller import translator_for, refresh_ui_labels
from .task_rings import task_status, elapsed_text
from .theme import APP_BG as BG, WHITE, INK, MUTED, LINE, ACCENT_DEEP as ACCENT, ACCENT_HOVER, ACCENT_SOFT as SOFT, BODY_FONT, ON_ACCENT


FONT = (BODY_FONT, 10)
MONO = ("Cascadia Mono", 10)
SECTION_TITLES = {"tasks": "会话管理", "usage": "全部用量", "mcp": "MCP 服务", "apps": "应用",
                  "skills": "技能库", "hooks": "事件 Hook", "draft": "接入草稿"}
RESOURCE_SECTIONS = ("usage", "apps", "mcp", "skills", "hooks", "draft")
STATES = {"running": "进行中", "completed": "已完成", "failed": "失败", "cancelled": "已取消",
          "waiting": "等待接入", "registered": "等待接入", "unknown": "状态未知", "stopped": "已停止观察",
          "idle": "空闲", "disconnected": "同步中断", "sync_lost": "同步中断"}


class IntegrationPanel(ToolSessionsPanelMixin, MCPPanelMixin, ApplicationPanelMixin, SkillPanelMixin, IntegrationDraftMixin, tk.Frame):
    def __init__(
        self, parent: tk.Misc, root_path: Path | str,
        on_notify: Callable[[dict[str, Any]], None] | None = None, *,
        manager: IntegrationManager | None = None,
        on_draft: Callable[[str, Callable[..., None]], None] | None = None,
        embedded: bool = False,
        sections: tuple[str, ...] | None = None,
        scope: str = "all",
    ) -> None:
        if scope not in {"all", "external", "remchat"}:
            raise ValueError(f"Unknown integration scope: {scope}")
        allowed = (("tasks",) if scope == "external" else RESOURCE_SECTIONS
                   if scope == "remchat" else tuple(SECTION_TITLES))
        chosen = tuple(dict.fromkeys(sections if sections is not None else allowed))
        if not chosen or any(key not in allowed for key in chosen):
            raise ValueError("Integration sections must belong to their scope.")
        super().__init__(parent, bg=BG)
        self.scope, self.sections = scope, chosen
        self.manager = manager or IntegrationManager(root_path)
        self.owns_manager = manager is None
        self.on_notify = on_notify
        self.on_draft = on_draft
        self.embedded = embedded
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.unsubscribe = self.manager.subscribe(lambda event: self.messages.put(("event", event)))
        self.after_id: str | None = None
        self.closed = False
        self.busy = False
        self._task_clock_second = -1
        self._native_catalog_dirty = False
        self._task_rows_dirty = False
        self.mcp_rows: list[dict[str, Any]] = []
        self.hook_rows: list[dict[str, Any]] = []
        self.skill_rows: list[dict[str, Any]] = []
        self.task_rows: list[dict[str, Any]] = []
        self.catalog: dict[str, Any] = {}
        self.catalog_identifier = ""
        self.mcp_enabled = tk.BooleanVar(master=self, value=False)
        self.hook_enabled = tk.BooleanVar(master=self, value=False)
        self.skill_enabled = tk.BooleanVar(master=self, value=False)
        self.status = tk.StringVar(master=self, value="")
        self._build()
        self.refresh()
        self.after_id = self.after(100, self._drain)
        self.bind("<Destroy>", self._destroyed, add=True)

    def _build(self) -> None:
        if not self.embedded:
            heading = tk.Frame(self, bg=BG)
            heading.pack(fill="x", padx=24, pady=(22, 14))
            title = "接入资源" if self.scope == "remchat" else "接入与会话管理"
            tk.Label(heading, text=title, bg=BG, fg=INK, font=(FONT[0], 18, "bold")).pack(anchor="w")
        self.resource_counts = tk.StringVar(self)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8 if self.embedded else 24,
                       pady=(10, 0) if self.embedded else 0)
        pages = {}
        for key in self.sections:
            page = tk.Frame(self.tabs, bg=WHITE)
            self.tabs.add(page, text=SECTION_TITLES[key])
            pages[key] = page
        self.pages = pages
        for key in self.sections:
            if key == "usage":
                self.usage_panel = UsagePanel(pages[key], self.manager)
                self.usage_panel.pack(fill="both", expand=True)
            else:
                getattr(self, "_build_" + key)(pages[key])
        self.tabs.bind("<<NotebookTabChanged>>", self._integration_tab_changed, add=True)
        status_label = tk.Label(self, textvariable=self.status, bg=BG, fg=MUTED, font=(FONT[0], 9), anchor="w", justify="left")
        status_label.pack(fill="x", padx=24, pady=12)
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(160, event.width)))

    def _button(self, parent: tk.Misc, label: str, command: Callable[[], None], *, primary: bool = False) -> tk.Button:
        button = tk.Button(parent, text=label, command=command, font=FONT, bg=ACCENT if primary else WHITE, fg=ON_ACCENT if primary else INK, activebackground=SOFT, activeforeground=INK, relief="flat", bd=0, padx=12, pady=7, cursor="hand2", highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT, takefocus=True)
        if primary:
            button.configure(activebackground=ACCENT_HOVER, activeforeground=ON_ACCENT)
            button._remchat_color_roles = {"background": "ACCENT_DEEP", "foreground": "ON_ACCENT",
                "activebackground": "ACCENT_HOVER", "activeforeground": "ON_ACCENT"}
        button.pack(side="left", padx=(0, 8), pady=2)
        return button

    def _label(self, parent: tk.Misc, text: str) -> None:
        label = tk.Label(parent, text=text, bg=WHITE, fg=INK, font=FONT, anchor="w", justify="left", wraplength=720)
        label.pack(fill="x", pady=(8, 5))
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(160, event.width - 4)))

    def _text(self, parent: tk.Misc, height: int, *, code: bool = False) -> tk.Text:
        wrapper = tk.Frame(parent, bg=WHITE)
        wrapper.pack(fill="both", expand=True, pady=(0, 6))
        text = tk.Text(wrapper, height=height, font=MONO if code else FONT, bg=BG if code else WHITE, fg=INK, insertbackground=INK, selectbackground=SOFT, selectforeground=INK, wrap="word", relief="flat", bd=0, padx=10, pady=9, highlightthickness=1, highlightbackground=LINE)
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        menu = tk.Menu(text, tearoff=False)
        menu.add_command(label="复制", command=lambda: text.event_generate("<<Copy>>"))
        menu.add_command(label="粘贴", command=lambda: text.event_generate("<<Paste>>"))
        menu.add_command(label="全选", command=lambda: (text.tag_add("sel", "1.0", "end-1c"), text.focus_set()))
        text.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))
        text.bind("<Control-a>", lambda _event: (text.tag_add("sel", "1.0", "end-1c"), "break")[-1])
        return text

    @staticmethod
    def _set_text(widget: tk.Text, value: Any, *, readonly: bool = False) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        if readonly:
            widget.configure(state="disabled")

    def _split(self, page: tk.Frame) -> tuple[tk.Frame, tk.Frame]:
        split = tk.PanedWindow(page, orient="horizontal", bg=LINE, bd=0, sashwidth=3)
        split.pack(fill="both", expand=True, padx=16, pady=14)
        left = tk.Frame(split, bg=WHITE)
        detail = tk.Frame(split, bg=WHITE)
        # Tk fonts are DPI-scaled; the navigation rail must scale with them.
        from .dpi import display_scale
        scale = display_scale(self)
        split.add(left, width=round(210 * scale), minsize=round(155 * scale))
        split.add(detail, minsize=round(320 * scale))
        return left, self._scroll_detail(detail)

    def _scroll_detail(self, detail: tk.Frame) -> tk.Frame:
        """Natural-height forms scroll instead of squeezing editors to one line."""
        canvas = ScrollCanvas(detail, bg=WHITE, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(detail, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        right = tk.Frame(canvas, bg=WHITE)
        right._remchat_scroll_canvas = canvas
        item = canvas.create_window((0, 0), window=right, anchor="nw")
        pending: list[str | None] = [None]

        def layout() -> None:
            pending[0] = None
            if not canvas.winfo_exists():
                return
            width = max(1, canvas.winfo_width())
            height = max(right.winfo_reqheight(), canvas.winfo_height())
            # Let Tk derive window height from natural content. Freezing a
            # previous requested height delays reflow after labels wrap.
            canvas.itemconfigure(item, width=width)
            canvas.configure(scrollregion=(0, 0, width, height))

        def schedule(_event: tk.Event) -> None:
            if pending[0] is None:
                pending[0] = canvas.after_idle(layout)

        canvas.bind("<Configure>", schedule, add=True)
        right.bind("<Configure>", schedule, add=True)

        def wheel(event: tk.Event) -> str | None:
            # Text 自己滚动文本；只在本页其余控件上滚动外层，不劫持其他页。
            widget: tk.Misc | None = event.widget
            if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Scrollbar)):
                return None
            while widget is not None:
                if widget is right:
                    canvas.yview_scroll(-1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1, "units")
                    return "break"
                widget = getattr(widget, "master", None)
            return None

        top = self.winfo_toplevel()
        bindings = [(sequence, top.bind(sequence, wheel, add=True)) for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>")]

        def reveal_focus(event):
            widget = event.widget
            ancestor = widget
            while ancestor is not None and ancestor is not right:
                ancestor = getattr(ancestor, "master", None)
            if ancestor is None or not canvas.winfo_exists() or not widget.winfo_viewable():
                return
            layout()
            y = widget.winfo_rooty() - right.winfo_rooty()
            bottom = y + widget.winfo_height()
            visible_top = canvas.canvasy(0)
            visible_bottom = visible_top + canvas.winfo_height()
            content_height = max(right.winfo_reqheight(), canvas.winfo_height())
            if y < visible_top:
                canvas.yview_moveto(max(0, y - 8) / max(1, content_height))
            elif bottom > visible_bottom:
                canvas.yview_moveto(max(0, bottom + 8 - canvas.winfo_height()) / max(1, content_height))

        bindings.append(("<FocusIn>", top.bind("<FocusIn>", reveal_focus, add=True)))

        def cleanup(event: tk.Event) -> None:
            if event.widget is not canvas:
                return
            for sequence, identifier in bindings:
                if identifier:
                    try:
                        top.unbind(sequence, identifier)
                    except tk.TclError:
                        pass
            if pending[0]:
                try:
                    canvas.after_cancel(pending[0])
                except tk.TclError:
                    pass

        canvas.bind("<Destroy>", cleanup, add=True)
        return right

    def _detail_section(self, parent, title):
        """Meaningful groups without nesting decorative cards."""
        from .dpi import pixels
        section = tk.Frame(parent, bg=WHITE)
        section.pack(fill="x", pady=(pixels(self, 18), pixels(self, 6)))
        tk.Frame(section, bg=LINE, height=1).pack(fill="x", pady=(0, pixels(self, 12)))
        heading = tk.Label(section, text=title, bg=WHITE, fg=INK,
                           font=(BODY_FONT, 12, "bold"), anchor="w", justify="left")
        heading.pack(fill="x", pady=(0, pixels(self, 8)))
        heading.bind("<Configure>", lambda event: heading.configure(wraplength=max(100, event.width)))
        return section

    @staticmethod
    def _detail_top(body):
        canvas = getattr(body, "_remchat_scroll_canvas", None)
        if canvas is not None:
            def reset():
                try:
                    if canvas.winfo_exists():
                        canvas.yview_moveto(0)
                except tk.TclError:
                    pass
            canvas.after_idle(reset)

    def _integration_tab_changed(self, _event=None):
        # A task tab is an entry into its three-step setup, not a hidden restore
        # of a previous advanced-scroll position. Only viewport changes here.
        selected=self.tabs.select()
        if getattr(self,'_status_page',None)!=selected and not self.busy:
            self.status.set('')
        self._status_page=selected
        if "tasks" in self.pages and self.tabs.select() == str(self.pages["tasks"]):
            self._detail_top(self.task_form)
            if self._session_preview_active:
                self.preview_sessions()
        elif "usage" in self.pages and self.tabs.select() == str(self.pages["usage"]):
            self.usage_panel.refresh()

    def show_tasks(self):
        """Public desktop entry: show task setup, preserve all existing drafts."""
        self.show_section("tasks")

    def preview_sessions(self, tool_id=None) -> None:
        if "tasks" not in self.pages or self.closed:
            return
        if not self.busy and not self._tool_auto_busy:
            self._native_catalog_dirty = False
        super().preview_sessions(tool_id)

    def show_section(self, key: str = "tasks") -> None:
        """Select by stable keys; old numeric shortcut indices were inconsistent."""
        if key not in self.pages:
            key = self.sections[0]
        self.tabs.select(self.pages[key])
        if key == "tasks":
            self._detail_top(self.task_form)
        elif key == "usage":
            self.usage_panel.refresh()

    def _task_summary(self, row):
        state, elapsed = task_status(row)
        return f"{translator_for(self)(STATES.get(state, '时长未知'))} · {elapsed_text(elapsed)}"

    def _refresh_task_clock(self):
        # Use the already-observed local snapshot; no model call, store poll,
        # receiver start or inference of progress. Preserve selection/scroll.
        if "tasks" not in self.pages or not self.winfo_ismapped():
            return
        second = int(time.monotonic())
        if second == self._task_clock_second:
            return
        self._task_clock_second = second
        selection, yview = self.task_list.curselection(), self.task_list.yview()
        for index, row in enumerate(self.task_rows):
            if index >= self.task_list.size():
                break
            text = f"{self._task_summary(row)} · {row.get('name', row['id'])}"
            if self.task_list.get(index) != text:
                self.task_list.delete(index)
                self.task_list.insert(index, text)
        for index in selection:
            self.task_list.selection_set(index)
        self.task_list.yview_moveto(yview[0])

    def _list(self, parent: tk.Frame) -> tk.Listbox:
        self._label(parent, "搜索名称或描述")
        query = tk.StringVar(self)
        search = SoftTtkEntry(parent, textvariable=query, font=FONT, width=18)
        search.pack(fill="x", padx=4, pady=(0, 5))
        host = tk.Frame(parent, bg=WHITE)
        host.pack(fill="both", expand=True, pady=(5, 10))
        result = tk.Listbox(host, font=FONT, bg=WHITE, fg=INK, selectbackground=SOFT, selectforeground=ACCENT, activestyle="none", relief="flat", bd=0, highlightthickness=1, highlightbackground=LINE, exportselection=False)
        scroll = ttk.Scrollbar(host, command=result.yview)
        scroll.pack(side="right", fill="y")
        result.configure(yscrollcommand=scroll.set)
        result.pack(side="left", fill="both", expand=True)
        result._search_query = query
        query.trace_add("write", lambda *_args: self.refresh())
        return result

    def _resource_list(self, parent, *, edit, toggle):
        """Full-width overview; resource settings live on a separate subpage."""
        query = tk.StringVar(self)
        self._label(parent, "搜索名称或描述")
        search = SoftTtkEntry(parent, textvariable=query, font=FONT)
        search.pack(fill="x", pady=(0, 12))
        rows = ResourceRows(parent, query=query, edit=edit, toggle=toggle)
        rows.pack(fill="both", expand=True)
        query.trace_add("write", lambda *_args: self.refresh())
        return rows

    def _flow_buttons(self, parent, buttons):
        """Independent rows prevent long localized labels clipping controls."""
        layout = [None]
        def reflow(event):
            horizontal = event.width >= sum(button.winfo_reqwidth() + 8 for button in buttons)
            if horizontal == layout[0]:
                return
            layout[0] = horizontal
            for button in buttons:
                button.pack_forget()
                button.pack(side="left" if horizontal else "top", anchor="w",
                            fill="none" if horizontal else "x", padx=(0, 8), pady=3)
        parent.bind("<Configure>", reflow, add=True)


    def _build_hooks(self, page: tk.Frame) -> None:
        left, right = self._split(page)
        self._label(left, "本机事件 Hook")
        self.hook_list = self._list(left)
        self.hook_list.bind("<<ListboxSelect>>", self._select_hook)
        row = tk.Frame(left, bg=WHITE)
        row.pack(fill="x")
        self._button(row, "新增", self._new_hook)
        self._button(row, "移除", self._remove_hook)
        self._label(right, "只启动已启用的明确本机程序；REMCHAT_EVENT_JSON 携带事件。配置不加密，密钥用 env_refs 引用；检查后再启用。")
        self.hook_editor = self._text(right, 14, code=True)
        row = tk.Frame(right, bg=WHITE)
        row.pack(fill="x", pady=5)
        tk.Checkbutton(row, text="启用此 Hook", variable=self.hook_enabled, bg=WHITE, fg=INK, font=FONT, activebackground=WHITE).pack(side="left", padx=(0, 10))
        self._button(row, "保存配置", self._save_hook, primary=True)
        self._button(row, "发送 manual_test 事件", self._test_hook)
        self.hook_output = self._text(right, 7, code=True)
        self._set_text(self.hook_output, "Hook 结果将在这里显示。manual_test 只运行订阅此事件且已启用的 Hook。", readonly=True)
        self._new_hook()

    def _new_hook(self) -> None:
        self.hook_list.selection_clear(0, "end")
        self.hook_enabled.set(False)
        self._set_text(self.hook_editor, {"id": "my-hook", "command": "python", "args": ["我的脚本绝对路径.py"], "events": ["task_completed", "task_failed", "manual_test"], "timeout": 30, "enabled": False})

    def _select_hook(self, _event: tk.Event) -> None:
        selected = self._selection(self.hook_list, self.hook_rows)
        if selected:
            self._set_text(self.hook_editor, selected)
            self.hook_enabled.set(bool(selected.get("enabled")))

    def _save_hook(self) -> None:
        def action() -> None:
            value = json.loads(self.hook_editor.get("1.0", "end-1c"))
            value["enabled"] = self.hook_enabled.get()
            self.manager.upsert_hook(value)
            self.refresh()
            self.status.set("Hook 已保存；请检查程序与参数，只有已启用 Hook 才响应事件。")
        self._guard(action)

    def _test_hook(self) -> None:
        self._guard(lambda: self.manager.emit_event("manual_test", {"message": "用户从接入界面发送测试事件。"}))

    def _remove_hook(self) -> None:
        selected = self._selection(self.hook_list, self.hook_rows)
        if selected:
            self._guard(lambda: (self.manager.remove_hook(selected["id"]), self.refresh(), self._new_hook()))

    def _build_tasks(self, page: tk.Frame) -> None:
        host = tk.Frame(page, bg=WHITE)
        host.pack(fill="both", expand=True, padx=20, pady=18)
        right = self._scroll_detail(host)
        self.task_form = right
        self._build_tool_sessions(right)
        # History follows setup on the same scroll surface. A narrow side rail
        # previously crushed both long task names and the three-step guidance.
        left = tk.Frame(right, bg=WHITE)
        self._label(left, "我的监工任务")
        self.task_list = self._list(left)
        self.task_list.configure(height=5)
        self.task_list.bind("<<ListboxSelect>>", self._select_task)
        row = tk.Frame(left, bg=WHITE)
        row.pack(fill="x")
        self._button(row, "停止观察", self._cancel_task)
        self._button(row, "移除记录", self._remove_task)
        self.task_advanced_button = self._button(right, "高级手动接入  ▸", self._toggle_task_advanced)
        self.task_advanced_button.pack_configure(side="top", anchor="w", pady=(12, 8))
        self.task_advanced = tk.Frame(right, bg=WHITE)
        # Preserve every legacy adapter/marker action under explicit disclosure.
        right = self.task_advanced
        self._label(right, "手动事件 / Hook / 标记文件：仅用于尚无原生连接的工具，或已有脚本。")
        self._label(right, "1  选择正在工作的工具")
        self.adapter_rows = self.manager.list_adapters()
        self.adapter_choice = ttk.Combobox(right, state="readonly",
            values=[item["name"] for item in self.adapter_rows], font=FONT)
        if self.adapter_rows:
            self.adapter_choice.current(0)
        self.adapter_choice.pack(fill="x", pady=(0, 8))
        self.adapter_choice.bind("<<ComboboxSelected>>", self._show_adapter_help)
        self._label(right, "2  给这次任务起个名字")
        self.task_name = SoftTtkEntry(right, font=FONT)
        self.task_name.pack(fill="x", pady=(0, 5))
        self._label(right, "3  创建接入草稿，然后按草稿中的说明连接")
        row = tk.Frame(right, bg=WHITE)
        row.pack(fill="x", pady=8)
        create = self._button(row, "创建任务并导出接入草稿", self._prepare_task_connection, primary=True)
        listen = self._button(row, "启动提醒监听", self._start_receiver)
        self._flow_buttons(row, (create, listen))
        self._last_export_directory = None
        next_row = tk.Frame(right, bg=WHITE)
        next_row.pack(fill="x", pady=(0, 5))
        self.export_folder_button = self._button(next_row, "打开接入草稿目录", self._open_export_directory)
        self.export_folder_button.configure(state="disabled")
        self.task_help = tk.StringVar(self, "雷姆只观察任务消息，不会控制外部工具。草稿不会改它们的全局设置。")
        help_label = tk.Label(right, textvariable=self.task_help, bg=WHITE, fg=MUTED,
                             font=FONT, justify="left", anchor="w")
        help_label.pack(fill="x", pady=(4, 12))
        help_label.bind("<Configure>", lambda event: help_label.configure(wraplength=max(100, event.width - 8)))
        self._label(right, "接好之后，雷姆会等真实的完成或失败消息再提醒你。")
        self.receiver_state = tk.StringVar(self, "提醒监听未启动。完成接入后，点击“启动提醒监听”。")
        receiver = tk.Label(right, textvariable=self.receiver_state, bg=WHITE, fg=MUTED,
                            font=FONT, anchor="w", justify="left")
        receiver.pack(fill="x", pady=(4, 8))
        receiver.bind("<Configure>", lambda event: receiver.configure(wraplength=max(100, event.width - 8)))
        advanced = self.task_advanced
        self._label(advanced, "当前会话 ID（OpenCode / Hermes 必填，其余可留空）")
        self.adapter_session = SoftTtkEntry(advanced, font=FONT)
        self.adapter_session.pack(fill="x", pady=(0, 8))
        self._label(advanced, "已有脚本：选择完成标记文件，或读取事件 JSON")
        path_row = tk.Frame(advanced, bg=WHITE)
        path_row.pack(fill="x")
        self.task_path = SoftTtkEntry(path_row, font=FONT)
        self.task_path.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(path_row, "选择文件", self._choose_task_file)
        row = tk.Frame(advanced, bg=WHITE)
        row.pack(fill="x", pady=7)
        self.file_mode = ttk.Combobox(row, values=["标记文件出现或更新", "读取 JSON 的 state"], state="readonly", width=24)
        self.file_mode.current(0)
        self.file_mode.pack(side="left", padx=(0, 8))
        self._button(row, "创建文件观察任务", self._register_task)
        self._label(advanced, "本机事件接收器与接入代码")
        row = tk.Frame(advanced, bg=WHITE)
        row.pack(fill="x", pady=3)
        self._button(row, "启动本机接收器", self._start_receiver)
        self._button(row, "停止接收器", self._stop_receiver)
        row = tk.Frame(advanced, bg=WHITE)
        row.pack(fill="x", pady=3)
        self._button(row, "复制选中任务接入代码", self._copy_task_snippet)
        self._button(row, "复制 Claude Code Hook", self._copy_claude_hook)
        row = tk.Frame(advanced, bg=WHITE)
        row.pack(fill="x", pady=3)
        self._button(row, "为选中任务重新导出草稿", self._export_adapter)
        self._label(advanced, "Claude 的 Stop 只表示回合结束，不等于任务成功。批处理 wrapper 只用于非交互任务。")
        self.task_output = self._text(advanced, 10, code=True)
        self._set_text(self.task_output, "注册一个任务，然后让工作脚本 POST 完成事件，或写入完成标记文件。已有标记文件不会被误判为本次任务完成。", readonly=True)
        self.task_history = left
        left.pack(fill="x", pady=(24, 12))

    def _toggle_task_advanced(self) -> None:
        if self.task_advanced.winfo_manager():
            self.task_advanced.pack_forget()
            self.task_advanced_button.configure(text="高级手动接入  ▸")
        else:
            self.task_advanced.pack(fill="both", expand=True, pady=(0, 10), before=self.task_history)
            self.task_advanced_button.configure(text="高级手动接入  ▾")

    def _choose_registered_task(self, identifier: str) -> None:
        self.task_list._search_query.set("")
        self.refresh()
        for index, row in enumerate(self.task_rows):
            if row["id"] == identifier:
                self.task_list.selection_clear(0, "end")
                self.task_list.selection_set(index)
                self.task_list.see(index)
                self._select_task()
                break

    def _prepare_task_connection(self) -> None:
        if self.busy:
            self.status.set("接入草稿正在处理，请稍候。")
            return
        index = self.adapter_choice.current()
        if index < 0:
            self.status.set("先选择要观察的工具。")
            return
        adapter = self.adapter_rows[index]
        if not adapter.get("available"):
            self.task_help.set("适配文件缺失，暂时不能创建草稿；其他聊天功能不受影响。")
            return
        if adapter["id"] in {"opencode", "hermes"} and not self.adapter_session.get().strip():
            if not self.task_advanced.winfo_manager():
                self._toggle_task_advanced()
            self.adapter_session.focus_set()
            self.task_help.set("这个工具需要当前会话 ID，避免把别人的任务误当成你的。填写后再创建草稿。")
            return
        def action():
            task = self.manager.register_task(self.task_name.get())
            self._choose_registered_task(task["id"])
            self._export_adapter()
        self._guard(action)

    def _show_adapter_help(self, _event=None):
        index = self.adapter_choice.current()
        if index >= 0:
            row = self.adapter_rows[index]
            warning = "此工具暂时使用手动标记接入，没有承诺自动检测任务完成。" if row.get("level", "").startswith("experimental") else "先导出草稿，再按目录里的 README 完成接入。"
            self.task_help.set(warning + "\n" + row.get("limitations", ""))

    def _open_export_directory(self):
        if self._last_export_directory is not None:
            self._open_path(self._last_export_directory)

    def _choose_task_file(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="选择完成标记路径", defaultextension=".done", confirmoverwrite=False)
        if path:
            self.task_path.delete(0, "end")
            self.task_path.insert(0, path)

    def _register_task(self) -> None:
        def action() -> None:
            row = self.manager.register_task(self.task_name.get(), watch_path=self.task_path.get(), file_mode="json" if self.file_mode.current() == 1 else "marker")
            self._choose_registered_task(row["id"])
            self._set_text(self.task_output, row, readonly=True)
            self.status.set(f"已开始监工：{row['name']}。等待真实事件。")
        self._guard(action)

    def _select_task(self, _event: tk.Event | None = None) -> None:
        row = self._selection(self.task_list, self.task_rows)
        if row:
            self._set_text(self.task_output, row, readonly=True)
            self.task_help.set(f"{row['name']} · {self._task_summary(row)}\n{row.get('message', '')}")

    def _cancel_task(self) -> None:
        row = self._selection(self.task_list, self.task_rows)
        if row:
            self._guard(lambda: (self.manager.cancel_monitoring(row["id"]), self.refresh(), self.status.set("已停止观察此任务，不会取消宿主任务。重新观察请注册新任务并更新接入草稿。")))

    def _remove_task(self) -> None:
        row = self._selection(self.task_list, self.task_rows)
        if row:
            self._guard(lambda: (self.manager.remove_task(row["id"]), self.refresh()))

    def _start_receiver(self) -> None:
        self._guard(lambda: (self._set_text(self.task_output, self.manager.start_receiver(), readonly=True), self.status.set("本机接收器已启动，仅监听 127.0.0.1。接入 token 仅提供给本机工作脚本。"), self.refresh()))

    def _stop_receiver(self) -> None:
        self._work("停止本机接收器…", self.manager.stop_receiver, lambda _value: (self.status.set("本机接收器已停止。文件监工继续按任务配置运行。"), self.refresh()))

    def _copy_task_snippet(self) -> None:
        def action() -> None:
            row = self._selection(self.task_list, self.task_rows)
            if not row:
                raise ValueError("先从左侧选择一个任务。")
            info = self.manager.monitor.receiver_info()
            if not info["running"]:
                raise ValueError("先启动本机接收器。")
            snippet = "import json, urllib.request\nfrom pathlib import Path\n" + f"directory = Path({str(self.manager.store.directory)!r})\n" + "receiver = json.loads((directory / 'receiver.json').read_text(encoding='utf-8'))\nassert receiver['running'] and receiver['url'].startswith('http://127.0.0.1:'), '先启动本机接收器'\ntoken = (directory / 'receiver-token.txt').read_text(encoding='utf-8').strip()\n" + f"payload = {{'task_id': {row['id']!r}, 'state': 'completed', 'message': '任务已完成'}}\n" + "request = urllib.request.Request(receiver['url'], data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')\nwith urllib.request.urlopen(request, timeout=5) as response:\n    print(response.read().decode('utf-8'))\n"
            if not copy_text(self, snippet):
                return
            self._set_text(self.task_output, snippet, readonly=True)
            self.status.set("已复制可运行的 Python 完成事件代码；失败时把 state 改为 failed。")
        self._guard(action)

    def _copy_claude_hook(self) -> None:
        def action() -> None:
            row = self._selection(self.task_list, self.task_rows)
            if not row:
                raise ValueError("先从左侧选择要监听 Claude 回合的任务。")
            configuration = self.manager.claude_hook_config(row["id"])
            text = json.dumps(configuration, ensure_ascii=False, indent=2)
            if not copy_text(self, text):
                return
            self._set_text(self.task_output, text, readonly=True)
            self.status.set("已复制。合并到目标项目 .claude/settings.local.json 的 hooks；Stop 仅记录结束检查，不提前报完成。真实结束用批处理/marker；请启动本机接收器。")
        self._guard(action)

    def _export_adapter(self) -> None:
        def action() -> None:
            row = self._selection(self.task_list, self.task_rows)
            index = self.adapter_choice.current()
            if not row or index < 0:
                raise ValueError("先选择任务和外部工具。")
            identifier = self.adapter_rows[index]["id"]
            session = self.adapter_session.get().strip()
            self._work("正在导出独立适配草稿…", lambda: self.manager.export_adapter(identifier, row["id"], session_id=session), self._adapter_exported)
        self._guard(action)

    def _adapter_exported(self, result: dict) -> None:
        self._set_text(self.task_output, result, readonly=True)
        self.task_help.set(result["note"] + "\n草稿目录：" + str(result.get("directory", result.get("path", ""))))
        self.status.set(result["note"])
        self._last_export_directory = Path(result["directory"])
        self.export_folder_button.configure(state="normal")

    @staticmethod
    def _selection(widget: tk.Listbox, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        selected = widget.curselection()
        return rows[selected[0]] if selected and selected[0] < len(rows) else None

    def refresh(self) -> None:
        if self.closed:
            return
        try:
            if 'apps' in self.pages:self._refresh_apps()
            sources = {}
            for key, read in (("mcp", self.manager.list_mcp), ("hooks", self.manager.list_hooks),
                              ("skills", self.manager.skills.list), ("tasks", self.manager.list_tasks)):
                if key in self.pages:
                    sources[key] = read()
            self.resource_counts.set(" · ".join(f"{SECTION_TITLES[key]} {len(rows)}" for key, rows in sources.items()))
            for key, rows in sources.items():
                # Keep reviewed Chinese message IDs for LocaleController's
                # formatted templates; translating the prefix early loses the
                # original source on the next live language switch.
                self.tabs.tab(self.pages[key], text=f"{SECTION_TITLES[key]}  {len(rows)}")
            # Counts can change without a geometry event. Translate just this
            # notebook, preserving its reviewed Chinese sources and selection.
            refresh_ui_labels(self.tabs)
            if hasattr(self, "receiver_state"):
                running = self.manager.monitor.receiver_info().get("running", False)
                self.receiver_state.set("提醒监听已启动 · 仅接收本机事件，等待真实完成消息。" if running else
                                        "提醒监听未启动。完成接入后，点击“启动提醒监听”。")
            for key, rows in sources.items():
                prefix = {"mcp": "mcp", "hooks": "hook", "skills": "skill", "tasks": "task"}[key]
                widget, attribute = getattr(self, prefix + "_list"), prefix + "_rows"
                selected = self._selection(widget, getattr(self, attribute))
                identifier = selected.get("id") if selected else None
                query = widget._search_query.get().strip().casefold()
                if query:
                    rows = [row for row in rows if query in " ".join(str(row.get(key, '')) for key in ('id', 'name', 'description', 'tags', 'state')).casefold()]
                setattr(self, attribute, rows)
                if isinstance(widget, ResourceRows):
                    state = ((lambda row: "已连接" if self.manager.mcp_connected(row["id"]) else "允许连接 · 尚未连接" if row.get("enabled") else "已停用 · 不会连接")
                             if attribute == "mcp_rows" else
                             (lambda row: "已允许雷姆参考 · 不执行脚本" if row.get("enabled") else "已停用 · 先阅读再允许参考"))
                    widget.set_rows(rows, state=state)
                    continue
                widget.delete(0, "end")
                for index, row in enumerate(rows):
                    prefix = self._task_summary(row) if attribute == "task_rows" else "开" if row.get("enabled") else "关"
                    if attribute == "mcp_rows":
                        prefix = "已连接" if self.manager.mcp_connected(row["id"]) else "未连接" if row.get("enabled") else "停用"
                    widget.insert("end", f"{prefix} · {row.get('name', row['id'])}")
                    if row["id"] == identifier:
                        widget.selection_set(index)
            self._refresh_controls()
            if "tasks" in self.pages:
                self._task_rows_dirty = False
                self._refresh_tool_sessions()
        except (ValueError, OSError) as error:
            self.status.set(f"接入配置读取失败：{error}")

    def _refresh_controls(self) -> None:
        """A scoped panel does not construct or touch the other scope's forms."""
        for key, update in (("mcp", self._mcp_controls), ("apps",self._apps_controls), ("skills", self._skill_controls),
                            ("tasks", self._tool_controls)):
            if key in self.pages:
                update()

    def _refresh_native_catalog(self) -> None:
        # Catalog changes are manager-owned, including an empty archive gaining
        # its first conversation. An off-page view only remembers invalidation.
        if (not self._native_catalog_dirty or "tasks" not in self.pages
                or not self._session_preview_active or not self.winfo_ismapped()
                or self.tabs.select() != str(self.pages["tasks"])
                or self.busy or self._tool_auto_busy):
            return
        self._native_catalog_dirty = False
        self._refresh_tool_sessions()
        self._schedule_auto_tool_preview(preserve=True)

    def _guard(self, action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as error:
            self.status.set(str(error))
            messagebox.showerror("接入操作未完成", str(error), parent=self)

    def _work(self, message: str, task: Callable[[], Any], complete: Callable[[Any], None], *, on_error: Callable[[str], None] | None = None) -> None:
        if self.busy:
            self.status.set("上一个接入操作还在运行，请稍候。")
            return
        self.busy = True
        self.status.set(message)
        self._refresh_controls()
        def run() -> None:
            try:
                self.messages.put(("result", (complete, task())))
            except Exception as error:
                self.messages.put(("error", (on_error, str(error))))
        threading.Thread(target=run, name="remchat-integration-ui", daemon=True).start()

    def _drain(self) -> None:
        if self.closed:
            return
        for _ in range(40):
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "result":
                self.busy = False
                self._refresh_controls()
                callback, result = payload
                self._guard(lambda: callback(result))
                self.refresh()
            elif kind == "error":
                self.busy = False
                self._refresh_controls()
                callback, error = payload
                self.status.set(f"操作失败：{error}")
                if callback is not None:
                    self._guard(lambda: callback(error))
                self._refresh_controls()
            elif kind == "draft" and "draft" in self.pages:
                self._accept_draft(payload)
            elif kind == "tool_auto" and "tasks" in self.pages:
                self._accept_auto_tool_preview(payload)
            elif kind == "event":
                event_type = payload.get("type", "")
                if event_type == "usage_changed":
                    if "usage" in self.pages and self.tabs.select() == str(self.pages["usage"]):
                        self.usage_panel.refresh()
                    continue  # Usage metadata is not a task-finished alert.
                if event_type.startswith("hook_") and "hooks" in self.pages:
                    self._set_text(self.hook_output, payload, readonly=True)
                if event_type in {"tool_catalog_changed", "tool_snapshot_changed", "native_sync_state",
                                  "tool_connection", "tool_connection_removed", "task", "task_removed"}:
                    if "tasks" in self.pages:
                        self._native_catalog_dirty = True
                        if event_type in {"task", "task_removed", "tool_connection", "tool_connection_removed"}:
                            self._task_rows_dirty = True
                if self.on_notify is not None:
                    self._guard(lambda: self.on_notify(payload))
        if self._task_rows_dirty and self.winfo_ismapped():
            self.refresh()
        self._refresh_native_catalog()
        self._refresh_task_clock()
        self.after_id = self.after(100, self._drain)

    def _open_path(self, path: Path) -> None:
        def action() -> None:
            path.mkdir(parents=True, exist_ok=True)
            from ..platforms import native_module
            if not native_module("shell").open_directory(path, launch=False):
                if copy_text(self, str(path)):
                    self.status.set(f"路径已复制：{path}")
        self._guard(action)

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is self:
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if "tasks" in self.pages:
            self._stop_auto_tool_preview()
        self._draft_active = None
        self._pending_draft = None
        self.unsubscribe()
        if "usage" in self.pages:
            self.usage_panel.close()
        if self.after_id:
            try:
                self.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.owns_manager:
            self.manager.close()
