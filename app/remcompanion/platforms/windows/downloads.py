"""Windows-only download, environment installation and release selection."""

from pathlib import Path
import shutil
from ...config import AppConfig, ensure_project_directories
from ...process import run_command
from ...downloader import (
    LogCallback, ProgressCallback, _github_assets,
    _safe_extract_zip, _safe_extract_tar, clone_gpt_sovits,
    install_release_zip,
)
from .download_sources import PYPI_INDEXES, TORCH_INDEXES, install_from_indexes
from .file_transfer import download_file
from .model_assets import download_gpt_pretrained

WINDOWS_TORCH_PACKAGES = ("torch==2.8.0", "torchaudio==2.8.0")


def install_llama_release(
    *,
    repo: str,
    asset_contains: str,
    destination: Path,
    downloads_dir: Path,
    log: LogCallback,
    progress: ProgressCallback | None,
) -> None:
    """CUDA 版 llama.cpp 需要主程序包和同版本 cudart 包。"""

    assets = _github_assets(repo, asset_contains)
    main = [item for item in assets if not item[1].lower().startswith("cudart-")]
    runtime = [item for item in assets if item[1].lower().startswith("cudart-")]
    if not main:
        raise RuntimeError("llama.cpp 发布中没有找到 CUDA 主程序包")
    selected = [main[0], *runtime[:1]]
    for url, filename in selected:
        archive = download_file(url, downloads_dir / filename, log=log, progress=progress)
        _safe_extract_zip(archive, destination)
        log(f"[完成] 已解压 llama.cpp 组件 {filename}")


def _gpt_venv_creation_command(environment_dir: Path) -> list[str] | None:
    """Return the uv command needed to create/repair the managed environment.

    A failed dependency installation still leaves a valid virtual environment
    behind.  In that normal resume case, returning ``None`` prevents a second
    ``uv venv`` call from failing with "already exists".  ``--clear`` is used
    only when our managed directory exists but its Python executable does not.
    """

    python = environment_dir / "Scripts" / "python.exe"
    if python.is_file() and python.stat().st_size > 0:
        return None

    command = ["uv", "venv", str(environment_dir), "--python", "3.10"]
    if environment_dir.exists():
        command.append("--clear")
    return command


def setup_gpt_environment(config: AppConfig, log: LogCallback = print) -> None:
    marker = config.root / "runtime" / "gpt-sovits" / ".ready"
    if marker.exists() and config.gpt_python.exists():
        log(f"[跳过] GPT-SoVITS Python 环境已经完成: {config.gpt_python}")
        return
    if not config.gpt_sovits_dir.exists():
        raise FileNotFoundError("请先下载 GPT-SoVITS 源码")
    if shutil.which("uv") is None:
        raise FileNotFoundError("找不到 uv。请安装 uv 后重试。")

    environment_dir = marker.parent
    # 明确把受管 Python 安装到启动脚本指定的缓存目录，避免 uv
    # 在首次建环境时回落到系统盘或依赖机器上已有的 Python 3.10。
    run_command(
        ["uv", "python", "install", "3.10", "--no-registry"],
        cwd=config.root,
        log=log,
    )
    python = environment_dir / "Scripts" / "python.exe"
    venv_command = _gpt_venv_creation_command(environment_dir)
    if venv_command is None:
        log(f"[继续] 复用已创建但尚未完成安装的 Python 环境: {python}")
    else:
        run_command(
            venv_command,
            cwd=config.root,
            log=log,
        )
    torch_lock = config.root / 'platforms' / 'windows' / 'config' / 'torch-windows-cu128.txt'
    if not torch_lock.is_file():
        raise FileNotFoundError(f'找不到 Windows Torch 哈希约束文件: {torch_lock}')
    install_from_indexes(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            '-r', str(torch_lock), '--no-deps', '--require-hashes',
        ],
        TORCH_INDEXES,
        cwd=config.root,
        log=log,
    )
    extra = config.gpt_sovits_dir / "extra-req.txt"
    requirements = config.gpt_sovits_dir / "requirements.txt"
    constraints = config.root / "platforms" / "windows" / "config" / "gpt-sovits-windows-constraints.txt"
    if not constraints.exists():
        raise FileNotFoundError(f"找不到 Windows 依赖约束文件: {constraints}")
    install_from_indexes(
        ["uv", "pip", "install", "--python", str(python), "-r", str(extra), "--no-deps"],
        PYPI_INDEXES,
        cwd=config.root,
        log=log,
    )
    install_from_indexes(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "-r",
            str(requirements),
            "--constraint",
            str(constraints),
        ],
        PYPI_INDEXES,
        cwd=config.root,
        log=log,
    )
    # TorchAudio 2.9+ delegates loading to TorchCodec, whose official index does
    # not currently publish Windows wheels.  The pinned 2.8 pair keeps the
    # native Windows audio backend and must be verified before writing .ready.
    run_command(
        [
            str(python),
            "-c",
            (
                "import torch, torchaudio; "
                "assert torch.__version__.startswith('2.8.0'), torch.__version__; "
                "assert torchaudio.__version__.startswith('2.8.0'), torchaudio.__version__; "
                "assert torch.cuda.is_available(), 'CUDA is unavailable'; "
                "print('torch=' + torch.__version__ + ', torchaudio=' + "
                "torchaudio.__version__ + ', gpu=' + torch.cuda.get_device_name(0))"
            ),
        ],
        cwd=config.gpt_sovits_dir,
        log=log,
    )
    marker.write_text("GPT-SoVITS environment ready\n", encoding="utf-8")
    log(f"[完成] GPT-SoVITS 环境: {python}")


