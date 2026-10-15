"""Calendar-led memories, direct rich writing and day-first checklists.

THESIS: Time is navigation; one writing surface renders rich text and Markdown live.
OWN-WORLD: RemChat blue-grey/white, plain calm reading surfaces and rounded forms.
STORY: Locate a day, finish its tasks, capture media and write a memory.
FIRST VIEWPORT: Calendar/filter rail plus chronological day/time feed.
FORM: User-pinned journal timeline, extending the existing native persistence.
"""
import json
from datetime import date
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from .font_source import native_font
import tkinter.font as tkfont
from tkinter import ttk
from .localized_dialogs import filedialog, simpledialog, messagebox
from PIL import Image, ImageTk, ImageDraw
from .diary_panel import DiaryPanel as _BaseDiaryPanel, _SAVE_ERRORS
from .diary_widgets import _Toolbar, _frame, _label, _button, _entry
from .rich_journal_editor import RichJournalEditor
from .journal_read_view import JournalReadView
from .settings_card import SettingsCard
from .rounded_controls import style_checkbox
from .theme_controller import set_color_roles
from .dpi import pixels
from . import theme
from .scroll_surface import ScrollCanvas
from ..plaza.journal_document import JournalMedia, validate_document
from .locale_controller import translator_for
from ..i18n import weekday_labels


