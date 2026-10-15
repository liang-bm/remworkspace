"""长期记忆的分类查看与单条编辑。所有变更前生成可恢复的数据库备份。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
import tkinter as tk
from .soft_fields import SoftEntry
import tkinter.font as tkfont
from tkinter import ttk
from .localized_dialogs import messagebox

from ..memory import ConversationMemory, StoredMemory
from ..text_editing import bind_text_editing
from .locale_controller import translator_for
from .dpi import pixels
from .theme import WHITE, INK, MUTED, LINE, ACCENT_DEEP, ACCENT_SOFT, ERROR, BODY_FONT


class MemoryPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, memory: ConversationMemory, root_path: Path,
                 on_changed=lambda: None) -> None:
        super().__init__(parent, bg=WHITE)
        self.memory = memory
        self.root_path = root_path
        self.on_changed = on_changed
        self.rows: dict[int, StoredMemory] = {}
        self.selected_id: int | None = None
        self._loaded_memory: StoredMemory | None = None
        # The first option is UI, not a reserved user category name.
        self._category_keys: tuple[str | None, ...] = (None,)
        self.category = tk.StringVar(self, translator_for(self)("全部分类"))
        self.query = tk.StringVar(self, "")
        self.edit_category = tk.StringVar(self, "fact")
        self.pinned = tk.BooleanVar(self, False)
        self.status = tk.StringVar(self, "先选中一条记忆，再编辑或删除。")
        self._build()
        self.refresh()

    def _build(self) -> None:
        filters = tk.Frame(self, bg=WHITE)
        filters.pack(fill="x", pady=(4, 8))
        self._filter_layout = None
        self.category_box = ttk.Combobox(filters, textvariable=self.category,
                                        state="readonly", width=16)
        self.category_box.pack(side="left")
        self.category_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        entry = SoftEntry(filters, textvariable=self.query, font=("Microsoft YaHei UI", 10),
                         relief="flat", bg="#F1F5FA", fg=INK)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda _event: self.refresh())
        search = tk.Button(filters, text="搜索 / 刷新", command=self.refresh, bg=ACCENT_SOFT,
                           fg=ACCENT_DEEP, relief="flat", padx=10, pady=5)
        search.pack(side="right")
        def filter_layout(event):
            horizontal = event.width >= self.category_box.winfo_reqwidth() + search.winfo_reqwidth() + pixels(self, 128)
            if horizontal == self._filter_layout:
                return
            self._filter_layout = horizontal
            for widget in (self.category_box, entry, search):
                widget.pack_forget()
            self.category_box.pack(side="left" if horizontal else "top", fill="none" if horizontal else "x")
            entry.pack(side="left" if horizontal else "top", fill="x", expand=horizontal,
                       padx=8 if horizontal else 0, pady=0 if horizontal else 6)
            search.pack(side="right" if horizontal else "top", anchor="e")
        filters.bind("<Configure>", filter_layout)
        host = tk.Frame(self, bg=WHITE)
        host.pack(fill="x")
        # The platform Treeview row is commonly fixed at 20 physical pixels,
        # while its point-sized glyphs grow with DPI. Do not change the global
        # style: this memory table owns its font and measured row geometry.
        self._table_font = tkfont.Font(root=self, family=BODY_FONT, size=10)
        self._heading_font = tkfont.Font(root=self, family=BODY_FONT, size=10, weight="bold")
        self._configure_table_style()
        self.table = ttk.Treeview(host, columns=("category", "content"), show="headings",
                                 height=7, selectmode="browse", style="RemChat.Memory.Treeview")
        self.table.heading("category", text="分类")
        self.table.heading("content", text="记忆内容")
        self.table.column("category", width=pixels(self, 120), minwidth=pixels(self, 72), stretch=False)
        self.table.column("content", width=pixels(self, 400), minwidth=pixels(self, 80))
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.table.pack(side="left", fill="x", expand=True)
        self.table.bind("<<TreeviewSelect>>", self._select)
        # ttk stores settings per theme. Windows switches to clam in dark
        # mode; reapply this table's measured geometry without touching other
        # Treeviews or rebuilding the selection/editor.
        self.table.bind("<<ThemeChanged>>", lambda _event: self._configure_table_style(), add=True)
        tk.Label(self, text="编辑选中记忆", bg=WHITE, fg=INK, anchor="w",
                 font=(BODY_FONT, 11, "bold")).pack(fill="x", pady=(pixels(self, 20), 4))
        hint = tk.Label(self, text="列表仅显示单行摘要；完整内容在下方编辑，不会自动保存。",
                        bg=WHITE, fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 9))
        hint.pack(fill="x", pady=(0, 4))
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=max(100, event.width)))
        self.editor = tk.Text(self, height=4, wrap="word", bg="#F7F9FC", fg=INK,
                              font=("Microsoft YaHei UI", 10), padx=10, pady=8,
                              bd=0, highlightthickness=1, highlightbackground=LINE)
        self.editor.pack(fill="x", pady=10)
        bind_text_editing(self.editor)
        actions = tk.Frame(self, bg=WHITE)
        actions.pack(fill="x")
        ttk.Combobox(actions, textvariable=self.edit_category, width=15,
                     values=("fact", "preference", "goal", "relationship")).pack(side="left")
        tk.Checkbutton(actions, text="固定记忆", variable=self.pinned, bg=WHITE,
                       fg=INK, activebackground=WHITE).pack(side="left", padx=8)
        commands = tk.Frame(self, bg=WHITE)
        commands.pack(fill="x", pady=(6, 0))
        self._command_buttons = []
        self._command_rows = [tk.Frame(commands, bg=WHITE) for _ in range(3)]
        self._command_layout = None
        for label, command, color in (("保存修改", self._save, ACCENT_DEEP),
                                       ("重新读取选中", self._reload_selected, ACCENT_DEEP),
                                       ("删除选中", self._delete, ERROR)):
            button = tk.Button(commands, text=label, command=command, bg=WHITE, fg=color,
                               relief="flat", padx=10, pady=5)
            self._command_buttons.append(button)
            button.bind("<Configure>", lambda _event: self._layout_commands())
        commands.bind("<Configure>", lambda _event: self._layout_commands())
        self._layout_commands()
        status_label = tk.Label(self, textvariable=self.status, bg=WHITE, fg=MUTED, anchor="w",
                                justify="left", font=("Microsoft YaHei UI", 9))
        status_label.pack(fill="x", pady=(8, 0))
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(160, event.width)))

    def _configure_table_style(self) -> None:
        style = ttk.Style(self)
        settings = {
            "RemChat.Memory.Treeview": {"configure": {"font": self._table_font,
                "rowheight": self._table_font.metrics("linespace") + pixels(self, 12)}},
            "RemChat.Memory.Treeview.Heading": {"configure": {"font": self._heading_font}},
        }
        # A newly mapped dark page can receive its ThemeChanged event before
        # the Python binding runs. Prepare only our styles in every existing
        # theme, without switching the current theme or global row settings.
        for name in style.theme_names():
            style.theme_settings(name, settings)

    def _layout_commands(self) -> None:
        """Wrap full-size commands rather than clipping them at narrow widths."""
        host = self._command_buttons[0].master
        width = max(1, host.winfo_width())
        row = column = used = 0
        assignments = []
        for button in self._command_buttons:
            requested = button.winfo_reqwidth() + 6
            if column and used + requested > width:
                row += 1
                column = used = 0
            assignments.append(row)
            used += requested
            column += 1
        signature = tuple(assignments)
        if signature == self._command_layout:
            return
        self._command_layout = signature
        for button in self._command_buttons:
            button.pack_forget()
        for frame in self._command_rows:
            frame.pack_forget()
        for index in range(row + 1):
            self._command_rows[index].pack(fill="x")
        for button, index in zip(self._command_buttons, assignments):
            button.pack(in_=self._command_rows[index], side="left", padx=(0, 6), pady=2)

    def refresh(self) -> None:
        try:
            all_rows = self.memory.list_memories()
            categories = (None, *sorted({row.category for row in all_rows}))
            index = self.category_box.current()
            category = self._category_keys[index] if 0 <= index < len(self._category_keys) else None
            if category not in categories:
                category = None
            rows = self.memory.list_memories(
                category=category if category is not None else "",
                query=self.query.get(),
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.set(f"读取记忆失败，列表与草稿已保留；可重试：{error}")
            return
        self._category_keys = categories
        self.retranslate(selected_index=categories.index(category))
        selected = self.selected_id
        dirty = self._is_dirty()
        self.rows = {row.id: row for row in rows}
        self.table.delete(*self.table.get_children())
        for row in rows:
            # Presentation only: multiline originals stay in StoredMemory and
            # _load_editor. A Treeview row has no per-item auto-height.
            preview = " ".join(row.content.splitlines())
            self.table.insert("", "end", iid=str(row.id),
                              values=(row.category, ("★ " if row.pinned else "") + preview))
        if selected in self.rows:
            self.table.selection_set(str(selected))
        elif not dirty:
            self._clear_editor()
        status = f"显示 {len(rows)} / {len(all_rows)} 条 · 数据库：{self.memory.path}"
        if dirty:
            status += "\n" + ("当前编辑项不在筛选结果中；未保存草稿已保留。" if selected not in self.rows else "未保存草稿已保留；保存时会核对数据库原内容。")
        self.status.set(status)

    def retranslate(self, *, selected_index: int | None = None) -> None:
        """Update only the UI option; never translate user category values."""
        index = self.category_box.current() if selected_index is None else selected_index
        labels = (translator_for(self)("全部分类"), *self._category_keys[1:])
        self.category_box.configure(values=labels)
        self.category_box.current(index if 0 <= index < len(labels) else 0)

    def _is_dirty(self) -> bool:
        row = self._loaded_memory
        return row is not None and (self.editor.get("1.0", "end-1c") != row.content or
                                    self.edit_category.get() != row.category or self.pinned.get() != row.pinned)

    def _clear_editor(self) -> None:
        self.selected_id = None
        self._loaded_memory = None
        self.editor.delete("1.0", "end")

    def _load_editor(self, row: StoredMemory) -> None:
        self.selected_id = row.id
        self._loaded_memory = row
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", row.content)
        self.edit_category.set(row.category)
        self.pinned.set(row.pinned)

    def _select(self, _event=None) -> None:
        selection = self.table.selection()
        if not selection:
            return
        row = self.rows.get(int(selection[0]))
        if row is None:
            return
        if self._is_dirty():
            if row.id == self.selected_id:
                return  # Programmatic refresh selection is not permission to discard.
            tr = translator_for(self)
            if not messagebox.askyesno(tr("切换记忆"), tr("当前有未保存修改。切换会放弃这份草稿，是否继续？"), parent=self.winfo_toplevel()):
                self.table.selection_remove(*self.table.selection())
                if self.selected_id in self.rows:
                    self.table.selection_set(str(self.selected_id))
                return
        self._load_editor(row)

    def _reload_selected(self) -> None:
        if self.selected_id is None:
            self.status.set("请先选中一条记忆。")
            return
        try:
            row = next((row for row in self.memory.list_memories() if row.id == self.selected_id), None)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.set(f"读取记忆失败，列表与草稿已保留；可重试：{error}")
            return
        if row is None:
            self.status.set("这条记忆已不存在，原稿保留；可先复制内容再选择其他记忆。")
            return
        tr = translator_for(self)
        if self._is_dirty() and not messagebox.askyesno(tr("重新读取记忆"), tr("重新读取会放弃未保存修改，是否继续？"), parent=self.winfo_toplevel()):
            return
        self._load_editor(row)
        self.refresh()
        self.status.set("已重新读取选中记忆，未改动数据库。")

    def _backup(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return self.memory.backup(self.root_path / "data" / "backups" / f"memory-{stamp}.sqlite3")

    def _save(self) -> None:
        if self.selected_id is None or self._loaded_memory is None:
            self.status.set("请先选中一条记忆。")
            return
        content = self.editor.get("1.0", "end-1c").strip()
        category = self.edit_category.get().strip()
        if not content or not category:
            self.status.set("内容和分类不能为空。")
            return
        try:
            backup = self._backup()
            self.memory.update_memory(self.selected_id, content=content,
                                      category=category, pinned=self.pinned.get(), expected=self._loaded_memory)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.set(str(error))
            return
        baseline = self._loaded_memory
        self._load_editor(StoredMemory(self.selected_id, content, category, baseline.importance, self.pinned.get()))
        self.refresh()
        self.status.set(f"已保存，下一轮生效。恢复备份：{backup}")
        self.on_changed()

    def _delete(self) -> None:
        if self.selected_id is None or self._loaded_memory is None:
            self.status.set("请先选中一条记忆。")
            return
        tr = translator_for(self)
        if not messagebox.askyesno(tr("删除记忆"), tr("只删除这条长期记忆，聊天记录保持不变。\n删除前会保存数据库备份。"),
                                  parent=self.winfo_toplevel()):
            return
        try:
            backup = self._backup()
            if not self.memory.delete_memory(self.selected_id, expected=self._loaded_memory):
                raise ValueError("这条记忆已改变或不存在，未删除。请重新读取选中记忆。")
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.set(str(error))
            return
        self._clear_editor()
        self.refresh()
        self.status.set(f"已删除选中记忆，可从备份恢复：{backup}")
        self.on_changed()
