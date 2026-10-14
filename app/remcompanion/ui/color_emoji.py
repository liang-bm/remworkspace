"""Offline colored emoji presentation without replacing stored Unicode text.

Small Labels sit over visible glyph boxes, not Text image/window segments.
Consequently native copy, undo, IME, ASR, offsets and JSON remain ordinary text.
Only visible lines are scanned; code stays literal and unsupported emoji retain
the system glyph. Graphics: Twemoji 17.0.2, CC-BY-4.0 (bundled attribution).
"""
from functools import lru_cache
import json
from pathlib import Path
import re
import tkinter as tk
import tkinter.font as tkfont
from .font_source import native_font
from PIL import Image, ImageTk
from . import theme
from .dpi import pixels

DIRECTORY=Path(__file__).resolve().parents[1]/'assets'/'emoji'

@lru_cache(maxsize=1)
def catalog():
    try:rows=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))['assets']
    except (OSError,ValueError,KeyError):return {}
    return {row['emoji']:row['file'] for row in rows}

@lru_cache(maxsize=128)
def _bitmap(filename,size):
    with Image.open(DIRECTORY/filename) as image:
        return image.convert('RGBA').resize((size,size),Image.Resampling.LANCZOS)

def emoji_photo(widget,value,size):
    filename=catalog().get(value)
    if not filename:return None
    try:return ImageTk.PhotoImage(_bitmap(filename,size),master=widget)
    except OSError:return None

class ColorEmojiText:
    def __init__(self,text):
        self.text=text;self._job=None;self._labels=[];self._command=None
        self._paint_key=None;self._view=None
        values=sorted(catalog(),key=len,reverse=True)
        self._pattern=re.compile('|'.join(re.escape(value) for value in values)) if values else None
        self._tag='EmojiView'+str(id(self))
        text.bindtags((*text.bindtags(),self._tag))
        for event in ('<Configure>','<Map>','<KeyRelease>','<<Modified>>','<ButtonRelease-1>'):
            text.bind_class(self._tag,event,self.schedule,add=True)
        text.bind('<Destroy>',self._destroy,add=True)
        text.configure(yscrollcommand=self._view_changed)
        self.schedule()

    def set_scroll_command(self,command):self._command=command

    def _view_changed(self,first,last):
        self._view=(first,last)
        if callable(self._command):self._command(first,last)
        elif self._command:self.text.tk.call(*self.text.tk.splitlist(self._command),first,last)
        self.schedule()

    def schedule(self,_event=None):
        if self._job is None:self._job=self.text.after_idle(self.draw)

    def draw(self):
        self._job=None;text=self.text
        if not text.winfo_exists():return
        # Elision is deliberately not used: the real emoji owns its caret width.
        if not text.winfo_ismapped() or self._pattern is None:
            for label in self._labels:label.place_forget()
            self._paint_key=None
            return
        start=text.index('@0,0 linestart');end=text.index(f'@{text.winfo_width()},{text.winfo_height()} lineend')
        source=text.get(start,end);font=native_font(text,font=text.cget('font'))
        key=(start,end,source,text.winfo_width(),text.winfo_height(),self._view,
             text.cget('font'),text.cget('background'),theme.get_mode(),str(text.tag_ranges('sel')),
             tuple(text.dump(start,end,text=False,tag=True)))
        if key==self._paint_key:return
        self._paint_key=key
        for label in self._labels:label.place_forget()
        size=max(12,font.metrics('linespace')-pixels(text,2));used=0
        for match in self._pattern.finditer(source):
            following=source[match.end():match.end()+1]
            if (following and (following=='\u200d' or 0x1f3fb<=ord(following)<=0x1f3ff)) or source[match.start()-1:match.start()]=='\u200d':continue
            index=text.index(f'{start}+{match.start()}c')
            if any(tag in ('code', '_live:code', '_live:inlinecode') for tag in text.tag_names(index)):continue
            box=text.bbox(index)
            if not box or box[1]<0 or box[1]+box[3]>text.winfo_height():continue
            # Bound the number of native overlay children even for emoji walls.
            if used>=128:break
            color=text.cget('background')
            for tag in text.tag_names(index):
                candidate=text.tag_cget(tag,'background')
                if candidate:color=candidate
            if used==len(self._labels):
                label=tk.Label(text,bd=0,padx=0,pady=0,takefocus=False)
                label._remchat_theme_exclude=True
                for event in ('<Button-1>','<B1-Motion>','<ButtonRelease-1>','<MouseWheel>'):
                    label.bind(event,lambda e,sequence=event:self._forward(e,sequence))
                self._labels.append(label)
            label=self._labels[used];photo=emoji_photo(label,match.group(),min(size,box[3]))
            if photo is None:continue
            label.configure(image=photo,bg=color);label._emoji_photo=photo
            # bbox already includes Text padx/pady. Place's default 'inside'
            # adds that internal border again, duplicating the glyph offset.
            label.place(x=box[0],y=box[1],width=max(box[2],photo.width()),height=box[3],bordermode='ignore')
            used+=1

    def _forward(self,event,sequence):
        self.text.focus_set()
        options={'x':event.x_root-self.text.winfo_rootx(),'y':event.y_root-self.text.winfo_rooty()}
        if sequence=='<MouseWheel>':options['delta']=event.delta
        self.text.event_generate(sequence,**options)
        return 'break'

    def _destroy(self,event):
        if event.widget is not self.text:return
        if self._job is not None:self.text.after_cancel(self._job)
        for sequence in ('<Configure>','<Map>','<KeyRelease>','<<Modified>>','<ButtonRelease-1>'):
            self.text.unbind_class(self._tag,sequence)
        for label in self._labels:label._emoji_photo=None
        self._labels.clear()

def show_emoji_picker(anchor,editor):
    existing=getattr(editor,'_emoji_picker',None)
    if existing is not None and existing.winfo_exists():existing.destroy();return
    from .diary_widgets import _button
    window=tk.Toplevel(anchor);editor._emoji_picker=window
    window.title('选择表情');window.transient(anchor.winfo_toplevel());window.configure(bg=theme.WHITE)
    window.resizable(False,False)
    box=tk.Frame(window,bg=theme.WHITE);box.pack(padx=12,pady=12)
    def insert(value):
        if editor.winfo_exists() and editor.cget('state')=='normal':
            editor.insert('insert',value);editor.see('insert');editor.focus_set()
        window.destroy()
    for i,value in enumerate(catalog()):
        photo=emoji_photo(box,value,pixels(anchor,27))
        button=_button(box,'',lambda emoji=value:insert(emoji));button._emoji_photo=photo
        button.configure(image=photo,padx=7,pady=7);button.grid(row=i//10,column=i%10,padx=2,pady=2)
    window.bind('<Escape>',lambda e:window.destroy())
    window.update_idletasks()
    x=min(anchor.winfo_rootx(),max(0,window.winfo_screenwidth()-window.winfo_reqwidth()-20))
    y=min(anchor.winfo_rooty()+anchor.winfo_height(),max(0,window.winfo_screenheight()-window.winfo_reqheight()-40))
    window.geometry(f'+{max(0,x)}+{max(0,y)}')
