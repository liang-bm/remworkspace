"""跟随宠物的 RemChat 气泡：输入、思考、回复以及最小显示控制。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from .platforms import native_module
import queue
import threading
import uuid
import tkinter as tk
from .ui.font_source import native_font
from tkinter import font as tkfont

from .audio_player import EphemeralAudioPlayer
from .config import AppConfig, load_config
from .dialogue import DialogueResult, DialogueService
from .session_telemetry import observe_request
from .pet_tracker import (
    BUBBLE_TAIL_ANCHOR_X,
    CodexPetTracker,
    WindowRect,
    compute_follow_position,
)
from .text_editing import bind_text_editing
from .ui.overlay_layout import (
    OverlayMetrics,
    fit_overlay_height,
    measure_text_height,
    work_area_at,
)

TRANSPARENT = "#010203"
PORCELAIN = "#F7FCFF"
ICE = "#EAF5FC"
ICE_ACTIVE = "#DCEFFA"
INK = "#17334D"
MUTED = "#67849B"
LINE = "#B9D3E4"
BLUE = "#3E82AE"
BLUE_DEEP = "#28658E"
ROSE = "#E99AAF"
SUCCESS = "#4D9F82"
ERROR = "#CE5B70"
SHADOW = "#9EB9CB"


def enable_dpi_awareness() -> None:
    """Apply native scaling before creating Tk, without native code in UI."""
    native_module("windowing").enable_dpi_awareness()


@dataclass(frozen=True)
class UIEvent:
    kind: str
    payload: object | None = None


def _rounded_rectangle(
    canvas: tk.Canvas,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    radius: int,
    **kwargs,
) -> int:
    """用平滑多边形绘制圆角气泡主体。"""

    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(
        points,
        smooth=True,
        splinesteps=32,
        **kwargs,
    )


class CompanionOverlay:
    """冰蓝瓷器感的一体式聊天气泡。"""

    def __init__(
        self,
        root: tk.Misc,
        config: AppConfig,
        *,
        anchor_provider: Callable[[], WindowRect | None] | None = None,
        tight_anchor: bool = False,
        on_close: Callable[[], None] | None = None,
        service: DialogueService | None = None,
        usage_ledger=None,
        speech_mode: tk.StringVar | None = None,
        audio_enabled: tk.BooleanVar | None = None,
        audio_player: EphemeralAudioPlayer | None = None,
        on_exchange: Callable[[str, str, DialogueResult], None] | None = None,
        on_pet_action: Callable[[str], None] | None = None,
        on_pet_reply: Callable[[str], None] | None = None,
        on_pet_thinking: Callable[[bool], None] | None = None,
        on_request_state: Callable[[bool, str], None] | None = None,
        on_collapsed: Callable[[bool], None] | None = None,
        on_request_error: Callable[[str], None] | None = None,
        on_request_started: Callable[[str], None] | None = None,
        on_reply_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self._owns_service = service is None
        self._owns_audio_player = audio_player is None
        self._closed = threading.Event()
        self._event_lock = threading.Lock()
        self._cancel_flag = threading.Event()
        self._drain_after_id: str | None = None
        self._follow_after_id: str | None = None
        # 桌面版把主聊天窗与悬浮窗接到同一个服务实例，避免重复加载模型，
        # 也保证一轮消息只写入一次记忆。独立运行 overlay 时仍可自行创建。
        self.service = service or DialogueService(config, log=self._service_log)
        self.usage_ledger = usage_ledger
        self.anchor_provider = anchor_provider
        self.tight_anchor = tight_anchor
        self.on_close = on_close
        self.on_exchange = on_exchange
        self.on_pet_action = on_pet_action
        self.on_pet_reply = on_pet_reply
        self.on_pet_thinking = on_pet_thinking
        self.on_request_state = on_request_state
        self.on_collapsed = on_collapsed
        self.on_request_error = on_request_error
        self.on_request_started = on_request_started
        self.on_reply_progress = on_reply_progress
        self.audio_player = audio_player or EphemeralAudioPlayer(
            log=self._service_log
        )
        self.tracker = CodexPetTracker()
        self.events: queue.Queue[UIEvent] = queue.Queue()
        self.busy = False
        self._own_request_active = False
        self._external_request_active = False
        self._last_reply = "输入想说的话。"
        self._thinking = False
        self.collapsed = False
        self._layout_pending: str | None = None
        self.metrics = OverlayMetrics()
        self._drag_origin: tuple[int, int] | None = None
        self._last_follow_position: tuple[int, int] | None = None
        self._last_pet_handle: int | None = None
        self._tracker_missing_logged = False
        self.last_audio_path: Path | None = None

        self.speech_mode = speech_mode or tk.StringVar(
            master=root, value=config.companion.default_speech_mode
        )
        self.follow_pet = tk.BooleanVar(
            master=root,
            value=config.companion.follow_pet,
        )
        self.audio_enabled = audio_enabled or tk.BooleanVar(
            master=root, value=config.companion.autoplay
        )

        self._expanded_width = config.companion.window_width
        self._preferred_height = config.companion.window_height
        self.width = self._expanded_width
        self.height = self._preferred_height
        self._configure_window()
        self._build_bubble()
        self._place_fallback()
        self._drain_after_id = self.root.after(70, self._drain_events)
        self._follow_after_id = self.root.after(
            config.companion.follow_interval_ms, self._follow_tick
        )
        # 创建/显示气泡只做 UI 工作。模型由 respond() 或语音提醒按需启动，
        # 否则仅打开桌面就会加载 Qwen/TTS，占用内存与显存。

    def _configure_window(self) -> None:
        root = self.root
        root.title("RemChat · 雷姆")
        root.geometry(f"{self.width}x{self.height}")
        root.resizable(False, False)
        root.overrideredirect(True)
        root.configure(bg=TRANSPARENT)
        root.attributes("-topmost", True)
        native_module("windowing").configure_overlay_transparency(root, TRANSPARENT)
        root.option_add("*Font", ("Microsoft YaHei UI", 11))
        root.update_idletasks()
        self._enable_windows_rounding()

    def _enable_windows_rounding(self) -> None:
        """Compatibility method; the selected native surface owns OS styling."""
        native_module("windowing").enable_overlay_rounding(self.root)

    def _build_bubble(self) -> None:
        """内容区保持简洁，只把收起和隐藏放在安静的顶部边缘。"""

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.place(x=0, y=0)
        # 回复区现在用于鼠标选字，窗口拖拽改由气泡四周和顶部露出的画布承担。
        # 这样既保留悬浮窗移动能力，也不会在复制回复时把整个窗口拖走。
        self._bind_drag(self.canvas)
        self.header = tk.Frame(self.root, bg=PORCELAIN)
        self._bind_drag(self.header)
        self.header_label = tk.Label(
            self.header,
            text="雷姆",
            bg=PORCELAIN,
            fg=MUTED,
            font=("Microsoft YaHei UI", 10),
        )
        self.header_label.pack(side="left")
        self._bind_drag(self.header_label)
        self.hide_button = tk.Button(
            self.header,
            text="×",
            command=self._hide_requested,
            bg=PORCELAIN,
            fg=MUTED,
            activebackground=ICE_ACTIVE,
            activeforeground=INK,
            relief="flat",
            bd=0,
            width=2,
            cursor="hand2",
            font=("Microsoft YaHei UI", 11),
            takefocus=True,
        )
        self.hide_button.pack(side="right")
        self.collapse_button = tk.Button(
            self.header,
            text="⌄",
            command=self.toggle_collapsed,
            bg=PORCELAIN,
            fg=MUTED,
            activebackground=ICE_ACTIVE,
            activeforeground=INK,
            relief="flat",
            bd=0,
            width=2,
            cursor="hand2",
            font=("Microsoft YaHei UI", 11),
            takefocus=True,
        )
        self.collapse_button.pack(side="right")
        self.reply_panel = tk.Frame(self.root, bg=PORCELAIN)
        self.reply_text = tk.Text(
            self.reply_panel,
            bg=PORCELAIN,
            fg=INK,
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            padx=0,
            pady=7,
            cursor="arrow",
            font=("Microsoft YaHei UI", 13),
            spacing1=1,
            spacing3=2,
        )
        self.reply_scrollbar = tk.Scrollbar(
            self.reply_panel,
            orient="vertical",
            command=self.reply_text.yview,
            width=11,
            bd=0,
            relief="flat",
            highlightthickness=0,
            troughcolor=PORCELAIN,
            bg=LINE,
            activebackground=BLUE,
        )
        self.reply_text.configure(yscrollcommand=self.reply_scrollbar.set)
        self.reply_text.pack(fill="both", expand=True)
        # 回复区应允许鼠标选中文字；不再把左键拖选劫持为窗口拖拽。
        # 气泡空白区域仍保留窗口拖拽行为。
        bind_text_editing(self.reply_text, read_only=True)

        self.input_shell = tk.Frame(
            self.root,
            bg=ICE,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.input_text = tk.Text(
            self.input_shell,
            bg=ICE,
            fg=INK,
            insertbackground=BLUE_DEEP,
            selectbackground="#C6E0F0",
            relief="flat",
            bd=0,
            wrap="word",
            padx=12,
            pady=9,
            height=2,
            font=("Microsoft YaHei UI", 12),
        )
        self.send_button = tk.Button(
            self.input_shell,
            text="↑",
            command=self._send,
            bg=BLUE_DEEP,
            fg="#FFFFFF",
            activebackground="#205779",
            activeforeground="#FFFFFF",
            disabledforeground="#D7E6EF",
            relief="flat",
            bd=0,
            padx=16,
            cursor="hand2",
            font=("Microsoft YaHei UI", 16),
        )
        # 固定按钮优先占据右侧；文本框只扩展到剩余区域。
        self.send_button.pack(side="right", fill="y")
        self.input_text.pack(side="left", fill="both", expand=True)
        from .ui.speech_input import SpeechBinding
        self.speech_input=SpeechBinding(self.input_shell,self.input_text,self.config.root,
            context=lambda:self.service.current_conversation_id,send_button=self.send_button,
            palette=dict(surface=PORCELAIN,wave=ICE,text=INK,accent=BLUE,button=BLUE_DEEP,on_button='#FFFFFF'))
        self.speech_input.button.configure(bg=PORCELAIN)
        self.speech_input.button.pack(side='right',before=self.input_text,padx=2)
        self.input_text.bind("<Return>", self._on_return)
        bind_text_editing(self.input_text)
        self.root.bind("<Escape>", lambda _event: self.set_collapsed(True))
        self._fit_control_metrics()
        self._place_contents()
        self._set_reply(self._last_reply)

    def _fit_control_metrics(self) -> None:
        """DPI 感知下按真实控件尺寸保留操作空间，不能让字体挤掉展开按钮。"""

        self.root.update_idletasks()
        metrics = self.metrics
        header_font = native_font(self.root,font=self.header_label.cget("font"))
        reply_font = native_font(self.root,font=self.reply_text.cget("font"))
        header_height = max(
            metrics.header_height,
            self.hide_button.winfo_reqheight() + 8,
            self.collapse_button.winfo_reqheight() + 8,
        )
        collapsed_width = max(
            metrics.collapsed_width,
            header_font.measure("思考中") + 8
            + self.hide_button.winfo_reqwidth()
            + self.collapse_button.winfo_reqwidth()
            + 2 * metrics.margin_x,
        )
        self.metrics = replace(
            metrics,
            header_height=header_height,
            reply_top=header_height + 6,
            input_height=max(metrics.input_height, self.input_text.winfo_reqheight() + 2),
            collapsed_width=collapsed_width,
            collapsed_height=max(metrics.collapsed_height, header_height + 23),
        )
        self._preferred_height = max(
            self._preferred_height,
            self.metrics.chrome_height + reply_font.metrics("linespace")
            + self.metrics.reply_padding,
        )

    def _draw_shell(self) -> None:
        """尺寸变化只重画外壳，不销毁 Text 的选区、滚动位置和输入。"""

        self.canvas.configure(width=self.width, height=self.height)
        self.canvas.delete("all")
        card_bottom = self.height - 23
        _rounded_rectangle(
            self.canvas, 5, 7, self.width - 6, card_bottom + 4, 20,
            fill=SHADOW, outline="",
        )
        _rounded_rectangle(
            self.canvas, 2, 2, self.width - 10, card_bottom, 20,
            fill=PORCELAIN, outline=LINE, width=1,
        )
        self.canvas.create_polygon(
            BUBBLE_TAIL_ANCHOR_X - 14, card_bottom - 1,
            BUBBLE_TAIL_ANCHOR_X, self.height - 3,
            BUBBLE_TAIL_ANCHOR_X + 14, card_bottom - 1,
            fill=PORCELAIN, outline=LINE,
        )

    def _place_contents(self) -> None:
        metrics = self.metrics
        self._update_header()
        self._draw_shell()
        self.header.place(
            x=metrics.margin_x, y=8,
            width=self.width - 2 * metrics.margin_x,
            height=metrics.header_height - 8,
        )
        if self.collapsed:
            self.reply_panel.place_forget()
            self.input_shell.place_forget()
            self.collapse_button.configure(text="⌃")
            return
        self.collapse_button.configure(text="⌄")
        self.reply_panel.place(
            x=metrics.margin_x, y=metrics.reply_top,
            width=self.width - 2 * metrics.margin_x - 4,
            height=max(1, self.height - metrics.chrome_height),
        )
        self.input_shell.place(
            x=metrics.margin_x,
            y=self.height - metrics.bottom_space - metrics.input_height,
            width=self.width - 2 * metrics.margin_x - 4,
            height=metrics.input_height,
        )

    def _schedule_layout(self) -> None:
        if self._closed.is_set():
            return
        if self._layout_pending is None:
            self._layout_pending = self.root.after_idle(self._fit_reply)

    def _fit_reply(self) -> None:
        """先按完整宽度排版，超过上限后加滚动条，再按最终宽度复测。"""

        self._layout_pending = None
        if self._closed.is_set() or self.collapsed or not self.root.winfo_exists():
            return
        self.reply_scrollbar.pack_forget()
        self.reply_text.pack_forget()
        self.reply_text.pack(side="left", fill="both", expand=True)
        self.root.update_idletasks()
        content_height = measure_text_height(self.reply_text) + self.metrics.reply_padding
        _pet, work, _handle = self._tracked_rect()
        height = fit_overlay_height(
            content_height,
            preferred_height=self._preferred_height,
            work_area_height=work.height,
            metrics=self.metrics,
        )
        reply_height = height - self.metrics.chrome_height
        if content_height > reply_height:
            self.reply_text.pack_forget()
            self.reply_scrollbar.pack(side="right", fill="y")
            self.reply_text.pack(side="left", fill="both", expand=True)
            self.root.update_idletasks()
            content_height = measure_text_height(self.reply_text) + self.metrics.reply_padding
            height = fit_overlay_height(
                content_height,
                preferred_height=self._preferred_height,
                work_area_height=work.height,
                metrics=self.metrics,
            )
        self._resize(self._expanded_width, height)

    def _resize(self, width: int, height: int) -> None:
        if (width, height) == (self.width, self.height):
            self._place_contents()
            return
        # 未跟随时也让气泡尾部留在原处，长回复往上展开而不是压到宠物上。
        y = self.root.winfo_y() + self.height - height
        self.width, self.height = width, height
        self.root.geometry(f"{width}x{height}")
        self._place_contents()
        if self.follow_pet.get():
            self._follow_once()
        else:
            screen_height = self.root.winfo_screenheight()
            y = min(max(self.metrics.screen_margin, y), max(0, screen_height - height))
            self.root.geometry(f"+{self.root.winfo_x()}+{y}")
            self._last_follow_position = (self.root.winfo_x(), y)

    def set_collapsed(self, collapsed: bool) -> None:
        """收起仅改变展示；后台回复、复制内容和语音生命周期继续正常。"""

        if self._closed.is_set() or self.collapsed == collapsed:
            return
        self.collapsed = collapsed
        if collapsed:
            self._resize(self.metrics.collapsed_width, self.metrics.collapsed_height)
        else:
            self._resize(self._expanded_width, self._preferred_height)
            self._schedule_layout()
        if self.on_collapsed is not None:
            self.on_collapsed(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.collapsed)

    def hide(self) -> None:
        if self._closed.is_set():
            return
        self.root.withdraw()

    def show(self) -> None:
        if self._closed.is_set():
            return
        self.root.deiconify()
        self.root.lift()
        self._follow_once()
        if not self.collapsed:
            self._schedule_layout()

    def _hide_requested(self) -> None:
        if self._closed.is_set():
            return
        if self.on_close is not None:
            self.on_close()
        else:
            self.hide()

    def _bind_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)

    def _set_reply(self, text: str) -> None:
        if self._closed.is_set():
            return
        self.reply_text.configure(state="normal")
        self.reply_text.delete("1.0", "end")
        self.reply_text.insert("1.0", text)
        self.reply_text.configure(state="disabled")
        self.reply_text.yview_moveto(0)
        if text != "雷姆思考中…":
            self._last_reply = text
        self._schedule_layout()

    def _update_header(self) -> None:
        if self.busy:
            phase = "思考中" if self._thinking else "回复中"
            text = phase if self.collapsed else f"雷姆 · {phase}"
        else:
            text = "雷姆"
        self.header_label.configure(text=text)

    def set_thinking(self, active: bool) -> None:
        """旧入口仍可用，主窗请求现在也会同步锁住气泡发送。"""
        self.set_request_state(active, source="main")

    def set_request_state(
        self,
        active: bool,
        *,
        source: str = "main",
        text: str | None = None,
    ) -> None:
        """由桌面协调器同步请求状态；setter 不回调，避免循环通知。"""

        if self._closed.is_set():
            return
        if source == "overlay":
            self._own_request_active = active
        else:
            self._external_request_active = active
        self.busy = self._own_request_active or self._external_request_active
        self.send_button.configure(state="disabled" if self.busy else "normal")
        self._thinking = self.busy
        self._update_header()
        if text is not None:
            self._set_reply(text)
        elif active:
            self._set_reply("雷姆思考中…")
        elif not self.busy and self.reply_text.get("1.0", "end-1c") == "雷姆思考中…":
            self._set_reply(self._last_reply)

    def show_partial_reply(self, text: str) -> None:
        """同步主窗正在显示的文字；不会播放音频或提前放开请求锁。"""
        if self._closed.is_set():
            return
        self._thinking = False
        self._update_header()
        self._set_reply(text)

    def show_error(self, message: str, *, source: str = "main") -> None:
        self.set_request_state(False, source=source)
        self._set_reply(f"暂时无法回复：{message}")

    def clear_reply(self) -> None:
        """会话切换后清空旧回复，避免主窗和气泡落在不同会话。"""
        self._last_reply = "输入想说的话。"
        if not self.busy:
            self._set_reply(self._last_reply)

    def _on_return(self, event) -> str | None:
        if event.state & 0x0001:
            return None
        self._send()
        return "break"

    def _send(self) -> None:
        if self._closed.is_set() or self.busy:
            return
        # 广场等第三个入口也会共享服务；不要把点击变成排队中的隐藏请求。
        if getattr(self.service, "is_busy", False):
            self._set_reply("雷姆正在完成另一项互动，请稍后再发送。")
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            self._set_reply("请输入内容。")
            return
        self.set_request_state(True, source="overlay")
        if self.on_request_state is not None:
            self.on_request_state(True, "overlay")
        if self.on_request_started is not None:
            self.on_request_started(text)
        if self.on_pet_thinking is not None:
            self.on_pet_thinking(True)
        elif self.on_pet_action is not None:
            self.on_pet_action("review")
        self.input_text.delete("1.0", "end")
        mode = self.speech_mode.get()
        audio_enabled = self.audio_enabled.get()
        # 销毁气泡不等于关闭共享服务；单独取消本入口的未完成一轮。
        self._cancel_flag = threading.Event()
        cancel_flag = self._cancel_flag
        metrics_session = str(getattr(self.service, "current_conversation_id", "overlay"))
        metrics_request = uuid.uuid4().hex
        usage_ledger = getattr(self, "usage_ledger", None)

        def worker() -> None:
            try:
                with observe_request(None, "", metrics_request, ledger=usage_ledger,
                                     metrics_session=metrics_session):
                    result = self.service.respond(
                        text,
                        speech_mode=mode,
                        synthesize_audio=audio_enabled,
                        cancel_event=cancel_flag,
                        on_text_ready=lambda reply, emotion: self._enqueue_event(
                            UIEvent("reply-preview", (reply, emotion))
                        ),
                    )
            except Exception as error:
                self._enqueue_event(UIEvent("reply-error", str(error)))
            else:
                self._enqueue_event(UIEvent("reply", (text, result)))

        threading.Thread(
            target=worker,
            name="remchat-dialogue",
            daemon=True,
        ).start()

    def _service_log(self, message: str) -> None:
        self._enqueue_event(UIEvent("service-log", message))

    @staticmethod
    def _discard_event_audio(event: UIEvent) -> None:
        """迟到回复只清理音频；不能在后台线程调用任何 Tk/宠物回调。"""
        if event.kind == "reply" and isinstance(event.payload, tuple):
            result = event.payload[1]
            if isinstance(result, DialogueResult) and result.audio_path is not None:
                try:
                    result.audio_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _enqueue_event(self, event: UIEvent) -> None:
        # 与 close 原子协调：检查关闭与入队之间不能遗留未播放 WAV。
        with self._event_lock:
            if self._closed.is_set():
                self._discard_event_audio(event)
            else:
                self.events.put(event)

    def close(self) -> None:
        """释放本窗口所有轮询；hide 仍只隐藏，close 不关闭共享服务。"""
        if self.speech_input.controller.owner is self.speech_input:self.speech_input.controller.cancel()
        with self._event_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._cancel_flag.set()
            try:
                while True:
                    self._discard_event_audio(self.events.get_nowait())
            except queue.Empty:
                pass
        for attribute in ("_drain_after_id", "_follow_after_id", "_layout_pending"):
            token = getattr(self, attribute)
            if token is not None:
                try:
                    self.root.after_cancel(token)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        self.busy = False
        self._own_request_active = False
        self._external_request_active = False
        if self._owns_service:
            self.service.close()
        if self._owns_audio_player:
            self.audio_player.stop()

    def _apply_reply(
        self,
        user_text: str,
        result: DialogueResult,
        *,
        play_audio: bool = True,
        notify: bool = True,
        finalize_request: bool = True,
    ) -> None:
        if self._closed.is_set():
            if play_audio and result.audio_path is not None:
                result.audio_path.unlink(missing_ok=True)
            return
        self._set_reply(result.reply_zh)
        self.last_audio_path = result.audio_path
        if play_audio and result.audio_path is not None:
            self.audio_player.play(result.audio_path)
        if finalize_request:
            if self.on_pet_thinking is not None:
                self.on_pet_thinking(False)
            if self.on_pet_reply is not None:
                self.on_pet_reply(result.emotion)
            elif self.on_pet_action is not None:
                self.on_pet_action("waiting")
            self.set_request_state(False, source="overlay")
            if self.on_request_state is not None:
                self.on_request_state(False, "overlay")
            self.input_text.focus_set()
        if notify and self.on_exchange is not None:
            self.on_exchange("overlay", user_text, result)

    def show_external_exchange(
        self,
        user_text: str,
        result: DialogueResult,
    ) -> None:
        """显示主聊天窗产生的一轮消息，不重复播放和回调。"""

        self._apply_reply(
            user_text,
            result,
            play_audio=False,
            notify=False,
            finalize_request=False,
        )
        self.set_request_state(False, source="main")

    def _drain_events(self) -> None:
        self._drain_after_id = None
        if self._closed.is_set():
            return
        try:
            while True:
                event = self.events.get_nowait()
                if event.kind == "reply":
                    user_text, result = event.payload  # type: ignore[misc]
                    self._apply_reply(user_text, result)
                elif event.kind == "reply-preview":
                    text, _emotion = event.payload  # type: ignore[misc]
                    self.show_partial_reply(text)
                    if self.on_reply_progress is not None:
                        self.on_reply_progress(text)
                    if self.on_pet_thinking is not None:
                        self.on_pet_thinking(False)
                    if self.on_pet_action is not None:
                        self.on_pet_action("waiting")
                elif event.kind == "reply-error":
                    if self.on_pet_thinking is not None:
                        self.on_pet_thinking(False)
                    if self.on_pet_action is not None:
                        self.on_pet_action("failed")
                    self.show_error(str(event.payload), source="overlay")
                    if self.on_request_state is not None:
                        self.on_request_state(False, "overlay")
                    if self.on_request_error is not None:
                        self.on_request_error(str(event.payload))
                elif event.kind == "service-log":
                    pass
        except queue.Empty:
            pass
        if not self._closed.is_set():
            self._drain_after_id = self.root.after(70, self._drain_events)

    def _place_fallback(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(16, screen_width - self.width - 34)
        y = max(16, screen_height - self.height - 86)
        self.root.geometry(f"+{x}+{y}")
        self._last_follow_position = (x, y)

    def _tracked_rect(self) -> tuple[WindowRect | None, WindowRect, int | None]:
        if self.anchor_provider is not None:
            rect = self.anchor_provider()
            work = WindowRect(
                0,
                0,
                self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(),
            )
            if rect is not None:
                work = work_area_at(rect, work)
            return rect, work, None
        candidate = self.tracker.find()
        if candidate is None:
            work = WindowRect(
                0,
                0,
                self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(),
            )
            return None, work, None
        work = self.tracker.work_area_for(candidate.handle)
        if work is None:
            work = WindowRect(
                0,
                0,
                self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(),
            )
        return candidate.rect, work, candidate.handle

    def _follow_tick(self) -> None:
        self._follow_after_id = None
        if self._closed.is_set() or not self.root.winfo_exists():
            return
        self._follow_once()
        self._follow_after_id = self.root.after(
            self.config.companion.follow_interval_ms,
            self._follow_tick,
        )

    def _follow_once(self) -> None:
        """布局变化与定时跟随复用同一个位置计算，尾部锚点保持一致。"""
        if self._closed.is_set():
            return
        if self.follow_pet.get():
            rect, work_area, handle = self._tracked_rect()
            if rect is not None:
                position = compute_follow_position(
                    rect,
                    self.width,
                    self.height,
                    work_area=work_area,
                    tight_pet_window=self.tight_anchor,
                )
                # 映射窗口时原生系统可能覆盖此前的位置请求。缓存只能说明
                # 已请求过；窗口可见后同时核对实际位置，避免首次显示错位。
                actual_position = (
                    (self.root.winfo_x(), self.root.winfo_y())
                    if self.root.winfo_ismapped() else None
                )
                if position != self._last_follow_position or (
                    actual_position is not None and actual_position != position
                ):
                    self.root.geometry(f"+{position[0]}+{position[1]}")
                    self._last_follow_position = position
                if handle is not None and handle != self._last_pet_handle:
                    print(
                        "[跟随] 已识别 Codex 宠物窗口 "
                        f"hwnd={handle}, rect={rect}",
                        flush=True,
                    )
                self._last_pet_handle = handle
                self._tracker_missing_logged = False
            elif not self._tracker_missing_logged:
                print(
                    "[跟随] 暂未识别宠物窗口，保持后备位置。",
                    flush=True,
                )
                self._tracker_missing_logged = True

    def _drag_start(self, event) -> None:
        self.follow_pet.set(False)
        self._drag_origin = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _drag_move(self, event) -> None:
        if self._drag_origin is None:
            return
        x = event.x_root - self._drag_origin[0]
        y = event.y_root - self._drag_origin[1]
        self.root.geometry(f"+{x}+{y}")
        self._last_follow_position = (x, y)

    def _close(self) -> None:
        self.audio_player.stop()
        if self.on_close is not None:
            self.on_close()
        else:
            self.root.destroy()


def acquire_single_instance(
    name: str = "Local\\RemCompanionUI",
) -> object | None:
    """Route instance ownership to the selected platform implementation."""
    return native_module("windowing").acquire_single_instance(name)


def main() -> None:
    mutex = acquire_single_instance()
    if mutex is None:
        return
    enable_dpi_awareness()
    config = load_config()
    root = tk.Tk()
    CompanionOverlay(root, config)
    root.mainloop()
    del mutex


if __name__ == "__main__":
    main()
