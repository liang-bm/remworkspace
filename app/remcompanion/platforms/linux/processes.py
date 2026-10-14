"""Linux launch options and unreaped-leader private process-group scope."""

from __future__ import annotations

from pathlib import Path
import os
import logging
import signal
import subprocess
import sys
import threading
import time
from typing import Any


def hidden_process_options() -> dict[str, Any]:
    return {"creationflags": 0}


def detached_process_options() -> dict[str, Any]:
    return {"creationflags": 0}


def background_process_options() -> dict[str, Any]:
    return {"creationflags": 0}


def background_python(python: Path) -> Path:
    return python


class ProcessScope:
    """只管理本对象创建的进程，关闭时不按名字枚举或终止用户其他程序。"""

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.process_group_id: int | None = None
        self.job: Any = None
        self.kernel: Any = None
        self.lock = threading.RLock()
        self.closed = False

    def launch(self, argv: list[str], **options: Any) -> subprocess.Popen[str]:
        with self.lock:
            if self.closed:
                raise RuntimeError("子进程范围已关闭。")
            if self.process is not None:
                raise RuntimeError("一个进程范围只能启动一次，避免覆盖已有进程所有权。")
            options.update({"shell": False, "creationflags": 0, "start_new_session": True})
            try:
                self.process = subprocess.Popen(argv, **options)
                # An unreaped leader pins its private PGID; never signal a reused group.
                self.process_group_id = self.process.pid
                return self.process
            except Exception:
                self.close(graceful=False)
                raise

    def _close_posix(self, process: subprocess.Popen[str], *, graceful: bool) -> None:
        """Retain the unreaped leader while signalling its group; otherwise fail closed.

        CPython's poll/wait both reap under _waitpid_lock (checked in 3.8/3.12).
        The application must not separately waitpid or change SIGCHLD sigaction.
        """
        wait_lock = getattr(process, "_waitpid_lock", None)
        if sys.implementation.name != "cpython" or wait_lock is None:
            logging.warning("接入进程组未清理：无法确认 CPython waitpid 锁，可能留下后代；拒绝按可能复用的 PGID 发信号。")
            return
        if not wait_lock.acquire(timeout=0.25):
            logging.warning("接入进程组未清理：waitpid 锁忙，无法确认组身份，可能留下后代。")
            return
        try:
            if process.returncode is not None:
                logging.warning("接入进程组未清理：父进程已被回收，PGID 可能已复用，无法确认组身份，可能留下后代。")
                return
            if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
                logging.warning("接入进程组未清理：SIGCHLD 非默认策略，不能确认父 PID 仍被保留，可能留下后代。")
                return
            group_id = self.process_group_id
            if group_id != process.pid or group_id is None or group_id <= 1 or group_id == os.getpgrp():
                logging.warning("接入进程组未清理：不是本范围创建的独立组，拒绝发送信号。")
                return
            # Never poll()/wait() while holding this lock: even a zombie leader
            # retains its PID and keeps this group number from being recycled.
            if graceful:
                # stdin.close() may wait forever on another thread's buffered
                # write lock. Never touch that stream while owning scope/PID locks.
                time.sleep(1)
            try:
                os.killpg(group_id, signal.SIGTERM)
            except ProcessLookupError:
                pass  # Still reap the known, unreaped leader after releasing its lock.
            deadline = time.monotonic() + 1
            while True:
                try:
                    os.killpg(group_id, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    try:
                        os.killpg(group_id, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    break
                time.sleep(0.03)
        finally:
            wait_lock.release()
        # Group signalling has finished before reaping permits PID reuse.
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            logging.warning("接入进程组清理后父进程未及时退出；不再按数值 PID/PGID 追加信号。")

    def close(self, *, graceful: bool = True) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.process:
                self._close_posix(self.process, graceful=graceful)
