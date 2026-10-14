"""关闭本项目后端：PID、可执行文件、进程出生时间同时匹配才允许终止。

旧版纯 PID 文件不能证明身份。缺少 ownership 记录或无法读取真实身份时，
保留进程，不按进程名扫描、不猜测、不因 Linux venv 共用 Python 而误杀。
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import os
import re
import sys
import tempfile
import time

from .platforms import native_module


_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_IDENTITY = re.compile(r"^[0-9a-f]{64}$")


def _valid_pid(pid: int) -> bool:
    return type(pid) is int and pid > 0 and pid != os.getpid()


def _executable(path: Path, *, strict: bool = False) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=strict)))


def _fingerprint(pid: int, executable: str, birth: int, *, boot: str = "") -> str:
    value = json.dumps({"platform": sys.platform, "pid": pid, "executable": executable,
                        "birth": birth, "boot": boot}, sort_keys=True, separators=(",", ":"))
    return sha256(value.encode("utf-8")).hexdigest()


def process_identity(pid: int, expected_executable: Path) -> str | None:
    """Read actual executable and native birth identity; never send a signal."""
    if not _valid_pid(pid):
        return None
    return native_module("backend_processes").process_identity(pid, expected_executable)


def stop_owned_windows_process(pid: int, expected_executable: Path, *, expected_identity: str | None = None) -> bool:
    """Compatibility API: never invoke the Windows backend on another platform."""
    backend = native_module("backend_processes")
    stop = getattr(backend, "stop_owned_windows_process", None)
    return bool(stop and stop(pid, expected_executable, expected_identity=expected_identity))


def stop_owned_process(pid: int, expected_executable: Path, *, expected_identity: str | None = None) -> bool:
    """Require a birth record and delegate only to the selected native backend."""
    if not _valid_pid(pid) or not isinstance(expected_identity, str) or not _IDENTITY.fullmatch(expected_identity):
        return False
    return native_module("backend_processes").stop_owned_process(
        pid, expected_executable, expected_identity=expected_identity)


def _owner_path(root: Path, label: str) -> Path:
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise ValueError("后端 label 请使用小写字母、数字、连字符或下划线（例如 tts-api）")
    root = Path(root).resolve()
    directory = root / "data" / "backend-ownership"
    try:
        directory.resolve().relative_to(root)
    except ValueError as error:
        raise ValueError("后端身份目录不能通过链接指向项目之外") from error
    return directory / f"{label}.json"


def record_process_identity(root: Path, label: str, pid: int, expected_executable: Path) -> str | None:
    """仅对调用方自己启动/确认的进程记录身份；不覆盖旧 PID 文本格式。"""
    path = _owner_path(root, label)
    identity = process_identity(pid, expected_executable)
    if identity is None:
        return None
    record = {"version": 1, "pid": pid, "executable": _executable(expected_executable),
              "identity": identity, "recorded_at": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{label}-",
                                         suffix=".tmp", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        return identity
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_process_identity(root: Path, label: str, pid: int, expected_executable: Path) -> str | None:
    """只读匹配 PID/exe 的版本化 birth 记录；旧文件、损坏文件均安全 None。"""
    try:
        path = _owner_path(root, label)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
            return None
        if type(value.get("pid")) is not int or value["pid"] != pid or value.get("executable") != _executable(expected_executable):
            return None
        identity = value.get("identity")
        return identity if isinstance(identity, str) and _IDENTITY.fullmatch(identity) else None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
