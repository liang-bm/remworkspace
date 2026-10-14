"""Explicit local-only resource and semantic-memory maintenance."""
import argparse
import json
from pathlib import Path
from .config import load_config
from .secure_resources import ResourceVault,checked_path,atomic,canonical

def main(argv=None):
    parser=argparse.ArgumentParser(description='本地私有资源维护；不提交、不上传，不公开声线')
    parser.add_argument('action',choices=['status','verify','seal','policy','restore-local','semantic'])
    parser.add_argument('--path',help='当前工程内的相对路径')
    parser.add_argument('--kind',choices=['voice','configuration','artwork'],default='voice')
    parser.add_argument('--capability',choices=['inference','training','export'])
    parser.add_argument('--allow',action='store_true')
    parser.add_argument('--include-configuration',action='store_true',help='显式恢复本地个人配置/加密凭据，绝不上传')
    parser.add_argument('--base-url');parser.add_argument('--model');parser.add_argument('--revision')
    parser.add_argument('--vault-directory',type=Path,help='仓库之外的已有本地保险库；恢复仍需原账户密钥')
    args=parser.parse_args(argv);config=load_config();vault=ResourceVault(config.root,directory=args.vault_directory)
    if args.action=='status':result=vault.status()
    elif args.action=='verify':
        data=vault._load()
        for row in data['entries'].values():vault._decode(row)
        result=dict(vault.status(),verified=True)
    elif args.action=='seal':
        if not args.path:parser.error('seal 必须明确指定 --path')
        path=checked_path(config.root,args.path)
        result=vault.seal(path,kind=args.kind,remove_plaintext=args.kind=='voice')
    elif args.action=='policy':
        if not args.capability:parser.error('policy 必须指定 --capability')
        vault.policy(args.capability,args.allow);result=vault.status()
    elif args.action=='restore-local':
        result=vault.restore_local(include_configuration=args.include_configuration)
        if args.vault_directory is not None:
            location=checked_path(config.root,'data/resource-vault-location.json');location.parent.mkdir(parents=True,exist_ok=True)
            atomic(location,canonical({'schema':1,'directory':str(vault.directory)}))
    else:
        from .semantic_memory import SemanticMemory
        result=SemanticMemory(config.root/'data/memory.sqlite3').configure({'enabled':args.allow,'base_url':args.base_url,'model':args.model,'revision':args.revision})
    print(json.dumps(result,ensure_ascii=False));return 0

if __name__=='__main__':raise SystemExit(main())
