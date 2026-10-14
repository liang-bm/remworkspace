"""Review an MCP import before making any configuration changes."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import font as tkfont, ttk

from .locale_controller import LocaleController, translator_for
from .theme_controller import ThemeController
from .theme import get_mode, WHITE, INK, MUTED, BODY_FONT
from .dpi import pixels


def review_mcp_import(parent, rows, existing: set[str]) -> bool:
    if not rows:
        raise ValueError("配置没有可导入的服务。")
    window = tk.Toplevel(parent)
    window.title("检查 MCP 导入")
    window.transient(parent.winfo_toplevel())
    window.configure(bg=WHITE)
    width = min(pixels(window, 740), max(1, window.winfo_screenwidth() - pixels(window, 40)))
    height = min(pixels(window, 640), max(1, window.winfo_screenheight() - pixels(window, 40)))
    window.geometry(f"{width}x{height}")
    window.minsize(min(pixels(window, 480), width), min(pixels(window, 440), height))
    accepted = False
    body = tk.Frame(window, bg=WHITE)
    body.pack(fill="both", expand=True, padx=20, pady=16)
    tk.Label(body, text="检查服务与入口", bg=WHITE, fg=INK,
             font=(BODY_FONT, 16, "bold"), anchor="w").pack(fill="x")
    conflicts = sum(row["id"] in existing for row in rows)
    hint = tk.Label(body, text=f"{len(rows)} 项配置 · 将覆盖 {conflicts} 项同 ID 配置。全部默认停用，不会启动程序。",
                    bg=WHITE, fg=MUTED, font=(BODY_FONT, 10), anchor="w", justify="left")
    hint.pack(fill="x", pady=(8, 12))
    hint.bind("<Configure>", lambda event: hint.configure(wraplength=max(100, event.width - 4)))
    table_host = tk.Frame(body, bg=WHITE)
    table_host.pack(fill="both", expand=True)
    table_font = tkfont.Font(root=window, family=BODY_FONT, size=9)
    heading_font = tkfont.Font(root=window, family=BODY_FONT, size=9, weight="bold")
    def configure_table():
        style = ttk.Style(window)
        settings = {
            "RemChat.McpImport.Treeview": {"configure": {"font": table_font,
                "rowheight": table_font.metrics("linespace") + pixels(window, 12)}},
            "RemChat.McpImport.Treeview.Heading": {"configure": {"font": heading_font}},
        }
        for name in style.theme_names():
            style.theme_settings(name, settings)
    configure_table()
    table = ttk.Treeview(table_host, columns=("name", "entry", "state"), show="headings", height=7,
                         style="RemChat.McpImport.Treeview")
    table.bind("<<ThemeChanged>>", lambda _event: configure_table(), add=True)
    for key, label, width in (("name", "服务名称", 140), ("entry", "服务入口", 300), ("state", "导入操作", 90)):
        table.heading(key, text=label)
        minimum = heading_font.measure(label) + pixels(window, 24)
        table.column(key, width=max(pixels(window,width),minimum), minwidth=minimum, stretch=True)
    scroll = ttk.Scrollbar(table_host, command=table.yview)
    scroll.pack(side="right", fill="y")
    table.configure(yscrollcommand=scroll.set)
    horizontal = ttk.Scrollbar(table_host, orient="horizontal", command=table.xview)
    horizontal.pack(side="bottom", fill="x")
    table.configure(xscrollcommand=horizontal.set)
    table.pack(fill="both", expand=True)
    for row in rows:
        table.insert("", "end", values=(row.get("name", row["id"]), row.get("url", row.get("command", "")),
                                        "覆盖旧配置" if row["id"] in existing else "新增"))
    tk.Label(body, text="完整配置（只读）", bg=WHITE, fg=INK,
             font=(BODY_FONT, 10), anchor="w").pack(fill="x", pady=(12, 4))
    raw_host = tk.Frame(body, bg=WHITE)
    raw_host.pack(fill="both", expand=True)
    raw = tk.Text(raw_host, height=10, wrap="word", font=(BODY_FONT, 10), bg=WHITE, fg=INK)
    raw.insert("1.0", json.dumps(rows, ensure_ascii=False, indent=2))
    raw.configure(state="disabled")
    raw.pack(side="left", fill="both", expand=True)
    raw_scroll = ttk.Scrollbar(raw_host, command=raw.yview)
    raw_scroll.pack(side="right", fill="y")
    raw.configure(yscrollcommand=raw_scroll.set)
    actions = tk.Frame(body, bg=WHITE)
    actions.pack(fill="x", pady=(12, 0))

    def accept():
        nonlocal accepted
        accepted = True
        window.destroy()

    cancel = parent._button(actions, "取消", window.destroy)
    confirm = parent._button(actions, "确认导入并覆盖" if conflicts else "确认导入", accept, primary=True)
    for button in (cancel, confirm):
        button.configure(wraplength=180, takefocus=True)
    window.bind("<Escape>", lambda _event: window.destroy())
    theme_controller = ThemeController(window)
    theme_controller.apply(get_mode())
    locale_controller = LocaleController(window, translator_for(parent))
    locale_controller.apply()
    window.grab_set()
    cancel.focus_set()
    try:
        window.wait_window()
    finally:
        locale_controller.close()
    return accepted
