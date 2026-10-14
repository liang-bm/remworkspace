"""Bounded, read-only selected-session evidence; no Tk, model or attachment.

The adapters own record parsing. This module selects one session, projects its
public journey, and reads that session's existing ledger. Missing dates remain
undated; cumulative counters never become actual daily usage.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from .recorder import _redact
from .project_instructions import discover_project_instructions
from ..platforms.path_conversion import workspace_path

TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")
STATE = {"pending": "pending", "running": "running", "started": "running", "in_progress": "running",
         "done": "done", "completed": "done", "failed": "failed", "cancelled": "cancelled"}
EVENTS = {"request", "plan", "step", "goal", "goal_update", "checkpoint", "memory", "report", "turn_end"}


def public(value, limit=4000):
    return _redact(value)[:limit] if isinstance(value, str) else ""


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None  # Do not guess a host's timezone.
            value = parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    if type(value) not in {int, float}:
        return None
    return float(value) if math.isfinite(value) and 0 < value <= time.time() + 300 else None


def local_day(value):
    stamp = timestamp(value)
    return datetime.fromtimestamp(stamp).date().isoformat() if stamp is not None else ""


def _sql_stamp(value):
    # SQLite CURRENT_TIMESTAMP is explicitly UTC, unlike arbitrary host text.
    return timestamp(value.replace(" ", "T") + "Z") if isinstance(value, str) and len(value) == 19 else timestamp(value)


def _rows(value, maximum):
    return [row for row in value[-maximum:] if isinstance(row, dict)] if isinstance(value, list) else []


def _tokens(value):
    return {key: number for key, number in value.items() if key in TOKEN_FIELDS and type(number) is int and number >= 0} if isinstance(value, dict) else {}


def _sum_tokens(rows):
    result = dict.fromkeys(TOKEN_FIELDS)
    for row in rows:
        for key, value in row["tokens"].items():
            result[key] = (result[key] or 0) + value
    return result


def tool_counts(rows, day=""):
    """Count native call IDs once, not started/completed lifecycle rows twice."""
    calls = {}
    for row in _rows(rows, 200):
        identity = public(row.get("call_id") or row.get("id"), 200)
        name = public(row.get("name"), 200)
        if not identity or not name:
            continue
        stamp = timestamp(row.get("timestamp") or row.get("finished_at") or row.get("started_at"))
        item = {"id": identity, "name": name, "category": row.get("category") if row.get("category") in {"command", "mcp", "skill", "other"} else "other",
                "state": row.get("state") if row.get("state") in {"started", "completed", "failed", "cancelled"} else "unknown",
                "timestamp": stamp, "evidence_kind": public(row.get("evidence_kind"), 80), "summary": public(row.get("summary"), 1000)}
        old = calls.get(identity)
        if old is None or item["state"] != "started" or old["state"] == "started":
            calls[identity] = item
    visible = [row for row in calls.values() if not day or not local_day(row["timestamp"]) or local_day(row["timestamp"]) == day]
    groups = Counter((row["category"], row["name"]) for row in visible)
    return {"count": sum(row["evidence_kind"] != "skill_file_read" for row in visible) if calls else None,
            "reference_count": sum(row["evidence_kind"] == "skill_file_read" for row in visible), "undated": sum(row["timestamp"] is None for row in visible),
            "rows": sorted(({"category": category, "name": name, "count": count} for (category, name), count in groups.items()), key=lambda row: (-row["count"], row["name"])),
            "calls": visible, "coverage": "调用按公开原生 ID 去重；Skill 的参考读取另列，不代表执行脚本或证明遵循。wrapper 内部 MCP 未展开；单 Skill Token/费用没有可归因计量。"}


def project_journey(raw, *, day=""):
    """Pure allowlisted view; dates never come from file mtime or last_activity."""
    if day:
        date.fromisoformat(day)
    requests = [{"id": public(row.get("id"), 200), "text": public(row.get("text"), 12000),
                 "timestamp": timestamp(row.get("timestamp")), "source": public(row.get("source"), 200)}
                for row in _rows(raw.get("requests"), 100) if public(row.get("text"), 12000).strip()]
    plan = [{"id": public(row.get("id"), 200), "title": public(row.get("title"), 200),
             "state": STATE.get(row.get("state"), "unknown"), "timestamp": timestamp(row.get("timestamp") or row.get("updated_at")),
             "outcome": public(row.get("outcome")), "remaining": public(row.get("remaining")),
             "depends_on": [public(item, 200) for item in row.get("depends_on", [])[:20] if isinstance(item, str)] if isinstance(row.get("depends_on"), list) else []}
            for row in _rows(raw.get("plan", raw.get("steps")), 60)]
    timeline, seen = [], set()
    for row in _rows(raw.get("timeline"), 200):
        if row.get("kind") not in EVENTS:
            continue
        identity = (row["kind"], public(row.get("id"), 200))
        if identity[1] and identity in seen:
            continue
        seen.add(identity)
        timeline.append({"id": identity[1], "kind": row["kind"], "title": public(row.get("title") or row.get("text") or row.get("summary"), 12000),
                         "body": public(row.get("body"), 12000),
                         "state": STATE.get(row.get("state"), "unknown"), "timestamp": timestamp(row.get("timestamp")),
                         "source": public(row.get("source"), 200), "time_basis": public(row.get("time_basis"), 80),
                         "outcome": public(row.get("outcome")), "remaining": public(row.get("remaining"))})
    if not timeline:
        timeline = [{**row, "kind": "request", "title": row["text"], "state": "unknown"} for row in requests]
        timeline.extend({**row, "kind": "step", "time_basis": "plan_reported"} for row in plan)
    # A timestamp-sort is truthful only when all events have actual timestamps.
    if timeline and all(row["timestamp"] is not None for row in timeline):
        timeline.sort(key=lambda row: row["timestamp"])
    visible = lambda row: not day or not local_day(row.get("timestamp")) or local_day(row.get("timestamp")) == day
    goal = raw.get("active_goal") or raw.get("goal")
    goal = public((goal.get("objective") or goal.get("title")) if isinstance(goal, dict) else goal, 12000)
    # A normal user message is not /goal. Preserve its full text in the prompt
    # archive instead of inventing a native objective or its completion state.
    goal_basis = "宿主/用户明确上报目标" if goal else "所选记录未提供原生目标；普通对话不作为 /goal"
    goal_source = "来源：宿主 / 用户上报" if goal else "所选记录未提供原生目标"
    if not goal:
        explicit = next((row["text"][5:].strip() for row in reversed(requests)
                         if len(row["text"]) > 5 and row["text"].startswith("/goal") and row["text"][5].isspace()), "")
        if explicit:
            goal, goal_basis = explicit, "公开用户 /goal 指令；目标状态未上报"
            goal_source = "来源：公开 /goal · 状态未知"
    memory = raw.get("latest_memory") or raw.get("memory")
    memory = public(memory.get("summary") if isinstance(memory, dict) else memory)
    checkpoints = [{"summary": public(row.get("summary")), "timestamp": timestamp(row.get("timestamp"))}
                   for row in _rows(raw.get("checkpoints"), 10)]
    return {"goal": goal, "goal_basis": goal_basis, "goal_source": goal_source, "memory": memory, "checkpoints": checkpoints,
            "plan": plan, "timeline": [row for row in timeline if visible(row)], "requests": [row for row in requests if visible(row)],
            "undated_events": sum(row["timestamp"] is None for row in timeline),
            "known_dates": sorted({local_day(row["timestamp"]) for row in timeline if row["timestamp"] is not None}, reverse=True),
            "tools": tool_counts(raw.get("tools", raw.get("tool_events")), day),
            "timeline_basis": "宿主公开事件时间；计划状态的时间仅为上报时间" if not raw.get("trace_receive_time") else "RemChat 接收/手工记录时间，不冒充宿主执行时间",
            "coverage": public(raw.get("message"), 2000) or "有界公开证据；未读取的历史与缺失内容不补造。"}


@contextmanager
def _readonly(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("本地数据库不存在；不会创建空数据库。")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    deadline = time.monotonic() + 2
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
    try:
        yield connection
    finally:
        connection.close()


class SessionJourney:
    """One UI worker projects shared native evidence; it never owns sync life."""
    def __init__(self, manager, memory=None):
        self.manager, self.memory = manager, memory
        self._sources, self._choices = {}, {}
        self._watch_owner = "journey-" + uuid.uuid4().hex
        self._selected_tool = None
        self.closed = False

    def tools(self):
        return [{"id": "remchat", "name": "RemChat", "supported": self.memory is not None},
                {"id": "project_trace", "name": "已上报项目", "supported": True}, *self.manager.tool_catalog()]

    def sessions(self, tool_id):
        if self.closed:
            raise RuntimeError("会话浏览已关闭。")
        if tool_id != self._selected_tool:
            self.manager.unwatch_tool_sessions(self._watch_owner)
            self._selected_tool = tool_id
        result = []
        if tool_id == "remchat":
            if self.memory is None:
                return []
            with _readonly(self.memory.path) as connection:
                for row in connection.execute("SELECT id,substr(title,1,200) AS title,created_at FROM conversations ORDER BY updated_at DESC,id DESC"):
                    key = "remchat:" + str(row["id"])
                    result.append({"key": key, "id": str(row["id"]), "tool_id": tool_id, "name": public(row["title"], 200) or "未命名对话",
                                   "created_at": _sql_stamp(row["created_at"]), "source": "remchat_memory", "kind": "remchat"})
        elif tool_id == "project_trace":
            raw = self.manager.project_trace.snapshot()
            if raw.get("session_id"):
                result.append({"key": "trace:" + raw["session_id"], "id": raw["session_id"], "tool_id": tool_id,
                               "name": "当前已上报项目 · " + public(raw.get("workspace"), 120), "source": "project_trace", "kind": "trace"})
        else:
            catalog = next((row for row in self.tools() if row["id"] == tool_id), None)
            if not catalog or not catalog.get("supported"):
                raise ValueError(catalog.get("description", "此工具还没有公开会话接口。") if catalog else "未知工具。")
            connected = [row for row in self.manager.list_tool_connections() if row.get("tool_id") == tool_id and row.get("state") == "connected"]
            for connection in connected:
                for row in self.manager.cached_tool_sessions(connection["id"]):
                    result.append({**row, "key": connection["id"] + ":" + row["id"], "tool_id": tool_id, "kind": "connected", "connection_id": connection["id"]})
            existing = {row["id"] for row in result}
            for row in self.manager.list_tasks():
                # The native Codex catalog is the sidebar authority. A stale
                # previously tracked guardian/child cannot re-enter via saved
                # fallback when it was intentionally absent from that catalog.
                if tool_id == "codex":
                    continue
                if row.get("tool_id") == tool_id and row.get("external_session_id") and row["external_session_id"] not in existing:
                    result.append({"key": "saved:" + row["id"], "id": row["external_session_id"], "tool_id": tool_id,
                                   "name": public(row.get("name"), 200), "source": "saved_last_sync", "kind": "saved", "task_id": row["id"]})
        # Replace this tool's catalog as a whole. A fixed LRU size would let
        # visible older conversations lose their snapshot lookup keys.
        self._choices = {key: row for key, row in self._choices.items() if row.get("tool_id") != tool_id}
        for row in result:
            self._choices[row["key"]] = dict(row)
        return result

    def _remchat(self, identifier):
        with _readonly(self.memory.path) as connection:
            rows = connection.execute("SELECT id,substr(content,1,12000) AS content,created_at FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 100", (int(identifier),)).fetchall()
            requests = [{"id": str(row["id"]), "text": public(row["content"], 12000), "timestamp": _sql_stamp(row["created_at"]), "source": "RemChat 保存的用户消息"} for row in reversed(rows)]
            summary = connection.execute("SELECT substr(content,1,4000) AS content,created_at FROM summaries WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (int(identifier),)).fetchone()
            memories = connection.execute("SELECT substr(m.content,1,1000) AS content FROM memories m JOIN messages u ON u.id=m.source_message_id WHERE u.conversation_id=? ORDER BY m.id DESC LIMIT 8", (int(identifier),)).fetchall()
        return {"requests": requests, "memory": "\n".join(public(row["content"], 1000) for row in memories),
                "checkpoints": [{"summary": public(summary["content"]), "timestamp": _sql_stamp(summary["created_at"])}] if summary else [],
                "message": "仅读取所选 RemChat 会话最近100条用户消息、最多8条关联记忆和一个已保存摘要；不读取助手正文。记忆保存不等于模型当前全部保留。"}

    def _usage(self, choice, raw):
        samples, origin = [], "所选记录的原生计量"
        legacy_excluded, cumulative_snapshot = 0, None
        ledger = getattr(self.manager, "usage_ledger", None)
        if ledger is not None:
            try:
                with _readonly(ledger.path) as connection:
                    for row in connection.execute("SELECT source,scope,reason,usage_id,usage_time,observed_at,day,kind,tokens,model FROM usage_samples WHERE tool_id=? AND session_id=? AND kind IN ('actual','observed','baseline') ORDER BY rowid DESC LIMIT 256", (choice["tool_id"], choice["id"])):
                        if choice["tool_id"] == "codex" and row["source"] in {"local_session_record", "codex_turn_counter"} and row["scope"] == "cumulative":
                            legacy_excluded += 1
                            continue
                        if row["source"] in {"codex_thread_counter", "codex_turn_counter", "codex_legacy_counter"} and row["scope"] == "cumulative" and row["kind"] == "baseline":
                            continue
                        tokens = _tokens(json.loads(row["tokens"]))
                        if tokens:
                            samples.append({"id": row["usage_id"], "tokens": tokens, "kind": row["kind"], "date": row["day"] or "", "timestamp": timestamp(row["usage_time"]), "model": public(row["model"], 200)})
                    counter = connection.execute("SELECT source,highwater,observed_at FROM usage_streams WHERE tool_id=? AND session_id=? AND source IN ('codex_thread_counter','codex_legacy_counter') AND mode='cumulative' ORDER BY observed_at DESC LIMIT 1", (choice["tool_id"], choice["id"])).fetchone()
                    if counter and _tokens(json.loads(counter["highwater"])):
                        cumulative_snapshot = {"tokens": _tokens(json.loads(counter["highwater"])), "timestamp": timestamp(counter["observed_at"]),
                                               "source": counter["source"], "time_basis": "本机观察时间", "scope": "session"}
                if samples:
                    origin = "既有本地账本中归属所选会话的最近256项；actual/observed/baseline不混加"
            except (OSError, ValueError, sqlite3.Error):
                origin = "既有账本不可读；仅所选记录原生计量（不会记账或重建数据库）"
        had_ledger = bool(samples)
        if raw.get("usage_counter_scope") == "session" and _tokens(raw.get("usage")):
            cumulative_snapshot = {"tokens": _tokens(raw["usage"]), "timestamp": timestamp(raw.get("usage_time")),
                                   "source": public(raw.get("usage_source"), 80), "time_basis": "宿主计量时间", "scope": "session"}
        if raw.get("usage_events") or _tokens(raw.get("usage")) or raw.get("trace_receive_time"):
            measurements = _rows(raw.get("usage_events"), 256)
            if raw.get("trace_receive_time"):
                # ProjectTrace stores explicit usage reports separately. Its
                # timestamp is when RemChat accepted the report, not host time.
                measurements = [{"usage": _tokens(row), "usage_scope": row.get("scope"),
                                 "usage_id": hashlib.sha256(json.dumps([public(row.get("source"), 80), public(row.get("model"), 200), public(row.get("usage_id") or row.get("id"), 160)]).encode()).hexdigest(),
                                 "usage_time": row.get("timestamp") if row.get("scope") == "turn" else None,
                                 "usage_model": row.get("model")}
                                for row in [*_rows(raw.get("usage_turns"), 200), *_rows(raw.get("usage_cumulative"), 100)][-256:]]
                origin = "明确上报的项目 turn/cumulative 计量；turn 按 RemChat 接收日期展示，不冒充宿主执行日期"
            if _tokens(raw.get("usage")):
                measurements = [*measurements, raw]
            seen = {row["id"] for row in samples if row["kind"] == "actual"}
            for row in measurements:
                identity = public(row.get("usage_id") or (row.get("usage", {}).get("update_id") if isinstance(row.get("usage"), dict) else ""), 200)
                tokens = _tokens(row.get("usage"))
                if not tokens or not identity or identity in seen:
                    continue
                seen.add(identity)
                stamp = timestamp(row.get("usage_time"))
                scope = row.get("usage_scope", raw.get("usage_scope", raw.get("usage", {}).get("scope") if isinstance(raw.get("usage"), dict) else ""))
                actual = scope == "turn" and stamp is not None
                if not actual and row.get("usage_counter_scope") in {"session", "turn"}:
                    if row.get("usage_counter_scope") == "session":
                        cumulative_snapshot = {"tokens": tokens, "timestamp": stamp, "source": public(row.get("usage_source"), 80),
                                               "time_basis": "宿主计量时间", "scope": "session"}
                    continue  # A counter snapshot must not become another baseline charge.
                if not actual and choice["tool_id"] == "codex" and scope in {"session", "cumulative"}:
                    legacy_excluded += 1
                    continue  # Old saved projections have no trustworthy counter domain.
                if had_ledger and not actual:
                    continue  # Do not add another cumulative baseline to an existing ledger.
                samples.append({"id": identity, "tokens": tokens, "kind": "actual" if actual else "baseline",
                                "date": local_day(stamp) if actual else "", "timestamp": stamp,
                                "model": public(row.get("usage_model") or row.get("model"), 200)})
            if had_ledger and measurements:
                origin += "；按原生response ID去重合并所选日志的实际计量"
        totals = {kind: _sum_tokens([row for row in samples if row["kind"] == kind]) for kind in ("actual", "observed", "baseline")}
        daily = {}
        for row in samples:
            if row["kind"] == "actual" and row["date"]:
                group = daily.setdefault(row["date"], {"date": row["date"], "tokens": dict.fromkeys(TOKEN_FIELDS), "measurements": 0})
                group["measurements"] += 1
                for key, number in row["tokens"].items():
                    group["tokens"][key] = (group["tokens"][key] or 0) + number
        return {"totals": totals, "daily": sorted(daily.values(), key=lambda row: row["date"]), "samples": samples,
                "cumulative_snapshot": cumulative_snapshot, "legacy_counter_measurements_excluded": legacy_excluded,
                "origin": origin, "coverage": "近期有界/非完整历史；unknown不当0；会话累计快照不相加、不归于单模型/日/Skill；旧混合counter记录保留但排除统计；费用/配额未知。"}

    def snapshot(self, key, day=""):
        if self.closed or key not in self._choices:
            raise ValueError("请先发现并选择一个会话。")
        choice = self._choices[key]
        if choice["kind"] == "remchat":
            raw = self._remchat(choice["id"])
        elif choice["kind"] == "trace":
            raw = self.manager.project_trace.snapshot()
            if raw.get("session_id") != choice["id"]:
                raise ValueError("观察会话已改变，请重新选择。")
            raw = dict(raw, trace_receive_time=True)
        elif choice["kind"] == "local":
            raw = self._sources[choice["tool_id"]].snapshot(choice["id"])
        elif choice["kind"] == "connected":
            self.manager.watch_tool_session(choice["connection_id"], choice["id"], self._watch_owner)
            raw = self.manager.tool_session_snapshot(choice["connection_id"], choice["id"])
        else:
            task = next((row for row in self.manager.list_tasks() if row["id"] == choice["task_id"]), None)
            if task is None:
                raise ValueError("保存的会话记录已移除。")
            raw = dict(task.get("session_detail", {}), message="仅上次同步的公开记录；不是当前运行状态。")
        result = project_journey(raw, day=day)
        # Catalog choices carry redacted display paths, not filesystem
        # identities. Missing/invalid native cwd must stay unavailable, never
        # become a truncated or display-derived directory to scan.
        workspace = workspace_path(raw.get("workspace"))
        result.update(session=dict(choice), state=public(raw.get("state"), 32) or "unknown", model=public(raw.get("model"), 200), usage=self._usage(choice, raw), day=day,
                      working_directory=workspace,
                      project_instructions=discover_project_instructions(workspace, choice["tool_id"]),
                      subagents=[{key: public(row.get(key), 200) for key in ("id", "name", "model", "session_kind", "state")}
                                 for row in _rows(raw.get("subagents"), 128)],
                      child_count=raw.get("child_count", 0),
                      metadata_only=raw.get("metadata_only") is True,
                      source_message=public(raw.get("message"), 1000),
                      read_at=time.time(), trace_enabled=bool(self.manager.project_trace.snapshot().get("enabled")))
        if choice.get("connection_id"):
            result["native_sync"] = self.manager.native_sync_status(choice["tool_id"])
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.manager.unwatch_tool_sessions(self._watch_owner)
        for source in self._sources.values():
            source.close()
        self._sources.clear()
        self._choices.clear()
