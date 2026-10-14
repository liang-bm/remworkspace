"""Word learning is the experience; local dictionaries are its resources."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from .soft_fields import SoftEntry
from tkinter import filedialog, messagebox, ttk

from ..plaza.dictionaries import LocalDictionaries
from ..plaza.words import WordDeck
from ..word_memory import memory_prompt, memory_system_prompt, normalize_memory_evidence, parse_memory_result
from ..plaza.classic_examples import classic_examples
from . import theme
from .theme_controller import set_color_roles
from .settings_card import SettingsCard
from .selection_feedback import Tooltip


def configure_entry_text(widget: tk.Text) -> None:
    colors = theme.palette()
    widget.tag_configure("headword", font=("Segoe UI", 22, "bold"), spacing3=10)
    widget.tag_configure("source", font=(theme.BODY_FONT, 10), foreground=colors["MUTED"], spacing3=12)
    widget.tag_configure("credit", font=(theme.BODY_FONT, 10), foreground=colors["MUTED"], spacing3=10)
    widget.tag_configure("phonetic", font=("Segoe UI", 12), spacing3=10)
    widget.tag_configure("section", font=(theme.BODY_FONT, 12, "bold"), spacing1=12, spacing3=8)
    widget.tag_configure("pos", font=(theme.BODY_FONT, 11, "bold"), foreground=colors["ACCENT"], spacing1=12, spacing3=6)
    widget.tag_configure("sense", font=(theme.BODY_FONT, 11), spacing3=7)
    widget.tag_configure("example", font=(theme.BODY_FONT, 10), foreground=colors["MUTED"],
                         lmargin1=18, lmargin2=18, spacing1=0, spacing2=0, spacing3=0)
    widget.tag_configure("example_end", spacing3=8)


def _display_example(value: str) -> str:
    """Layout-only whitespace normalization; never mutate source/card strings."""
    return "\n".join(line.strip() for line in str(value).splitlines() if line.strip())


def insert_dictionary_entry(widget: tk.Text, entry: dict, *, header: bool = True,
                            examples: bool = True) -> None:
    """Insert only plain source strings, using semantic visual hierarchy."""
    if header:
        widget.insert("end", str(entry["word"]) + "\n", "headword")
        widget.insert("end", "来源：" + str(entry["source"]) + "\n", "source")
        if entry.get("phonetics"):
            widget.insert("end", " / ".join(str(p) for p in entry["phonetics"]) + "\n", "phonetic")
    senses = entry.get("senses") or []
    if not senses:
        widget.insert("end", str(entry["text"]) + "\n", "sense")
        return
    previous_pos = None
    for index, sense in enumerate(senses, 1):
        pos = str(sense.get("pos", ""))
        if pos and pos != previous_pos:
            widget.insert("end", pos + "\n", "pos")
        previous_pos = pos
        label = str(sense.get("label", "")).strip() or f"{index}."
        widget.insert("end", label + " " + str(sense["definition"]) + "\n", "sense")
        for example in sense.get("examples", []) if examples else ():
            lines = _display_example(example).splitlines()
            for index, line in enumerate(lines):
                widget.insert("end", line + "\n", ("example", "example_end") if index == len(lines) - 1 else "example")


class _FlowToolbar(tk.Frame):
    """Natural-width native controls wrap before they run outside the panel."""
    def __init__(self, parent):
        super().__init__(parent)
        set_color_roles(self, background=getattr(parent, "_remchat_color_roles", {}).get("background", "WHITE"))
        self._buttons = []
        self._layout_id = None
        self.bind("<Configure>", self._schedule)
        self.bind("<Destroy>", self._destroyed, add="+")

    def add(self, button):
        self._buttons.append(button)
        self._schedule()

    def _schedule(self, _event=None):
        if self._layout_id is None:
            self._layout_id = self.after_idle(self._layout)

    def _layout(self):
        self._layout_id = None
        available = max(1, self.winfo_width())
        row = column = used = 0
        for button in self._buttons:
            width = button.winfo_reqwidth()
            if used and used + width + 8 > available:
                row += 1
                column = used = 0
            button.grid(row=row, column=column, sticky="w", padx=(0, 8), pady=(0, 4))
            used += width + 8
            column += 1

    def _destroyed(self, event):
        if event.widget is self and self._layout_id is not None:
            self.after_cancel(self._layout_id)
            self._layout_id = None


class WordLearningPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, project_root: Path, *,
                 get_deck: Callable[[], WordDeck],
                 commit: Callable[[Callable[[WordDeck], object]], object],
                 on_explain: Callable[[str], None], on_status: Callable[[str], None],
                 on_model_request=None):
        super().__init__(parent)
        set_color_roles(self, background="APP_BG")
        self.project_root = Path(project_root)
        self.get_deck, self.commit = get_deck, commit
        self.on_explain, self.on_status = on_explain, on_status
        self.on_model_request = on_model_request
        self._ai_results = {}
        self._ai_selection = {}
        self._example_controls = []
        self._ai_busy = False
        self._ai_word = ""
        self._ai_serial = 0
        self._ai_timeout = None
        self.library = LocalDictionaries(self.project_root)
        self._current = None
        self._word = ""
        self._revealed = False
        self._entries: list[dict] = []
        self._classic_examples = ()
        self._classic_error = ""
        self._lookup_errors = []
        self._closed = self._busy = False
        self._generation = 0
        self._pending_word = None
        self._queue = queue.Queue()
        self._poll_id = None
        self._editor = self._dictionary = None
        self._reading_section = "释义"
        self._build()
        self.bind("<Destroy>", self._destroyed, add="+")
        self.refresh()

    @staticmethod
    def _label(parent, text="", *, size=11, bold=False, muted=False):
        widget = tk.Label(parent, text=text, anchor="w", justify="left",
                          font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
        set_color_roles(widget, background=getattr(parent, "_remchat_color_roles", {}).get("background", "WHITE"),
                        foreground="MUTED" if muted else "INK")
        widget.bind("<Configure>", lambda event: widget.configure(wraplength=max(120, event.width)))
        return widget

    @staticmethod
    def _button(parent, text, command, *, primary=False):
        widget = tk.Button(parent, text=text, command=command, font=(theme.BODY_FONT, 10, "bold"),
                           relief="flat", bd=0, padx=12, pady=8, cursor="hand2", takefocus=True,
                           highlightthickness=1)
        set_color_roles(widget, background="ACCENT_DEEP" if primary else "ACCENT_SOFT",
                        foreground="ON_ACCENT" if primary else "INK", highlightcolor="ACCENT",
                        highlightbackground="WHITE", activebackground="ACCENT_HOVER" if primary else "SUBTLE",
                        activeforeground="ON_ACCENT" if primary else "INK")
        return widget

    def _build(self):
        body = tk.Frame(self)
        set_color_roles(body, background="APP_BG")
        body.pack(fill="both", expand=True, padx=24, pady=18)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)
        self.progress = tk.StringVar(self)
        self._label(body, muted=True, size=10).grid(row=0, column=0, sticky="ew", pady=(0, 12))
        body.grid_slaves(row=0)[0].configure(textvariable=self.progress)
        self.headword = tk.StringVar(self)
        title = self._label(body, size=28, bold=True)
        title.configure(textvariable=self.headword)
        title.grid(row=1, column=0, sticky="ew")
        self.pronunciation = tk.StringVar(self)
        self._label(body, muted=True).grid(row=2, column=0, sticky="ew", pady=(4, 10))
        body.grid_slaves(row=2)[0].configure(textvariable=self.pronunciation)
        source_row = tk.Frame(body)
        set_color_roles(source_row, background="APP_BG")
        source_row.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self._label(source_row, "基础释义", bold=True).pack(side="left", padx=(0, 12))
        self.sources = ttk.Combobox(source_row, state="readonly", font=(theme.BODY_FONT, 10), width=1)
        self.sources.pack(side="left", fill="x", expand=True)
        self.sources.bind("<<ComboboxSelected>>", lambda _event: self._render())
        reading_host = tk.Frame(body)
        set_color_roles(reading_host, background="APP_BG")
        reading_host.grid(row=4, column=0, sticky="nsew")
        reading_host.grid_columnconfigure(0, weight=1)
        reading_host.grid_rowconfigure(0, weight=1)
        self.reading_card = SettingsCard(reading_host, stretch=True, inset=18)
        self.reading_card.configure(height=240)
        self.reading_card.grid(row=0, column=0, sticky="nsew")
        reading = self.reading_card.body
        self.reading_title = self._label(reading, "基础释义", bold=True, size=13)
        self.reading_title.pack(fill="x", pady=(0, 4))
        self.reading_source = self._label(reading, muted=True, size=10)
        self.reading_source.pack(fill="x", pady=(0, 14))
        divider = tk.Frame(reading, height=1)
        set_color_roles(divider, background="LINE")
        divider.pack(fill="x", pady=(0, 12))
        text_host = tk.Frame(reading)
        set_color_roles(text_host, background="WHITE")
        text_host.pack(fill="both", expand=True)
        self.reading_nav = tk.Frame(reading_host)
        set_color_roles(self.reading_nav, background="APP_BG")
        self.reading_nav.grid(row=0, column=1, sticky="ne", padx=(14, 0), pady=18)
        self.reading_buttons = {}
        for label in ("释义", "例句", "我的例句", "我的笔记", "AI帮记"):
            button = tk.Button(self.reading_nav, text=label,
                               command=lambda section=label: self._choose_section(section),
                               relief="flat", bd=0, padx=10, pady=10, font=(theme.BODY_FONT, 10),
                               takefocus=True, highlightthickness=1)
            button.pack(fill="x", pady=3)
            self.reading_buttons[label] = button
            Tooltip(button, {"释义": "只读本地词典的正规释义；不会改动你的笔记。",
                             "例句": "只展示所选词典随附的例句；名著和影视原句在 AI 帮记中。",
                             "我的例句": "只收你明确选择保存的帮记例句，与词典例句分开。",
                             "我的笔记": "你的记忆线索；旧卡释义和例句不再展示。用下方按钮编辑。",
                             "AI帮记": "生活联想与名著、影视语境补充；点击下方生成才调用当前模型。"}[label])
        def responsive_reading(event):
            narrow = event.width < 520
            columns = 3 if event.width < 430 else 5
            layout = (narrow, columns if narrow else 1)
            if layout == getattr(self, "_nav_layout", None):
                return
            self._nav_layout = layout
            if narrow:
                self.reading_nav.grid(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=(4, 0))
                for button in self.reading_buttons.values():
                    button.pack_forget()
                for index, button in enumerate(self.reading_buttons.values()):
                    button.grid(row=index // columns, column=index % columns, sticky="ew", padx=2)
                for index in range(5):
                    self.reading_nav.columnconfigure(index, weight=1 if index < columns else 0)
            else:
                self.reading_nav.grid(row=0, column=1, columnspan=1, sticky="ne", padx=(14, 0), pady=18)
                for button in self.reading_buttons.values():
                    button.grid_forget()
                for button in self.reading_buttons.values():
                    button.pack(fill="x", pady=3)
        reading_host.bind("<Configure>", responsive_reading, add=True)
        scrollbar = ttk.Scrollbar(text_host, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.answer = tk.Text(text_host, wrap="word", font=(theme.BODY_FONT, 11), height=8,
                              padx=4, pady=4, bd=0, highlightthickness=0, takefocus=True,
                              yscrollcommand=scrollbar.set)
        set_color_roles(self.answer, background="WHITE", foreground="INK", insertbackground="INK",
                        selectbackground="ACCENT_SOFT", selectforeground="INK")
        configure_entry_text(self.answer)
        self.answer.pack(fill="both", expand=True)
        scrollbar.configure(command=self.answer.yview)
        review = _FlowToolbar(body)
        review.grid(row=5, column=0, sticky="ew", pady=(10, 2))
        self.reveal_button = self._button(review, "翻开释义", self._reveal, primary=True)
        review.add(self.reveal_button)
        self.forgot_button = ttk.Button(review, text="还没记住", command=lambda: self._review(False), takefocus=True)
        review.add(self.forgot_button)
        self.known_button = ttk.Button(review, text="记住了", command=lambda: self._review(True), takefocus=True)
        review.add(self.known_button)
        self.edit_button = ttk.Button(review, text="编辑笔记", command=self._edit, takefocus=True)
        review.add(self.edit_button)
        actions = _FlowToolbar(body)
        actions.grid(row=6, column=0, sticky="ew", pady=(0, 2))
        for text, command in (("管理词卡", self._manage), ("词典资源", self._open_dictionary),
                              ("导入", self._import_cards), ("导出", self._export_cards)):
            actions.add(ttk.Button(actions, text=text, command=command, takefocus=True))
        self.explain_button = ttk.Button(actions, text="AI帮记", command=self._explain, takefocus=True)
        actions.add(self.explain_button)
        self.save_ai_button = ttk.Button(actions, text="解释加入笔记", command=self._save_ai, takefocus=True)
        actions.add(self.save_ai_button)
        self.save_ai_examples_button = ttk.Button(actions, text="添加所选例句", command=self._save_ai_examples, takefocus=True)
        actions.add(self.save_ai_examples_button)
        Tooltip(self.explain_button, "按需调用当前模型组织助记；只发送单词及少量已核验句源，不上传词典或个人笔记。")
        Tooltip(self.save_ai_button, "只把解释和记忆线索追加到笔记；不保存例句，不覆盖原笔记或复习进度。")
        Tooltip(self.save_ai_examples_button, "先在帮记结果中逐条勾选，再加入个人例句库；未勾选的不会保存。")
        self.message = tk.StringVar(self)
        message = self._label(body, muted=True, size=10)
        message.configure(textvariable=self.message)
        message.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        self.bind("<<ThemeChanged>>", lambda _event: self._style_reading_nav(), add=True)

    def _style_reading_nav(self):
        for label, button in self.reading_buttons.items():
            selected = label == self._reading_section
            set_color_roles(button, background="ACCENT_SOFT" if selected else "APP_BG",
                            foreground="INK" if selected else "MUTED", activebackground="ACCENT_SOFT",
                            activeforeground="INK", highlightbackground="ACCENT_SOFT" if selected else "APP_BG",
                            highlightcolor="ACCENT", disabledforeground="MUTED")
            button.configure(text=("› " if selected else "  ") + label,
                             state="normal" if self._current and (self._revealed or label == "AI帮记") else "disabled")

    def _choose_section(self, section):
        if self._current and (self._revealed or section == "AI帮记"):
            self._reading_section = section
            self._render()

    def refresh(self):
        if self._closed:
            return
        deck = self.get_deck()
        current = deck.next_due()
        word = str(current["word"]) if current else ""
        self.progress.set(f"{len(deck.cards)} 张词卡 · 已学 {sum(int(c.get('reviews', 0)) > 0 for c in deck.cards)} 张"
                          f" · 完成 {sum(int(c.get('reviews', 0)) for c in deck.cards)} 次复习")
        self._current = current
        changed = word != self._word
        if changed:
            self._word = word
            self._revealed = False
            self._reading_section = "释义"
            self._entries = []
            self._classic_examples = ()
            self._classic_error = ""
            self._lookup_errors = []
            self.sources.configure(values=(), state="disabled")
            self.sources.set("正在匹配本地词典…" if word else "当前没有到期词卡")
            self.pronunciation.set("")
            self.headword.set(word or "今天先到这里")
            self._generation += 1
            if word:
                self._request_lookup(word)
        elif not word:
            self.headword.set("今天先到这里")
        self.reveal_button.configure(state="normal" if current else "disabled")
        self.edit_button.configure(state="normal" if current else "disabled")
        self.explain_button.configure(state="normal" if current and not self._ai_busy else "disabled")
        self._render(preserve_scroll=not changed)

    def _request_lookup(self, word):
        if self._busy:
            self._pending_word = word
            return
        self._busy = True
        generation = self._generation
        self.message.set("正在自动匹配本地词典；首次只建立索引，不调用模型。")

        def run():
            try:
                entries = self.library.lookup(word)
                errors = [s["source"] + "：" + s["error"] for s in self.library.status if s.get("error")]
            except Exception as error:
                entries, errors = [], [str(error)]
            try:
                classics, classic_error = classic_examples(word, limit=3), ""
            except (OSError, ValueError, KeyError) as error:
                classics, classic_error = (), "语境原句暂不可用：" + str(error)
            try:
                self._queue.put((generation, word, entries, errors, classics, classic_error))
            finally:
                if self._closed:
                    self.library.close()

        threading.Thread(target=run, daemon=True, name="remchat-word-definition").start()
        self._poll_id = self.after(60, self._drain)

    def _drain(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            generation, word, entries, errors, classics, classic_error = self._queue.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(60, self._drain)
            return
        self._busy = False
        if generation == self._generation and word == self._word:
            preferred = str(self._current.get("dictionary_source", "")) if self._current else ""
            self._entries = sorted(entries, key=lambda e: (e["source"] != preferred if preferred else "柯林斯" not in e["source"], e["source"]))
            self._lookup_errors = errors
            self._classic_examples, self._classic_error = classics, classic_error
            self.sources.configure(values=[str(e["source"]) for e in self._entries])
            if self._entries:
                self.sources.configure(state="readonly")
                self.sources.current(0)
            else:
                self.sources.configure(state="disabled")
                self.sources.set("未匹配词典 · 打开下方“词典资源”检查")
            self.message.set((f"已匹配 {len(entries)} 本本地词典；先回想，再翻开释义。" if entries else
                              "本地词典未找到该词；可记录学习笔记，或在词典资源中检查/导入。")
                             + ("\n" + "\n".join(errors) if errors else ""))
            self._render()
        pending = self._pending_word
        self._pending_word = None
        if pending and pending == self._word:
            self._request_lookup(pending)

    def _render(self, *, preserve_scroll=False):
        position = self.answer.yview()[0] if preserve_scroll else 0
        for control in self._example_controls:
            control.destroy()
        self._example_controls.clear()
        self.answer.configure(state="normal")
        self.answer.delete("1.0", "end")
        index = self.sources.current()
        entry = self._entries[index] if 0 <= index < len(self._entries) else None
        titles = {"释义": "基础释义", "例句": "词典例句", "我的例句": "我的例句库", "我的笔记": "我的学习笔记", "AI帮记": "AI帮记"}
        self.reading_title.configure(text=titles[self._reading_section])
        self.reading_source.configure(text=("来源：" + str(entry["source"]) if entry else "本地词典资源")
                                      if self._reading_section == "释义" else
                                      ("来源：" + str(entry["source"]) if entry else "只展示词典随附例句") if self._reading_section == "例句" else
                                      "模型按需组织 · 解释与例句分别由你选择保存" if self._reading_section == "AI帮记" else
                                      "你明确加入的例句 · 不混入词典原文" if self._reading_section == "我的例句" else
                                      "个人内容 · 与词典原文分开保存，可随时编辑")
        if not self._current:
            self.answer.insert("end", "当前没有到期词卡。可以导入新词，或等待下一轮复习。")
            self.message.set("记住后次日复习；没记住 10 分钟后重试。")
        elif self._reading_section == "AI帮记":
            result = self._ai_results.get(self._word)
            if result:
                self.answer.insert("end", "解释与记忆线索 · AI生成\n", "section")
                self.answer.insert("end", result["explanation"] + "\n", "sense")
                self.answer.insert("end", "词义请以词典核对；点击“解释加入笔记”才保存。\n", "source")
                self.answer.insert("end", "可选例句\n", "section")
                if not result["examples"]:
                    self.answer.insert("end", "本次没有可独立保存的例句，可重新生成。", "source")
                for index, example in enumerate(result["examples"]):
                    self._insert_example(example, selectable=index)
                self.answer.insert("end", "\n勾选后点击“添加所选例句”；未保存的内容不会写入你的存档。", "source")
            else:
                self.answer.insert("end", "正在组织帮记材料…可以继续读词典或记笔记。" if self._ai_busy and self._ai_word == self._word else
                                   "点击下方“AI帮记”，让模型用生活场景、联想或合理拆分帮你记。\n\n生成后可以逐条勾选例句加入自己的例句库，也可以单独把解释加入笔记。两者都不会自动保存。")
        elif not self._revealed:
            self.answer.insert("end", "先在心里回想它的意思。\n\n翻开后显示本地词典基础释义、出处清晰的例句和你自己的笔记。")
        else:
            if self._reading_section == "释义":
                if entry:
                    insert_dictionary_entry(self.answer, entry, header=False, examples=False)
                else:
                    self.answer.insert("end", "本地词典仍在匹配，完成后自动显示。" if self._busy else
                                       "本地词典未找到基础释义。没有补造词义、音标或例句。", "source")
            elif self._reading_section == "例句":
                supplied = 0
                for sense in (entry.get("senses") or []) if entry else ():
                    examples = sense.get("examples") or []
                    if not examples:
                        continue
                    self.answer.insert("end", str(sense.get("definition", "")) + "\n", "pos")
                    for example in examples:
                        supplied += 1
                        self.answer.insert("end", _display_example(example) + "\n", "sense")
                        self.answer.insert("end", "出处：" + str(entry["source"]) + " · 词条例句\n\n", "source")
                if not supplied:
                    self.answer.insert("end", "所选词典没有可独立展示的例句。完整原文可在“词典资源”中查看。", "source")
            elif self._reading_section == "我的例句":
                examples = self.get_deck().get_examples(self._word)
                if not examples:
                    self.answer.insert("end", "例句库还是空的。先生成 AI 帮记，勾选喜欢的例句，再点击“添加所选例句”。", "source")
                for example in examples:
                    self._insert_example(example)
            else:
                fields = (("学习笔记", "notes"),)
                personal = False
                for label, key in fields:
                    value = str(self._current.get(key, "")).strip()
                    if value:
                        personal = True
                        self.answer.insert("end", label + "\n", "section")
                        self.answer.insert("end", value + "\n\n", "sense")
                if not personal:
                    self.answer.insert("end", "还没有记录，可用下方“编辑笔记”随手记下你的记忆线索。", "source")
        self.answer.configure(state="disabled")
        self.answer.yview_moveto(position)
        state = "normal" if self._current and self._revealed else "disabled"
        self.forgot_button.configure(state=state)
        self.known_button.configure(state=state)
        result = self._ai_results.get(self._word)
        self.save_ai_button.configure(state="normal" if self._current and result and result["explanation"] else "disabled")
        self._update_example_selection()
        phonetic = entry if entry and entry.get("phonetics") else None
        self.pronunciation.set((" / ".join(phonetic["phonetics"]) + " · " + phonetic["source"]) if phonetic else "")
        self._style_reading_nav()

    def _insert_example(self, example, *, selectable=None):
        if selectable is not None:
            word = self._word
            value = tk.BooleanVar(self.answer, value=selectable in self._ai_selection.get(word, set()))
            def toggle():
                selected = self._ai_selection.setdefault(word, set())
                selected.add(selectable) if value.get() else selected.discard(selectable)
                self._update_example_selection()
            control = tk.Checkbutton(self.answer, text="选择此例句", variable=value, command=toggle,
                                     font=(theme.BODY_FONT, 10), bd=0, takefocus=True, cursor="hand2")
            control._selection_var = value
            set_color_roles(control, background="WHITE", foreground="INK", selectcolor="WHITE",
                            activebackground="SUBTLE", activeforeground="INK")
            self._example_controls.append(control)
            self.answer.window_create("end", window=control)
            self.answer.insert("end", "\n")
        self.answer.insert("end", example["text"] + "\n", "sense")
        if example.get("translation"):
            self.answer.insert("end", example["translation"] + "\n", "example")
        if example.get("kind") == "quote":
            kind = {"book": "名著原句", "film": "电影剧本", "tv": "电视剧剧本"}.get(example.get("source_kind"), "作品原句")
            credit = example.get("credit", "")
            source = f"{kind} · 《{example['source_title']}》" + (f" · {credit}" if credit else "")
        else:
            source = "AI 自拟 · 非作品引用"
        self.answer.insert("end", source + "\n", "credit")

    def _update_example_selection(self):
        selected = self._ai_selection.get(self._word, set())
        result = self._ai_results.get(self._word)
        count = len([index for index in selected if result and 0 <= index < len(result["examples"])])
        self.save_ai_examples_button.configure(text=f"添加所选例句（{count}）" if count else "添加所选例句",
                                              state="normal" if self._current and count else "disabled")

    def _reveal(self):
        if self._current:
            self._revealed = True
            self._render()

    def _commit(self, change, success=""):
        try:
            outcome = self.commit(change)
            if isinstance(outcome, tuple) and len(outcome) == 2:
                saved, result = outcome
            else:
                saved, result = True, outcome
            if not saved:
                return False, result
            if success:
                self.on_status(success)
                self.message.set(success)
            self.refresh()
            return True, result
        except (OSError, ValueError) as error:
            self.message.set(str(error))
            self.on_status(str(error))
            return False, None

    def _review(self, known):
        if not self._current or not self._revealed:
            return
        word = self._word
        identity = {k: self._current.get(k, "") for k in ("reviews", "known", "due")}

        def change(deck):
            matches = [c for c in deck.cards if str(c["word"]) == word]
            if len(matches) != 1 or any(matches[0].get(k, "") != v for k, v in identity.items()):
                raise ValueError("词卡或复习进度已变化，请重新选择")
            deck.review(matches[0], known)

        self._commit(change, "已记录复习；基础释义仍直接来自本地词典。")

    def _edit(self, card=None):
        card = self._current if card is None else card
        if not card:
            return
        if self._editor is not None and self._editor.winfo_exists():
            self._editor.lift()
            return
        word = str(card["word"])
        window = self._editor = tk.Toplevel(self)
        window.title(f"学习笔记 · {word}")
        window.geometry("640x480")
        window.minsize(420, 340)
        set_color_roles(window, background="WHITE")
        self._label(window, word, size=18, bold=True).pack(fill="x", padx=20, pady=(16, 6))
        self._label(window, "随手记下自己的记忆线索；与词典原文分开保存，不重置复习进度。", muted=True).pack(fill="x", padx=20, pady=(0, 12))
        tabs = ttk.Notebook(window)
        tabs.pack(fill="both", expand=True, padx=20)
        fields = {}
        initial = {}
        for key, title in (("notes", "学习笔记"),):
            frame = tk.Frame(tabs)
            set_color_roles(frame, background="APP_BG")
            tabs.add(frame, text=title)
            scroll = ttk.Scrollbar(frame, orient="vertical")
            scroll.pack(side="right", fill="y")
            editor = tk.Text(frame, wrap="word", font=(theme.BODY_FONT, 11), undo=True,
                             bd=0, padx=12, pady=12, yscrollcommand=scroll.set)
            set_color_roles(editor, background="APP_BG", foreground="INK", insertbackground="INK",
                            selectbackground="ACCENT_SOFT", selectforeground="INK")
            editor.pack(fill="both", expand=True)
            scroll.configure(command=editor.yview)
            initial[key] = str(card.get(key, ""))
            editor.insert("1.0", initial[key])
            fields[key] = editor
        feedback = self._label(window, muted=True, size=10)
        feedback.pack(fill="x", padx=20, pady=(8, 0))

        def values():
            return {key: editor.get("1.0", "end-1c") for key, editor in fields.items()}

        def cancel():
            if values() != initial and not messagebox.askyesno("放弃未保存修改？", "当前笔记尚未保存。确定关闭？", parent=window):
                return
            window.destroy()

        def save():
            material = values()
            saved, _ = self._commit(lambda deck: deck.update_material(word, **material), "学习笔记与个人材料已保存；复习进度保持原样。")
            if saved:
                window.destroy()
            else:
                feedback.configure(text="保存失败，草稿已保留；请查看页面状态并重试。")

        actions = tk.Frame(window)
        set_color_roles(actions, background="WHITE")
        actions.pack(fill="x", padx=20, pady=14)
        self._button(actions, "保存笔记", save, primary=True).pack(side="right", padx=(8, 0))
        self._button(actions, "取消", cancel).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", cancel)
        fields["notes"].focus_set()

    def _import_cards(self):
        path = filedialog.askopenfilename(parent=self, title="导入自己的词卡",
                                         filetypes=[("词卡", "*.csv *.tsv *.txt *.json")])
        if path:
            saved, added = self._commit(lambda deck: deck.import_file(path))
            if saved:
                self.message.set(f"新增 {added} 张词卡；已有词卡、笔记和进度均保留。")

    def _export_cards(self):
        path = filedialog.asksaveasfilename(parent=self, title="导出词卡与笔记", defaultextension=".json",
                                          initialfile="remchat-words.json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                Path(path).write_text(json.dumps(self.get_deck().snapshot(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self.message.set("词卡、笔记和复习进度已导出；不包含整本词典。")
            except OSError as error:
                self.message.set(str(error))

    def _manage(self):
        window = tk.Toplevel(self)
        window.title("词卡管理")
        window.geometry("620x430")
        set_color_roles(window, background="WHITE")
        self._label(window, "词卡与笔记", size=16, bold=True).pack(fill="x", padx=18, pady=14)
        self._label(window, "可多选删除；基础释义不会在这里覆盖个人材料。", muted=True).pack(fill="x", padx=18)
        listing_frame = tk.Frame(window)
        set_color_roles(listing_frame, background="WHITE")
        listing_frame.pack(fill="both", expand=True, padx=18, pady=12)
        scrollbar = ttk.Scrollbar(listing_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        listing = tk.Listbox(listing_frame, selectmode="extended", exportselection=False,
                             font=(theme.BODY_FONT, 11), yscrollcommand=scrollbar.set, bd=0)
        set_color_roles(listing, background="APP_BG", foreground="INK", selectbackground="ACCENT_SOFT", selectforeground="INK")
        listing.pack(fill="both", expand=True)
        scrollbar.configure(command=listing.yview)
        displayed = [str(c["word"]) for c in self.get_deck().cards]
        for word in displayed:
            listing.insert("end", word)

        def edit_selected():
            selected = listing.curselection()
            if not selected:
                return
            deck = self.get_deck()
            if displayed != [str(c["word"]) for c in deck.cards]:
                self.message.set("词库已变化，请重新打开管理窗口。")
                return
            self._edit(deck.cards[selected[0]])

        def delete():
            indices = listing.curselection()
            if not indices:
                return
            if displayed != [str(c["word"]) for c in self.get_deck().cards]:
                self.message.set("词库已变化，请重新打开管理窗口。")
                return
            if not messagebox.askyesno("删除词卡", f"删除所选 {len(indices)} 张词卡及其笔记/进度？", parent=window):
                return

            def change(deck):
                for index in reversed(indices):
                    del deck.cards[index]

            saved, _ = self._commit(change, "已删除所选词卡；词典资源不受影响。")
            if saved:
                for index in reversed(indices):
                    listing.delete(index)
                    del displayed[index]

        actions = tk.Frame(window)
        set_color_roles(actions, background="WHITE")
        actions.pack(fill="x", padx=18, pady=(0, 14))
        self._button(actions, "编辑选中词卡", edit_selected).pack(side="left")
        self._button(actions, "添加单词", self._add_card).pack(side="left", padx=8)
        self._button(actions, "删除选中词卡", delete).pack(side="right")

    def _add_card(self):
        window = tk.Toplevel(self)
        window.title("添加学习单词")
        window.geometry("440x230")
        set_color_roles(window, background="WHITE")
        self._label(window, "添加单词", size=16, bold=True).pack(fill="x", padx=20, pady=(16, 8))
        self._label(window, "只需单词；基础释义自动从本地词典匹配，笔记可稍后写。", muted=True).pack(fill="x", padx=20)
        value = tk.StringVar(window)
        editor = SoftEntry(window, textvariable=value, font=(theme.BODY_FONT, 12))
        set_color_roles(editor, background="APP_BG", foreground="INK", insertbackground="INK")
        editor.pack(fill="x", padx=20, pady=14, ipady=6)
        feedback = self._label(window, muted=True, size=10)
        feedback.pack(fill="x", padx=20)

        def save():
            word = value.get().strip()
            if not word or len(word) > 512 or any(ord(c) < 32 for c in word):
                feedback.configure(text="请输入有效单词，最多 512 个字符。")
                return
            saved, added = self._commit(lambda deck: deck.add_dictionary_entry({"word": word}))
            if saved and added:
                window.destroy()
            elif saved:
                feedback.configure(text="已有这个单词；原材料与复习进度均保留。")

        self._button(window, "加入学习", save, primary=True).pack(anchor="e", padx=20, pady=12)
        editor.bind("<Return>", lambda _event: save())
        editor.focus_set()

    def _open_dictionary(self):
        from .dictionary_panel import LocalDictionaryWindow
        if self._dictionary is not None and self._dictionary.winfo_exists():
            self._dictionary.lift()
            return

        def add(entry):
            saved, added = self._commit(lambda deck: deck.add_dictionary_entry(entry))
            return ("已加入复习；基础释义继续从词典读取，个人笔记保持独立。" if added else
                    "已有该词卡；原有笔记、材料和复习进度保留。") if saved else "未保存，请查看页面状态。"

        self._dictionary = LocalDictionaryWindow(self, self.project_root, add, word=self._word)

    def _explain(self):
        if not self._current or self._ai_busy or self._closed:
            return
        evidence = normalize_memory_evidence(self._classic_examples)
        prompt = memory_prompt(self._word, evidence=evidence)
        system_prompt = memory_system_prompt()
        if self.on_model_request is None:
            self.on_explain(system_prompt + "\n\n学习数据：\n" + prompt)
            self.message.set("助记请求已发送到聊天；词卡材料没有被修改。")
            return
        self._ai_busy = True
        self._ai_word = self._word
        self._ai_serial += 1
        serial, word = self._ai_serial, self._word
        self._reading_section = "AI帮记"
        self.explain_button.configure(text="正在帮记…", state="disabled")
        self.message.set("正在调用当前模型；词典、笔记和复习进度不变。")
        self._render()

        def done(content="", error=""):
            if self._closed or serial != self._ai_serial:
                return
            if self._ai_timeout is not None:
                self.after_cancel(self._ai_timeout)
                self._ai_timeout = None
            self._ai_busy = False
            self._ai_serial += 1  # Late/duplicate callbacks cannot apply twice.
            self.explain_button.configure(text="AI帮记", state="normal" if self._current else "disabled")
            if error or not str(content).strip():
                self.message.set("AI帮记未完成：" + (str(error)[:300] or "模型未返回内容") + "；可重试。")
            else:
                try:
                    parsed = parse_memory_result(content, evidence=evidence)
                    if not parsed["explanation"] and not parsed["examples"]:
                        raise ValueError("模型未返回可用的解释或例句")
                    self._ai_results[word] = parsed
                    self._ai_selection[word] = set()
                    self.message.set("帮记已生成，尚未保存；例句可逐条选，解释可单独加入笔记。" if word == self._word else
                                     "上一个单词的帮记已生成；当前词卡没有改动。")
                except (ValueError, TypeError) as problem:
                    self.message.set("帮记结果未能读取：" + str(problem)[:200] + "；原笔记和例句库未变，可重试。")
            if word == self._word:
                self._render(preserve_scroll=True)

        self._ai_timeout = self.after(180000, lambda: done(error="请求超时"))
        try:
            self.on_model_request(prompt, lambda content: done(content), on_error=lambda error: done(error=error),
                                  system_prompt=system_prompt)
        except Exception as error:
            done(error=str(error))

    def _save_ai(self):
        result = self._ai_results.get(self._word)
        if not self._current or not result or not result["explanation"]:
            return
        word = self._word
        block = "AI帮记（词义请以词典核对）\n" + result["explanation"]

        def append(deck):
            card = next((row for row in deck.cards if str(row["word"]) == word), None)
            if card is None:
                raise ValueError("词卡已变化，请重新选择")
            notes = str(card.get("notes", "")).strip()
            if block not in notes:
                deck.update_material(word, notes=notes + ("\n\n" if notes else "") + block)

        saved, _ = self._commit(append, "解释与记忆线索已加入笔记；例句库未改，原笔记和复习进度保留。")
        if saved:
            self._revealed = True
            self._reading_section = "我的笔记"
            self._render()

    def _save_ai_examples(self):
        result = self._ai_results.get(self._word)
        if not self._current or not result:
            return
        word = self._word
        selected = [dict(result["examples"][index]) for index in sorted(self._ai_selection.get(word, set()))
                    if 0 <= index < len(result["examples"])]
        if not selected:
            return
        saved, added = self._commit(lambda deck: deck.add_examples(word, selected))
        if saved:
            self._ai_selection[word] = set()
            text = f"已加入 {added} 条例句；未勾选的未保存，笔记和复习进度不变。" if added else "所选例句已在你的库中，不重复添加。"
            self.message.set(text)
            self.on_status(text)
            self._revealed = True
            self._reading_section = "我的例句"
            self._render()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._ai_serial += 1
        if self._ai_timeout is not None:
            self.after_cancel(self._ai_timeout)
            self._ai_timeout = None
        self._pending_word = None
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if not self._busy or not self._queue.empty():
            self.library.close()

    def _destroyed(self, event):
        if event.widget is self:
            self.close()
