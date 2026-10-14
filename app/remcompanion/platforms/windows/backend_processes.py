"""Windows backend identity and handle-bound process-tree shutdown."""

from pathlib import Path
import time
from ...backend_processes import _executable, _fingerprint, _valid_pid, _IDENTITY


def _windows_kernel():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _windows_handle_identity(kernel, handle, pid: int, expected_executable: Path) -> str | None:
    import ctypes
    from ctypes import wintypes
    size = wintypes.DWORD(32768)
    executable = ctypes.create_unicode_buffer(size.value)
    if not kernel.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(size)):
        return None
    try:
        actual, expected = _executable(Path(executable.value)), _executable(expected_executable)
    except (OSError, RuntimeError):
        return None
    if actual != expected:
        return None
    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
        return None
    birth = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    return _fingerprint(pid, actual, birth) if birth > 0 else None


def _windows_birth(kernel, handle) -> int | None:
    import ctypes
    from ctypes import wintypes
    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
        return None
    value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    return value if value > 0 else None


def _windows_actual_parent(handle, pid: int) -> int | None:
    """从已打开实例查 PPID，不拿可能已复用 PID 的旧快照当最终凭据。

    Windows 原生 ProcessBasicInformation 若不可用/布局不符，保守拒绝此分支。
    返回值只用于与 Toolhelp 的候选关系交叉校验，不按名称匹配进程。
    """
    import ctypes
    from ctypes import wintypes
    class BasicInformation(ctypes.Structure):
        _fields_ = [("exit_status", wintypes.LONG), ("peb", ctypes.c_void_p),
                    ("affinity", ctypes.c_size_t), ("base_priority", wintypes.LONG),
                    ("unique_pid", ctypes.c_size_t), ("inherited_pid", ctypes.c_size_t)]
    try:
        query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
                          wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        query.restype = wintypes.LONG
        value, returned = BasicInformation(), wintypes.ULONG()
        code = query(handle, 0, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(returned))
        if code < 0 or returned.value != ctypes.sizeof(value) or int(value.unique_pid) != pid:
            return None
        return int(value.inherited_pid)
    except (OSError, AttributeError):
        return None


