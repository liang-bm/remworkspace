"""小花园的时间、浇水与收获规则；无需联网和后台线程。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time

from .farm_layout import BLOCKED_PLOTS, LEGACY_PLOTS, PLOTS


@dataclass(frozen=True)
class Crop:
    name: str
    minutes: int
    color: str


# 这些是向用户公开的游戏规则，不是隐藏的模型调参。
CROPS = {"radish": Crop("萝卜", 5, "#F08593"),
         "carrot": Crop("胡萝卜", 10, "#EDAA55"),
         "flower": Crop("蓝铃花", 15, "#76A9E0"),
         "tomato": Crop("番茄", 12, "#EA6E60"),
         "corn": Crop("玉米", 18, "#E7C666"),
         "pumpkin": Crop("南瓜", 25, "#D68C43"),
         "strawberry": Crop("草莓", 16, "#D96179"),
         "sunflower": Crop("向日葵", 20, "#E4B94E")}


@dataclass
class Garden:
    plots: list[dict[str, object]] = field(default_factory=lambda: [{} for _ in PLOTS])
    harvests: dict[str, int] = field(default_factory=dict)
    source_version: int = field(default=2, repr=False)

    def plant(self, index: int, crop: str, now: float | None = None) -> None:
        if index in BLOCKED_PLOTS:
            raise ValueError("该位置靠近障碍物，不能耕种")
        if not 0 <= index < len(self.plots) or self.plots[index]:
            raise ValueError("请选择一块空地")
        if crop not in CROPS:
            raise ValueError("请选择已提供的种子")
        self.plots[index] = {"crop": crop, "planted": time.time() if now is None else now,
                             "watered": False}

    def water(self, index: int) -> None:
        if index in BLOCKED_PLOTS:
            raise ValueError("该位置靠近障碍物，不能耕种")
        if not 0 <= index < len(self.plots) or not self.plots[index]:
            raise ValueError("空地还不需要浇水")
        self.plots[index]["watered"] = True

    def progress(self, index: int, now: float | None = None) -> float:
        plot = self.plots[index]
        if not plot or not plot.get("watered"):
            return 0.0
        duration = CROPS[str(plot["crop"])].minutes * 60
        elapsed = (time.time() if now is None else now) - float(plot["planted"])
        return min(1.0, max(0.0, elapsed / duration))

    def harvest(self, index: int, now: float | None = None) -> str:
        if index in BLOCKED_PLOTS:
            raise ValueError("该位置靠近障碍物，不能耕种")
        if self.progress(index, now) < 1:
            raise ValueError("还没有成熟，先浇水再耐心等一会儿")
        crop = str(self.plots[index]["crop"])
        self.harvests[crop] = self.harvests.get(crop, 0) + 1
        self.plots[index] = {}
        return CROPS[crop].name

    def snapshot(self) -> dict[str, object]:
        return {"version": 2, "plots": self.plots, "harvests": self.harvests}

    @classmethod
    def restore(cls, raw: dict[str, object]) -> Garden:
        if not isinstance(raw, dict):
            raise ValueError("花园存档应为 JSON 对象，原文件已保留")
        if not raw:
            return cls()
        plots = raw.get("plots")
        harvests = raw.get("harvests", {})
        version = raw.get("version", 1)
        if (type(version) is not int or version not in {1, 2}
                or not isinstance(plots, list)
                or len(plots) != (len(LEGACY_PLOTS) if version == 1 else len(PLOTS))
                or not isinstance(harvests, dict)):
            raise ValueError("花园存档格式错误，原文件已保留")
        cleaned = []
        for plot in plots:
            if not isinstance(plot, dict):
                raise ValueError("花园土地格式错误，原文件已保留")
            if plot:
                if plot.get("crop") not in CROPS:
                    raise ValueError("花园存在未知种子，原文件已保留")
                try:
                    timestamp = float(plot["planted"])
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError("播种时间格式错误，原文件已保留") from exc
                if not math.isfinite(timestamp) or timestamp < 0:
                    raise ValueError("播种时间不是有效时间戳，原文件已保留")
                if type(plot.get("watered", False)) is not bool:
                    raise ValueError("浇水状态应为布尔值，原文件已保留")
                cleaned.append({"crop": plot["crop"], "planted": timestamp,
                                "watered": plot.get("watered", False)})
            else:
                cleaned.append({})
        if any(crop not in CROPS or type(count) is not int or count < 0
               for crop, count in harvests.items()):
            raise ValueError("收获统计存在未知作物或无效数量，原文件已保留")
        counts = dict(harvests)
        cleaned.extend({} for _ in range(len(PLOTS) - len(cleaned)))
        return cls(cleaned, counts, version)
