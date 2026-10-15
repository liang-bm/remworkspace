"""Explicit external app handoff, not impersonated RemChat OAuth access."""
import tkinter as tk
from ..integrations.presets import application_rows,application_link,COMMANDS_DOC
from ..platforms import native_module
from ..clipboard import copy_text
from . import theme
from .diary_widgets import _button,_frame,_label
from .settings_card import SettingsCard


class ApplicationPanelMixin:
    def _build_apps(self,page):
        body=_frame(page);body.pack(fill='both',expand=True,padx=20,pady=18)
        _label(body,'应用',size=14,bold=True).pack(anchor='w')
        self._label(body,'默认关闭。开启后在 Codex 桌面端完成登录；RemChat 不读取外部账户授权，也不会自动发送请求。')
        self.app_rows=[]
        self.app_list=self._resource_list(body,edit=self._open_app_detail,toggle=self._toggle_app)

    def _refresh_apps(self):
        self.app_rows=application_rows(self.manager.store.read().get('apps',[]))
        query=self.app_list._search_query.get().strip().casefold()
        rows=[row for row in self.app_rows if query in (row['name']+' '+row['description']).casefold()]
        self.app_list.set_rows(rows,state=lambda row:'已允许入口 · 授权在 Codex' if row['enabled'] else '未启用')
        self.tabs.tab(self.pages['apps'],text=f'应用  {len(self.app_rows)}')

    def _apps_controls(self):self.app_list.set_busy(self.busy)

    def _toggle_app(self,row,desired):
        if self.busy:return
        def save():self.manager.store.update_item('apps',{'id':row['id'],'enabled':bool(desired)})
        def complete(_):
            self._refresh_apps()
            self.status.set('已允许此应用入口；请在 Codex 完成授权。' if desired else '入口已关闭；不会撤销你在外部应用中的授权。')
            if desired:self._launch_app(row,connect=True)
        self._work('正在保存入口设置…',save,complete)

    def _launch_app(self,row,*,connect=False):
        enabled=next((item['enabled'] for item in application_rows(self.manager.store.read().get('apps',[])) if item['id']==row['id']),False)
        if not enabled:self.status.set('请先打开此应用入口的开关。');return
        uri=application_link(row['id'],connect=connect)
        def launch():return native_module('shell').open_uri(uri)
        def complete(_):
            self.status.set('已打开 Codex 连接设置；外部授权完成后再打开应用。' if connect else
                            '已打开 Codex 草稿，尚未发送；可用功能和授权以 Codex 的实际结果为准。')
        self._work('正在打开原生应用入口…',launch,complete)

    def _open_app_detail(self,row):
        if isinstance(row,tk.Event):return
        window=tk.Toplevel(self);window.title(row['name']);window.transient(self.winfo_toplevel())
        window._remchat_title_exclude = True
        window.configure(bg=theme.APP_BG);window.geometry('570x320');window.minsize(390,250)
        card=SettingsCard(window,stretch=True,inset=18);card.pack(fill='both',expand=True,padx=10,pady=10)
        body=card.body
        _label(body,row['name'],size=15,bold=True).pack(anchor='w')
        for text in (row['description'],
                     '在 Codex 中连接和使用；不共享或复制登录令牌。打开应用只填写草稿，是否发送由你决定。',
                     '此功能由 Codex 内置工具提供，没有公开的独立 RemChat 授权接口。' if not row['plugin'] else
                     '连接入口会打开官方插件安装/登录流程；已有授权由 Codex 自行识别。'):
            label=_label(body,text,muted=True);label.configure(justify='left',wraplength=510)
            label.pack(fill='x',pady=(8,0))
            label.bind('<Configure>',lambda event:e_wrap(event))
        bar=_frame(body);bar.pack(fill='x',pady=(16,0))
        connect=_button(bar,'连接 / 登录',lambda:self._launch_app(row,connect=True));connect.pack(side='left',padx=(0,7))
        use=_button(bar,'打开应用',lambda:self._launch_app(row),primary=True);use.pack(side='left',padx=(0,7))
        copy=_button(bar,'复制入口链接',lambda:copy_text(window,application_link(row['id'])));copy.pack(side='left')
        self._flow_buttons(bar,(connect,use,copy))
        _button(body,'官方连接说明',lambda:self._guard(lambda:native_module('shell').open_uri(COMMANDS_DOC))).pack(anchor='w',pady=(8,0))
        window.bind('<Escape>',lambda e:window.destroy())

def e_wrap(event):event.widget.configure(wraplength=max(120,event.width-4))
