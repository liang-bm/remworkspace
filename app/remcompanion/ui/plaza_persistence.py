"""Plaza state commits: publish a changed copy only after its save succeeds.

Kept separate from game drawing so IO failures and recovery have one policy.
The mixin uses PlazaPanel's existing status, timers and redraw methods.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json

from ..plaza.companion import CompanionSettings
from ..plaza.garden import Garden
from ..plaza.gomoku import GomokuGame
from ..plaza.words import WordDeck


ACTIVITIES = {"gomoku": ("game", GomokuGame), "garden": ("garden", Garden), "words": ("words", WordDeck)}


class PlazaPersistenceMixin:
    def _load(self, name: str, restore: Callable, default: Callable) -> object:
        try:
            if name == "garden":
                path = self.store.path / "garden.json"
                if path.is_symlink():
                    raise ValueError("花园存档是符号链接，已停止写入")
                self._garden_bytes = path.read_bytes() if path.exists() else None
                raw = json.loads(self._garden_bytes.decode("utf-8-sig")) if self._garden_bytes is not None else {}
                if not isinstance(raw, dict):
                    raise ValueError("花园存档应为 JSON 对象，原文件已保留")
                return restore(raw)
            return restore(self.store.load(name))
        except (ValueError, TypeError, OverflowError, OSError) as error:
            self._save_errors[name] = str(error)
            return default()

    def _save(self, name: str, value: dict[str, object]) -> bool:
        if name in self._save_errors:
            self.status.set("当前活动存档读取失败，原文件已保留；修复后点击“重新读取当前存档”。")
            return False
        try:
            if name == "garden":
                path = self.store.path / "garden.json"
                if path.is_symlink():
                    raise OSError("花园存档是符号链接，已停止写入")
                original = path.read_bytes() if path.exists() else None
                if original != self._garden_bytes:
                    raise OSError("花园存档已被其他程序修改；请重新读取当前存档")
                if original is not None and self.garden.source_version < 2:
                    self.store.backup_original("garden", self.garden.source_version, original)
            committed = self.store.save(name, value)
            if name == "garden":
                self._garden_bytes = committed
            return True
        except OSError as error:
            self.status.set(f"无法保存 {name}.json；活动未改变：{error}")
            return False

    def _commit_activity(self, name: str, change: Callable, *, candidate=None) -> tuple[bool, object]:
        """Mutate a copy, save, then replace; the current object is never rolled back."""
        attribute, _kind = ACTIVITIES[name]
        if candidate is None:
            candidate = deepcopy(getattr(self, attribute))
        result = change(candidate)
        if not self._save(name, candidate.snapshot()):
            return False, None
        if name == "garden":
            candidate.source_version = 2
        setattr(self, attribute, candidate)
        return True, result

    def _reload_activity(self) -> None:
        """Explicitly reload only the visible activity; never overwrite its file."""
        name = self._active_page
        kind = CompanionSettings if name == "companion" else ACTIVITIES[name][1]
        try:
            if name == "garden":
                path = self.store.path / "garden.json"
                if path.is_symlink():
                    raise ValueError("花园存档是符号链接，已停止写入")
                original = path.read_bytes() if path.exists() else None
                raw = json.loads(original.decode("utf-8-sig")) if original is not None else {}
                if not isinstance(raw, dict):
                    raise ValueError("花园存档应为 JSON 对象，原文件已保留")
                candidate = kind.restore(raw)
            else:
                candidate = kind.restore(self.store.load(name))
        except (ValueError, TypeError, OverflowError, OSError) as error:
            self._save_errors[name] = str(error)
            self.status.set(f"重读 {name}.json 失败，当前活动未改变：{error}")
            return
        if name != "garden":
            self._save_errors.pop(name, None)
        if name == "gomoku":
            self._cancel_ai()
            self.game = candidate
            self._update_game_status()
            self._draw_board()
            # Resume an unfinished saved white turn with the offline opponent;
            # explicit reload does not spend model tokens even if its box is on.
            if self.game.turn == 2 and not self.game.finished:
                self._request_ai(force_local=True)
        elif name == "garden":
            if not self.pixel_farm.reload_world():
                self.status.set("重读人物存档失败，农场当前状态未改变；请检查 farm-world.json。")
                return
            self._save_errors.pop(name, None)
            self._garden_bytes = original
            self.garden = candidate
            self._draw_garden()
        elif name == "words":
            self.words = candidate
            self._draw_word()
        else:
            self.schedule.settings = candidate
            self.companion_enabled.set(candidate.enabled)
            self.interval.set(str(candidate.interval_minutes))
            self.quiet_start.set(candidate.quiet_start)
            self.quiet_end.set(candidate.quiet_end)
            self._update_companion_status()
        self.record_activity()
        self.status.set("已重新读取当前存档；其他活动与聊天未改变。")
