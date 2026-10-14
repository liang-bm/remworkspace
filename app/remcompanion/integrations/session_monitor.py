"""Explicit, read-only tool connections. Never start/resume a model turn.

Native transports are isolated by OS. A connection is not a task; a session is
not a run. Local archive synchronization lasts for the application's lifetime;
only explicitly selected sessions create monitor tasks. Missing evidence stays
unknown, and an explicit disconnect prevents automatic startup reconnection.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from ..platforms import native_module
from ..platforms.path_conversion import workspace_path
from ..project_trace.recorder import _redact
from .native_sync import NativeSynchronization


def _text(value, limit=200):
    return _redact(value)[:limit] if isinstance(value, str) else ""


def _stamp(value):
    if type(value) not in {int, float}:
        return None
    return float(value) if math.isfinite(value) and 0 < value <= time.time() + 300 else None


def project_workspace_matches(row, trace):
    """Do not attribute another project's relative paths to this project."""
    workspace = row.get("workspace")
    if not workspace:
        return True  # No path evidence; sources without it must not invent files.
    if not isinstance(workspace, str) or trace.workspace is None:
        return False
    def key(path):
        value = str(path)
        if sys.platform == "win32":
            if value[:8].casefold() == "\\\\?\\unc\\":
                value = "\\\\" + value[8:]
            elif value.startswith("\\\\?\\"):
                value = value[4:]
        return os.path.normcase(str(Path(value).resolve()))
    try:
        return key(workspace) == key(trace.workspace)
    except (OSError, ValueError, RuntimeError):
        return False


def public_detail(snapshot):
    """Allowlisted small public data only; no reasoning, raw args or results."""
    result = {key: _text(snapshot.get(key), limit) for key, limit in
              (("model", 200), ("goal", 4000), ("memory", 4000), ("request_summary", 2000))}
    result["last_activity"] = _stamp(snapshot.get("last_activity"))
    for key in ("session_kind", "parent_thread_id", "state_evidence"):
        result[key] = _text(snapshot.get(key), 200)
    result["subagents"] = [{key: _text(row.get(key), 200) for key in ("id", "name", "model", "session_kind", "state")}
                           for row in snapshot.get("subagents", [])[:128] if isinstance(row, dict)] if isinstance(snapshot.get("subagents"), list) else []
    usage = snapshot.get("usage", {})
    result["usage_scope"] = "turn" if snapshot.get("usage_scope", usage.get("scope") if isinstance(usage, dict) else None) == "turn" else "cumulative"
    result["usage_id"] = _text(snapshot.get("usage_id", usage.get("update_id") if isinstance(usage, dict) else None), 160)
    result["usage_counter_scope"] = snapshot.get("usage_counter_scope") if snapshot.get("usage_counter_scope") in {"session", "turn"} else ""
    result["usage_counter_epoch"] = _text(snapshot.get("usage_counter_epoch"), 256)
    result["usage_source"] = _text(snapshot.get("usage_source"), 80)
    result["usage"] = {key: value for key, value in snapshot.get("usage", {}).items()
                       if key in {"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"}
                       and type(value) is int and value >= 0} if isinstance(snapshot.get("usage"), dict) else {}
    for field, keys, limit in (("plan", ("id", "title", "state", "outcome", "remaining"), 60),
                               ("tools", ("id", "name", "state", "category", "summary", "evidence_kind"), 200),
                               ("requests", ("id", "text"), 20),
                               ("files", ("id", "path", "operation"), 30)):
        rows = snapshot.get(field, [])
        result[field] = [{key: _text(row.get(key), 1000 if key in {"text", "summary", "path"} else 200)
                          for key in keys} for row in rows[-limit:] if isinstance(row, dict)] if isinstance(rows, list) else []
        if isinstance(rows, list):
            for public, native in zip(result[field], [row for row in rows[-limit:] if isinstance(row, dict)]):
                for key in ("timestamp", "started_at", "finished_at"):
                    public[key] = _stamp(native.get(key))
    result["checkpoints"] = [{"summary": _text(row.get("summary"), 2000), "timestamp": _stamp(row.get("timestamp"))}
                             for row in snapshot.get("checkpoints", [])[-10:] if isinstance(row, dict)] if isinstance(snapshot.get("checkpoints"), list) else []
    return result


