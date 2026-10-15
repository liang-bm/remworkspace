"""Shared user-started recording; all ASR work stays off the Tk thread."""
import queue
import tempfile
from pathlib import Path
import threading
import time
import tkinter as tk
from .localized_dialogs import messagebox
from weakref import WeakSet
from . import theme
from .dpi import pixels
from .rounded_controls import rounded_box
from ..asr import AsrSettingsStore,transcribe
from ..platforms import native_module
from .locale_controller import translator_for


class SpeechController:
    def __init__(self,root,project_root):
        self.root=root;self.project_root=Path(project_root);self.bindings=WeakSet()
        self.owner=None;self.recorder=None;self.state='idle';self.paused=False;self._closed=False
        self._started=0;self._accumulated=0;self._serial=0;self._queue=queue.Queue();self._job=None
        self._cancel=threading.Event();self._temporary=None
        root.bind('<Destroy>',self._destroyed,add=True)
    def elapsed(self):return self._accumulated+(time.perf_counter()-self._started if self.state=='recording' and not self.paused else 0)
    def begin(self,binding):
        if self.state!='idle':return
        try:
            settings=AsrSettingsStore(self.project_root).load()
            if settings.mode=='off':raise ValueError('请先在 设置 → 语音输入 中选择在线或本地识别')
            binding.prepare()
            self.recorder=native_module('audio_input').Recorder();self.recorder.start()
            self._temporary=tempfile.TemporaryDirectory(prefix='remchat-recording-')
        except (OSError,ValueError) as error:
            if self.recorder:
                try:self.recorder.cancel()
                except OSError:pass
            self.recorder=None
            messagebox.showerror('语音输入未开始',str(error),parent=binding.target);return
        self.settings=settings;self.owner=binding;self._cancel=threading.Event();self._serial+=1
        self.state='recording';self.paused=False;self._accumulated=0;self._started=time.perf_counter()
        binding.show_recording();self._refresh();self._tick()
    def pause(self):
        if self.state!='recording':return
        try:
            if self.paused:self.recorder.resume();self._started=time.perf_counter()
            else:self._accumulated=self.elapsed();self.recorder.pause()
            self.paused=not self.paused
            self._refresh()
        except OSError as error:self.cancel();messagebox.showerror('录音已停止',str(error),parent=self.root)
    def finish(self):
        if self.state!='recording':return
        self._accumulated=self.elapsed();self.state='transcribing';self._refresh()
        recorder=self.recorder;temporary=self._temporary;settings=self.settings;cancel=self._cancel;serial=self._serial
        # Background stop flushes native buffers before WAV serialization and
        # before any upload. No network call while merely recording/paused.
        def work():
            try:
                audio=Path(temporary.name)/'recording.wav';recorder.stop(audio)
                text=transcribe(self.project_root,settings,audio,cancel)
                self._queue.put((serial,text,None))
            except Exception as error:self._queue.put((serial,'',str(error)))
            finally:temporary.cleanup()
        threading.Thread(target=work,name='remchat-asr',daemon=True).start()
    def cancel(self):
        if self._job is not None:
            self.root.after_cancel(self._job);self._job=None
        self._cancel.set();self._serial+=1
        if self.state=='recording' and self.recorder:
            try:self.recorder.cancel()
            except OSError:pass
        if self._temporary:
            try:self._temporary.cleanup()
            except OSError:pass
        owner=self.owner;self.state='idle';self.owner=None;self.recorder=None;self._temporary=None
        if owner:owner.hide_recording()
        self._refresh()
    def _refresh(self):
        for binding in tuple(self.bindings):binding.refresh()
    def _tick(self):
        self._job=None
        if self._closed:return
        try:
            while True:
                serial,text,error=self._queue.get_nowait()
                if serial!=self._serial:continue
                owner=self.owner;self.state='idle';self.owner=None;self.recorder=None;self._temporary=None
                if owner:owner.hide_recording()
                self._refresh()
                if error:messagebox.showerror('语音转文字失败',error,parent=self.root)
                elif text and owner:owner.insert_result(text)
        except queue.Empty:pass
        if self.state=='recording':
            if self.elapsed()>=120:self.finish()
            elif self.owner:
                try:self.owner.paint_recording(self.recorder.levels(),self.elapsed(),self.paused)
                except OSError as error:
                    self.cancel();messagebox.showerror('录音已停止',str(error),parent=self.root)
        if self.state!='idle':self._job=self.root.after(33 if self.state=='recording' else 60,self._tick)
    def _destroyed(self,event):
        if event.widget is self.root:
            self._closed=True;self.cancel()


