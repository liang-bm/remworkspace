"""Validated rich journal data and bounded, local-only media; no HTML/scripts."""
import json
import os
from pathlib import Path
from uuid import uuid4
from .diary import DiaryError, _linkish

TAGS=frozenset(('bold','italic','highlight','h1','h2','h3','h4','h5','h6','quote','bullet','todo','done','code'))
MEDIA={'.png':'image','.jpg':'image','.jpeg':'image','.webp':'image','.gif':'image','.bmp':'image',
       '.mp3':'audio','.wav':'audio','.ogg':'audio','.m4a':'audio','.flac':'audio',
       '.mp4':'video','.webm':'video','.mov':'video','.mkv':'video'}
MAX_MEDIA=128*1024*1024

def validate_document(value):
    if not value: return None
    if not isinstance(value,str) or len(value.encode('utf-8'))>6*1024*1024: raise DiaryError('富文本记录过大')
    try: doc=json.loads(value)
    except (ValueError,TypeError) as error: raise DiaryError('富文本记录无法读取') from error
    if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('text'),str): raise DiaryError('不支持这个富文本版本')
    if len(doc['text'].encode('utf-8'))>2*1024*1024 or '\x00' in doc['text']: raise DiaryError('正文最多 2 MiB')
    if ('legacy_body' in doc and (not isinstance(doc['legacy_body'],str)
            or len(doc['legacy_body'].encode('utf-8'))>2*1024*1024 or '\x00' in doc['legacy_body'])):
        raise DiaryError('旧正文记录损坏')
    spans=doc.get('spans',[])
    if not isinstance(spans,list) or len(spans)>20000: raise DiaryError('格式数量过多')
    for span in spans:
        if (not isinstance(span,dict) or span.get('tag') not in TAGS
                or type(span.get('start')) is not int or type(span.get('end')) is not int
                or not 0<=span['start']<=span['end']<=len(doc['text'])): raise DiaryError('富文本格式范围损坏')
    attachments=doc.get('attachments',[])
    if not isinstance(attachments,list) or len(attachments)>64: raise DiaryError('每篇最多 64 个附件')
    for item in attachments:
        if (not isinstance(item,dict) or not isinstance(item.get('file'),str)
                or not item['file'] or len(item['file'])>100 or item['file'] in ('.','..') or Path(item['file']).name!=item['file']
                or not all(c.isalnum() or c in '.-_' for c in item['file'])
                or not isinstance(item.get('name'),str) or len(item['name'])>240
                or item.get('kind') not in ('image','audio','video','file')): raise DiaryError('附件记录损坏')
    return doc

class JournalMedia:
    def __init__(self,root):
        self.root=Path(root).resolve(); self.directory=self.root/'data/plaza/diary-media'
    def _check(self):
        for p in (self.root/'data',self.root/'data/plaza',self.directory):
            if _linkish(p) or (p.exists() and not p.is_dir()) or not p.resolve().is_relative_to(self.root): raise OSError('附件目录被替换，停止写入')
        self.directory.mkdir(parents=True,exist_ok=True)
    def import_file(self,path):
        path=Path(path)
        if _linkish(path) or not path.is_file(): raise DiaryError('请选择普通附件文件')
        if path.stat().st_size>MAX_MEDIA: raise DiaryError('单个附件最多 128 MiB')
        self._check(); suffix=path.suffix.lower()
        if not suffix or len(suffix)>16 or not suffix[1:].isalnum(): suffix='.bin'
        name=uuid4().hex+suffix; target=self.directory/name
        try:
            with path.open('rb') as source,target.open('xb') as output:
                size=0
                while block:=source.read(1024*1024):
                    size+=len(block)
                    if size>MAX_MEDIA: raise DiaryError('附件读取时超过大小限制')
                    output.write(block)
                output.flush(); os.fsync(output.fileno())
        except BaseException:
            target.unlink(missing_ok=True); raise
        return {'file':name,'name':path.name[:240],'kind':MEDIA.get(suffix,'file'),'size':size}
    def resolve(self,item):
        validate_document(json.dumps({'version':1,'text':'','attachments':[item]})); self._check()
        path=self.directory/item['file']
        if _linkish(path) or not path.is_file() or not path.resolve().is_relative_to(self.directory): raise DiaryError('附件文件不可读取')
        return path
    def open(self,item):
        path=self.resolve(item)
        from ..platforms import native_module
        shell=native_module('shell')
        if item['kind'] not in ('image','audio','video'):
            shell.open_directory(self.directory); return
        if MEDIA.get(path.suffix.lower())!=item['kind']: raise DiaryError('附件类型不匹配')
        shell.open_directory(path)
