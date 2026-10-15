"""RemChat 主窗口：布局、会话操作与跨组件事件协调。"""

from __future__ import annotations

from collections.abc import Callable
import queue
import threading
import tkinter as tk
import uuid
from .ui.localized_dialogs import messagebox

from .audio_player import EphemeralAudioPlayer
from .chat_background import discover_background_files
from .dialogue import DialogueResult, DialogueService, RequestCancelled
from .pet_window import bundled_asset_path
from .text_editing import bind_text_editing
from .i18n import LANGUAGES, Translator, normalize_language
from .ui.locale_controller import LocaleController
from .ui.dpi import pixels, display_scale
from .integrations import IntegrationManager
from .session_telemetry import observe_request
from .ui.plaza_panel import PlazaPanel
from .ui.events import ChatEvent
from .ui.chat_settings import ChatSettingsMixin
from .ui.model_settings import ModelSettingsMixin
from .ui.chat_messages import ChatMessagesMixin
from .ui.chat_background_view import ChatBackgroundViewMixin
from .ui.scroll_surface import ScrollCanvas, CanvasBackdrop
from .ui.focus_timer import FocusTimerMixin
from .ui.composer_context import ComposerContextMixin
from .ui.external_exchange import ExternalExchange, ExternalExchangeMixin
from .ui.theme import (
    APP_BG, SIDEBAR, WHITE, INK, MUTED, SUBTLE, LINE, ACCENT,
    ACCENT_DEEP, ACCENT_SOFT, ROSE, SUCCESS, ERROR,
    COMPOSER_BG,
    ON_ACCENT,
)


ExchangeCallback = Callable[[str, str, DialogueResult], None]


