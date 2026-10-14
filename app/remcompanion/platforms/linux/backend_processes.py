"""Linux /proc birth identity and pidfd-bound backend termination."""

from pathlib import Path
import os
import re
import select
import signal
from ...backend_processes import _executable, _fingerprint, _valid_pid, _IDENTITY
from .pidfd import open_pidfd, send_signal


def _linux_identity(pid: int, expected_executable: Path) -> str | None:
    try:
        directory = Path("/proc") / str(pid)
        actual = _executable(directory / "exe", strict=True)
        expected = _executable(expected_executable, strict=True)
        if actual != expected:
            return None
        # comm 可包含空格与括号；最后一个 ')' 才是 field 2 的结束。
        fields = (directory / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        birth = int(fields[19])  # /proc/pid/stat field 22: starttime ticks
        # starttime 从开机开始计数；boot_id 防止重启后旧 PID + ticks 碰巧重复。
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f-]{36}", boot):
            return None
        return _fingerprint(pid, actual, birth, boot=boot) if birth >= 0 else None
    except (OSError, RuntimeError, ValueError, IndexError):
        return None


def process_identity(pid: int, expected_executable: Path) -> str | None:
    return _linux_identity(pid, expected_executable) if _valid_pid(pid) else None


def stop_owned_process(pid: int, expected_executable: Path, *, expected_identity: str | None = None) -> bool:
    if not _valid_pid(pid) or not isinstance(expected_identity, str) or not _IDENTITY.fullmatch(expected_identity):
        return False
    try:
        descriptor = open_pidfd(pid)
    except OSError:
        return False
    try:
        identity = _linux_identity(pid, expected_executable)
        if identity is None or identity != expected_identity:
            return False
        try:
            send_signal(descriptor, signal.SIGTERM)
        except OSError:
            return False
        # 返回 True 必须代表已退出，而不只是“信号已发送”。超时升级也只
        # 使用同一个 pidfd，不再按数字 PID 寻找可能复用后的别的进程。
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        if poller.poll(2000):
            return True
        try:
            send_signal(descriptor, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        return bool(poller.poll(2000))
    finally:
        os.close(descriptor)
