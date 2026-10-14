"""Clear, bounded native rich-text previews on the journal's plain surface."""
import json
import re
from math import ceil
import tkinter as tk
from .font_source import native_font
import tkinter.font as tkfont
from .rich_journal_editor import RichJournalEditor
from .theme_controller import set_color_roles
from .dpi import pixels
from . import theme
from ..plaza.journal_document import validate_document
from ..text_editing import bind_text_editing


class JournalReadView(RichJournalEditor):
    MAX_PREVIEW_CHARACTERS=5000
    MAX_PREVIEW_LINES=12

    def __init__(self,parent,entry):
        super().__init__(parent,width=1,height=1,wrap='word',undo=False,
            relief='flat',bd=0,highlightthickness=0,padx=pixels(parent,4),
            pady=pixels(parent,6),font=(theme.BODY_FONT,12),spacing3=pixels(parent,6),
            exportselection=False,cursor='xterm',takefocus=True)
        self._remchat_locale_exclude=True
        self._layout_job=None;self._layout_width=None
        set_color_roles(self,background='WHITE',foreground='INK',insertbackground='INK',
            selectbackground='ACCENT_SOFT',selectforeground='INK')
        document=validate_document(entry.rich)
        self._complete_source=document['text'] if document else entry.body
        self._complete_code_spans=[]
        for span in sorted((value for value in (document or {}).get('spans',()) if value['tag']=='code'),key=lambda value:value['start']):
            a,b=span['start'],span['end']
            if self._complete_code_spans and a<=self._complete_code_spans[-1][1]:
                self._complete_code_spans[-1]=(self._complete_code_spans[-1][0],max(b,self._complete_code_spans[-1][1]))
            else:self._complete_code_spans.append((a,b))
        if document:
            text=document['text'][:self.MAX_PREVIEW_CHARACTERS]
            # Preview truncation must also bound style ranges. Stored text and
            # the editable document remain untouched, including legacy body.
            spans=[{**span,'end':min(span['end'],len(text))} for span in document.get('spans',())
                   if span['start']<len(text)]
            preview={'version':1,'text':text,'spans':spans,'attachments':[]}
            self.load(text,json.dumps(preview,ensure_ascii=False))
        else:self.load(entry.body[:self.MAX_PREVIEW_CHARACTERS])
        self.has_text=bool(self.get('1.0','end-1c').strip())
        self.configure(state='disabled')
        bind_text_editing(self,read_only=True)
        self.bind('<Configure>',self._schedule_layout,add=True)
        self.bind('<Map>',self._schedule_layout,add=True)
        self.bind('<<ThemeChanged>>',self._theme_changed,add=True)
        self.bind('<<JournalRendered>>',self._rendered,add=True)
        self.bind('<Destroy>',self._destroy,add=True)

    def _copy_code_source(self,code):
        """A clipped timeline must still copy the complete original code."""
        source=self._complete_source
        if code.literal:
            return next((source[a:b] for a,b in self._complete_code_spans if a==code.code_start),source[code.code_start:code.code_end])
        opening=re.match(r' {0,3}(`{3,}|~{3,})',source[code.start:])
        if not opening:return source[code.code_start:code.code_end]
        fence=opening.group(1)
        closing=re.compile(r'^ {0,3}'+re.escape(fence[0])+r'{'+str(len(fence))+r',}[ \t]*\r?$',re.MULTILINE)
        match=closing.search(source,code.code_start)
        return source[code.code_start:match.start() if match else len(source)]

    def _rendered(self,_event=None):
        self._layout_width=None;self._schedule_layout()

    def _theme_changed(self,_event=None):
        self.refresh_formats();self._layout_width=None;self._schedule_layout()

    def _schedule_layout(self,_event=None):
        if self._layout_job is None:self._layout_job=self.after_idle(self._fit)

    def _fit(self):
        self._layout_job=None
        if not self.winfo_exists() or not self.winfo_ismapped():return
        width=self.winfo_width()
        if width<2 or width==self._layout_width:return
        self._layout_width=width
        measured=self.count('1.0','end','ypixels')
        line_height=max(1,native_font(self,font=self.cget('font')).metrics('linespace'))
        height=max(1,min(self.MAX_PREVIEW_LINES,ceil((measured[0] if measured else line_height)/line_height)))
        if int(self.cget('height'))!=height:self.configure(height=height)

    def _destroy(self,event):
        if event.widget is self and self._layout_job is not None:
            self.after_cancel(self._layout_job);self._layout_job=None
