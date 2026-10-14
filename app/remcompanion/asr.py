"""Explicit ASR settings, multipart transcription and isolated local inference."""
from dataclasses import dataclass,asdict
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from uuid import uuid4
import wave
from .plaza.diary import _linkish
from .secret_store import create_secret_store

MAX_AUDIO=16*1024*1024

def checked_secret_store(root,provider_id):
    from .secret_store import PROVIDER_ID_PATTERN
    if not isinstance(provider_id,str) or not PROVIDER_ID_PATTERN.fullmatch(provider_id):raise ValueError('密钥来源标识不合法')
    root=Path(root).resolve();directory=root/'data/credentials'
    for path in (root/'data',directory,directory/(provider_id+'.bin'),directory/(provider_id+'.tmp')):
        if _linkish(path) or not path.resolve().is_relative_to(root):raise OSError('凭据路径已被替换为链接')
    if directory.exists() and not directory.is_dir():raise OSError('凭据目录不合法')
    return create_secret_store(root)

@dataclass(frozen=True)
class AsrSettings:
    mode:str='off'
    endpoint:str='https://api.siliconflow.cn/v1/audio/transcriptions'
    model:str='TeleAI/TeleSpeechASR'
    provider_id:str='asr-input'
    executable:str=''
    model_file:str=''
    language:str='auto'
    timeout:int=60
    def validate(self):
        if self.mode not in ('off','online','local_service','whisper_cpp'):raise ValueError('不支持这个语音输入方式')
        if (not isinstance(self.endpoint,str) or len(self.endpoint)>2048
                or any(ord(c)<33 for c in self.endpoint)):raise ValueError('ASR 地址不合法')
        parsed=urlsplit(self.endpoint)
        if parsed.query:raise ValueError('ASR 地址不应包含查询参数；密钥请填入 API Key')
        try:parsed.port
        except ValueError:raise ValueError('ASR 端口不合法') from None
        if self.mode in ('online','local_service') and (parsed.username or parsed.password or parsed.fragment or not parsed.hostname):raise ValueError('ASR 地址不合法')
        if self.mode=='local_service':
            if parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.scheme not in ('http','https'):raise ValueError('本地 ASR 服务只能连接本机回环地址')
        elif self.mode=='online' and parsed.scheme!='https':raise ValueError('在线 ASR 必须使用 HTTPS')
        if not isinstance(self.model,str) or not self.model.strip() or len(self.model)>240 or any(ord(c)<32 for c in self.model):raise ValueError('请填写 ASR 模型 ID')
        from .secret_store import PROVIDER_ID_PATTERN
        if not isinstance(self.provider_id,str) or not PROVIDER_ID_PATTERN.fullmatch(self.provider_id):raise ValueError('密钥来源标识不合法')
        if type(self.timeout) is not int or not 10<=self.timeout<=180:raise ValueError('超时需在 10–180 秒之间')
        if self.language not in ('auto','zh','en','ja'):raise ValueError('不支持这个识别语言')
        for path in (self.executable,self.model_file):
            if not isinstance(path,str) or len(path)>4096 or '\x00' in path:raise ValueError('本地路径不合法')
        return self

class AsrSettingsStore:
    def __init__(self,root):self.root=Path(root).resolve();self.path=self.root/'data/asr-settings.json'
    def _check(self):
        if _linkish(self.path) or _linkish(self.path.parent) or not self.path.resolve().is_relative_to(self.root):raise OSError('语音配置路径已被替换为链接')
    def load(self):
        self._check()
        if not self.path.exists():return AsrSettings()
        if self.path.stat().st_size>16384:raise ValueError('语音配置文件过大')
        raw=json.loads(self.path.read_text(encoding='utf-8-sig'))
        if not isinstance(raw,dict) or raw.pop('version',1)!=1:raise ValueError('语音配置版本不支持')
        try:return AsrSettings(**raw).validate()
        except TypeError:raise ValueError('语音配置字段不合法') from None
    def save(self,settings,key=''):
        settings.validate();self._check();self.path.parent.mkdir(parents=True,exist_ok=True)
        if key.strip():checked_secret_store(self.root,'asr-input').set('asr-input',key)
        value={'version':1,**asdict(settings)}
        descriptor,name=tempfile.mkstemp(prefix='.asr-',suffix='.json',dir=self.path.parent)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
            self._check();os.replace(name,self.path)
        finally:Path(name).unlink(missing_ok=True)

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('ASR 服务重定向已阻止，避免凭据发送到其他地址')

