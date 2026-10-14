"""User-started WinMM PCM microphone capture; real RMS, pause and resume."""
import array
import ctypes
from ctypes import wintypes
from collections import deque
import math
import threading
import wave

_POINTER=ctypes.c_size_t
class _Format(ctypes.Structure):
    _fields_=[('tag',wintypes.WORD),('channels',wintypes.WORD),('rate',wintypes.DWORD),('bytes',wintypes.DWORD),('align',wintypes.WORD),('bits',wintypes.WORD),('extra',wintypes.WORD)]
class _Header(ctypes.Structure):
    _fields_=[('data',ctypes.c_void_p),('length',wintypes.DWORD),('recorded',wintypes.DWORD),('user',_POINTER),('flags',wintypes.DWORD),('loops',wintypes.DWORD),('next',ctypes.c_void_p),('reserved',_POINTER)]
# A failed driver close must not leave native pointers referring to freed
# Python buffers. Retain only failed recorder instances until process exit.
_FAILED_RECORDERS=[]

class Recorder:
    def __init__(self):
        self.handle=wintypes.HANDLE();self.api=ctypes.WinDLL('winmm');self._buffers=[]
        self._chunks=[];self._levels=deque(maxlen=80);self._lock=threading.Lock();self._stopping=False;self._closed=True
        self._bytes=0;self._api_lock=threading.RLock();self._worker=None;self._failure=None;self._queue_serial=0
        self._worker_stop=threading.Event();self._event_handle=None
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype=wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype=wintypes.DWORD
        self.kernel.SetEvent.argtypes=[wintypes.HANDLE];self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.api.waveInOpen.argtypes=[ctypes.POINTER(wintypes.HANDLE),wintypes.UINT,ctypes.POINTER(_Format),_POINTER,_POINTER,wintypes.DWORD]
        for name in ('waveInPrepareHeader','waveInUnprepareHeader','waveInAddBuffer'):
            getattr(self.api,name).argtypes=[wintypes.HANDLE,ctypes.POINTER(_Header),wintypes.UINT]
        for name in ('waveInStart','waveInStop','waveInReset','waveInClose'):getattr(self.api,name).argtypes=[wintypes.HANDLE]
        self.api.waveInGetErrorTextW.argtypes=[wintypes.UINT,wintypes.LPWSTR,wintypes.UINT]
        for name in ('waveInOpen','waveInPrepareHeader','waveInUnprepareHeader','waveInAddBuffer',
                     'waveInStart','waveInStop','waveInReset','waveInClose'):
            getattr(self.api,name).restype=wintypes.UINT
    def _check(self,result):
        if result:
            text=ctypes.create_unicode_buffer(512)
            self.api.waveInGetErrorTextW(result,text,512)
            raise OSError('麦克风无法使用：'+text.value)
    def start(self):
        form=_Format(1,1,16000,32000,2,16,0)
        self._event_handle=self.kernel.CreateEventW(None,False,False,None)
        if not self._event_handle:raise ctypes.WinError(ctypes.get_last_error())
        try:
            # CALLBACK_EVENT, not CALLBACK_FUNCTION: Microsoft forbids calling
            # wave APIs from a waveInProc callback because that can deadlock.
            self._check(self.api.waveInOpen(ctypes.byref(self.handle),0xFFFFFFFF,ctypes.byref(form),self._event_handle,0,0x50000))
            self._closed=False;self._stopping=False
            for _ in range(8):
                data=ctypes.create_string_buffer(2048);header=_Header(ctypes.addressof(data),2048,0,self._queue_serial,0,0,None,0)
                self._queue_serial+=1
                self._buffers.append((data,header));self._check(self.api.waveInPrepareHeader(self.handle,ctypes.byref(header),ctypes.sizeof(header)))
                self._check(self.api.waveInAddBuffer(self.handle,ctypes.byref(header),ctypes.sizeof(header)))
            self._check(self.api.waveInStart(self.handle))
            self._worker=threading.Thread(target=self._pump,name='remchat-mic-pcm',daemon=True)
            self._worker.start()
        except BaseException:
            try:self.cancel()
            except OSError:pass
            raise
    def _pump(self):
        try:
            while not self._worker_stop.is_set():
                result=self.kernel.WaitForSingleObject(self._event_handle,100)
                if result==0xFFFFFFFF:raise ctypes.WinError(ctypes.get_last_error())
                with self._api_lock:
                    if self._stopping or self._closed:return
                    self._collect_ready(requeue=True)
        except OSError as error:
            self._failure=error;self._worker_stop.set()
    def _collect_ready(self,*,requeue):
        # Multiple buffers can return together; preserve capture order even
        # after the ring wraps or when the UI/CPU is temporarily busy.
        for _data,header in sorted(self._buffers,key=lambda pair:pair[1].user):
            if not header.flags&1:continue  # WHDR_DONE, set by the driver.
            size=min(header.recorded,header.length)
            if size:
                data=ctypes.string_at(header.data,size-size%2);samples=array.array('h',data)
                level=math.sqrt(sum(n*n for n in samples)/max(1,len(samples)))/32768
                with self._lock:
                    data=data[:max(0,120*32000-self._bytes)]
                    if data:self._chunks.append(data);self._bytes+=len(data)
                    self._levels.append(level)
            header.recorded=0
            if requeue:
                header.user=self._queue_serial;self._queue_serial+=1
                self._check(self.api.waveInAddBuffer(self.handle,ctypes.byref(header),ctypes.sizeof(header)))
    def levels(self):
        if self._failure:raise self._failure
        with self._lock:return tuple(self._levels)
    def pause(self):
        with self._api_lock:self._check(self.api.waveInStop(self.handle))
    def resume(self):
        with self._api_lock:self._check(self.api.waveInStart(self.handle))
    def _finish(self):
        self._worker_stop.set();self._stopping=True
        if self._event_handle:self.kernel.SetEvent(self._event_handle)
        try:
            with self._api_lock:
                if not self._closed:
                    self._check(self.api.waveInReset(self.handle))
                    # Reset returns all buffers; collect the final partial
                    # block before unpreparing, closing and writing the WAV.
                    self._collect_ready(requeue=False)
                    for _data,header in self._buffers:
                        self._check(self.api.waveInUnprepareHeader(self.handle,ctypes.byref(header),ctypes.sizeof(header)))
                    self._check(self.api.waveInClose(self.handle));self._closed=True
        except OSError:
            if self not in _FAILED_RECORDERS:_FAILED_RECORDERS.append(self)
            raise
        finally:
            if self._worker and self._worker is not threading.current_thread():self._worker.join(timeout=2)
            if self._closed and (not self._worker or not self._worker.is_alive()) and self._event_handle:
                self.kernel.CloseHandle(self._event_handle);self._event_handle=None
                self._buffers=[]
    def stop(self,path):
        self._finish()
        with self._lock:data=b''.join(self._chunks);self._chunks=[]
        with wave.open(str(path),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(data)
    def cancel(self):
        try:self._finish()
        finally:
            with self._lock:self._chunks=[]
