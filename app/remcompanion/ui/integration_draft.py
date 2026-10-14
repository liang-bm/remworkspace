"""User-reviewed connection drafts, separate from MCP/Hook execution.

The host supplies text widgets, its UI queue and on_draft(prompt, callback).
Callbacks may pass text or (text, error); only the Tk queue applies results.
Stopping reception discards this generation, not the provider's inference.
"""

from __future__ import annotations

import tkinter as tk

from ..clipboard import copy_text
from .theme import WHITE


class IntegrationDraftMixin:
    def _build_draft(self, page: tk.Frame) -> None:
        self._draft_sequence = 0
        self._draft_active = None
        self._pending_draft = None
        shell = tk.Frame(page, bg=WHITE)
        shell.pack(fill="both", expand=True, padx=20, pady=16)
        body = self._scroll_detail(shell)
        self._label(body, "描述想接入的服务，并提供真实 URL 或本机程序路径。雷姆整理草稿，你检查后启用。")
        self._label(body, "整理草稿需已确认质量的 API 模型；本地小模型用于聊天与监工。不会自动执行工具。")
        self.draft_request = self._text(body, 4)
        actions = tk.Frame(body, bg=WHITE)
        actions.pack(fill="x", pady=4)
        buttons = [self._button(actions, label, command, primary=column == 0)
                   for column, (label, command) in enumerate((
            ("让雷姆整理草稿", self._generate_draft),
            ("复制接入提示词", self._copy_draft_prompt),
            ("导入已检查的草稿", self._import_draft),
        ))]
        # The shared button factory packs. Remove all siblings before using grid.
        for button in buttons:
            button.pack_forget()
        for column, button in enumerate(buttons):
            button.configure(wraplength=160, padx=8, takefocus=True)
            button.grid(row=0, column=column, sticky="w", padx=(0, 8))
            actions.columnconfigure(column, weight=1)
            if column == 0:
                self.draft_generate_button = button
        recovery = tk.Frame(body, bg=WHITE)
        recovery.pack(fill="x", pady=(0, 4))
        self.draft_stop_button = self._button(recovery, "停止接收草稿", self._stop_draft)
        self.draft_apply_button = self._button(recovery, "采用返回草稿", self._apply_pending_draft)
        for button in (self.draft_stop_button, self.draft_apply_button):
            button.configure(wraplength=200, takefocus=True)
        self.draft_output = self._text(body, 18, code=True)
        self._set_text(self.draft_output, "草稿不会执行命令，导入后所有接入均保持停用。")
        self._draft_controls()

    def _draft_controls(self) -> None:
        waiting = self._draft_active is not None
        self.draft_generate_button.configure(state="disabled" if waiting else "normal")
        self.draft_stop_button.configure(state="normal" if waiting else "disabled")
        self.draft_apply_button.configure(state="normal" if self._pending_draft is not None else "disabled")

    def _generate_draft(self) -> None:
        if self._draft_active is not None:
            self.status.set("本次草稿仍在等待；不会重复请求。")
            return
        request = self.draft_request.get("1.0", "end-1c").strip()
        if not request:
            self.status.set("先写下需要接入的服务与已有信息。")
            return
        if self.on_draft is None:
            if self._copy_draft_prompt():
                self.status.set("已复制完整提示词，可让当前 CLI 或聊天模型生成草稿后粘贴回来。")
            return
        self._draft_sequence += 1
        generation = self._draft_sequence
        self._draft_active = generation
        self._pending_draft = None
        self._draft_request_snapshot = request
        self._draft_output_snapshot = self.draft_output.get("1.0", "end-1c")
        self._draft_controls()
        self.status.set("雷姆正在整理接入草稿…")

        def receive(text: str, error: str = "") -> None:
            if not self.closed:
                self.messages.put(("draft", (generation, text, error)))
        try:
            self.on_draft(self.manager.draft_prompt(request), receive)
        except Exception as error:
            self._accept_draft((generation, "", str(error)))

    def _accept_draft(self, payload: tuple[int, str, str]) -> None:
        generation, text, error = payload
        if self.closed or generation != self._draft_active:
            return
        self._draft_active = None
        self._draft_controls()
        if error:
            self.status.set(f"草稿生成失败；原稿已保留：{error}")
            return
        if not isinstance(text, str) or not text.strip():
            self.status.set("未收到可用草稿，原稿已保留；请检查模型设置或稍后重试。")
            return
        edited = (self.draft_output.get("1.0", "end-1c") != self._draft_output_snapshot or
                  self.draft_request.get("1.0", "end-1c").strip() != self._draft_request_snapshot)
        if edited:
            self._pending_draft = text
            self._draft_controls()
            self.status.set("你已修改需求或草稿；返回内容已暂存。点击“采用返回草稿”才会替换原稿。")
        else:
            self._set_text(self.draft_output, text)
            self.status.set("草稿已生成。检查 URL、程序和参数后再导入。")

    def _stop_draft(self) -> None:
        if self._draft_active is None:
            return
        self._draft_active = None
        self._draft_controls()
        self.status.set("已停止接收本次草稿；不取消模型调用，迟到结果会忽略，原稿保留。")

    def _apply_pending_draft(self) -> None:
        if self._pending_draft is None:
            return
        self._set_text(self.draft_output, self._pending_draft)
        self._pending_draft = None
        self._draft_controls()
        self.status.set("已采用返回草稿；检查后再导入，不会自动启用。")

    def _copy_draft_prompt(self) -> bool:
        prompt = self.manager.draft_prompt(self.draft_request.get("1.0", "end-1c"))
        if copy_text(self, prompt):
            self.status.set("接入提示词已复制。")
            return True
        self.status.set("提示词复制未完成；原稿保留，请重试复制。")
        return False

    def _import_draft(self) -> None:
        self._guard(lambda: (self.manager.import_draft(self.draft_output.get("1.0", "end-1c")), self.refresh(), self.status.set("草稿已导入，所有条目停用。去对应页检查，再显式启用。")))
