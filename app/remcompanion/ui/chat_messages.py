"""ChatMessagesMixin：ChatWindow 的单一界面职责；状态由宿主窗口持有。"""

from __future__ import annotations

import tkinter as tk
import sqlite3
from dataclasses import dataclass

from ..companion_preferences import MAX_CHAT_FONT_SIZE, MIN_CHAT_FONT_SIZE
from ..text_editing import bind_text_editing
from .text_layout import fit_message_text, fit_message_batch
from .message_bubble import MessageBubble
from .message_rows import layout_message_rows
from .theme import (
    APP_BG, INK, MUTED, LINE, ACCENT, ACCENT_DEEP, USER_BUBBLE, REM_BUBBLE,
)


@dataclass(frozen=True)
class ReadingAnchor:
    message: MessageBubble
    offset: float
    text_position: tuple[int, float] | None


class ChatMessagesMixin:
    """仅供 ChatWindow 组合使用；不持有独立模型、数据库或请求线程。"""

    def _bind_chat_font_shortcuts(self) -> None:
        """Ctrl 加减只改变主聊天文字，不触碰悬浮框或任何窗口尺寸。"""

        bindings = (
            ("<Control-plus>", 1),
            ("<Control-equal>", 1),
            ("<Control-KP_Add>", 1),
            ("<Control-minus>", -1),
            ("<Control-KP_Subtract>", -1),
        )
        for sequence, delta in bindings:
            self.root.bind(
                sequence,
                lambda _event, step=delta: self._adjust_chat_font(step),
                add="+",
            )

    def _adjust_chat_font(self, delta: int) -> str:
        selected = min(
            MAX_CHAT_FONT_SIZE,
            max(MIN_CHAT_FONT_SIZE, self.chat_font_size.get() + delta),
        )
        self.on_set_chat_font_size(selected)
        self._set_status(f"主聊天文字字号：{selected}")
        return "break"

    def _on_chat_font_changed(self, *_args: object) -> None:
        anchor = self._capture_reading_anchor()
        selected = self.chat_font_size.get()
        self.input_text.configure(
            font=("Microsoft YaHei UI", selected),
        )
        for message in self._message_labels:
            try:
                message.configure(
                    font=("Microsoft YaHei UI", selected),
                )
            except tk.TclError:
                continue
        self._resize_message_bubbles(
            max(1, self.message_canvas.winfo_width())
        )
        self._restore_reading_anchor(anchor)

    def _capture_reading_anchor(self, *, force=False):
        """Preserve the actual reading line when width or font changes."""
        if self._following_latest and not force:
            return None
        top = self.message_canvas.canvasy(0)
        bottom = top + self.message_canvas.winfo_height()
        for message in self._message_labels:
            if not message.winfo_exists():
                continue
            y = getattr(message, "_message_world_y", 0)
            if y < bottom and y + getattr(message, "_height", 0) > top:
                return ReadingAnchor(message, y - top, message.capture_text_anchor(top - y))
        return None

    def _restore_reading_anchor(self, anchor) -> None:
        if anchor is None:
            return
        message = anchor.message
        if not message.winfo_exists():
            return
        target = message._message_world_y - anchor.offset
        if anchor.text_position is not None:
            target = message._message_world_y + message.text_anchor_position(anchor.text_position)
        self.message_canvas.yview_moveto(target / max(1, self._messages_height))
        self._position_chat_background()

    def _begin_message_fold(self):
        anchor = self._capture_reading_anchor(force=True)
        # Expanding a message is a reading action, not a request to chase the
        # bottom as more content arrives or the expanded answer becomes taller.
        self._following_latest = False
        return anchor

    def _clear_messages(self) -> None:
        self._following_latest = True
        for message in self._message_labels:
            self.message_canvas.delete(message._row_window)
            message.master.destroy()
        for widget in self.messages_frame.winfo_children():
            widget.destroy()
        self._message_labels.clear()
        self._empty_state = None
        self._thinking_message_widget = None
        self._external_exchange = None
        self._history_before_id: int | None = None
        self._history_has_more = False
        self._history_loading = False
        self._history_control: tk.Widget | None = None

    def _load_history(self) -> None:
        self._clear_messages()
        try:
            page = self.service.memory.history_page(self.service.current_conversation_id)
        except (sqlite3.Error, OSError) as error:
            self._set_status(f"读取聊天历史失败：{error}", error=True)
            self._history_control = tk.Button(
                self.messages_frame, text="重新读取聊天历史", command=self._load_history,
                bg=APP_BG, fg=ACCENT_DEEP, relief="flat", cursor="hand2",
                activebackground=APP_BG, activeforeground=INK,
                font=("Microsoft YaHei UI", 10), highlightthickness=1,
                highlightbackground=APP_BG, highlightcolor=ACCENT_DEEP,
            )
            self._history_control.pack(pady=12)
            self._history_control.bind("<Return>", lambda _event: self._load_history())
            self._update_messages_layout_and_scroll()
            self._schedule_theme_retouch()
            return
        self._history_has_more = page.has_more
        if not page.messages:
            self._add_empty_state()
            self._update_messages_layout_and_scroll()
        else:
            self._history_before_id = page.messages[0].id
            self._history_control = tk.Button(
                self.messages_frame, command=self._load_earlier_messages,
                bg=APP_BG, fg=ACCENT_DEEP, relief="flat", bd=0,
                activebackground=APP_BG, activeforeground=INK,
                font=("Microsoft YaHei UI", 10), padx=12, pady=6,
                highlightthickness=1, highlightbackground=APP_BG,
                highlightcolor=ACCENT_DEEP,
            )
            self._history_control.pack(pady=(12, 4))
            self._history_control.bind("<Return>", lambda _event: self._load_earlier_messages())
            self._refresh_history_control()
            added = [self._add_message(item.role, item.content, update_layout=False)
                     for item in page.messages]
            self._finish_message_batch(added)
        self._update_stats()

    def _refresh_history_control(self) -> None:
        if self._history_control is None:
            return
        self._history_control.configure(
            text="正在读取更早消息…" if self._history_loading else
                 "查看更早消息" if self._history_has_more else "已到对话开头",
            state="normal" if self._history_has_more and not self._history_loading else "disabled",
            disabledforeground=MUTED,
            cursor="hand2" if self._history_has_more else "arrow",
        )
        if hasattr(self, "_locale_controller"):
            self._locale_controller.apply()

    def _finish_message_batch(self, added: list[tk.Text]) -> None:
        self.root.update_idletasks()
        fit_message_batch(added, max(1, self.message_canvas.winfo_width()))
        self._update_messages_layout_and_scroll()
        # Pack positions settle only after the Canvas window takes its final
        # height. Read pagination anchors after that geometry pass, not before.
        self.root.update_idletasks()
        self._schedule_theme_retouch()

    def _load_earlier_messages(self) -> str:
        """Prepend one local page, preserving existing widgets and viewport.

        The first existing message is our pixel anchor. Compensating for its
        new Y position keeps the reader in place even when the header shrinks.
        """
        if self._closing or self._history_loading or not self._history_has_more:
            return "break"
        first = next((widget for widget in self._message_labels if widget.winfo_exists()), None)
        if first is None:
            return "break"
        anchor = first
        self.root.update_idletasks()
        old_y = anchor._message_world_y
        old_top = self.message_canvas.canvasy(0)
        self._history_loading = True
        self._refresh_history_control()
        try:
            page = self.service.memory.history_page(
                self.service.current_conversation_id, before_id=self._history_before_id,
            )
        except (sqlite3.Error, OSError) as error:
            self._set_status(f"读取更早消息失败，可重试：{error}", error=True)
        else:
            self._following_latest = False
            # Reverse + insert at front preserves chronological widget order.
            added = []
            for item in reversed(page.messages):
                before = self._message_labels[0].master
                added.append(self._add_message(item.role, item.content, before=before, update_layout=False))
            if page.messages:
                self._history_before_id = page.messages[0].id
            self._history_has_more = page.has_more
            self._finish_message_batch(added)
            target = old_top + anchor._message_world_y - old_y
            self.message_canvas.yview_moveto(target / max(1, self._messages_height))
            self._position_chat_background()
        finally:
            self._history_loading = False
            self._refresh_history_control()
        return "break"

    def _add_empty_state(self) -> None:
        self._empty_state = tk.Frame(self.messages_frame, bg=APP_BG)
        # 不再横向铺满空状态容器，避免一整块纯色遮住背景图。
        self._empty_state.pack(pady=(150, 30))
        tk.Label(
            self._empty_state,
            text="◇",
            bg=APP_BG,
            fg=ACCENT,
            font=("Segoe UI", 28, "bold"),
        ).pack()
        tk.Label(
            self._empty_state,
            text="今天想和雷姆聊些什么？",
            bg=APP_BG,
            fg=INK,
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(pady=(8, 3))

    def _add_message(
        self, role: str, text: str, *, before: tk.Widget | None = None,
        update_layout: bool = True,
    ) -> tk.Text:
        if self._empty_state is not None:
            self._empty_state.destroy()
            self._empty_state = None
        # Each row is a separate Canvas window; the header frame stays small
        # even when a conversation's logical height exceeds native UI limits.
        container = tk.Frame(self.message_canvas, bg=APP_BG)
        message = MessageBubble(
            container, role=role,
            font=("Microsoft YaHei UI", self.chat_font_size.get()),
            background=self._message_background_crop,
            on_layout=self._update_messages_layout_and_scroll,
            visible_region=self._message_visible_region,
            before_layout=self._begin_message_fold,
            after_layout=self._restore_reading_anchor,
        )
        message._row_window = self.message_canvas.create_window(
            (0, 0), window=container, anchor="nw", state="hidden",
        )
        message.insert("1.0", text)
        message.configure(state="disabled")
        message.pack()
        if before is None:
            self._message_labels.append(message)
        else:
            self._message_labels.insert(0, message)
        if update_layout:
            self._resize_single_message_bubble(
                message, text, max(1, self.message_canvas.winfo_width()),
            )
            self._update_messages_layout_and_scroll()
            self._schedule_theme_retouch()
        return message

    def _resize_single_message_bubble(
        self,
        message: tk.Text,
        text: str,
        viewport_width: int,
    ) -> None:
        """保留兼容入口；真正的高度由 Tk 像素布局决定。"""
        try:
            fit_message_text(message, viewport_width)
        except tk.TclError:
            pass

    def _update_messages_layout_and_scroll(self) -> None:
        """Measure rows once, then update the logical scroll extent."""
        if (getattr(self, "_message_layout_busy", False) or self._closing
                or not self.message_canvas.winfo_ismapped()):
            return
        self._message_layout_busy = True
        try:
            layout_message_rows(self)
            if self._following_latest:
                self.message_canvas.yview_moveto(1.0)
            self._position_chat_background()
        finally:
            self._message_layout_busy = False

    def _resize_message_area(self, event) -> None:
        if self._closing:
            return
        size = (self.message_canvas.winfo_width(), self.message_canvas.winfo_height())
        if event is not None and event.type == tk.EventType.Configure:
            size = (event.width, event.height)
            if getattr(self, "_message_area_size", None) == size:
                return
        self._message_area_size = size
        if not self.message_canvas.winfo_ismapped():
            return
        if getattr(self, "_message_resize_after_id", None) is None:
            # Capture the reading line once before the first reflow; later
            # Configure events only replace the pending dimensions.
            self._message_resize_anchor = self._capture_reading_anchor()
            self._message_resize_after_id = self.root.after(16, self._resize_message_area_ready)

    def _resize_message_area_ready(self) -> None:
        self._message_resize_after_id = None
        if self._closing or not self.message_canvas.winfo_ismapped():
            return
        anchor = getattr(self, "_message_resize_anchor", None)
        self._message_resize_anchor = None
        self._resize_message_bubbles(self.message_canvas.winfo_width())
        if not self._message_labels:
            self._update_messages_layout_and_scroll()
        self._restore_reading_anchor(anchor)
        self._schedule_background_render()
        self._position_chat_background()

    def _resize_message_bubbles(self, viewport_width: int) -> None:
        """限制消息行宽并按实际换行数自动调整只读 Text 高度。"""

        if not self._message_labels:
            return
        fit_message_batch(self._message_labels, viewport_width)
        # Width, font and language callbacks all come through here. Reflow
        # changes requested heights; sync the parent too, or the last message
        # can be physically clipped despite having the correct Text row count.
        self._update_messages_layout_and_scroll()

    def _on_messages_frame_configure(self, _event) -> None:
        """The small history/empty-state header can change height independently."""
        size=(_event.width,_event.height)
        if getattr(self,'_message_header_size',None)==size:return
        self._message_header_size=size
        self._update_messages_layout_and_scroll()

    def _scroll_messages(self, *args: str) -> None:
        self.message_canvas.yview(*args)
        self._following_latest = self.message_canvas.yview()[1] >= 0.98
        self._position_chat_background()

    def _on_mousewheel(self, event) -> None:
        if event.widget.winfo_toplevel() is not self.root:
            return
        ancestor=event.widget
        settings_canvas=getattr(self,'settings_canvas',None)
        while ancestor is not None and ancestor not in (self.message_canvas,settings_canvas):
            ancestor=getattr(ancestor,'master',None)
        if self.current_view == "chat" and self.root.state() == "normal" and ancestor is self.message_canvas:
            self.message_canvas.yview_scroll(int(-event.delta / 120), "units")
            self._following_latest = self.message_canvas.yview()[1] >= 0.98
            self._position_chat_background()
            return 'break'
        elif self.current_view == "settings" and self.root.state() == "normal" and self.settings_canvas is not None and ancestor is self.settings_canvas and not isinstance(event.widget,tk.Text):
            self.settings_canvas.yview_scroll(
                int(-event.delta / 120),
                "units",
            )
            return 'break'

    def _scroll_to_bottom(self) -> None:
        self._following_latest = True
        if not self.message_canvas.winfo_ismapped():
            return
        self.message_canvas.yview_moveto(1.0)
        self._position_chat_background()
