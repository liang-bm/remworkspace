"""Windows installer sources, never model API endpoints or global settings."""
from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from ...process import CommandError, run_command

PYPI_INDEXES = (
    'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple',
    'https://pypi.org/simple',
)
TORCH_INDEXES = (
    'https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/',
    'https://download.pytorch.org/whl/cu128',
)
PUBLIC_HF_REPOSITORIES = frozenset({
    'bartowski/Qwen_Qwen3.5-4B-GGUF',
    'lj1995/GPT-SoVITS',
    'XXXXRT/GPT-SoVITS-Pretrained',
})
# Official LFS metadata, checked on 2026-10-04. A different/custom model is not
# silently held to this file's digest, nor sent to a mirror as a private repo.
DEFAULT_QWEN_PATH = '/bartowski/Qwen_Qwen3.5-4B-GGUF/resolve/main/Qwen_Qwen3.5-4B-Q4_K_M.gguf'
DEFAULT_QWEN_SIZE = 3013027808
DEFAULT_QWEN_SHA256 = '13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983'
USER_AGENT = 'RemChat/Windows-installer'


def candidates(url: str) -> tuple[str, ...]:
    """Only known public HF paths without credentials get a mirror candidate."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'huggingface.co'
            or any(key not in {'download', 'recursive', 'expand', 'limit'}
                   for key in urllib.parse.parse_qs(parsed.query))):
        return (url,)
    parts = parsed.path.strip('/').split('/')
    offset = 2 if parts[:2] == ['api', 'models'] else 0
    repository = '/'.join(parts[offset:offset + 2])
    if repository not in PUBLIC_HF_REPOSITORIES:
        return (url,)
    mirrored = parsed._replace(netloc='hf-mirror.com').geturl()
    return mirrored, url


def request(url: str, headers: dict[str, str] | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})


def read_json(url: str, *, log=print) -> object:
    urls = candidates(url)
    for index, candidate in enumerate(urls):
        try:
            with urllib.request.urlopen(request(candidate), timeout=20) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise ValueError('下载目录元数据过大，已停止读取')
                return json.loads(body.decode('utf-8'))
        except (urllib.error.URLError, OSError, ValueError) as error:
            if index == len(urls) - 1:
                raise
            log(f'[回退] 模型目录镜像不可用，改用官方源：{type(error).__name__}')
    raise RuntimeError('没有可用下载源')


def install_from_indexes(command: list[str], indexes: tuple[str, ...], *, cwd: Path, log=print) -> None:
    """Retry the same constrained command, without mixing competing indexes."""
    for index, source in enumerate(indexes):
        log(f'[安装源] {source}')
        try:
            run_command([*command, '--index-url', source], cwd=cwd, log=log)
            return
        except CommandError:
            if index == len(indexes) - 1:
                raise
            log('[回退] 国内依赖源安装失败，使用同版本约束重试官方源。')
