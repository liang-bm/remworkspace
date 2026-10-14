"""Public GPT-SoVITS asset trees for the Windows installer."""
from pathlib import Path, PurePosixPath
import urllib.parse

from .download_sources import read_json
from .file_transfer import download_file


def download_gpt_pretrained(config, log=print, progress=None) -> None:
    if not config.gpt_sovits_dir.exists():
        raise FileNotFoundError('请先下载 GPT-SoVITS 源码')
    destination = config.gpt_sovits_dir / 'GPT_SoVITS' / 'pretrained_models'
    repository = str(config.downloads['gpt_pretrained_repo'])
    encoded_repo = '/'.join(urllib.parse.quote(part, safe='') for part in repository.split('/'))
    required = (
        'chinese-hubert-base', 'chinese-roberta-wwm-ext-large',
        'models--nvidia--bigvgan_v2_24khz_100band_256x',
        'sv/pretrained_eres2netv2w24s4ep4.ckpt', 'v2Pro/s2Dv2Pro.pth',
        'v2Pro/s2Gv2Pro.pth', 's1v3.ckpt', 'hifigan_config.json', 'hifigan_do_03357000',
    )
    trees = {}
    for requested in required:
        single = bool(PurePosixPath(requested).suffix) or requested == 'hifigan_do_03357000'
        parent = str(PurePosixPath(requested).parent) if single else requested
        prefix = '' if parent == '.' else '/' + '/'.join(
            urllib.parse.quote(part, safe='') for part in PurePosixPath(parent).parts)
        if prefix not in trees:
            payload = read_json(f'https://huggingface.co/api/models/{encoded_repo}/tree/main{prefix}?recursive=true&expand=false&limit=1000', log=log)
            if not isinstance(payload, list):
                raise ValueError(f'模型目录格式无效：{requested}')
            trees[prefix] = [row for row in payload if isinstance(row, dict) and row.get('type') == 'file']
            # This selected asset tree must be complete. Don't silently report
            # success if the server capped a newly expanded directory.
            if len(payload) >= 1000:
                raise ValueError(f'模型目录达到分页上限，请检查仓库结构：{requested}')
        rows = [row for row in trees[prefix] if not single or row.get('path') == requested]
        if not rows:
            raise FileNotFoundError(f'模型仓库缺少所需文件：{requested}')
        for row in rows:
            remote = str(row.get('path', ''))
            parts = PurePosixPath(remote).parts
            if not parts or remote.startswith('/') or any(part in {'.', '..'} or ':' in part or '\\' in part for part in parts):
                raise ValueError('模型目录包含不安全路径')
            if not single and not remote.startswith(requested + '/'):
                raise ValueError('模型目录返回了所选范围之外的路径')
            target = destination.joinpath(*parts)
            encoded_path = '/'.join(urllib.parse.quote(part, safe='') for part in parts)
            lfs = row.get('lfs') or {}
            if not isinstance(lfs, dict):
                raise ValueError('模型文件摘要格式无效')
            download_file(f'https://huggingface.co/{encoded_repo}/resolve/main/{encoded_path}?download=true',
                          target, log=log, progress=progress,
                          expected_size=row.get('size'), sha256=lfs.get('oid'))