class ToolSessionMonitor:
    def __init__(self, manager):
        self.manager, self.store = manager, manager.store
        self.lock = threading.RLock()
        self.sources = {}
        self.closed = False
        self.trace_seen = {}
        self.usage_seen = {}
        self.connection_epochs = {}
        self.sync = NativeSynchronization(self)

    @staticmethod
    def public_error(error):
        return _text(str(error), 600)

    def start(self):
        self.sync.start()

    def catalog(self):
        if sys.platform != "win32":
            return [{"id": "codex", "name": "Codex", "supported": False,
                     "description": "本轮只实现 Windows 工具会话连接，Linux 请继续使用已有显式事件接入。"}]
        return native_module("tool_sessions").catalog()

    def connections(self):
        names = {row["id"]: row["name"] for row in self.catalog()}
        with self.lock:
            rows = self.store.read()["tool_connections"]
            for row in rows:
                # Old labels combined a tool with its editor. Correct the view
                # without rewriting saved tasks or third-party configuration.
                row["name"] = names.get(row.get("tool_id"), row.get("name", "工具"))
                if row["id"] not in self.sources:
                    row.update(state="disconnected", message=("已手动断开；点击连接工具可重新接入。" if row.get("autoconnect") is False else
                               "本地存档正在后台接入或等待可读位置；不会自动启动宿主。"))
            return rows

    def task_view(self, row):
        """Saved native rows are not a live connection after an app restart."""
        row = deepcopy(row)
        identifier = row.get("connection_id")
        if identifier:
            row["tool_name"] = {"codex": "Codex", "claude_code": "Claude Code", "antigravity": "Antigravity"}.get(row.get("tool_id"), row.get("tool_name", ""))
        with self.lock:
            if identifier and identifier not in self.sources:
                row.update(sync_state="disconnected",
                           observed_until=row.get("observed_until") or row.get("updated_at"),
                           message="当前未连接此工具；保留上次观察，不推测宿主是否仍在运行。")
        return row

    def connect(self, tool_id, endpoint="", *, automatic=False):
        if sys.platform != "win32":
            raise ValueError("此连接实现目前只支持 Windows。")
        entry = next((row for row in self.catalog() if row["id"] == tool_id), None)
        if not entry or not entry.get("supported"):
            raise ValueError(entry.get("description", "此工具尚无可用的会话连接适配器。") if entry else "未知工具")
        if not isinstance(endpoint, str) or len(endpoint) > 1000:
            raise ValueError("连接位置应为最多1000字的字符串。")
        with self.lock:
            if self.closed:
                raise RuntimeError("会话观察已关闭。")
            old = next((row for row in self.store.read()["tool_connections"]
                        if row.get("tool_id") == tool_id and row.get("endpoint", "") == endpoint), None)
            identity = (tool_id, endpoint)
            epoch = self.connection_epochs.get(identity, 0)
            if automatic and old and old.get("autoconnect") is False:
                raise ValueError("已手动断开，后台不会自动重连。")
            if old and old["id"] in self.sources and old.get("state") == "connected":
                return old
            previous = self.sources.pop(old["id"], None) if old else None
        if previous:
            previous.close()
        source = native_module("tool_sessions").create_source(tool_id, endpoint)
        identifier = old["id"] if old else "connection-" + uuid.uuid4().hex[:20]
        row = {"id": identifier, "tool_id": tool_id, "name": entry["name"], "endpoint": endpoint,
               "state": "connected", "source": _text(getattr(source, "source", "native_session")),
               "location": _text(getattr(source, "location", getattr(source, "data_directory", "")), 1000),
               "message": _text(getattr(source, "description", "已建立只读会话连接。"), 1000)}
        row["autoconnect"] = True
        try:
            with self.lock:
                if self.closed:
                    raise RuntimeError("会话观察已关闭。")
                latest = next((item for item in self.store.read()["tool_connections"]
                               if item.get("tool_id") == tool_id and item.get("endpoint", "") == endpoint), None)
                if (self.connection_epochs.get(identity, 0) != epoch or
                        automatic and latest and latest.get("autoconnect") is False):
                    raise ValueError("连接期间已手动断开；没有恢复此工具。")
                # Two connect clicks may finish transport setup concurrently.
                # Recheck the logical identity, not just a newly generated ID.
                existing = next((item for item in self.store.read()["tool_connections"]
                                 if item.get("tool_id") == tool_id and item.get("endpoint", "") == endpoint
                                 and item["id"] in self.sources), None)
                if existing:
                    source.close()
                    return existing
                if identifier in self.sources:
                    source.close()
                    return self.store.get_item("tool_connections", identifier)
                self.store.update_item("tool_connections", row)
                self.sources[identifier] = source
                self.sync.attach(row, source)
        except Exception:
            source.close()
            raise
        self.manager._publish({"type": "tool_connection", "connection": row})
        return deepcopy(row)

    def _source(self, identifier):
        with self.lock:
            source = self.sources.get(identifier)
            if self.closed or source is None:
                raise ValueError("工具未连接或连接已断开，请先连接工具。")
            return source

    def _discover_source(self, source, workspace="", *, private_paths=False):
        if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
            raise ValueError("工作空间路径无效。")
        rows = source.discover(workspace)
        return [{**{key: _text(row.get(key), 1000 if key == "workspace" else 200)
                 for key in ("id", "name", "workspace", "state", "model", "source", "session_kind", "parent_thread_id")},
                 # Only the background cache opts in; public callers retain
                 # redacted display paths, never filesystem identities.
                 **({"workspace": workspace_path(row.get("workspace"))} if private_paths else {}),
                 "created_at": _stamp(row.get("created_at")),
                 "last_activity": _stamp(row.get("last_activity")),
                 "updated_at": _stamp(row.get("updated_at")),
                 "child_count": row.get("child_count", 0) if type(row.get("child_count", 0)) is int else 0,
                 "metadata_only": row.get("metadata_only") is True,
                 "discovery_message": _text(row.get("discovery_message"), 1000),
                 "metadata_location": _text(row.get("metadata_location"), 1000)}
                for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]

    def discover(self, identifier, workspace=""):
        self._source(identifier)  # Enforce explicit disconnect/closed scope.
        if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
            raise ValueError("工作空间路径无效。")
        return self.sync.catalog(identifier, workspace, wait=True)

    def snapshot(self, identifier, session_id):
        self._source(identifier)
        return self.sync.snapshot(identifier, session_id)

    def track(self, identifier, session_ids):
        if (not isinstance(session_ids, list) or not 1 <= len(session_ids) <= 20
                or any(not isinstance(value, str) or not value or len(value) > 200 for value in session_ids)):
            raise ValueError("请明确选择1–20个会话；不自动跟进工具全部历史。")
        task_ids = {session_id: "session-" + hashlib.sha256((identifier + ":" + session_id).encode()).hexdigest()[:32]
                    for session_id in dict.fromkeys(session_ids)}
        guards = self.manager._guard_task_observation(list(task_ids.values()))
        try:
            source = self._source(identifier)
            connection = self.store.get_item("tool_connections", identifier)
            snapshots = [(session_id, self.snapshot(identifier, session_id)) for session_id in task_ids]
            if any(not isinstance(snapshot, dict) or snapshot.get("id") != session_id
                   for session_id, snapshot in snapshots):
                raise ValueError("宿主返回的会话身份不匹配，未添加会话。")
            result = []
            with self.manager.monitor.lock:
                if any(entry["generation"] != generation for entry, generation in guards.values()):
                    raise ValueError("读取期间任务已停止观察或移除；没有重新加入。需要恢复时请重新选择会话。")
                with self.lock:
                    if self.sources.get(identifier) is not source or self.closed:
                        raise ValueError("连接已改变，请重新选择会话。")
                for session_id, snapshot in snapshots:
                    task_id = task_ids[session_id]
                    tasks = self.store.read()["tasks"]
                    row = next((item for item in tasks if item["id"] == task_id), None)
                    if row is None:
                        row = {"id": task_id, "created_at": time.time(), "state": "unknown",
                               "started_at": None, "finished_at": None, "run_id": "", "watch_path": "",
                               "connection_id": identifier, "tool_id": connection["tool_id"],
                               "tool_name": connection["name"], "external_session_id": session_id,
                               "name": snapshot.get("name") or session_id, "source": connection["source"]}
                    row["monitoring"] = True
                    self._apply(row, snapshot, initial=True)
                    result.append(self.store.get_item("tasks", task_id))
            return result
        finally:
            self.manager._release_task_observation(guards)

    def _apply(self, row, snapshot, *, initial=False):
        if not isinstance(snapshot, dict) or snapshot.get("id") != row["external_session_id"]:
            raise ValueError("宿主返回的会话身份不匹配。")
        state = snapshot.get("state", "unknown")
        state = state if state in {"started", "completed", "failed", "cancelled", "idle", "unknown"} else "unknown"
        detail = public_detail(snapshot)
        connection = self.store.get_item("tool_connections", row["connection_id"])
        self._record_native_usage(connection, snapshot)
        candidate = deepcopy(row)
        candidate.update(state=state, name=_text(snapshot.get("name") or row["name"], 160),
                         started_at=_stamp(snapshot.get("started_at")), finished_at=_stamp(snapshot.get("finished_at")),
                         run_id=_text(snapshot.get("run_id"), 128), workspace=_text(snapshot.get("workspace"), 1000),
                         sync_state="connected", session_detail=detail,
                         message=_text(snapshot.get("message"), 2000) or "会话事件已同步；缺失的信息不推测。")
        if candidate["started_at"] and candidate["finished_at"] and candidate["finished_at"] < candidate["started_at"]:
            candidate["finished_at"] = None
        candidate.pop("observed_until", None)
        if candidate == row:
            self.sync_project(row)  # Retry trace persistence failures; dedup is local.
            return
        candidate["updated_at"] = time.time()
        self.store.update_item("tasks", candidate)
        if connection.get("state") != "connected":
            connection.update(state="connected", message="只读会话同步已恢复。")
            self.store.update_item("tool_connections", connection)
            self.manager._publish({"type": "tool_connection", "connection": connection})
        transition = (not initial and row.get("state") == "started" and
                      state in {"completed", "failed", "cancelled"} and row.get("run_id") == candidate.get("run_id"))
        self.manager._on_monitor_event({"type": "task", "task": candidate, "state": state,
                                       "message": candidate["message"], "transition": transition})
        self.sync_project(candidate)

    def _record_native_usage(self, connection, snapshot):
        """Read actual measurements without creating an observed task."""
        # Sources may expose a bounded canonical generation history. Never
        # recount every poll, mix two native tables, or use file mtime as usage.
        samples = snapshot.get("usage_events", [])
        samples = samples[-256:] if isinstance(samples, list) else []
        for sample in [*samples, snapshot]:
            if not isinstance(sample, dict):
                continue
            measurement = public_detail(sample)
            measurement.update(source=snapshot.get("source") or connection.get("source", ""),
                               usage_time=sample.get("usage_time"),
                               usage_id_scope=sample.get("usage_id_scope", "session"),
                               usage_model=sample.get("usage_model", sample.get("model", "")),
                               run_id=sample.get("run_id", snapshot.get("run_id", "")))
            if (snapshot.get("source") == "local_session_record"
                    and sample.get("usage_source") == "codex_token_usage_record"):
                # Atomic native response evidence is a separate counter stream
                # from legacy thread-counter observations; never mix/sum modes.
                measurement["source"] = "codex_token_usage_record"
            elif (snapshot.get("source") == "local_session_record"
                  and sample.get("usage_source") in {"codex_thread_counter", "codex_turn_counter", "codex_legacy_counter"}):
                measurement["source"] = sample["usage_source"]
            if not measurement["usage"]:
                continue
            # Immutable samples dominate archive polls. Cache only persisted
            # public measurements, so 20 sessions do not issue thousands of
            # unchanged SQLite transactions every two seconds. Retry failures.
            evidence = {key: measurement.get(key) for key in
                        ("source", "usage", "usage_scope", "usage_id", "usage_id_scope",
                         "usage_time", "usage_model", "run_id", "usage_counter_scope", "usage_counter_epoch")}
            fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
            key = (connection["tool_id"], snapshot["id"], evidence["source"], evidence["usage_id"])
            if self.usage_seen.get(key) == fingerprint:
                continue
            self.manager.record_usage(connection["tool_id"], snapshot["id"], measurement)
            if not self.manager.usage_error:
                self.usage_seen[key] = fingerprint
                while len(self.usage_seen) > 8192:
                    self.usage_seen.pop(next(iter(self.usage_seen)))

    def _apply_current_task(self, identifier, source, task_id, snapshot):
        with self.manager.monitor.lock:
            current = next((item for item in self.store.read()["tasks"] if item["id"] == task_id), None)
            with self.lock:
                still_current = self.sources.get(identifier) is source and not self.closed
            if current and current.get("monitoring", True) and still_current:
                self._apply(current, snapshot)

    def _lost(self, identifier, message):
        with self.manager.monitor.lock:
            connection = self.store.get_item("tool_connections", identifier)
            if connection.get("state") != "error" or connection.get("message") != message:
                connection.update(state="error", message=message)
                self.store.update_item("tool_connections", connection)
                self.manager._publish({"type": "tool_connection", "connection": connection})
            for row in self.store.read()["tasks"]:
                if row.get("connection_id") != identifier or not row.get("monitoring", True):
                    continue
                if row.get("sync_state") == "disconnected" and row.get("message") == message:
                    continue
                row.update(sync_state="disconnected", observed_until=time.time(), message=message, updated_at=time.time())
                self.store.update_item("tasks", row)
                self.manager._publish({"type": "task", "task": row, "state": "unknown", "transition": False})

    def disconnect(self, identifier):
        with self.lock:
            row = self.store.get_item("tool_connections", identifier)
            identity = (row["tool_id"], row.get("endpoint", ""))
            self.connection_epochs[identity] = self.connection_epochs.get(identity, 0) + 1
            source = self.sources.pop(identifier, None)
            row.update(state="disconnected", autoconnect=False, message="已断开；不停止宿主或模型。")
            self.store.update_item("tool_connections", row)
            self.sync.detach(identifier, row["tool_id"], row.get("location", ""))
        self._lost(identifier, "已断开会话观察；外部工具继续运行。")
        if source:
            source.close()
        row = self.store.get_item("tool_connections", identifier)
        row.update(state="disconnected", autoconnect=False, message="已断开；不停止宿主或模型。")
        self.store.update_item("tool_connections", row)
        self.manager._publish({"type": "tool_connection", "connection": row})

    def _lost_task(self, task_id, message):
        with self.manager.monitor.lock:
            row = next((item for item in self.store.read()["tasks"] if item["id"] == task_id), None)
            if not row or not row.get("monitoring", True):
                return
            if row.get("sync_state") == "disconnected" and row.get("message") == message:
                return
            row.update(sync_state="disconnected", observed_until=time.time(), message=message, updated_at=time.time())
            self.store.update_item("tasks", row)
            self.manager._publish({"type": "task", "task": row, "state": "unknown", "transition": False})

    def sync_project(self, row):
        """Selected, explicitly bound session only. Each public event deduplicates."""
        binding = row.get("trace_binding", {})
        trace = self.manager.project_trace
        if not isinstance(binding, dict) or not binding.get("active", True) or not row.get("monitoring", True):
            return
        actual_workspace = self.sync._workspace_identity(row.get("connection_id"), row.get("external_session_id"))
        if actual_workspace is None:
            return  # No native identity yet; never use a redacted display path.
        workspace_row = {"workspace": actual_workspace}
        with trace.lock:
            session = binding.get("observation_session_id")
            if not trace.enabled or trace.data["session_id"] != session or binding.get("external_session_id") != row.get("external_session_id"):
                return
            if not project_workspace_matches(workspace_row, trace):
                return  # Legacy binding cannot relocate another project's files.
            detail = row.get("session_detail", {})
            source = ("external_" + row.get("tool_id", "tool") + ":" + row.get("external_session_id", ""))[:80]
            if trace.data.get("focused_source") != source:
                try:
                    trace.focus_external(source)
                except (OSError, ValueError):
                    return  # Retry next poll; never apply B's events to A's focus.
                self.trace_seen = {key: value for key, value in self.trace_seen.items()
                                   if key[0] != session or key[2] not in {"goal_update", "memory", "plan", "step"}}
            events = []
            if detail.get("goal"):
                events.append({"kind": "goal_update", "title": detail["goal"]})
            if detail.get("memory"):
                events.append({"kind": "memory", "summary": detail["memory"]})
            plan = detail.get("plan", [])
            if plan:
                steps = [{"id": "host-" + str(index), "title": item["title"][:200], "depends_on": []}
                         for index, item in enumerate(plan) if item.get("title")]
                if steps:
                    events.append({"kind": "plan", "steps": steps})
                    for index, item in enumerate(plan):
                        if not item.get("title"):
                            continue
                        state = {"completed": "done", "in_progress": "running", "inProgress": "running"}.get(item.get("state"), item.get("state", "pending"))
                        events.append({"kind": "step", "step_id": "host-" + str(index),
                                       "state": state if state in {"pending", "running", "done", "failed", "cancelled"} else "pending"})
            for item in detail.get("requests", []):
                if item.get("text"):
                    events.append({"kind": "request", "text": item["text"],
                                   "reason": "宿主请求 " + item.get("id", "")[:160],
                                   "_host_id": item.get("id", "")})
            for item in detail.get("tools", []):
                if item.get("name") and item.get("state") in {"started", "completed", "failed"}:
                    events.append({"kind": "tool", "name": item["name"], "state": item["state"],
                                   "category": item.get("category") if item.get("category") in {"mcp", "skill", "command", "other"} else "other",
                                   "call_id": item.get("id", "")[:160], "summary": item.get("summary", ""),
                                   "_host_id": item.get("id", "")})
            if detail.get("usage") and detail.get("model"):
                tokens = {key: value for key, value in detail["usage"].items() if key in {"input_tokens", "output_tokens", "total_tokens"}}
                usage_id = detail.get("usage_id", "")
                if usage_id:
                    usage_id = hashlib.sha256((row["id"] + usage_id).encode()).hexdigest()
                events.append({"kind": "usage", "scope": detail.get("usage_scope", "cumulative"),
                               "model": detail["model"], "usage_id": usage_id, **tokens})
            for item in detail.get("files", []):
                if item.get("path") and item.get("operation") in {"created", "modified", "deleted"}:
                    events.append({"kind": "file", "path": item["path"], "operation": item["operation"],
                                   "_host_id": item.get("id", "")})
            import json
            for event in events:
                host_id = event.pop("_host_id", "")
                body = json.dumps(event, sort_keys=True, ensure_ascii=False)
                immutable = event["kind"] in {"request", "tool", "file"} or (event["kind"] == "usage" and event.get("scope") == "turn")
                identity = host_id or (event.get("usage_id", "") if event["kind"] == "usage" else "")
                run_key = "" if immutable and identity else row.get("run_id", "")
                digest = hashlib.sha256((session + row["id"] + run_key + identity + body).encode()).hexdigest()
                key = (session, row["id"], event["kind"], digest if immutable else event.get("step_id", ""))
                if self.trace_seen.get(key) == digest:
                    continue
                event.update(session_id=session, source=source, event_id=uuid.uuid4().hex)
                try:
                    trace.accept(event)
                except (OSError, ValueError):
                    continue
                self.trace_seen[key] = digest
            while len(self.trace_seen) > 1000:
                self.trace_seen.pop(next(iter(self.trace_seen)))

    def focus_project(self, row):
        """A project view focuses one native session; other rings keep following."""
        session = row.get("trace_binding", {}).get("observation_session_id")
        with self.manager.project_trace.lock:
            source = ("external_" + row.get("tool_id", "tool") + ":" + row.get("external_session_id", ""))[:80]
            self.manager.project_trace.focus_external(source)
            # Re-entering an earlier session must reapply its current plan even
            # when that snapshot was already seen before another source's plan.
            self.trace_seen = {key: value for key, value in self.trace_seen.items()
                               if key[0] != session or key[2] not in {"goal_update", "memory", "plan", "step"}}
            self.sync_project(row)

    def close(self):
        with self.lock:
            self.closed = True
            sources, self.sources = list(self.sources.values()), {}
        self.sync.close()
        for source in sources:
            source.close()
