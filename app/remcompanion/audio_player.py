"""播放一次性对话语音，并在播放结束后删除临时文件。"""

from __future__ import annotations

from typing import Callable
from pathlib import Path
import threading
import subprocess

from .platforms import native_module

# Preserve the existing dependency alias; native calls live only in the selected driver.
winsound = getattr(native_module("audio"), "winsound", None)


LogCallback = Callable[[str], None]


def _delete_audio(path: Path, log: LogCallback) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        log(f"[语音清理失败] {path}: {error}")


def cleanup_stale_dialogue_audio(root: Path, log: LogCallback = print) -> int:
    """清理上次异常退出遗留的临时对话音频和旧版对话缓存。"""

    removed = 0
    directories = (
        root / "tmp" / "dialogue-audio",
        root / "data" / "audio-cache" / "dialogue",
    )
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.glob("*.wav"):
            try:
                path.unlink()
            except OSError as error:
                log(f"[语音清理失败] {path}: {error}")
            else:
                removed += 1
    return removed


class EphemeralAudioPlayer:
    """Serial WAV playback; normal finish, stop and failures all remove temporary files."""

    def __init__(self, log: LogCallback = print) -> None:
        self.log = log
        self._lock = threading.Lock()
        self._current: Path | None = None
        self._process: subprocess.Popen | None = None
        self._generation = 0
        self._driver = native_module("audio").AudioDriver(log, lambda: winsound)

    @property
    def is_playing(self) -> bool:
        """Whether this player currently owns a prepared/in-flight WAV."""
        with self._lock:
            return self._current is not None

    def play(self, path: Path) -> None:
        audio_path = path.resolve()
        self.stop()
        with self._lock:
            self._current = audio_path
            generation = self._generation

        prepared = self._driver.prepare(audio_path)
        if prepared is None:
            _delete_audio(audio_path, self.log)
            with self._lock:
                if self._current == audio_path:
                    self._current = None
            return

        def is_current() -> bool:
            with self._lock:
                return generation == self._generation

        def publish_process(process) -> bool:
            with self._lock:
                if generation != self._generation:
                    return False
                self._process = process
                return True

        def worker() -> None:
            try:
                if not is_current():
                    return
                self._driver.play(prepared, is_current, publish_process)
            except (RuntimeError, OSError) as error:
                self.log(f"[语音播放失败] {error}")
            finally:
                _delete_audio(audio_path, self.log)
                with self._lock:
                    if self._current == audio_path:
                        self._current = None
                    if generation == self._generation:
                        self._process = None

        threading.Thread(target=worker, name="rem-audio-playback", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            process = self._process
            self._process = None
            current = self._current
            self._current = None
        self._driver.stop(process)
        if current is not None:
            # A driver holding the file may require the worker's final deletion retry.
            _delete_audio(current, self.log)
