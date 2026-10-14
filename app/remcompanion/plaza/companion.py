"""主动陪伴的纯调度规则，UI 通过 Tk.after 触发，默认关闭。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import time


@dataclass
class CompanionSettings:
    enabled: bool = False
    interval_minutes: int = 45
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"

    @staticmethod
    def minutes(value: str) -> int:
        try:
            hour, minute = (int(piece) for piece in value.split(":"))
        except (ValueError, TypeError) as exc:
            raise ValueError("安静时段请使用 HH:MM，例如 22:00") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("安静时段的小时需为 00–23，分钟需为 00–59")
        return hour * 60 + minute

    def validate(self) -> None:
        if not 5 <= self.interval_minutes <= 240:
            raise ValueError("互动间隔可设置为 5–240 分钟")
        self.minutes(self.quiet_start)
        self.minutes(self.quiet_end)

    def is_quiet(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        start, end = self.minutes(self.quiet_start), self.minutes(self.quiet_end)
        current = now.hour * 60 + now.minute
        if start == end:
            return False
        return start <= current < end if start < end else current >= start or current < end

    def snapshot(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def restore(cls, raw: dict[str, object]) -> CompanionSettings:
        settings = cls(enabled=bool(raw.get("enabled", False)),
                       interval_minutes=int(raw.get("interval_minutes", 45)),
                       quiet_start=str(raw.get("quiet_start", "22:00")),
                       quiet_end=str(raw.get("quiet_end", "08:00")))
        settings.validate()
        return settings


class CompanionSchedule:
    """间隔从最近互动算起，安静时段不堆积提醒。"""

    def __init__(self, settings: CompanionSettings) -> None:
        self.settings = settings
        self.last_activity = time.monotonic()

    def record_activity(self, now: float | None = None) -> None:
        self.last_activity = time.monotonic() if now is None else now

    def due(self, now: float | None = None, clock: datetime | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not self.settings.enabled:
            return False
        if self.settings.is_quiet(clock):
            self.last_activity = now
            return False
        return now - self.last_activity >= self.settings.interval_minutes * 60
