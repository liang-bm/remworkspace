"""Optional real LOCAL embeddings + sqlite-vec; never a hash embedding."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import urllib.request
from urllib.parse import urlsplit
from .secure_resources import atomic,checked_path,canonical

def validate_settings(value):
    if not isinstance(value,dict) or type(value.get('enabled'))!=bool:raise ValueError('语义记忆配置无效')
    if not value['enabled']:return {'enabled':False}
    model=value.get('model');revision=value.get('revision');base=value.get('base_url')
    if not all(isinstance(x,str) and x and len(x)<=256 for x in (model,revision,base)):raise ValueError('真实Embedding模型、版本标识及地址不能为空')
    parsed=urlsplit(base)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('语义记忆仅允许明确本机回环Embedding地址；不上传到云端')
    return {'enabled':True,'model':model,'revision':revision,'base_url':base.rstrip('/')}

class SemanticMemory:
    def __init__(self,memory_path):
        self.memory=Path(memory_path);self.root=self.memory.parent.parent if self.memory.parent.name=='data' else self.memory.parent
        self.settings_path=checked_path(self.root,'data/semantic-memory.json')
        self.index=checked_path(self.root,'data/memory-vectors.sqlite3')
    def settings(self):
        if not self.settings_path.exists():return {'enabled':False}
        if self.settings_path.stat().st_size>65536:raise ValueError('语义记忆配置过大')
        return validate_settings(json.loads(self.settings_path.read_text(encoding='utf-8')))
    def configure(self,value):
        value=validate_settings(value);self.settings_path.parent.mkdir(parents=True,exist_ok=True)
        atomic(self.settings_path,canonical(value))
        return value
    def _snapshot(self):
        connection=sqlite3.connect(self.memory)
        try:rows=connection.execute('SELECT id,content FROM memories ORDER BY id').fetchall()
        finally:connection.close()
        return rows,hashlib.sha256(canonical(rows)).hexdigest()
    def _embed(self,texts,settings):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*_args,**_kwargs):return None
        request=urllib.request.Request(settings['base_url']+'/embeddings',data=canonical({'model':settings['model'],'input':texts}),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=15) as response:
            raw=response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('Embedding返回过大')
        result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError('Embedding返回必须为对象')
        rows=result.get('data')
        if not isinstance(rows,list) or len(rows)!=len(texts):raise ValueError('Embedding条数不匹配')
        if result.get('model',settings['model'])!=settings['model']:raise ValueError('Embedding模型身份不匹配')
        vectors=[None]*len(texts)
        for row in rows:
            if not isinstance(row,dict) or type(row.get('index'))!=int or not 0<=row['index']<len(texts) or vectors[row['index']] is not None:raise ValueError('Embedding索引无效')
            vector=row.get('embedding')
            if not isinstance(vector,list) or not 1<=len(vector)<=8192 or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector) or not any(vector):raise ValueError('Embedding必须为非零有限实数向量')
            norm=math.hypot(*vector)
            if not math.isfinite(norm) or not norm:raise ValueError('Embedding范数无效')
            vectors[row['index']]=[x/norm for x in vector]
        if len({len(v) for v in vectors})!=1:raise ValueError('Embedding维度不一致')
        return vectors
    @staticmethod
    def _connection(path):
        import sqlite_vec
        connection=sqlite3.connect(path,timeout=2)
        try:
            connection.enable_load_extension(True);sqlite_vec.load(connection)
        except BaseException:connection.close();raise
        finally:
            try:connection.enable_load_extension(False)
            except sqlite3.ProgrammingError:pass
        return connection
    def rank(self,query,limit):
        settings=self.settings()
        if not settings['enabled'] or not query.strip():return []
        rows,digest=self._snapshot()
        if not rows:return []
        query_vector=self._embed([query],settings)[0];dimension=len(query_vector)
        identity=hashlib.sha256(canonical(settings)).hexdigest()
        connection=self._connection(self.index)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS vector_meta (id INTEGER PRIMARY KEY,identity TEXT,digest TEXT,dimension INTEGER)')
            previous=connection.execute('SELECT identity,digest,dimension FROM vector_meta WHERE id=1').fetchone()
            if previous!=(identity,digest,dimension):
                embedded=[]
                for offset in range(0,len(rows),32):embedded+=self._embed([r[1] for r in rows[offset:offset+32]],settings)
                if any(len(v)!=dimension for v in embedded):raise ValueError('文档与查询向量维度不一致')
                if self._snapshot()[1]!=digest:return []
                connection.execute('DROP TABLE IF EXISTS memory_vectors')
                connection.execute(f'CREATE VIRTUAL TABLE memory_vectors USING vec0(memory_id INTEGER PRIMARY KEY,embedding float[{dimension}] distance_metric=cosine)')
                import sqlite_vec
                connection.executemany('INSERT INTO memory_vectors(memory_id,embedding) VALUES (?,?)',[(r[0],sqlite_vec.serialize_float32(v)) for r,v in zip(rows,embedded)])
                connection.execute('INSERT OR REPLACE INTO vector_meta VALUES (1,?,?,?)',(identity,digest,dimension));connection.commit()
            import sqlite_vec
            hits=connection.execute('SELECT memory_id FROM memory_vectors WHERE embedding MATCH ? AND k=? ORDER BY distance',(sqlite_vec.serialize_float32(query_vector),max(1,min(limit,len(rows))))).fetchall()
            return [r[0] for r in hits] if self._snapshot()[1]==digest else []
        finally:connection.close()
