"""A curated offline catalog; selecting a template only fills a disabled draft."""
import tkinter as tk
from tkinter import ttk
from .localized_dialogs import filedialog
from ..integrations.presets import MCP_PRESETS,mcp_preset
from ..platforms import native_module
from . import theme
from .diary_widgets import _button,_frame,_label
from .scroll_surface import ScrollCanvas


def show_presets(panel):
    if panel.busy:return
    previous=getattr(panel,'_mcp_presets_window',None)
    if previous is not None and previous.winfo_exists():previous.lift();return
    window=tk.Toplevel(panel);panel._mcp_presets_window=window
    window.title('MCP · 常用预设');window.transient(panel.winfo_toplevel())
    window.configure(bg=theme.WHITE);window.geometry('620x640');window.minsize(390,280)
    _label(window,'常用预设',size=15,bold=True).pack(anchor='w',padx=18,pady=(16,4))
    _label(window,'只填入草稿，默认关闭；连接时才可能启动程序或下载依赖。',muted=True).pack(anchor='w',padx=18,pady=(0,10))
    host=_frame(window);host.pack(fill='both',expand=True)
    canvas=ScrollCanvas(host,bg=theme.WHITE,bd=0,highlightthickness=0)
    scrollbar=ttk.Scrollbar(host,command=canvas.yview);scrollbar.pack(side='right',fill='y')
    canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scrollbar.set)
    body=_frame(canvas);slot=canvas.create_window(0,0,window=body,anchor='nw')
    canvas.bind('<Configure>',lambda event:canvas.itemconfigure(slot,width=event.width))
    body.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
    for preset in MCP_PRESETS:
        row=_frame(body);row.pack(fill='x',padx=18,pady=(10,12))
        _label(row,preset['name'],size=12,bold=True).pack(anchor='w')
        for text in (preset['description'],preset['setup']):
            label=_label(row,text,muted=True);label.configure(justify='left',wraplength=550)
            label.pack(fill='x',pady=(4,0))
            label.bind('<Configure>',lambda event:event.widget.configure(wraplength=max(120,event.width-4)))
        actions=_frame(row);actions.pack(fill='x',pady=(7,0))
        _button(actions,'使用预设',lambda value=preset:_choose(panel,window,value),primary=True).pack(side='left',padx=(0,8))
        _button(actions,'官方文档',lambda value=preset:panel._guard(lambda:native_module('shell').open_uri(value['docs']))).pack(side='left')
    def wheel(event):
        amount=-int(event.delta/120) if event.num not in (4,5) else (-1 if event.num==4 else 1)
        if amount:canvas.yview_scroll(amount,'units')
        return 'break'
    def enrol(widget):
        for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):widget.bind(sequence,wheel,add=True)
        for child in widget.winfo_children():enrol(child)
    enrol(canvas)
    window.bind('<Escape>',lambda event:window.destroy())

def _choose(panel,window,preset):
    if panel.busy or not panel._discard_mcp():return
    directory=None
    if preset.get('directory'):
        directory=filedialog.askdirectory(parent=window,title='选择此 MCP 明确允许访问的工作目录')
        if not directory:return
    try:value=mcp_preset(preset['id'],panel.manager.store.root,directory=directory,
                         existing={row['id'] for row in panel.manager.list_mcp()})
    except (OSError,ValueError) as error:panel.status.set(str(error));return
    panel._clear_catalog();panel._mcp_loaded_id=''
    panel.mcp_list.selection_clear(0,'end');panel.mcp_form.load(value)
    panel.mcp_enabled.set(False);panel.mcp_state.set('预设已填入 · 默认关闭，尚未保存或连接')
    panel._show_mcp_editor(new=True);panel._mcp_controls()
    panel.status.set(preset['setup']);window.destroy()
