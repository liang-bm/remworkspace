"""Explicit local privacy maintenance. Reports contain counts, not user text.

This does not erase SSD remanence, operating-system snapshots or external app
histories. A caller must close RemChat before maintenance; no backup is made
of the conversation data the user explicitly asks to forget.
"""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tomllib
import re
from .secure_resources import checked_path, linked, atomic

def memory_databases(root):
    root=Path(root).resolve();paths=[]
    for base in ('data','backups','tmp'):
        directory=checked_path(root,base)
        if directory.exists():
            for path in directory.rglob('*.sqlite3'):
                if path.name=='memory.sqlite3' or path.name.startswith('memory-'):
                    relative=path.relative_to(root).as_posix();checked_path(root,relative)
                    with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as connection:
                        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        if {'messages','memories','conversations'}.issubset(tables):paths.append(path)
    return sorted(set(paths))

def audit_memory(root):
    report=[]
    for path in memory_databases(root):
        with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as connection:
            report.append({'path':path.relative_to(root).as_posix(),
                'messages':connection.execute('SELECT count(*) FROM messages').fetchone()[0],
                'memories':connection.execute('SELECT count(*) FROM memories').fetchone()[0],
                'conversations':connection.execute('SELECT count(*) FROM conversations').fetchone()[0]})
    return report

def clear_memory(root):
    root=Path(root).resolve();before=audit_memory(root);cleared=0;removed_backups=0
    for path in memory_databases(root):
        if path!=root/'data/memory.sqlite3':
            # Explicitly confirmed removal of corresponding old memory copies.
            for candidate in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')):
                checked_path(root,candidate.relative_to(root).as_posix()).unlink(missing_ok=True)
            removed_backups+=1;continue
        connection=sqlite3.connect(path,timeout=2,isolation_level=None)
        try:
            connection.execute('PRAGMA secure_delete=ON');connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN EXCLUSIVE')
            connection.execute('DELETE FROM memories');connection.execute('DELETE FROM summaries')
            connection.execute('DELETE FROM messages');connection.execute('DELETE FROM conversations')
            connection.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            connection.execute('COMMIT')
            checkpoint=connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint and checkpoint[0]:raise OSError('记忆仍有活动连接，未完成WAL清理')
            connection.execute('VACUUM')
            checkpoint=connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint and checkpoint[0]:raise OSError('记忆WAL仍在使用')
            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise OSError('清理后的数据库完整性错误')
            cleared+=1
        except BaseException:
            if connection.in_transaction:connection.rollback()
            raise
        finally:connection.close()
    exports=0
    vectors=checked_path(root,'data/memory-vectors.sqlite3')
    for candidate in (vectors,Path(str(vectors)+'-wal'),Path(str(vectors)+'-shm')):
        checked_path(root,candidate.relative_to(root).as_posix()).unlink(missing_ok=True)
    for base in ('data','backups','tmp'):
        directory=checked_path(root,base)
        if not directory.exists():continue
        for path in directory.rglob('*.json'):
            if 'memory' not in path.name.lower() or linked(path) or path.stat().st_size>16*1024*1024:continue
            value=json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(value,dict) and isinstance(value.get('memories'),list):
                checked_path(root,path.relative_to(root).as_posix()).unlink();exports+=1
    return {'databases_cleared':cleared,'memory_backups_removed':removed_backups,'memory_exports_removed':exports,
            'before':before,'after':audit_memory(root),'diary_and_todos_retained':True,'physical_secure_erase_claimed':False}

def reset_personality(root,default_config):
    root=Path(root).resolve();defaults=tomllib.loads(Path(default_config).read_text(encoding='utf-8'))
    prompt=defaults['llm']['system_prompt']
    atomic(checked_path(root,'data/global-instruction.md'),prompt.encode('utf-8'))
    target=checked_path(root,'config/rem_companion.toml')
    original=target.read_text(encoding='utf-8');raw=tomllib.loads(original)
    start=re.search(r'(?m)^\[llm\]\s*$',original)
    if start is None:raise ValueError('未找到人格配置章节')
    following=re.search(r'(?m)^\[',original[start.end():]);end=start.end()+following.start() if following else len(original)
    section=original[start.end():end]
    field=re.search(r'''(?ms)^system_prompt\s*=\s*(?:""".*?"""|\x27\x27\x27.*?\x27\x27\x27|"(?:[^"\\]|\\.)*"|\x27[^\x27]*\x27)''',section)
    if field is None:raise ValueError('无法安全定位人格字段；未改写TOML')
    section=section[:field.start()]+'system_prompt = '+json.dumps(prompt,ensure_ascii=False)+section[field.end():]
    updated=original[:start.end()]+section+original[end:]
    expected=dict(raw);expected['llm']=dict(raw['llm'],system_prompt=prompt)
    if tomllib.loads(updated)!=expected:raise ValueError('人格替换影响其他设置，未保存')
    atomic(target,updated.encode('utf-8'))
    return {'personality_reset':True,'other_runtime_settings_unchanged':True}
