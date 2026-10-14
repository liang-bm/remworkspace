"""Caret-anchored local suggestions; keyboard selection never submits a reply."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
import tkinter as tk

from ..composer_tokens import active_token, workspace_names
from . import theme
from .dpi import pixels


@dataclass(frozen=True)
class Choice:
    value: str
    title: str
    detail: str


COMMANDS = (
    ('help', '快捷交互帮助', '查看 @ 引用与 / 指令'),
    ('new', '新对话', '保留当前会话，开始新对话'),
    ('attach', '添加文件或图像', '仅发送本轮选中的内容'),
    ('workspace', '选择工作空间', '为 @ 文件引用选择项目目录'),
    ('skills', '技能库', '阅读并管理技能，不执行脚本'),
    ('mcp', 'MCP 服务', '查看连接，执行权留在你手中'),
    ('monitor', '任务监工', '接收真实任务事件与完成提醒'),
    ('models', '模型设置', '本地模型或自定义 API'),
    ('settings', '设置', '打开应用设置'),
)


class ComposerShortcuts:
    def __init__(self, owner):
        self.owner = owner
        self.text = owner.input_text
        self.popup = None
        self.choices = []
        self.token = None
        self.selected = 0
        self.closed = False
        self._generation = 0
        self._workspace = None
        self._names = ()
        self._indexed_at = 0.0
        self._indexing = False
        self._stop = threading.Event()
        self._results = queue.Queue()
        self._pending = None
        self._bindings = []
        for sequence, callback in (
            ('<KeyRelease>', self._schedule), ('<ButtonRelease-1>', self._schedule),
            ('<Up>', lambda event: self._move(-1)), ('<Down>', lambda event: self._move(1)),
            ('<Tab>', self._tab), ('<Escape>', self._escape),
            ('<FocusOut>', self._focus_out),
        ):
            self._bindings.append((sequence, self.text.bind(sequence, callback, add='+')))
        self._poll_id = self.text.after(100, self._poll)

    def _schedule(self, event=None):
        if self.closed or getattr(event, 'keysym', '') in {'Return', 'Tab', 'Up', 'Down', 'Escape'}:
            return
        if self._pending is not None:
            self.text.after_cancel(self._pending)
        self._pending = self.text.after(120, self.refresh)

    def refresh(self):
        self._pending = None
        if self.closed or self.owner.busy or self.owner._closing or not self.text.winfo_viewable():
            self.hide()
            return
        value = self.text.get('1.0', 'end-1c')
        cursor = len(self.text.get('1.0', 'insert'))
        self.token = active_token(value, cursor)
        if self.token is None:
            self.hide()
            return
        query = self.token.query.casefold()
        choices = []
        if self.token.prefix == '/':
            choices += [Choice('/' + name, '/' + name + '  ' + self.owner.translator(title), self.owner.translator(detail))
                        for name, title, detail in COMMANDS]
            try:
                choices += [Choice('/skill:' + row['id'], '/' + row['id'], str(row.get('description', '')))
                            for row in self.owner.integration_manager.skills.list() if row.get('enabled')]
            except (OSError, ValueError):
                pass
        else:
            self._ensure_index()
            choices += [Choice('@"' + name + '"', name, self.owner.translator('工作空间文件 · 仅本轮只读引用')) for name in self._names]
            try:
                choices += [Choice('@chat:' + str(row.id), self.owner.translator('会话') + ' · ' + row.title,
                                   self.owner.translator('引用最近 20 条消息，不切换会话'))
                            for row in self.owner.service.memory.list_conversations(limit=20)]
            except (OSError, ValueError):
                pass
        query = query.removeprefix('skill:')
        choices = [item for item in choices if not query or query in item.title.casefold() or query in item.value.casefold() or query in item.detail.casefold()]
        choices.sort(key=lambda item: (not item.value.lstrip('/@"').casefold().startswith(query), len(item.title)))
        self.choices = choices[:8]
        self.selected = 0
        self._render()

    def _ensure_index(self):
        workspace = self.owner.workspace_directory
        if workspace != self._workspace:
            self._generation += 1
            self._stop.set()
            self._stop = threading.Event()
            self._workspace = workspace
            self._names = ()
            self._indexing = False
            self._indexed_at = 0.0
        if workspace is None or self._indexing or time.monotonic() - self._indexed_at < 10:
            return
        self._indexing = True
        generation, stop = self._generation, self._stop
        def index():
            try:
                names = workspace_names(workspace, stop)
                error = ''
            except OSError as exception:
                names, error = (), str(exception)
            self._results.put((generation, names, error))
        threading.Thread(target=index, daemon=True, name='remchat-reference-index').start()

    def _poll(self):
        self._poll_id = None
        if self.closed:
            return
        try:
            while True:
                generation, names, error = self._results.get_nowait()
                if generation != self._generation or self._workspace != self.owner.workspace_directory:
                    continue
                self._indexing = False
                self._indexed_at = time.monotonic()
                self._names = names
                if error:
                    self.owner._set_status(error, error=True)
                if self.token and self.token.prefix == '@' and self.text.focus_get() is self.text:
                    self.refresh()
        except queue.Empty:
            pass
        self._poll_id = self.text.after(100, self._poll)

    def _render(self):
        self.hide()
        colors = theme.palette()
        self.popup = tk.Toplevel(self.owner.root)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.transient(self.owner.root)
        self.popup._remchat_locale_exclude = True
        self.popup.configure(bg=colors['LINE'])
        shell = tk.Frame(self.popup, bg=colors['WHITE'], padx=12, pady=8)
        shell.pack(fill='both', expand=True, padx=1, pady=1)
        heading = '指令与已启用技能' if self.token.prefix == '/' else '文件与会话引用'
        tk.Label(shell, text=self.owner.translator(heading), bg=colors['WHITE'], fg=colors['MUTED'],
                 font=(theme.BODY_FONT, 9), anchor='w').pack(fill='x', pady=(0, 6))
        self._rows = []
        for index, item in enumerate(self.choices):
            button = tk.Button(shell, text=item.title + '\n' + item.detail, anchor='w', justify='left',
                wraplength=pixels(self.text, 410), font=(theme.BODY_FONT, 10), bd=0, padx=10, pady=6,
                bg=colors['WHITE'], fg=colors['INK'], activeforeground=colors['INK'],
                activebackground=colors['ACCENT_SOFT'], command=lambda n=index: self.accept(n),
                cursor='hand2', takefocus=True)
            button.pack(fill='x', pady=1)
            self._rows.append(button)
        if not self.choices:
            message = '正在读取文件列表…' if self._indexing else ('请先选择工作空间，再使用 @ 文件引用' if self.token.prefix == '@' and self._workspace is None else '没有匹配项')
            tk.Label(shell, text=self.owner.translator(message), bg=colors['WHITE'], fg=colors['MUTED'],
                     font=(theme.BODY_FONT, 10), wraplength=pixels(self.text, 400)).pack(fill='x', pady=8)
        tk.Label(shell, text=self.owner.translator('↑↓ 选择 · Tab/Enter 补全 · Esc 关闭'),
                 bg=colors['WHITE'], fg=colors['MUTED'], font=(theme.BODY_FONT, 8)).pack(anchor='w', pady=(8, 0))
        self._highlight()
        self.popup.update_idletasks()
        width = min(max(pixels(self.text, 380), self.popup.winfo_reqwidth()), self.owner.root.winfo_screenwidth())
        height = min(self.popup.winfo_reqheight(), self.owner.root.winfo_screenheight())
        x = min(max(0, self.text.winfo_rootx()), max(0, self.text.winfo_screenwidth() - width))
        y = max(0, self.text.winfo_rooty() - height - 4)
        self.popup.geometry(f'{width}x{height}+{x}+{y}')
        self.popup.deiconify()

    def _highlight(self):
        colors = theme.palette()
        for index, row in enumerate(getattr(self, '_rows', ())):
            row.configure(bg=colors['ACCENT_SOFT'] if index == self.selected else colors['WHITE'])

    def _move(self, offset):
        if self.popup is None or not self.choices:
            return None
        self.selected = (self.selected + offset) % len(self.choices)
        self._highlight()
        return 'break'

    def _tab(self, _event=None):
        return 'break' if self.accept() else None

    def _escape(self, _event=None):
        if self.popup is not None:
            if self._pending is not None:
                self.text.after_cancel(self._pending)
                self._pending = None
            self.hide()
            self.token = None
            return 'break'
        return None

    def _focus_out(self, _event=None):
        self.text.after_idle(self._dismiss_unfocused)

    def _dismiss_unfocused(self):
        if self.closed:
            return
        focus = self.text.focus_get()
        if focus is not self.text and (not self.popup or not focus or focus.winfo_toplevel() is not self.popup):
            self.hide()

    def accept(self, index=None):
        if not self.popup or not self.choices or self.token is None:
            return False
        if not self.owner._context_mutation_allowed():
            return True
        item = self.choices[self.selected if index is None else index]
        # A delayed click may be looking at an old token: never replace unrelated input.
        value = self.text.get('1.0', 'end-1c')
        current = active_token(value, len(self.text.get('1.0', 'insert')))
        if current != self.token:
            self.hide()
            return True
        if item.value.startswith('@'):
            from ..composer_tokens import prepare_references
            try:
                snapshot = prepare_references(
                    item.value, self.owner._context_snapshot(),
                    workspace=self.owner.workspace_directory,
                    memory=self.owner.service.memory, skills=self.owner.integration_manager.skills,
                    root_path=self.owner.service.config.root,
                )
                provider = self.owner.service.provider_repository.load().active_provider
                snapshot.validate(supports_images=provider.supports_images)
            except (OSError, ValueError, KeyError) as error:
                self.owner._set_status(str(error), error=True)
                return True
            self.owner.attachments = list(snapshot.attachments)
            self.owner._refresh_context_controls()
        def index(offset):
            # Text's '+Nc' walks characters, unlike Tcl string length's
            # surrogate units. Keep the caret-derived Python character offset.
            return f'1.0+{offset}c'
        start = index(current.start)
        self.text.delete(start, index(current.end))
        self.text.insert(start, item.value + ' ')
        added = len(item.value + ' ')
        self.text.mark_set('insert', f'{start}+{added}c')
        self.hide()
        self.text.focus_set()
        if item.value.startswith('@'):
            self.owner._set_status('引用快照已添加；可从＋查看，API 会收到引用内容')
        return True

    def hide(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None

    def reset(self):
        if self._pending is not None:
            self.text.after_cancel(self._pending)
            self._pending = None
        self.hide()
        self.token = None
        self._generation += 1
        self._stop.set()
        self._stop = threading.Event()
        self._workspace = None
        self._names = ()
        self._indexing = False
        self._indexed_at = 0.0

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.reset()
        for handle in (self._pending, self._poll_id):
            if handle is not None:
                try:
                    self.text.after_cancel(handle)
                except tk.TclError:
                    pass
        for sequence, identifier in self._bindings:
            if identifier:
                self.text.unbind(sequence, identifier)

    def run_local(self, text):
        command = text.strip()
        mapping = {
            '/new': self.owner._new_conversation,
            '/attach': self.owner._choose_attachments,
            '/workspace': self.owner._choose_workspace,
            '/models': self.owner._open_context_models,
            '/settings': self.owner._show_settings,
            '/skills': lambda: self._open_integration('skills'),
            '/mcp': lambda: self._open_integration('mcp'),
            '/monitor': lambda: self._open_integration('tasks'),
            '/help': lambda: self.owner._set_status('@ 引用项目文件或会话；/ 调用本地指令或已启用技能。技能仅参考，不执行脚本。'),
        }
        action = mapping.get(command)
        if action is None:
            head = command.split(maxsplit=1)[0]
            if head in mapping:
                self.owner._set_status('此指令不接受参数；请单独输入指令，草稿已保留', error=True)
                return True
            if head.startswith('/') and '/' not in head[1:] and not head.startswith('/skill:'):
                self.owner._set_status('未知指令；输入 / 查看本地指令与已启用技能', error=True)
                return True
            return False
        self.hide()
        self.text.delete('1.0', 'end')
        action()
        return True

    def _open_integration(self, key):
        self.owner._show_project_management(section=key)
