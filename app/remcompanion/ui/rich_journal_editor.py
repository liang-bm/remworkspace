"""Single native rich/live-Markdown editor with validated manual formatting."""
import json
import re
import tkinter as tk
import tkinter.font as tkfont
from .font_source import native_font
from . import theme
from ..plaza.journal_document import TAGS, validate_document


class RichJournalEditor(tk.Text):
    def __init__(self,parent,**options):
        super().__init__(parent,**options)
        self.attachments=[]
        self.legacy_body=''
        self._rich_fonts=[]
        self.refresh_formats()
        from ..text_editing import bind_text_editing
        bind_text_editing(self)
        from .color_emoji import ColorEmojiText
        self._color_emoji=ColorEmojiText(self)
        from .journal_live import JournalLiveDisplay
        self._live=JournalLiveDisplay(self)
        self.bind('<Control-b>',lambda e:self.toggle('bold'))
        self.bind('<Control-i>',lambda e:self.toggle('italic'))
        self.bind('<Destroy>',self._release_formats,add=True)

    def _release_formats(self,event):
        if event.widget is self:self._rich_fonts.clear()

    def refresh_formats(self):
        font=native_font(self,font=self.cget('font'))
        # actual() is the matched physical font on Xft. Feeding its size back
        # through fontconfig applies GNOME's Xft.dpi a second time. Keep the
        # requested logical size (and negative pixel-size semantics) instead.
        family,size=font.cget('family'),int(font.cget('size'))
        self._rich_fonts=[]
        def configured(tag,**options):
            if 'font' in options:
                f=native_font(self,font=options['font']); self._rich_fonts.append(f);options['font']=f
            self.tag_configure(tag,**options)
        configured('bold',font=(family,size,'bold'))
        configured('italic',font=(family,size,'italic'))
        colors=theme.palette()
        configured('highlight',background='#665528' if theme.get_mode()=='dark' else '#FFF1A8')
        for level,delta in enumerate((8,6,4,3,2,1),1):
            configured('h'+str(level),font=(family,size+delta*(1 if size>=0 else -1),'bold'),spacing1=12,spacing3=7)
        configured('quote',foreground=colors['MUTED'],lmargin1=18,lmargin2=18,spacing1=7,spacing3=7)
        configured('bullet',lmargin1=8,lmargin2=24)
        configured('done',overstrike=True,foreground=colors['MUTED'])
        configured('code',font=('Consolas',size),background='#242A34' if theme.get_mode()=='dark' else '#EEF3F8',
                   foreground=colors['INK'],lmargin1=16,lmargin2=16,rmargin=16,spacing1=3,spacing3=3)
        if hasattr(self,'_color_emoji'):self._color_emoji.schedule()
        if hasattr(self,'_live'):self._live.refresh_formats()

    def configure(self,cnf=None,**options):
        if isinstance(cnf,dict):options={**cnf,**options};cnf=None
        if hasattr(self,'_color_emoji') and 'yscrollcommand' in options:
            self._color_emoji.set_scroll_command(options['yscrollcommand'])
            options['yscrollcommand']=self._color_emoji._view_changed
        return super().configure(cnf,**options)

    config=configure

    def insert(self,index,chars,*tags):
        result=super().insert(index,chars,*tags)
        if hasattr(self,'_color_emoji'):self._color_emoji.schedule()
        return result

    def replace_code(self,start,end,value,language='text'):
        """Insert a source-preserving fenced block; live display hides fences."""
        if self.cget('state')!='normal' or not value.strip():return 'break'
        if not re.fullmatch(r'[A-Za-z0-9_+#.-]{1,40}',language):language='text'
        start,end=self.index(start),self.index(end)
        prefix='\n' if self.compare(start,'!=',start+' linestart') else ''
        suffix='\n' if self.compare(end,'!=','end-1c') else ''
        # A pasted code fragment can itself contain fences. A longer outer
        # fence preserves those as literal code instead of ending the block.
        longest=max((len(match.group()) for match in re.finditer(r'`+',value)),default=0)
        fence='`'*max(3,longest+1)
        content=fence+language+'\n'+value+('' if value.endswith('\n') else '\n')+fence
        self.edit_separator();self.delete(start,end)
        self.insert(start,prefix+content+suffix)
        a=self.index(f'{start}+{len(prefix)+len(fence)+len(language)+1}c')
        b=self.index(f'{a}+{len(value)}c')
        # Don't inherit an old literal-code span when replacing it.
        self.tag_remove('code',start,self.index(f'{start}+{len(prefix+content+suffix)}c'))
        self.mark_set('insert',a);self.edit_separator()
        self.edit_modified(True);self.see('insert');self.focus_set();return 'break'

    def _offset_index(self,text,offset):
        # Text index columns/count('chars') expose UTF-16 units on Tk 8.6,
        # but its +Nc traversal counts Unicode scalars. Do not confuse them:
        # a surrogate-pair multiplier shifts every format after an emoji.
        return '1.0+'+str(offset)+'c'

    def load(self,body='',rich=''):
        if hasattr(self,'_selection_tools'):self._selection_tools.reset()
        self.configure(state='normal'); self.delete('1.0','end')
        self.attachments=[]; self.legacy_body=body
        document=validate_document(rich)
        if document:
            text=document['text']; self.insert('1.0',text)
            for span in document.get('spans',[]):
                self.tag_add(span['tag'],self._offset_index(text,span['start']),self._offset_index(text,span['end']))
            self.attachments=list(document.get('attachments',[]))
            self.legacy_body=document.get('legacy_body','')
        else:
            # Preserve the complete old Markdown. Hiding/display styling is
            # reversible and does not rewrite its source on load or save.
            self.insert('1.0',body)
        self.edit_reset(); self.edit_modified(False)
        if hasattr(self,'_live'):self._live.reset()

    def document(self):
        text=self.get('1.0','end-1c'); spans=[]
        for tag in TAGS:
            ranges=self.tag_ranges(tag)
            for start,end in zip(ranges[::2],ranges[1::2]):
                a=len(self.get('1.0',start));b=len(self.get('1.0',end))
                if a<b:spans.append({'tag':tag,'start':a,'end':b})
        value={'version':1,'text':text,'spans':spans,'attachments':self.attachments}
        if self.legacy_body:value['legacy_body']=self.legacy_body
        raw=json.dumps(value,ensure_ascii=False,separators=(',',':'))
        validate_document(raw)
        return raw

    def toggle(self,tag):
        if self.cget('state')!='normal':return 'break'
        try:a,b=self.index('sel.first'),self.index('sel.last')
        except tk.TclError:a,b=self.index('insert wordstart'),self.index('insert wordend')
        if self.compare(a,'==',b):return 'break'
        if tag in self.tag_names(a):self.tag_remove(tag,a,b)
        else:self.tag_add(tag,a,b)
        self.edit_modified(True);self.focus_set();return 'break'

    def block(self,tag):
        if self.cget('state')!='normal':return 'break'
        try:a,b=self.index('sel.first linestart'),self.index('sel.last lineend')
        except tk.TclError:a,b=self.index('insert linestart'),self.index('insert lineend')
        if tag.startswith('h'):
            for level in range(1,7):self.tag_remove('h'+str(level),a,b)
        self.tag_add(tag,a,b);self.edit_modified(True);self.focus_set();return 'break'

    def bullets(self):
        if self.cget('state')!='normal':return 'break'
        try:a,b=self.index('sel.first linestart'),self.index('sel.last lineend')
        except tk.TclError:a,b=self.index('insert linestart'),self.index('insert lineend')
        first,last=int(a.split('.')[0]),int(b.split('.')[0])
        self.edit_separator()
        for line in range(first,last+1):
            index=str(line)+'.0'
            if self.get(index,index+'+2c')!='• ':self.insert(index,'• ')
            self.tag_add('bullet',index,index+' lineend')
        self.edit_separator();self.edit_modified(True);self.focus_set();return 'break'
