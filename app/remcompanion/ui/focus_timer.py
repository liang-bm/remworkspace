"""FocusTimerMixin：ChatWindow 的单一界面职责；状态由宿主窗口持有。"""

from __future__ import annotations

import time
import threading
import tkinter as tk
import math

from .events import ChatEvent
from .theme import ROSE, ACCENT_DEEP, MUTED


class FocusTimerMixin:
    """仅供 ChatWindow 组合使用；不持有独立模型、数据库或请求线程。"""

    @staticmethod
    def _format_focus_time(seconds: int) -> str:
        minutes, remainder = divmod(max(0, seconds), 60)
        return f"{minutes:02d}:{remainder:02d}"

    def _sync_focus_controls(self, remaining: int | None = None) -> None:
        active = self._focus_is_active()
        self.focus_minutes_input.configure(
            state="disabled" if active else "normal",
        )
        self.focus_toggle_button.configure(
            text="结束" if active else "开始",
            bg=ROSE if active else ACCENT_DEEP,
        )
        if active:
            shown = self._format_focus_time(remaining if remaining is not None else 0)
            self.focus_status.configure(
                text=shown,
                fg=ACCENT_DEEP,
            )
        else:
            self.focus_status.configure(text="", fg=MUTED)

    @staticmethod
    def _parse_focus_minutes(value: str) -> int:
        try:
            minutes = int(value.strip())
        except ValueError as error:
            raise ValueError("专注时间请输入 1–180 的整数") from error
        if not 1 <= minutes <= 180:
            raise ValueError("专注时间请输入 1–180 的整数")
        return minutes

    def _toggle_focus_timer(self) -> None:
        if not self._focus_is_active():
            self._start_focus_timer()
        else:
            self._stop_focus_timer()

    def _start_focus_timer(self) -> None:
        if self._focus_is_active():
            return
        try:
            minutes = self._parse_focus_minutes(self.focus_minutes.get())
        except ValueError as error:
            self._set_status(str(error), error=True)
            return
        self._focus_total_seconds = minutes * 60
        self._focus_paused_remaining = None
        self._focus_deadline = time.monotonic() + self._focus_total_seconds
        try:
            self.on_set_focus_mode(True)
            from .focus_curtain import FocusCurtain
            self._focus_curtain = FocusCurtain(
                self.root, snapshot=self._focus_snapshot, on_pause=self._pause_focus_timer,
                on_end=self._stop_focus_timer, keep_pet_visible=self.on_keep_focus_pet_visible,
            )
            self.on_focus_curtain(True)
            wallpaper = self._background_paths[self._background_index % len(self._background_paths)] if self._background_paths else None
            self._focus_curtain.show(wallpaper)
        except (OSError, RuntimeError, tk.TclError) as error:
            self._finish_focus_timer(completed=False)
            self._set_status(f"专注幕布无法打开：{error}", error=True)
            return
        self._set_status(f"{minutes} 分钟专注已开始")
        self._focus_tick()

    def _stop_focus_timer(self) -> None:
        if not self._focus_is_active():
            return
        self._finish_focus_timer(completed=False)

    def _focus_tick(self) -> None:
        self._focus_after_id = None
        if not self._focus_is_active():
            return
        remaining = math.ceil(self._focus_snapshot()[0])
        if remaining <= 0:
            self._finish_focus_timer(completed=True)
            return
        self._sync_focus_controls(remaining)
        self._focus_after_id = self.root.after(1000, self._focus_tick)

    def _focus_is_active(self) -> bool:
        return self._focus_deadline is not None or getattr(self, "_focus_paused_remaining", None) is not None

    def _focus_snapshot(self) -> tuple[float, float, bool]:
        paused = self._focus_paused_remaining is not None
        remaining = self._focus_paused_remaining if paused else max(
            0.0, (self._focus_deadline or 0.0) - time.monotonic(),
        )
        return remaining, self._focus_total_seconds, paused

    def _pause_focus_timer(self) -> None:
        if not self._focus_is_active():
            return
        if self._focus_paused_remaining is not None:
            self._focus_deadline = time.monotonic() + self._focus_paused_remaining
            self._focus_paused_remaining = None
            self.on_set_focus_mode(True)
        else:
            remaining = self._focus_snapshot()[0]
            if remaining <= 0:
                self._finish_focus_timer(completed=True)
                return
            self._focus_paused_remaining = remaining
            self._focus_deadline = None
            self.on_set_focus_mode(False)
        self._sync_focus_controls(math.ceil(self._focus_snapshot()[0]))

    def _finish_focus_timer(self, *, completed: bool) -> None:
        if self._focus_after_id is not None:
            try:
                self.root.after_cancel(self._focus_after_id)
            except tk.TclError:
                pass
        self._focus_after_id = None
        self._focus_deadline = None
        self._focus_paused_remaining = None
        curtain = getattr(self, "_focus_curtain", None)
        if curtain is not None:
            curtain.close()
            self._focus_curtain = None
        self.on_focus_curtain(False)
        self.on_set_focus_mode(False)
        self._sync_focus_controls()
        if completed:
            self.on_pet_action("happy")
            self._set_status("专注完成，雷姆正在提醒你休息")
            self._start_focus_reminder()
        else:
            self._set_status("专注计时已提前结束")

    def _start_focus_reminder(self) -> None:
        """在后台合成提醒语音，避免计时结束时冻结界面。"""

        mode = self.speech_mode.get()
        text = (
            "集中時間が終わりました。お疲れさまでした。少し休みましょう。"
            if mode == "ja"
            else "专注时间到了，辛苦了。休息一下吧。"
        )

        def worker() -> None:
            try:
                path = self.service.synthesize_notification(
                    text,
                    speech_mode=mode,
                )
            except Exception as error:
                self._enqueue_event(ChatEvent("focus-reminder-error", str(error)))
            else:
                self._enqueue_event(ChatEvent("focus-reminder-ready", path))

        threading.Thread(
            target=worker,
            name="rem-focus-reminder",
            daemon=True,
        ).start()