class JournalPanel(_BaseDiaryPanel):
    def _make_editor_container(self,parent):
        from .journal_live import WritingSurface
        return WritingSurface(parent)

    def __init__(self,parent,project_root,*,on_changed=None,backdrop=None):
        self._journal_view='timeline'; self._show_calendar=True
        # ``backdrop`` remains a compatible ignored argument for older callers.
        # The diary must never sample the main chat's picture into its text.
        self._timeline_records=[]; self._timeline_limit=20; self._media_photos=[];self._readers=[]
        self._task_controls={}
        self._search_hits={};self._search_locate_pending=None
        self.media=JournalMedia(project_root)
        super().__init__(parent,project_root,on_changed=on_changed)
        self.day_var.trace_add('write',lambda *_:self._reload_tasks())
        self._reload_tasks(); self._show_timeline()
        self._remchat_retranslate = self._retranslate_journal

    def _retranslate_journal(self):
        self._schedule_calendar()
        self.editor._selection_tools.dismiss()
        if self._journal_view == 'timeline' and not self.search.get().strip():
            self._publish_timeline(self._timeline_records)

    def _build(self):
        super()._build()
        old=self.editor; parent=old.master; old.destroy()
        self.editor=RichJournalEditor(parent,width=1,height=1,wrap='word',undo=True,
            bd=0,relief='flat',padx=pixels(self,18),pady=pixels(self,16),
            font=(theme.BODY_FONT,12),spacing3=8,maxundo=200,exportselection=False)
        self.editor._remchat_locale_exclude=True
        set_color_roles(self.editor,background='APP_BG',foreground='INK',insertbackground='INK',
                        selectbackground='ACCENT_SOFT',selectforeground='INK')
        self.editor.grid(row=0,column=0,sticky='nsew')
        scroll=next((w for w in parent.winfo_children() if isinstance(w,ttk.Scrollbar)),None)
        if scroll:
            scroll.configure(command=self.editor.yview);self.editor.configure(yscrollcommand=scroll.set)
        self.editor.bind('<<Modified>>',self._text_changed)
        self.editor.bind('<Control-s>',lambda e:self._save_key())
        self.editor.bind('<Control-f>',lambda e:self._focus_search())
        self.editor.bind('<<JournalRendered>>',self._locate_ready,add=True)
        from .journal_selection import JournalSelectionTools
        self.editor._selection_tools=JournalSelectionTools(self.editor,code=self._code_block,
            emoji=self._emoji_menu,attachment=self._attach)
        from .speech_input import SpeechBinding
        self.speech_input=SpeechBinding(self.actions,self.editor,self.project_root,
            context=lambda:self._navigation_serial,dialog=True,on_insert=self._mark_dirty,label='🎙 语音记录')
        self.actions.buttons.append(self.speech_input.button);self.actions._schedule()
        self.notebook.tab(0,text='书写')
        self.notebook.hide(self.preview)
        self.notebook.configure(style='Journal.TNotebook')
        self._journal_notebook_theme=None
        self.notebook.bind('<<ThemeChanged>>',self._journal_notebook_style,add=True)
        self._journal_notebook_style()
        self.actions.show(self.preview_button,False)
        self.actions.show(self.properties_button,False)
        self.actions.show(self.navigation_button,True)
        self.navigation_button.configure(text='返回总览页面',command=self._show_timeline)
        self.file_button.configure(text='导入 / 导出')
        for button in self.formats.buttons:button.destroy()
        self.formats.buttons=[]
        for label,command in (('B 粗体',lambda:self.editor.toggle('bold')),
            ('马克笔',lambda:self.editor.toggle('highlight')),('标题 ▾',self._heading_menu),
            ('• 列表',self.editor.bullets),('❝ 引用',lambda:self.editor.block('quote')),
            ('表情',self._emoji_menu),('＋ 附件',self._attach),('{ } 代码块',self._code_block)):
            self.formats.add(label,command)
        from .color_emoji import emoji_photo
        button=self.formats.buttons[5]
        button._emoji_icon=emoji_photo(button,'😊',pixels(self,20))
        if button._emoji_icon:button.configure(image=button._emoji_icon,compound='left')
        self.editor_tasks=_frame(self.right_body)
        self.editor_tasks.grid(row=3,column=0,sticky='ew',pady=(0,8))
        self.formats.grid(row=4,column=0,sticky='ew',pady=(0,8))
        self.notebook.grid(row=5,column=0,sticky='nsew')
        # Move the original status row without disturbing its traces.
        for child in self.right_body.winfo_children():
            if isinstance(child,tk.Label) and str(child.cget('textvariable'))==str(self.status):child.grid(row=7,column=0,sticky='ew')
        self.editor_media=_frame(self.right_body); self.editor_media.grid(row=6,column=0,sticky='ew',pady=(8,0))
        self.right_body.rowconfigure(4,weight=0);self.right_body.rowconfigure(5,weight=1)
        self.list_frame.grid_remove(); self.sidebar_card.body.rowconfigure(6,weight=0)
        self.search_row.grid_remove()
        self.mood_choice.grid_remove();self.filters.columnconfigure(1,weight=0)
        self.tag_choice.grid(row=0,column=0,columnspan=2,sticky='ew',padx=0)
        self.mood_label.grid_remove();self.mood_entry.grid_remove()
        self.tag_label.configure(text='设置标签 tag')
        self.timeline_card=SettingsCard(self,stretch=True,inset=16)
        body=self.timeline_card.body;body.columnconfigure(0,weight=1);body.rowconfigure(2,weight=1)
        header=self.overview_header=_frame(body);header.grid(row=0,column=0,sticky='ew',pady=(0,8));header.columnconfigure(1,weight=1)
        bar=self.overview_actions=_Toolbar(header);bar.grid(row=0,column=0,sticky='w')
        bar.add('日历',self._toggle_side)
        bar.add('今天',self._today)
        bar.add('写一刻',self.new_entry,primary=True)
        self.overview_search=_frame(header);self.overview_search.columnconfigure(1,weight=1)
        self.overview_search.grid(row=0,column=1,sticky='ew',padx=(12,12))
        _label(self.overview_search,'搜索日记',size=9,muted=True).grid(row=0,column=0,sticky='w',padx=(0,8))
        self.overview_search_entry=_entry(self.overview_search,self.search,width=16)
        self.overview_search_entry.grid(row=0,column=1,sticky='ew',ipady=4)
        self.search_clear=_button(self.overview_search,'×',lambda:self.search.set(''))
        self.search_clear.configure(padx=7,pady=4);self.search_clear.grid(row=0,column=2,sticky='e',padx=(4,0))
        self.overview_search_entry.bind('<Escape>',lambda e:(self.search.set(''),'break')[1])
        self.overview_search_entry.bind('<Return>',lambda e:self._open_first_hit())
        self.template_button=_button(header,'设置日记模板',self._templates)
        self.template_button.grid(row=0,column=2,sticky='ne')
        header.bind('<Configure>',self._overview_header_layout)
        self.feed_day=_label(body,'日记',size=15,bold=True);self.feed_day.grid(row=1,column=0,sticky='ew',pady=(0,10))
        frame=_frame(body);frame.grid(row=2,column=0,sticky='nsew');frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        self.feed=ScrollCanvas(frame,width=1,height=1,bd=0,highlightthickness=0,bg=theme.WHITE)
        self.feed.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(frame,command=self.feed.yview);scrollbar.grid(row=0,column=1,sticky='ns')
        self.feed.configure(yscrollcommand=scrollbar.set)
        self.feed_body=_frame(self.feed)
        self._feed_window=self.feed.create_window(0,0,window=self.feed_body,anchor='nw')
        self.feed_body.columnconfigure(0,weight=1)
        self.feed.bind('<Configure>',self._feed_size)
        self.feed_body.bind('<Configure>',lambda e:self.feed.configure(scrollregion=self.feed.bbox('all')))
        self.feed.bind('<MouseWheel>',lambda e:(self.feed.yview_scroll(-int(e.delta/120),'units'),'break')[1])

    def _overview_header_layout(self,event):
        controls=sum(button.winfo_reqwidth()+pixels(self,6) for button in self.overview_actions.buttons)
        template=self.template_button.winfo_reqwidth()
        mode=0 if event.width>=controls+template+pixels(self,280) else 1 if event.width>=controls+template+pixels(self,12) else 2
        if getattr(self,'_template_header_narrow',None)==mode:return
        self._template_header_narrow=mode
        for column in range(3):self.overview_header.columnconfigure(column,weight=1 if column==(1 if mode==0 else 0) else 0,
            minsize=controls if column==0 and mode<2 else 0)
        self.overview_actions.grid(columnspan=3 if mode==2 else 1,sticky='ew')
        self.template_button.grid(row=1 if mode==2 else 0,column=0 if mode==2 else 2,
            sticky='e',pady=(4 if mode==2 else 0,0))
        self.overview_search.grid(row=0 if mode==0 else 2 if mode==2 else 1,column=1 if mode==0 else 0,
            columnspan=1 if mode==0 else 3,sticky='ew',padx=(12 if mode==0 else 0,12 if mode==0 else 0),pady=(6 if mode else 0,0))

    def _focus_search(self):
        if self._show_timeline():self.overview_search_entry.focus_set()
        return 'break'

    def _open_first_hit(self):
        if self._search_hits:self._open_search_hit(next(iter(self._search_hits.values())))
        return 'break'

    def _today(self):
        self.search.set('');return super()._today()

    def _calendar_click(self,event):
        self.search.set('');return super()._calendar_click(event)

    def _calendar_key(self,amount):
        self.search.set('');return super()._calendar_key(amount)

    def _journal_notebook_style(self,_event=None):
        style=ttk.Style(self);key=style.theme_use()
        if key==self._journal_notebook_theme:return
        self._journal_notebook_theme=key
        style.layout('Journal.TNotebook.Tab',[('null',{})])

    def _metadata_layout(self,event):
        required=self.day_entry.winfo_reqwidth()+self.tag_label.winfo_reqwidth()+pixels(self,210)
        narrow=event.width<required
        if getattr(self,'_journal_metadata_narrow',None)==narrow:return
        self._journal_metadata_narrow=narrow
        self.mood_label.grid_remove();self.mood_entry.grid_remove()
        self.metadata.columnconfigure(1,weight=1 if narrow else 0)
        self.metadata.columnconfigure(3,weight=0 if narrow else 1)
        self.tag_label.grid(row=1 if narrow else 0,column=0 if narrow else 2,sticky='w',
            padx=(0 if narrow else 18,8),pady=(8 if narrow else 0,0))
        self.tag_entry.grid(row=1 if narrow else 0,column=1 if narrow else 3,columnspan=3 if narrow else 1,
            sticky='ew',ipady=4,pady=(8 if narrow else 0,0))

    def _responsive_layout(self):
        self._responsive_job=None
        if self._closed or not hasattr(self,'timeline_card'):return
        narrow=self.winfo_width()<pixels(self,820)
        side=self._show_calendar and (not narrow or getattr(self,'_calendar_explicit',False))
        if side:
            self.columnconfigure(0,weight=0,minsize=pixels(self,250));self.columnconfigure(1,weight=1)
            self.sidebar_card.grid(row=0,column=0,sticky='nsew',padx=(10,8),pady=10)
        else:
            self.sidebar_card.grid_remove();self.columnconfigure(0,weight=1,minsize=0);self.columnconfigure(1,weight=0)
        active=self.timeline_card if self._journal_view=='timeline' else self.editor_card
        inactive=self.editor_card if active is self.timeline_card else self.timeline_card
        inactive.grid_remove()
        active.grid(row=0,column=1 if side else 0,columnspan=1 if side else 2,sticky='nsew',padx=(0,10),pady=10)
        self.actions.show(self.navigation_button,True);self.actions.show(self.preview_button,False)
        self._schedule_calendar()

    def _toggle_side(self):
        self._show_calendar=not(self.sidebar_card.winfo_ismapped());self._calendar_explicit=self._show_calendar
        self._schedule_responsive()

    def _show_timeline(self):
        if hasattr(self,'speech_input') and self.speech_input.controller.owner is self.speech_input:self.speech_input.controller.cancel()
        if not self.flush_pending():return False
        self._journal_view='timeline';self._schedule_responsive();self._refresh_list();return True

    def _show_navigation(self):return self._show_timeline()
    def _show_editor(self):
        self._journal_view='editor';self._drawer_page='editor';self._schedule_responsive()

    def _clear_editor(self,*,day=None):
        if hasattr(self,'speech_input') and self.speech_input.controller.owner is self.speech_input:self.speech_input.controller.cancel()
        super()._clear_editor(day=day)
        self.editor.load();self._media_photos=[]
        self._show_attachments(self.editor_media,[])
        self._reload_tasks()

    def _load_entry(self,entry):
        super()._load_entry(entry)
        self._loading=True
        try:self.editor.load(entry.body,entry.rich)
        except _SAVE_ERRORS as error:
            self.editor.load(entry.body);self.status.set('格式无法读取，原记录保留 · '+str(error))
        self.editor.configure(state='disabled' if entry.deleted_at else 'normal')
        self._loading=False;self._dirty=False
        self._show_attachments(self.editor_media,self.editor.attachments)
        self._reload_tasks()

    def _draft(self):
        entry=super()._draft()
        return replace(entry,rich=self.editor.document())

    def new_entry(self):
        if not super().new_entry():return False
        if self.store:
            try:
                weekday=date.fromisoformat(self.day_var.get()).weekday()
                template=next((t for t in self.store.templates() if t['enabled'] and weekday in t['weekdays'] and t['body']),None)
                if template:self._apply_template(template)
            except _SAVE_ERRORS as error:self.status.set('模板无法读取 · '+str(error))
        self.editor.focus_set();return True

    def _apply_template(self,template):
        if self.editor.get('1.0','end-1c').strip():
            messagebox.showinfo('保留当前正文','模板仅填入空白日记，当前内容未改变。',parent=self);return
        value=template['body']
        if value.startswith('{'):
            doc=validate_document(value);self.editor.load(doc['text'],value)
        else:self.editor.load(value)
        self._mark_dirty()

    def _publish_list(self,rows,tags,keep):
        super()._publish_list(rows,tags,keep)
        serial=self._query_serial; ids=[r.id for r in rows[:self._timeline_limit]]
        def read():
            try:records=[self.store.get(i) for i in ids];self._results.put(('timeline',serial,[r for r in records if r],None))
            except _SAVE_ERRORS as error:self._results.put(('timeline',serial,None,error))
        self._executor.submit(read)

    def _refresh_list(self,*,keep=None):
        query=self.search.get().strip()
        if not query:
            self._search_hits={}
            return super()._refresh_list(keep=keep)
        self._search_job=None
        if not self.store or self._closed:return
        self._query_serial+=1;serial=self._query_serial
        tag='' if self.tag_filter.get()=='全部标签' else self.tag_filter.get()
        deleted=self.trash_mode.get()
        self.list_status.set('搜索中…')
        self.feed_day.configure(text='搜索中…')
        def read():
            try:
                hits=self.store.search(query,tag=tag,deleted=deleted,limit=200,
                    cancelled=lambda:self._closed or serial!=self._query_serial)
                tags=self.store.tags(deleted=deleted)
                self._results.put(('journal-search',serial,hits,tags,None))
            except _SAVE_ERRORS as error:self._results.put(('journal-search',serial,None,None,error))
        if self._query_future is not None:self._query_future.cancel()
        self._query_future=self._executor.submit(read)

    def _publish_search(self,serial,hits,tags,error):
        if serial!=self._query_serial or self._closed:return
        if error:
            self.feed_day.configure(text='搜索未完成 · '+str(error));return
        self._search_hits={hit.entry.id:hit for hit in hits}
        # Keep the inherited selection/tag/calendar machinery, not its second
        # literal search or chronologically sorted full-body timeline reader.
        _BaseDiaryPanel._publish_list(self,[hit.entry for hit in hits],tags,None)
        for child in self.feed_body.winfo_children():child.destroy()
        self._readers=[];self._media_photos=[]
        self.feed_day.configure(text=f'搜索结果 · {len(hits)} 篇')
        self.feed.yview_moveto(0)
        for row,hit in enumerate(hits):
            entry=hit.entry;item=_frame(self.feed_body);item.grid(row=row,column=0,sticky='ew',pady=(8,18));item.columnconfigure(0,weight=1)
            label=_label(item,entry.day+'  ·  '+(entry.title or '无标题'),size=13,bold=True)
            label.grid(row=0,column=0,sticky='ew',pady=(0,7))
            label.bind('<Configure>',lambda e,w=label:w.configure(wraplength=max(100,e.width)))
            snippet=tk.Text(item,width=1,height=3,wrap='word',bd=0,highlightthickness=0,
                padx=0,pady=4,font=(theme.BODY_FONT,11),cursor='xterm',takefocus=True)
            set_color_roles(snippet,background='WHITE',foreground='INK',selectbackground='ACCENT_SOFT',selectforeground='INK')
            snippet.insert('1.0',hit.snippet)
            snippet.tag_configure('match',background=theme.ACCENT_SOFT,foreground=theme.INK)
            for a,b in hit.snippet_ranges:snippet.tag_add('match',f'1.0+{a}c',f'1.0+{b}c')
            snippet.configure(state='disabled');snippet.grid(row=1,column=0,sticky='ew')
            from .color_emoji import ColorEmojiText
            snippet._color_emoji=ColorEmojiText(snippet)
            from ..text_editing import bind_text_editing
            bind_text_editing(snippet,read_only=True)
            bar=_Toolbar(item);bar.grid(row=2,column=0,sticky='ew',pady=(5,0))
            bar.add('定位日记',lambda value=hit:self._open_search_hit(value))
            for tag in entry.tags:bar.add('#'+tag,lambda value=tag:self.tag_filter.set(value))
        if not hits:_label(self.feed_body,'没有找到这段内容，换个关键词试试。',muted=True).grid(row=0,column=0,sticky='ew',pady=20)
        self._feed_size();self._bind_scroll(self.feed,self.feed_body)

    def _open_search_hit(self,hit):
        if not self.flush_pending():return
        try:entry=self.store.get(hit.entry.id)
        except _SAVE_ERRORS as error:self.status.set('无法打开 · '+str(error));return
        if not entry:return
        self._month=date.fromisoformat(entry.day).replace(day=1);self._selected_day=entry.day
        self._load_entry(entry);self._schedule_calendar()
        self._search_locate_pending=(entry.id,self._navigation_serial,hit.body_ranges)
        self.editor.focus_set()

    def _locate_ready(self,_event=None):
        pending=self._search_locate_pending
        if not pending:return
        self._search_locate_pending=None
        entry_id,serial,ranges=pending
        if not self._current or self._current.id!=entry_id or serial!=self._navigation_serial:return
        self.editor.tag_configure('_search:hit',background=theme.ACCENT_SOFT)
        for a,b in ranges:self.editor.tag_add('_search:hit',f'1.0+{a}c',f'1.0+{b}c')
        if ranges:
            first=f'1.0+{ranges[0][0]}c';self.editor.mark_set('insert',first);self.editor.see(first)

    def _publish_timeline(self,records):
        if self._closed:return
        if self.search.get().strip():return
        records=sorted(records,key=lambda entry:(entry.day,entry.created_at),reverse=True)
        self._timeline_records=records
        for child in self.feed_body.winfo_children():child.destroy()
        self._media_photos=[];self._readers=[]
        self.feed_day.configure(text=self._selected_day or '日记 · 时间线')
        row=0
        days=([self._selected_day] if self._selected_day else [date.today().isoformat()])+[r.day for r in records]
        # Show today's checklist even with no diary record. No blank entry is
        # manufactured when a date is merely opened.
        for day in dict.fromkeys(days):
            if not day:continue
            group=_frame(self.feed_body)
            group.grid(row=row,column=0,sticky='ew',pady=(8,24));row+=1
            group.columnconfigure(1,weight=1)
            stamp=date.fromisoformat(day)
            _label(group,f'{stamp.day:02d}',size=22,bold=True).grid(row=0,column=0,sticky='nw',padx=(0,18))
            tr = translator_for(self)
            _label(group,tr('{month}月 · {weekday}', month=stamp.month,
                           weekday=weekday_labels(tr.language)[stamp.weekday()]),muted=True).grid(row=0,column=1,sticky='w')
            tasks=_frame(group);tasks.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(8,4))
            self._task_list(tasks,day,compact=True)
            for entry in (r for r in records if r.day==day):
                item=_frame(group);item.grid(row=group.grid_size()[1],column=0,columnspan=2,sticky='ew',pady=(14,4));item.columnconfigure(1,weight=1)
                time=(entry.created_at or entry.updated_at)[11:16]
                rail=tk.Canvas(item,width=pixels(self,60),height=pixels(self,100),bd=0,highlightthickness=0,bg=theme.WHITE)
                rail.grid(row=0,column=0,rowspan=4,sticky='ns',padx=(0,12))
                rail.create_text(pixels(self,25),pixels(self,13),text=time,font=(theme.BODY_FONT,9),fill=theme.MUTED)
                rail.create_oval(pixels(self,45),pixels(self,9),pixels(self,53),pixels(self,17),fill=theme.ACCENT,outline='')
                rail.create_line(pixels(self,49),pixels(self,23),pixels(self,49),pixels(self,100),fill=theme.LINE,width=2)
                if entry.title:
                    title_label = _label(item,entry.title,size=13,bold=True)
                    title_label._remchat_locale_exclude = True
                    title_label.grid(row=0,column=1,sticky='ew',pady=(0,7))
                reader=JournalReadView(item,entry)
                self._readers.append(reader)
                if reader.has_text:reader.grid(row=1,column=1,sticky='ew')
                document=validate_document(entry.rich) if entry.rich else None
                media=_frame(item);media.grid(row=2,column=1,sticky='ew',pady=(6,0));self._show_attachments(media,document.get('attachments',[]) if document else [])
                footer=_Toolbar(item);footer.grid(row=3,column=1,sticky='ew',pady=(8,0))
                for tag in entry.tags:footer.add('#'+tag,lambda value=tag:self.tag_filter.set(value))
                footer.add('阅读 / 编辑',lambda value=entry:self._open_record(value))
        if not records:
            empty=_label(self.feed_body,'今天有些什么值得记住？',size=13,muted=True)
            empty.grid(row=row,column=0,sticky='ew',pady=18)
        if len(self._rows)>len(records):
            _button(self.feed_body,'更多记录',self._more_records).grid(row=row+1,column=0,pady=12)
        self._feed_size()
        self._bind_scroll(self.feed,self.feed_body)
        for key,controls in tuple(self._task_controls.items()):
            alive=[pair for pair in controls if pair[0].winfo_exists()]
            if alive:self._task_controls[key]=alive
            else:self._task_controls.pop(key,None)

    @staticmethod
    def _bind_scroll(canvas,body):
        # Scoped bindtags: scrolling over a diary paragraph, photo or button
        # moves this feed, without a bind_all handler stealing other editors.
        tag='JournalScroll'+str(id(canvas))
        if not getattr(canvas,'_journal_scroll_bound',False):
            def wheel(event):
                if getattr(event,'num',0) in (4,5):amount=-1 if event.num==4 else 1
                else:amount=-int(event.delta/120) if abs(event.delta)>=120 else (-1 if event.delta>0 else 1)
                canvas.yview_scroll(amount,'units');return 'break'
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):canvas.bind_class(tag,sequence,wheel)
            def cleanup(event):
                if event.widget is canvas:
                    for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):canvas.unbind_class(tag,sequence)
            canvas.bind('<Destroy>',cleanup,add=True);canvas._journal_scroll_bound=True
        def apply(widget):
            tags=widget.bindtags()
            if tag not in tags:widget.bindtags((tag,*tags))
            for child in widget.winfo_children():apply(child)
        apply(body)

    def _more_records(self):
        self._timeline_limit=min(200,self._timeline_limit+20);self._refresh_list()
    def _open_record(self,entry):
        if self.flush_pending():self._load_entry(entry)
    def _feed_size(self,_event=None):
        if not hasattr(self,'feed'):return
        self.feed.itemconfigure(self._feed_window,width=max(1,self.feed.winfo_width()))

    def _task_list(self,host,day,compact=False):
        for child in host.winfo_children():child.destroy()
        if not self.store:return
        try:tasks=self.store.daily_tasks(day)
        except _SAVE_ERRORS:return
        _label(host,'每日待办',bold=True).pack(anchor='w',pady=(0,4))
        for task in tasks[:4]:
            variable=tk.BooleanVar(host,bool(task['done']))
            control=tk.Checkbutton(host,text=task['text'],variable=variable,anchor='w',font=(theme.BODY_FONT,10),
                bg=theme.WHITE,fg=theme.MUTED if task['done'] else theme.INK,
                command=lambda t=task,v=variable:self._check_task(day,t['id'],v.get()))
            control._remchat_locale_exclude = True
            control.pack(anchor='w',fill='x');style_checkbox(control)
            self._register_task(control,variable,day,task)
        if len(tasks)>4:_button(host,f'全部待办 · {len(tasks)}',lambda:self._all_tasks(day)).pack(anchor='w')
        _button(host,'＋ 添加待办',lambda:self._add_task(day)).pack(anchor='w',pady=(3,0))
    def _reload_tasks(self):
        if hasattr(self,'editor_tasks'):self._task_list(self.editor_tasks,self.day_var.get())
    def _check_task(self,day,task_id,done):
        try:self.store.check_task(day,task_id,done)
        except _SAVE_ERRORS as error:self.status.set('待办未保存 · '+str(error));return
        kept=[]
        for control,variable in self._task_controls.get((day,task_id),[]):
            if control.winfo_exists():
                variable.set(bool(done));control._task_font.configure(overstrike=bool(done))
                control.configure(fg=theme.MUTED if done else theme.INK);kept.append((control,variable))
        self._task_controls[(day,task_id)]=kept
    def _register_task(self,control,variable,day,task):
        control._task_font=native_font(control,font=control.cget('font'))
        control._task_font.configure(overstrike=bool(task['done']))
        control.configure(font=control._task_font)
        self._task_controls.setdefault((day,task['id']),[]).append((control,variable))
    def _add_task(self,day=None):
        value=simpledialog.askstring('每日待办','要做什么？',parent=self)
        if value and self.store:
            try:self.store.add_task(day or self.day_var.get(),value);self._reload_tasks();self._refresh_list()
            except _SAVE_ERRORS as error:self.status.set(str(error))
    def _all_tasks(self,day):
        window=tk.Toplevel(self);window.title(day+' · 待办');window.geometry('580x500')
        canvas=ScrollCanvas(window,bg=theme.WHITE,bd=0,highlightthickness=0)
        scroll=ttk.Scrollbar(window,command=canvas.yview);scroll.pack(side='right',fill='y')
        canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
        box=_frame(canvas);slot=canvas.create_window(18,18,window=box,anchor='nw')
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(slot,width=max(1,e.width-36)))
        box.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        for task in self.store.daily_tasks(day):
            variable=tk.BooleanVar(box,bool(task['done']))
            check=tk.Checkbutton(box,text=task['text'],variable=variable,anchor='w',wraplength=500,
                command=lambda t=task,v=variable:self._check_task(day,t['id'],v.get()),font=(theme.BODY_FONT,11),bg=theme.WHITE,fg=theme.INK)
            check.pack(anchor='w',fill='x',pady=3);style_checkbox(check)
            self._register_task(check,variable,day,task)
        self._bind_scroll(canvas,box)

    def _heading_menu(self):
        menu=tk.Menu(self,tearoff=False)
        for level in range(1,7):menu.add_command(label=f'H{level} · 标题 {level}',command=lambda n=level:self.editor.block('h'+str(n)))
        menu.tk_popup(self.formats.winfo_rootx(),self.formats.winfo_rooty()+self.formats.winfo_height())
    def _emoji_menu(self):
        if self.editor.cget('state')!='normal':return
        from .color_emoji import show_emoji_picker
        show_emoji_picker(self.formats,self.editor)

    def _code_block(self):
        if self.editor.cget('state')!='normal':return
        from .journal_code import edit_code_block
        edit_code_block(self,self.editor)

    def _attach(self):
        if self._batch_busy or self.editor.cget('state')!='normal':return
        paths=filedialog.askopenfilenames(parent=self,title='添加图片、音频、视频或文件')
        if not paths:return
        context=(self._navigation_serial,self._edit_serial)
        self._batch_busy=True;self.status.set('正在导入附件…')
        remaining=64-len(self.editor.attachments)
        def work():
            items=[];errors=[]
            for path in paths[:remaining]:
                try:items.append(self.media.import_file(path))
                except (OSError,ValueError) as error:errors.append(str(error))
            self._results.put(('media',context,items,errors))
        self._executor.submit(work)
    def _publish_media(self,context,items,errors):
        self._batch_busy=False
        if context[0]!=self._navigation_serial:
            self.status.set('附件已复制，未替换正在编辑的记录');return
        self.editor.attachments.extend(items)
        if items and not (self.title_var.get() or self.editor.get('1.0','end-1c').strip()):self.title_var.set('记录一刻')
        self._show_attachments(self.editor_media,self.editor.attachments);self._mark_dirty()
        if errors:self.status.set('部分附件未导入 · '+'；'.join(errors))
    def _show_attachments(self,host,items):
        for child in host.winfo_children():child.destroy()
        for index,item in enumerate(items[:16]):
            label={'image':'▧ 图片','audio':'♫ 音频','video':'▶ 视频','file':'▤ 文件'}[item['kind']]
            button=_button(host,label+' · '+item['name'][:22],lambda value=item:self._open_media(value))
            button.grid(row=index//3,column=index%3,sticky='ew',padx=(0,6),pady=(0,6))
            # Photos are compact media cards, never a one-photo full-width slab.
            host.columnconfigure(index%3,weight=0)
            if item['kind']=='image':
                size=(pixels(self,128),pixels(self,90))
                def thumbnail(value=item,control=button,target=size):
                    try:
                        with Image.open(self.media.resolve(value)) as image:
                            if image.width*image.height>24000000:return
                            image.thumbnail(target);thumb=image.convert('RGBA')
                        mask=Image.new('L',thumb.size)
                        ImageDraw.Draw(mask).rounded_rectangle((0,0,thumb.width-1,thumb.height-1),radius=12,fill=255)
                        thumb.putalpha(mask)
                        self._results.put(('thumb',control,thumb))
                    except (OSError,ValueError,Image.DecompressionBombError):pass
                self._executor.submit(thumbnail)
        if len(items)>16:_button(host,f'全部附件 · {len(items)}',lambda:self._all_media(items)).grid(row=6,column=0,columnspan=3,sticky='w')
    def _all_media(self,items):
        window=tk.Toplevel(self);window.title('日记附件');window.geometry('680x520')
        canvas=ScrollCanvas(window,bg=theme.WHITE,bd=0,highlightthickness=0)
        scroll=ttk.Scrollbar(window,command=canvas.yview);scroll.pack(side='right',fill='y')
        canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
        body=_frame(canvas);slot=canvas.create_window(12,12,window=body,anchor='nw')
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(slot,width=max(1,e.width-24)))
        body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        for item in items:
            _button(body,item['name'],lambda value=item:self._open_media(value)).pack(fill='x',pady=4)
        self._bind_scroll(canvas,body)
    def _publish_thumb(self,button,thumb):
        # A destroyed old entry's result cannot repaint a newly selected one.
        if self._closed or not button.winfo_exists():return
        photo=ImageTk.PhotoImage(thumb,master=self)
        button.configure(image=photo,compound='top');button._journal_photo=photo
    def _open_media(self,item):
        try:self.media.open(item)
        except (OSError,ValueError) as error:messagebox.showerror('附件无法打开',str(error),parent=self)

    def _templates(self):
        from .journal_templates import edit_templates
        if self.store:edit_templates(self,self.store,self._apply_template,self._reload_tasks)

    def _file_menu(self):
        menu=tk.Menu(self,tearoff=False)
        menu.add_command(label='导入日记文件',command=self._import)
        menu.add_command(label='导出当前记录',command=self._export)
        menu.add_command(label='导出全部记录',command=self._export_all)
        menu.add_command(label='恢复日记' if self._current and self._current.deleted_at else '移入回收站',
            command=self._trash_current,state='normal' if self._current else 'disabled')
        menu.add_checkbutton(label='回收站',variable=self.trash_mode,command=self._toggle_trash)
        try:menu.tk_popup(self.file_button.winfo_rootx(),self.file_button.winfo_rooty()+self.file_button.winfo_height())
        finally:menu.grab_release()

    def _export(self):
        from ..plaza.journal_archive import export_entry
        if self._batch_busy:return
        try:entry=self._draft()
        except _SAVE_ERRORS as error:self.status.set(str(error));return
        path=filedialog.asksaveasfilename(parent=self,title='导出日记（含格式与附件）',defaultextension='.remjournal',
            initialfile=entry.day+'.remjournal',filetypes=(('RemChat 日记','*.remjournal'),))
        if not path:return
        self._batch_busy=True;self.status.set('正在导出…')
        def work():
            try:export_entry(entry,self.media,path);self._results.put(('export',1,Path(path).name,None))
            except Exception as error:self._results.put(('export',0,'',str(error)))
        self._executor.submit(work)

    def _export_all(self):
        from ..plaza.journal_archive import export_collection
        if not self.store or self._batch_busy or not self.flush_pending():return
        directory=filedialog.askdirectory(parent=self,title='导出全部记录（含格式与附件）到新文件夹')
        if not directory:return
        deleted=self.trash_mode.get();self._batch_busy=True;self.status.set('正在导出…')
        def work():
            count=0
            def records():
                nonlocal count
                for entry in self.store.iter_entries(deleted=deleted,cancelled=lambda:self._closed):
                    count+=1;yield entry
            try:
                folder=export_collection(records(),self.media,directory,cancelled=lambda:self._closed)
                self._results.put(('export',count,folder.name,None))
            except Exception as error:self._results.put(('export',0,'',str(error)))
        self._executor.submit(work)

    def _import(self):
        from ..plaza.journal_archive import import_entry
        if self._batch_busy or not self.flush_pending():return
        paths=filedialog.askopenfilenames(parent=self,title='导入日记',filetypes=(('日记或旧文本','*.remjournal *.md *.markdown *.txt'),))
        if not paths:return
        self._batch_busy=True;self.status.set('正在导入…');context=(self._navigation_serial,self._edit_serial)
        def work():
            count=0;errors=[];last=None
            for path in paths:
                if self._closed:break
                try:
                    last=(import_entry(self.store,self.media,path) if Path(path).suffix.lower()=='.remjournal' else self.store.import_markdown(Path(path)))
                    count+=1
                except Exception as error:errors.append(Path(path).name+'：'+str(error))
            self._results.put(('import',count,errors,last,context))
        self._executor.submit(work)

    def refresh_theme(self):
        super().refresh_theme()
        if hasattr(self,'editor'):self.editor.refresh_formats()
        if hasattr(self,'feed'):self.feed.configure(bg=theme.WHITE)
        for reader in self._readers:
            if reader.winfo_exists():reader._theme_changed()

    def on_hide(self):
        if self.speech_input.controller.owner is self.speech_input:self.speech_input.controller.cancel()
        return super().on_hide()

    def close(self):
        if hasattr(self,'speech_input') and self.speech_input.controller.owner is self.speech_input:self.speech_input.controller.cancel()
        return super().close()
