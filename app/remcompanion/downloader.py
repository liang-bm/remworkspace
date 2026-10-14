"""可恢复下载器与第三方组件安装器。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
import json
import html
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .config import AppConfig
from .process import run_command
from .platforms import native_module

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[str, int, int | None], None]
USER_AGENT = "RemCompanion/0.1 (+local desktop installer)"


def _request(url: str, *, headers: dict[str, str] | None = None) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    return urllib.request.Request(url, headers=merged)


def download_file(
    url: str,
    destination: Path,
    *,
    log: LogCallback = print,
    progress: ProgressCallback | None = None,
) -> Path:
    """下载到 `.part` 后原子改名；服务器支持 Range 时自动续传。"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        log(f"[跳过] 已存在 {destination}")
        return destination

    partial = destination.with_suffix(destination.suffix + ".part")
    downloaded = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}

    try:
        response = urllib.request.urlopen(_request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as error:
        if error.code == 416 and partial.exists():
            partial.replace(destination)
            return destination
        raise

    status = getattr(response, "status", 200)
    if downloaded and status != 206:
        # 对方忽略 Range 时必须重写，不能把完整文件追加到旧 part 后面。
        downloaded = 0
    mode = "ab" if downloaded and status == 206 else "wb"
    content_length = response.headers.get("Content-Length")
    total = downloaded + int(content_length) if content_length else None
    log(f"[下载] {url}\n      -> {destination}")

    last_report = downloaded
    with partial.open(mode) as stream:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
            downloaded += len(chunk)
            if progress and (downloaded - last_report >= 16 * 1024 * 1024):
                progress(destination.name, downloaded, total)
                last_report = downloaded
    response.close()
    partial.replace(destination)
    if progress:
        progress(destination.name, downloaded, total)
    return destination


def _json_url(url: str) -> tuple[object, dict[str, str]]:
    with urllib.request.urlopen(_request(url), timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload, dict(response.headers.items())


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"ZIP 包含越界路径: {member.filename}")
        bundle.extractall(destination)


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"TAR 包含越界路径: {member.name}")
        bundle.extractall(destination)


