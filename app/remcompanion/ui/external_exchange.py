"""主窗展示其他入口的请求阶段；不发请求、不落盘、不播音。"""

from __future__ import annotations

from dataclasses import dataclass

from ..dialogue import DialogueResult
from .message_bubble import MessageBubble


@dataclass
class ExternalExchange:
    user_text: str
    reply_widget: MessageBubble


class ExternalExchangeMixin:
    """Tk 主线程中的展示接收端，供 ChatWindow 组合使用。"""

    def begin_external_exchange(self, user_text: str) -> None:
        if self._closing or self._external_exchange is not None:
            return
        self._following_latest = True
        self._add_message("user", user_text)
        widget = self._add_message("assistant", "雷姆思考中…")
        self._external_exchange = ExternalExchange(user_text, widget)
        self._set_status("雷姆正在思考…")

    def _replace_external_reply(self, text: str) -> bool:
        exchange = self._external_exchange
        if exchange is None or not exchange.reply_widget.winfo_exists():
            return False
        widget = exchange.reply_widget
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        self._resize_single_message_bubble(
            widget, text, max(1, self.message_canvas.winfo_width()),
        )
        self._update_messages_layout_and_scroll()
        return True

    def show_external_reply_progress(self, text: str) -> None:
        """文字已生成但请求未结束；保留锁，不抢走气泡的输入焦点。"""
        if self._closing or not self._external_busy:
            return
        if self._replace_external_reply(text):
            self._set_status(
                "回复已生成 · 正在准备语音…"
                if self.audio_enabled.get() else "回复已生成",
            )

    def show_external_error(self, message: str) -> None:
        if self._closing:
            return
        self._replace_external_reply(f"回答失败：{message}")
        self._external_exchange = None
        self._set_status(f"悬浮框回答失败：{message}", error=True)

    def show_external_exchange(
        self, user_text: str, result: DialogueResult,
    ) -> None:
        """原位完成气泡的消息；兼容只有最终结果的既有调用方。"""
        if self._closing:
            return
        exchange = self._external_exchange
        if exchange is None or exchange.user_text != user_text:
            self._add_message("user", user_text)
            self._add_message("assistant", result.reply_zh)
        elif not self._replace_external_reply(result.reply_zh):
            self._add_message("assistant", result.reply_zh)
        self._external_exchange = None
        self._update_stats(
            message_count=result.message_count,
            memory_count=result.memory_count,
        )
        self._refresh_conversation_list()
        self._set_status(
            "回答完成 · 本轮语音已跳过" if result.audio_error else "回答完成",
            error=bool(result.audio_error),
        )
