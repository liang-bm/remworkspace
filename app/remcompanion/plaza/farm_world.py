"""Source-pixel foot navigation; legacy plot IDs survive lawn expansion."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .farm_layout import (MAP_WIDTH, MAP_HEIGHT, NAV_STEP, PLOTS, DIRECTIONS,
                         BY_ID, ENTRY_GATE, WATER_BANK, WELL_BANK, walkable,
                         LEGACY_PLOTS, plot_at)

WIDTH, HEIGHT = MAP_WIDTH, MAP_HEIGHT


def viewport_geometry(width, height, position, *, overview=False):
    width, height = max(1, width), max(1, height)
    scale = (min if overview else max)(width / WIDTH, height / HEIGHT)
    if overview:
        return scale, ((width - WIDTH * scale) / 2, (height - HEIGHT * scale) / 2)
    center = (position[0] * scale, position[1] * scale)
    return scale, (min(0, max(width - WIDTH * scale, width / 2 - center[0])),
                   min(0, max(height - HEIGHT * scale, height / 2 - center[1])))


@dataclass
class FarmWorld:
    x: int = 208
    y: int = 256
    facing: str = "down"
    tilled: set[int] = field(default_factory=set)
    gate_open: bool = False
    source_version: int = field(default=3, repr=False)
    relocated: bool = field(default=False, repr=False)

    def _can_step(self, start, direction):
        dx, dy = DIRECTIONS[direction]
        # Test the swept sole halfway too: a thin fence cannot be skipped.
        return all(walkable(start[0] + dx * distance, start[1] + dy * distance,
                            self.gate_open) for distance in (NAV_STEP // 2, NAV_STEP))

    def move(self, direction):
        if direction not in DIRECTIONS:
            raise ValueError("未知移动方向")
        self.facing = direction
        if not self._can_step((self.x, self.y), direction):
            return False
        dx, dy = DIRECTIONS[direction]
        self.x += dx * NAV_STEP
        self.y += dy * NAV_STEP
        return True

    def target_plot(self):
        dx, dy = DIRECTIONS[self.facing]
        target = (self.x + dx * 24, self.y + dy * 24)
        return plot_at(target)

    def path_to(self, target):
        if not walkable(*target, self.gate_open) or any(n % NAV_STEP for n in target):
            return []
        start = (self.x, self.y)
        queue, previous = deque([start]), {start: None}
        while queue:
            point = queue.popleft()
            if point == target:
                route = []
                while point != start:
                    prior, direction = previous[point]
                    route.append(direction)
                    point = prior
                return route[::-1]
            for direction, (dx, dy) in DIRECTIONS.items():
                nxt = (point[0] + dx * NAV_STEP, point[1] + dy * NAV_STEP)
                if nxt not in previous and self._can_step(point, direction):
                    previous[nxt] = (point, direction)
                    queue.append(nxt)
        return []

    def _approach_pads(self, pads, *, prefer_order=False):
        choices = []
        for point, facing in pads:
            if not walkable(*point, self.gate_open):
                continue
            route = self.path_to(point)
            if route or point == (self.x, self.y):
                if prefer_order:
                    return route, facing
                choices.append((len(route), route, facing))
        if not choices:
            return None
        _, route, facing = min(choices, key=lambda choice: choice[0])
        return route, facing

    def approach(self, target):
        return self._approach_pads(((target[0] - dx * 24, target[1] - dy * 24), facing)
            for facing, (dx, dy) in DIRECTIONS.items())

    def approach_object(self, object_id):
        # Catalog order can prefer a visible front interaction over a closer
        # pad behind a roof. Only reachable physical pads are considered.
        return self._approach_pads(((pad.point, pad.facing) for pad in BY_ID[object_id].pads),
                                   prefer_order=True)

    def interaction_object(self):
        for item in BY_ID.values():
            if item.interaction and any(pad.point == (self.x, self.y) and
                pad.facing == self.facing for pad in item.pads):
                return item
        return None

    def interaction(self):
        item = self.interaction_object()
        return item.interaction if item else None

    def snapshot(self):
        return {"version": 3, "position": [self.x, self.y], "facing": self.facing,
                "tilled": sorted(self.tilled), "gate_open": self.gate_open}

    @classmethod
    def restore(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("人物存档应为 JSON 对象，原文件已保留")
        if not raw:
            return cls()
        version, position = raw.get("version", 1), raw.get("position")
        if type(version) is not int or version not in {1, 2, 3}:
            raise ValueError("人物存档版本无效，原文件已保留")
        tilled, facing = raw.get("tilled", []), raw.get("facing", "down")
        gate_open = raw.get("gate_open", False) if version in {2, 3} else False
        if (not isinstance(position, list) or len(position) != 2
                or any(type(n) is not int for n in position)
                or not isinstance(facing, str) or facing not in DIRECTIONS
                or type(gate_open) is not bool or not isinstance(tilled, list)
                or any(type(n) is not int or not 0 <= n <
                    (len(PLOTS) if version == 3 else len(LEGACY_PLOTS)) for n in tilled)):
            raise ValueError("人物存档格式错误，原文件已保留")
        if version == 1:
            x, y = position
            if not (0 < x < 15 and 0 < y < 11 and not (1 <= x <= 5 and 1 <= y <= 4)
                    and not (12 <= x <= 14 and 1 <= y <= 3)):
                raise ValueError("旧人物位置无效，原文件已保留")
            position = [x * 32 + 16, (y + 1) * 32]
        elif (not 0 < position[0] < WIDTH or not 0 < position[1] < HEIGHT
              or any(n % NAV_STEP for n in position)):
            raise ValueError("人物位置无效，原文件已保留")
        instance = cls(*position, facing, set(tilled), gate_open, version)
        if not walkable(*position, gate_open):
            # Layout migration is in memory ONLY; reading never rewrites an old save.
            # Pick a connected safe node closest to the prior foot location.
            connected = {(208, 256)}
            queue = deque(connected)
            while queue:
                point = queue.popleft()
                for direction, (dx, dy) in DIRECTIONS.items():
                    nxt = (point[0] + dx * NAV_STEP, point[1] + dy * NAV_STEP)
                    if nxt not in connected and instance._can_step(point, direction):
                        connected.add(nxt)
                        queue.append(nxt)
            instance.x, instance.y = min(connected,
                key=lambda p: ((p[0] - position[0]) ** 2 + (p[1] - position[1]) ** 2, p))
            instance.relocated = True
        return instance
