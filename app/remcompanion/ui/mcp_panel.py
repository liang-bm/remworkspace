"""MCP settings flow; configuration, connection and execution stay separate."""

from __future__ import annotations

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ..integrations.storage import parse_mcp_import
from .mcp_form import MCPForm
from .resource_rows import PermissionSwitch
from .theme import WHITE, INK, MUTED, BODY_FONT

FONT = (BODY_FONT, 10)


class MCPPanelMixin:
    """Host: IntegrationPanel layout helpers, manager and main-thread queue."""
    def _build_mcp(self, page: tk.Frame) -> None:
        self.mcp_overview = tk.Frame(page, bg=WHITE)
        self.mcp_overview.pack(fill="both", expand=True, padx=20, pady=18)
        left = self.mcp_overview
        heading = tk.Frame(left, bg=WHITE)
        heading.pack(fill="x", pady=(0, 12))
        tk.Label(heading, text="服务器", bg=WHITE, fg=INK, font=(BODY_FONT, 14, "bold")).pack(side="left")
        self._mcp_loaded_id = ""
        actions = tk.Frame(heading, bg=WHITE)
        actions.pack(side="right")
        self.mcp_new_button = self._button(actions, "新增服务", self._new_mcp, primary=True)
        self.mcp_import_button = self._button(actions, "导入 JSON", self._import_mcp)
        from .mcp_presets import show_presets
        self.mcp_preset_button = self._button(actions, "常用预设 · 8", lambda:show_presets(self))
        self._flow_buttons(actions,(self.mcp_new_button,self.mcp_import_button,self.mcp_preset_button))
        self._label(left, "开关仅允许连接，不会自动启动服务。点击编辑检查入口，保存后再明确连接。")
        self.mcp_list = self._resource_list(left, edit=self._select_mcp, toggle=self._toggle_mcp_permission)
        self.mcp_list.bind("<<ListboxSelect>>", self._select_mcp)
        self.mcp_editor_page = tk.Frame(page, bg=WHITE)
        self.mcp_editor_inner = tk.Frame(self.mcp_editor_page, bg=WHITE)
        self.mcp_editor_inner.pack(fill="both", expand=True, padx=20, pady=18)
        header = tk.Frame(self.mcp_editor_inner, bg=WHITE)
        header.pack(fill="x", pady=(0, 8))
        self.mcp_back_button = self._button(header, "返回服务列表", self._back_mcp)
        self.mcp_title = tk.StringVar(self, "新增服务")
        tk.Label(header, textvariable=self.mcp_title, bg=WHITE, fg=INK,
                 font=(BODY_FONT, 14, "bold")).pack(side="left", padx=12)
        right = self._scroll_detail(self.mcp_editor_inner)
        self.mcp_detail_body = right
        permission = self._detail_section(right, "允许与连接")
        self.mcp_check = PermissionSwitch(permission, text="允许连接此服务", variable=self.mcp_enabled)
        self.mcp_check.pack(fill="x", pady=(0, 6))
        self._label(permission, "允许只保存权限，不会自动连接。先检查下方入口，再点击“保存并连接”；本机程序会在连接时启动。")
        row = tk.Frame(permission, bg=WHITE)
        row.pack(fill="x", pady=4)
        self.mcp_save_button = self._button(row, "保存配置", self._save_mcp)
        self.mcp_connect_button = self._button(row, "保存并连接", self._connect_mcp, primary=True)
        self._flow_buttons(row, (self.mcp_save_button, self.mcp_connect_button))
        self.mcp_disconnect_button = self._button(permission, "断开连接", self._disconnect_mcp)
        self.mcp_disconnect_button.pack_configure(side="top", anchor="w", pady=(4, 8))
        self.mcp_state = tk.StringVar(self, "尚未连接")
        label = tk.Label(permission, textvariable=self.mcp_state, bg=WHITE, fg=MUTED,
                         font=FONT, anchor="w", justify="left")
        label.pack(fill="x")
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(100, e.width - 4)))
        configuration = self._detail_section(right, "服务信息")
        self._label(configuration, "这里只编辑入口和参数。保存不会执行程序；配置 ID 用来区分服务。")
        self.mcp_form = MCPForm(configuration, text_factory=self._text,
                                button_factory=self._button, enabled=self.mcp_enabled)
        self.mcp_form.pack(fill="x")
        # User-defined identity stays visible above permissions, while the
        # editable field keeps one source of truth and its existing draft.
        self.mcp_identity_label = tk.Label(right, textvariable=self.mcp_form.fields["name"],
            bg=WHITE, fg=INK, font=(BODY_FONT, 15, "bold"), anchor="w", justify="left")
        self.mcp_identity_label._remchat_locale_exclude = True
        self.mcp_identity_label.pack(fill="x", before=permission, pady=(8, 4))
        self.mcp_identity_label.bind("<Configure>", lambda event: self.mcp_identity_label.configure(
            wraplength=max(100, event.width - 4)))
        self.mcp_editor = self.mcp_form.raw  # Kept for advanced configuration callers.
        self.mcp_capability_host = tk.Frame(right, bg=WHITE)
        self.mcp_capability_host.pack(fill="both", expand=True, pady=(12, 0))
        self._label(self.mcp_capability_host, "服务能力")
        self.operation = ttk.Combobox(self.mcp_capability_host, state="readonly", font=FONT)
        self.operation.pack(fill="x", pady=(0, 4))
        self.operation.bind("<<ComboboxSelected>>", self._inspect_operation)
        self._label(self.mcp_capability_host, "操作参数（JSON）· 执行工具可能改变外部数据")
        self.arguments = self._text(self.mcp_capability_host, 3, code=True)
        self._set_text(self.arguments, {})
        self.mcp_execute_button = self._button(self.mcp_capability_host, "执行选中操作", self._execute_operation)
        self.mcp_execute_button.pack_configure(side="top", anchor="w")
        self.mcp_technical_host = self._detail_section(right, "连接结果与技术信息")
        self.mcp_output = self._text(self.mcp_technical_host, 7, code=True)
        self.mcp_remove_button = self._button(self.mcp_technical_host, "移除服务", self._remove_mcp)
        self.mcp_remove_button.pack_configure(side="top", anchor="w", pady=(12, 8))
        self._set_text(self.mcp_output, "连接后显示真实工具、资源和提示词；保存配置不代表已连接。", readonly=True)
        self._new_mcp()
        self._back_mcp()

    def _show_mcp_editor(self, *, new=False):
        self.mcp_overview.pack_forget()
        self.mcp_editor_page.pack(fill="both", expand=True)
        self.mcp_title.set("新增服务" if new else "编辑服务")
        self._detail_top(self.mcp_detail_body)

    def _back_mcp(self):
        if self.busy:
            return
        # Navigation preserves the in-memory draft. Changing another selection
        # still invokes _discard_mcp; simply going back never loses input.
        self.mcp_editor_page.pack_forget()
        self.mcp_overview.pack(fill="both", expand=True, padx=20, pady=18)
        self.status.set("已返回列表。未保存草稿仍保留，重新编辑当前服务可继续。")

    def _toggle_mcp_permission(self, selected, desired):
        if self.busy:
            return
        if self.mcp_form.dirty():
            self.status.set("当前有未保存草稿。请先保存，或返回编辑后处理草稿，再更改开关。")
            return
        value = {**selected, "enabled": desired}
        def save_permission():
            with self.manager.store.lock:
                if self.manager.store.get_item("mcp", selected["id"]) != selected:
                    raise ValueError("服务配置已经变化，未覆盖。请刷新后重新检查。")
                self.manager.upsert_mcp(value)
        def complete(_result):
            if self._mcp_loaded_id == selected["id"]:
                self.mcp_form.load(value, existing=True)
                self._clear_catalog()
                self.mcp_state.set("允许连接 · 尚未连接" if desired else "已停用 · 不会连接")
            self.status.set("允许状态已保存。没有连接服务或启动程序；请在编辑页明确连接。" if desired else
                            "服务已停用并断开；配置保留，没有启动程序。")
        self._work("正在保存允许状态…", save_permission, complete,
                   on_error=lambda error: self._mcp_error(selected["id"], error))

    def _mcp_controls(self):
        busy = self.busy
        self.mcp_list.set_busy(busy)
        self.mcp_form.set_busy(busy)
        for button in (self.mcp_new_button, self.mcp_import_button, self.mcp_preset_button,
                       self.mcp_save_button, self.mcp_connect_button):
            button.configure(state="disabled" if busy else "normal")
        selected = self._selection(self.mcp_list, self.mcp_rows)
        self.mcp_remove_button.configure(state="normal" if selected and not busy else "disabled")
        connected = self.manager.mcp_connected(self._mcp_loaded_id)
        self.mcp_disconnect_button.configure(state="normal" if connected and not busy else "disabled")
        self.mcp_execute_button.configure(state="normal" if self.catalog_identifier and not busy else "disabled")
        self.mcp_check.configure(state="disabled" if busy else "normal")
        self.mcp_back_button.configure(state="disabled" if busy else "normal")
        # Keep the operation/arguments area out of the unconnected first-run flow.
        if self.catalog_identifier:
            if not self.mcp_capability_host.winfo_manager():
                self.mcp_capability_host.pack(fill="both", expand=True, before=self.mcp_technical_host)
        else:
            self.mcp_capability_host.pack_forget()

    def _discard_mcp(self):
        if self.mcp_form.dirty():
            return messagebox.askyesno("未保存的服务设置",
                "切换会放弃当前未保存修改，是否继续？", parent=self, default="no")
        return True

    def _new_mcp(self) -> None:
        if self.busy or (hasattr(self, "mcp_form") and self.mcp_form._base and not self._discard_mcp()):
            return
        self._clear_catalog()
        self.mcp_list.selection_clear(0, "end")
        identifiers = {row["id"] for row in self.manager.list_mcp()}
        identifier, number = "my-service", 2
        while identifier in identifiers:
            identifier = f"my-service-{number}"
            number += 1
        self._mcp_loaded_id = ""
        self.mcp_form.load({"id": identifier, "name": "我的服务", "transport": "http",
            "url": "http://127.0.0.1:8000/mcp", "protocol_version": "2025-11-25",
            "headers_env": {}, "enabled": False})
        self.mcp_state.set("尚未连接 · 填写后可先保存")
        self._show_mcp_editor(new=True)
        self._mcp_controls()

    def _select_mcp(self, _event=None):
        selected = self._selection(self.mcp_list, self.mcp_rows)
        if not selected:
            return
        if selected["id"] == self._mcp_loaded_id:
            self._show_mcp_editor()
            return
        if self.busy or not self._discard_mcp():
            self._select_mcp_row(self._mcp_loaded_id)
            return
        self._mcp_loaded_id = selected["id"]
        self.mcp_form.load(selected, existing=True)
        self._clear_catalog()
        self.mcp_state.set("连接已建立 · 重新连接可读取能力" if self.manager.mcp_connected(selected["id"])
                           else "未连接 · 配置允许连接" if selected.get("enabled") else "已停用 · 勾选允许后再连接")
        self._mcp_controls()
        self._show_mcp_editor()

    def _select_mcp_row(self, identifier):
        self.mcp_list.selection_clear(0, "end")
        for index, row in enumerate(self.mcp_rows):
            if row["id"] == identifier:
                self.mcp_list.selection_set(index)
                self.mcp_list.see(index)
                return

    def _clear_catalog(self):
        self.catalog, self.catalog_identifier = {}, ""
        if hasattr(self, "operation"):
            self.operation.configure(values=[])
            self.operation.set("")

    def _mcp_value(self):
        return self.mcp_form.read()

    def _selected_mcp_id(self):
        return self._mcp_value()["id"]

    def _check_mcp_id(self, value):
        if not self._mcp_loaded_id and any(row["id"] == value["id"] for row in self.manager.list_mcp()):
            raise ValueError("此 ID 已存在。请在左侧选择已有服务，或给新服务换一个 ID。")

    def _mcp_saved(self, value):
        self._mcp_loaded_id = value["id"]
        self.mcp_form.load(value, existing=True)
        self._clear_catalog()
        self.mcp_list._search_query.set("")
        self.refresh()
        self._select_mcp_row(value["id"])

    def _save_mcp(self):
        if self.busy:
            return
        def action():
            value = self._mcp_value()
            self._check_mcp_id(value)
            def complete(_result):
                self._mcp_saved(value)
                self.mcp_state.set("已保存 · 尚未连接")
                self.status.set("配置已保存，没有启动程序或网络连接。")
            self._work("正在保存配置…", lambda: self.manager.upsert_mcp(value), complete,
                       on_error=lambda error: self._mcp_error(value["id"], error))
        self._guard(action)

    def _connect_mcp(self):
        if self.busy:
            return
        def action():
            value = self._mcp_value()
            if not value["enabled"]:
                self.mcp_state.set("先勾选“允许连接此服务”，再连接。")
                self.mcp_check.focus_set()
                return
            self._check_mcp_id(value)
            self._clear_catalog()
            identifier = value["id"]
            self.mcp_state.set("正在连接…")
            def connect():
                self.manager.upsert_mcp(value)
                self.manager.connect_mcp(identifier)
                return self.manager.catalog_mcp(identifier)
            def complete(result):
                self._mcp_saved(value)
                self._show_catalog(result, identifier)
            self._work("正在连接 MCP…",
                connect, complete,
                on_error=lambda error: self._connect_failed(value, error))
        self._guard(action)
        self._mcp_controls()

    def _connect_failed(self, value, error):
        try:
            saved = self.manager.store.get_item("mcp", value["id"])
        except (ValueError, OSError):
            saved = None
        if saved == value:
            self._mcp_saved(value)  # Save succeeded even if the handshake failed.
        self._mcp_error(value["id"], error)

    def _mcp_error(self, identifier, error):
        if not self._mcp_loaded_id or identifier == self._mcp_loaded_id:
            self.mcp_state.set("操作失败 · 请检查入口或参数后重试")
            self._set_text(self.mcp_output, error, readonly=True)
        self._mcp_controls()

    def _show_catalog(self, value, identifier):
        if self._mcp_loaded_id != identifier or self.mcp_form.dirty():
            self.mcp_state.set("连接已完成，但草稿已修改；保存并重新连接后查看能力。")
            return
        self.catalog, self.catalog_identifier = value, identifier
        operations = [f"工具 · {tool['name']}" for tool in value["tools"]] + [f"资源 · {resource['uri']}" for resource in value["resources"]] + [f"提示词 · {prompt['name']}" for prompt in value["prompts"]]
        self.operation.configure(values=operations)
        if operations:
            self.operation.current(0)
        self._set_text(self.mcp_output, value, readonly=True)
        self.mcp_state.set(f"已连接：{len(value['tools'])} 个工具、{len(value['resources'])} 个资源、{len(value['prompts'])} 个提示词。")
        self.status.set("能力来自真实服务。选择操作并检查参数后才会执行。")
        self._mcp_controls()

    def _inspect_operation(self, _event=None):
        if " · " not in self.operation.get():
            return
        kind, name = self.operation.get().split(" · ", 1)
        category = {"工具": "tools", "资源": "resources", "提示词": "prompts"}[kind]
        row = next((item for item in self.catalog.get(category, []) if item.get("name" if kind != "资源" else "uri") == name), {})
        self._set_text(self.mcp_output, row, readonly=True)

    def _execute_operation(self):
        if self.busy:
            return
        def action():
            identifier = self._selected_mcp_id()
            if identifier != self.catalog_identifier or " · " not in self.operation.get() or self.mcp_form.dirty():
                raise ValueError("配置已修改或尚未连接，请保存并重新连接后选择操作。")
            kind, name = self.operation.get().split(" · ", 1)
            args = json.loads(self.arguments.get("1.0", "end-1c"))
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象。")
            if kind == "工具":
                job = lambda: self.manager.call_tool(identifier, name, args, explicit_user_action=True)
            elif kind == "资源":
                job = lambda: self.manager.read_resource(identifier, name)
            else:
                if any(not isinstance(value, str) for value in args.values()):
                    raise ValueError("提示词参数必须是字符串。")
                job = lambda: self.manager.get_prompt(identifier, name, args)
            self._work(f"正在执行 {name}…", job,
                lambda result: self._set_text(self.mcp_output, result, readonly=True),
                on_error=lambda error: self._mcp_error(identifier, error))
        self._guard(action)

    def _disconnect_mcp(self):
        if self.busy:
            return
        def action():
            identifier = self._mcp_loaded_id
            self._clear_catalog()
            self._work("正在断开 MCP…", lambda: self.manager.disconnect_mcp(identifier),
                       lambda _result: self.mcp_state.set("已断开 · 配置保留"))
        self._guard(action)

    def _import_mcp(self):
        if self.busy or not self._discard_mcp():
            return
        path = filedialog.askopenfilename(parent=self, title="选择 MCP 配置 JSON",
                                         filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if path:
            def action():
                source = Path(path)
                if source.stat().st_size > 1024 * 1024:
                    raise ValueError("配置文件超过 1 MB，请只导入所需服务。")
                text = source.read_text(encoding="utf-8-sig")
                rows = parse_mcp_import(text)
                self._review_mcp_import(text, rows)
            self._guard(action)

    def _review_mcp_import(self, text, rows):
        from .mcp_import import review_mcp_import
        baseline = self.manager.store.read()
        existing = {row["id"] for row in baseline["mcp"]}
        if not review_mcp_import(self, rows, existing):
            self.status.set("已取消导入，没有修改配置。")
            return
        def apply_import():
            with self.manager.store.lock:
                if self.manager.store.read() != baseline:
                    raise ValueError("配置在预览期间发生变化，未覆盖；请重新导入检查。")
                return self.manager.import_mcp(text)
        self._work("正在导入 MCP 配置…", apply_import, self._mcp_imported,
                   on_error=lambda error: self._mcp_error(self._mcp_loaded_id, error))

    def _mcp_imported(self, rows):
        self.mcp_list._search_query.set("")
        self.refresh()
        self._mcp_loaded_id = rows[0]["id"]
        self.mcp_form.load(self.manager.store.get_item("mcp", self._mcp_loaded_id), existing=True)
        self._select_mcp_row(self._mcp_loaded_id)
        self._clear_catalog()
        self.mcp_state.set("已导入 · 全部停用，未连接")
        self._mcp_controls()
        self.status.set("导入完成。旧配置已有备份；检查后逐项允许连接。")
        self._show_mcp_editor()

    def _remove_mcp(self):
        selected = self._selection(self.mcp_list, self.mcp_rows)
        if self.busy or not selected:
            return
        if not messagebox.askyesno("移除 MCP 服务",
            f"移除 {selected.get('name', selected['id'])}？连接会关闭，原配置可从 data/integrations/backups 找回。",
            parent=self, default="no"):
            return
        def action():
            def complete(_result):
                self._mcp_loaded_id = ""
                self.mcp_form._base = {}
                self.refresh()
                self._new_mcp()
                self.status.set("服务已移除，旧配置已备份。")
            self._work("正在移除服务…", lambda: self.manager.remove_mcp(selected["id"]), complete,
                       on_error=lambda error: self._mcp_error(selected["id"], error))
        self._guard(action)
