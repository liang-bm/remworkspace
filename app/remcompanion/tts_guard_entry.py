"""Dependency-light private GPT-SoVITS launcher; token arrives only on stdin.

Executed with the separate engine interpreter, not by importing PyTorch into
the desktop. Only loopback POST /tts and authenticated /health are available.
"""
import hmac
import hashlib
import json
from pathlib import Path
import runpy
import re
import shutil
import sys
from urllib.parse import parse_qs

class TTSGate:
    def __init__(self,app,token,reference_directory):
        self.app=app;self.token=token;self.references=Path(reference_directory).resolve()
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        async def reject(code,message):
            await send({'type':'http.response.start','status':code,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':json.dumps({'message':message}).encode('utf-8')})
        if scope.get('path')=='/proof' and scope.get('method')=='GET':
            try:challenge=parse_qs(scope.get('query_string',b'').decode('ascii')).get('challenge',[])
            except (ValueError,UnicodeError):challenge=[]
            if len(challenge)!=1 or not re.fullmatch('[a-f0-9]{64}',challenge[0]):return await reject(400,'Invalid challenge')
            proof=hmac.new(self.token.encode('ascii'),challenge[0].encode('ascii'),hashlib.sha256).hexdigest()
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
            return await send({'type':'http.response.body','body':json.dumps({'proof':proof}).encode('ascii')})
        headers=[v for k,v in scope.get('headers',[]) if k.lower()==b'authorization']
        expected=('Bearer '+self.token).encode('ascii')
        if len(headers)!=1 or not hmac.compare_digest(headers[0],expected):return await reject(401,'Authentication required')
        if scope.get('path')=='/health' and scope.get('method')=='GET':return await reject(200,'RemChat protected TTS')
        if scope.get('path')!='/tts' or scope.get('method')!='POST':return await reject(403,'Endpoint disabled')
        body=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            if message['type']!='http.request':return await reject(400,'Invalid request')
            body.extend(message.get('body',b''))
            if len(body)>1024*1024:return await reject(413,'Request too large')
            if not message.get('more_body'):break
        try:
            payload=json.loads(body)
            if not isinstance(payload,dict) or payload.get('aux_ref_audio_paths'):raise ValueError()
            reference=Path(payload['ref_audio_path'])
            if not reference.is_absolute() or reference.is_symlink():raise ValueError()
            resolved=reference.resolve(strict=True)
            if self.references not in resolved.parents or not resolved.is_file():raise ValueError()
            for parent in reference.parents:
                if parent.is_symlink():raise ValueError()
                if parent==self.references:break
            if not isinstance(payload.get('text'),str) or not 0<len(payload['text'])<=65536:raise ValueError()
        except (ValueError,KeyError,OSError,TypeError):return await reject(403,'Reference is not an authorized resource lease')
        sent=False
        async def replay():
            nonlocal sent
            if not sent:sent=True;return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)

def main():
    configuration=json.loads(sys.stdin.buffer.readline(16384))
    token=configuration.get('token','')
    if not isinstance(token,str) or not 32<=len(token)<=256 or not token.isascii():raise ValueError('Missing private service credential')
    import uvicorn
    original=uvicorn.run
    def guarded(*args,**kwargs):
        host=kwargs.get('host')
        if host not in ('127.0.0.1','::1','localhost'):raise ValueError('Private TTS must bind to loopback')
        app=kwargs.pop('app',args[0] if args else None)
        if app is None or isinstance(app,str):raise ValueError('Expected a native ASGI application')
        return original(TTSGate(app,token,configuration['reference_directory']),**kwargs)
    uvicorn.run=guarded
    script=Path.cwd()/'api_v2.py'
    sys.argv=[str(script),*sys.argv[1:]]
    sys.path.insert(0,str(Path.cwd()))
    try:runpy.run_path(str(script),run_name='__main__')
    finally:
        directory=Path(configuration['model_lease_directory'])
        references=Path(configuration['reference_directory']).resolve()
        if (not directory.is_symlink() and directory.resolve().parent==references
                and re.fullmatch('[a-f0-9]{32}',directory.name) and directory.is_dir()
                and not any(p.is_symlink() for p in directory.rglob('*'))):shutil.rmtree(directory)

if __name__=='__main__':main()
