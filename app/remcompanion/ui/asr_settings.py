"""Dedicated ASR settings; saving does not record, upload or download."""
from dataclasses import replace
import tkinter as tk
from .localized_dialogs import filedialog
import webbrowser
from . import theme
from .diary_widgets import _frame,_label,_entry,_button,_Toolbar
from .combobox_popup import AnchoredCombobox
from .settings_card import SettingsCard
from ..asr import AsrSettings,AsrSettingsStore
from ..model_providers import ModelProviderRepository

MODES={'关闭':'off','在线 ASR':'online','本地 ASR 服务':'local_service','本地 Whisper.cpp':'whisper_cpp'}

class AsrSettingsPanel(tk.Frame):
    def __init__(self,parent,root_path):
        super().__init__(parent,bg=theme.APP_BG)
        self.store=AsrSettingsStore(root_path);self.root_path=root_path;self.error=''
        try:self.settings=self.store.load()
        except (OSError,ValueError,TypeError) as error:self.settings=AsrSettings();self.error=str(error)
        self.mode=tk.StringVar(self,next(label for label,value in MODES.items() if value==self.settings.mode))
        self.endpoint=tk.StringVar(self,self.settings.endpoint);self.model=tk.StringVar(self,self.settings.model)
        self.key=tk.StringVar(self);self.engine=tk.StringVar(self,self.settings.executable);self.model_file=tk.StringVar(self,self.settings.model_file)
        self.language=tk.StringVar(self,self.settings.language);self.timeout=tk.StringVar(self,str(self.settings.timeout))
        self.status=tk.StringVar(self,'未启用' if self.settings.mode=='off' else '已保存')
        self.providers={'独立 ASR 密钥':'asr-input'}
        for provider in ModelProviderRepository(root_path).load().providers:
            if provider.kind=='remote' and provider.usage_policy!='coding_only':self.providers['已保存 · '+provider.display_name]=provider.provider_id
        self.key_source=tk.StringVar(self,next((label for label,value in self.providers.items() if value==self.settings.provider_id),'独立 ASR 密钥'))
        card=SettingsCard(self,inset=18);card.pack(fill='x',pady=(0,16));body=card.body
        body.columnconfigure(0,weight=1)
        _label(body,'语音输入',size=16,bold=True).grid(row=0,column=0,sticky='w',pady=(0,10))
        self.choice=AnchoredCombobox(body,state='readonly',textvariable=self.mode,values=tuple(MODES),width=1)
        self.choice.grid(row=1,column=0,sticky='ew',pady=(0,14));self.choice.bind('<<ComboboxSelected>>',self._mode_changed)
        _label(body,'录音由你启动；停止后转成文字，确认后再发送。',muted=True).grid(row=2,column=0,sticky='ew',pady=(0,14))
        self.online=_frame(body);self.online.grid(row=3,column=0,sticky='ew');self.online.columnconfigure(0,weight=1)
        self._field(self.online,0,'转写接口',self.endpoint)
        self._field(self.online,2,'ASR 模型 ID',self.model)
        _label(self.online,'密钥来源',bold=True).grid(row=4,column=0,sticky='w',pady=(10,5))
        AnchoredCombobox(self.online,state='readonly',values=tuple(self.providers),textvariable=self.key_source,width=1).grid(row=5,column=0,sticky='ew')
        self.key_entry=self._field(self.online,6,'独立 API Key · 留空保留原值',self.key,show='•')
        _button(self.online,'硅基流动 TeleSpeechASR',self._siliconflow).grid(row=8,column=0,sticky='w',pady=(8,0))
        self.local=_frame(body);self.local.grid(row=4,column=0,sticky='ew');self.local.columnconfigure(0,weight=1)
        self._file_field(self.local,0,'Whisper.cpp 引擎（whisper-cli.exe）',self.engine)
        self._file_field(self.local,2,'本地 GGML 模型（.bin）',self.model_file)
        links=_Toolbar(self.local);links.grid(row=4,column=0,sticky='ew',pady=(8,0))
        links.add('获取开源引擎',lambda:webbrowser.open('https://github.com/ggml-org/whisper.cpp/releases'))
        links.add('下载本地模型',lambda:webbrowser.open('https://huggingface.co/ggerganov/whisper.cpp/tree/main'))
        limits=_frame(body);limits.grid(row=5,column=0,sticky='ew',pady=(14,0));limits.columnconfigure(0,weight=1);limits.columnconfigure(1,weight=1)
        _label(limits,'识别语言',bold=True).grid(row=0,column=0,sticky='w')
        AnchoredCombobox(limits,state='readonly',values=('auto','zh','en','ja'),textvariable=self.language,width=1).grid(row=1,column=0,sticky='ew',padx=(0,10))
        _label(limits,'转写超时（秒）',bold=True).grid(row=0,column=1,sticky='w')
        _entry(limits,self.timeout,width=1).grid(row=1,column=1,sticky='ew')
        actions=_Toolbar(body);actions.grid(row=6,column=0,sticky='ew',pady=(16,0));actions.add('保存语音输入设置',self.save,primary=True)
        actions.add('清除独立 ASR 密钥',self.clear_key)
        label=_label(body,muted=True);label.configure(textvariable=self.status,wraplength=550);label.grid(row=7,column=0,sticky='ew',pady=(8,0))
        self._mode_changed()
        if self.error:self.status.set('配置无法读取，原文件保留 · '+self.error)

    def _field(self,parent,row,label,variable,**options):
        _label(parent,label,bold=True).grid(row=row,column=0,sticky='w',pady=(8,5))
        entry=_entry(parent,variable,width=1,**options);entry.grid(row=row+1,column=0,sticky='ew');return entry
    def _file_field(self,parent,row,label,variable):
        _label(parent,label,bold=True).grid(row=row,column=0,sticky='w',pady=(8,5))
        frame=_frame(parent);frame.grid(row=row+1,column=0,sticky='ew');frame.columnconfigure(0,weight=1)
        _entry(frame,variable,width=1).grid(row=0,column=0,sticky='ew',padx=(0,8))
        _button(frame,'选择文件',lambda:self._choose_file(variable)).grid(row=0,column=1)
    def _choose_file(self,variable):
        path=filedialog.askopenfilename(parent=self,title='选择已下载的本地 ASR 文件')
        if path:variable.set(path)
    def _siliconflow(self):
        self.mode.set('在线 ASR');self.endpoint.set(AsrSettings().endpoint);self.model.set('TeleAI/TeleSpeechASR');self._mode_changed()
    def _mode_changed(self,_event=None):
        mode=MODES[self.mode.get()]
        self.online.grid() if mode in ('online','local_service') else self.online.grid_remove()
        self.local.grid() if mode=='whisper_cpp' else self.local.grid_remove()
        if mode=='local_service' and self.endpoint.get()==AsrSettings().endpoint:self.endpoint.set('http://127.0.0.1:8080/v1/audio/transcriptions')
    def save(self):
        try:
            value=AsrSettings(mode=MODES[self.mode.get()],endpoint=self.endpoint.get().strip(),model=self.model.get().strip(),
                provider_id=self.providers[self.key_source.get()],executable=self.engine.get().strip(),model_file=self.model_file.get().strip(),
                language=self.language.get(),timeout=int(self.timeout.get()))
            self.store.save(value,self.key.get());self.key.set('');self.settings=value
            if value.mode=='off':
                for controller in getattr(self._root(),'_remchat_speech_controllers',{}).values():controller.cancel()
            self.status.set('已保存 · 仅在点击录音后识别')
        except (OSError,ValueError,TypeError) as error:self.status.set('未保存 · '+str(error))
    def clear_key(self):
        from tkinter import messagebox
        if not messagebox.askyesno('清除 ASR 密钥','仅清除独立 ASR 密钥，不改变聊天供应商的密钥。',parent=self):return
        from ..asr import checked_secret_store
        try:checked_secret_store(self.root_path,'asr-input').delete('asr-input');self.key.set('');self.status.set('已清除独立 ASR 密钥')
        except OSError as error:self.status.set(str(error))
