"""Personality editor: explicit saves, recoverable reloads, no implicit writes."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from ..companion_preferences import open_in_vscode
from ..global_instruction import (
    InstructionError, InstructionSnapshot, instruction_path,
    read_instruction, save_instruction,
)
from ..text_editing import bind_text_editing
from .locale_controller import translator_for
from .theme import BODY_FONT
from .theme_controller import set_color_roles


class InstructionPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, root_path: Path, fallback: str) -> None:
        super().__init__(parent)
        set_color_roles(self, background='WHITE')
        self.root_path = root_path
        self.fallback = fallback.strip()
        self._snapshot: InstructionSnapshot | None = None
        # A failed initial read leaves an untouched empty editor, not a draft.
        self._loaded_text = ''
        self.status = tk.StringVar(self, '修改会从下一轮对话开始生效')
        self._build()
        self._load()

    def _build(self) -> None:
        host = tk.Frame(self)
        set_color_roles(host, background='WHITE')
        host.pack(fill='x')
        scrollbar = tk.Scrollbar(host)
        set_color_roles(scrollbar, background='LINE', activebackground='ACCENT_DEEP',
                        troughcolor='APP_BG', highlightbackground='WHITE')
        scrollbar.pack(side='right', fill='y')
        self.text = tk.Text(host, relief='flat', bd=0, highlightthickness=1,
                            wrap='word', height=8, width=1, padx=12, pady=10,
                            font=(BODY_FONT, 10), undo=True, yscrollcommand=scrollbar.set)
        set_color_roles(self.text, background='APP_BG', foreground='INK', insertbackground='ACCENT_DEEP',
                        selectbackground='ACCENT_DEEP', selectforeground='ON_ACCENT',
                        highlightbackground='LINE', highlightcolor='ACCENT_DEEP')
        self.text.pack(side='left', fill='x', expand=True)
        scrollbar.configure(command=self.text.yview)
        bind_text_editing(self.text)
        commands = tk.Frame(self)
        set_color_roles(commands, background='WHITE')
        commands.pack(fill='x', pady=(10, 0))
        # Independent rows prevent a long button in a later row from widening
        # the grid columns of every earlier row.
        self._command_rows = [tk.Frame(commands) for _ in range(3)]
        for row in self._command_rows:
            set_color_roles(row, background='WHITE')
        self._command_layout: tuple[int, ...] | None = None
        self._buttons = []
        for label, command, primary in (('保存指令', self.save, True),
                                         ('重新读取指令', self.reload, False),
                                         ('VS Code 打开', self.open_editor, False)):
            button = tk.Button(commands, text=label, command=command,
                               relief='flat', bd=0, padx=12, pady=8, cursor='hand2',
                               font=(BODY_FONT, 9, 'bold'), highlightthickness=1)
            set_color_roles(button, background='ACCENT_DEEP' if primary else 'ACCENT_SOFT',
                            foreground='ON_ACCENT' if primary else 'ACCENT_DEEP',
                            activebackground='ACCENT_DEEP', activeforeground='ON_ACCENT',
                            disabledforeground='MUTED', highlightbackground='LINE',
                            highlightcolor='ACCENT_DEEP')
            button.bind('<Configure>', lambda _event: self._layout_commands())
            self._buttons.append(button)
        self.save_button, self.reload_button, self.open_button = self._buttons
        commands.bind('<Configure>', lambda _event: self._layout_commands())
        self._layout_commands()
        self.status_label = tk.Label(self, textvariable=self.status,
                                     anchor='w', justify='left', font=(BODY_FONT, 9))
        set_color_roles(self.status_label, background='WHITE', foreground='MUTED')
        self.status_label.pack(fill='x', pady=(10, 0))
        self.status_label.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(1, event.width)))

    def _layout_commands(self) -> None:
        width = max(1, self._buttons[0].master.winfo_width())
        row = used = 0
        assignments = []
        for button in self._buttons:
            requested = button.winfo_reqwidth() + 6
            if used and used + requested > width:
                row += 1
                used = 0
            assignments.append(row)
            used += requested
        layout = tuple(assignments)
        if layout == self._command_layout:
            return
        self._command_layout = layout
        for button in self._buttons:
            button.pack_forget()
        for host in self._command_rows:
            host.pack_forget()
        for host in self._command_rows[:row + 1]:
            host.pack(fill='x')
        for button, assigned in zip(self._buttons, assignments):
            button.pack(in_=self._command_rows[assigned], side='left', padx=(0, 6), pady=2)

    def _feedback(self, text: str, *, error: bool = False, saved: bool = False) -> None:
        self.status.set(text)
        set_color_roles(self.status_label, foreground='ERROR' if error else 'SUCCESS' if saved else 'MUTED')

    def _load(self) -> bool:
        try:
            snapshot = read_instruction(self.root_path)
        except InstructionError as error:
            self._feedback(str(error), error=True)
            if self._snapshot is None:
                self.save_button.configure(state='disabled')
            return False
        except (OSError, ValueError, UnicodeError) as error:
            self._feedback(f'读取人格指令失败，文件与草稿已保留；修复后重试：{error}', error=True)
            if self._snapshot is None:
                self.save_button.configure(state='disabled')
            return False
        content = snapshot.content if snapshot.revision is not None else self.fallback
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', content)
        self.text.edit_reset()
        self._loaded_text = content
        self._snapshot = snapshot
        self.save_button.configure(state='normal')
        self._feedback('已重新读取人格指令，未修改文件。' if snapshot.revision is not None
                       else '人格文件尚未创建；保存后才会写入，当前显示默认指令。')
        return True

    def is_dirty(self) -> bool:
        return self.text.get('1.0', 'end-1c') != self._loaded_text

    def reload(self) -> bool:
        translate = translator_for(self)
        if self.is_dirty() and not messagebox.askyesno(
            translate('重新读取指令'),
            translate('重新读取会放弃未保存修改，是否继续？'), parent=self.winfo_toplevel(),
        ):
            return False
        return self._load()

    def save(self) -> bool:
        if self._snapshot is None:
            self._feedback('尚未成功读取人格文件，未保存。请修复后重新读取。', error=True)
            return False
        try:
            result = save_instruction(self.root_path, self.text.get('1.0', 'end-1c'), self._snapshot)
        except InstructionError as error:
            self._feedback(str(error), error=True)
            return False
        except (OSError, ValueError, UnicodeError) as error:
            self._feedback(f'人格指令未保存，界面草稿已保留：{error}', error=True)
            return False
        self._snapshot = result.snapshot
        # Preserve the editor's whitespace and caret after a successful save.
        self._loaded_text = self.text.get('1.0', 'end-1c')
        if result.backup is not None:
            self._feedback(f'已保存，将从下一轮对话生效。原文件备份：{result.backup}', saved=True)
        else:
            self._feedback('已保存，将从下一轮对话生效', saved=True)
        return True

    def open_editor(self) -> bool:
        try:
            path = instruction_path(self.root_path)
            if not path.is_file():
                self._feedback('人格文件尚未创建，请先保存指令；界面草稿未改动。', error=True)
                return False
            open_in_vscode(path)
        except InstructionError as error:
            self._feedback(str(error), error=True)
            return False
        except (OSError, ValueError) as error:
            self._feedback(f'无法打开人格文件，文件与草稿未改动：{error}', error=True)
            return False
        self._feedback('已请求 VS Code 打开文件；界面草稿未保存。外部修改后请重新读取。')
        return True
