"""Own only installer children launched by this application; never scan processes."""
from __future__ import annotations

import subprocess
import threading
from .processes import ProcessScope


class DownloadProcesses:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.RLock()
        self._scopes: set[ProcessScope] = set()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise RuntimeError("安装已取消；已下载的数据保留，可在下次启动后继续。")

    def run(self, argv: list[str], *, env: dict, log=print) -> str:
        scope = ProcessScope()
        # Registration and launch share the same lock with close(). No late child
        # may appear between taking the shutdown snapshot and closing the UI.
        with self._lock:
            self.check()
            self._scopes.add(scope)
            try:
                process = scope.launch(argv, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace")
            except BaseException:
                self._scopes.discard(scope)
                raise
        output: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self.check()
                output.append(line)
                log(line.rstrip())
            self.check()
            code = process.wait()
            if code:
                raise RuntimeError(f"Linux 安装命令失败，退出码 {code}。请查看上方日志。")
            return "".join(output)
        except BaseException:
            # A retained, unreaped group leader protects against PID reuse.
            if process.returncode is None:
                scope.close(graceful=False)
            raise
        finally:
            if process.stdout:
                process.stdout.close()
            with self._lock:
                self._scopes.discard(scope)

    def close(self) -> None:
        with self._lock:
            self.cancelled.set()
            scopes = tuple(self._scopes)
        for scope in scopes:
            scope.close(graceful=False)


ACTIVE_DOWNLOADS = DownloadProcesses()
