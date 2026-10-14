"""统一的子进程执行器，向控制中心实时转发日志。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import os
import subprocess

from .platforms import native_module

LogCallback = Callable[[str], None]


class CommandError(RuntimeError):
    """外部命令返回非零退出码。"""


def project_environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    paths = {
        "REM_HOME": root,
        "HF_HOME": root / "cache" / "huggingface",
        "HUGGINGFACE_HUB_CACHE": root / "cache" / "huggingface" / "hub",
        "TORCH_HOME": root / "cache" / "torch",
        "PIP_CACHE_DIR": root / "cache" / "pip",
        "UV_CACHE_DIR": root / "cache" / "uv",
        "UV_PYTHON_INSTALL_DIR": root / "runtime" / "uv-python",
        "TEMP": root / "tmp",
        "TMP": root / "tmp",
    }
    env.update({key: str(value) for key, value in paths.items()})
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    return env


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    log: LogCallback = print,
    env_updates: Mapping[str, str] | None = None,
) -> None:
    """执行命令并逐行输出；禁止 shell=True，避免路径和台词被再次解释。"""

    command = [str(item) for item in args]
    log("$ " + subprocess.list2cmdline(command))
    env = project_environment(Path(os.environ.get("REM_HOME", cwd)))
    if env_updates:
        env.update({key: str(value) for key, value in env_updates.items()})

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **native_module("processes").hidden_process_options(),
    )
    assert process.stdout is not None
    for line in process.stdout:
        log(line.rstrip())
    code = process.wait()
    if code != 0:
        raise CommandError(f"命令失败，退出码 {code}: {subprocess.list2cmdline(command)}")