def _windows_parent_snapshot(kernel) -> dict[int, list[int]] | None:
    """只读取进程父子关系；快照里的 exe 名从不作为终止依据。"""
    import ctypes
    from ctypes import wintypes
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("exe", wintypes.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        result: dict[int, list[int]] = {}
        while True:
            result.setdefault(int(entry.parent), []).append(int(entry.pid))
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                # 正常枚举结束是 ERROR_NO_MORE_FILES；其他错误代表快照不完整。
                return result if ctypes.get_last_error() == 18 else None
    finally:
        kernel.CloseHandle(snapshot)


def _windows_shutdown_job(kernel):
    """只接纳经过 birth/ancestry 核验的实例，关闭时回收其后续子进程。"""
    import ctypes
    from ctypes import wintypes
    class BasicLimit(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                    ("processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes")]
    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("basic", BasicLimit), ("io", IOCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    limit = ExtendedLimit()
    limit.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
        kernel.CloseHandle(job)
        return None
    return job


def _windows_terminate_owned_tree(kernel, root_handle, root_pid: int) -> bool:
    """回收已核验固定实例及 Job 覆盖后续子树，不声称恢复绑定前历史闭包。"""
    import ctypes
    from ctypes import wintypes
    root_birth = _windows_birth(kernel, root_handle)
    if root_birth is None or _windows_actual_parent(root_handle, root_pid) is None:
        return False
    job = _windows_shutdown_job(kernel)
    if not job:
        return False
    handles = {root_pid: (root_handle, root_birth)}
    complete = True
    unproven_job_coverage = False
    try:
        # 先绑定根，使之后新建的子进程自动继承此关闭专用 Job。
        if not kernel.AssignProcessToJobObject(job, root_handle):
            return False
        deadline = time.monotonic() + 2
        while True:
            parents = _windows_parent_snapshot(kernel)
            if parents is None:
                complete = False
                break
            added = False
            queue = list(handles)
            for parent_pid in queue:
                for pid in parents.get(parent_pid, []):
                    if pid in handles:
                        continue
                    if not _valid_pid(pid):
                        complete = False
                        continue
                    # QUERY_INFORMATION 给原生 PPID 查询；SET_QUOTA 给 Job 绑定。
                    handle = kernel.OpenProcess(0x1000 | 0x0400 | 0x0100 | 0x0001 | 0x00100000, False, pid)
                    if not handle:
                        complete = False
                        continue
                    birth = _windows_birth(kernel, handle)
                    actual_parent = _windows_actual_parent(handle, pid)
                    if actual_parent != parent_pid or birth is None or birth < handles[parent_pid][1]:
                        kernel.CloseHandle(handle)
                        # PPID 已改变或 birth 更早说明快照候选不是当前父实例后代。
                        if actual_parent is None or birth is None:
                            complete = False
                        continue
                    handles[pid] = (handle, birth)
                    queue.append(pid)
                    added = True
                    in_job = wintypes.BOOL()
                    inherited = kernel.IsProcessInJob(handle, job, ctypes.byref(in_job)) and in_job.value
                    if not inherited and not kernel.AssignProcessToJobObject(job, handle):
                        # uv launcher 的内部 Job 可能拒绝再次绑定。身份已核验的
                        # 固定实例仍可单独结束，但无法证明它的后续fork完整归属。
                        # 即使已知handle全退出、post快照无反例，也不能证明消失的
                        # 中间父进程没有遗留更深后代，因此保守返回False。
                        unproven_job_coverage = True
            if not added:
                break
            if time.monotonic() >= deadline:
                complete = False
                break
        kernel.CloseHandle(job)  # 精确 Job 成员及其后续子进程一起退出。
        job = None
        for pid, (handle, _birth) in reversed(list(handles.items())):
            # CloseHandle(job) 发起的是异步退出。先等 Job 的正常回收完成，
            # 不把“已在退出、再次 Terminate 拒绝”误报为关闭失败。
            if kernel.WaitForSingleObject(handle, 2000) != 0:
                terminated = kernel.TerminateProcess(handle, 0)
                if not terminated and kernel.WaitForSingleObject(handle, 0) != 0:
                    complete = False
            if kernel.WaitForSingleObject(handle, 2000) != 0:
                complete = False
        return complete and not unproven_job_coverage
    finally:
        if job:
            kernel.CloseHandle(job)
        for pid, (handle, _birth) in handles.items():
            if pid != root_pid:
                kernel.CloseHandle(handle)


def process_identity(pid: int, expected_executable: Path) -> str | None:
    if not _valid_pid(pid):
        return None
    kernel = _windows_kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        return _windows_handle_identity(kernel, handle, pid, expected_executable)
    finally:
        kernel.CloseHandle(handle)


def stop_owned_windows_process(pid: int, expected_executable: Path, *, expected_identity: str | None = None) -> bool:
    """固定父实例上核验 FILETIME；只回收有实际父子关系/birth 证据的树。"""
    if not _valid_pid(pid) or not isinstance(expected_identity, str) or not _IDENTITY.fullmatch(expected_identity):
        return False
    kernel = _windows_kernel()
    handle = kernel.OpenProcess(0x1000 | 0x0400 | 0x0100 | 0x0001 | 0x00100000, False, pid)
    if not handle:
        return False
    try:
        identity = _windows_handle_identity(kernel, handle, pid, expected_executable)
        if identity is None or identity != expected_identity:
            return False
        return _windows_terminate_owned_tree(kernel, handle, pid)
    finally:
        kernel.CloseHandle(handle)


def stop_owned_process(pid: int, expected_executable: Path, *, expected_identity: str | None = None) -> bool:
    return stop_owned_windows_process(pid, expected_executable, expected_identity=expected_identity)
