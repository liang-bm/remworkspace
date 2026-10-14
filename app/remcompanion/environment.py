"""环境检查与面向用户的状态摘要。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from .config import AppConfig, ensure_project_directories
from .dataset import canonical_stem


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _command_version(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except Exception as error:  # 环境检查不能因单个工具异常而整体退出。
        return str(error)


def run_doctor(config: AppConfig) -> list[CheckResult]:
    ensure_project_directories(config)
    checks: list[CheckResult] = []

    for tool in ("git", "uv", "python", "nvidia-smi"):
        path = shutil.which(tool)
        checks.append(CheckResult(tool, path is not None, path or "未找到"))

    if shutil.which("nvidia-smi"):
        gpu = _command_version(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ]
        )
        checks.append(CheckResult("GPU", "RTX 4060" in gpu or "8188 MiB" in gpu, gpu))

    total_wav = 0
    total_srt = 0
    total_unpaired = 0
    missing_sources = 0
    sources_ok = True
    for source in config.dataset.source_dirs:
        if not source.is_dir():
            missing_sources += 1
            sources_ok = False
            continue
        from .resource_vault import inventory
        entries = inventory(source)
        wav_files = [Path(row["path"]) for row in entries if Path(row["path"]).suffix.lower() == ".wav"] if entries else list(source.glob("*.wav"))
        srt_files = [Path(row["path"]) for row in entries if Path(row["path"]).suffix.lower() == ".srt"] if entries else list(source.glob("*.srt"))
        wav_keys = {canonical_stem(path) for path in wav_files}
        srt_keys = {canonical_stem(path) for path in srt_files}
        total_wav += len(wav_files)
        total_srt += len(srt_files)
        total_unpaired += len(wav_keys ^ srt_keys)
        sources_ok = sources_ok and bool(wav_files) and wav_keys == srt_keys
    checks.append(
        CheckResult(
            "雷姆素材",
            sources_ok,
            (
                f"来源={len(config.dataset.source_dirs)}, WAV={total_wav}, "
                f"SRT={total_srt}, 未配对={total_unpaired}, 缺目录={missing_sources}"
            ),
        )
    )

    component_checks = (
        ("FFmpeg", config.ffmpeg),
        ("GPT-SoVITS 源码", config.gpt_sovits_dir / "webui.py"),
        ("GPT-SoVITS Python", config.gpt_python),
        ("Qwen3.5-4B", config.qwen_model),
        ("llama-server", config.llama_server),
        ("训练清单", config.dataset_manifest),
        ("TTS 语言检测模型", config.fast_langdetect_model),
    )
    for name, path in component_checks:
        checks.append(CheckResult(name, path.exists(), str(path)))

    free_gb = shutil.disk_usage(config.root).free / (1024**3)
    checks.append(CheckResult("磁盘空间", free_gb >= 25, f"剩余 {free_gb:.1f} GB"))
    return checks


def format_doctor_results(results: list[CheckResult]) -> str:
    lines = []
    for result in results:
        # CLI 可能直接运行在 Windows GBK 控制台，使用 ASCII 避免编码异常。
        marker = "[OK]" if result.ok else "[--]"
        lines.append(f"{marker} {result.name}: {result.detail}")
    return "\n".join(lines)
