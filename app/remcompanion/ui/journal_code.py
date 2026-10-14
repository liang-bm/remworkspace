"""Insert/edit a rich, literal code range; never execute diary contents."""
import tkinter as tk
from tkinter import ttk
from . import theme
from .diary_widgets import _button, _label
from .soft_fields import SoftCombobox
from .journal_syntax import LANGUAGES, language_name
from .settings_card import SettingsCard
from ..text_editing import bind_text_editing

def edit_code_block(parent,editor):
    if editor.cget('state')!='normal':return
    current=getattr(editor,'_code_dialog',None)
    if current is not None and current.winfo_exists():current.lift();return
    code=editor._live.code_at() if hasattr(editor,'_live') else None
    selected=False
    try:
        start,end=editor.index('sel.first'),editor.index('sel.last');selected=True
    except tk.TclError:
        if code:
            start,end=editor._live.plan.index(code.start),editor._live.plan.index(code.end)
        else:start=end=editor.index('insert')
    existing=(editor._live.plan.source[code.code_start:code.code_end] if code and not selected else editor.get(start,end))
    window=tk.Toplevel(parent);window.title('代码块');window.transient(parent.winfo_toplevel());window.configure(bg=theme.APP_BG)
    editor._code_dialog=window
    navigation=getattr(parent,'_navigation_serial',None)
    window.geometry('680x440');window.minsize(400,280)
    card=SettingsCard(window,stretch=True,inset=18);card.pack(fill='both',expand=True,padx=12,pady=12)
    body=card.body;body.columnconfigure(0,weight=1);body.rowconfigure(1,weight=1)
    heading=tk.Frame(body,bg=theme.WHITE);heading.grid(row=0,column=0,sticky='ew',pady=(0,12));heading.columnconfigure(0,weight=1)
    _label(heading,'代码块',size=13,bold=True).grid(row=0,column=0,sticky='w')
    language=tk.StringVar(window,language_name(code.language) if code and not selected else '纯文本')
    choice=SoftCombobox(heading,state='readonly',width=14,textvariable=language,values=[name for _,name in LANGUAGES])
    choice.grid(row=0,column=1,sticky='e')
    content=tk.Frame(body,bg=theme.WHITE);content.grid(row=1,column=0,sticky='nsew');content.columnconfigure(0,weight=1);content.rowconfigure(0,weight=1)
    text=tk.Text(content,wrap='none',font=('Consolas',11),bg=theme.APP_BG,fg=theme.INK,insertbackground=theme.INK,relief='flat',bd=0,padx=12,pady=10,undo=True,width=1,height=1)
    text.grid(row=0,column=0,sticky='nsew');text.insert('1.0',existing);bind_text_editing(text)
    vertical=ttk.Scrollbar(content,command=text.yview);vertical.grid(row=0,column=1,sticky='ns')
    horizontal=ttk.Scrollbar(content,orient='horizontal',command=text.xview);horizontal.grid(row=1,column=0,sticky='ew')
    text.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
    bar=tk.Frame(body,bg=theme.WHITE);bar.grid(row=2,column=0,sticky='e',pady=(12,0))
    # Marks follow edits while the dialog is open; stale numeric indices do not.
    editor.mark_set('code_dialog_start',start);editor.mark_gravity('code_dialog_start','left')
    editor.mark_set('code_dialog_end',end);editor.mark_gravity('code_dialog_end','right')
    def close():
        if editor.winfo_exists():editor.mark_unset('code_dialog_start','code_dialog_end')
        editor._code_dialog=None
        window.destroy()
    def apply():
        if (editor.winfo_exists() and editor.cget('state')=='normal'
                and getattr(parent,'_navigation_serial',None)==navigation):
            identifier=next((identifier for identifier,name in LANGUAGES if name==language.get()),'text')
            editor.replace_code('code_dialog_start','code_dialog_end',text.get('1.0','end-1c'),identifier);editor.focus_set()
        close()
    _button(bar,'取消',close).pack(side='left',padx=(0,8));_button(bar,'插入 / 更新',apply,primary=True).pack(side='left')
    window.protocol('WM_DELETE_WINDOW',close);window.bind('<Escape>',lambda e:close());window.bind('<Control-Return>',lambda e:apply());text.focus_set()
