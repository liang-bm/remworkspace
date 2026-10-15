"""ChatSettingsMixin：ChatWindow 的单一界面职责；状态由宿主窗口持有。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading
import tkinter as tk
from .localized_dialogs import messagebox

from ..companion_preferences import (
    INSTRUCTION_FILE, MEMORY_EDITOR_FILE, PET_SCALES, clear_safe_cache,
    open_in_vscode, scan_safe_cache,
)
from ..pet_window import ACTION_LABELS
from ..text_editing import bind_text_editing
from .events import ChatEvent
from .memory_panel import MemoryPanel
from .action_panel import ActionPanel
from .dpi import pixels
from .general_settings import build_general_settings
from .instruction_panel import InstructionPanel
from .integrations_panel import IntegrationPanel
from .settings_card import SettingsCard
from .theme_controller import set_color_roles
from .combobox_popup import AnchoredCombobox
from .theme import (
    APP_BG, SIDEBAR, WHITE, INK, MUTED, SUBTLE, LINE, ACCENT,
    ACCENT_DEEP, ACCENT_SOFT, ACCENT_HOVER, ROSE, SUCCESS, ERROR,
    ON_ACCENT,
)


class ChatSettingsMixin:
    """仅供 ChatWindow 组合使用；不持有独立模型、数据库或请求线程。"""

    @staticmethod
    def _credential_storage_description() -> str:
        from ..platforms import native_module
        return native_module("secrets").storage_description()

    def _build_settings_view(self) -> None:
        self.settings_body = tk.Frame(self.content_host, bg=APP_BG)
        self.settings_category = "models"
        self._settings_pages: dict[str, tk.Frame] = {}
        self._settings_canvases: dict[str, tk.Canvas] = {}
        self._settings_nav_buttons: dict[str, tk.Button] = {}
        self._settings_nav_surfaces: dict[str, SettingsCard] = {}
        self._settings_visible_category: str | None = None

        rail = self.settings_rail = tk.Frame(
            self.settings_body,
            bg=SIDEBAR,
            width=pixels(self.root, 184),
            bd=0,
        )
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        tk.Label(
            rail,
            text="设置",
            bg=SIDEBAR,
            fg=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(fill="x", padx=pixels(self.root, 24), pady=(pixels(self.root, 24), pixels(self.root, 10)))
        navigation = (
            ("常规", (("general", "通用"), ("appearance", "外观与动作"))),
            ("功能", (("models", "模型与 API"), ("asr", "语音输入"), ("conversation", "对话与人格"),
                      ("actions", "动作工作台"), ("integrations", "接入资源"))),
            ("数据与存储", (("storage", "记忆与存储"),)),
        )
        self._settings_categories = {key: label for _group, items in navigation for key, label in items}
        self.settings_category_choice = tk.StringVar(self.root, self._settings_categories['models'])
        self.settings_compact_nav = tk.Frame(self.settings_body, bg=APP_BG)
        choice = AnchoredCombobox(self.settings_compact_nav, state='readonly',
                                  values=tuple(self._settings_categories.values()),
                                  textvariable=self.settings_category_choice,
                                  font=('Microsoft YaHei UI', 10), width=1)
        choice.pack(fill='x', padx=pixels(self.root,16), pady=pixels(self.root,8))
        self.settings_category_box = choice
        choice._remchat_ui_choice_labels = tuple(self._settings_categories.values())
        choice.bind('<<ComboboxSelected>>', lambda _event: self._show_settings_category(
            tuple(self._settings_categories)[choice.current()]))
        for group, items in navigation:
            heading = tk.Frame(rail, bg=SIDEBAR)
            heading.pack(fill="x", padx=pixels(self.root, 24),
                         pady=(pixels(self.root, 18), pixels(self.root, 7)))
            tk.Label(heading, text=group, bg=SIDEBAR, fg=SUBTLE, anchor="w",
                     font=("Microsoft YaHei UI", 8), cursor="arrow",
                     takefocus=False).pack(side="left")
            tk.Frame(heading, bg=LINE, height=1).pack(
                side="left", fill="x", expand=True, padx=(pixels(self.root, 10), 0),
            )
            for key, label in items:
                surface = SettingsCard(rail, surface_role="SIDEBAR", background_role="SIDEBAR",
                                       shadow=False, inset=4, radius=12)
                surface.pack(fill="x", padx=pixels(self.root, 10), pady=pixels(self.root, 2))
                button = tk.Button(
                    surface.body,
                    text=label,
                    command=lambda value=key: self._show_settings_category(value),
                    bg=SIDEBAR,
                    fg=MUTED,
                    activebackground=ACCENT_SOFT,
                    activeforeground=ACCENT_DEEP,
                    relief="flat",
                    bd=0,
                    anchor="w",
                    padx=pixels(self.root, 12),
                    pady=pixels(self.root, 9),
                    highlightthickness=1,
                    highlightbackground=SIDEBAR,
                    highlightcolor=ACCENT,
                    takefocus=True,
                    cursor="hand2",
                    font=("Microsoft YaHei UI", 9),
                    wraplength=pixels(self.root, 140),
                )
                button.pack(fill="x")
                self._settings_nav_buttons[key] = button
                self._settings_nav_surfaces[key] = surface

        self.settings_page_host = tk.Frame(self.settings_body, bg=APP_BG)
        self.settings_page_host.pack(side="left", fill="both", expand=True)
        self.settings_body.bind('<Configure>', self._settings_responsive_navigation, add=True)
        general_panel = self._create_settings_page(
            "general", "基本偏好", "设置软件语言与阅读字号；你的聊天、记忆和人格文件保持原样。",
        )
        build_general_settings(self, general_panel)
        model_panel = self._create_settings_page(
            "models",
            "模型与 API",
            "在本地 Qwen 与 OpenAI 兼容接口之间切换。"
            + self._credential_storage_description(),
        )
        model_card = SettingsCard(model_panel)
        model_card.pack(fill="x", pady=(0, pixels(self.root, 16)))
        self._build_model_provider_settings(model_card.body)
        asr_panel=self._create_settings_page('asr','语音输入','选择在线转写或本地模型。在线录音会发送到所选接口；临时录音在识别或取消后删除。')
        from .asr_settings import AsrSettingsPanel
        self.asr_settings_panel=AsrSettingsPanel(asr_panel,self.service.config.root)
        self.asr_settings_panel.pack(fill='x')

        conversation_panel = self._create_settings_page(
            "conversation",
            "对话与人格",
            "调整长期人格指令；语音与悬浮窗开关仍在主聊天页顶部。",
        )
        instruction = self._settings_section(
            conversation_panel,
            "全局 instruction",
            "",
            last=True,
            group="长期人格",
        )
        self.instruction_panel = InstructionPanel(
            instruction, self.service.config.root, self.service.config.llm.system_prompt,
        )
        self.instruction_panel.pack(fill="x", padx=22, pady=(0, 18))
        # Retain existing integration entry points; the panel owns file state.
        self.instruction_text = self.instruction_panel.text
        self.instruction_status = self.instruction_panel.status_label

        storage_panel = self._create_settings_page(
            "storage",
            "记忆与存储",
            "管理长期记忆和可安全重建的缓存，不触碰模型、训练数据与语音素材。",
        )
        cache = self._settings_section(
            storage_panel,
            "清理缓存",
            "仅清理 pip、uv、构建缓存及临时对话语音；模型、训练数据、"
            "记忆和试听样本不会删除。",
            group="本地存储",
        )
        cache_actions = tk.Frame(cache, bg=WHITE)
        cache_actions.pack(fill="x", padx=22, pady=(0, 18))
        self.cache_status = tk.Label(
            cache_actions,
            text="正在扫描可安全清理的缓存…",
            bg=WHITE,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        self.cache_status.pack(side="left")
        self._setting_button(
            cache_actions,
            "重新扫描",
            self._refresh_cache_stats,
        ).pack(side="right")
        self._setting_button(
            cache_actions,
            "立即清理",
            self._confirm_clear_cache,
            danger=True,
        ).pack(side="right", padx=8)

        memory = self._settings_section(
            storage_panel,
            "记忆管理",
            "导出为 JSON 并在 VS Code 中编辑；回到这里确认后才会写回 SQLite。",
            last=True,
            group="长期记忆",
        )
        memory_actions = tk.Frame(memory, bg=WHITE)
        memory_actions.pack(fill="x", padx=22, pady=(0, 18))
        self.memory_status = tk.Label(
            memory_actions,
            text=f"当前 {self.service.memory.memory_count()} 条长期记忆",
            bg=WHITE,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        self.memory_status.pack(side="left")
        self._setting_button(
            memory_actions,
            "VS Code 编辑",
            self._open_memory_editor,
            primary=True,
        ).pack(side="right")
        self._setting_button(
            memory_actions,
            "应用 JSON",
            self._apply_memory_editor,
        ).pack(side="right", padx=8)
        self.memory_panel = MemoryPanel(
            memory, self.service.memory, self.service.config.root,
            on_changed=self._update_stats,
        )
        self.memory_panel.pack(fill="x", padx=22, pady=(0, 20))
        self._build_storage_locations(storage_panel)

        appearance_panel = self._create_settings_page(
            "appearance",
            "外观与动作",
            "设置雷姆尺寸、管理背景相册，并预览已经安装的动作。",
        )
        theme_section = self._settings_section(
            appearance_panel, "界面主题",
            "浅色保留当前蓝白风格；深色采用蓝灰底色。即时生效并在下次启动时保留。",
            group="界面",
        )
        theme_controls = tk.Frame(theme_section, bg=WHITE)
        theme_controls.pack(fill="x", padx=22, pady=(0, 18))
        for mode, label in (("light", "浅色"), ("dark", "深色")):
            tk.Radiobutton(
                theme_controls, text=label, variable=self.theme_mode, value=mode,
                command=lambda selected=mode: self.set_theme(selected),
                bg=WHITE, fg=INK, activebackground=WHITE, activeforeground=INK,
                selectcolor=WHITE, font=("Microsoft YaHei UI", 10), padx=12, pady=7,
            ).pack(side="left", padx=(0, 16))
        scale = self._settings_section(
            appearance_panel,
            "雷姆大小",
            "缩放以脚底中心为锚点，悬浮气泡会继续跟随。",
            group="桌面雷姆",
        )
        scale_actions = tk.Frame(scale, bg=WHITE)
        scale_actions.pack(fill="x", padx=22, pady=(0, 18))
        for value, label in (
            (0.75, "75%"),
            (1.0, "100%"),
            (1.25, "125%"),
            (1.5, "150%"),
        ):
            button = self._setting_button(
                scale_actions,
                label,
                lambda selected=value: self._set_pet_scale(selected),
            )
            button.pack(side="left", padx=(0, 8))
            self._scale_buttons[value] = button

        background = self._settings_section(
            appearance_panel,
            "聊天背景",
            "导入图片并勾选参与更换；支持固定、每次启动或每天更换，不连续轮播。"
            "0% 显示原图，100% 完全淡化。",
            group="聊天页面",
        )
        album_actions = tk.Frame(background, bg=WHITE)
        set_color_roles(album_actions, background="WHITE")
        album_actions.pack(fill="x", padx=22, pady=(0, 12))
        self._setting_button(album_actions, "管理背景相册", self._open_background_album, primary=True).pack(side="left")
        self.background_album_status = tk.Label(background, text="", bg=WHITE, fg=MUTED,
                                                 font=("Microsoft YaHei UI", 9), anchor="w", justify="left")
        set_color_roles(self.background_album_status, background="WHITE", foreground="MUTED")
        self.background_album_status.pack(fill="x", padx=22, pady=(0, 8))
        self.background_album_status.bind("<Configure>", lambda event: self.background_album_status.configure(wraplength=max(100, event.width)), add=True)
        self._refresh_background_album()
        background_controls = tk.Frame(background, bg=WHITE)
        background_controls.pack(fill="x", padx=22, pady=(0, 18))
        self.background_fade_scale = tk.Scale(
            background_controls,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.background_fade,
            command=self._preview_background_fade,
            showvalue=False,
            resolution=1,
            bg=WHITE,
            fg=INK,
            activebackground=ACCENT,
            troughcolor="#DDEBF4",
            highlightthickness=0,
            bd=0,
            sliderlength=18,
            length=360,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )
        self.background_fade_scale.pack(side="left", fill="x", expand=True)
        self.background_fade_value = tk.Label(
            background_controls,
            text=f"{round(self.background_fade.get())}% 淡化",
            width=10,
            bg=WHITE,
            fg=ACCENT_DEEP,
            anchor="e",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.background_fade_value.pack(side="right", padx=(14, 0))

        actions = self._settings_section(
            appearance_panel,
            "动作预览",
            "静待使用 idle；思考、输出、专注和回复情绪会自动切换对应动作。",
            last=True,
            group="互动",
        )
        action_buttons = tk.Frame(actions, bg=WHITE)
        action_buttons.pack(fill="x", padx=22, pady=(0, 16))
        self._setting_button(action_buttons, "打开动作工作台", lambda: self._show_settings_category("actions"), primary=True).pack(side="left")

        action_page = tk.Frame(self.settings_page_host, bg=APP_BG)
        self.action_panel = ActionPanel(action_page, self.service.config.root,
                                        self.on_pet_preview, self.on_reload_actions)
        self.action_panel.pack(fill="both", expand=True)
        self._settings_pages["actions"] = action_page

        resources_page = tk.Frame(self.settings_page_host, bg=APP_BG)
        self.integration_panel = IntegrationPanel(
            resources_page, self.service.config.root, manager=self.integration_manager,
            on_draft=self._request_integration_draft, scope="remchat",
        )
        self.integration_panel.pack(fill="both", expand=True)
        self._settings_pages["integrations"] = resources_page

        self._show_settings_category("models")
        self.root.after(250, self._refresh_cache_stats)

    def _create_settings_page(
        self,
        key: str,
        title: str,
        description: str,
    ) -> tk.Frame:
        """创建带独立滚动区的设置分类页，并返回内容面板。"""

        page = tk.Frame(self.settings_page_host, bg=APP_BG)
        from .scroll_surface import ScrollCanvas
        canvas = ScrollCanvas(page, bg=APP_BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=APP_BG)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        intro = tk.Frame(content, bg=APP_BG)
        intro.pack(fill="x", padx=pixels(self.root, 28), pady=(pixels(self.root, 28), pixels(self.root, 24)))
        tk.Label(
            intro,
            text=title,
            bg=APP_BG,
            fg=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        description_label = tk.Label(
            intro,
            text=description,
            bg=APP_BG,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=690,
            font=("Microsoft YaHei UI", 9),
        )
        description_label.pack(fill="x", pady=(6, 0))
        def wrap_description(event) -> None:
            width = max(160, event.width - 2)
            if int(description_label.cget("wraplength")) != width:
                description_label.configure(wraplength=width)
        description_label.bind("<Configure>", wrap_description)
        panel = tk.Frame(content, bg=APP_BG, bd=0)
        panel.pack(fill="x", padx=pixels(self.root, 28), pady=(0, pixels(self.root, 28)))

        layout_job = None
        sizes = {}
        last_gutter = None
        last_bounds = None

        def resize_ready() -> None:
            nonlocal layout_job, last_gutter, last_bounds
            layout_job = None
            if not canvas.winfo_exists() or not canvas.winfo_ismapped():
                return
            width = canvas.winfo_width()
            if int(canvas.itemcget(content_window, "width")) != width:
                canvas.itemconfigure(content_window, width=width)
            gutter = pixels(self.root, 20 if width < pixels(self.root, 700) else 32)
            if gutter != last_gutter:
                intro.pack_configure(padx=gutter)
                panel.pack_configure(padx=gutter)
                last_gutter = gutter
            bounds = canvas.bbox("all")
            if bounds != last_bounds:
                canvas.configure(scrollregion=bounds)
                last_bounds = bounds

        def resize(event) -> None:
            nonlocal layout_job
            if event.type == tk.EventType.Configure:
                size = (event.width, event.height)
                if sizes.get(event.widget) == size:
                    return  # Native scroll moves the content, not its size.
                sizes[event.widget] = size
            if layout_job is None:
                layout_job = canvas.after(16, resize_ready)

        def destroy(event) -> None:
            nonlocal layout_job
            if event.widget is canvas and layout_job is not None:
                canvas.after_cancel(layout_job)
                layout_job = None

        canvas.bind("<Configure>", resize)
        canvas.bind("<Map>", resize, add=True)
        content.bind("<Configure>", resize)
        canvas.bind("<Destroy>", destroy, add=True)
        self._settings_pages[key] = page
        self._settings_canvases[key] = canvas
        return panel

    def _settings_responsive_navigation(self, event) -> None:
        if event.widget is not self.settings_body:
            return
        # Keep enough width for provider directory + details. Hysteresis
        # prevents the rail flickering during drags across one breakpoint.
        was_compact = getattr(self, '_settings_compact', False)
        compact = event.width < pixels(self.root, 1100 if was_compact else 1000)
        if getattr(self, '_settings_compact', None) == compact:
            return
        self._settings_compact = compact
        self.settings_page_host.pack_forget()
        if compact:
            self.settings_rail.pack_forget()
            self.settings_compact_nav.pack(side='top', fill='x')
            self.settings_page_host.pack(side='top', fill='both', expand=True)
        else:
            self.settings_compact_nav.pack_forget()
            self.settings_rail.pack(side='left', fill='y')
            self.settings_page_host.pack(side='left', fill='both', expand=True)

    def _show_settings_category(self, key: str) -> None:
        if key not in self._settings_pages or key == self._settings_visible_category:
            return
        previous = self._settings_visible_category
        if previous is not None:
            self._settings_pages[previous].pack_forget()
        self._settings_pages[key].pack(fill="both", expand=True)
        self._settings_visible_category = key
        self.settings_category = key
        self.settings_category_box.current(tuple(self._settings_categories).index(key))
        self.settings_canvas = self._settings_canvases.get(key)
        for name, button in self._settings_nav_buttons.items():
            if previous is not None and name not in {previous, key}:
                continue
            selected = name == key
            # Navigation changes happen after the last whole-tree theme pass.
            # Keep the role explicit and write a readable current-theme value now.
            set_color_roles(
                button,
                background="ACCENT_SOFT" if selected else "SIDEBAR",
                foreground="ACCENT_DEEP" if selected else "MUTED",
                activebackground="ACCENT_SOFT", activeforeground="ACCENT_DEEP",
                highlightbackground="ACCENT_SOFT" if selected else "SIDEBAR",
            )
            button.configure(
                font=(
                    "Microsoft YaHei UI",
                    9,
                    "bold" if selected else "normal",
                ),
            )
            self._settings_nav_surfaces[name].set_surface("ACCENT_SOFT" if selected else "SIDEBAR")

    def _settings_section(
        self,
        parent: tk.Widget,
        title: str,
        description: str,
        *,
        last: bool = False,
        group: str | None = None,
    ) -> tk.Frame:
        if group is not None and getattr(parent, "_settings_last_group", None) != group:
            label = tk.Label(parent, text=group, bg=APP_BG, fg=MUTED,
                             anchor="w", font=("Microsoft YaHei UI", 10))
            label.pack(fill="x", padx=pixels(self.root, 8),
                       pady=(pixels(self.root, 8), pixels(self.root, 10)))
            parent._settings_last_group = group
        card = SettingsCard(parent)
        card.pack(fill="x", pady=(0, pixels(self.root, 20 if last else 16)))
        section = card.body
        tk.Label(
            section,
            text=title,
            bg=WHITE,
            fg=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(fill="x", padx=pixels(self.root, 16), pady=(pixels(self.root, 8), pixels(self.root, 6)))
        description_label = tk.Label(
            section,
            text=description,
            bg=WHITE,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=680,
            font=("Microsoft YaHei UI", 9),
        )
        description_label.pack(fill="x", padx=pixels(self.root, 16), pady=(0, pixels(self.root, 16)))
        def wrap_description(event) -> None:
            width = max(160, event.width - 2)
            if int(description_label.cget("wraplength")) != width:
                description_label.configure(wraplength=width)
        description_label.bind("<Configure>", wrap_description)
        return section

    def _setting_button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
        danger: bool = False,
    ) -> tk.Button:
        background = ACCENT_DEEP if primary else SIDEBAR
        foreground = ON_ACCENT if primary else (ERROR if danger else INK)
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=ACCENT_HOVER if primary else ACCENT_SOFT,
            activeforeground=ON_ACCENT if primary else foreground,
            relief="flat",
            bd=0,
            padx=13,
            pady=7,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
            disabledforeground=SUBTLE,
            highlightthickness=1,
            highlightbackground=background,
            highlightcolor=ACCENT,
            takefocus=True,
        )
        set_color_roles(button, background="ACCENT_DEEP" if primary else "SIDEBAR",
                        foreground="ON_ACCENT" if primary else "ERROR" if danger else "INK",
                        activebackground="ACCENT_HOVER" if primary else "ACCENT_SOFT",
                        activeforeground="ON_ACCENT" if primary else "ERROR" if danger else "INK",
                        highlightbackground="ACCENT_DEEP" if primary else "SIDEBAR",
                        highlightcolor="ACCENT", disabledforeground="SUBTLE")
        return button

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    def _save_instruction(self) -> bool:
        return self.instruction_panel.save()

    def _open_instruction_editor(self) -> None:
        self.instruction_panel.open_editor()

    def _refresh_cache_stats(self) -> None:
        self.cache_status.configure(text="正在扫描…", fg=MUTED)

        def worker() -> None:
            try:
                stats = scan_safe_cache(self.service.config.root)
            except Exception as error:
                self.events.put(ChatEvent("cache-error", str(error)))
            else:
                self.events.put(ChatEvent("cache-stats", stats))

        threading.Thread(
            target=worker,
            name="rem-cache-scan",
            daemon=True,
        ).start()

    def _confirm_clear_cache(self) -> None:
        if not messagebox.askyesno(
            "清理缓存",
            "只会删除可重建的安装/构建缓存和临时对话语音。\n"
            "模型、训练数据、记忆与试听样本不会删除。是否继续？",
            parent=self.root,
        ):
            return
        self.audio_player.stop()
        self.cache_status.configure(text="正在清理…", fg=MUTED)

        def worker() -> None:
            try:
                stats = clear_safe_cache(self.service.config.root)
            except Exception as error:
                self.events.put(ChatEvent("cache-error", str(error)))
            else:
                self.events.put(ChatEvent("cache-cleared", stats))

        threading.Thread(
            target=worker,
            name="rem-cache-clean",
            daemon=True,
        ).start()

    def _open_memory_editor(self) -> None:
        path = self.service.config.root / MEMORY_EDITOR_FILE
        try:
            count = self.service.memory.export_memory_editor(path)
            open_in_vscode(path)
        except (OSError, ValueError, FileNotFoundError) as error:
            self.memory_status.configure(text=str(error), fg=ERROR)
            return
        self.memory_status.configure(
            text=f"已导出 {count} 条；编辑保存后点击“应用 JSON”",
            fg=SUCCESS,
        )

    def _apply_memory_editor(self) -> None:
        path = self.service.config.root / MEMORY_EDITOR_FILE
        try:
            plan = self.service.memory.preview_memory_import(path)
        except (OSError, ValueError) as error:
            self.memory_status.configure(text=str(error), fg=ERROR)
            return
        if not messagebox.askyesno(
            "应用记忆更改",
            f"将新增 {plan.create} 条、更新 {plan.update} 条、"
            f"删除 {plan.delete} 条长期记忆。\n是否写回 SQLite？",
            parent=self.root,
        ):
            return
        try:
            applied = self.service.memory.apply_memory_import(path)
        except (OSError, ValueError) as error:
            self.memory_status.configure(text=str(error), fg=ERROR)
            return
        self.service.memory.export_memory_editor(path)
        self.memory_status.configure(
            text=(
                f"已应用：新增 {applied.create}、更新 {applied.update}、"
                f"删除 {applied.delete}"
            ),
            fg=SUCCESS,
        )

    def _set_pet_scale(self, scale: float) -> None:
        if scale not in PET_SCALES:
            return
        self.pet_scale.set(scale)
        self.on_set_pet_scale(scale)

    def _preview_background_fade(self, value: str) -> None:
        """滑杆移动时即时重绘，并在短暂停顿后合并保存。"""

        try:
            normalized = min(100, max(0, round(float(value))))
        except ValueError:
            return
        self.background_fade_value.configure(
            text=f"{normalized}% 淡化",
        )
        self._background_render_key = None
        self._schedule_background_render()
        if self._background_fade_save_after_id is not None:
            try:
                self.root.after_cancel(self._background_fade_save_after_id)
            except tk.TclError:
                pass
        self._background_fade_save_after_id = self.root.after(
            350,
            lambda selected=normalized: self._commit_background_fade(
                selected
            ),
        )

    def _commit_background_fade(self, value: int) -> None:
        self._background_fade_save_after_id = None
        self.on_set_background_fade(value)

    def _build_storage_locations(self, parent: tk.Frame) -> None:
        section = self._settings_section(parent, "数据位置", "产品更名为 RemChat，现有 F 盘数据路径保持不变。", last=True, group="文件位置")
        from pathlib import Path
        paths = (
            ("聊天与记忆", Path("data/memory.sqlite3")),
            ("人格指令", INSTRUCTION_FILE),
            ("模型与 API", Path("data/model-providers.json")),
            ("凭据密文", Path("data/credentials")),
            ("连接与监工", Path("data/integrations")),
            ("广场存档", Path("data/plaza")),
            ("自定义动作", Path("data/pet-actions")),
            ("自动备份", Path("data/backups")),
            ("运行日志", Path("logs")),
        )
        details = tk.Text(section, height=len(paths), bg=WHITE, fg=MUTED, wrap="word",
                          font=("Microsoft YaHei UI", 9), bd=0, padx=22, pady=10)
        details.pack(fill="x", padx=0, pady=(0, 10))
        details.insert("1.0", "\n".join(f"{label}  ·  {self.service.config.root / path}" for label, path in paths))
        details.configure(state="disabled")
        bind_text_editing(details, read_only=True)
