"""Selection tools around the one native editor, never another text model."""
from __future__ import annotations

import tkinter as tk
from . import theme
from .diary_widgets import _button, _frame, _label
from .dpi import pixels
from .settings_card import SettingsCard


class JournalSelectionTools:
    def __init__(self, editor, *, code, emoji, attachment):
        self.editor=editor
        self.actions={'code':code,'emoji':emoji,'attachment':attachment}
        self.popup=None;self._job=None;self._focus_job=None;self._restoring=False;self._closed=False
        self._marks=('journal_selection_start','journal_selection_end')
        self._bindings=[]
        for sequence,handler in (
            ('<ButtonRelease-1>',self._schedule),('<<Selection>>',self._schedule),
            ('<KeyRelease>',self._schedule),('<Button-3>',self._context),
            ('<Shift-F10>',self._context),('<Menu>',self._context),
            ('<MouseWheel>',self.dismiss),('<Button-4>',self.dismiss),
            ('<Button-5>',self.dismiss),('<ButtonPress-1>',self.dismiss),
            ('<Unmap>',self.dismiss),('<<ThemeChanged>>',self.dismiss),
            ('<FocusOut>',self._lost_focus),('<Destroy>',self._destroy)):
            # Replace only the generic Text context menu; the other native
            # selection / editing bindings remain intact.
            self._bindings.append((sequence,editor.bind(sequence,handler,add=sequence!='<Button-3>')))

    def _range(self):
        try:
            a,b=self.editor.index('sel.first'),self.editor.index('sel.last')
            return (a,b) if self.editor.compare(a,'<',b) else None
        except tk.TclError:return None

    def _remember(self):
        selection=self._range()
        if not selection:return False
        for name,index,gravity in zip(self._marks,selection,('left','right')):
            self.editor.mark_set(name,index);self.editor.mark_gravity(name,gravity)
        return True

    def _restore(self):
        if not all(mark in self.editor.mark_names() for mark in self._marks):return
        self._restoring=True
        try:
            self.editor.tag_remove('sel','1.0','end')
            self.editor.tag_add('sel',*self._marks)
        finally:self._restoring=False

    def _schedule(self,event=None):
        if self._closed or self._restoring:return
        if event is not None and event.type==tk.EventType.KeyRelease and event.keysym in ('Escape','Menu','F10'):return
        if self._job is None:self._job=self.editor.after_idle(self._selection_changed)

    def _selection_changed(self):
        self._job=None
        if self._closed:return
        if self.popup is not None and self.popup.full:return
        selected=self._range()
        if (not selected or not self.editor.winfo_ismapped()
                or self.editor.cget('state')!='normal' or self.editor.focus_get() is not self.editor):
            self.dismiss();return
        box=self.editor.bbox(selected[0])
        if not box:self.dismiss();return
        if self.popup is not None and self.popup.selection==selected:return
        self.dismiss();self._remember()
        self.popup=_SelectionPopup(self,False,selected)
        self.popup.place_near(self.editor.winfo_rootx()+box[0],self.editor.winfo_rooty()+box[1],above=True)

    def _context(self,event):
        if self.editor.cget('state')!='normal':return
        self.dismiss()
        if not self._remember():
            if event.type==tk.EventType.ButtonPress:
                self.editor.mark_set('insert',self.editor.index(f'@{event.x},{event.y}'))
            for mark in self._marks:self.editor.mark_set(mark,'insert')
        selected=self._range()
        self.popup=_SelectionPopup(self,True,selected)
        if event.type==tk.EventType.ButtonPress:x,y=event.x_root,event.y_root
        else:
            box=self.editor.bbox('insert') or (8,8,1,20)
            x,y=self.editor.winfo_rootx()+box[0],self.editor.winfo_rooty()+box[1]+box[3]
        self.popup.place_near(x,y)
        self.popup.grab_set();self.popup.buttons[0].focus_set()
        return 'break'

    def more(self):
        popup=self.popup
        if popup is None:return
        x,y=popup.winfo_rootx(),popup.winfo_rooty()+popup.winfo_height()
        selected=popup.selection;self.dismiss()
        self.popup=_SelectionPopup(self,True,selected);self.popup.place_near(x,y)
        self.popup.grab_set();self.popup.buttons[0].focus_set()

    def run(self,action):
        self._restore();self.dismiss()
        editor=self.editor
        if not editor.winfo_exists() or editor.cget('state')!='normal':return
        editor.focus_set()
        if action in ('bold','italic','highlight'):editor.toggle(action)
        elif action.startswith('h') and action[1:].isdigit():editor.block(action)
        elif action=='quote':editor.block('quote')
        elif action=='bullet':editor.bullets()
        elif action=='clear':
            from ..plaza.journal_document import TAGS
            selection=self._range()
            if selection:
                for tag in TAGS:editor.tag_remove(tag,*selection)
                editor.edit_modified(True)
        elif action=='copy':
            from ..clipboard import copy_text
            selection=self._range()
            if selection:copy_text(editor,editor.get(*selection))
        elif action in ('cut','paste','all'):
            editor.event_generate({'cut':'<<Cut>>','paste':'<<Paste>>','all':'<<SelectAll>>'}[action])
        elif action in self.actions:self.actions[action]()

    def _lost_focus(self,_event=None):
        if self._closed:return
        if self._focus_job is None:self._focus_job=self.editor.after_idle(self._check_focus)

    def _check_focus(self):
        self._focus_job=None
        if self._closed:return
        focus=self.editor.focus_get()
        if focus is self.editor:return
        if self.popup is not None and focus is not None and focus.winfo_toplevel() is self.popup:return
        self.dismiss()

    def dismiss(self,_event=None):
        popup,self.popup=self.popup,None
        if popup is not None and popup.winfo_exists():
            try:popup.grab_release()
            except tk.TclError:pass
            popup.destroy()

    def reset(self):
        self.dismiss()
        if self._job is not None:self.editor.after_cancel(self._job);self._job=None
        if self._focus_job is not None:self.editor.after_cancel(self._focus_job);self._focus_job=None
        self.editor.mark_unset(*self._marks)

    def _destroy(self,event):
        if event.widget is not self.editor:return
        self._closed=True;self.dismiss()
        if self._job is not None:self.editor.after_cancel(self._job);self._job=None
        if self._focus_job is not None:self.editor.after_cancel(self._focus_job);self._focus_job=None


