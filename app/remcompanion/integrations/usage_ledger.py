"""Local metadata-only token ledger; no prices, quotas, prompts or model calls.

Native turn IDs identify measurements, not user prompts. Only a real usage_time
allocates a turn to a date. Cumulative counters start as unallocated history;
monotone increases are observation-interval estimates, never actual dated calls.
One native source/session uses one counter mode, preventing turn/counter overlap.
Only explicitly source-scoped native response IDs deduplicate copied sessions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

from ..project_trace.recorder import _redact

FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")
SQL_MAX = (1 << 63) - 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_streams (
    stream TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT NOT NULL,
    epoch TEXT NOT NULL,
    highwater TEXT NOT NULL,
    observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_samples (
    sample TEXT PRIMARY KEY,
    stream TEXT NOT NULL,
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    model TEXT NOT NULL,
    scope TEXT NOT NULL,
    usage_id TEXT NOT NULL,
    usage_time REAL,
    observed_at REAL NOT NULL,
    interval_start REAL,
    day TEXT,
    kind TEXT NOT NULL,
    tokens TEXT NOT NULL,
    request_count INTEGER,
    estimated_time INTEGER NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_counters (
    stream TEXT NOT NULL,
    epoch TEXT NOT NULL,
    model TEXT NOT NULL,
    highwater TEXT NOT NULL,
    observed_at REAL NOT NULL,
    PRIMARY KEY(stream, epoch)
);
CREATE INDEX IF NOT EXISTS usage_samples_period ON usage_samples(day, tool_id, model);
CREATE INDEX IF NOT EXISTS usage_samples_native ON usage_samples(source, usage_id, scope);
"""


def _label(value, limit=200, default=""):
    if not isinstance(value, str):
        return default
    value = _redact(value.strip()).encode("utf-8", errors="replace").decode("utf-8")
    return value[:limit] or default


def _identity(value, label, limit=256):
    if (not isinstance(value, str) or not value or len(value) > limit
            or any(ord(char) < 32 for char in value)):
        raise ValueError(label + " 必须是有界的非空标识，不含控制字符。")
    return _label(value, limit)


