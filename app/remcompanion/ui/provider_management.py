"""Provider editor commands, kept separate from layout and the chat request lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from urllib.parse import urlparse

from ..model_catalog import fetch_model_ids
from ..model_providers import (
    LOCAL_PROVIDER_ID, ProviderConfig, export_provider_config,
    import_provider_config, new_provider_id,
)
from ..provider_presets import preset_for_id
from .events import ChatEvent
from .theme import WHITE, MUTED, SUCCESS, ERROR


def _credential_tag(api_key: str | None) -> bytes:
    """In-memory equality only; never put credentials in events, caches or logs."""
    return sha256((api_key or "").encode("utf-8")).digest()


def _catalog_source(provider: ProviderConfig, credential: bytes) -> tuple:
    """Only GET /models inputs identify a catalog; selecting a model keeps it."""
    return provider.base_url, provider.models_path, provider.requires_api_key, provider.transport, provider.usage_policy, credential


@dataclass(frozen=True)
class _ProviderRequest:
    provider: ProviderConfig
    credential_tag: bytes


class ProviderManagementMixin:
    """Commands operate on the host's public snapshot; keys remain in the secret store."""

    def _build_provider_management(self, parent: tk.Frame) -> None:
        commands = tk.Frame(parent, bg=WHITE)
        commands.pack(fill="x", padx=5, pady=(18, 8))
        self.provider_management_buttons = {}
        for index, (label, command) in enumerate((
            ("新增", self._add_provider), ("复制配置", self._duplicate_provider),
            ("导入配置", self._import_provider), ("导出配置", self._export_provider),
            ("删除配置", self._delete_provider), ("清除密钥", self._clear_provider_key),
        )):
            button = self._setting_button(commands, label, command, danger=index >= 4)
            button.configure(padx=4, wraplength=0, font=("Microsoft YaHei UI", 8))
            button.grid(row=index // 2, column=index % 2, sticky="nsew", padx=2, pady=3)
            self.provider_management_buttons[label] = button
        commands.columnconfigure(0, weight=1)
        commands.columnconfigure(1, weight=1)

    def _refresh_provider_management_buttons(self, provider, is_draft):
        self.provider_management_buttons["删除配置"].configure(
            text="丢弃草稿" if is_draft else "删除配置", state="disabled" if provider.is_local else "normal")
        self.provider_management_buttons["清除密钥"].configure(
            state="disabled" if provider.is_local or is_draft else "normal")
        self.provider_management_buttons["复制配置"].configure(state="disabled" if provider.is_local else "normal")
        self.provider_management_buttons["导出配置"].configure(state="disabled" if provider.is_local else "normal")

    def _editor_variables(self) -> tuple:
        return (self.provider_name_var, self.provider_base_var, self.provider_model_var,
                self.provider_key_var, self.provider_endpoint_var, self.provider_models_path_var,
                self.provider_format_var, self.provider_requires_key, self.provider_advanced, self.provider_images,
                self.provider_sampling_var, self.provider_output_parameter_var)

    def _stash_provider_draft(self) -> None:
        self._stash_provider_model_rows()
        self._provider_drafts[self.selected_provider_id] = tuple(var.get() for var in self._editor_variables())

    def _restore_provider_draft(self) -> None:
        values = self._provider_drafts.get(self.selected_provider_id)
        if values:
            for variable, value in zip(self._editor_variables(), values):
                variable.set(value)

    def _install_provider(self, provider: ProviderConfig) -> None:
        updated = self.provider_state.replace_provider(provider)
        self.service.provider_repository.save(updated)
        self._stash_provider_draft()
        self.provider_state = updated
        self.selected_provider_id = provider.provider_id
        self._rebuild_provider_list()
        self._refresh_provider_editor()
        self.provider_status.configure(text="提供商已添加，尚未启用", fg=SUCCESS)
        self._schedule_theme_retouch()
        if hasattr(self, "quick_model_box"):
            self._refresh_quick_models()

    def _add_provider(self) -> None:
        self._stash_provider_draft()
        draft = preset_for_id("custom").create()
        self._provider_extra_drafts[draft.provider_id] = draft
        self.selected_provider_id = draft.provider_id
        self.provider_filter_var.set("")
        self._rebuild_provider_list()
        self._refresh_provider_editor()
        self.provider_name_entry.focus_set()
        self.provider_name_entry.select_range(0, "end")
        self._schedule_theme_retouch()

    def _duplicate_provider(self) -> None:
        try:
            source = self._provider_from_editor()
            if source.is_local:
                raise ValueError("内置模型不复制；可新增兼容接口连接其他本地服务")
            self._install_provider(replace(source, provider_id=new_provider_id(),
                display_name=source.display_name + self.translator(" · 副本"), advanced_model_enabled=False, supports_images=False,
                models=tuple(replace(model, advanced_model_enabled=False, supports_images=False) for model in source.configured_models)))
        except (OSError, ValueError) as error:
            self.provider_status.configure(text=str(error), fg=ERROR)

    def _delete_provider(self) -> None:
        provider = self._provider_editor_config()
        if provider.provider_id not in self.provider_state.provider_ids:
            # This branch discards memory only; deleting a virtual preset must
            # not remove its catalog entry, any saved configuration or secret.
            identifier = provider.provider_id
            self._provider_extra_drafts.pop(identifier, None)
            for preset_id, draft in list(self._provider_preset_configs.items()):
                if draft.provider_id == identifier:
                    del self._provider_preset_configs[preset_id]
            for drafts in (self._provider_drafts, self._provider_model_drafts,
                           self._provider_models, self._provider_model_sources):
                drafts.pop(identifier, None)
            for kind in ("models", "test"):
                self._provider_requests.pop((kind, identifier), None)
            self.selected_provider_id = self.provider_state.active_provider_id
            self.provider_key_var.set("")
            self._rebuild_provider_list()
            self._refresh_provider_editor()
            self.provider_status.configure(text="未保存草稿已丢弃", fg=MUTED)
            self._schedule_theme_retouch()
            return
        if provider.provider_id == LOCAL_PROVIDER_ID:
            self.provider_status.configure(text="不能删除内置本地模型", fg=ERROR)
            return
        if not messagebox.askyesno(self.translator("删除配置"), self.translator(
            "删除 {name} 的配置和本机密钥？若它正在启用，将切回本地模型。",
            name=provider.display_name), parent=self.root):
            return
        try:
            updated = self.provider_state.remove_provider(provider.provider_id)
            self.service.provider_repository.save(updated)
        except (OSError, ValueError) as error:
            self.provider_status.configure(text=str(error), fg=ERROR)
            return
        self.provider_state = updated
        self._provider_drafts.pop(provider.provider_id, None)
        self._provider_model_drafts.pop(provider.provider_id, None)
        self._provider_models.pop(provider.provider_id, None)
        self._provider_model_sources.pop(provider.provider_id, None)
        for kind in ("models", "test"):
            self._provider_requests.pop((kind, provider.provider_id), None)
        self.selected_provider_id = updated.active_provider_id
        self._rebuild_provider_list()
        self._refresh_provider_editor()
        try:
            self.service.secret_store.delete(provider.provider_id)
        except OSError:
            self.provider_status.configure(text="配置已删除，但密钥删除失败；请检查凭据目录。", fg=ERROR)
        else:
            self.provider_status.configure(text="配置与本机密钥已删除；配置备份不包含密钥。", fg=SUCCESS)
        self._schedule_theme_retouch()
        if hasattr(self, "quick_model_box"):
            self._refresh_quick_models()

    def _clear_provider_key(self) -> None:
        provider = self._provider_editor_config()
        if provider.provider_id not in self.provider_state.provider_ids:
            self.provider_key_var.set("")
            return
        if provider.is_local:
            return
        if not messagebox.askyesno(self.translator("清除密钥"), self.translator(
            "清除 {name} 的本机密钥？此操作不可恢复，远程聊天可能无法使用。", name=provider.display_name), parent=self.root):
            return
        try:
            self.service.secret_store.delete(provider.provider_id)
        except OSError as error:
            self.provider_status.configure(text=str(error), fg=ERROR)
            return
        self.provider_key_var.set("")
        self._stash_provider_draft()
        self.provider_status.configure(text="本机保存的密钥已清除", fg=SUCCESS)
        self._schedule_theme_retouch()

    def _toggle_provider_key(self) -> None:
        self.provider_key_entry.configure(show="" if self.provider_key_entry.cget("show") else "•")

    def _refresh_provider_request_buttons(self) -> None:
        """Busy belongs to the selected provider, not the last visible button."""
        provider = self._provider_editor_config()
        for kind, button, ready, busy in (
            ("models", self.provider_fetch_button, "获取模型", "获取中…"),
            ("test", self.provider_test_button, "测试连接", "测试中…"),
        ):
            pending = (kind, provider.provider_id) in self._provider_requests
            disabled = pending or bool(provider.chat_block_reason) or (kind == "models" and provider.is_local)
            button.configure(state="disabled" if disabled else "normal",
                             text=busy if pending else ready)

    def _begin_provider_request(self, kind: str, provider: ProviderConfig,
                                api_key: str | None) -> _ProviderRequest | None:
        key = (kind, provider.provider_id)
        if key in self._provider_requests:
            return None
        request = _ProviderRequest(provider, _credential_tag(api_key))
        self._provider_requests[key] = request
        self._refresh_provider_request_buttons()
        return request

    def _finish_provider_request(self, kind: str, request: _ProviderRequest) -> bool:
        key = (kind, request.provider.provider_id)
        # Object identity also rejects a duplicate completion from an old job.
        if self._provider_requests.get(key) is not request:
            return False
        del self._provider_requests[key]
        self._refresh_provider_request_buttons()
        if self.selected_provider_id != request.provider.provider_id:
            return False
        try:
            current = self._provider_from_editor(allow_empty_model=kind == "models")
            credential = _credential_tag(self._provider_api_key(current))
        except (OSError, ValueError, KeyError):
            current, credential = None, None
        matches = current is not None and credential == request.credential_tag
        if matches:
            matches = (
                _catalog_source(current, credential) == _catalog_source(request.provider, credential)
                if kind == "models" else current == request.provider
            )
        if not matches and not any(
            provider_id == self.selected_provider_id for _, provider_id in self._provider_requests
        ):
            self.provider_status.configure(text="设置已更改，旧请求结果未应用。", fg=MUTED)
            self._schedule_theme_retouch()
        return matches

    def _provider_catalog_changed(self, *_args) -> None:
        if self._provider_refreshing:
            return
        self._provider_models.pop(self.selected_provider_id, None)
        self._provider_model_sources.pop(self.selected_provider_id, None)
        self.provider_model_picker.configure(values=())
        self.provider_model_picker.set("")

    def _refresh_provider_catalog(self) -> None:
        try:
            current = self._provider_from_editor(allow_empty_model=True)
            source = _catalog_source(current, _credential_tag(self._provider_api_key(current)))
        except (OSError, ValueError, KeyError):
            source = None
        values = self._provider_models.get(self.selected_provider_id, ()) if (
            source is not None and self._provider_model_sources.get(self.selected_provider_id) == source
        ) else ()
        self.provider_model_picker.configure(values=values)
        self.provider_model_picker.set("")

    def _provider_api_key(self, provider: ProviderConfig) -> str | None:
        pending = self.provider_key_var.get().strip()
        if provider.provider_id not in self.provider_state.provider_ids:
            # Virtual presets have no persisted credentials. Never borrow a
            # configured provider's key merely because its name looks similar.
            return pending if provider.requires_api_key else None
        previous = self.provider_state.get(provider.provider_id)
        stored = self.service.secret_store.get(provider.provider_id) if not previous.is_local else None
        old_url, new_url = urlparse(previous.base_url), urlparse(provider.base_url)
        if stored and (not pending or not provider.requires_api_key) and (old_url.scheme, old_url.netloc.lower()) != (new_url.scheme, new_url.netloc.lower()):
            raise ValueError("接口来源已改变，请重新输入 API Key 或清除旧密钥，避免发往新地址")
        return (pending or stored) if provider.requires_api_key else None

    def _export_provider(self) -> None:
        try:
            provider = self._provider_from_editor()
            if provider.is_local:
                raise ValueError("内置本地模型请使用本机 TOML 配置")
            text = export_provider_config(provider)
            filename = filedialog.asksaveasfilename(parent=self.root, title=self.translator("导出配置"),
                defaultextension=".json", initialfile=provider.provider_id + ".json", filetypes=[("JSON", "*.json")])
            if not filename:
                return
            path = Path(filename)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        except (OSError, ValueError) as error:
            self.provider_status.configure(text=str(error), fg=ERROR)
            return
        self.provider_status.configure(text="配置已导出，不包含 API Key 或高级能力授权。", fg=SUCCESS)
        self._schedule_theme_retouch()

    def _import_provider(self) -> None:
        filename = filedialog.askopenfilename(parent=self.root, title=self.translator("导入配置"), filetypes=[("JSON", "*.json")])
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > 64 * 1024:
                raise ValueError("提供商配置不能超过 64 KB")
            provider = import_provider_config(path.read_text(encoding="utf-8-sig"))
            self._install_provider(provider)
        except (OSError, ValueError) as error:
            self.provider_status.configure(text=str(error), fg=ERROR)

    def _fetch_provider_models(self) -> None:
        if ("models", self.selected_provider_id) in self._provider_requests:
            return
        try:
            provider = self._provider_from_editor(allow_empty_model=True)
            provider.ensure_chat_supported()
            api_key = self._provider_api_key(provider)
        except (OSError, ValueError) as error:
            self.provider_status.configure(text=str(error), fg=ERROR)
            return
        if provider.is_local:
            return
        request = self._begin_provider_request("models", provider, api_key)
        if request is None:
            return
        self.provider_status.configure(text="正在查询模型列表，不会切换当前模型…", fg=MUTED)
        self._schedule_theme_retouch()

        def worker() -> None:
            try:
                models, seconds = fetch_model_ids(provider, api_key)
            except Exception as error:
                payload = (request, (), 0.0, str(error))
            else:
                payload = (request, models, seconds, "")
            self._enqueue_event(ChatEvent("provider-models", payload))
        threading.Thread(target=worker, name="remchat-model-catalog", daemon=True).start()

    def _accept_provider_models(self, payload: tuple) -> None:
        request, models, seconds, error = payload
        if not self._finish_provider_request("models", request):
            return
        if error:
            self.provider_status.configure(text=error, fg=ERROR)
            return
        provider = request.provider
        self._provider_models[provider.provider_id] = models
        self._provider_model_sources[provider.provider_id] = _catalog_source(provider, request.credential_tag)
        self.provider_model_picker.configure(values=models)
        self.provider_status.configure(text=f"已获取 {len(models)} 个模型（{seconds:.2f} 秒）；请选择或手动填写，不会自动启用。", fg=SUCCESS)

    def _request_integration_draft(self, prompt: str, callback) -> None:
        provider = self.service.provider_repository.load().active_provider
        if provider.is_local or not provider.advanced_model_enabled:
            raise ValueError("高级接入草稿需要已确认质量的 API 模型；本地小模型用于聊天与监工。")
        self._request_model_text(prompt, callback, expected_provider=provider,
                                 on_error=lambda error: callback("", error))

    def _accept_provider_test(self, payload: tuple) -> None:
        request, seconds, error = payload
        if not self._finish_provider_request("test", request):
            return
        self.provider_status.configure(text=(f"连接失败：{error[:180]}" if error else
            f"连接成功（{seconds:.2f} 秒）；这不是模型质量评测。"), fg=ERROR if error else SUCCESS)
