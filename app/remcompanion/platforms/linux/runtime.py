"""Linux executable locations; Windows-style config paths are converted explicitly."""
from __future__ import annotations
from pathlib import Path, PureWindowsPath
import os
import shutil
import sys
from ..path_conversion import windows_path_to_wsl

def environment_python(root: Path, name: str) -> Path:
    return root / "runtime" / name / "bin" / "python"

def download_settings(settings: dict) -> dict:
    # Legacy Windows configs may be mounted in WSL. Never forward their archives
    # to a Linux installer. LLM installation has its own native profile pins.
    return {key: value for key, value in settings.items()
            if key not in {"llama_asset_contains", "ffmpeg_asset_contains"}}

def project_path(root: Path, value: object) -> Path:
    text = str(value)
    windows = PureWindowsPath(text)
    if windows.is_absolute():
        return Path(windows_path_to_wsl(text))
    if windows.drive:
        raise ValueError("Linux 配置不接受 F:relative 这种盘符相对路径，请使用绝对路径")
    return root / text.replace("\\", "/")

def engine_executable(root: Path, engine: str, executable: str) -> Path:
    if engine == "llama.cpp":
        from .llama_install import executable_for
        return executable_for(root)
    directory = root / "engines" / engine
    preferred = directory / ("bin" if engine == "ffmpeg" else "") / executable
    return preferred if preferred.is_file() else Path(shutil.which(executable) or preferred)

def desktop_executable(root: Path) -> Path:
    return root / "desktop-app" / "RemChat"

def hook_python(root: Path) -> str:
    candidates = [environment_python(root, name) for name in ("desktop-build", "gpt-sovits", "control-center")]
    if not getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable))
    return next((str(path) for path in candidates if path.is_file()), shutil.which("python3") or shutil.which("python") or "")

def host_facts() -> dict:
    return {"secure_credentials": "requires Linux secret service",
            "audio_players": tuple(program for program in ("ffplay", "aplay", "paplay", "afplay") if shutil.which(program)),
            "display_available": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))}

def wsl_inventory(timeout: float = 20) -> dict:
    return {"available": False, "distributions": [], "error": "当前平台没有 wsl.exe"}
