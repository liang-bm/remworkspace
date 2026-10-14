"""Offline practice recipes; never reads or spends garden crops.

Only a finished dish is persisted. Loading is read-only, and an invalid,
externally changed, or linked save stays untouched while practice still works.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import stat
from typing import Any

from .storage import PlazaStore


@dataclass(frozen=True)
class CookingStep:
    action: str
    label: str
    station: str
    instruction: str
    feedback: str


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    ingredient: str
    steps: tuple[CookingStep, ...]


RECIPES = {
    "radish_soup": Recipe("radish_soup", "萝卜汤", "白萝卜", (
        CookingStep("wash", "洗萝卜", "sink", "在水槽洗净白萝卜。", "洗好了，白萝卜干干净净。"),
        CookingStep("cut", "切萝卜", "board", "在砧板把白萝卜切成小块。", "萝卜切成小块啦，可以下锅了。"),
        CookingStep("heat", "煮萝卜汤", "stove", "把萝卜加入练习汤锅，开火煮软。", "汤锅咕嘟咕嘟，萝卜煮软了。"),
        CookingStep("plate", "盛汤装盘", "serve", "把萝卜汤盛入碗中，完成练习。", "热乎乎的萝卜汤做好啦！"),
    )),
    "carrot_stew": Recipe("carrot_stew", "胡萝卜炖菜", "胡萝卜", (
        CookingStep("wash", "洗胡萝卜", "sink", "在水槽洗净胡萝卜。", "胡萝卜洗好了，带到砧板吧。"),
        CookingStep("cut", "切胡萝卜", "board", "在砧板把胡萝卜切成小块。", "切好了，大小均匀的小块更好炖。"),
        CookingStep("heat", "炖胡萝卜", "stove", "把小块胡萝卜放入练习锅，开火炖软。", "胡萝卜炖软了，香气飘出来啦。"),
        CookingStep("plate", "装盘炖菜", "serve", "把胡萝卜炖菜盛到盘里，完成练习。", "胡萝卜炖菜做好啦！"),
    )),
    "flower_tea": Recipe("flower_tea", "花茶", "食用花", (
        CookingStep("wash", "轻洗花朵", "sink", "用清水轻轻洗净练习用食用花。", "花朵洗净了，接着整理花瓣。"),
        CookingStep("prepare", "整理花瓣", "board", "在砧板旁整理花瓣，放进茶壶。", "花瓣整理好了，茶壶准备就绪。"),
        CookingStep("heat", "加热泡茶", "stove", "烧好练习热水，注入茶壶泡开花瓣。", "热水泡开了花瓣，茶香轻轻飘出来。"),
        CookingStep("pour", "倒茶装杯", "serve", "把花茶倒进杯中，完成练习。", "一杯暖暖的花茶做好啦！"),
    )),
}
STATION_NAMES = {"sink": "水槽", "board": "砧板", "stove": "炉灶", "serve": "装盘台"}


@dataclass(frozen=True)
class ActionResult:
    message: str
    action: str
    station: str
    completed: bool = False
    persisted: bool = False


def _identity(info) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _safe_info(path: Path, *, directory: bool = False):
    """lstat does not follow the final link; Windows junctions are links too."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if directory:
            raise OSError("厨房存档目录不存在；请重新打开农场") from None
        return None
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise OSError("厨房存档或目录是链接文件；原文件已保留，停止写入")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("厨房存档目录无效；原文件已保留，停止写入")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError("厨房存档不是独立普通文件；原文件已保留，停止写入")
    return info


