"""Display-only live Markdown, with native Text retaining input and undo.

The worker sees immutable strings, never Tk. Existing rich-format tags are
untouched. Completed syntax is hidden, not deleted; only a caret actually
inside a syntax marker reveals it. Small code headers live in the viewport,
not in Text's document, so clipboard, emoji and persistence offsets stay exact.
"""
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import tkinter as tk
import tkinter.font as tkfont
from .font_source import native_font
import webbrowser
from . import theme
from .dpi import pixels
from .journal_markdown import PREFIX, display_plan
from .journal_syntax import COLOURS, language_name

_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix='journal-display')

class WritingSurface(tk.Frame):
    """Single pane; legacy DiaryPanel's notebook calls remain harmless."""
    def __init__(self, parent):
        super().__init__(parent, bg=theme.WHITE, bd=0)
        self._pages = []
        self.columnconfigure(0, weight=1); self.rowconfigure(0, weight=1)
    def add(self, page, **_options):
        self._pages.append(page)
        if len(self._pages) == 1: page.grid(row=0, column=0, sticky='nsew')
    def hide(self, page): page.grid_remove()
    def tab(self, *_args, **_options): return {}
    def select(self, *_args): return str(self._pages[0]) if self._pages else ''
    def index(self, _page): return 0
    def configure(self, cnf=None, **options):
        if isinstance(cnf, dict): options = {**cnf, **options}; cnf = None
        options.pop('style', None)
        return super().configure(cnf, **options)
    config = configure

