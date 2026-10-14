"""Linux external applications; commands are argument arrays, not shell text."""
from __future__ import annotations
from pathlib import Path
import shutil
import shlex
import subprocess

def vscode_executable() -> Path | None:
    command = shutil.which("code")
    return Path(command) if command else None


def open_directory(path: Path, *, launch: bool = True) -> bool:
    if not launch:
        return False
    subprocess.Popen(["xdg-open", str(path)])
    return True

def open_uri(uri: str) -> bool:
    from urllib.parse import urlsplit
    parsed=urlsplit(uri)
    if parsed.scheme not in ('https','codex') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('不支持此应用链接')
    if parsed.scheme=='codex':
        command=shutil.which('xdg-mime')
        if not command:raise OSError('缺少桌面链接处理器；请先安装并注册 Codex 桌面端')
        result=subprocess.run([command,'query','default','x-scheme-handler/codex'],capture_output=True,text=True,timeout=5)
        if result.returncode or not result.stdout.strip():raise OSError('尚未注册 Codex 桌面端；可复制入口链接，安装后打开')
    command=shutil.which('xdg-open')
    if not command:raise OSError('系统未提供 xdg-open')
    subprocess.Popen([command,uri],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return True


def program_argv(command: str, args: list[str]) -> list[str]:
    return [shutil.which(command) or command, *args]


def legacy_hook_command(argv: list[str]) -> str:
    """Export safely quoted shell form for older Claude Code versions."""
    return " ".join(shlex.quote(value) for value in argv)
