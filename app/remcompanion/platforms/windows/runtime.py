"""Windows executable locations and read-only host probes."""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import sys

def environment_python(root: Path, name: str) -> Path:
    return root / "runtime" / name / "Scripts" / "python.exe"

def download_settings(settings: dict) -> dict:
    # Platform assets are intentionally not part of the shared default template.
    return {"llama_asset_contains": "bin-win-cuda-12.4-x64.zip",
            "ffmpeg_asset_contains": "ffmpeg-master-latest-win64-gpl-shared.zip",
            **settings}

def project_path(root: Path, value: object) -> Path:
    return root / str(value)

def engine_executable(root: Path, engine: str, executable: str) -> Path:
    directory = root / "engines" / engine
    preferred = directory / ("bin" if engine == "ffmpeg" else "") / (executable + ".exe")
    if preferred.exists():
        return preferred
    matches = sorted(directory.glob("**/" + executable + ".exe"))
    return matches[0] if matches else preferred

def desktop_executable(root: Path) -> Path:
    directory = root / "desktop-app"
    candidate = directory / "RemChat.exe"
    return candidate if candidate.is_file() else directory / "RemCompanion.exe"

def hook_python(root: Path) -> str:
    candidates = [environment_python(root, name) for name in ("desktop-build", "gpt-sovits", "control-center")]
    if not getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable))
    return next((str(path) for path in candidates if path.is_file()), shutil.which("python") or "")

def host_facts() -> dict:
    return {"secure_credentials": "Windows DPAPI",
            "audio_players": tuple(program for program in ("ffplay", "aplay", "paplay", "afplay") if shutil.which(program)),
            "display_available": True}

def decode_wsl_output(raw: bytes) -> str:
    """wsl.exe 的版本/清单通常为 UTF-16LE，而 Linux 子命令通常为 UTF-8。"""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace").strip()
    if b"\x00" in raw:
        return raw.decode("utf-16-le", errors="replace").strip()
    return raw.decode("utf-8", errors="replace").strip()



def wsl_inventory(timeout: float = 20) -> dict:
    """Windows 主机上的只读 WSL 探测；权限/缺失环境作为结果明确返回。"""
    executable = shutil.which("wsl.exe")
    if executable is None:
        return {"available": False, "distributions": [], "error": "当前平台没有 wsl.exe"}
    result: dict[str, Any] = {"available": True, "distributions": []}
    for key, arguments in (("version", ["--version"]), ("distributions", ["--list", "--quiet"])):
        try:
            process = subprocess.run([executable, *arguments], capture_output=True,
                                     timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output = decode_wsl_output(process.stdout or process.stderr)
            if process.returncode:
                result[key + "_error"] = output or f"exit code {process.returncode}"
            elif key == "distributions":
                result[key] = [line.strip() for line in output.splitlines() if line.strip()]
            else:
                result[key] = output
        except (OSError, subprocess.TimeoutExpired) as exc:
            result[key + "_error"] = str(exc)
    return result
