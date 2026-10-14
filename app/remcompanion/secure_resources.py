"""Authenticated, account-bound, outside-repository resource storage.

No rights/DRM claim: the logged-in account and administrators can inspect an
active lease. Authentication completes before a path is released to a reader.
"""
from __future__ import annotations
from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import threading
import uuid
from .secret_store import create_secret_store
from .platforms import native_module

MAGIC=b'RCVAULT1'; BLOCK=4*1024*1024
KEY_ID='private-resource-master'

def linked(path):
    try:return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)
    except FileNotFoundError:return False

def relative_name(relative):
    if not isinstance(relative,str) or not relative or '\\' in relative:raise ValueError('资源路径无效')
    pure=PurePosixPath(relative)
    if pure.is_absolute() or pure.as_posix()!=relative or any(p in ('.','..') or ':' in p for p in pure.parts):raise ValueError('资源路径越界')
    return pure.parts

def checked_path(root, relative):
    parts=relative_name(relative)
    root=Path(root).absolute(); path=root.joinpath(*parts)
    if path.resolve()==root.resolve() or root.resolve() not in path.resolve().parents:
        raise ValueError('资源路径越界')
    for node in (path,*path.parents):
        if linked(node):raise ValueError('资源路径包含链接')
        if node==root:break
    return path

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')

def derive(master, salt, purpose):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=purpose).derive(master)

def private_dir(path):
    if any(linked(p) for p in (Path(path),*Path(path).parents)):raise ValueError('私有目录不能包含链接')
    native_module('resource_security').private_directory(Path(path))

def atomic(path, data):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

class ResourceLease:
    def __init__(self,vault,rows):
        self.directory=checked_path(vault.directory,'runtime/'+uuid.uuid4().hex)
        private_dir(self.directory); self.paths={};self.closed=False
        try:
            for row in rows:
                target=checked_path(self.directory,row['path'])
                target.parent.mkdir(parents=True,exist_ok=True)
                pending=target.with_name(target.name+'.pending')
                try:
                    with pending.open('xb') as stream:vault._decode(row,stream)
                    pending.replace(target)
                finally:pending.unlink(missing_ok=True)
                self.paths[row['path']]=target
        except BaseException:self.close();raise
    def close(self):
        if self.closed:return
        if linked(self.directory):raise OSError('运行目录变成链接，未跟随删除')
        if self.directory.exists():
            if any(linked(p) for p in self.directory.rglob('*')):raise OSError('运行资源包含链接，未跟随删除')
            try:shutil.rmtree(self.directory)
            except FileNotFoundError:
                if self.directory.exists():raise
        self.closed=True
    def __enter__(self):return self
    def __exit__(self,*_error):self.close()