class JournalLiveDisplay:
    def __init__(self, editor):
        self.editor = editor
        self.plan = None; self._epoch = 0; self._closed = False
        self._parse_job = self._poll_job = self._view_job = None
        self._future = None; self._headers = {}; self._markers = {}
        self._fonts = []; self._revealed = None
        self._original = editor._w + '_journal_source'
        editor.tk.call('rename', editor._w, self._original)
        editor.tk.createcommand(editor._w, self._proxy)
        for sequence in ('<Configure>', '<Map>', '<KeyRelease>', '<ButtonRelease-1>',
                         '<<Selection>>', '<FocusIn>', '<FocusOut>'):
            editor.bind(sequence, self.schedule_view, add=True)
        editor.bind('<Destroy>', self._destroy, add=True)
        editor.bind('<Control-Button-1>', self._open_link, add=True)
        self.refresh_formats(); self.changed()

    def _proxy(self, *arguments):
        result = self.editor.tk.call(self._original, *arguments)
        command = arguments[0] if arguments else ''
        if command in ('insert', 'delete', 'replace') or (
                command == 'edit' and len(arguments) > 1 and arguments[1] in ('undo', 'redo')):
            self.changed()
        elif command in ('yview', 'xview', 'see') or (
                command == 'mark' and len(arguments) > 2 and arguments[1:3] == ('set', 'insert')):
            if command in ('yview','xview') and len(arguments)>1 and hasattr(self.editor,'_selection_tools'):
                self.editor._selection_tools.dismiss()
            self.schedule_view()
        return result

    def refresh_formats(self):
        editor = self.editor
        base = native_font(editor,font=editor.cget('font'))
        family, size = base.cget('family'), int(base.cget('size'))
        direction=1 if size>=0 else -1
        self._fonts = []
        def font(*values):
            value = native_font(editor,font=values); self._fonts.append(value); return value
        configure = editor.tag_configure
        configure(PREFIX + 'hidden', elide=True)
        configure(PREFIX + 'bold', font=font(family, size, 'bold'))
        configure(PREFIX + 'italic', font=font(family, size, 'italic'))
        configure(PREFIX + 'bolditalic', font=font(family, size, 'bold', 'italic'))
        configure(PREFIX + 'strike', overstrike=True)
        configure(PREFIX + 'highlight', background='#665528' if theme.get_mode() == 'dark' else '#FFF1A8')
        for level, delta in enumerate((8, 6, 4, 3, 2, 1), 1):
            configure(PREFIX + f'h{level}', font=font(family, size + delta*direction, 'bold'),
                      spacing1=pixels(editor, 12), spacing3=pixels(editor, 7))
        configure(PREFIX + 'quote', foreground=theme.MUTED, lmargin1=pixels(editor, 20),
                  lmargin2=pixels(editor, 20), spacing1=5, spacing3=5)
        configure(PREFIX + 'bullet', lmargin1=pixels(editor, 28), lmargin2=pixels(editor, 28))
        self.code_background = '#242A34' if theme.get_mode() == 'dark' else '#EEF3F8'
        configure(PREFIX + 'inlinecode', font=font('Consolas', size), background=self.code_background)
        configure(PREFIX + 'code', font=font('Consolas', size), background=self.code_background,
                  foreground=theme.INK, lmargin1=pixels(editor, 16), lmargin2=pixels(editor, 16),
                  rmargin=pixels(editor, 16), spacing1=0, spacing3=0, wrap='char', tabs=(pixels(editor, 36),))
        configure(PREFIX + 'head', font=font('Consolas', direction*max(9,abs(size)-1)),
                  spacing1=pixels(editor, 5), spacing3=pixels(editor, 5))
        configure(PREFIX + 'foot', font=font('Consolas', 3), spacing1=1, spacing3=pixels(editor, 6))
        configure(PREFIX+'literal_head',spacing1=pixels(editor,32))
        configure(PREFIX+'literal_foot',spacing3=pixels(editor,10))
        configure(PREFIX + 'link', foreground=theme.ACCENT, underline=True)
        configure(PREFIX + 'rule', foreground=theme.LINE)
        for category, colour in COLOURS[theme.get_mode()].items():
            configure(PREFIX + 'syntax:' + category, foreground=colour)
        editor.tag_raise('sel'); self.schedule_view()

    def changed(self):
        if self._closed: return
        self._epoch += 1
        if self._parse_job is None: self._parse_job = self.editor.after_idle(self._submit)
        self.schedule_view()

    def reset(self):
        self.plan = None; self._revealed = None
        for name in self.editor.tag_names():
            if name.startswith(PREFIX): self.editor.tag_remove(name, '1.0', 'end')
        self.changed()

    def _submit(self):
        self._parse_job = None
        if self._closed: return
        editor = self.editor; epoch = self._epoch
        source = editor.get('1.0', 'end-1c')
        ranges = editor.tag_ranges('code')
        literal = tuple((len(editor.get('1.0', a)), len(editor.get('1.0', b))) for a, b in zip(ranges[::2], ranges[1::2]))
        if self._future is not None: self._future.cancel()
        self._future = _WORKERS.submit(display_plan, source, literal, lambda: self._closed or epoch != self._epoch)
        self._submitted_epoch = epoch
        if self._poll_job is None: self._poll_job = editor.after(16, self._poll)

    def _poll(self):
        self._poll_job = None
        if self._closed or self._future is None: return
        if not self._future.done():
            self._poll_job = self.editor.after(16, self._poll); return
        future = self._future; self._future = None
        if future.cancelled() or self._submitted_epoch != self._epoch: return
        try: plan = future.result()
        except (ValueError, RuntimeError): return  # Raw source is always editable.
        if plan is not None: self._apply(plan)

    def _apply(self, plan):
        previous = self.plan; editor = self.editor
        prefix = suffix = 0
        if previous:
            old, new = previous.chunks, plan.chunks
            while prefix < min(len(old), len(new)) and old[prefix].key == new[prefix].key: prefix += 1
            while suffix < min(len(old), len(new)) - prefix and old[-1 - suffix].key == new[-1 - suffix].key: suffix += 1
            # Text inherits neighbouring tags when typing at an edge. Reapply
            # one adjacent chunk, not every line after the insertion point.
            prefix = max(0, prefix - 1); suffix = max(0, suffix - 1)
        changed = plan.chunks[prefix:len(plan.chunks) - suffix if suffix else None]
        start = changed[0].start if changed else 0
        end = changed[-1].end if changed else len(plan.source)
        for name in editor.tag_names():
            if name.startswith(PREFIX): editor.tag_remove(name, plan.index(start), plan.index(end))
        grouped = defaultdict(list)
        for chunk in changed:
            for tag, a, b in chunk.styles:
                if a < b: grouped[tag].extend((plan.index(a), plan.index(b)))
            for a, b in chunk.hidden:
                if a < b: grouped[PREFIX + 'hidden'].extend((plan.index(a), plan.index(b)))
        for tag, ranges in grouped.items():
            for index in range(0, len(ranges), 512): editor.tag_add(tag, *ranges[index:index + 512])
        self.plan = plan; self._revealed = None
        editor.tag_raise('sel'); self.schedule_view()
        if hasattr(editor, '_color_emoji'): editor._color_emoji.schedule()
        editor.event_generate('<<JournalRendered>>', when='tail')

    def schedule_view(self, _event=None):
        if not self._closed and self._view_job is None:
            self._view_job = self.editor.after_idle(self._view)

    def _caret_offset(self):
        editor = self.editor
        if editor.cget('state') != 'normal' or editor.focus_get() is not editor: return -1
        return len(editor.get('1.0', 'insert'))

    def _view(self):
        self._view_job = None
        editor, plan = self.editor, self.plan
        if self._closed or not plan: return
        # Never position obsolete headers against a newer text/IME transaction.
        if self._future is not None or self._parse_job is not None:
            for widget in (*self._headers.values(), *self._markers.values()): widget.place_forget()
            return
        caret = self._caret_offset()
        reveal = next(((a, b) for chunk in plan.chunks for a, b in chunk.hidden if a <= caret < b), None)
        if reveal != self._revealed:
            if self._revealed:
                editor.tag_add(PREFIX + 'hidden', plan.index(self._revealed[0]), plan.index(self._revealed[1]))
            if reveal: editor.tag_remove(PREFIX + 'hidden', plan.index(reveal[0]), plan.index(reveal[1]))
            self._revealed = reveal
        visible = set()
        if editor.winfo_ismapped():
            for number, code in enumerate(plan.codes):
                if code.literal:
                    box=editor.bbox(plan.index(code.start))
                    first=(box[0],box[1]-pixels(editor,31),box[2],pixels(editor,28)) if box else None
                    endbox=editor.bbox(plan.index(max(code.start,code.end-1)))
                    last=(endbox[0],endbox[1]+endbox[3],endbox[2],pixels(editor,9)) if endbox else None
                else:
                    first = editor.dlineinfo(plan.index(code.start))
                    last = editor.dlineinfo(plan.index(code.closing)) if code.closing is not None else None
                if first and (code.literal or not (code.start <= caret < code.code_start)):
                    key = (number, 'head'); visible.add(key)
                    header = self._headers.get(key)
                    if header is None:
                        header = tk.Canvas(editor, bd=0, highlightthickness=0, cursor='hand2', takefocus=True)
                        header.bind('<Button-1>', lambda event, n=number: self.copy_code(n))
                        header.bind('<Return>', lambda event, n=number: self.copy_code(n))
                        self._headers[key] = header
                    self._draw_header(header, code, first, footer=False)
                if last and (code.literal or not (code.closing <= caret < code.end)):
                    key = (number, 'foot'); visible.add(key)
                    footer = self._headers.get(key)
                    if footer is None:
                        footer = tk.Canvas(editor, bd=0, highlightthickness=0, takefocus=False)
                        self._headers[key] = footer
                    self._draw_header(footer, code, last, footer=True)
        for key, widget in tuple(self._headers.items()):
            if key not in visible: widget.destroy(); del self._headers[key]
        self._draw_bullets(caret)

    def _draw_header(self, widget, code, line, *, footer):
        editor = self.editor
        padding = editor.winfo_pixels(editor.cget('padx'))
        width = max(1, editor.winfo_width() - padding * 2 - pixels(editor, 16))
        height = max(1, line[3]); radius = min(pixels(editor, 10), height)
        widget.configure(bg=editor.cget('bg'), width=width, height=height)
        widget.delete('all'); bg = self.code_background
        if footer:
            widget.create_rectangle(0, 0, width, max(0, height - radius), fill=bg, outline='')
            widget.create_rectangle(radius, 0, width - radius, height, fill=bg, outline='')
            widget.create_oval(0, height - 2 * radius, 2 * radius, height, fill=bg, outline='')
            widget.create_oval(width - 2 * radius, height - 2 * radius, width, height, fill=bg, outline='')
        else:
            widget.create_rectangle(0, radius, width, height, fill=bg, outline='')
            widget.create_rectangle(radius, 0, width - radius, height, fill=bg, outline='')
            widget.create_oval(0, 0, 2 * radius, 2 * radius, fill=bg, outline='')
            widget.create_oval(width - 2 * radius, 0, width, 2 * radius, fill=bg, outline='')
            widget.create_text(pixels(editor, 16), height / 2, text=language_name(code.language),
                               fill=theme.MUTED, anchor='w', font=(theme.BODY_FONT, 9))
            widget.create_text(width - pixels(editor, 13), height / 2, text='复制', fill=theme.ACCENT,
                               anchor='e', font=(theme.BODY_FONT, 9))
        widget.place(x=padding, y=line[1], width=width, height=height, bordermode='ignore')

    def _draw_bullets(self, caret):
        editor, plan = self.editor, self.plan; visible = set()
        for number, (start, end, prefix) in enumerate(plan.bullets):
            line = editor.dlineinfo(plan.index(start))
            if not line or start <= caret < end: continue
            visible.add(number); marker = self._markers.get(number)
            if marker is None:
                marker = tk.Canvas(editor, width=pixels(editor, 20), height=pixels(editor, 20),
                                   bd=0, highlightthickness=0, takefocus=False)
                marker.bind('<Button-1>', lambda event, n=number: self._toggle_task(n))
                self._markers[number] = marker
            marker.configure(bg=editor.cget('bg')); marker.delete('all')
            if prefix == '•': marker.create_text(pixels(editor, 10), pixels(editor, 10), text='•', fill=theme.INK, font=(theme.BODY_FONT, 12))
            else:
                from .rounded_controls import rounded_box
                rounded_box(marker, (pixels(editor, 2), pixels(editor, 2), pixels(editor, 18), pixels(editor, 18)),
                    radius=pixels(editor, 4), fill=theme.ACCENT_DEEP if prefix == '☑' else editor.cget('bg'),
                    outline=theme.ACCENT_DEEP if prefix == '☑' else theme.LINE, width=1)
                if prefix == '☑': marker.create_text(pixels(editor, 10), pixels(editor, 10), text='✓', fill=theme.ON_ACCENT)
            marker.place(x=editor.winfo_pixels(editor.cget('padx')) + pixels(editor, 3),
                         y=line[1], width=pixels(editor, 20), height=pixels(editor, 20), bordermode='ignore')
        for key, widget in tuple(self._markers.items()):
            if key not in visible: widget.destroy(); del self._markers[key]

    def _toggle_task(self, number):
        if not self.plan or self.editor.cget('state') != 'normal': return 'break'
        start, end, prefix = self.plan.bullets[number]
        if prefix not in ('☐', '☑'): return 'break'
        source = self.plan.source[start:end]; offset = source.find('[') + 1
        self.editor.edit_separator()
        self.editor.replace(self.plan.index(start + offset), self.plan.index(start + offset + 1), 'x' if prefix == '☐' else ' ')
        self.editor.edit_separator(); return 'break'

    def code_at(self, index='insert'):
        if not self.plan: return None
        offset = len(self.editor.get('1.0', index))
        return next((code for code in self.plan.codes if code.start <= offset < code.end), None)

    def copy_code(self, number):
        if not self.plan or number >= len(self.plan.codes): return 'break'
        code = self.plan.codes[number]
        value = self.plan.source[code.code_start:code.code_end]
        if hasattr(self.editor,'_copy_code_source'):value=self.editor._copy_code_source(code)
        from ..clipboard import copy_text
        if not copy_text(self.editor,value):return 'break'
        header = self._headers.get((number, 'head'))
        if header is not None:
            items = header.find_all()
            if items: header.itemconfigure(items[-1], text='已复制')
        return 'break'

    def _open_link(self, event):
        if not self.plan: return
        offset = len(self.editor.get('1.0', self.editor.index(f'@{event.x},{event.y}')))
        target = next((url for a, b, url in self.plan.links if a <= offset < b), '')
        if target: webbrowser.open(target); return 'break'

    def _destroy(self, event):
        if event.widget is not self.editor: return
        self._closed = True; self._epoch += 1
        for job in (self._parse_job, self._poll_job, self._view_job):
            if job is not None:
                try: self.editor.after_cancel(job)
                except tk.TclError: pass
        if self._future is not None: self._future.cancel()
        self._fonts.clear();self._headers.clear();self._markers.clear()
        try: self.editor.tk.deletecommand(self.editor._w)
        except tk.TclError: pass
