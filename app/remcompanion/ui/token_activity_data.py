"""Pure selected-session chart data; never charge snapshots or invent usage.

The journey's samples are the authority. Actual response measurements and
counter observation deltas remain separate; dates/models are never inferred
from the session's latest model or from a cumulative snapshot.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math

FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")
UNASSIGNED = "未归属"


def _empty():
    return dict.fromkeys(FIELDS)


def _tokens(value):
    return {key: number for key, number in value.items()
            if key in FIELDS and type(number) is int and number >= 0} if isinstance(value, dict) else {}


def _add(target, tokens):
    for key, number in tokens.items():
        target[key] = (target[key] or 0) + number


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        return ""
    try:
        return value if date.fromisoformat(value).isoformat() == value else ""
    except ValueError:
        return ""


def _stamp(value):
    if type(value) not in {int, float}:
        return None
    try:
        if value <= 0 or not math.isfinite(value):
            return None
        datetime.fromtimestamp(value)  # Reject finite but unrenderable epochs.
        return float(value)
    except (ValueError, OverflowError, OSError):
        return None


def _model(value):
    value = value.strip()[:200] if isinstance(value, str) else ""
    return UNASSIGNED if not value or value.casefold() in {"unknown", "unassigned", "none"} or value == "未上报" else value


def _samples(value):
    """One actual ID is one measurement, even in overlapping native windows."""
    rows, identities = [], {}
    skipped = duplicates = conflicts = 0
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str) or raw["kind"] not in {"actual", "observed", "baseline"}:
            skipped += 1
            continue
        tokens = _tokens(raw.get("tokens"))
        if not tokens:
            skipped += 1
            continue
        identity = raw.get("id")
        identity = identity.strip() if isinstance(identity, str) and len(identity) <= 256 else ""
        row = {"id": identity, "kind": raw["kind"], "tokens": tokens, "date": _day(raw.get("date")),
               "model": _model(raw.get("model")), "timestamp": _stamp(raw.get("timestamp"))}
        if row["kind"] != "actual":
            rows.append(row)
            continue
        if not identity:
            skipped += 1  # No native identity: cannot verify a unique measurement.
            continue
        previous = identities.get(identity)
        if previous is None:
            identities[identity] = row
            rows.append(row)
            continue
        duplicates += 1
        if ((previous["date"] and row["date"] and previous["date"] != row["date"])
                or (previous["model"] != UNASSIGNED and row["model"] != UNASSIGNED and previous["model"] != row["model"])
                or any(key in previous["tokens"] and previous["tokens"][key] != number for key, number in tokens.items())):
            conflicts += 1
            continue  # Preserve first attribution; never synthesize a max-field hybrid.
        previous["tokens"].update(tokens)
        previous["date"] = previous["date"] or row["date"]
        if previous["model"] == UNASSIGNED:
            previous["model"] = row["model"]
        previous["timestamp"] = previous["timestamp"] or row["timestamp"]
    return rows, skipped, duplicates, conflicts


def build_activity(usage, days=30, end_day="", model=""):
    """Aggregate chart-ready local calendar rows from SessionJourney._usage.

    Actual/observed totals are selected-period/model sums of reported fields;
    missing fields stay None (total is not reconstructed from input/output).
    Baseline and session snapshots stay unallocated and outside these filters.
    ``measurements`` counts native actual records, not API requests. Available
    models/dates cover all supplied samples, not just the current chart window.
    """
    if type(days) is not int or not 1 <= days <= 366:
        raise ValueError("统计天数应为 1–366 的整数。")
    if not isinstance(end_day, str) or (end_day and not _day(end_day)):
        raise ValueError("截止日期应为 YYYY-MM-DD。")
    if not isinstance(model, str) or len(model) > 200:
        raise ValueError("模型筛选字段无效。")
    usage = usage if isinstance(usage, dict) else {}
    end = date.fromisoformat(end_day) if end_day else date.today()
    try:
        start = end - timedelta(days=days - 1)
    except OverflowError as error:
        raise ValueError("统计日期范围超出日历边界。") from error
    selected_model = _model(model) if model.strip() else ""
    samples, skipped, duplicates, conflicts = _samples(usage.get("samples"))
    totals = {kind: _empty() for kind in ("actual", "observed", "baseline")}
    daily = [{"date": (start + timedelta(days=index)).isoformat(), "tokens": _empty(),
              "measurements": 0, "observed_tokens": _empty(), "observed_measurements": 0,
              "field_measurements": dict.fromkeys(FIELDS, 0)} for index in range(days)]
    by_day, by_model = {row["date"]: row for row in daily}, {}
    measurements = observed_count = baseline_count = undated = 0
    last_timestamp = None
    for row in samples:
        kind, tokens = row["kind"], row["tokens"]
        if kind == "baseline":
            _add(totals["baseline"], tokens)
            baseline_count += 1
            continue
        if not row["date"]:
            undated += 1
            continue
        if row["date"] not in by_day or (selected_model and row["model"] != selected_model):
            continue
        _add(totals[kind], tokens)
        group = by_day[row["date"]]
        _add(group["tokens" if kind == "actual" else "observed_tokens"], tokens)
        group["measurements" if kind == "actual" else "observed_measurements"] += 1
        if row["timestamp"] is not None:
            last_timestamp = max(last_timestamp or 0, row["timestamp"])
        if kind == "observed":
            observed_count += 1
            continue
        measurements += 1
        for field in tokens:
            group["field_measurements"][field] += 1
        model_group = by_model.setdefault(row["model"], {"model": row["model"], "tokens": _empty(), "measurements": 0})
        _add(model_group["tokens"], tokens)
        model_group["measurements"] += 1
    snapshot = usage.get("cumulative_snapshot")
    snapshot = {**snapshot, "tokens": _tokens(snapshot.get("tokens")), "timestamp": _stamp(snapshot.get("timestamp"))} if isinstance(snapshot, dict) else None
    if snapshot is not None and not snapshot["tokens"]:
        snapshot = None
    notices = ["仅已读取的计量记录；未上报不按 0，计量记录数不等于 API 请求次数。",
               "缓存输入包含在输入 Tokens 中，不重复相加；观测增量不并入实际计量。"]
    if baseline_count or snapshot is not None:
        notices.append("未归属历史与会话累计快照单独保留，不归属日期或模型，也不与图表相加。")
    if undated:
        notices.append(f"{undated} 条计量缺少有效日期，未分配到图表。")
    if skipped or conflicts:
        notices.append(f"忽略 {skipped} 条无效/无标识计量、{conflicts} 条同 ID 冲突记录。")
    excluded = usage.get("legacy_counter_measurements_excluded")
    if type(excluded) is int and excluded > 0:
        notices.append(f"{excluded} 条旧混合累计计量已排除重复统计，原记录保留。")
    origin = usage.get("origin")
    if isinstance(origin, str) and origin.strip():
        notices.append(origin[:1000])
    return {"period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
            "totals": totals, "daily": daily,
            "models": sorted(by_model.values(), key=lambda row: (-(row["tokens"]["total_tokens"] or 0), row["model"])),
            "available_models": sorted({row["model"] for row in samples if row["kind"] == "actual"}),
            "known_dates": sorted({row["date"] for row in samples if row["kind"] == "actual" and row["date"]}),
            "measurements": measurements, "observed_count": observed_count, "baseline_measurements": baseline_count,
            "measured_days": sum(row["measurements"] > 0 for row in daily),
            "known_days": sum(row["tokens"]["total_tokens"] is not None for row in daily),
            "last_timestamp": last_timestamp, "cumulative_snapshot": snapshot,
            "duplicates_excluded": duplicates, "notices": notices}