class CookingModel:
    """One in-memory practice session and append-only finished-dish records."""
    def __init__(self, store: PlazaStore):
        self.store = store
        self.path = Path(store.path).absolute() / "cooking.json"
        self.recipe_id = "radish_soup"
        self.step_index = 0
        self.completed = False
        self.last_saved = False
        self.error = ""
        self._raw: dict[str, Any] = {}
        self._source_bytes: bytes | None = None
        self._source_identity = None
        self.reload_records()

    @property
    def recipe(self) -> Recipe:
        return RECIPES[self.recipe_id]

    @property
    def current_step(self) -> CookingStep | None:
        return None if self.completed else self.recipe.steps[self.step_index]

    @property
    def records(self) -> list[dict[str, Any]]:
        # Callers cannot alter records or unknown fields before a guarded save.
        return deepcopy(self._raw.get("records", []))

    def counts(self) -> dict[str, int]:
        records = self._raw.get("records", [])
        return {key: sum(record["recipe"] == key for record in records) for key in RECIPES}

    def _read_source(self):
        # Guard the complete parent chain, not only the file's final component.
        for directory in reversed(self.path.parents):
            _safe_info(directory, directory=True)
        before = _safe_info(self.path)
        if before is None:
            return None, None
        content = self.path.read_bytes()
        after = _safe_info(self.path)
        if after is None or _identity(before) != _identity(after):
            raise OSError("厨房存档在读取中被修改；原文件已保留，请重新读取记录")
        return content, _identity(after)

    @staticmethod
    def _validate(raw):
        if not isinstance(raw, dict):
            raise ValueError("厨房存档应为 JSON 对象；原文件已保留")
        if not raw:
            return
        version = raw.get("version", 1)
        if type(version) is not int or version != 1:
            raise ValueError("厨房存档版本不受支持；原文件已保留")
        records = raw.get("records", [])
        if not isinstance(records, list):
            raise ValueError("厨房成品记录格式错误；原文件已保留")
        for record in records:
            if (not isinstance(record, dict) or not isinstance(record.get("recipe"), str)
                    or not record["recipe"] or not isinstance(record.get("completed_at"), str)
                    or ("name" in record and not isinstance(record["name"], str))):
                raise ValueError("厨房成品记录格式错误；原文件已保留")
            try:
                datetime.fromisoformat(record["completed_at"])
            except ValueError:
                raise ValueError("厨房成品时间格式错误；原文件已保留") from None

    def reload_records(self) -> bool:
        """Explicit reload does not change the dish in progress or write a file."""
        try:
            content, identity = self._read_source()
            raw = json.loads(content.decode("utf-8-sig")) if content is not None else {}
            self._validate(raw)
            candidate = deepcopy(raw)
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            self.error = str(error)
            return False
        self._raw = candidate
        self._source_bytes, self._source_identity = content, identity
        self.error = ""
        return True

    def select_recipe(self, recipe_id: str) -> None:
        if recipe_id not in RECIPES:
            raise ValueError("未知练习菜谱")
        self.recipe_id = recipe_id
        self.restart()

    def restart(self) -> None:
        self.step_index = 0
        self.completed = self.last_saved = False

    def _record_completion(self, now: datetime | None = None) -> bool:
        if self.error:
            return False
        try:
            candidate = deepcopy(self._raw)
            candidate.setdefault("version", 1)
            candidate.setdefault("records", []).append({
                "recipe": self.recipe_id, "name": self.recipe.name,
                "completed_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
            })
            content, identity = self._read_source()
            if content != self._source_bytes or identity != self._source_identity:
                raise OSError("厨房存档已被其他程序修改；未覆盖，请重新读取记录")
            committed = self.store.save("cooking", candidate)
            content, identity = self._read_source()
            if content != committed:
                raise OSError("厨房存档在保存后被修改；请重新读取记录")
        except (OSError, ValueError, RecursionError) as error:
            self.error = str(error)
            return False
        self._raw = candidate
        self._source_bytes, self._source_identity = content, identity
        return True

    def perform_action(self, action: str | None = None, *, now: datetime | None = None) -> ActionResult:
        step = self.current_step
        if step is None:
            raise ValueError("这道菜已经完成；点“再做一份”开始新的练习")
        if action is not None and action != step.action:
            raise ValueError(f"先到{STATION_NAMES[step.station]}{step.label}，再继续下一步")
        self.step_index += 1
        self.completed = self.step_index == len(self.recipe.steps)
        if self.completed:
            self.last_saved = self._record_completion(now)
        message = step.feedback
        if self.completed:
            message += " 已记入成品记录。" if self.last_saved else " 本次仅练习，成品未写入记录；原文件已保留。"
        return ActionResult(message, step.action, step.station, self.completed, self.last_saved)
