"""Linux WAV playback via existing external player, never through a shell."""

from pathlib import Path
import shutil
import subprocess

# Compatibility dependency alias, not a Windows implementation.
winsound = None


class AudioDriver:
    def __init__(self, log, sound_provider) -> None:
        self.log = log

    def prepare(self, path: Path):
        for program, arguments in (
            ("paplay", []), ("aplay", ["-q"]),
            ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "error"]),
        ):
            executable = shutil.which(program)
            if executable:
                return [executable, *arguments, str(path)]
        self.log("[语音播放失败] 未找到 paplay/aplay/ffplay；已清理临时语音")
        return None

    def play(self, prepared, is_current, publish_process) -> None:
        process = subprocess.Popen(prepared, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True)
        if not publish_process(process):
            process.terminate()
        code = process.wait()
        if code and is_current():
            self.log(f"[语音播放失败] 播放器退出码 {code}")

    def stop(self, process) -> None:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass
