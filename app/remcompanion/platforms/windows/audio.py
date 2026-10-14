"""Windows synchronous WAV playback; the shared layer owns temporary-file deletion."""

from pathlib import Path
import winsound


class AudioDriver:
    def __init__(self, log, sound_provider) -> None:
        self.log = log
        self._sound_provider = sound_provider

    def prepare(self, path: Path):
        return path

    def play(self, prepared, is_current, publish_process) -> None:
        # Do not use SND_ASYNC: returning means playback has actually completed.
        sound = self._sound_provider()
        sound.PlaySound(str(prepared), sound.SND_FILENAME | sound.SND_NODEFAULT)

    def stop(self, process) -> None:
        sound = self._sound_provider()
        try:
            sound.PlaySound(None, sound.SND_PURGE)
        except RuntimeError:
            pass