class ResourceVault:
    def __init__(self,root,*,directory=None,store=None):
        self.root=Path(root).resolve()
        scope=hashlib.sha256(os.fsencode(str(self.root))).hexdigest()[:16]
        locator=checked_path(self.root,'data/resource-vault-location.json')
        if directory is None and locator.is_file():
            if locator.stat().st_size>8192:raise ValueError('保险库位置配置过大')
            location=json.loads(locator.read_text(encoding='utf-8'))
            if not isinstance(location,dict) or location.get('schema')!=1 or not isinstance(location.get('directory'),str) or not Path(location['directory']).is_absolute():raise ValueError('保险库位置配置无效')
            directory=location['directory']
        self.directory=(Path(directory) if directory is not None else self.root.parent/'RemChat-private'/scope).absolute()
        if self.root==self.directory.resolve() or self.root in self.directory.resolve().parents:
            raise ValueError('保险库必须在工程目录之外')
        self.index=self.directory/'manifest.json'
        self.marker=self.directory/'.remchat-resource-vault'
        if self.directory.exists() and (not self.directory.is_dir() or linked(self.directory) or (not self.marker.is_file() and any(self.directory.iterdir()))):raise ValueError('已有未知目录不能充当保险库')
        self.store=store if store is not None else create_secret_store(self.root)
        self._lock=threading.RLock()
    @property
    def enabled(self):return self.index.is_file()
    @contextmanager
    def _maintenance(self):
        private_dir(self.directory)
        if not self.marker.exists():atomic(self.marker,b'RemChat private resources v1\n')
        lock=self.directory/'maintenance.lock'
        try:descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:raise OSError('保险库维护正在进行或上次中断；不会并发覆盖清单') from None
        try:
            os.write(descriptor,str(os.getpid()).encode('ascii'));os.close(descriptor);descriptor=None
            yield
        finally:
            if descriptor is not None:os.close(descriptor)
            lock.unlink(missing_ok=True)
    def _master(self,create=False):
        secret=self.store.get(KEY_ID)
        wrapped=checked_path(self.directory,'master.dpapi')
        if secret is None and os.name=='nt' and wrapped.is_file():
            from .platforms.windows.secrets import WindowsDpapiProtector
            secret=base64.b64encode(WindowsDpapiProtector().unprotect(wrapped.read_bytes())).decode('ascii')
        if secret is None:
            if not create or self.index.exists():raise OSError('资源密钥不可用；不会创建新密钥覆盖旧保险库')
            master=os.urandom(32)
            self.store.set(KEY_ID,base64.b64encode(master).decode('ascii'))
            secret=self.store.get(KEY_ID)
            if secret!=base64.b64encode(master).decode('ascii'):raise OSError('资源密钥写后验证失败')
            if os.name=='nt':
                from .platforms.windows.secrets import WindowsDpapiProtector
                atomic(self.directory/'master.dpapi',WindowsDpapiProtector().protect(master))
        try:master=base64.b64decode(secret,validate=True)
        except (ValueError,TypeError):raise OSError('资源密钥格式错误') from None
        if len(master)!=32:raise OSError('资源密钥长度错误')
        return master
    def _sign(self,payload):
        key=derive(self._master(),None,b'RemChat manifest authentication v1')
        return hmac.new(key,canonical(payload),hashlib.sha256).hexdigest()
    def _load(self):
        if not self.index.exists():return {'schema':1,'policy':{'inference':True,'training':False,'export':False},'entries':{}}
        if linked(self.directory) or linked(self.index) or self.index.stat().st_size>16*1024*1024:raise ValueError('保险库清单无效')
        data=json.loads(self.index.read_text(encoding='utf-8'))
        if not isinstance(data,dict):raise ValueError('保险库清单无效')
        signature=data.pop('signature',None)
        if not isinstance(signature,str) or not hmac.compare_digest(signature,self._sign(data)):raise ValueError('保险库清单认证失败')
        if data.get('schema')!=1 or not isinstance(data.get('entries'),dict):raise ValueError('保险库版本无效')
        policy=data.get('policy')
        if not isinstance(policy,dict) or set(policy)!= {'inference','training','export'} or any(type(v)!=bool for v in policy.values()):raise ValueError('资源策略无效')
        for name,row in data['entries'].items():
            if not isinstance(row,dict) or row.get('path')!=name:raise ValueError('资源记录无效')
            relative_name(name)
            if not re.fullmatch(r'[a-f0-9]{32}\.rvault',str(row.get('blob',''))):raise ValueError('资源对象名称无效')
            if type(row.get('bytes'))!=int or not 0<=row['bytes']<2**36 or not re.fullmatch('[a-f0-9]{64}',str(row.get('sha256',''))):raise ValueError('资源记录大小无效')
            if row.get('kind') not in ('voice','configuration','artwork'):raise ValueError('资源类别无效')
        return data
    def _save(self,data):
        payload=dict(data,signature=self._sign(data));atomic(self.index,canonical(payload))
    def require(self,capability):
        if not self.enabled:return
        if capability not in self._load()['policy'] or not self._load()['policy'][capability]:raise PermissionError('资源策略不允许 '+capability)
    def policy(self,capability,allow):
        if capability not in ('inference','training','export') or type(allow)!=bool:raise ValueError('资源策略参数无效')
        with self._lock,self._maintenance():
            data=self._load();data['policy'][capability]=allow;self._save(data)
    def status(self):
        data=self._load()
        return {'enabled':self.enabled,'files':len(data['entries']),'bytes':sum(r['bytes'] for r in data['entries'].values()),'policy':data['policy'],'protection':'AES-256-GCM + account key store','outside_repository':True}
    def contains(self,path):
        if not self.enabled:return False
        try:name=Path(path).absolute().relative_to(self.root).as_posix()
        except ValueError:return False
        return name in self._load()['entries']
    def _decode(self,row,output=None,*,master=None):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        path=checked_path(self.directory,'blobs/'+row['blob'])
        if path.stat().st_size!=row['bytes']+52:raise ValueError('资源密文大小错误')
        digest=hashlib.sha256();written=0
        with path.open('rb') as source:
            header=source.read(36)
            if header[:8]!=MAGIC:raise ValueError('资源密文格式错误')
            source.seek(-16,2);tag=source.read(16);source.seek(36)
            key=derive(master or self._master(),header[8:24],b'RemChat resource file v1')
            decryptor=Cipher(algorithms.AES256(key),modes.GCM(header[24:36],tag)).decryptor()
            decryptor.authenticate_additional_data(header+row['path'].encode('utf-8'))
            remaining=row['bytes']
            while remaining:
                block=source.read(min(BLOCK,remaining))
                if not block:raise ValueError('资源密文截断')
                plaintext=decryptor.update(block);digest.update(plaintext);written+=len(plaintext)
                if output is not None:output.write(plaintext)
                remaining-=len(block)
            tail=decryptor.finalize();digest.update(tail);written+=len(tail)
            if output is not None:output.write(tail);output.flush();os.fsync(output.fileno())
        if written!=row['bytes'] or digest.hexdigest()!=row['sha256']:raise ValueError('资源内容完整性错误')
        return written
    def seal(self,path,*,kind='voice',remove_plaintext=True):
        return self.seal_many([(path,kind,remove_plaintext)])[0]
    def seal_many(self,items):
        """Commit a verified batch once, then remove only unchanged originals.

        A crash before commit leaves originals intact. A crash after commit can
        leave redundant originals; it cannot remove the only verified copy.
        """
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        with self._lock,self._maintenance():
            private_dir(self.directory);private_dir(self.directory/'blobs');private_dir(self.directory/'runtime')
            master=self._master(create=True);data=self._load()
            created=[];pending=[];old_blobs=[];committed=False
            try:
                seen=set()
                for path,kind,remove in items:
                    if kind not in ('voice','configuration','artwork'):raise ValueError('资源类别无效')
                    name=Path(path).absolute().relative_to(self.root).as_posix();source=checked_path(self.root,name)
                    if name in seen:raise ValueError('加密批次存在重复路径')
                    seen.add(name)
                    if not source.is_file():raise FileNotFoundError('待加密资源不存在')
                    before=source.stat();digest=hashlib.sha256();count=0
                    salt=os.urandom(16);nonce=os.urandom(12);header=MAGIC+salt+nonce
                    encryptor=Cipher(algorithms.AES256(derive(master,salt,b'RemChat resource file v1')),modes.GCM(nonce)).encryptor()
                    encryptor.authenticate_additional_data(header+name.encode('utf-8'))
                    blob=uuid.uuid4().hex+'.rvault';target=checked_path(self.directory,'blobs/'+blob);created.append(target)
                    with source.open('rb') as plain,target.open('xb') as encrypted:
                        encrypted.write(header)
                        while block:=plain.read(BLOCK):
                            digest.update(block);count+=len(block);encrypted.write(encryptor.update(block))
                        encrypted.write(encryptor.finalize());encrypted.write(encryptor.tag)
                        encrypted.flush();os.fsync(encrypted.fileno())
                    row={'path':name,'blob':blob,'bytes':count,'sha256':digest.hexdigest(),'kind':kind,'mtime_ns':before.st_mtime_ns}
                    self._decode(row,master=master)
                    previous=data['entries'].get(name)
                    if previous:old_blobs.append(previous['blob'])
                    data['entries'][name]=row
                    pending.append((source,before,count,bool(remove)))
                for source,before,_count,_remove in pending:
                    after=source.stat()
                    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise OSError('资源加密时发生外部修改，原文件保留')
                self._save(data);committed=True
                for source,before,_count,remove in pending:
                    if remove:
                        after=source.stat()
                        if linked(source) or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise OSError('资源在提交后改变，已保留明文和密文')
                        source.unlink()
                for old in old_blobs:checked_path(self.directory,'blobs/'+old).unlink(missing_ok=True)
            except BaseException:
                if not committed:
                    # Replacement may have committed just before an I/O error.
                    try:current={r['blob'] for r in self._load()['entries'].values()}
                    except Exception:current={p.name for p in created}
                    for target in created:
                        if target.name not in current:target.unlink(missing_ok=True)
                raise
        return [{'bytes':count,'verified':True,'plaintext_removed':remove} for _source,_before,count,remove in pending]
    def lease(self,paths,*,capability='inference'):
        self.require(capability);data=self._load();rows=[]
        for path in paths:
            name=Path(path).absolute().relative_to(self.root).as_posix();row=data['entries'].get(name)
            if row is None or row['kind']!='voice':raise PermissionError('资源不属于已保护的声线')
            rows.append(row)
        return ResourceLease(self,rows)
    def latest_weight(self,prefix,suffix):
        rows=self._load()['entries']
        names=[name for name in rows if name.startswith(prefix.rstrip('/')+'/') and name.endswith(suffix)]
        if not names:raise FileNotFoundError('保险库没有对应声线模型')
        return self.root/max(names,key=lambda name:(rows[name].get('mtime_ns',0),name))
    def restore_local(self,*,include_configuration=False):
        """Only app-owned artwork/configuration; voice raw export remains denied."""
        rows=[r for r in self._load()['entries'].values() if r['kind']=='artwork' or (include_configuration and r['kind']=='configuration')]
        with ResourceLease(self,rows) as lease:
            for row in rows:
                target=checked_path(self.root,row['path']);target.parent.mkdir(parents=True,exist_ok=True)
                atomic(target,lease.paths[row['path']].read_bytes())
        return {'restored_files':len(rows),'voice_raw_exported':False}