def transcribe(root,settings,audio,cancelled):
    settings.validate();audio=Path(audio)
    if not audio.is_file() or audio.stat().st_size>MAX_AUDIO:raise ValueError('录音超过 16 MiB 限制')
    with wave.open(str(audio),'rb') as wav:
        if wav.getsampwidth()!=2 or wav.getnchannels()!=1 or wav.getframerate()!=16000:raise ValueError('录音需为 16 kHz 单声道 16-bit WAV')
        if wav.getnframes()<1600:raise ValueError('录音太短，请说完再停止')
    if cancelled.is_set():return ''
    if settings.mode=='whisper_cpp':return _local_transcribe(settings,audio,cancelled)
    if settings.mode not in ('online','local_service'):raise ValueError('请先在设置 → 语音输入中选择识别方式')
    secret=(checked_secret_store(root,settings.provider_id).get(settings.provider_id) or '') if settings.mode=='online' else ''
    if settings.mode=='online' and not secret:raise ValueError('请先保存 ASR API Key 或选择已保存的供应商密钥')
    boundary='RemChatASR'+uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{settings.model}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode('utf-8')
    body+=audio.read_bytes()+f'\r\n--{boundary}--\r\n'.encode('ascii')
    headers={'Content-Type':'multipart/form-data; boundary='+boundary,'Accept':'application/json'}
    if secret:headers['Authorization']='Bearer '+secret
    request=Request(settings.endpoint,data=body,headers=headers,method='POST')
    try:
        with build_opener(_NoRedirect()).open(request,timeout=settings.timeout) as response:
            raw=response.read(1024*1024+1)
    except HTTPError as error:
        # No request/body/key/transcript is echoed in logs or dialogs.
        raise ValueError(f'ASR 服务返回 HTTP {error.code}；请检查模型、密钥、额度与接口') from None
    except (URLError,TimeoutError) as error:raise ValueError('ASR 服务连接失败或超时') from None
    if cancelled.is_set():return ''
    if len(raw)>1024*1024:raise ValueError('ASR 响应过大')
    value=json.loads(raw.decode('utf-8'));text=value.get('text') if isinstance(value,dict) else None
    if not isinstance(text,str) or len(text)>65536:raise ValueError('ASR 未返回有效的 text 字段')
    try:text.encode('utf-8')
    except UnicodeError:raise ValueError('ASR 返回了无效 Unicode 文本') from None
    if '\x00' in text:raise ValueError('ASR 文本含无效空字符')
    return text.strip()

def _local_transcribe(settings,audio,cancelled):
    executable,model=Path(settings.executable),Path(settings.model_file)
    if not executable.is_file() or not model.is_file():raise ValueError('请选择已下载的 whisper-cli 引擎和 GGML 模型文件')
    with tempfile.TemporaryDirectory(prefix='remchat-local-asr-') as directory:
        output=Path(directory)/'transcript';diagnostic=Path(directory)/'engine.log'
        command=[str(executable),'-m',str(model),'-f',str(audio),'-l',settings.language,'-otxt','-of',str(output)]
        with diagnostic.open('wb') as log:
            process=subprocess.Popen(command,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+settings.timeout
            while process.poll() is None:
                if cancelled.is_set() or time.monotonic()>deadline:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
                    if cancelled.is_set():return ''
                    raise ValueError('本地 ASR 推理超时，可提高超时或换较小模型')
                time.sleep(.05)
        if process.returncode:raise ValueError('本地 ASR 引擎失败，请检查引擎版本和模型兼容性')
        path=output.with_suffix('.txt')
        if not path.is_file() or path.stat().st_size>1024*1024:raise ValueError('本地 ASR 没有生成有效文本')
        return path.read_text(encoding='utf-8-sig').strip()