def download_language_assets(
    config: AppConfig,
    log: LogCallback = print,
    progress: ProgressCallback | None = None,
) -> None:
    """安装中日文本前端资源及 TTS 推理语言检测模型。"""

    downloads = config.root / "tmp" / "downloads"
    # split-lang 2.x 会显式请求 fast-langdetect 的 full 模型。仅安装 Python
    # 包自带的 lite 模型时，API 可以启动，但第一次 /tts 请求才会失败。
    # 因此把完整模型纳入一键下载，而不是依赖推理请求临时联网。
    download_file(
        str(config.downloads["fast_langdetect_url"]),
        config.fast_langdetect_model,
        log=log,
        progress=progress,
    )
    g2pw_archive = download_file(
        str(config.downloads["g2pw_url"]),
        downloads / "G2PWModel.zip",
        log=log,
        progress=progress,
    )
    g2pw_target = config.gpt_sovits_dir / "GPT_SoVITS" / "text"
    if not (g2pw_target / "G2PWModel").exists():
        _safe_extract_zip(g2pw_archive, g2pw_target)
        log(f"[完成] G2PW 中文前端: {g2pw_target / 'G2PWModel'}")

    nltk_archive = download_file(
        str(config.downloads["nltk_url"]),
        downloads / "nltk_data.zip",
        log=log,
        progress=progress,
    )
    nltk_marker = config.root / "runtime" / "gpt-sovits" / ".nltk-ready"
    if not nltk_marker.exists():
        _safe_extract_zip(nltk_archive, config.root / "runtime" / "gpt-sovits")
        nltk_marker.write_text("ready\n", encoding="utf-8")
        log("[完成] NLTK 数据")

    open_jtalk_archive = download_file(
        str(config.downloads["open_jtalk_url"]),
        downloads / "open_jtalk_dic_utf_8-1.11.tar.gz",
        log=log,
        progress=progress,
    )
    pyopenjtalk_dir = (
        config.root
        / "runtime"
        / "gpt-sovits"
        / "Lib"
        / "site-packages"
        / "pyopenjtalk"
    )
    open_jtalk_marker = pyopenjtalk_dir / ".dictionary-ready"
    if not open_jtalk_marker.exists():
        _require_dir = pyopenjtalk_dir
        if not _require_dir.exists():
            raise FileNotFoundError(f"没有找到 pyopenjtalk 安装目录: {_require_dir}")
        _safe_extract_tar(open_jtalk_archive, pyopenjtalk_dir)
        open_jtalk_marker.write_text("ready\n", encoding="utf-8")
        log("[完成] OpenJTalk 日语词典")


def download_components(
    config: AppConfig,
    *,
    log: LogCallback = print,
    progress: ProgressCallback | None = None,
) -> None:
    ensure_project_directories(config)
    downloads = config.root / "tmp" / "downloads"

    clone_gpt_sovits(config, log)
    install_release_zip(
        repo=str(config.downloads["ffmpeg_repo"]),
        asset_contains=str(config.downloads["ffmpeg_asset_contains"]),
        destination=config.root / "engines" / "ffmpeg",
        downloads_dir=downloads,
        log=log,
        progress=progress,
    )
    download_gpt_pretrained(config, log, progress)
    setup_gpt_environment(config, log)
    download_language_assets(config, log, progress)
    download_file(
        str(config.downloads["qwen_url"]),
        config.qwen_model,
        log=log,
        progress=progress,
    )
    install_llama_release(
        repo=str(config.downloads["llama_repo"]),
        asset_contains=str(config.downloads["llama_asset_contains"]),
        destination=config.root / "engines" / "llama.cpp",
        downloads_dir=downloads,
        log=log,
        progress=progress,
    )
    log("[完成] 所有下载和环境安装任务已结束。")


def download_local_llm(
    config: AppConfig,
    *,
    log: LogCallback = print,
    progress: ProgressCallback | None = None,
) -> None:
    """只安装聊天所需的 Qwen GGUF 与 llama.cpp。

    设置页的一键按钮不应顺带下载 GPT-SoVITS、FFmpeg 或训练依赖。该窄入口
    复用已经验证过的断点续传与安全解压逻辑，下载完成后即可由原有启动器加载。
    """

    ensure_project_directories(config)
    downloads = config.root / "tmp" / "downloads"
    download_file(
        str(config.downloads["qwen_url"]),
        config.qwen_model,
        log=log,
        progress=progress,
    )
    install_llama_release(
        repo=str(config.downloads["llama_repo"]),
        asset_contains=str(config.downloads["llama_asset_contains"]),
        destination=config.root / "engines" / "llama.cpp",
        downloads_dir=downloads,
        log=log,
        progress=progress,
    )
    log("[完成] 本地聊天模型与 llama.cpp 已就绪。")