class SpeechBinding:
    def __init__(self,parent,target,project_root,*,context=lambda:None,dialog=False,on_insert=None,label='🎙',send_button=None,palette=None):
        self.target=target;self.parent=parent;self.context=context;self.dialog=dialog;self.on_insert=on_insert
        self._idle_label=label;self.send_button=send_button;self._send_state=None;self._palette=palette
        root=target._root();controllers=getattr(root,'_remchat_speech_controllers',None)
        if controllers is None:root._remchat_speech_controllers=controllers={}
        key=str(Path(project_root).resolve())
        if key not in controllers:controllers[key]=SpeechController(root,project_root)
        self.controller=controllers[key];self.controller.bindings.add(self)
        colors=self.colors()
        self.button=tk.Button(parent,text=label,command=self.click,relief='flat',bd=0,
            padx=8,pady=7,bg=colors['wave'],fg=colors['text'],activebackground=colors['wave'],
            font=('Segoe UI Emoji',12),cursor='hand2',takefocus=True)
        self.button.bind('<Destroy>',self._destroy,add=True)
        self._toplevel=self.target.winfo_toplevel()
        self._window_binding=self._toplevel.bind('<Unmap>',self._window_hidden,add=True)
        # The porcelain overlay intentionally opts out of main-window themes;
        # its input controls still use the same lightweight rounded chrome.
        self._rounded=None
        if palette:
            from .rounded_controls import RoundedChrome
            self._rounded=RoundedChrome(parent)
        self.panel=None;self.window=None;self._mark='asr-insert-'+str(id(self))
    def colors(self):
        return self._palette or dict(surface=theme.WHITE,wave=theme.APP_BG,text=theme.INK,
            accent=theme.ACCENT,button=theme.ACCENT_DEEP,on_button=theme.ON_ACCENT)
    def click(self):
        if self.controller.owner is self:
            if self.controller.state=='recording':self.controller.finish()
            else:self.controller.cancel()
        else:self.controller.begin(self)
    def prepare(self):
        if self.target.cget('state') not in ('normal',):raise ValueError('当前编辑框只读')
        self._context=self.context();self.target.mark_set(self._mark,'insert');self.target.mark_gravity(self._mark,'right')
    def refresh(self):
        if not self.button.winfo_exists():return
        owned=self.controller.owner is self;state=self.controller.state
        tr = translator_for(self.parent)
        self.button.configure(text=tr.display(('■' if state=='recording' else '取消') if owned else self._idle_label),
                              state='normal' if owned or state=='idle' else 'disabled')
        if self.send_button and self.send_button.winfo_exists():
            if state!='idle':
                if self._send_state is None:self._send_state=self.send_button.cget('state')
                self.send_button.configure(state='disabled')
            elif self._send_state is not None:
                self.send_button.configure(state=self._send_state);self._send_state=None
        if owned and self.panel and self.panel.winfo_exists():
            if state=='transcribing':self.time_label.configure(text=tr('识别中…'))
            label=('继续' if self.controller.paused else '暂停') if self.dialog else ('▷' if self.controller.paused else 'Ⅱ')
            self._pause_button.configure(text=tr.display(label),state='normal' if state=='recording' else 'disabled')
            self._stop_button.configure(state='normal' if state=='recording' else 'disabled')
    def show_recording(self):
        colors=self.colors()
        tr = translator_for(self.parent)
        if self.dialog:
            self.window=tk.Toplevel(self.target);self.window.title(tr('语音日记'));self.window.geometry('620x250');self.window.configure(bg=colors['surface'])
            self.window.transient(self.target.winfo_toplevel());self.window.protocol('WM_DELETE_WINDOW',self.controller.cancel)
            container=self.window
        else:
            self._geometry=self.target.pack_info();self.target.pack_forget();container=self.parent
            self._button_geometry=self.button.pack_info();self.button.pack_forget()
        self.panel=tk.Frame(container,bg=colors['surface'],bd=0)
        self.panel.pack(fill='both',expand=True,padx=8,pady=8)
        self.time_label=tk.Label(self.panel,text='00:00',bg=colors['surface'],fg=colors['text'],font=(theme.BODY_FONT,14 if self.dialog else 9))
        self.time_label.pack(side='top' if self.dialog else 'left',padx=8,pady=6)
        commands=tk.Frame(self.panel,bg=colors['surface']);commands.pack(side='bottom' if self.dialog else 'right',padx=4,pady=6)
        controls=(('暂停',self.controller.pause),('停止并转写',self.controller.finish),('取消',self.controller.cancel)) if self.dialog else (
            ('×',self.controller.cancel),('Ⅱ',self.controller.pause),('■',self.controller.finish))
        for text,command in controls:
            button=tk.Button(commands,text=tr.display(text),command=command,bd=0,relief='flat',bg=colors['button'],fg=colors['on_button'],
                font=(theme.BODY_FONT,12),padx=10,pady=7,cursor='hand2');button.pack(side='left',padx=3)
            if command==self.controller.pause:self._pause_button=button
            if command==self.controller.finish:self._stop_button=button
        self.wave=tk.Canvas(self.panel,height=pixels(self.target,55),width=1,bg=colors['surface'],bd=0,highlightthickness=0)
        self.wave.pack(fill='both',expand=True)
    def paint_recording(self,levels,elapsed,paused):
        if not self.panel or not self.panel.winfo_exists():return
        stamp=f'{int(elapsed)//60:02d}:{int(elapsed)%60:02d}'
        if self.dialog:stamp='00:'+stamp
        self.time_label.configure(text=stamp+(translator_for(self.parent)(' · 已暂停') if paused else ''))
        self.wave.delete('all');width,height=self.wave.winfo_width(),self.wave.winfo_height()
        colors=self.colors()
        rounded_box(self.wave,(1,1,width-1,height-1),radius=min(24,height/2),fill=colors['wave'],outline='')
        count=max(1,min(80,(width-20)//7));values=list(levels)[-count:];values=[0]*(count-len(values))+values
        for index,value in enumerate(values):
            amplitude=min(height*.42,max(2,(value**.5)*height*.9));x=10+(index+.5)*(width-20)/count
            self.wave.create_line(x,height/2-amplitude,x,height/2+amplitude,fill=colors['accent'],
                                  width=max(2,pixels(self.wave,3)),capstyle='round')
    def hide_recording(self):
        if self.panel and self.panel.winfo_exists():self.panel.destroy()
        self.panel=None
        if self.window and self.window.winfo_exists():self.window.destroy()
        self.window=None
        if not self.dialog and hasattr(self,'_geometry') and self.target.winfo_exists():
            if self.button.winfo_exists():self.button.pack(**self._button_geometry)
            self.target.pack(**self._geometry)
    def insert_result(self,text):
        if not self.target.winfo_exists() or self.context()!=self._context:
            # Changed destination means review/copy, never cross-conversation
            # or cross-diary insertion into the now-current editor.
            window=tk.Toplevel(self.parent);window.title('识别文字 · 未插入')
            box=tk.Text(window,wrap='word',width=60,height=8);box.pack(fill='both',expand=True,padx=12,pady=12)
            box.insert('1.0',text);box.configure(state='disabled');return
        self.target.insert(self._mark,text);self.target.mark_unset(self._mark);self.target.focus_set()
        if self.on_insert:self.on_insert()
    def _window_hidden(self,event):
        if self.controller.owner is self and event.widget is self._toplevel:self.controller.cancel()
    def _destroy(self,event):
        if event.widget is not self.button:return
        if self.controller.owner is self:self.controller.cancel()
        self.controller.bindings.discard(self)
        try:
            script=str(self._toplevel.tk.call('bind',str(self._toplevel),'<Unmap>'))
            self._toplevel.tk.call('bind',str(self._toplevel),'<Unmap>','\n'.join(
                line for line in script.splitlines() if self._window_binding not in line))
            self._toplevel.deletecommand(self._window_binding)
        except tk.TclError:pass
