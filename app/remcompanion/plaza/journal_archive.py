"""Explicit journal interchange including rich formatting and local media."""
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import zipfile
import re
from uuid import uuid4
from .diary import DiaryError,_safe_file,_linkish
from .journal_document import validate_document,MAX_MEDIA


def export_entry(entry,media,path):
    path=Path(path).absolute();_safe_file(path)
    if _linkish(path.parent) or not path.parent.is_dir():raise OSError('导出目录不是普通目录')
    document=validate_document(entry.rich)
    value={'version':1,'entry':asdict(entry)}
    descriptor,temporary=tempfile.mkstemp(prefix='.journal-',suffix='.zip',dir=path.parent);os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr('record.json',json.dumps(value,ensure_ascii=False))
            for item in document.get('attachments',[]) if document else ():
                output.write(media.resolve(item),'media/'+item['file'],compress_type=zipfile.ZIP_STORED)
        _safe_file(path);os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)
    return path

def import_entry(store,media,path):
    path=Path(path)
    if _linkish(path) or not path.is_file():raise DiaryError('请选择普通日记文件')
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist())>65:raise DiaryError('日记文件内容过多')
        if len(set(archive.namelist()))!=len(archive.infolist()):raise DiaryError('日记文件有重复内容')
        info=archive.getinfo('record.json')
        if info.file_size>8*1024*1024:raise DiaryError('日记记录过大')
        value=json.loads(archive.read(info).decode('utf-8'))
        if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('entry'),dict):raise DiaryError('日记文件版本不支持')
        entry=value['entry'];rich=entry.get('rich','');document=validate_document(rich)
        if document:
            media._check();items=[]
            # Never extractall. Only exact validated manifest files are read
            # into our random temporary paths, then imported under new UUIDs.
            with tempfile.TemporaryDirectory(prefix='.import-',dir=media.directory) as directory:
                for item in document.get('attachments',[]):
                    info=archive.getinfo('media/'+item['file'])
                    if info.file_size>MAX_MEDIA:raise DiaryError('附件过大')
                    temporary=Path(directory)/item['file']
                    with archive.open(info) as source,temporary.open('xb') as output:
                        count=0
                        while block:=source.read(1024*1024):
                            count+=len(block)
                            if count>MAX_MEDIA:raise DiaryError('附件解压超过限制')
                            output.write(block)
                    imported=media.import_file(temporary);imported['name']=item['name'];items.append(imported)
            document['attachments']=items;rich=json.dumps(document,ensure_ascii=False)
        return store.save(day=entry['day'],title=entry['title'],body=entry['body'],tags=entry.get('tags',()),mood=entry.get('mood',''),rich=rich,
                          created_at=entry.get('created_at') or None)

def export_collection(entries,media,directory,*,cancelled=None):
    directory=Path(directory).absolute()
    if _linkish(directory) or not directory.is_dir():raise OSError('导出目录不是普通目录')
    destination=directory/('RemChatJournal-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
    destination.mkdir()
    for entry in entries:
        if cancelled and cancelled():raise DiaryError('导出已停止；已完成文件保留在新文件夹')
        title=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',entry.title).strip(' .')[:60] or '日记'
        export_entry(entry,media,destination/f'{entry.day}-{title}-{uuid4().hex[:8]}.remjournal')
    return destination
