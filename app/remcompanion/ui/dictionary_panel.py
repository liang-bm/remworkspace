"""Local dictionary reading stays separate from review state and model requests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import queue
import threading
import tkinter as tk
from .soft_fields import SoftEntry
from tkinter import ttk
from .localized_dialogs import filedialog

from ..plaza.dictionaries import LocalDictionaries
from . import theme
from .theme_controller import set_color_roles
from .word_learning import configure_entry_text, insert_dictionary_entry


class LocalDictionaryWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, root_path: Path,
                 on_add: Callable[[dict], str], *, word: str = "") -> None:
        super().__init__(parent)
        self.title("RemChat · 本地词典")
        self.geometry("820x620")
        self.minsize(560, 420)
        self.library = LocalDictionaries(root_path)
        self.on_add = on_add
        self._entries: list[dict] = []
        self._responses = queue.Queue()
        self._busy = self._closed = False
        self._poll_id = None
        self._startup_id = None
        colors = theme.palette()
        set_color_roles(self, background="WHITE")
        body = tk.Frame(self, bg=colors["WHITE"])
        body.pack(fill="both", expand=True, padx=20, pady=16)
        self._label(body, "词典资源", size=18, bold=True).pack(anchor="w")
        self._label(body, "学习时自动读取基础释义；这里查完整词条、管理本地资源。", muted=True).pack(
            anchor="w", pady=(4, 14))
        search = tk.Frame(body, bg=colors["WHITE"])
        search.pack(fill="x")
        self.query = tk.StringVar(self, word)
        self.query_entry = SoftEntry(search, textvariable=self.query, font=(theme.BODY_FONT, 11),
                                    bd=1, relief="solid")
        set_color_roles(self.query_entry, background="APP_BG", foreground="INK", insertbackground="INK")
        self.query_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        self.query_entry.bind("<Return>", lambda _event: self.lookup())
        self.search_button = self._button(search, "查词", self.lookup, primary=True)
        self.search_button.pack(side="right")
        self._label(body, "词条来源", muted=True).pack(anchor="w", pady=(14, 4))
        self.sources = ttk.Combobox(body, state="readonly", font=(theme.BODY_FONT, 10))
        self.sources.pack(fill="x", pady=(0, 10))
        self.sources.bind("<<ComboboxSelected>>", lambda _event: self._show_entry())
        reading = tk.Frame(body, bg=colors["WHITE"])
        reading.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(reading, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(reading, wrap="word", font=(theme.BODY_FONT, 11), spacing1=3,
                            spacing3=7, padx=12, pady=10, bd=0, height=10,
                            yscrollcommand=scrollbar.set)
        set_color_roles(self.text, background="APP_BG", foreground="INK", insertbackground="INK",
                        selectbackground="ACCENT_SOFT", selectforeground="INK")
        self.text.pack(fill="both", expand=True)
        configure_entry_text(self.text)
        scrollbar.configure(command=self.text.yview)
        actions = tk.Frame(body, bg=colors["WHITE"])
        actions.pack(fill="x", pady=(12, 8))
        self.add_button = self._button(actions, "加入复习词卡", self._add, primary=True)
        self.add_button.pack(side="left")
        self.add_button.configure(state="disabled")
        self.import_button = self._button(actions, "导入 MDX 词典", self._import)
        self.import_button.pack(side="right")
        self.raw_mode = tk.BooleanVar(self, False)
        toggle = tk.Checkbutton(actions, text="完整原文", variable=self.raw_mode, command=self._show_entry,
                                font=(theme.BODY_FONT, 10))
        set_color_roles(toggle, background="WHITE", foreground="INK", activebackground="WHITE",
                        selectcolor="APP_BG", activeforeground="INK")
        toggle.pack(side="right", padx=(0, 12))
        self.message = tk.StringVar(self)
        note = self._label(body, "", muted=True)
        note.configure(textvariable=self.message, justify="left", anchor="w")
        note.pack(fill="x")
        note.bind("<Configure>", lambda event: note.configure(wraplength=max(120, event.width)))
        self._set_text("输入单词查词。\n词典仅在本地读取；明确加入词卡才保存，已有复习进度不会覆盖。\n"
                       "支持 MDX 文字释义与例句；图片、声音和外部脚本不加载。")
        self._source_status()
        self.bind("<Destroy>", self._destroyed, add="+")
        self._startup_id = self.after_idle(self._ready)

    def _ready(self):
        self._startup_id = None
        if self._closed:
            return
        self.query_entry.focus_set()
        if self.query.get().strip():
            self.lookup()

    @staticmethod
    def _label(parent, text, *, size=10, bold=False, muted=False):
        label = tk.Label(parent, text=text, anchor="w", justify="left",
                         font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
        set_color_roles(label, background="WHITE", foreground="MUTED" if muted else "INK")
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(120, event.width)))
        return label

    @staticmethod
    def _button(parent, text, command, *, primary=False):
        button = tk.Button(parent, text=text, command=command, font=(theme.BODY_FONT, 10, "bold"),
                           relief="flat", bd=0, padx=14, pady=8, cursor="hand2", takefocus=True)
        set_color_roles(button, background="ACCENT_DEEP" if primary else "ACCENT_SOFT",
                        foreground="ON_ACCENT" if primary else "INK",
                        activebackground="ACCENT_HOVER" if primary else "ACCENT_SOFT",
                        activeforeground="ON_ACCENT" if primary else "INK")
        return button

    def _set_text(self, value):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)

    def _source_status(self):
        sources = self.library.status
        available = sum(bool(source.get("available")) for source in sources)
        self.message.set(f"已发现 {available} 本本地词典 · 统一存放于 data/plaza/dictionaries。")

    def _work(self, task, complete, message):
        if self._closed or self._busy:
            return
        self._busy = True
        self.message.set(message)
        self.search_button.configure(state="disabled")
        self.query_entry.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self.add_button.configure(state="disabled")

        def run():
            try:
                self._responses.put((complete, task(), ""))
            except Exception as error:
                self._responses.put((complete, None, str(error)))
            finally:
                if self._closed:
                    self.library.close()

        threading.Thread(target=run, daemon=True, name="remchat-local-dictionary").start()
        self._poll_id = self.after(100, self._drain)

    def _drain(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            complete, result, error = self._responses.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(100, self._drain)
            return
        self._busy = False
        self.search_button.configure(state="normal")
        self.query_entry.configure(state="normal")
        self.import_button.configure(state="normal")
        if error:
            self.message.set(f"本地词典处理失败：{error}")
        else:
            complete(result)
        self.add_button.configure(state="normal" if self._entries else "disabled")

    def lookup(self):
        word = self.query.get().strip()
        if self._busy:
            return
        if not word:
            self.message.set("先输入一个单词。")
            self.query_entry.focus_set()
            return
        self._entries = []
        self.sources.configure(values=())
        self.sources.set("")
        self._set_text("正在读取本地词典……")
        self._work(lambda: self.library.lookup(word), self._looked_up,
                   "首次查询会创建本地索引，窗口仍可操作。")

    def _looked_up(self, entries):
        self._entries = entries
        self.sources.configure(values=[f"{entry['source']} · {entry['word']}" for entry in entries])
        if entries:
            self.sources.current(0)
            self._show_entry()
        else:
            self._set_text("没有找到这个词。请检查拼写，或导入包含该词的 MDX 词典。")
        problems = [f"{source['source']}：{source['error']}" for source in self.library.status
                    if source.get("error")]
        self.message.set(f"找到 {len(entries)} 个本地词条；不同词典可从上方切换。" +
                         ("\n" + "\n".join(problems) if problems else ""))

    def _show_entry(self):
        index = self.sources.current()
        if not 0 <= index < len(self._entries):
            return
        entry = self._entries[index]
        if self.raw_mode.get():
            self._set_text(f"{entry['word']}\n来源：{entry['source']}\n\n{entry['text']}")
        else:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            insert_dictionary_entry(self.text, entry)
            self.text.configure(state="disabled")
            self.text.yview_moveto(0)

    def _add(self):
        index = self.sources.current()
        if self._busy or not 0 <= index < len(self._entries):
            return
        self.message.set(self.on_add(self._entries[index]))

    def _import(self):
        path = filedialog.askopenfilename(parent=self, title="导入自有电子词典",
                                         filetypes=[("MDX 电子词典", "*.mdx")])
        if path:
            self._work(lambda: self.library.import_file(Path(path)), self._imported,
                       "正在校验并保存词典，不覆盖已有文件……")

    def _imported(self, path):
        self.library.refresh()
        self.message.set(f"已保存到 {path}；可以开始查词。")

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._closed = True
        if self._startup_id is not None:
            self.after_cancel(self._startup_id)
            self._startup_id = None
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        # A finishing worker owns close while indexing; do not block the Tk thread.
        if not self._busy or not self._responses.empty():
            self.library.close()