def _hash(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _usage(value):
    return {key: value[key] for key in FIELDS if type(value.get(key)) is int and 0 <= value[key] <= SQL_MAX} if isinstance(value, dict) else {}


def _timestamp(value, now):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            value = parsed.timestamp()
        except (ValueError, OSError, OverflowError):
            return None
    try:
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= now + 300:
            return None
    except OverflowError:
        return None
    try:
        datetime.fromtimestamp(value)
    except (ValueError, OSError, OverflowError):
        return None
    return float(value)


class UsageLedger:
    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self.path = self.root / "data/usage/usage.sqlite3"
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError("本地用量账本版本不受支持；未覆盖已有数据。")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=1")

    @contextmanager
    def _connection(self):
        with self.lock:
            connection = None
            try:
                connection = sqlite3.connect(self.path, timeout=5)
                connection.row_factory = sqlite3.Row
                with connection:
                    yield connection
            except sqlite3.Error as error:
                raise ValueError("本地用量账本暂不可用：" + str(error)) from error
            finally:
                if connection is not None:
                    connection.close()

    @staticmethod
    def _insert(connection, sample, stream, source, session_id, tool_id, model, scope, usage_id,
                usage_time, observed_at, kind, tokens, count=None, reason="", interval_start=None):
        day = datetime.fromtimestamp(usage_time if kind == "actual" else observed_at).date().isoformat() if kind in {"actual", "observed"} else None
        return bool(connection.execute("""INSERT OR IGNORE INTO usage_samples
            (sample,stream,source,session_id,tool_id,model,scope,usage_id,usage_time,observed_at,interval_start,day,kind,tokens,request_count,estimated_time,reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sample, stream, source, session_id, tool_id, model, scope, usage_id, usage_time, observed_at,
             interval_start, day, kind, json.dumps(tokens, separators=(",", ":")), count, kind == "observed", reason)).rowcount)

    @staticmethod
    def _native_conflict(old, tokens, model):
        previous = json.loads(old["tokens"])
        return ((old["model"] != "unknown" and model != "unknown" and old["model"] != model)
                or any(key in previous and previous[key] != number for key, number in tokens.items()))

    def _native_turn(self, connection, stream, source, session_id, tool_id, model,
                     usage_id, usage_time, now, tokens):
        """One trusted native response across copied sessions, first attribution.

        Existing session-keyed rows remain evidence. On the first explicit
        source-scoped observation they are reconciled without deleting rows or
        rewriting their original token/model/time metadata.
        """
        rows = connection.execute("""SELECT * FROM usage_samples
            WHERE source=? AND usage_id=? AND scope='turn' AND kind IN ('actual','baseline')
            ORDER BY rowid""", (source, usage_id)).fetchall()
        if not rows:
            return self._insert(connection, _hash(source, "native-turn", usage_id), stream,
                source, session_id, tool_id, model, "turn", usage_id, usage_time, now,
                "actual" if usage_time is not None else "baseline", tokens, 1,
                "" if usage_time is not None else "missing_usage_time_unallocated")
        old, changed = rows[0], False
        for duplicate in rows[1:]:
            reason = "native_response_conflict" if self._native_conflict(old, json.loads(duplicate["tokens"]), duplicate["model"]) else "native_response_duplicate"
            connection.execute("""UPDATE usage_samples SET kind='ignored',day=NULL,
                request_count=NULL,estimated_time=0,reason=? WHERE sample=?""", (reason, duplicate["sample"]))
            changed = True
        conflict = self._native_conflict(old, tokens, model)
        if not conflict:
            merged = json.loads(old["tokens"])
            merged.update(tokens)
            stamp = old["usage_time"] if old["usage_time"] is not None else usage_time
            reported_model = model if old["model"] == "unknown" else old["model"]
            if merged != json.loads(old["tokens"]) or stamp != old["usage_time"] or reported_model != old["model"]:
                kind = "actual" if stamp is not None else "baseline"
                day = datetime.fromtimestamp(stamp).date().isoformat() if stamp is not None else None
                connection.execute("""UPDATE usage_samples SET tokens=?,usage_time=?,day=?,kind=?,model=?,reason=?
                    WHERE sample=?""", (json.dumps(merged, separators=(",", ":")), stamp, day, kind,
                    reported_model, "" if kind == "actual" else "missing_usage_time_unallocated", old["sample"]))
                changed = True
        if old["stream"] != stream or conflict:
            reason = "native_response_conflict" if conflict else "native_response_duplicate"
            # Already migrated legacy rows are the proof: do not create another
            # ignored row for the identical copy on every poll.
            encoded = json.dumps(tokens, separators=(",", ":"))
            proof = connection.execute("""SELECT 1 FROM usage_samples WHERE stream=? AND source=?
                AND usage_id=? AND scope='turn' AND kind='ignored' AND tokens=? AND model=?
                AND usage_time IS ? AND reason IN ('native_response_duplicate','native_response_conflict') LIMIT 1""",
                (stream, source, usage_id, encoded, model, usage_time)).fetchone()
            if not proof:
                changed = self._insert(connection, _hash(stream, "native-proof", usage_id, tokens, model, usage_time, reason),
                    stream, source, session_id, tool_id, model, "turn", usage_id, usage_time, now,
                    "ignored", tokens, reason=reason) or changed
        return changed

    def record(self, tool_id, session_id, snapshot):
        """Persist one allowlisted measurement; True means new/updated evidence."""
        tool_id = _identity(tool_id, "工具 ID", 80)
        session_id = _identity(session_id, "会话 ID")
        if not isinstance(snapshot, dict):
            return False
        tokens = _usage(snapshot.get("usage"))
        if not tokens:
            return False
        native_scope = snapshot.get("usage_scope", "")
        scope = "cumulative" if native_scope in {"session", "cumulative"} else "turn" if native_scope == "turn" else ""
        if not scope:
            return False
        source = _label(snapshot.get("source"), default=tool_id)
        model = _label(snapshot.get("usage_model") if "usage_model" in snapshot else snapshot.get("model"), default="unknown")
        raw_id = snapshot.get("usage_id")
        usage_id = _label(raw_id, 160) if isinstance(raw_id, str) and len(raw_id) <= 160 and not any(ord(char) < 32 for char in raw_id) else ""
        # A session-wide counter survives turn changes; only an explicitly
        # reported counter epoch can override the legacy run-based behavior.
        epoch = _label(snapshot.get("usage_counter_epoch"), 256) if snapshot.get("usage_counter_scope") in {"session", "turn"} else _label(snapshot.get("run_id"), 128)
        now = time.time()
        usage_time = _timestamp(snapshot.get("usage_time"), now)
        stream = _hash(source, session_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM usage_streams WHERE stream=?", (stream,)).fetchone()
            if previous and previous["mode"] != scope:
                return self._insert(connection, _hash(stream, "mixed", scope, usage_id, tokens, usage_time), stream,
                    source, session_id, tool_id, model, scope, usage_id, usage_time, now, "ignored", tokens,
                    reason="mixed_scope_not_added")
            if not previous:
                connection.execute("INSERT INTO usage_streams VALUES (?,?,?,?,?,?,?,?,?)",
                    (stream, source, session_id, tool_id, scope, model, epoch, "{}", now))
            if scope == "turn":
                if not usage_id:
                    return self._insert(connection, _hash(stream, "no-id", tokens, model, usage_time), stream,
                        source, session_id, tool_id, model, scope, "", usage_time, now, "unavailable", tokens,
                        reason="missing_native_usage_id_not_counted")
                if snapshot.get("usage_id_scope") == "source":
                    return self._native_turn(connection, stream, source, session_id, tool_id, model,
                        usage_id, usage_time, now, tokens)
                sample = _hash(stream, "turn", usage_id)
                old = connection.execute("SELECT * FROM usage_samples WHERE sample=?", (sample,)).fetchone()
                kind = "actual" if usage_time is not None else "baseline"
                if old:
                    if old["kind"] == "ignored" and old["reason"] in {"native_response_duplicate", "native_response_conflict"}:
                        return False  # A legacy unflagged poll cannot recharge a reconciled copy.
                    # The same native ID is one measurement even if a later
                    # final block supplies more fields or a real timestamp.
                    merged = json.loads(old["tokens"])
                    if not any(key in merged and number < merged[key] for key, number in tokens.items()):
                        merged.update(tokens)  # Never synthesize a max-per-field hybrid of contradictory replies.
                    stamp = old["usage_time"] if old["usage_time"] is not None else usage_time
                    kind = "actual" if stamp is not None else "baseline"
                    reported_model = model if old["model"] == "unknown" and model != "unknown" else old["model"]
                    if merged == json.loads(old["tokens"]) and stamp == old["usage_time"] and reported_model == old["model"]:
                        return False
                    day = datetime.fromtimestamp(stamp).date().isoformat() if stamp is not None else None
                    connection.execute("UPDATE usage_samples SET tokens=?,usage_time=?,day=?,kind=?,model=?,reason=? WHERE sample=?",
                        (json.dumps(merged, separators=(",", ":")), stamp, day, kind, reported_model,
                         "" if kind == "actual" else "missing_usage_time_unallocated", sample))
                    return True
                return self._insert(connection, sample, stream, source, session_id, tool_id, model, scope,
                    usage_id, usage_time, now, kind, tokens, 1,
                    "" if kind == "actual" else "missing_usage_time_unallocated")
            epoch = epoch or (previous["epoch"] if previous else "")
            counter = connection.execute("SELECT * FROM usage_counters WHERE stream=? AND epoch=?", (stream, epoch)).fetchone()
            # Persist high-water marks per actual run ID. Revisiting an old
            # epoch is not a fresh reset and cannot charge its tokens twice.
            # A first reported ID can clarify an earlier unknown epoch, so
            # carry its existing high-water mark rather than repeat history.
            basis = counter or (previous if previous and not previous["epoch"] and epoch else None)
            highwater = json.loads(basis["highwater"]) if basis else {}
            if any(key in highwater and number < highwater[key] for key, number in tokens.items()):
                return self._insert(connection, _hash(stream, "regression", epoch, tokens), stream,
                    source, session_id, tool_id, model, scope, usage_id, usage_time, now, "ignored", tokens,
                    reason="counter_regression_ignored")
            fresh = {key: number for key, number in tokens.items() if key not in highwater}
            delta = {key: number - highwater[key] for key, number in tokens.items() if key in highwater}
            transition = bool(basis and basis["model"] != model)
            changed = False
            if fresh:
                changed = self._insert(connection, _hash(stream, "baseline", epoch, sorted(fresh)), stream,
                    source, session_id, tool_id, model, scope, usage_id, usage_time, now, "baseline", fresh,
                    reason="first_counter_or_field_unallocated")
            if any(delta.values()):
                # A model change spans an unassignable interval. Preserve that
                # positive amount as history instead of attributing it to the
                # model seen only at the end of the interval.
                changed = self._insert(connection, _hash(stream, "delta", epoch, tokens), stream,
                    source, session_id, tool_id, "unknown" if transition else model, scope, usage_id, usage_time,
                    now, "baseline" if transition else "observed", delta,
                    reason="model_transition_unallocated" if transition else "observed_interval_delta",
                    interval_start=basis["observed_at"] if basis else None) or changed
            if not counter or tokens != highwater or transition or (previous and previous["epoch"] != epoch):
                highwater.update(tokens)
                connection.execute("INSERT INTO usage_counters VALUES (?,?,?,?,?) ON CONFLICT(stream,epoch) DO UPDATE SET model=excluded.model,highwater=excluded.highwater,observed_at=excluded.observed_at",
                    (stream, epoch, model, json.dumps(highwater, separators=(",", ":")), now))
                connection.execute("UPDATE usage_streams SET highwater=?,epoch=?,model=?,observed_at=? WHERE stream=?",
                    (json.dumps(highwater, separators=(",", ":")), epoch or (previous["epoch"] if previous else ""), model, now, stream))
            return changed

    def _rows(self, tool_id, model, start, end, include_history=False):
        if not isinstance(tool_id, str) or not isinstance(model, str) or len(tool_id) > 80 or len(model) > 200:
            raise ValueError("用量筛选字段无效。")
        clauses, parameters = [], []
        for field, value in (("tool_id", tool_id), ("model", model)):
            if value:
                clauses.append(field + "=?")
                parameters.append(value)
        clauses.append("((day>=? AND day<=?)" + (" OR day IS NULL)" if include_history else ")"))
        parameters.extend((start.isoformat(), end.isoformat()))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            for native in connection.execute("SELECT * FROM usage_samples" + where, parameters):
                row = dict(native)
                row["tokens"] = json.loads(row["tokens"])
                yield row  # Stream large histories; never materialize raw samples.

    def _choices(self, field, tool_id="", model=""):
        clauses, parameters = [], []
        for column, value in (("tool_id", tool_id), ("model", model)):
            if value:
                clauses.append(column + "=?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            return [row[0] for row in connection.execute("SELECT DISTINCT " + field + " FROM usage_samples" + where + " ORDER BY " + field + " LIMIT 512", parameters)]

    @staticmethod
    def _add(target, tokens):
        for key, value in tokens.items():
            target[key] = (target[key] or 0) + value

    @staticmethod
    def _group(grouped, row):
        group = grouped.setdefault(row["day"], {"actual": dict.fromkeys(FIELDS), "observed": dict.fromkeys(FIELDS),
            "actual_count": 0, "observed_count": 0, "observed_start": None, "observed_end": None})
        UsageLedger._add(group[row["kind"]], row["tokens"])
        group[row["kind"] + "_count"] += 1
        if row["kind"] == "observed":
            start = row["interval_start"]
            if start is not None:
                group["observed_start"] = min(group["observed_start"], start) if group["observed_start"] is not None else start
            group["observed_end"] = max(group["observed_end"] or 0, row["observed_at"])

    @staticmethod
    def _daily(grouped, start, end):
        result, current = [], start
        while current <= end:
            day = current.isoformat()
            group = grouped.get(day, {"actual": dict.fromkeys(FIELDS), "observed": dict.fromkeys(FIELDS),
                "actual_count": 0, "observed_count": 0, "observed_start": None, "observed_end": None})
            actual, observed = group["actual_count"], group["observed_count"]
            actual_tokens, observed_tokens = group["actual"], group["observed"]
            result.append({"date": day, "actual": actual_tokens if actual else None, "observed": observed_tokens if observed else None,
                **actual_tokens, "observed_total_tokens": observed_tokens["total_tokens"],
                "request_count": actual or None, "observed_count": observed,
                "observed_start": group["observed_start"], "observed_end": group["observed_end"],
                "known": bool(actual), "estimated_time": bool(observed),
                "coverage": "mixed" if actual and observed else "reported" if actual else "observed" if observed else "unknown"})
            if current == end:
                break
            current += timedelta(days=1)
        return result

    def summary(self, days=30, tool_id="", model=""):
        if type(days) is not int or not 1 <= days <= 3660:
            raise ValueError("统计天数应为 1–3660 的整数。")
        end = datetime.fromtimestamp(time.time()).date()
        start = end - timedelta(days=days - 1)
        actual_tokens, observed_tokens, baseline_tokens = (dict.fromkeys(FIELDS) for _ in range(3))
        grouped = {}
        actual_count = observed_count = baseline_count = baseline_requests = missing_time = missing_id = mixed_scope = regressions = duplicates = native_conflicts = 0
        baseline_has_requests = False
        legacy_counters = 0
        for row in self._rows(tool_id, model, start, end, include_history=True):
            if self._ambiguous_codex_counter(row):
                legacy_counters += 1
                continue  # Preserve rows on disk, but never sum mixed old counter domains.
            if row["scope"] == "cumulative" and row["source"] in {"codex_thread_counter", "codex_turn_counter", "codex_legacy_counter"} and row["kind"] == "baseline":
                continue  # Counter initial values are snapshots, not additive charges.
            if row["kind"] in {"actual", "observed"}:
                self._group(grouped, row)
                if row["kind"] == "actual":
                    self._add(actual_tokens, row["tokens"])
                    actual_count += 1
                else:
                    self._add(observed_tokens, row["tokens"])
                    observed_count += 1
            elif row["kind"] == "baseline":
                self._add(baseline_tokens, row["tokens"])
                baseline_count += 1
                if row["request_count"] is not None:
                    baseline_has_requests = True
                    baseline_requests += row["request_count"]
                missing_time += row["reason"] == "missing_usage_time_unallocated"
            elif row["kind"] == "unavailable":
                missing_id += 1
            else:
                mixed_scope += row["reason"] == "mixed_scope_not_added"
                regressions += row["reason"] == "counter_regression_ignored"
                duplicates += row["reason"] in {"native_response_duplicate", "native_response_conflict"}
                native_conflicts += row["reason"] == "native_response_conflict"
        return {"period": {"start": start.isoformat(), "end": end.isoformat(), "days": days, "timezone": "system local date at record time"},
            "totals": actual_tokens, "observed_totals": observed_tokens,
            "request_count": actual_count or None, "actual_measurements": actual_count, "observed_count": observed_count,
            "daily": self._daily(grouped, start, end), "models": self._choices("model", tool_id, model), "tools": self._choices("tool_id", tool_id, model),
            "baseline": {"tokens": baseline_tokens, "measurements": baseline_count,
                "request_count": baseline_requests if baseline_has_requests else None,
                "included_in_daily": False, "period": "unallocated history; cannot filter by usage date"},
            "coverage": {"kind": "partial", "actual_measurements": actual_count, "observed_deltas": observed_count,
                "baseline_measurements": baseline_count, "missing_time_measurements": missing_time,
                "missing_id_measurements": missing_id, "mixed_scope_ignored": mixed_scope,
                "regressions_ignored": regressions,
                "duplicates_ignored": duplicates, "native_conflicts_ignored": native_conflicts,
                "legacy_counter_measurements_excluded": legacy_counters,
                "complete": False, "message": "仅本机已观察的计量；未读到的历史、无 ID 计量与未知日期不补造。累计增量按观察区间末日展示，不代表实际发生日。"},
            "cost": {"available": False, "amount": None, "currency": None, "reason": "未提供真实费用，不根据 token 估价。"},
            "quota": {"available": False, "remaining": None, "reason": "配额未上报；配额和 token 消耗不是同一指标。"}}

    def calendar(self, year, tool_id="", model=""):
        if type(year) is not int or not 1 <= year <= 9999:
            raise ValueError("日历年份无效。")
        start, end = date(year, 1, 1), date(year, 12, 31)
        grouped = {}
        for row in self._rows(tool_id, model, start, end):
            if row["kind"] in {"actual", "observed"} and not self._ambiguous_codex_counter(row):
                self._group(grouped, row)
        return self._daily(grouped, start, end)

    @staticmethod
    def _ambiguous_codex_counter(row):
        # Turn-counter observations can overlap the thread counter or atomic
        # responses. Keep them as evidence, never another additive token total.
        return row["tool_id"] == "codex" and row["scope"] == "cumulative" and row["source"] in {"local_session_record", "codex_turn_counter"}

    def close(self):
        """Connections are scoped to operations; no owned persistent resource."""
        return None
