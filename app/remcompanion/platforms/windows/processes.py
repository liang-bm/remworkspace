"""Windows launch options and Job Object controlled process scope."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import threading
from typing import Any


def hidden_process_options() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def detached_process_options() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}


def background_process_options() -> dict[str, Any]:
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            "startupinfo": startup_info}


def background_python(python: Path) -> Path:
    pythonw = python.with_name("pythonw.exe")
    return pythonw if pythonw.is_file() else python


class ProcessScope:
    """只管理本对象创建的进程，关闭时不按名字枚举或终止用户其他程序。"""

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.process_group_id: int | None = None
        self.job: Any = None
        self.kernel: Any = None
        self.lock = threading.RLock()
        self.closed = False
        self._create_windows_job()

    def _create_windows_job(self) -> None:
        class BasicLimit(ctypes.Structure):
            _fields_ = [("per_process_time", ctypes.c_longlong), ("per_job_time", ctypes.c_longlong), ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("basic", BasicLimit), ("io", IOCounters), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "无法创建接入子进程 Job Object。")
        limit = ExtendedLimit()
        limit.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(job)
            raise OSError(error, "无法设置接入子进程关闭规则。")
        self.job, self.kernel = job, kernel

    def launch(self, argv: list[str], **options: Any) -> subprocess.Popen[str]:
        with self.lock:
            if self.closed:
                raise RuntimeError("子进程范围已关闭。")
            if self.process is not None:
                raise RuntimeError("一个进程范围只能启动一次，避免覆盖已有进程所有权。")
            options.update({"shell": False, **hidden_process_options()})
            try:
                self.process = subprocess.Popen(argv, **options)
                if self.job and not self.kernel.AssignProcessToJobObject(self.job, wintypes.HANDLE(int(self.process._handle))):
                    error = ctypes.get_last_error()
                    if self.process.poll() is None:
                        self.process.kill()
                        self.process.wait(timeout=2)
                        raise OSError(error, "不能将接入程序加入受控进程范围。")
                return self.process
            except Exception:
                self.close(graceful=False)
                raise

    def close(self, *, graceful: bool = True) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            process = self.process
            if process and graceful:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            if self.job:
                self.kernel.CloseHandle(self.job)
                self.job = None
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