class _SelectionPopup(tk.Toplevel):
    def __init__(self,tools,full,selection):
        super().__init__(tools.editor)
        self.tools=tools;self.full=full;self.selection=selection;self.buttons=[]
        self.withdraw();self.overrideredirect(True);self.transient(tools.editor.winfo_toplevel())
        self.configure(bg=theme.APP_BG)
        self._remchat_locale_exclude=True
        card=SettingsCard(self,shadow=True,inset=8,radius=12)
        card.pack(fill='both',expand=True)
        body=card.body
        groups=(('文字格式',(('B 加粗','bold'),('I 斜体','italic'),('马克笔高亮','highlight'))),
                ('段落',tuple((f'标题 {level}','h'+str(level)) for level in range(1,7))+(('• 列表','bullet'),('❝ 引用','quote'))),
                ('插入',(('代码块','code'),('表情','emoji'),('附件','attachment'))))
        if full:
            for row,(label,items) in enumerate(groups):
                _label(body,label,size=9,muted=True).grid(row=row*2,column=0,sticky='w',pady=(5,4))
                group=_frame(body);group.grid(row=row*2+1,column=0,sticky='ew')
                for index,(text,action) in enumerate(items):
                    button=self._add(group,text,action)
                    button.grid(row=index//3,column=index%3,sticky='ew',padx=(0,4),pady=(0,4))
                for column in range(3):group.columnconfigure(column,weight=1)
            clipboard=_frame(body);clipboard.grid(row=6,column=0,sticky='ew',pady=(6,0))
            for index,(text,action) in enumerate((('剪切','cut'),('复制','copy'),('粘贴','paste'),('全选','all'))):
                button=self._add(clipboard,text,action)
                button.grid(row=0,column=index,padx=(0,4),sticky='ew')
                clipboard.columnconfigure(index,weight=1)
                if not selection and action in ('cut','copy'):button.configure(state='disabled')
            self._add(body,'清除手动格式','clear').grid(row=7,column=0,sticky='ew',pady=(5,0))
            self.bind('<Button-1>',self._outside,add=True)
            self.bind('<FocusOut>',lambda e:tools._lost_focus(),add=True)
            for sequence,amount in (('<Down>',1),('<Right>',1),('<Up>',-1),('<Left>',-1)):
                self.bind(sequence,lambda event,n=amount:self._step(n))
        else:
            for column,(label,action) in enumerate((('B','bold'),('马克笔','highlight'),('代码块','code'))):
                self._add(body,label,action).grid(row=0,column=column,padx=(0,4))
            button=_button(body,'⋯',tools.more);button.grid(row=0,column=3)
            button.configure(takefocus=False)
        self.bind('<Escape>',lambda e:(tools.dismiss(),tools.editor.focus_set(),'break')[-1])
        self.update_idletasks()
        width=body.winfo_reqwidth()+pixels(self,16)
        card.configure(width=width)
        self.update_idletasks()

    def _add(self,parent,label,action):
        button=_button(parent,label,lambda:self.tools.run(action))
        button.configure(padx=pixels(self,9),pady=pixels(self,6),takefocus=self.full)
        button.bind('<Return>',lambda event:self.tools.run(action))
        self.buttons.append(button);return button

    def place_near(self,x,y,above=False):
        self.update_idletasks();width,height=self.winfo_reqwidth(),self.winfo_reqheight()
        gap=pixels(self,8);screen_w,screen_h=self.winfo_screenwidth(),self.winfo_screenheight()
        # Prefer above the selection so selected words and the next line stay
        # readable. At the top edge, use the immediately following line.
        y=y-height-gap if above and y-height-gap>=gap else y+gap
        x=max(gap,min(x,screen_w-width-gap));y=max(gap,min(y,screen_h-height-pixels(self,48)))
        self.geometry(f'{width}x{height}+{x}+{y}');self.deiconify();self.lift()

    def _outside(self,event):
        if not(self.winfo_rootx()<=event.x_root<self.winfo_rootx()+self.winfo_width()
               and self.winfo_rooty()<=event.y_root<self.winfo_rooty()+self.winfo_height()):
            self.tools.dismiss();return 'break'

    def _step(self,amount):
        focus=self.focus_get();available=[button for button in self.buttons if button.cget('state')!='disabled']
        index=available.index(focus) if focus in available else -1
        if available:available[(index+amount)%len(available)].focus_set()
        return 'break'
