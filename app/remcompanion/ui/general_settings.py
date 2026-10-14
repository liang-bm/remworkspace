"""Language and reading preferences, separate from model and personality settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..i18n import LANGUAGES
from ..companion_preferences import MIN_CHAT_FONT_SIZE, MAX_CHAT_FONT_SIZE
from . import theme
from .theme import WHITE, INK, MUTED
from .theme_controller import set_color_roles

# Refresh existing imported tokens and keep future page builds in sync.
theme.register_module(__name__)


def build_general_settings(window, panel: tk.Frame) -> None:
    section = window._settings_section(
        panel, "界面语言", "语言只影响软件界面，不改变聊天记录、回复语言或语音语言。",
        group="界面与阅读",
    )
    row = tk.Frame(section, bg=WHITE)
    row.pack(fill="x", padx=22, pady=(0, 20))
    window.ui_language_label = tk.StringVar(window.root, LANGUAGES[window.ui_language])
    window.ui_language_box = ttk.Combobox(
        row, textvariable=window.ui_language_label, values=tuple(LANGUAGES.values()),
        state="readonly", width=22, font=("Microsoft YaHei UI", 10),
    )
    window.ui_language_box.pack(side="left")

    def selected(_event=None) -> None:
        value = window.ui_language_label.get()
        language = next((code for code, label in LANGUAGES.items() if label == value), None)
        if language is not None:
            window.set_ui_language(language)

    window.ui_language_box.bind("<<ComboboxSelected>>", selected)
    section = window._settings_section(
        panel, "主聊天字号", "Ctrl + / Ctrl - 同步调整主聊天文字，不改变悬浮框大小与字号。",
        group="界面与阅读",
    )
    row = tk.Frame(section, bg=WHITE)
    row.pack(fill="x", padx=22, pady=(0, 22))
    window.chat_font_scale = tk.Scale(
        row, from_=MIN_CHAT_FONT_SIZE, to=MAX_CHAT_FONT_SIZE,
        orient="horizontal", variable=window.chat_font_size, resolution=1,
        command=lambda value: window.on_set_chat_font_size(round(float(value))),
        length=300, bg=WHITE, fg=INK, highlightthickness=0, bd=0,
        troughcolor=theme.palette()["ACCENT_SOFT"],
        activebackground=theme.palette()["ACCENT"],
    )
    set_color_roles(window.chat_font_scale, background="WHITE", foreground="INK",
                    troughcolor="ACCENT_SOFT", activebackground="ACCENT")
    window.chat_font_scale.pack(side="left", fill="x", expand=True)
    tk.Label(row, textvariable=window.chat_font_size, bg=WHITE, fg=MUTED,
             font=("Microsoft YaHei UI", 10), width=4).pack(side="right")
    section = window._settings_section(
        panel, "桌面跟踪环", "隐藏只关闭桌面显示，监工记录会保留；在这里随时恢复或管理任务。",
        last=True,
        group="任务提醒",
    )
    row = tk.Frame(section, bg=WHITE)
    row.pack(fill="x", padx=22, pady=(0, 20))
    tk.Checkbutton(
        row, text="显示桌面跟踪环", variable=window.task_rings_visible,
        command=lambda: window.on_set_task_rings_visible(window.task_rings_visible.get()),
        bg=WHITE, fg=INK, activebackground=WHITE, activeforeground=INK, selectcolor=WHITE,
        highlightthickness=1, highlightbackground=WHITE, highlightcolor=theme.palette()["ACCENT"],
        takefocus=True, font=(theme.BODY_FONT, 10),
    ).pack(anchor="w", pady=(0, 10))
    for text, command in (("恢复全部跟踪环", window.on_restore_task_rings),
                          ("查看全部监工任务", window.on_inspect_task_rings)):
        tk.Button(
            row, text=text, command=command, bg=WHITE, fg=INK, relief="flat", padx=16, pady=8,
            activebackground=theme.palette()["ACCENT_SOFT"], activeforeground=INK,
            highlightthickness=1, highlightbackground=theme.palette()["LINE"],
            highlightcolor=theme.palette()["ACCENT"], takefocus=True,
            font=(theme.BODY_FONT, 10),
        ).pack(side="left", padx=(0, 10))
