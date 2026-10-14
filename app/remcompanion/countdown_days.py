"""Local calendar events; never mixed with diary or model configuration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import hashlib
import os
from pathlib import Path
import tempfile
import uuid


@dataclass(frozen=True)
class CountdownEvent:
    key: str
    title: str
    target: date

    @classmethod
    def create(cls, title: str, target: str, *, key: str = "") -> CountdownEvent:
        title = title.strip()
        if not title or len(title) > 120:
            raise ValueError("请输入事项名称（1–120 字）")
        try:
            day = date.fromisoformat(target.strip())
            if day.isoformat() != target.strip():
                raise ValueError
        except (ValueError, AttributeError) as error:
            raise ValueError("日期请输入 YYYY-MM-DD，例如 2026-10-15") from error
        return cls(key or uuid.uuid4().hex, title, day)

    def days(self, today: date | None = None) -> int:
        return (self.target - (today or date.today())).days

    def label(self, today: date | None = None) -> str:
        days = self.days(today)
        if days > 0:
            return f"距离 {self.title} 还有 {days} 天"
        if days == 0:
            return f"{self.title} 就是今天"
        return f"{self.title} 已过 {-days} 天"


class CountdownDaysStore:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "data" / "countdown-days.json"
        self.events: list[CountdownEvent] = []
        self.selected_key = ""
        self._revision = None
        self._read_revision = ""

    def _read(self) -> dict:
        if not self.path.exists():
            self._read_revision = ""
            return {"schema": 1, "events": [], "selected": ""}
        if self.path.stat().st_size > 1024 * 1024:
            raise ValueError("倒计日文件过大，未覆盖原文件")
        try:
            payload = self.path.read_bytes()
            raw = json.loads(payload.decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("events"), list):
                raise ValueError
            seen = set()
            for item in raw["events"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise ValueError
                event = CountdownEvent.create(item["title"], item["date"], key=item["id"])
                if event.key in seen:
                    raise ValueError
                seen.add(event.key)
            if not isinstance(raw.get("selected", ""), str):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
            raise ValueError("倒计日文件格式无效，未覆盖原文件") from error
        self._read_revision = hashlib.sha256(payload).hexdigest()
        return raw

    def load(self) -> list[CountdownEvent]:
        raw = self._read()
        events = [CountdownEvent.create(item["title"], item["date"], key=item["id"]) for item in raw["events"]]
        self.events = events
        self.selected_key = raw.get("selected", "")
        self._revision = self._read_revision
        return list(events)

    def current(self, today: date | None = None) -> CountdownEvent | None:
        selected = next((event for event in self.events if event.key == self.selected_key), None)
        if selected is not None:
            return selected
        day = today or date.today()
        future = [event for event in self.events if event.target >= day]
        return min(future, key=lambda event: event.target) if future else max(
            self.events, key=lambda event: event.target, default=None,
        )

    def replace(self, events: list[CountdownEvent], selected_key: str = "") -> None:
        # Re-read before writing: corrupt/future files are never replaced by
        # an empty fallback. Preserve unknown metadata for compatible versions.
        raw = self._read()
        if self._revision is not None and self._read_revision != self._revision:
            raise ValueError("倒计日在别处已更改，请关闭后重新打开再保存")
        validated = [CountdownEvent.create(event.title, event.target.isoformat(), key=event.key) for event in events]
        if len({event.key for event in validated}) != len(validated):
            raise ValueError("倒计日标识重复，未保存")
        prior = {item["id"]: item for item in raw["events"]}
        raw["events"] = [{**prior.get(event.key, {}), "id": event.key,
                          "title": event.title, "date": event.target.isoformat()} for event in validated]
        raw["selected"] = selected_key if any(event.key == selected_key for event in validated) else ""
        payload = (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".countdown-days-", suffix=".tmp", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.events = validated
        self.selected_key = raw["selected"]
        self._revision = hashlib.sha256(payload).hexdigest()
