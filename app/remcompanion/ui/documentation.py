"""Read-only offline guide from the same localized READMEs used on GitHub."""
from __future__ import annotations

from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk

from . import theme
from .combobox_popup import AnchoredCombobox
from .dpi import pixels
from .theme_controller import set_color_roles
from ..clipboard import copy_text
from ..i18n import normalize_language
from ..text_editing import bind_text_editing


_GUIDE_FILES = {"zh-Hans": "README.md", "zh-Hant": "README.md",
                "en": "README.en.md", "ja": "README.ja.md"}
_GUIDE_LABELS = {
    "zh-Hans": ("使用指南", "复制说明", "使用指南暂不可读取，请检查程序包中的 README 文件。"),
    "zh-Hant": ("使用指南", "複製說明", "使用指南暫時無法讀取，請檢查程式包中的 README 檔案。"),
    "en": ("User guide", "Copy section", "The guide is unavailable. Check the README files in your program package."),
    "ja": ("使い方", "この節をコピー", "説明を読み込めません。プログラム内の README ファイルを確認してください。"),
}
_MAX_GUIDE_BYTES = 65536


def _split_sections(content: str) -> tuple[tuple[str, str], ...]:
    """Keep the introduction; headings in code samples are not chapters."""
    sections = []
    title = "RemChat"
    body = []
    fence = None
    for line in content.replace("\r\n", "\n").splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is not None:
            body.append(line)
            if (marker and marker[1][0] == fence[0]
                    and len(marker[1]) >= fence[1] and not marker[2].strip()):
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            body.append(line)
            continue
        heading = re.match(r"^##[ \t]+(.+?)\s*$", line)
        if heading:
            if body:
                sections.append((title, "\n".join(body).strip()))
            title, body = heading[1].strip(), []
        elif not body and line.startswith("# "):
            title = line[2:].strip()
        else:
            body.append(line)
    if body or not sections:
        sections.append((title, "\n".join(body).strip()))
    return tuple(sections)


def guide_sections(language: str = "zh-Hans", *, project_root: Path | None = None) -> tuple[tuple[str, str], ...]:
    language = normalize_language(language)
    filename = _GUIDE_FILES[language]
    bundled = Path(__file__).resolve().parent.parent / "documentation" / filename
    source_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[3]
    for path in (bundled, source_root / filename):
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_GUIDE_BYTES + 1)
            if not raw.strip() or len(raw) > _MAX_GUIDE_BYTES:
                continue
            return _split_sections(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError):
            continue
    title, _copy, unavailable = _GUIDE_LABELS[language]
    return ((title, unavailable),)


class GuideWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, tr=lambda text: text,
                 language: str | None = None, project_root: Path | None = None):
        super().__init__(parent)
        self.transient(parent)
        self.project_root = project_root
        self.guide_language = normalize_language(language or getattr(tr, "language", "zh-Hans"))
        labels = _GUIDE_LABELS[self.guide_language]
        self.title(labels[0])
        set_color_roles(self, background="APP_BG")
        width = min(pixels(self, 820), self.winfo_screenwidth() - 60)
        height = min(pixels(self, 580), self.winfo_screenheight() - 100)
        self.geometry(f"{max(400, width)}x{max(300, height)}")
        self.minsize(pixels(self, 340), pixels(self, 280))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = tk.Frame(self)
        set_color_roles(header, background="APP_BG")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=pixels(self, 18), pady=pixels(self, 14))
        header.columnconfigure(0, weight=1)
        self.sections = guide_sections(self.guide_language, project_root=self.project_root)
        self.choice = AnchoredCombobox(header, state="readonly", width=18,
                                      values=[title for title, _body in self.sections])
        self.choice.grid(row=0, column=0, sticky="ew", padx=(0, pixels(self, 12)))
        self.choice._remchat_locale_exclude = True
        button = tk.Button(header, text=labels[1], command=self._copy,
                           font=(theme.BODY_FONT, 10), relief="flat", bd=0,
                           padx=12, pady=6, cursor="hand2", takefocus=True)
        set_color_roles(button, background="ACCENT_SOFT", foreground="ACCENT_DEEP",
                        activebackground="ACCENT_SOFT", activeforeground="ACCENT_DEEP")
        button.grid(row=0, column=1)
        button._remchat_locale_exclude = True
        self.copy_button = button
        self.view = tk.Text(self, wrap="word", width=1, height=1, relief="flat", bd=0,
                            font=(theme.BODY_FONT, 11), padx=pixels(self, 18), pady=pixels(self, 14),
                            spacing1=pixels(self, 4), spacing3=pixels(self, 8), cursor="xterm")
        set_color_roles(self.view, background="WHITE", foreground="INK", insertbackground="INK",
                        selectbackground="ACCENT_SOFT", selectforeground="INK")
        self.view.grid(row=1, column=0, sticky="nsew", padx=(pixels(self, 18), 0), pady=(0, pixels(self, 18)))
        self.view._remchat_locale_exclude = True
        scrollbar = ttk.Scrollbar(self, command=self.view.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, pixels(self, 18)), pady=(0, pixels(self, 18)))
        self.view.configure(yscrollcommand=scrollbar.set)
        self.view.tag_configure("title", font=(theme.BODY_FONT, 15, "bold"), spacing3=pixels(self, 16))
        bind_text_editing(self.view)
        self.choice.bind("<<ComboboxSelected>>", self._show)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.choice.current(0)
        self._show()

    def refresh_guide(self, language: str) -> None:
        language = normalize_language(language)
        if language == self.guide_language:
            return
        self.guide_language = language
        labels = _GUIDE_LABELS[language]
        self.title(labels[0])
        self.copy_button.configure(text=labels[1])
        self.sections = guide_sections(language, project_root=self.project_root)
        self.choice.configure(values=[title for title, _body in self.sections])
        self.choice.current(0)
        self._show()

    def _show(self, _event=None):
        index = self.choice.current()
        if not 0 <= index < len(self.sections):
            return
        title, body = self.sections[index]
        self.view.configure(state="normal")
        self.view.delete("1.0", "end")
        self.view.insert("end", title + "\n\n", "title")
        self.view.insert("end", body)
        self.view.configure(state="disabled")
        self.view.yview_moveto(0)

    def _copy(self):
        copy_text(self, self.view.get("1.0", "end-1c"))
