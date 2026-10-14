"""Read-only farm calendar: one Shanghai civil day is one game month."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
EPOCH_DATE = date(2026, 10, 5)
SEASONS = ("spring", "summer", "autumn", "winter")
SEASON_LABELS = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}
PERIOD_LABELS = {"dawn": "黎明", "day": "白天", "dusk": "傍晚", "night": "夜间"}
# Explicit scene-design times, independent of any crop's real growth duration.
DAWN_HOUR, DAY_HOUR, DUSK_HOUR, NIGHT_HOUR = 5, 7, 17, 20


def calendar_state(now=None, *, epoch_date=EPOCH_DATE) -> dict:
    """Return display state only; naive datetimes mean Shanghai wall time.

    Aware datetimes are converted to Asia/Shanghai. A date means local noon.
    Pre-epoch dates stay at year 1/spring/month 1, avoiding negative game years.
    """
    if now is None:
        now = datetime.now(SHANGHAI)
    elif isinstance(now, datetime):
        now = now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)
    elif isinstance(now, date):
        now = datetime(now.year, now.month, now.day, 12, tzinfo=SHANGHAI)
    else:
        raise TypeError("now应为date或datetime")
    if isinstance(epoch_date, str):
        epoch_date = date.fromisoformat(epoch_date)
    if isinstance(epoch_date, datetime):
        epoch_date = epoch_date.date()
    if not isinstance(epoch_date, date):
        raise TypeError("epoch_date应为date或ISO日期")
    elapsed = max(0, (now.date() - epoch_date).days)
    season = SEASONS[(elapsed % 12) // 3]
    month, year = elapsed % 3 + 1, elapsed // 12 + 1
    hour = now.hour
    period = ("dawn" if DAWN_HOUR <= hour < DAY_HOUR else
              "day" if DAY_HOUR <= hour < DUSK_HOUR else
              "dusk" if DUSK_HOUR <= hour < NIGHT_HOUR else "night")
    return {"season": season, "season_label": SEASON_LABELS[season],
            "month": month, "year": year, "period": period,
            "period_label": PERIOD_LABELS[period], "elapsed_days": elapsed,
            "local_date": now.date().isoformat(),
            "label": f"第{year}年 · {SEASON_LABELS[season]}{month}月 · {PERIOD_LABELS[period]}"}
