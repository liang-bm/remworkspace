"""Main-chat attachment/workspace controls. The floating bubble owns none of this state."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
from .localized_dialogs import filedialog

from ..text_editing import bind_text_editing
from ..workspace_context import ContextSnapshot, MAX_ATTACHMENTS, read_attachment
from .theme import WHITE, INK, MUTED, ACCENT_SOFT, ACCENT_DEEP, LINE
from .composer_modes import MODE_IDS, capture_mode, mode_copy


class ComposerContextMixin:
    def _build_context_toolbar(self) -> None:
        self.workspace_directory: Path | None = None
        self.attachments = []
        self._sent_context = ContextSnapshot()
        self.composer_mode_id = "ask"
        self.project_process_enabled = tk.BooleanVar(self.root, False)
        self.project_process_panel = None
        tools = tk.Frame(self.composer, bg=WHITE)
        tools.pack(side="bottom", fill="x", padx=22, pady=(0, 8))
        row = tk.Frame(tools, bg=WHITE)
        row.pack(fill="x")
        self.attachment_add_button = self._setting_button(row, "＋", self._open_attachment_menu)
        self.attachment_add_button.pack(side="left", padx=(0, 8))
        self.workspace_button = self._setting_button(row, "选择工作空间", self._choose_workspace)
        self.workspace_button.pack(side="left", padx=(0, 12))
        self.workspace_button._remchat_locale_exclude = True
        self.quick_model_box = ttk.Combobox(row, state="readonly", width=26)
        self.quick_model_box.pack(side="right", fill="x", expand=True)
        self.quick_model_box.bind("<<ComboboxSelected>>", self._quick_switch_model)
        self.context_controls = tk.Frame(tools, bg=WHITE)
        self.context_controls.pack(fill="x", pady=(5, 0))
        self.composer_mode_button = self._setting_button(self.context_controls, "Ask", self._open_composer_modes)
        self.composer_mode_button.configure(pady=2, padx=8, font=("Microsoft YaHei UI", 8))
        self.composer_mode_button.pack(side="left", padx=(0, 8))
        self.composer_mode_button._remchat_locale_exclude = True
        self.attachment_review_button = self._setting_button(self.context_controls, "附件 0", self._preview_attachments)
        self.attachment_review_button.configure(pady=2, padx=8, font=("Microsoft YaHei UI", 8))
        self.attachment_review_button.pack(side="right")
        self.attachment_review_button._remchat_locale_exclude = True
        self.context_notice = tk.Label(self.context_controls, bg=WHITE, fg=MUTED,
                                       font=("Microsoft YaHei UI", 8), anchor="w", justify="left")
        self.context_notice.pack(side="left", fill="x", expand=True)
        self.context_notice._remchat_locale_exclude = True
        self.context_notice.bind("<Configure>", lambda event: self.context_notice.configure(wraplength=max(160, event.width)))
        self._refresh_quick_models()
        self._refresh_context_controls()

    def _context_mutation_allowed(self) -> bool:
        if self.busy or self.service.is_busy:
            self._set_status("当前回答完成后再更改模型或附件", error=True)
            return False
        return True

    def _context_snapshot(self) -> ContextSnapshot:
        return ContextSnapshot(tuple(self.attachments), self.workspace_directory.name if self.workspace_directory else "")

    def _refresh_context_controls(self) -> None:
        if not hasattr(self, "quick_model_box"):
            return
        tr = self.translator
        folder = self.workspace_directory.name if self.workspace_directory else ""
        self.workspace_button.configure(text=(folder[:16] + ("…" if len(folder) > 16 else "")) if folder else tr("选择工作空间"))
        self.attachment_review_button.configure(text=tr("附件 {count}", count=len(self.attachments)))
        provider = self.service.provider_repository.load().active_provider
        self.context_notice.configure(text=tr("只读附件 · 不执行命令") if provider.is_local else tr("只读附件 · 发送到当前 API · 不执行命令"))
        self._refresh_composer_mode()

    def _mode_words(self):
        return mode_copy(getattr(self, "ui_language", "zh-Hans"))

    def _refresh_composer_mode(self) -> None:
        if not hasattr(self, "composer_mode_button"):
            return
        # A model switch cannot leave a hidden, now-ineligible Collaboration.
        provider = self.service.provider_repository.load().active_provider
        if self.composer_mode_id == "collaborate" and (provider.is_local or not provider.advanced_model_enabled):
            if not (self.busy or self.service.is_busy):
                self.composer_mode_id = "ask"
        try:
            self.composer_mode_button.configure(text=self._mode_words()[self.composer_mode_id] + " ▾",
                state="disabled" if self.busy or self.service.is_busy else "normal")
        except tk.TclError:
            pass  # Closing a request never resurrects a destroyed composer.

    def _set_composer_mode(self, mode_id: str) -> bool:
        if self.busy or self.service.is_busy:
            self._set_status(self._mode_words()["busy"], error=True)
            return False
        if mode_id not in MODE_IDS:
            self._set_status("聊天模式无效，请重新选择", error=True)
            return False
        provider = self.service.provider_repository.load().active_provider
        try:
            capture_mode(mode_id, provider)
        except ValueError:
            self._set_status(self._mode_words()["locked"], error=True)
            return False
        self.composer_mode_id = mode_id
        self._refresh_composer_mode()
        self._set_status(self._mode_words()["changed"])
        return True

    def _composer_mode_group(self):
        words = self._mode_words()
        provider = self.service.provider_repository.load().active_provider
        available = not provider.is_local and provider.advanced_model_enabled
        items = []
        for mode_id in MODE_IDS:
            mark = "● " if self.composer_mode_id == mode_id else "○ "
            description = words["locked_help"] if mode_id == "collaborate" and not available else words[mode_id + "_help"]
            enabled = not (self.busy or self.service.is_busy) and (mode_id != "collaborate" or available)
            items.append((mark + words[mode_id], description, lambda value=mode_id: self._set_composer_mode(value), enabled))
        items.append((words["settings"], words["settings_help"], self._open_context_models))
        return words["title"], tuple(items)

    def _open_composer_modes(self) -> None:
        if self.busy or self.service.is_busy:
            self._set_status(self._mode_words()["busy"], error=True)
            return
        old = getattr(self, "_attachment_menu", None)
        if old is not None and old.winfo_exists():
            old.close()
        from .composer_popover import ComposerPopover
        self._attachment_menu = ComposerPopover(self.root, self.composer_mode_button,
            (self._composer_mode_group(),), tr=self.translator)

    def _capture_composer_request(self, snapshot, provider):
        mode = capture_mode(self.composer_mode_id, provider)
        return replace(snapshot, request_instruction=mode.prompt()), mode

    def _record_project_request(self, text: str, mode) -> None:
        trace = self.integration_manager.project_trace
        if not trace.enabled:
            return
        try:
            observed = trace.snapshot()
            source = "RemChat · " + mode.mode_id
            recorded_text = text if len(text) <= 12000 else text[:11950] + "\n[本地过程记录仅保留前 11950 个字符；原文在聊天记录中]"
            payload = {"session_id": observed["session_id"], "source": source}
            if observed.get("goal"):
                payload.update(kind="request", text=recorded_text, reason="用户选择 " + mode.mode_id + "；仅问答/计划/只读拟稿，不执行项目操作")
            else:
                payload.update(kind="goal", title=recorded_text)
            trace.accept(payload)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            # Observation is optional; a local recording failure never retries
            # a model/TTS request or changes the user's draft.
            self.service.log("[项目观察] 本轮请求未记录：" + str(error))

    def _refresh_quick_models(self) -> None:
        if not hasattr(self, "quick_model_box"):
            return
        state = self.service.provider_repository.load()
        choices = [(provider, model) for provider in state.providers if not provider.chat_block_reason
                   for model in provider.configured_models]
        self._quick_model_keys = [(provider.provider_id, model.model_id) for provider, model in choices]
        self.quick_model_box.configure(values=tuple(
            f"{model.display_name or model.model_id} · {self.translator('本地' if provider.is_local else 'API')} · {provider.display_name}"
            for provider, model in choices))
        active = (state.active_provider_id, state.active_provider.model)
        if active in self._quick_model_keys:
            self.quick_model_box.current(self._quick_model_keys.index(active))
        else:
            self.quick_model_box.set("")
        self._refresh_context_controls()

    def _quick_switch_model(self, _event=None) -> None:
        if not self._context_mutation_allowed():
            self._refresh_quick_models()
            return
        index = self.quick_model_box.current()
        if index < 0 or index >= len(self._quick_model_keys):
            return
        state = self.service.provider_repository.load()
        try:
            provider_id, model_id = self._quick_model_keys[index]
            provider = state.get(provider_id).for_model(model_id)
            provider.ensure_chat_supported()
            if provider.requires_api_key and not self.service.secret_store.get(provider.provider_id):
                raise ValueError("该模型尚未配置 API Key，请先在设置中保存")
            self._context_snapshot().validate(supports_images=provider.supports_images)
            updated = state.activate(provider.provider_id, model_id)
            self.service.provider_repository.save(updated)
        except (KeyError, OSError, ValueError) as error:
            self._refresh_quick_models()
            self._set_status(str(error), error=True)
            return
        self._stash_provider_draft()
        self.provider_state = updated
        self._refresh_provider_editor()
        self._refresh_quick_models()
        self._set_status("已切换模型，下次发送生效")
        self._schedule_theme_retouch()

    def _choose_workspace(self) -> None:
        if not self._context_mutation_allowed():
            return
        chosen = filedialog.askdirectory(parent=self.root, title=self.translator("选择工作空间"),
                                          initialdir=str(self.workspace_directory or self.service.config.root))
        if not chosen:
            return
        path = Path(chosen).resolve()
        if not path.is_dir():
            self._set_status("工作空间目录不存在", error=True)
            return
        if path != self.workspace_directory:
            self._stop_project_process()
            if hasattr(self, "composer_shortcuts"):
                self.composer_shortcuts.reset()
        self.workspace_directory = path
        self._refresh_context_controls()
        self._set_status("工作空间仅用于选文件；不会自动扫描或修改项目")

    def _open_attachment_menu(self) -> None:
        old = getattr(self, "_attachment_menu", None)
        if old is not None and old.winfo_exists():
            old.close()
            return
        from .composer_popover import ComposerPopover
        groups = (
            ("附件与工作空间", (
                ("添加文件或图像", "仅发送本轮选中的内容", self._choose_attachments),
                ("查看本轮附件", "发送前检查，支持移除", self._preview_attachments),
                ("清空本轮附件", "不会删除磁盘上的文件", self._clear_attachments),
                ("取消工作空间选择", "不会修改项目目录", self._clear_workspace))),
            self._composer_mode_group(),
            ("技能与接入", (
                ("Skills / MCP 管理", "查看已安装技能和工具连接", self._open_context_integrations),
                ("模型设置", "本地模型或自定义 API", self._open_context_models))))
        self._attachment_menu = ComposerPopover(self.root, self.attachment_add_button,
                                                groups, tr=self.translator)

    def _switch_project_observation(self) -> None:
        self.project_process_enabled.set(not self.project_process_enabled.get())
        self._toggle_project_process()

    def _open_context_integrations(self) -> None:
        self._show_integration_resources("skills")

    def _open_context_models(self) -> None:
        self._show_settings()
        self._show_settings_category("models")

    def _choose_attachments(self) -> None:
        if not self._context_mutation_allowed():
            return
        chosen = filedialog.askopenfilenames(parent=self.root, title=self.translator("添加文件或图像"),
            initialdir=str(self.workspace_directory or self.service.config.root),
            filetypes=[(self.translator("文字、代码和图像"), "*.txt *.md *.py *.js *.ts *.json *.toml *.csv *.srt *.png *.jpg *.jpeg *.webp"), ("All", "*.*")])
        if not chosen:
            return
        self._add_attachment_paths(chosen)

    def _add_attachment_paths(self, paths) -> None:
        if not self._context_mutation_allowed():
            return
        try:
            items = list(self.attachments)
            known = {item.path for item in items}
            for path in paths:
                selected = Path(path).expanduser().resolve(strict=True)
                if selected in known:
                    continue
                if len(items) >= MAX_ATTACHMENTS:
                    raise ValueError("一轮最多添加 5 个附件")
                item = read_attachment(path, workspace=self.workspace_directory)
                if item.path not in known:
                    items.append(item)
                    known.add(item.path)
            provider = self.service.provider_repository.load().active_provider
            ContextSnapshot(tuple(items)).validate(supports_images=provider.supports_images)
        except (OSError, ValueError) as error:
            self._set_status(str(error), error=True)
            return
        self.attachments = items
        self._refresh_context_controls()
        self._set_status("附件快照已添加；可查看内容，成功发送后清空")

    def _preview_attachments(self) -> None:
        if not self.attachments:
            self._set_status("本轮没有附件")
            return
        preview = tk.Toplevel(self.root)
        preview.title(self.translator("查看本轮附件"))
        preview.geometry("760x540")
        preview.configure(bg=WHITE)
        text = tk.Text(preview, wrap="word", bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 10), bd=0, padx=18, pady=16)
        scrollbar = tk.Scrollbar(preview, command=text.yview)
        scrollbar.pack(side="right", fill="y")
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(fill="both", expand=True)
        text.insert("1.0", self._context_snapshot().preview())
        text.configure(state="disabled")
        bind_text_editing(text, read_only=True)
        self._schedule_theme_retouch()

    def _clear_attachments(self) -> None:
        if not self._context_mutation_allowed():
            return
        self.attachments.clear()
        self._refresh_context_controls()
        self._set_status("本轮附件已清空，原文件未修改")

    def _clear_workspace(self) -> None:
        if self._context_mutation_allowed():
            self._stop_project_process()
            if hasattr(self, "composer_shortcuts"):
                self.composer_shortcuts.reset()
            self.workspace_directory = None
            self._refresh_context_controls()

    def _reset_composer_context(self) -> None:
        if hasattr(self, "composer_shortcuts"):
            self.composer_shortcuts.reset()
        self._stop_project_process()
        self.attachments.clear()
        self.workspace_directory = None
        self._sent_context = ContextSnapshot()
        self._refresh_context_controls()

    def _stop_project_process(self) -> None:
        trace = self.integration_manager.project_trace
        if trace.enabled:
            trace.disable()
        self.project_process_enabled.set(False)
        if self.project_process_panel is not None:
            self.project_process_panel.refresh(force=True)

    def _toggle_project_process(self) -> None:
        trace = self.integration_manager.project_trace
        if trace.enabled:
            self._stop_project_process()
            self._set_status("项目观察已关闭；外部任务不受影响")
            return
        if self.workspace_directory is None:
            self._choose_workspace()
        if self.workspace_directory is None:
            self.project_process_enabled.set(False)
            return
        try:
            trace.enable(self.workspace_directory)
        except (OSError, ValueError, RuntimeError) as error:
            self.project_process_enabled.set(False)
            self._set_status(str(error), error=True)
            return
        self.project_process_enabled.set(True)
        self._show_project_process()
        self._set_status("项目观察已开启；仅本地只读，不额外调用模型")

    def _show_project_process(self) -> None:
        self._show_plaza("process")

    def _commit_composer_context(self) -> None:
        if tuple(self.attachments) == self._sent_context.attachments:
            self.attachments.clear()
        self._sent_context = ContextSnapshot()
        self._refresh_context_controls()