class ChatWindow(
    ChatSettingsMixin, ModelSettingsMixin, ChatMessagesMixin,
    ChatBackgroundViewMixin, FocusTimerMixin, ComposerContextMixin,
    ExternalExchangeMixin,
):
    """完整会话历史、设置、输入和记忆状态所在的桌面窗口。"""

    def __init__(
        self,
        root: tk.Toplevel,
        service: DialogueService,
        *,
        speech_mode: tk.StringVar,
        audio_enabled: tk.BooleanVar,
        overlay_visible: tk.BooleanVar,
        pet_scale: tk.DoubleVar,
        background_fade: tk.DoubleVar,
        chat_font_size: tk.IntVar,
        audio_player: EphemeralAudioPlayer,
        on_exchange: ExchangeCallback,
        on_toggle_overlay: Callable[[], None],
        on_set_pet_scale: Callable[[float], None],
        on_set_background_fade: Callable[[int], None],
        on_set_chat_font_size: Callable[[int], None],
        on_set_focus_mode: Callable[[bool], None],
        on_pet_action: Callable[[str], None],
        on_pet_reply: Callable[[str], None],
        on_pet_thinking: Callable[[bool], None],
        on_exit: Callable[[], None],
        on_reply_progress: Callable[[str], None] | None = None,
        on_request_error: Callable[[str], None] | None = None,
        on_conversation_changed: Callable[[], None] | None = None,
        on_notification: Callable[[str], None] | None = None,
        on_passive_notification: Callable[[str], None] | None = None,
        on_request_state: Callable[[bool, str], None] | None = None,
        on_reload_actions: Callable[[], None] | None = None,
        on_pet_preview: Callable[[str], None] | None = None,
        theme_mode: str = "light",
        on_set_theme: Callable[[str], None] | None = None,
        ui_language: str = "zh-Hans",
        on_set_ui_language: Callable[[str], None] | None = None,
        task_rings_visible: tk.BooleanVar | None = None,
        on_set_task_rings_visible: Callable[[bool], None] | None = None,
        on_restore_task_rings: Callable[[], None] | None = None,
        on_inspect_task_rings: Callable[[], None] | None = None,
        on_focus_curtain: Callable[[bool], None] | None = None,
        on_keep_focus_pet_visible: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.service = service
        self.speech_mode = speech_mode
        self.audio_enabled = audio_enabled
        self.overlay_visible = overlay_visible
        self.task_rings_visible = task_rings_visible if task_rings_visible is not None else tk.BooleanVar(root, True)
        self.on_set_task_rings_visible = on_set_task_rings_visible or (lambda _visible: None)
        self.on_restore_task_rings = on_restore_task_rings or (lambda: None)
        self.on_inspect_task_rings = on_inspect_task_rings or (lambda: None)
        self.pet_scale = pet_scale
        self.background_fade = background_fade
        self.chat_font_size = chat_font_size
        self.audio_player = audio_player
        self.on_exchange = on_exchange
        self.on_toggle_overlay = on_toggle_overlay
        self.on_set_pet_scale = on_set_pet_scale
        self.on_set_background_fade = on_set_background_fade
        self.on_set_chat_font_size = on_set_chat_font_size
        self.on_set_focus_mode = on_set_focus_mode
        self.on_focus_curtain = on_focus_curtain or (lambda _active: None)
        self.on_keep_focus_pet_visible = on_keep_focus_pet_visible or (lambda: None)
        self.on_pet_action = on_pet_action
        self.on_pet_reply = on_pet_reply
        self.on_pet_thinking = on_pet_thinking
        self.on_exit = on_exit
        self.on_reply_progress = on_reply_progress
        self.on_request_error = on_request_error
        self.on_conversation_changed = on_conversation_changed
        self.on_notification = on_notification
        self.on_passive_notification = on_passive_notification or on_notification
        self.on_request_state = on_request_state
        self.on_reload_actions = on_reload_actions or (lambda: None)
        self.on_pet_preview = on_pet_preview or on_pet_action
        self.theme_mode = tk.StringVar(root, theme_mode if theme_mode in {"light", "dark"} else "light")
        self.on_set_theme = on_set_theme or (lambda _mode: None)
        self.ui_language = normalize_language(ui_language)
        self.translator = Translator(self.ui_language)
        self.on_set_ui_language = on_set_ui_language or (lambda _language: None)
        self.integration_manager = IntegrationManager(service.config.root)
        self.service.integration_context = self.integration_manager.context
        self.events: queue.Queue[ChatEvent] = queue.Queue()
        self._event_lock = threading.Lock()
        self.busy = False
        self._closing = False
        # One app-lifetime notification subscriber, independent of lazy project
        # creation. Scoped panels receive UI invalidations, never duplicate alerts.
        self._integration_unsubscribe = self.integration_manager.subscribe(self._queue_integration_notification)
        self._drain_after_id: str | None = None
        self._theme_after_id: str | None = None
        self.current_view = "chat"
        self._message_labels: list[tk.Text] = []
        self._conversation_buttons: list[tk.Button] = []
        self._context_conversation: tuple[int, str] | None = None
        self._mode_button_groups: list[dict[str, tk.Button]] = []
        self._scale_buttons: dict[float, tk.Button] = {}
        self._icon_image: tk.PhotoImage | None = None
        self._brand_image: tk.PhotoImage | None = None
        self._empty_state: tk.Frame | None = None
        self._focus_deadline: float | None = None
        self._focus_after_id: str | None = None
        self._focus_paused_remaining: float | None = None
        self._focus_total_seconds = 0
        self._focus_curtain = None
        self._background_paths = discover_background_files(
            self.service.config.root
        )
        self._background_index = 0
        self._background_photo: object | None = None
        self._background_render_key: tuple[object, ...] | None = None
        self._background_resize_after_id: str | None = None
        self._background_rotation_after_id: str | None = None
        self._background_fade_save_after_id: str | None = None
        self._initialize_background_album()
        self._thinking_message_widget: tk.Text | None = None
        self._cancel_flag = threading.Event()
        self._external_busy = False
        self._external_exchange: ExternalExchange | None = None
        self._request_running = False
        self._reply_text_ready = False
        self._following_latest = True

        self._configure_window()
        self._build_layout()
        self._bind_chat_font_shortcuts()
        self._sync_focus_controls()
        self._refresh_conversation_list()
        self._load_history()
        self._apply_option_styles()
        from .ui.theme_controller import ThemeController
        self._theme_controller = ThemeController(self.root)
        self._theme_controller.apply(self.theme_mode.get())
        self._locale_controller = LocaleController(self.root, self.translator)
        self._locale_controller.apply()
        self.action_panel.retranslate()
        self.memory_panel.retranslate()
        self._refresh_quick_models()
        self.speech_mode.trace_add("write", self._on_shared_option_changed)
        self.audio_enabled.trace_add("write", self._on_shared_option_changed)
        self.overlay_visible.trace_add("write", self._on_shared_option_changed)
        self.pet_scale.trace_add("write", self._on_shared_option_changed)
        self.chat_font_size.trace_add("write", self._on_chat_font_changed)
        self._drain_after_id = self.root.after(70, self._drain_events)
        self.input_text.focus_set()

    def _configure_window(self) -> None:
        self.root.title("RemChat")
        scale = display_scale(self.root)
        width = min(round(1280 * scale), round(self.root.winfo_screenwidth() * 0.90))
        height = min(round(800 * scale), round(self.root.winfo_screenheight() * 0.86))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(width, round(1000 * scale)), min(height, round(660 * scale)))
        self.root.configure(bg=APP_BG)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        icon_path = bundled_asset_path("icon.png")
        if icon_path.is_file():
            self._icon_image = tk.PhotoImage(master=self.root, file=str(icon_path))
            self.root.iconphoto(True, self._icon_image)

    def set_theme(self, mode: str) -> None:
        """即时换色，不重建会话、草稿或正在进行的任务。"""
        if mode not in {"light", "dark"} or self._closing:
            return
        self.theme_mode.set(mode)
        # Refresh imported tokens before stateful callbacks, then recolor the
        # existing widget tree once. Two full passes used to block the UI.
        from .ui import theme
        theme.set_mode(mode)
        self._apply_option_styles()
        self._theme_controller.apply(mode)
        self.countdown_days.draw()
        self._background_render_key = None
        self._render_chat_background(force=True)
        self.on_set_theme(mode)

    def set_ui_language(self, language: str) -> None:
        """Translate software labels only; keep all chat/request/data state."""
        if language not in LANGUAGES or self._closing:
            return
        self.ui_language = language
        self.translator.set_language(language)
        self.ui_language_label.set(LANGUAGES[language])
        self._locale_controller.apply()
        self.countdown_days.draw()
        self.action_panel.retranslate()
        self.memory_panel.retranslate()
        self._refresh_quick_models()
        self._resize_message_bubbles(max(1, self.message_canvas.winfo_width()))
        self.on_set_ui_language(language)

    def _retouch_theme(self) -> None:
        """让新增消息/控件也遵循当前主题，保留用户选区与输入。"""
        self._theme_after_id = None
        if not self._closing and hasattr(self, "_theme_controller"):
            self._theme_controller.apply(self.theme_mode.get())
        if not self._closing and hasattr(self, "_locale_controller"):
            self._locale_controller.apply()

    def _schedule_theme_retouch(self) -> None:
        if not self._closing and self._theme_after_id is None and hasattr(self, "_theme_controller"):
            self._theme_after_id = self.root.after_idle(self._retouch_theme)

    def _build_layout(self) -> None:
        self._build_sidebar()
        self.main = tk.Frame(self.root, bg=APP_BG)
        self.main.pack(side="left", fill="both", expand=True)
        self._build_header()
        self.content_host = tk.Frame(self.main, bg=APP_BG)
        self.content_host.pack(fill="both", expand=True)
        self._build_chat_view()
        self._build_settings_view()
        self._build_secondary_views()

    def _build_sidebar(self) -> None:
        self.sidebar = tk.Frame(
            self.root,
            bg=SIDEBAR,
            width=pixels(self.root, 244),
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=pixels(self.root, 12), pady=(22, 24))
        brand.columnconfigure(1, weight=1)
        if self._icon_image is not None:
            self._brand_image = self._icon_image.subsample(20, 20)
            tk.Label(
                brand,
                image=self._brand_image,
                bg=SIDEBAR,
                bd=0,
            ).grid(row=0, column=0, padx=(0, pixels(self.root, 6)))
        brand_text = tk.Frame(brand, bg=SIDEBAR)
        brand_text.grid(row=0, column=1, sticky="w")
        tk.Label(
            brand_text,
            text="RemChat",
            bg=SIDEBAR,
            fg=INK,
            anchor="w",
            font=("Yu Gothic UI Semibold", 16),
        ).pack(anchor="w")
        self.guide_button = tk.Button(brand, text=self.translator("指南"), command=self._show_guide,
                                      font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                                      padx=4, pady=6, cursor="hand2", takefocus=True)
        from .ui.theme_controller import set_color_roles
        set_color_roles(self.guide_button, background="SIDEBAR", foreground="ACCENT_DEEP",
                        activebackground="ACCENT_SOFT", activeforeground="ACCENT_DEEP")
        self.guide_button.grid(row=0, column=2, sticky="e", padx=(pixels(self.root, 4), 0))
        self.guide_button._remchat_ui_sources = {'text': '指南'}

        section = tk.Frame(self.sidebar, bg=SIDEBAR)
        section.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(
            section,
            text="聊天",
            bg=SIDEBAR,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")

        self.conversation_list = tk.Frame(self.sidebar, bg=SIDEBAR)
        self.conversation_list.pack(fill="both", expand=True, padx=10)
        self.conversation_menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=WHITE,
            fg=INK,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT_DEEP,
            bd=0,
            relief="flat",
            font=("Microsoft YaHei UI", 9),
        )
        self.conversation_menu.add_command(
            label="删除此对话",
            command=self._delete_context_conversation,
        )

        self.new_chat_button = tk.Button(
            self.sidebar,
            text="⊕  新对话",
            command=self._new_conversation,
            bg=ACCENT_SOFT,
            fg=ACCENT_DEEP,
            activebackground="#DCEFFA",
            activeforeground=ACCENT_DEEP,
            relief="flat",
            bd=0,
            pady=11,
            cursor="hand2",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.new_chat_button.pack(fill="x", padx=14, pady=(8, 10))

        self.settings_button = self._sidebar_action(
            "⚙  设置",
            self._show_settings,
        )
        self.settings_button.pack(fill="x", padx=10)
        self.plaza_button = self._sidebar_action("◇  功能广场", self._show_plaza)
        self.plaza_button.pack(fill="x", padx=10, before=self.settings_button)
        # This control must survive hiding every desktop ring. A ring's own
        # context menu or a settings card below the fold is not a recovery route.
        self.restore_rings_button = self._sidebar_action("↺  恢复跟踪环", self.on_restore_task_rings)
        self.restore_rings_button.pack(fill="x", padx=10, before=self.plaza_button)
        self._sidebar_action("⏻  退出应用", self.on_exit).pack(
            fill="x",
            padx=10,
            pady=(0, 12),
        )

    def _show_guide(self) -> None:
        window = getattr(self, "_guide_window", None)
        if window is not None and window.winfo_exists():
            window.refresh_guide(self.ui_language)
            window.deiconify()
            window.lift()
            return
        from .ui.documentation import GuideWindow
        self._guide_window = GuideWindow(self.root, tr=self.translator, language=self.ui_language,
                                        project_root=self.service.config.root)
        self._schedule_theme_retouch()

    def _sidebar_action(
        self,
        text: str,
        command: Callable[[], None],
    ) -> tk.Button:
        return tk.Button(
            self.sidebar,
            text=text,
            command=command,
            bg=SIDEBAR,
            fg="#596570",
            activebackground="#E8EBEE",
            activeforeground=INK,
            relief="flat",
            bd=0,
            anchor="w",
            padx=14,
            pady=9,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )

    def _build_header(self) -> None:
        header = tk.Frame(
            self.main,
            bg=WHITE,
            height=pixels(self.root, 76),
            highlightbackground=LINE,
            highlightthickness=1,
        )
        header.pack(fill="x")
        header.pack_propagate(False)

        self.focus_controls = tk.Frame(header, bg=WHITE)
        self.focus_controls.pack(side="left", fill="y", padx=24, pady=22)
        tk.Label(
            self.focus_controls,
            text="专注",
            bg=WHITE,
            fg=INK,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left", padx=(0, 8))
        self.focus_minutes = tk.StringVar(master=self.root, value="25")
        self.focus_minutes_input = tk.Spinbox(
            self.focus_controls,
            from_=1,
            to=180,
            textvariable=self.focus_minutes,
            width=4,
            justify="center",
            relief="flat",
            bd=0,
            highlightbackground=LINE,
            highlightthickness=1,
            font=("Microsoft YaHei UI", 9),
        )
        self.focus_minutes_input.pack(side="left")
        tk.Label(
            self.focus_controls,
            text="分钟",
            bg=WHITE,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(5, 8))
        self.focus_toggle_button = tk.Button(
            self.focus_controls,
            text="开始",
            command=self._toggle_focus_timer,
            bg=ACCENT_DEEP,
            fg=ON_ACCENT,
            activebackground=ACCENT,
            activeforeground=ON_ACCENT,
            relief="flat",
            bd=0,
            padx=11,
            pady=7,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.focus_toggle_button.pack(side="left")
        self.focus_status = tk.Label(
            self.focus_controls,
            text="",
            bg=WHITE,
            fg=ACCENT_DEEP,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.focus_status.pack(side="left", padx=(9, 0))

        self.settings_header_label = tk.Label(
            header,
            text="设置",
            bg=WHITE,
            fg=INK,
            font=("Microsoft YaHei UI", 15, "bold"),
        )

        self.header_controls = tk.Frame(header, bg=WHITE)
        self.header_controls.pack(side="right", padx=16, pady=17)
        self.overlay_button = tk.Button(
            self.header_controls,
            command=self._toggle_overlay,
            relief="flat",
            bd=0,
            padx=9,
            pady=7,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.overlay_button.pack(side="left", padx=(0, 7))
        self._build_mode_switch(self.header_controls).pack(
            side="left",
            padx=(0, 7),
        )
        self.audio_button = tk.Button(
            self.header_controls,
            command=self._toggle_audio,
            bg=WHITE,
            relief="flat",
            bd=0,
            padx=7,
            pady=7,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.audio_button.pack(side="left")

        from .ui.countdown_days import CountdownDaysChip
        self.countdown_days = CountdownDaysChip(header, self.service.config.root)
        self.countdown_days.pack(side="left", fill="both", expand=True,
                                 padx=pixels(header, 8), pady=pixels(header, 12))

    def _build_mode_switch(self, parent: tk.Widget) -> tk.Frame:
        shell = tk.Frame(parent, bg="#EEF1F4")
        group: dict[str, tk.Button] = {}
        for mode, label in (("zh", "中文"), ("ja", "日本語")):
            button = tk.Button(
                shell,
                text=label,
                command=lambda value=mode: self._set_mode(value),
                relief="flat",
                bd=0,
                padx=9,
                pady=7,
                cursor="hand2",
                font=("Microsoft YaHei UI", 9, "bold"),
            )
            button.pack(side="left")
            group[mode] = button
        self._mode_button_groups.append(group)
        return shell

    def _build_chat_view(self) -> None:
        self.chat_body = tk.Frame(self.content_host, bg=APP_BG)
        self.chat_body.pack(fill="both", expand=True)
        # 编辑器固定占据底部；消息区随后创建，只扩展到剩余空间。
        self._build_composer(self.chat_body)
        self._build_messages(self.chat_body)

    def _build_messages(self, parent: tk.Widget) -> None:
        shell = tk.Frame(parent, bg=APP_BG)
        shell.pack(fill="both", expand=True)
        self.message_canvas = ScrollCanvas(
            shell,
            bg=APP_BG,
            highlightthickness=0,
            bd=0,
        )
        scrollbar = tk.Scrollbar(
            shell,
            orient="vertical",
            command=self._scroll_messages,
        )
        self.message_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.message_canvas.pack(side="left", fill="both", expand=True)
        self.messages_frame = tk.Frame(self.message_canvas, bg=APP_BG)
        self.message_window = self.message_canvas.create_window(
            (0, 0),
            window=self.messages_frame,
            anchor="nw",
        )
        # Background and individual message rows share the Canvas, not one
        # native frame as tall as the entire conversation.
        self._background_window = self.message_canvas.create_image(0,0,anchor='nw')
        self.chat_background_label = CanvasBackdrop(self.message_canvas,self._background_window)
        self.message_canvas.tag_lower(self._background_window)
        self.chat_background_label.lower()
        self.messages_frame.bind(
            "<Configure>",
            self._on_messages_frame_configure,
        )
        self.message_canvas.bind("<Configure>", self._resize_message_area)
        self.message_canvas.bind("<Map>", self._resize_message_area, add=True)
        self.message_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.message_canvas.set_view_callback(self._commit_chat_viewport)
        self._schedule_background_rotation()

    def _build_composer(self, parent: tk.Widget) -> None:
        self.composer = tk.Frame(
            parent,
            bg=WHITE,
            height=pixels(self.root, 200),
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.composer.pack(side="bottom", fill="x")
        self.composer.pack_propagate(False)

        footer = tk.Frame(self.composer, bg=WHITE)
        footer.pack(side="bottom", fill="x", padx=24, pady=(0, 9))
        self.status_label = tk.Label(
            footer,
            text="Enter 发送 · Shift+Enter 换行",
            bg=WHITE,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        )
        self.status_label.pack(fill="x")
        self.status_label.configure(justify="left", wraplength=600)
        self.status_label.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(160, event.width)))
        self._build_context_toolbar()

        input_shell = tk.Frame(
            self.composer,
            bg=COMPOSER_BG,
            highlightbackground="#D7DDE2",
            highlightthickness=1,
        )
        input_shell.pack(fill="both", expand=True, padx=22, pady=(15, 8))
        self.send_button = tk.Button(
            input_shell,
            text="↑",
            command=self._send,
            bg=ACCENT_DEEP,
            fg=ON_ACCENT,
            activebackground=ACCENT_DEEP,
            activeforeground=ON_ACCENT,
            disabledforeground="#D5E4ED",
            relief="flat",
            bd=0,
            width=4,
            cursor="hand2",
            font=("Segoe UI", 14, "bold"),
        )
        self.send_button.pack(side="right", fill="y", padx=(8, 0))
        self.input_text = tk.Text(
            input_shell,
            bg=APP_BG,
            fg=INK,
            insertbackground=ACCENT_DEEP,
            selectbackground="#CCE7F8",
            relief="flat",
            bd=0,
            wrap="word",
            height=3,
            padx=14,
            pady=13,
            font=("Microsoft YaHei UI", self.chat_font_size.get()),
        )
        self.input_text.pack(side="left", fill="both", expand=True)
        from .ui.speech_input import SpeechBinding
        self.speech_input=SpeechBinding(input_shell,self.input_text,self.service.config.root,
            context=lambda:self.service.current_conversation_id,send_button=self.send_button)
        self.speech_input.button.pack(side='right',before=self.input_text,padx=(4,0))
        self.input_text.bind("<Return>", self._on_return)
        bind_text_editing(self.input_text)
        from .ui.composer_shortcuts import ComposerShortcuts
        self.composer_shortcuts = ComposerShortcuts(self)

        # 联网搜索按钮
        self.web_search_enabled = tk.BooleanVar(value=False)
        self.web_search_button = tk.Button(
            self.context_controls,
            text="🌐 联网搜索",
            command=self._toggle_web_search,
            bg="#EEF1F4",
            fg=MUTED,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT_DEEP,
            relief="flat",
            bd=0,
            padx=10,
            pady=2,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8),
        )
        self.web_search_button.pack(side="right", padx=(8, 0))

    def _refresh_conversation_list(self) -> None:
        for widget in self.conversation_list.winfo_children():
            widget.destroy()
        self._conversation_buttons.clear()
        current = self.service.current_conversation_id
        for conversation in self.service.memory.list_conversations(limit=9):
            # 设置属于一级导航；进入设置后，不再让某个会话同时呈现选中态。
            selected = (
                conversation.id == current and self.current_view == "chat"
            )
            title = conversation.title.strip() or "未命名对话"
            if len(title) > 14:
                title = f"{title[:14]}…"
            button = tk.Button(
                self.conversation_list,
                text=f"{'●' if selected else '○'}  {title}",
                command=lambda value=conversation.id: self._select_conversation(
                    value
                ),
                bg=ACCENT_SOFT if selected else SIDEBAR,
                fg=ACCENT_DEEP if selected else "#4B5762",
                activebackground="#E5EBEF",
                activeforeground=INK,
                relief="flat",
                bd=0,
                anchor="w",
                padx=13,
                pady=10,
                cursor="hand2",
                font=(
                    "Microsoft YaHei UI",
                    9,
                    "bold" if selected else "normal",
                ),
            )
            button.pack(fill="x", pady=2)
            # Conversation titles are user content, even if they match UI copy.
            button._remchat_locale_exclude = True
            button.bind(
                "<Button-3>",
                lambda event, value=conversation.id, name=title:
                    self._show_conversation_menu(event, value, name),
            )
            self._conversation_buttons.append(button)

    def _show_conversation_menu(
        self,
        event: tk.Event,
        conversation_id: int,
        title: str,
    ) -> str:
        """在鼠标位置打开只针对被右键会话的菜单。"""

        self._context_conversation = (conversation_id, title)
        try:
            self.conversation_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.conversation_menu.grab_release()
        return "break"

    def _delete_context_conversation(self) -> None:
        target = self._context_conversation
        self._context_conversation = None
        if target is None:
            return
        if self.busy or self.service.is_busy:
            self._set_status("当前回答完成后再删除对话", error=True)
            return

        conversation_id, title = target
        confirmed = messagebox.askyesno(
            "删除对话",
            f"确定删除“{title}”吗？\n\n"
            "该对话的聊天记录会永久删除，长期记忆不会受影响。",
            parent=self.root,
            icon="warning",
        )
        if not confirmed:
            return

        try:
            deleting_current = conversation_id == self.service.current_conversation_id
            self.service.delete_conversation(conversation_id)
        except ValueError:
            self._set_status("该对话已经不存在", error=True)
        else:
            if deleting_current:
                self._reset_composer_context()
            if self.on_conversation_changed is not None:
                self.on_conversation_changed()
            self._refresh_conversation_list()
            self._load_history()
            self._show_chat()
            self._set_status("对话已删除；长期记忆未改动")

    def _show_chat(self) -> None:
        if not self.plaza_body.flush_diary():return
        if self.current_view == "chat":
            return
        self.settings_body.pack_forget()
        self.plaza_body.pack_forget()
        self.plaza_body.pause_process()
        self.chat_body.pack(fill="both", expand=True)
        self.settings_header_label.pack_forget()
        self.focus_controls.pack(side="left", fill="y", padx=24, pady=22)
        self.header_controls.pack(side="right", padx=16, pady=17)
        self.countdown_days.pack(side="left", fill="both", expand=True,
                                 padx=pixels(self.root, 8), pady=pixels(self.root, 12))
        self.current_view = "chat"
        self.settings_button.configure(
            bg=SIDEBAR,
            fg="#596570",
            font=("Microsoft YaHei UI", 9),
        )
        self._refresh_conversation_list()
        self.input_text.focus_set()
        self._style_navigation()

    def _show_settings(self) -> None:
        if not self.plaza_body.flush_diary():return
        if self.current_view == "settings":
            return
        self.chat_body.pack_forget()
        self.plaza_body.pack_forget()
        self.plaza_body.pause_process()
        self.settings_body.pack(fill="both", expand=True)
        self.focus_controls.pack_forget()
        self.header_controls.pack_forget()
        self.countdown_days.pack_forget()
        self.settings_header_label.pack(side="left", padx=24)
        self.current_view = "settings"
        self.settings_button.configure(
            bg=ACCENT_SOFT,
            fg=ACCENT_DEEP,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self._refresh_conversation_list()
        self._style_navigation()
        self.memory_panel.refresh()

    def _build_secondary_views(self) -> None:
        self.plaza_body = PlazaPanel(
            self.content_host, self.service.config.root,
            self._send_plaza_prompt, self._notify,
            on_model_request=self._request_model_text,
            on_auto_notify=self._notify_passive,
            process_owner=self,
            can_notify=lambda: not (
                self.busy or self._closing or self.service.is_busy
                or self._focus_is_active()
                or self.audio_player.is_playing
            ),
        )

    def _show_plaza(self, page: str = "home", *, process_mode: str = "observe", section: str = "tasks") -> None:
        if not self.plaza_body.flush_diary():return
        if page == "process" and process_mode == "manage" and section != "tasks":
            self._show_integration_resources(section)
            return
        self.chat_body.pack_forget()
        self.settings_body.pack_forget()
        self.plaza_body.pack(fill="both", expand=True)
        self.focus_controls.pack_forget()
        self.header_controls.pack_forget()
        self.countdown_days.pack_forget()
        self.settings_header_label.configure(text="功能广场")
        self.settings_header_label.pack(side="left", padx=24)
        self.current_view = "plaza"
        self.plaza_body.show_page(page, process_mode=process_mode, section=section)
        self.plaza_body.record_activity()
        self.plaza_body.refresh()
        self._refresh_conversation_list()
        self._style_navigation()

    def _show_project_management(self, section: str = "tasks") -> None:
        """Preserve stable links while keeping external tasks out of Settings."""
        if section == "tasks":
            self._show_plaza("process", process_mode="manage", section="tasks")
        else:
            self._show_integration_resources(section)

    def _show_integration_resources(self, section: str = "usage") -> None:
        self._show_settings()
        self._show_settings_category("integrations")
        self.integration_panel.show_section(section)
        self.integration_panel.refresh()

    def _style_navigation(self) -> None:
        for key, button in (("plaza", self.plaza_button), ("settings", self.settings_button)):
            selected = self.current_view == key
            button.configure(bg=ACCENT_SOFT if selected else SIDEBAR,
                             fg=ACCENT_DEEP if selected else MUTED)
        self.settings_header_label.configure(text="设置" if self.current_view == "settings" else "功能广场")

    def _send_plaza_prompt(self, prompt: str) -> None:
        if self.busy or self.service.is_busy:
            self._notify("雷姆还在完成上一轮互动，稍后再聊棋局吧。")
            return
        self._show_chat()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", prompt)
        self._send()

    def _request_model_text(self, prompt: str, callback: Callable[[str], None], *, expected_provider=None,
                            on_error: Callable[[str], None] | None = None,
                            system_prompt: str | None = None) -> None:
        if self.busy or self.service.is_busy:
            if on_error is not None:
                on_error("模型正忙，请在当前回复完成后重试。")
            else:
                callback("")
                self._set_status("模型正忙，本次轻互动使用本地规则", error=True)
            return
        usage_ledger = self.integration_manager if self.integration_manager.usage_ledger is not None else None
        metrics_session = str(self.service.current_conversation_id)
        metrics_request = uuid.uuid4().hex
        def worker() -> None:
            try:
                with observe_request(None, "", metrics_request, ledger=usage_ledger,
                                     metrics_session=metrics_session):
                    options = {"system_prompt": system_prompt} if system_prompt is not None else {}
                    content = self.service.generate_text(prompt, expected_provider=expected_provider, **options)
            except Exception as error:
                self._enqueue_event(ChatEvent("model-callback", (callback, "", str(error), on_error)))
            else:
                self._enqueue_event(ChatEvent("model-callback", (callback, content, "")))
        threading.Thread(target=worker, name="remchat-light-interaction", daemon=True).start()

    def _on_integration_notification(self, event: dict) -> None:
        if event.get("type") != "task" or event.get("state") not in {"completed", "failed"}:
            return
        task = event.get("task", {})
        name = str(task.get("name", "外部任务"))[:80]
        if event["state"] == "completed":
            self._notify(f"{name}已经完成了，可以回去看看结果。")
        else:
            self._notify(f"{name}报告失败，请检查任务日志。")

    def _queue_integration_notification(self, event: dict) -> None:
        if (event.get("type") == "task" and event.get("state") in {"completed", "failed"}
                and event.get("transition", True)):
            self._enqueue_event(ChatEvent("integration-notification", event))

    def _notify(self, text: str) -> None:
        self._set_status(text)
        if self.on_notification is not None:
            self.on_notification(text)

    def _notify_passive(self, text: str) -> None:
        """Low-priority companionship, distinct from external task notices."""
        self._set_status(text)
        if self.on_passive_notification is not None:
            self.on_passive_notification(text)

    def close(self) -> None:
        """窗口销毁前释放监工、MCP、Hook 和活动计时器。"""
        if self.speech_input.controller.owner is self.speech_input:self.speech_input.controller.cancel()
        if self._closing:
            return
        if not self.plaza_body.flush_diary():return
        diary = getattr(self.plaza_body, "diary_panel", None)
        if diary is not None and not diary.close():
            return
        with self._event_lock:
            self._closing = True
            try:
                while True:
                    self._discard_closed_event(self.events.get_nowait())
            except queue.Empty:
                pass
        self._cancel_flag.set()
        if self._focus_is_active():
            self._finish_focus_timer(completed=False)
        self.countdown_days.close()
        background_worker=getattr(self,'_background_worker',None)
        if background_worker is not None:
            background_worker.close()
        self._integration_unsubscribe()
        shortcuts = getattr(self, "composer_shortcuts", None)
        if shortcuts is not None:
            shortcuts.close()
        if getattr(self, "project_process_panel", None) is not None:
            self.project_process_panel.close()
            self.project_process_panel = None
        if hasattr(self, "attachments"):
            self.attachments.clear()
        # Drop transient key drafts when the app closes; these are never persisted.
        if hasattr(self, "_provider_drafts"):
            self._provider_drafts.clear()
            self.provider_key_var.set("")
        if hasattr(self, "_locale_controller"):
            self._locale_controller.close()
        for name in ("_drain_after_id", "_focus_after_id", "_theme_after_id", "_background_resize_after_id",
                     "_background_rotation_after_id", "_background_fade_save_after_id", "_bubble_paint_pending",
                     "_message_resize_after_id", "_background_poll_after_id"):
            handle = getattr(self, name, None)
            if handle is not None:
                try:
                    self.root.after_cancel(handle)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        self._focus_deadline = None
        popup = getattr(self, "_attachment_menu", None)
        if popup is not None and popup.winfo_exists():
            popup.close(restore=False)
        if hasattr(self.plaza_body, "pixel_farm"):
            self.plaza_body.pixel_farm._stop_walk()
        self.plaza_body.stop_companion()
        if self.plaza_body.project_workspace is not None:
            self.plaza_body.project_workspace.close()
        self.integration_panel.close()
        self.integration_manager.close()

    @staticmethod
    def _discard_closed_event(event: ChatEvent) -> None:
        try:
            if event.kind in {"reply", "cancelled"} and isinstance(event.payload, tuple):
                result = event.payload[1]
                if isinstance(result, DialogueResult) and result.audio_path is not None:
                    result.audio_path.unlink(missing_ok=True)
            elif event.kind == "focus-reminder-ready" and event.payload is not None:
                event.payload.unlink(missing_ok=True)
        except OSError as error:
            # 锁定的临时 WAV 可由下次启动清理，不应阻断整个退出流程。
            print(f"[退出] 迟到语音暂未删除: {error}")

    def _enqueue_event(self, event: ChatEvent) -> None:
        """关闭与 worker 交付原子协调；迟到语音只清理、不回调界面。"""
        with self._event_lock:
            if self._closing:
                self._discard_closed_event(event)
            else:
                self.events.put(event)

    def _new_conversation(self) -> None:
        if self.busy or self.service.is_busy:
            self._set_status("当前回答完成后再新建对话", error=True)
            return
        if self.speech_input.controller.owner:self.speech_input.controller.cancel()
        self.service.start_new_conversation()
        self._reset_composer_context()
        if self.on_conversation_changed is not None:
            self.on_conversation_changed()
        self._refresh_conversation_list()
        self._load_history()
        self._show_chat()
        self._set_status("已新建对话；长期记忆会继续保留")

    def _select_conversation(self, conversation_id: int) -> None:
        if self.busy or self.service.is_busy or conversation_id == self.service.current_conversation_id:
            self._show_chat()
            return
        if self.speech_input.controller.owner:self.speech_input.controller.cancel()
        self.service.select_conversation(conversation_id)
        self._reset_composer_context()
        if self.on_conversation_changed is not None:
            self.on_conversation_changed()
        self._refresh_conversation_list()
        self._load_history()
        self._show_chat()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.configure(text=self.translator.display(text), fg=ERROR if error else MUTED)

    def _update_stats(
        self,
        *,
        message_count: int | None = None,
        memory_count: int | None = None,
    ) -> None:
        if message_count is None or memory_count is None:
            stats = self.service.memory.stats()
            message_count = stats.message_count
            memory_count = stats.memory_count
        if hasattr(self, "memory_status"):
            self.memory_status.configure(
                text=f"当前 {memory_count} 条长期记忆",
                fg=MUTED,
            )

    def _toggle_overlay(self) -> None:
        self.on_toggle_overlay()
        self.root.after_idle(self._apply_option_styles)

    def _set_mode(self, mode: str) -> None:
        if mode not in self.service.config.tts_profiles:
            return
        self.speech_mode.set(mode)
        self._set_status("中文发音" if mode == "zh" else "日语发音 · 中文字幕")

    def _toggle_audio(self) -> None:
        self.audio_enabled.set(not self.audio_enabled.get())
        if not self.audio_enabled.get():
            self.audio_player.stop()
        self._set_status(
            "自动语音已开启" if self.audio_enabled.get() else "自动语音已关闭"
        )

    def _apply_option_styles(self) -> None:
        visible = self.overlay_visible.get()
        overlay_text = "悬浮窗 开" if visible else "悬浮窗 关"
        self.overlay_button.configure(
            text=self.translator(overlay_text),
            bg=ACCENT_SOFT if visible else "#EEF1F4",
            fg=ACCENT_DEEP if visible else MUTED,
            activebackground="#DCEFFA",
            activeforeground=ACCENT_DEEP,
        )

        selected = self.speech_mode.get()
        for group in self._mode_button_groups:
            for mode, button in group.items():
                active = mode == selected
                button.configure(
                    bg=ACCENT_DEEP if active else "#EEF1F4",
                    fg=ON_ACCENT if active else MUTED,
                    activebackground=ACCENT if active else "#E2E7EA",
                    activeforeground=ON_ACCENT if active else INK,
                )

        enabled = self.audio_enabled.get()
        audio_text = "♪ 播放开" if enabled else "♪ 播放关"
        for button in (self.audio_button,):
            button.configure(
                text=self.translator(audio_text),
                bg="#FFF1F5" if enabled else "#EEF1F4",
                fg=ROSE if enabled else MUTED,
                activebackground="#FBE4EB",
                activeforeground=ROSE,
            )
        selected_scale = self.pet_scale.get()
        for scale, button in self._scale_buttons.items():
            active = scale == selected_scale
            button.configure(
                bg=ACCENT_DEEP if active else "#EEF2F5",
                fg=ON_ACCENT if active else INK,
                activebackground=ACCENT if active else "#E3E9ED",
                activeforeground=ON_ACCENT if active else INK,
            )

    def _on_shared_option_changed(self, *_args: object) -> None:
        self._apply_option_styles()
        self._retouch_theme()

    def _toggle_web_search(self) -> None:
        """切换联网搜索开关。"""
        current = self.web_search_enabled.get()
        self.web_search_enabled.set(not current)
        enabled = self.web_search_enabled.get()
        button_text = "🌐 联网搜索开" if enabled else "🌐 联网搜索关"
        self.web_search_button.configure(
            text=button_text,
            bg=ACCENT_SOFT if enabled else "#EEF1F4",
            fg=ACCENT_DEEP if enabled else MUTED,
            activebackground="#DCEFFA",
            activeforeground=ACCENT_DEEP,
        )
        self._set_status("联网搜索已开启" if enabled else "联网搜索已关闭")
        self._retouch_theme()

    def _on_return(self, event) -> str | None:
        if event.state & 0x0001:
            return None
        # Refresh synchronously on Enter: the debounced last keystroke may not
        # have rendered yet. Selecting a completion must never also send.
        from .composer_tokens import active_token
        current_token = active_token(self.input_text.get("1.0", "end-1c"),
                                     len(self.input_text.get("1.0", "insert")))
        if self.composer_shortcuts.popup is None or current_token != self.composer_shortcuts.token:
            self.composer_shortcuts.refresh()
        if self.composer_shortcuts.accept():
            return "break"
        self.composer_shortcuts.hide()
        self._send()
        return "break"

    def _send(self) -> None:
        if self.busy or self.service.is_busy:
            self._set_status("另一处互动正在进行，请稍等", error=True)
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            self._set_status("请先输入内容", error=True)
            return
        if self.composer_shortcuts.run_local(text):
            return
        original_context = self._context_snapshot()
        provider_snapshot = self.service.provider_repository.load().active_provider
        try:
            from .composer_tokens import prepare_references
            snapshot = prepare_references(
                text, original_context, workspace=self.workspace_directory,
                memory=self.service.memory, skills=self.integration_manager.skills,
                root_path=self.service.config.root,
            )
            snapshot, request_mode_snapshot = self._capture_composer_request(snapshot, provider_snapshot)
            snapshot.validate(supports_images=provider_snapshot.supports_images)
        except (OSError, ValueError, KeyError) as error:
            self._set_status(str(error), error=True)
            return
        # Reference-only snapshots aren't toolbar attachments. Remember the
        # original selection so successful delivery still clears it correctly.
        self._sent_context = original_context
        self.composer_shortcuts.hide()
        self.busy = True
        self._refresh_composer_mode()
        self._record_project_request(text, request_mode_snapshot)
        # 每轮新建标记，不能清空旧请求正在观察的 Event。
        self._cancel_flag = threading.Event()
        self._request_running = True
        self._reply_text_ready = False
        self._following_latest = True
        self.on_pet_thinking(True)
        if self.on_request_state is not None:
            self.on_request_state(True, "main")
        self.input_text.delete("1.0", "end")
        self._add_message("user", text)
        self._thinking_message_widget = self._add_message(
            "assistant",
            "雷姆思考中…",
        )
        self.send_button.configure(
            state="normal",
            text="✕",
            bg=ERROR,
            activebackground="#B84460",
            command=self._cancel_respond,
        )
        self._set_status("雷姆正在思考…")
        mode = self.speech_mode.get()
        use_audio = self.audio_enabled.get()
        use_web_search = self.web_search_enabled.get()
        cancel_flag = self._cancel_flag
        trace = self.integration_manager.project_trace
        observed = trace.snapshot()
        observation_session = observed["session_id"] if observed["enabled"] else ""
        observation_request = uuid.uuid4().hex
        metrics_session = str(self.service.current_conversation_id)
        usage_ledger = self.integration_manager if self.integration_manager.usage_ledger is not None else None

        def worker() -> None:
            try:
                with observe_request(trace, observation_session, observation_request,
                                     ledger=usage_ledger, metrics_session=metrics_session):
                    result = self.service.respond(
                        text,
                        speech_mode=mode,
                        synthesize_audio=use_audio,
                        use_web_search=use_web_search,
                        context_snapshot=snapshot,
                        expected_provider=provider_snapshot,
                        cancel_event=cancel_flag,
                        on_text_ready=lambda reply, emotion: self._enqueue_event(
                            ChatEvent("reply-preview", (reply, emotion))
                        ),
                    )
            except RequestCancelled:
                self._enqueue_event(ChatEvent("cancelled"))
            except Exception as error:
                self._enqueue_event(ChatEvent("cancelled" if cancel_flag.is_set() else "error", str(error)))
            else:
                self._enqueue_event(ChatEvent("cancelled" if cancel_flag.is_set() else "reply", (text, result)))

        threading.Thread(
            target=worker,
            name="rem-main-chat",
            daemon=True,
        ).start()

    def _cancel_respond(self) -> None:
        """停止结果落盘/播放；等待网络线程退出后再允许下一轮。"""
        if not self.busy:
            return
        self._cancel_flag.set()
        self.audio_player.stop()
        widget = self._thinking_message_widget
        self._thinking_message_widget = None
        if widget is not None:
            try:
                widget.master.destroy()   # 销毁气泡所在的 container
            except tk.TclError:
                pass
        self.send_button.configure(state="disabled", text="…")
        self._set_status("已停止本轮，正在等待接口释放…")
        self.input_text.focus_set()

    def _restore_send_button(self) -> None:
        """把发送按钮恢复到正常的发送状态。"""
        try:
            self.send_button.configure(
                state="normal",
                text="↑",
                bg=ACCENT_DEEP,
                activebackground=ACCENT_DEEP,
                command=self._send,
            )
        except tk.TclError:
            pass
        self._refresh_composer_mode()

    def _apply_reply(self, user_text: str, result: DialogueResult) -> None:
        widget = getattr(self, "_thinking_message_widget", None)
        self._thinking_message_widget = None

        def finish_reply() -> None:
            self.on_pet_thinking(False)
            if self.on_request_state is not None:
                self.on_request_state(False, "main")
            self.on_pet_reply(result.emotion)
            self._update_stats(
                message_count=result.message_count,
                memory_count=result.memory_count,
            )
            self._refresh_conversation_list()
            if result.audio_path is not None:
                self.audio_player.play(result.audio_path)
            if result.audio_error:
                self._set_status("回答完成 · 本轮语音已跳过", error=True)
            elif result.memories_added:
                self._set_status(f"已新增 {result.memories_added} 条长期记忆")
            else:
                self._set_status("回答完成")
            self.busy = False
            self._request_running = False
            self._restore_send_button()
            self._commit_composer_context()
            self.input_text.focus_set()
            self.on_exchange("main", user_text, result)

        if self._reply_text_ready and widget is not None and widget.winfo_exists():
            finish_reply()
        elif widget is not None and widget.winfo_exists():
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", result.reply_zh)
            widget.configure(state="disabled")
            self._resize_single_message_bubble(widget, result.reply_zh, self.message_canvas.winfo_width())
            self._update_messages_layout_and_scroll()
            if self.on_reply_progress is not None:
                self.on_reply_progress(result.reply_zh)
            finish_reply()
        else:
            self._add_message("assistant", result.reply_zh)
            finish_reply()

    def set_request_state(self, active: bool, source: str = "overlay") -> None:
        """另一入口占用同一服务时，主窗口禁发并禁止切换会话。"""

        self._external_busy = active
        self.busy = active or self._request_running
        self._refresh_composer_mode()
        if active:
            self.send_button.configure(state="disabled", text="…")
            self._set_status("雷姆正在回复悬浮框…")
        elif not self._request_running:
            self._restore_send_button()
            self._set_status("Enter 发送 · Shift+Enter 换行")

    def _apply_preview(self, text: str, emotion: str) -> None:
        if self._cancel_flag.is_set():
            return
        widget = self._thinking_message_widget
        if widget is None or not widget.winfo_exists():
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        self._reply_text_ready = True
        self._resize_single_message_bubble(widget, text, self.message_canvas.winfo_width())
        self._update_messages_layout_and_scroll()
        if self.on_reply_progress is not None:
            self.on_reply_progress(text)
        self.on_pet_thinking(False)
        self.on_pet_action("waiting")
        self._set_status("回复已生成 · 正在准备语音…" if self.audio_enabled.get() else "回复已生成")

    def _drain_events(self) -> None:
        self._drain_after_id = None
        if self._closing:
            return
        had_events = False
        try:
            while True:
                event = self.events.get_nowait()
                had_events = True
                if event.kind == "reply":
                    user_text, result = event.payload  # type: ignore[misc]
                    self._apply_reply(user_text, result)
                elif event.kind == "integration-notification":
                    self._on_integration_notification(event.payload)
                elif event.kind == "model-callback":
                    callback, content, error, *error_handlers = event.payload
                    error_handler = error_handlers[0] if error_handlers else None
                    if error and error_handler is not None:
                        error_handler(error)
                    else:
                        callback(content)
                    if error:
                        self._set_status(f"轻互动模型调用失败：{error}", error=True)
                elif event.kind == "reply-preview":
                    self._apply_preview(*event.payload)
                elif event.kind == "cancelled":
                    self._request_running = False
                    self.busy = self._external_busy
                    self.on_pet_thinking(False)
                    if self.on_request_state is not None:
                        self.on_request_state(False, "main")
                    self._restore_send_button()
                    late_result = event.payload[1] if isinstance(event.payload, tuple) else None
                    if isinstance(late_result, DialogueResult):
                        if late_result.audio_path is not None:
                            late_result.audio_path.unlink(missing_ok=True)
                        self._set_status("回复已生成并保存；已停止显示和语音")
                    else:
                        self._set_status("本轮已停止，未保存回复或记忆")
                    if self.on_request_error is not None:
                        self.on_request_error("本轮已停止")
                elif event.kind == "error":
                    widget = getattr(self, "_thinking_message_widget", None)
                    self._thinking_message_widget = None
                    if widget is not None and widget.winfo_exists():
                        try:
                            widget.configure(state="normal")
                            widget.delete("1.0", "end")
                            widget.insert("1.0", f"回答失败：{event.payload}")
                            widget.configure(state="disabled")
                            self._resize_single_message_bubble(
                                widget,
                                f"回答失败：{event.payload}",
                                max(1, self.message_canvas.winfo_width()),
                            )
                        except tk.TclError:
                            pass
                    self.on_pet_thinking(False)
                    if self.on_request_state is not None:
                        self.on_request_state(False, "main")
                    self.on_pet_action("failed")
                    self.busy = False
                    self._request_running = False
                    self._restore_send_button()
                    self._set_status(str(event.payload)[:80], error=True)
                    if self.on_request_error is not None:
                        self.on_request_error(str(event.payload))
                elif event.kind == "cache-stats":
                    stats = event.payload
                    self.cache_status.configure(
                        text=(
                            f"可清理 {self._format_bytes(stats.bytes)}"
                            f" · {stats.files} 个文件"
                        ),
                        fg=MUTED,
                    )
                elif event.kind == "cache-cleared":
                    stats = event.payload
                    self.cache_status.configure(
                        text=(
                            f"已清理 {self._format_bytes(stats.bytes)}"
                            f" · {stats.files} 个文件"
                        ),
                        fg=SUCCESS,
                    )
                elif event.kind == "cache-error":
                    self.cache_status.configure(
                        text=str(event.payload)[:80],
                        fg=ERROR,
                    )
                elif event.kind == "provider-models":
                    self._accept_provider_models(event.payload)
                elif event.kind == "provider-test-result":
                    self._accept_provider_test(event.payload)
                elif event.kind == "provider-test-ok":
                    self.provider_test_button.configure(
                        state="normal",
                        text="测试连接",
                    )
                    self.provider_status.configure(
                        text=f"{event.payload} 连接成功",
                        fg=SUCCESS,
                    )
                elif event.kind == "provider-test-error":
                    self.provider_test_button.configure(
                        state="normal",
                        text="测试连接",
                    )
                    self.provider_status.configure(
                        text=f"连接失败：{str(event.payload)[:180]}",
                        fg=ERROR,
                    )
                elif event.kind == "provider-download-log":
                    self.provider_status.configure(
                        text=str(event.payload)[-180:],
                        fg=MUTED,
                    )
                elif event.kind == "provider-download-ok":
                    self.provider_download_button.configure(
                        state="normal",
                        text="一键安装本地模型",
                    )
                    self.provider_status.configure(
                        text="本地模型与 llama.cpp 已安装完成",
                        fg=SUCCESS,
                    )
                elif event.kind == "provider-download-error":
                    self.provider_download_button.configure(
                        state="normal",
                        text="一键安装本地模型",
                    )
                    self.provider_status.configure(
                        text=f"安装失败：{str(event.payload)[:180]}",
                        fg=ERROR,
                    )
                elif event.kind == "focus-reminder-ready":
                    self.audio_player.play(event.payload)  # type: ignore[arg-type]
                    self._set_status("专注完成，休息一下吧")
                elif event.kind == "focus-reminder-error":
                    self._set_status(
                        "专注完成，但语音提醒生成失败",
                        error=True,
                    )
        except queue.Empty:
            pass
        if had_events and hasattr(self, "_locale_controller"):
            # Network/status ticks do not need to retranslate thousands of
            # unchanged controls. Newly mapped widgets enrol independently.
            for name in ('provider_status', 'cache_status', 'memory_status'):
                widget = getattr(self, name, None)
                if widget is not None and widget.winfo_exists():
                    self._locale_controller.schedule_widget(widget)
        try:
            self._drain_after_id = self.root.after(70, self._drain_events)
        except tk.TclError:
            pass

    def activate(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(260, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.root.focus_force()
        if self.current_view == "chat":
            self.input_text.focus_set()

    def hide(self) -> None:
        if not self.plaza_body.flush_diary():return
        self.root.withdraw()
