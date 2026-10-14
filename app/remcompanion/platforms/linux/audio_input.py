"""User-started native PipeWire/PulseAudio/ALSA PCM capture, no extra daemon."""
import array
from collections import deque
import math
import shutil
import subprocess
import threading
import wave


class Recorder:
    def __init__(self):
        self._chunks=[];self._levels=deque(maxlen=80);self._bytes=0
        self._lock=threading.Lock();self._lifecycle=threading.RLock()
        self._process=None;self._worker=None;self._stopping=None;self._failure=None
        self._closed=True;self._paused=False

    def _command(self):
        # stdout is explicitly raw PCM. No model, upload, or microphone is
        # touched until the user's recording button calls start().
        command=shutil.which('pw-record')
        if command:return [command,'--rate=16000','--channels=1','--format=s16','-']
        command=shutil.which('parec')
        if command:return [command,'--rate=16000','--channels=1','--format=s16le']
        command=shutil.which('arecord')
        if command:return [command,'-q','-t','raw','-f','S16_LE','-r','16000','-c','1']
        raise OSError('没有找到 pw-record、parec 或 arecord。请在系统安装对应录音工具；不会自动安装系统软件。')

    def _launch(self):
        self._stopping=threading.Event()
        self._process=subprocess.Popen(self._command(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,start_new_session=True)
        self._worker=threading.Thread(target=self._pump,args=(self._process,self._stopping),
                                      name='remchat-linux-mic-pcm',daemon=True)
        self._worker.start()

    def start(self):
        with self._lifecycle:
            if not self._closed:raise OSError('麦克风已经在使用')
            self._chunks=[];self._levels.clear();self._bytes=0;self._failure=None
            self._launch();self._closed=False;self._paused=False

    def _pump(self,process,stopping):
        pending=b''
        try:
            while block:=process.stdout.read(2048):
                data=pending+block;pending=data[-1:] if len(data)%2 else b''
                data=data[:len(data)-len(pending)]
                if not data:continue
                samples=array.array('h',data)
                level=math.sqrt(sum(value*value for value in samples)/len(samples))/32768
                with self._lock:
                    data=data[:max(0,120*32000-self._bytes)]
                    if data:self._chunks.append(data);self._bytes+=len(data)
                    self._levels.append(level)
            if not stopping.is_set():self._failure=OSError('麦克风录音进程提前退出，请检查默认输入设备和系统录音权限')
        except OSError:
            if not stopping.is_set():self._failure=OSError('麦克风读取失败，请检查默认输入设备和系统录音权限')
        finally:process.stdout.close()

    def levels(self):
        if self._failure:raise self._failure
        with self._lock:return tuple(self._levels)

    def _end_segment(self):
        process=self._process
        if process is None:return
        self._stopping.set()
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=1)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)
        if self._worker is not None:self._worker.join(timeout=1)
        if self._worker is not None and self._worker.is_alive():raise OSError('录音流未正常关闭，未保存录音')
        self._process=None;self._worker=None

    def pause(self):
        with self._lifecycle:
            if self._closed or self._paused:return
            self._end_segment();self._paused=True

    def resume(self):
        with self._lifecycle:
            if self._closed or not self._paused:return
            # Restart the native stream: a paused microphone is actually
            # closed, not silently recording and discarding private audio.
            self._launch();self._paused=False

    def stop(self,path):
        with self._lifecycle:
            self._end_segment();self._closed=True;self._paused=False
            if self._failure:raise self._failure
            with self._lock:data=b''.join(self._chunks);self._chunks=[]
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(data)

    def cancel(self):
        with self._lifecycle:
            try:self._end_segment()
            finally:
                self._closed=True;self._paused=False
                with self._lock:self._chunks=[]