def _github_assets(repo: str, contains: str) -> list[tuple[str, str]]:
    try:
        payload, _ = _json_url(f"https://api.github.com/repos/{repo}/releases/latest")
        assert isinstance(payload, dict)
        candidates = [
            (str(asset["browser_download_url"]), str(asset["name"]))
            for asset in payload.get("assets", [])
            if contains.lower() in str(asset.get("name", "")).lower()
        ]
        if candidates:
            return candidates
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        # GitHub 匿名 API 额度小且偶尔被本地网络中断；改走官方网页，不要求 Token。
        pass

    latest = f"https://github.com/{repo}/releases/latest"
    request = urllib.request.Request(
        latest,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        final_url = response.geturl()
    tag = PurePosixPath(urllib.parse.urlparse(final_url).path).name
    expanded_url = f"https://github.com/{repo}/releases/expanded_assets/{urllib.parse.quote(tag)}"
    expanded_request = urllib.request.Request(
        expanded_url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with urllib.request.urlopen(expanded_request, timeout=60) as response:
        page = response.read().decode("utf-8", errors="replace")
    candidates = _asset_links_from_html(page, contains)
    if not candidates:
        raise RuntimeError(f"{repo} 最新版本网页中找不到包含 {contains!r} 的文件")
    return candidates


def _github_asset(repo: str, contains: str) -> tuple[str, str]:
    candidates = _github_assets(repo, contains)
    # CUDA 发布通常同时包含主程序和 cudart 依赖；普通调用默认选择主程序。
    candidates.sort(key=lambda item: (item[1].lower().startswith("cudart-"), item[1].lower()))
    return candidates[0]


def _asset_links_from_html(page: str, contains: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    pattern = re.compile(r'href="(?P<href>[^"]+/releases/download/[^"]+)"')
    for match in pattern.finditer(page):
        href = html.unescape(match["href"])
        url = urllib.parse.urljoin("https://github.com", href)
        filename = urllib.parse.unquote(PurePosixPath(urllib.parse.urlparse(url).path).name)
        if contains.lower() in filename.lower():
            links.append((url, filename))
    return links


def install_release_zip(
    *,
    repo: str,
    asset_contains: str,
    destination: Path,
    downloads_dir: Path,
    log: LogCallback,
    progress: ProgressCallback | None,
) -> None:
    url, filename = _github_asset(repo, asset_contains)
    archive = download_file(url, downloads_dir / filename, log=log, progress=progress)
    _safe_extract_zip(archive, destination)
    log(f"[完成] 已解压 {repo} 到 {destination}")


def install_llama_release(
    *, repo: str, asset_contains: str, destination: Path, downloads_dir: Path,
    log: LogCallback, progress: ProgressCallback | None,
) -> None:
    """Install only the currently selected platform's llama runtime."""
    native_module("downloads").install_llama_release(
        repo=repo, asset_contains=asset_contains, destination=destination,
        downloads_dir=downloads_dir, log=log, progress=progress)


def clone_gpt_sovits(config: AppConfig, log: LogCallback = print) -> None:
    target = config.gpt_sovits_dir
    if (target / ".git").exists():
        log(f"[跳过] GPT-SoVITS 源码已经存在: {target}")
        return
    if any(target.iterdir()) if target.exists() else False:
        raise RuntimeError(f"目标目录非空且不是 Git 仓库，请人工检查: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            str(config.downloads["gpt_sovits_revision"]),
            str(config.downloads["gpt_sovits_git"]),
            str(target),
        ],
        cwd=config.root,
        log=log,
    )


def _hf_tree(repo_id: str, prefix: str) -> list[str]:
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in repo_id.split("/"))
    encoded_prefix = "/".join(urllib.parse.quote(part, safe="") for part in prefix.split("/"))
    url = f"https://huggingface.co/api/models/{encoded_repo}/tree/main/{encoded_prefix}?recursive=true&expand=false"
    payload, _ = _json_url(url)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise RuntimeError(f"Hugging Face 返回了未知目录结构: {prefix}")
    return [str(item["path"]) for item in payload if item.get("type") == "file"]


def download_hf_paths(
    repo_id: str,
    paths: list[str],
    destination: Path,
    *,
    explicit_files: set[str] | None = None,
    log: LogCallback = print,
    progress: ProgressCallback | None = None,
) -> None:
    """Download selected Hugging Face files or directory trees.

    Hugging Face model repositories sometimes contain files without an
    extension (for example ``hifigan_do_03357000``).  ``explicit_files`` keeps
    those paths from being mistaken for directories.
    """

    explicit_files = explicit_files or set()
    for requested in paths:
        suffix = PurePosixPath(requested).suffix
        files = [requested] if suffix or requested in explicit_files else _hf_tree(repo_id, requested)
        for remote_path in files:
            relative = Path(*PurePosixPath(remote_path).parts)
            url_path = "/".join(urllib.parse.quote(part, safe="") for part in PurePosixPath(remote_path).parts)
            url = f"https://huggingface.co/{repo_id}/resolve/main/{url_path}?download=true"
            download_file(url, destination / relative, log=log, progress=progress)


def download_gpt_pretrained(
    config: AppConfig,
    log: LogCallback = print,
    progress: ProgressCallback | None = None,
) -> None:
    if not config.gpt_sovits_dir.exists():
        raise FileNotFoundError("请先下载 GPT-SoVITS 源码")
    destination = config.gpt_sovits_dir / "GPT_SoVITS" / "pretrained_models"
    required = [
        "chinese-hubert-base",
        "chinese-roberta-wwm-ext-large",
        "models--nvidia--bigvgan_v2_24khz_100band_256x",
        "sv/pretrained_eres2netv2w24s4ep4.ckpt",
        "v2Pro/s2Dv2Pro.pth",
        "v2Pro/s2Gv2Pro.pth",
        "s1v3.ckpt",
        "hifigan_config.json",
        "hifigan_do_03357000",
    ]
    download_hf_paths(
        str(config.downloads["gpt_pretrained_repo"]),
        required,
        destination,
        explicit_files={"hifigan_do_03357000"},
        log=log,
        progress=progress,
    )


def _gpt_venv_creation_command(environment_dir: Path) -> list[str] | None:
    """Legacy Windows helper; new installation code uses its native adapter."""
    return native_module("downloads", platform="windows")._gpt_venv_creation_command(environment_dir)


def __getattr__(name: str):
    # Legacy API requested explicitly by existing Windows dependency tests.
    if name == "WINDOWS_TORCH_PACKAGES":
        return native_module("downloads", platform="windows").WINDOWS_TORCH_PACKAGES
    raise AttributeError(name)


def setup_gpt_environment(config: AppConfig, log: LogCallback = print) -> None:
    native_module("downloads").setup_gpt_environment(config, log)


def download_language_assets(config: AppConfig, log: LogCallback = print,
                             progress: ProgressCallback | None = None) -> None:
    native_module("downloads").download_language_assets(config, log, progress)


def download_components(config: AppConfig, *, log: LogCallback = print,
                        progress: ProgressCallback | None = None) -> None:
    native_module("downloads").download_components(config, log=log, progress=progress)


def download_local_llm(config: AppConfig, *, log: LogCallback = print,
                        progress: ProgressCallback | None = None) -> None:
    native_module("downloads").download_local_llm(config, log=log, progress=progress)


def close_active_downloads() -> None:
    """Native cancellation if supported; leave the Windows installer unchanged."""
    close = getattr(native_module("downloads"), "close_active_downloads", None)
    if close is not None:
        close()
