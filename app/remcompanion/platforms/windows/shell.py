"""Windows external application entry points (never implicit batch execution)."""
from __future__ import annotations
import os
from pathlib import Path
import base64
import shutil
import subprocess

def vscode_executable() -> Path | None:
    """优先定位 Code.exe，避免通过 code.cmd 启动时闪出命令行窗口。"""

    code_command = shutil.which("code")
    candidates: list[Path] = []
    if code_command:
        command_path = Path(code_command)
        # Windows 的 VS Code bin 目录同时包含 extensionless 的 ``code``
        # shell 脚本和 ``code.cmd``。shutil.which("code") 可能先返回前者；
        # 它是文本脚本，直接交给 CreateProcess 会触发 WinError 193。
        if (
            command_path.parent.name.lower() == "bin"
            or command_path.suffix.lower() in {"", ".cmd", ".bat"}
        ):
            candidates.append(command_path.parent.parent / "Code.exe")
        elif command_path.suffix.lower() == ".exe":
            candidates.append(command_path)
    code_cmd = shutil.which("code.cmd")
    if code_cmd:
        command_path = Path(code_cmd)
        candidates.append(command_path.parent.parent / "Code.exe")
    for environment_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(environment_name)
        if not base:
            continue
        candidates.extend(
            (
                Path(base) / "Programs" / "Microsoft VS Code" / "Code.exe",
                Path(base) / "Microsoft VS Code" / "Code.exe",
            )
        )
    return next(
        (
            path
            for path in candidates
            if path.is_file() and path.suffix.lower() == ".exe"
        ),
        None,
    )



def open_directory(path: Path, *, launch: bool = True) -> bool:
    if not launch:return False
    os.startfile(str(path))
    return True

def open_uri(uri: str) -> bool:
    """Only reviewed HTTPS / Codex links, never arbitrary shell commands."""
    from urllib.parse import urlsplit
    parsed=urlsplit(uri)
    if parsed.scheme not in ('https','codex') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('不支持此应用链接')
    if parsed.scheme=='codex':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,'codex') as key:
                winreg.QueryValueEx(key,'URL Protocol')
        except OSError:raise OSError('尚未安装或注册 Codex 桌面端；可以复制入口链接，安装后打开') from None
    os.startfile(uri);return True


def program_argv(command: str, args: list[str]) -> list[str]:
    executable = shutil.which(command) or command
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat"} or (path.stem.lower() == "codex" and path.suffix.lower() in {"", ".ps1"}):
        stem = path.stem.lower()
        cli_name = {"npx": "npx-cli.js", "npm": "npm-cli.js"}.get(stem)
        cli_path = path.parent / "node_modules" / "npm" / "bin" / str(cli_name)
        if stem == "codex":
            cli_path = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            cli_name = "codex.js"
        node = str(path.parent / "node.exe") if (path.parent / "node.exe").is_file() else shutil.which("node")
        if cli_name and cli_path.is_file() and node:
            return [node, str(cli_path), *args]
        raise ValueError("请使用 .exe 可执行程序，或填写 node.exe / python.exe 与脚本参数；不自动启动批处理 shell。")
    return [str(executable), *args]


def legacy_hook_command(argv: list[str]) -> str:
    """Export one stable command for old Windows Claude hook launchers.

    Some older launchers use cmd even when their UI uses Git Bash. Shell-form
    quoting cannot be shared with POSIX. An encoded, literal-only PowerShell
    invocation protects Unicode, spaces, percent signs and dollar/backtick
    characters from either outer shell. Nothing is executed during export.
    """
    values = ["'" + value.replace("'", "''") + "'" for value in argv]
    script = "& " + " ".join(values) + "\nexit $LASTEXITCODE"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded
