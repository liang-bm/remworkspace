"""从控制中心或 CLI 启动独立桌面伴侣。"""

from __future__ import annotations

from collections.abc import Callable
import subprocess
import sys

from .config import AppConfig
from .process import project_environment


def start_companion_overlay(
    config: AppConfig,
    log: Callable[[str], None] = print,
) -> int:
    """优先启动打包 EXE；开发环境回退到 Python 模块。

    两条路径共用命名互斥锁，重复点击不会出现多套宠物和聊天窗口。
    """

    log_file = config.root / "logs" / "companion-overlay.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    from .platforms import native_module
    options = native_module("processes").detached_process_options()
    desktop_exe = native_module("runtime").desktop_executable(config.root)
    command = (
        [str(desktop_exe)]
        if desktop_exe.is_file()
        else [sys.executable, "-m", "remcompanion.desktop"]
    )
    with log_file.open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command,
            cwd=desktop_exe.parent if desktop_exe.is_file() else config.root,
            env=project_environment(config.root),
            stdout=stream,
            stderr=subprocess.STDOUT,
            **options,
        )
    (config.root / "data" / "companion-overlay.pid").write_text(
        str(process.pid),
        encoding="utf-8",
    )
    mode = desktop_exe.name if desktop_exe.is_file() else "Python 开发入口"
    log(f"[启动] 桌面伴侣 PID={process.pid} · {mode}")
    return process.pid
