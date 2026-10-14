"""Verified Windows downloads; mirror and origin never share partial bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .download_sources import (
    DEFAULT_QWEN_PATH, DEFAULT_QWEN_SHA256, DEFAULT_QWEN_SIZE, candidates, request,
)


class DownloadIntegrityError(ValueError):
    """Incomplete or mismatched bytes must not become a ready component."""


def _assert_unlinked(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
            raise DownloadIntegrityError(f'下载路径不能是链接：{candidate}')


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError as error:
        raise DownloadIntegrityError('服务器 Content-Length 无效') from error
    if number < 0:
        raise DownloadIntegrityError('服务器 Content-Length 不能为负数')
    return number


def _verify(path: Path, size: int | None, digest: str | None) -> None:
    actual = path.stat().st_size
    if not actual or (size is not None and actual != size):
        raise DownloadIntegrityError(f'下载大小不符：{path.name}，实际 {actual}，预期 {size}')
    if digest:
        value = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                value.update(chunk)
        if value.hexdigest() != digest.lower():
            raise DownloadIntegrityError(f'SHA256 校验失败：{path.name}')


def _retain_invalid(partial: Path, log) -> None:
    if partial.exists():
        retained = partial.with_name(f'{partial.name}.invalid-{uuid.uuid4().hex[:12]}')
        partial.rename(retained)
        log(f'[保留] 无效续传数据：{retained}')


def _receive(url: str, partial: Path, *, size: int | None, digest: str | None, log, progress, name: str) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    if size is not None and offset > size:
        _retain_invalid(partial, log)
        offset = 0
    headers = {'Range': f'bytes={offset}-'} if offset else {}
    try:
        response = urllib.request.urlopen(request(url, headers), timeout=20)
    except urllib.error.HTTPError as error:
        # 416 is not a completion signal. Only an independently known complete
        # size (and digest when available) permits promotion of existing bytes.
        if error.code != 416 or not offset:
            error.close()
            raise
        error.close()
        if size is not None and offset == size:
            _verify(partial, size, digest)
            if progress:
                progress(name, offset, size)
            return
        _retain_invalid(partial, log)
        response = urllib.request.urlopen(request(url), timeout=20)
        offset = 0
    with response:
        status = getattr(response, 'status', 200)
        if status not in (200, 206):
            raise DownloadIntegrityError(f'下载返回非成功状态：{status}')
        if 'text/html' in response.headers.get('Content-Type', '').lower():
            raise DownloadIntegrityError('下载源返回网页而非文件，已拒绝安装')
        total = None
        if status == 206:
            span = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not span or int(span[1]) != offset or int(span[2]) < offset or int(span[2]) >= int(span[3]):
                raise DownloadIntegrityError('续传 Content-Range 无效，未拼接文件')
            total = int(span[3])
            length = _content_length(response.headers.get('Content-Length'))
            if length is not None and length != int(span[2]) - offset + 1:
                raise DownloadIntegrityError('续传长度与范围不一致')
        else:
            # A server ignoring Range sends a complete object. Rewrite only our
            # partial, never append a second full object to the old prefix.
            offset = 0
            total = _content_length(response.headers.get('Content-Length'))
        if size is not None and total is not None and total != size:
            raise DownloadIntegrityError(f'服务器文件大小改变：预期 {size}，收到 {total}')
        final_host = urllib.parse.urlsplit(response.geturl()).hostname
        log(f'[下载来源] {final_host} → {name}')
        downloaded = last_report = offset
        with partial.open('ab' if status == 206 and offset else 'wb') as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                downloaded += len(chunk)
                if progress and downloaded - last_report >= 16 * 1024 * 1024:
                    progress(name, downloaded, size if size is not None else total)
                    last_report = downloaded
        _verify(partial, size if size is not None else total, digest)
        if progress:
            progress(name, downloaded, size if size is not None else total)


def download_file(url: str, destination: Path, *, log=print, progress=None,
                  expected_size: int | None = None, sha256: str | None = None) -> Path:
    parsed = urllib.parse.urlsplit(url)
    if parsed.netloc == 'huggingface.co' and parsed.path == DEFAULT_QWEN_PATH:
        expected_size = DEFAULT_QWEN_SIZE
        sha256 = DEFAULT_QWEN_SHA256
    if expected_size is not None and (type(expected_size) is not int or expected_size <= 0):
        raise ValueError('下载预期大小必须是正整数')
    if sha256 is not None and not re.fullmatch(r'[a-fA-F0-9]{64}', sha256):
        raise ValueError('下载 SHA256 格式无效')
    _assert_unlinked(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size:
        # Existing user/model files are never silently deleted or overwritten.
        _verify(destination, expected_size, sha256)
        log(f'[跳过] 已存在{ "且校验通过" if sha256 else "" } {destination}')
        return destination
    urls = candidates(url)
    for index, candidate in enumerate(urls):
        mirror = candidate != url
        partial = destination.with_name(destination.name + ('.mirror.part' if mirror else '.part'))
        _assert_unlinked(partial)
        log(f'[下载源] {urllib.parse.urlsplit(candidate).hostname} → {destination}')
        try:
            _receive(candidate, partial, size=expected_size, digest=sha256,
                     log=log, progress=progress, name=destination.name)
            if destination.suffix.lower() == '.gguf':
                with partial.open('rb') as stream:
                    if stream.read(4) != b'GGUF':
                        raise DownloadIntegrityError('不是有效 GGUF 文件，已拒绝安装')
            _assert_unlinked(destination)
            # On Windows rename refuses an existing destination, unlike
            # replace. A file created by somebody else during the download
            # cannot be silently overwritten at promotion.
            partial.rename(destination)
            return destination
        except DownloadIntegrityError:
            _retain_invalid(partial, log)
            if index == len(urls) - 1:
                raise
            log('[回退] 镜像完整性检查失败，改用官方源；不混合两者的续传文件。')
        except (urllib.error.URLError, OSError) as error:
            if index == len(urls) - 1:
                raise
            log(f'[回退] 镜像传输失败，改用官方源：{type(error).__name__}')
    raise RuntimeError('没有可用下载源')
