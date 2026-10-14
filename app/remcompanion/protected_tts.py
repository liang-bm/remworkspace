"""Mutual local service proof + synthesis on ONE non-reconnecting socket."""
import hashlib
import hmac
import http.client
import json
import secrets

def request(config,token,payload=None,*,timeout=180):
    host=config.services.tts_host
    if host not in ('127.0.0.1','::1','localhost'):raise PermissionError('受保护声线仅允许本机回环地址')
    host='127.0.0.1' if host=='localhost' else host
    if not token or not token.isascii():raise PermissionError('TTS会话凭据不可用')
    connection=http.client.HTTPConnection(host,config.services.tts_port,timeout=timeout)
    challenge=secrets.token_hex(32)
    try:
        connection.connect();connection.auto_open=0
        connection.request('GET','/proof?challenge='+challenge)
        response=connection.getresponse();body=response.read(4097)
        if response.status!=200 or len(body)>4096:raise PermissionError('此端口未提供受保护TTS身份证明')
        proof=json.loads(body)
        expected=hmac.new(token.encode('ascii'),challenge.encode('ascii'),hashlib.sha256).hexdigest()
        if not isinstance(proof,dict) or not isinstance(proof.get('proof'),str) or not hmac.compare_digest(proof['proof'],expected):raise PermissionError('TTS身份验证失败；未发送会话令牌或文本')
        if payload is None:return True
        if connection.sock is None:raise ConnectionError('身份验证连接已关闭，不能换连接发送令牌')
        connection.request('POST','/tts',body=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json; charset=utf-8'})
        response=connection.getresponse();audio=response.read()
        if response.status!=200:raise RuntimeError(f'受保护TTS返回HTTP {response.status}')
        return audio,response.getheader('Content-Type','')
    finally:connection.close()
