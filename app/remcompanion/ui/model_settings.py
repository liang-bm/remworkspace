"""ModelSettingsMixin：ChatWindow 的单一界面职责；状态由宿主窗口持有。"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from .soft_fields import SoftEntry
from tkinter import ttk
from dataclasses import replace

from ..downloader import download_local_llm
from ..llm_client import test_provider_connection
from ..local_model_policy import save_local_model_policy
from ..provider_transactions import save_provider_credentials
from ..model_providers import LOCAL_PROVIDER_ID, ProviderConfig
from ..text_editing import bind_text_editing
from .events import ChatEvent
from .dpi import pixels
from .provider_management import ProviderManagementMixin
from .provider_models import ProviderModelsMixin
from ..provider_presets import PROVIDER_PRESETS, preset_for_id
from .theme import (
    APP_BG, WHITE, INK, MUTED, LINE, ACCENT, ACCENT_DEEP, ACCENT_SOFT, SUCCESS, ERROR,
)
from . import theme
from .scroll_surface import ScrollCanvas


class ModelSettingsMixin(ProviderModelsMixin, ProviderManagementMixin):
    """仅供 ChatWindow 组合使用；不持有独立模型、数据库或请求线程。"""

    def _build_model_provider_settings(self, panel: tk.Frame) -> None:
        """Provider list on the left; credentials and per-model draft on the right."""
        self.provider_state = self.service.provider_repository.load()
        self.selected_provider_id = self.provider_state.active_provider_id
        self._provider_drafts = {}
        self._provider_models = {}
        self._provider_model_sources = {}
        self._provider_requests = {}
        self._provider_refreshing = False
        # Presets are editor drafts only. Browsing never grows the persisted
        # provider state, activates a model or reads another provider's secret.
        self._provider_preset_configs = {}
        self._provider_extra_drafts = {}
        self.provider_filter_var = tk.StringVar(master=self.root)
        body = tk.Frame(panel, bg=WHITE)
        body.pack(fill="both", expand=True, padx=pixels(self.root, 12), pady=pixels(self.root, 12))
        body.columnconfigure(1, weight=1)
        provider_list = tk.Frame(body, bg=APP_BG, width=pixels(self.root, 196),
                                 highlightbackground=LINE, highlightthickness=1)
        provider_list.grid(row=0, column=0, sticky="new", padx=(0, pixels(self.root, 16)))
        tk.Label(provider_list, text="提供商", bg=APP_BG, fg=MUTED, anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill="x", padx=12, pady=(12, 6))
        search = SoftEntry(provider_list, textvariable=self.provider_filter_var, bg=WHITE, fg=INK,
                          insertbackground=INK, relief="flat", bd=0,
                          highlightbackground=LINE, highlightthickness=1,
                          font=("Microsoft YaHei UI", 9))
        search.pack(fill="x", padx=8, ipady=5, pady=(0, 8))
        bind_text_editing(search)
        self.provider_filter_label = tk.Label(provider_list, text="搜索提供商", bg=APP_BG, fg=MUTED,
                                              anchor="w", font=("Microsoft YaHei UI", 8))
        self.provider_filter_label.pack(fill="x", padx=10, pady=(0, 6))
        list_area = tk.Frame(provider_list, bg=APP_BG)
        list_area.pack(fill="both", expand=True)
        self.provider_list_canvas = ScrollCanvas(list_area, bg=APP_BG, highlightthickness=0,
                                              width=pixels(self.root, 184), height=pixels(self.root, 410))
        scroll = ttk.Scrollbar(list_area, orient="vertical", command=self.provider_list_canvas.yview)
        self.provider_list_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.provider_list_canvas.pack(side="left", fill="both", expand=True)
        self.provider_list_host = tk.Frame(self.provider_list_canvas, bg=APP_BG)
        list_window = self.provider_list_canvas.create_window((0, 0), window=self.provider_list_host, anchor="nw")
        self.provider_list_host.bind("<Configure>", lambda _e: self.provider_list_canvas.configure(
            scrollregion=self.provider_list_canvas.bbox("all")))
        self.provider_list_canvas.bind("<Configure>", lambda e: self.provider_list_canvas.itemconfigure(list_window, width=e.width))
        self.provider_buttons = {}
        self.provider_preset_buttons = {}
        self.provider_filter_var.trace_add("write", self._filter_provider_list)
        self._rebuild_provider_list()
        self._build_provider_management(provider_list)
        detail = tk.Frame(body, bg=WHITE)
        detail.grid(row=0, column=1, sticky="nsew")
        self.provider_title = tk.Label(detail, text="", bg=WHITE, fg=INK, anchor="w",
                                       font=("Microsoft YaHei UI", 13, "bold"))
        self.provider_title.pack(fill="x", pady=(0, 3))
        self.provider_title._remchat_locale_exclude = True
        self.provider_summary = tk.Label(detail, text="", bg=WHITE, fg=MUTED, anchor="w", justify="left",
                                         font=("Microsoft YaHei UI", 8))
        self.provider_summary.pack(fill="x", pady=(0, 12))
        for label in (self.provider_title, self.provider_summary):
            label.bind("<Configure>", lambda e, item=label: item.configure(wraplength=max(80, e.width)))
        for name in ("name", "base", "model", "key", "endpoint", "models_path", "format"):
            setattr(self, "provider_" + name + "_var", tk.StringVar(master=self.root))
        self.provider_requires_key = tk.BooleanVar(master=self.root, value=True)
        self.provider_advanced = tk.BooleanVar(master=self.root, value=False)
        self.provider_images = tk.BooleanVar(master=self.root, value=False)
        self.provider_name_entry = self._provider_field(detail, "提供商名称", self.provider_name_var)
        self.provider_base_entry = self._provider_field(detail, "Base URL", self.provider_base_var)
        self.provider_key_entry = self._provider_field(detail, "API Key", self.provider_key_var, show="•")
        auth_row = tk.Frame(detail, bg=WHITE)
        auth_row.pack(fill="x", pady=(0, 4))
        self.provider_auth_check = tk.Checkbutton(auth_row, text="需要密钥", variable=self.provider_requires_key,
                                                 bg=WHITE, fg=INK, selectcolor=WHITE)
        self.provider_auth_check.pack(side="left")
        self._setting_button(auth_row, "显示 / 隐藏", self._toggle_provider_key).pack(side="right")
        safety = tk.Label(detail, text="密钥留空保留原值；显式获取或测试会联网，测试可能计费。",
                          bg=WHITE, fg=MUTED, anchor="w", justify="left", font=("Microsoft YaHei UI", 8))
        safety.pack(fill="x", pady=(0, 6))
        safety.bind("<Configure>", lambda e: safety.configure(wraplength=max(80, e.width)))
        self._build_provider_models(detail)
        catalog_row = tk.Frame(detail, bg=WHITE)
        catalog_row.pack(fill="x", pady=(0, 10))
        self.provider_model_picker = ttk.Combobox(catalog_row, state="readonly", width=14)
        self.provider_model_picker.pack(side="left", fill="x", expand=True)
        self.provider_model_picker.bind("<<ComboboxSelected>>",
            lambda _e: self.provider_model_var.set(self.provider_model_picker.get()))
        self.provider_fetch_button = self._setting_button(catalog_row, "获取模型", self._fetch_provider_models)
        self.provider_fetch_button.pack(side="right", padx=(8, 0))
        self.provider_advanced_check = tk.Checkbutton(detail, text="确认该模型质量，允许生成接入草稿",
            variable=self.provider_advanced, bg=WHITE, fg=INK, selectcolor=WHITE, anchor="w", justify="left")
        self.provider_advanced_check.pack(fill="x")
        self.provider_images_check = tk.Checkbutton(detail, text="确认该模型支持图像输入",
            variable=self.provider_images, bg=WHITE, fg=INK, selectcolor=WHITE, anchor="w", justify="left")
        self.provider_images_check.pack(fill="x", pady=(0, 8))
        for control in (self.provider_advanced_check, self.provider_images_check):
            control.bind("<Configure>", lambda e, item=control: item.configure(wraplength=max(80, e.width)))
        self.provider_options_host = tk.Frame(detail, bg=WHITE)
        self.provider_options_host.pack(fill="x")
        self.provider_options_button = self._setting_button(self.provider_options_host, "接口高级选项 ▸", self._toggle_provider_options)
        self.provider_options_button.pack(anchor="w", pady=(2, 8))
        self.provider_options = tk.Frame(self.provider_options_host, bg=WHITE)
        self.provider_endpoint_entry = self._provider_field(self.provider_options, "聊天接口路径", self.provider_endpoint_var)
        self.provider_models_path_entry = self._provider_field(self.provider_options, "模型列表路径", self.provider_models_path_var)
        protocol_row = tk.Frame(self.provider_options, bg=WHITE)
        protocol_row.pack(fill="x", pady=(0, 8))
        tk.Label(protocol_row, text="JSON 输出模式", bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 8, "bold")).pack(side="left")
        self.provider_format_box = ttk.Combobox(protocol_row, textvariable=self.provider_format_var,
            values=("json_object", "json_schema", "prompt"), state="readonly", width=16)
        self.provider_format_box.pack(side="right", fill="x", expand=True, padx=(12, 0))
        self.provider_sampling_var = tk.BooleanVar(master=self.root)
        self.provider_output_parameter_var = tk.StringVar(master=self.root)
        self.provider_sampling_check = tk.Checkbutton(self.provider_options, text="发送温度与 top_p（需接口支持）",
            variable=self.provider_sampling_var, bg=WHITE, fg=INK, selectcolor=WHITE, anchor="w")
        self.provider_sampling_check.pack(fill="x")
        output_row = tk.Frame(self.provider_options, bg=WHITE)
        output_row.pack(fill="x", pady=(4, 8))
        tk.Label(output_row, text="输出参数", bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 8, "bold")).pack(side="left")
        self.provider_output_parameter_box = ttk.Combobox(output_row, textvariable=self.provider_output_parameter_var,
            values=("max_tokens", "max_completion_tokens"), state="readonly", width=20)
        self.provider_output_parameter_box.pack(side="right", fill="x", expand=True, padx=(12, 0))
        self.local_startup_panel = tk.Frame(detail, bg=WHITE)
        self.local_startup_var = tk.BooleanVar(master=self.root, value=self.service.config.llm.auto_start)
        self.local_startup_check = tk.Checkbutton(self.local_startup_panel, text="聊天时按需启动本地模型",
            variable=self.local_startup_var, command=self._save_local_startup_policy,
            bg=WHITE, fg=INK, selectcolor=WHITE, anchor="w")
        self.local_startup_check.pack(fill="x")
        self.provider_actions = tk.Frame(detail, bg=WHITE)
        self.provider_actions.pack(fill="x", pady=(10, 0))
        actions = self.provider_actions
        for column in range(2):
            actions.columnconfigure(column, weight=1)
        self.provider_save_activate_button = self._setting_button(actions, "保存并启用", self._save_provider_settings, primary=True)
        self.provider_save_activate_button.grid(
            row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        self._setting_button(actions, "保存", lambda: self._save_provider_settings(activate=False)).grid(
            row=0, column=1, sticky="ew", padx=(6, 0), pady=4)
        self.provider_test_button = self._setting_button(actions, "测试连接", self._test_current_provider)
        self.provider_test_button.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        self.provider_download_button = self._setting_button(actions, "安装本地模型", self._download_local_model)
        self.provider_download_button.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=4)
        self.provider_status = tk.Label(detail, text="", bg=WHITE, fg=MUTED, anchor="w", justify="left",
                                        font=("Microsoft YaHei UI", 8))
        self.provider_status.pack(fill="x", pady=(8, 0))
        self.provider_status.bind("<Configure>", lambda e: self.provider_status.configure(wraplength=max(80, e.width)))
        for variable in (self.provider_base_var, self.provider_endpoint_var):
            variable.trace_add("write", self._reset_model_capabilities)
        self.provider_model_var.trace_add("write", self._reset_current_model_capabilities)
        for variable in (self.provider_base_var, self.provider_models_path_var, self.provider_key_var, self.provider_requires_key):
            variable.trace_add("write", self._provider_catalog_changed)
        self._refresh_provider_editor()

    def _toggle_provider_options(self):
        if self.provider_options.winfo_manager():
            self.provider_options.pack_forget()
            self.provider_options_button.configure(text="接口高级选项 ▸")
        else:
            self.provider_options.pack(fill="x")
            self.provider_options_button.configure(text="接口高级选项 ▾")

    def _rebuild_provider_list(self) -> None:
        for widget in self.provider_list_host.winfo_children():
            widget.destroy()
        self.provider_buttons.clear()
        self.provider_preset_buttons.clear()
        self._provider_list_rows = []
        self._provider_list_heading(f"供应商 · {len(PROVIDER_PRESETS)} 个预设")
        for provider in self.provider_state.providers:
            if provider.is_local:
                self.provider_buttons[provider.provider_id] = self._provider_list_button(provider.display_name,
                    lambda value=provider.provider_id: self._select_provider(value))
        # One discoverable list, not two copies of the same official vendor.
        # Custom is last: common official vendors stay visible on first use.
        for preset in sorted(PROVIDER_PRESETS, key=lambda item: item.preset_id == "custom"):
            suffix = " · 协议未接入" if preset.transport != "openai_chat" else ""
            button = self._provider_list_button(
                preset.name + suffix, lambda value=preset.preset_id: self._select_provider_preset(value))
            self.provider_preset_buttons[preset.preset_id] = button
            configured = self._configured_provider_for_preset(preset)
            if configured:
                self.provider_buttons[configured.provider_id] = button
                self._provider_list_rows[-1] = (button, (preset.name + " " + configured.display_name).casefold())
        custom_configs = [item for item in self.provider_state.providers if item.provider_id not in self.provider_buttons]
        extra_drafts = [item for item in self._provider_extra_drafts.values() if item.provider_id not in self.provider_state.provider_ids]
        if custom_configs or extra_drafts:
            self._provider_list_heading("我的其他配置")
            for provider in custom_configs + extra_drafts:
                self.provider_buttons[provider.provider_id] = self._provider_list_button(
                    provider.display_name, lambda value=provider.provider_id: self._select_provider(value))
        self._filter_provider_list()

    def _provider_list_heading(self, text):
        label = tk.Label(self.provider_list_host, text=text, bg=APP_BG, fg=MUTED,
                         anchor="w", font=("Microsoft YaHei UI", 8, "bold"))
        label.pack(fill="x", padx=12, pady=(12, 5))
        label.bind("<MouseWheel>", self._scroll_provider_list)

    def _provider_list_button(self, label, command):
        button = self._setting_button(self.provider_list_host, label, command)
        button.configure(bg=APP_BG, fg=INK, anchor="w", padx=10, pady=9,
                         font=("Microsoft YaHei UI", 9), wraplength=pixels(self.root, 166))
        button.pack(fill="x", padx=5, pady=2)
        button.bind("<MouseWheel>", self._scroll_provider_list)
        button.bind("<Button-4>", lambda _e: self.provider_list_canvas.yview_scroll(-1, "units"))
        button.bind("<Button-5>", lambda _e: self.provider_list_canvas.yview_scroll(1, "units"))
        button._remchat_locale_exclude = True
        self._provider_list_rows.append((button, label.casefold()))
        return button

    def _filter_provider_list(self, *_args):
        query = self.provider_filter_var.get().strip().casefold()
        shown = 0
        labels = dict(getattr(self, "_provider_list_rows", ()))
        for child in self.provider_list_host.winfo_children():
            child.pack_forget()
        for child in self.provider_list_host.winfo_children():
            if child in labels:
                if not query or query in labels[child]:
                    child.pack(fill="x", padx=5, pady=2)
                    shown += 1
            elif not query:
                child.pack(fill="x", padx=12, pady=(12, 5))
        self.provider_filter_label.configure(text=(f"{shown} 项" if query else "搜索提供商"))
        self.provider_list_canvas.yview_moveto(0)

    def _provider_editor_config(self, provider_id=None):
        provider_id = provider_id or self.selected_provider_id
        if provider_id in self.provider_state.provider_ids:
            return self.provider_state.get(provider_id)
        for draft in (*self._provider_preset_configs.values(), *self._provider_extra_drafts.values()):
            if draft.provider_id == provider_id:
                return draft
        raise KeyError(f"未知提供商草稿：{provider_id}")

    def _select_provider_preset(self, preset_id):
        preset = preset_for_id(preset_id)
        if preset is None:
            return
        # Reuse an existing matching configuration; never refill it from a
        # changing upstream preset or copy a key between regional endpoints.
        existing = self._configured_provider_for_preset(preset)
        if existing:
            self._select_provider(existing.provider_id)
            return
        draft = self._provider_preset_configs.get(preset_id)
        if draft is None:
            draft = preset.create()
            self._provider_preset_configs[preset_id] = draft
        self._select_provider(draft.provider_id)

    def _configured_provider_for_preset(self, preset):
        return next((item for item in self.provider_state.providers if (
            item.preset_id == preset.preset_id or (item.base_url.rstrip("/") == preset.base_url.rstrip("/")
            and item.transport == preset.transport and item.usage_policy == preset.usage_policy)
        )), None)

    def _scroll_provider_list(self, event):
        if event.delta:
            self.provider_list_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _provider_field(
        self,
        parent: tk.Widget,
        label: str,
        variable: tk.StringVar,
        *,
        show: str | None = None,
    ) -> tk.Entry:
        tk.Label(
            parent,
            text=label,
            bg=WHITE,
            fg=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(fill="x", pady=(0, 5))
        entry = SoftEntry(
            parent,
            textvariable=variable,
            show=show or "",
            bg=APP_BG,
            fg=INK,
            insertbackground=INK,
            selectbackground="#CCE7F8",
            relief="flat",
            bd=0,
            highlightbackground=LINE,
            highlightthickness=1,
            font=("Microsoft YaHei UI", 9),
        )
        entry.pack(fill="x", ipady=5, pady=(0, 9))
        bind_text_editing(entry)
        return entry

    def _select_provider(self, provider_id: str) -> None:
        self._stash_provider_draft()
        self.selected_provider_id = provider_id
        self._refresh_provider_editor()
        self._schedule_theme_retouch()

    def _refresh_provider_editor(self) -> None:
        self._provider_refreshing = True
        try:
            self._load_provider_editor()
        finally:
            self._provider_refreshing = False
        self._refresh_provider_catalog()
        self._refresh_provider_request_buttons()

    def _load_provider_editor(self) -> None:
        provider = self._provider_editor_config()
        is_draft = provider.provider_id not in self.provider_state.provider_ids
        active = self.provider_state.active_provider_id
        for provider_id, button in self.provider_buttons.items():
            selected = provider_id == provider.provider_id
            is_active = provider_id == active
            listed = self._provider_editor_config(provider_id)
            button._remchat_color_roles = {
                "background": "ACCENT_SOFT" if selected else "APP_BG",
                "foreground": "ACCENT_DEEP" if selected or is_active else "INK",
                "activebackground": "ACCENT_SOFT", "activeforeground": "INK",
            }
            button.configure(
                text=f"{'●' if is_active else '○'}  {listed.display_name}" + (" · 草稿" if provider_id not in self.provider_state.provider_ids else ""),
                bg=ACCENT_SOFT if selected else APP_BG,
                fg=(ACCENT if theme.get_mode() == "dark" else ACCENT_DEEP) if selected or is_active else INK,
                activebackground=ACCENT_SOFT, activeforeground=INK,
                font=(
                    "Microsoft YaHei UI",
                    9,
                    "bold" if selected else "normal",
                ),
            )
        self.provider_title.configure(text=provider.display_name)
        preset = preset_for_id(provider.preset_id)
        for preset_id, button in self.provider_preset_buttons.items():
            item = preset_for_id(preset_id)
            selected = preset_id == provider.preset_id or (item.base_url.rstrip("/") == provider.base_url.rstrip("/")
                and item.transport == provider.transport and item.usage_policy == provider.usage_policy)
            button._remchat_color_roles = {"background": "ACCENT_SOFT" if selected else "APP_BG",
                "foreground": "ACCENT_DEEP" if selected else "INK", "activebackground": "ACCENT_SOFT",
                "activeforeground": "INK"}
            button.configure(bg=ACCENT_SOFT if selected else APP_BG,
                             fg=(ACCENT if theme.get_mode() == "dark" else ACCENT_DEEP) if selected else INK)
        self.provider_summary.configure(
            text=(
                "预设草稿 · 未保存"
                if is_draft
                else
                "当前启用 · 本地运行"
                if provider.provider_id == active and provider.is_local
                else "当前启用 · 远程 API"
                if provider.provider_id == active
                else "本地运行"
                if provider.is_local
                else "OpenAI Chat Completions"
            )
        )
        if provider.chat_block_reason:
            self.provider_summary.configure(text=provider.chat_block_reason, fg=ERROR)
        elif preset and preset.note:
            self.provider_summary.configure(text=preset.note, fg=MUTED)
        else:
            self.provider_summary.configure(fg=MUTED)
        self.provider_name_var.set(provider.display_name)
        self.provider_base_var.set(provider.base_url)
        self.provider_model_var.set(provider.model)
        self.provider_key_var.set("")
        self.provider_key_entry.configure(show="•")
        self.provider_endpoint_var.set(provider.endpoint_path)
        self.provider_models_path_var.set(provider.models_path)
        self.provider_format_var.set(provider.structured_output)
        self.provider_requires_key.set(provider.requires_api_key)
        self.provider_advanced.set(provider.advanced_model_enabled)
        self.provider_images.set(provider.supports_images)
        self.provider_sampling_var.set(provider.send_sampling)
        self.provider_output_parameter_var.set(provider.output_parameter)
        self._load_provider_model_rows(provider)
        self._restore_provider_draft()
        editable_state = "disabled" if provider.is_local else "normal"
        for entry in (
            self.provider_base_entry,
            self.provider_model_entry,
            self.provider_model_name_entry,
            self.provider_context_entry,
            self.provider_output_entry,
            self.provider_key_entry,
            self.provider_endpoint_entry,
            self.provider_models_path_entry,
        ):
            entry.configure(state=editable_state)
        for control in (self.provider_format_box, self.provider_model_picker, self.provider_output_parameter_box):
            control.configure(state="disabled" if provider.is_local else "readonly")
        self.provider_auth_check.configure(state=editable_state)
        self.provider_advanced_check.configure(state=editable_state)
        self.provider_images_check.configure(state=editable_state)
        self.provider_sampling_check.configure(state=editable_state)
        self.provider_add_model_button.configure(state=editable_state)
        self.provider_remove_model_button.configure(state=editable_state)
        self.provider_download_button.configure(
            state="normal" if provider.is_local else "disabled"
        )
        self.provider_save_activate_button.configure(state="disabled" if provider.chat_block_reason else "normal")
        self._refresh_provider_management_buttons(provider, is_draft)
        if provider.is_local:
            self.local_startup_var.set(self.service.config.llm.auto_start)
            self.local_startup_panel.pack(fill="x", pady=(0, 16), before=self.provider_actions)
            model_ready = self.service.config.qwen_model.is_file()
            runtime_ready = self.service.config.llama_server.is_file()
            self.provider_status.configure(
                text=(
                    f"模型 {'已就绪' if model_ready else '未下载'} · "
                    f"llama.cpp {'已就绪' if runtime_ready else '未安装'}"
                ),
                fg=SUCCESS if model_ready and runtime_ready else MUTED,
            )
        elif is_draft:
            self.local_startup_panel.pack_forget()
            self.provider_status.configure(text="未保存", fg=MUTED)
        else:
            self.local_startup_panel.pack_forget()
            try:
                has_key = bool(
                    self.service.secret_store.get(provider.provider_id)
                )
            except OSError:
                has_key = False
            self.provider_status.configure(
                text="API Key 已安全保存" if has_key else "尚未保存 API Key",
                fg=SUCCESS if has_key else MUTED,
            )

    def _save_local_startup_policy(self) -> None:
        previous = self.service.config.llm.auto_start
        requested = self.local_startup_var.get()
        try:
            save_local_model_policy(self.service.config.root, auto_start=requested)
        except (OSError, ValueError) as error:
            self.local_startup_var.set(previous)
            self.provider_status.configure(text=f"启动设置未保存，已保留原设置：{error}", fg=ERROR)
            self._schedule_theme_retouch()
            return
        self.service.config = replace(self.service.config, llm=replace(self.service.config.llm, auto_start=requested))
        self.provider_status.configure(text="启动设置已保存；不会立即启动或关闭模型。", fg=SUCCESS)
        self._schedule_theme_retouch()

    def _provider_from_editor(self, *, allow_empty_model: bool = False) -> ProviderConfig:
        current = self._provider_editor_config()
        if current.is_local:
            # 本地监听地址与模型别名来自 TOML，设置页不重复维护第二份真相。
            return replace(current,
                display_name=self.provider_name_var.get().strip(),
            ).validate()
        # GET /models does not require a complete model draft. Do not discard or
        # block its invalid/empty fields while the user is still adding a model.
        models = current.configured_models if allow_empty_model else self._models_from_editor()
        selected_model = current.active_model if allow_empty_model else models[self._model_index]
        return replace(current,
            display_name=self.provider_name_var.get().strip(),
            base_url=self.provider_base_var.get().strip(),
            endpoint_path=self.provider_endpoint_var.get().strip(),
            model=selected_model.model_id,
            models=models,
            structured_output=self.provider_format_var.get(),
            requires_api_key=self.provider_requires_key.get(),
            models_path=self.provider_models_path_var.get().strip(),
            advanced_model_enabled=self.provider_advanced.get(),
            supports_images=self.provider_images.get(),
            send_sampling=self.provider_sampling_var.get(),
            output_parameter=self.provider_output_parameter_var.get(),
        ).validate()

    def _save_provider_settings(self, *, activate: bool = True) -> None:
        try:
            provider = self._provider_from_editor()
            if not activate:
                previous = self._provider_editor_config(provider.provider_id)
                # Saving another model's draft must not silently switch the live model.
                ids = {item.model_id for item in provider.configured_models}
                if previous.model in ids:
                    provider = provider.for_model(previous.model)
            updated = self.provider_state.replace_provider(provider)
            if activate:
                updated = updated.activate(provider.provider_id)
            pending_key = self.provider_key_var.get().strip()
            self._provider_api_key(provider)
            if (
                provider.requires_api_key
                and activate
                and not pending_key
                and not self.service.secret_store.get(provider.provider_id)
            ):
                raise ValueError("请先输入 API Key，再保存并启用")
            save_provider_credentials(self.service.provider_repository, self.service.secret_store,
                                      self.provider_state, updated, provider, pending_key)
        except (OSError, ValueError) as error:
            # A failed rollback may leave the new public endpoint with no key.
            # Reflect the actual public snapshot, never an assumed old origin.
            self.provider_state = self.service.provider_repository.load()
            self.provider_status.configure(text=str(error), fg=ERROR)
            return
        self.provider_state = updated
        self._rebuild_provider_list()
        self._provider_drafts.pop(provider.provider_id, None)
        self._provider_model_drafts.pop(provider.provider_id, None)
        self.provider_key_var.set("")
        self._refresh_provider_editor()
        self.provider_status.configure(
            text=(f"已启用 {provider.display_name} · {provider.model}" if activate else "配置已保存"),
            fg=SUCCESS,
        )
        self._schedule_theme_retouch()
        if hasattr(self, "quick_model_box"):
            self._refresh_quick_models()

    def _test_current_provider(self) -> None:
        if ("test", self.selected_provider_id) in self._provider_requests:
            return
        try:
            provider = self._provider_from_editor()
            provider.ensure_chat_supported()
            api_key = self._provider_api_key(provider)
        except (OSError, ValueError) as error:
            self.provider_status.configure(text=str(error), fg=ERROR)
            return
        request = self._begin_provider_request("test", provider, api_key)
        if request is None:
            return
        self.provider_status.configure(
            text=f"正在连接 {provider.display_name}…",
            fg=MUTED,
        )

        def worker() -> None:
            started = time.monotonic()
            try:
                self.service.ensure_backends(
                    needs_tts=False,
                    provider=provider,
                )
                test_provider_connection(
                    self.service.config,
                    provider,
                    api_key=api_key,
                )
            except Exception as error:
                payload = (request, time.monotonic() - started, str(error))
            else:
                payload = (request, time.monotonic() - started, "")
            self._enqueue_event(ChatEvent("provider-test-result", payload))

        threading.Thread(
            target=worker,
            name="rem-provider-test",
            daemon=True,
        ).start()

    def _download_local_model(self) -> None:
        self.provider_download_button.configure(
            state="disabled",
            text="正在安装…",
        )
        self.provider_status.configure(
            text="正在下载本地模型；支持断点续传，请勿关闭应用。",
            fg=MUTED,
        )

        def log(message: str) -> None:
            self.events.put(ChatEvent("provider-download-log", message))

        def progress(name: str, downloaded: int, total: int | None) -> None:
            if total:
                text = (
                    f"{name} · {downloaded / (1024**2):.0f}/"
                    f"{total / (1024**2):.0f} MB"
                )
            else:
                text = f"{name} · {downloaded / (1024**2):.0f} MB"
            self.events.put(ChatEvent("provider-download-log", text))

        def worker() -> None:
            try:
                download_local_llm(
                    self.service.config,
                    log=log,
                    progress=progress,
                )
            except Exception as error:
                self.events.put(
                    ChatEvent("provider-download-error", str(error))
                )
            else:
                self.events.put(ChatEvent("provider-download-ok"))

        threading.Thread(
            target=worker,
            name="rem-local-model-download",
            daemon=True,
        ).start()
