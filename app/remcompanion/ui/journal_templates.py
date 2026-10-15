"""Local journal template manager; weekday rules materialize once per day."""
import tkinter as tk
import sqlite3
from tkinter import ttk
from .localized_dialogs import messagebox
from .diary_widgets import _frame,_label,_button,_entry,_Toolbar
from .rich_journal_editor import RichJournalEditor
from .rounded_controls import style_checkbox
from . import theme
from ..plaza.journal_document import validate_document
from ..plaza.diary import DiaryError
from .locale_controller import translator_for
from ..i18n import weekday_labels


def edit_templates(parent,store,on_apply,on_saved):
    window=tk.Toplevel(parent);window.title('日记模板与固定待办');window.geometry('920x680');window.minsize(700,520)
    body=_frame(window);body.pack(fill='both',expand=True,padx=18,pady=18)
    body.columnconfigure(1,weight=1);body.rowconfigure(0,weight=1)
    rail=_frame(body);rail.grid(row=0,column=0,sticky='ns',padx=(0,18));rail.rowconfigure(1,weight=1)
    _label(rail,'我的模板',size=14,bold=True).grid(row=0,column=0,sticky='w',pady=(0,12))
    listing=ttk.Treeview(rail,show='tree',height=8);listing.column('#0',width=170);listing.grid(row=1,column=0,sticky='ns')
    form=_frame(body);form.grid(row=0,column=1,sticky='nsew');form.columnconfigure(0,weight=1);form.rowconfigure(7,weight=1)
    name=tk.StringVar(window);enabled=tk.BooleanVar(window,True);days=[tk.BooleanVar(window,False) for _ in range(7)]
    selected=[None]; records={}
    _label(form,'模板名称',bold=True).grid(row=0,column=0,sticky='w',pady=(0,5))
    title=_entry(form,name,width=1);title.grid(row=1,column=0,sticky='ew',pady=(0,12))
    bar=_frame(form);bar.grid(row=2,column=0,sticky='ew',pady=(0,12))
    check=tk.Checkbutton(bar,text='按星期自动填入新日记',variable=enabled,bg=theme.WHITE,fg=theme.INK,font=(theme.BODY_FONT,10));check.pack(anchor='w');style_checkbox(check)
    week=_frame(bar);week.pack(fill='x',pady=(5,0))
    for index,label in enumerate(weekday_labels(translator_for(parent).language, compact=True)):
        c=tk.Checkbutton(week,text=label,variable=days[index],bg=theme.WHITE,fg=theme.INK,font=(theme.BODY_FONT,10));c.pack(side='left',padx=3);style_checkbox(c)
    _label(form,'固定待办 · 每行一项',bold=True).grid(row=3,column=0,sticky='w',pady=(0,5))
    tasks=tk.Text(form,height=4,wrap='word',undo=True,bd=0,font=(theme.BODY_FONT,11),bg=theme.APP_BG,fg=theme.INK,insertbackground=theme.INK,padx=12,pady=10)
    tasks.grid(row=4,column=0,sticky='ew',pady=(0,10))
    _label(form,'日记正文模板',bold=True).grid(row=5,column=0,sticky='w')
    toolbar=_Toolbar(form);toolbar.grid(row=6,column=0,sticky='ew',pady=6)
    editor=RichJournalEditor(form,height=5,wrap='word',undo=True,bd=0,font=(theme.BODY_FONT,11),bg=theme.APP_BG,fg=theme.INK,insertbackground=theme.INK,padx=12,pady=10)
    editor.grid(row=7,column=0,sticky='nsew')
    for label,command in (('B 粗体',lambda:editor.toggle('bold')),('高亮',lambda:editor.toggle('highlight')),
                          ('H1',lambda:editor.block('h1')),('H2',lambda:editor.block('h2')),('• 列表',editor.bullets),('引用',lambda:editor.block('quote'))):toolbar.add(label,command)
    status=tk.StringVar(window,'');_label(form).grid(row=9,column=0,sticky='ew')
    form.grid_slaves(row=9)[0].configure(textvariable=status)

    def reload():
        records.clear();records.update({t['id']:t for t in store.templates()})
        listing.delete(*listing.get_children())
        for t in records.values():listing.insert('','end',iid=t['id'],text=t['name'])
        if selected[0] in records:listing.selection_set(selected[0])
    def fresh():
        selected[0]=None;listing.selection_remove(listing.selection());name.set('');tasks.delete('1.0','end');editor.load();enabled.set(True)
        for value in days:value.set(False)
        title.focus_set()
    def choose(_event=None):
        selection=listing.selection()
        if not selection:return
        t=records.get(selection[0])
        if not t:return
        selected[0]=t['id'];name.set(t['name']);enabled.set(bool(t['enabled']))
        for index,value in enumerate(days):value.set(index in t['weekdays'])
        tasks.delete('1.0','end');tasks.insert('1.0','\n'.join(t['tasks']))
        raw=t['body']
        if raw.startswith('{'):
            doc=validate_document(raw);editor.load(doc['text'],raw)
        else:editor.load(raw)
    def save():
        try:
            selected[0]=store.save_template(name=name.get(),body=editor.document(),
                weekdays=[i for i,v in enumerate(days) if v.get()],
                tasks=[line.strip() for line in tasks.get('1.0','end-1c').splitlines() if line.strip()],
                template_id=selected[0],enabled=enabled.get())
            reload();on_saved();status.set('已保存')
            return True
        except (DiaryError,OSError,sqlite3.Error) as error:status.set(str(error));return False
    def apply():
        if save():on_apply(records[selected[0]]);window.destroy()
    def remove():
        if selected[0] and messagebox.askyesno('删除模板','删除这个模板？已生成的每日待办和日记会保留。',parent=window):
            try:store.delete_template(selected[0]);fresh();reload();on_saved()
            except (DiaryError,OSError,sqlite3.Error) as error:status.set(str(error))
    listing.bind('<<TreeviewSelect>>',choose)
    _button(rail,'＋ 新模板',fresh).grid(row=2,column=0,sticky='ew',pady=(12,5))
    _button(rail,'删除模板',remove).grid(row=3,column=0,sticky='ew')
    actions=_Toolbar(form);actions.grid(row=8,column=0,sticky='ew',pady=(12,0))
    actions.add('保存模板',save,primary=True);actions.add('填入当前空白日记',apply);actions.add('关闭',window.destroy)
    reload();fresh()
