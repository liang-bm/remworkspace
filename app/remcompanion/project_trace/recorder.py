"""Thread-safe plan/step trace plus bounded optional filesystem observation."""

from __future__ import annotations

from copy import deepcopy
from collections import deque
import json
from pathlib import Path
import re
import threading
import time
import uuid

from .files import MAX_CACHE_BYTES, OBSERVATION_POLICY, allowed_path, difference, read_text, references, scan

STATES = {"pending", "running", "done", "failed", "cancelled"}
ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
MAX_PLAN_HISTORY = 20
MAX_REQUESTS = 200
MAX_CHECKPOINTS = 100
MAX_TIMELINE = 500
MAX_TOOL_EVENTS = 200
MAX_USAGE_TURNS = 200
MAX_USAGE_CUMULATIVE = 100
MAX_USAGE_IDS = 1000
MAX_EVENT_BYTES = 65536


def _redact(value: str) -> str:
    """Conservative common-credential masking, not a guarantee for arbitrary secrets."""
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[redacted private key]", value, flags=re.S)
    value = re.sub(r"(?i)\bBearer\s+[^\s\"'<>;,]+", "Bearer [redacted]", value)
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,})\b", "[redacted]", value)
    value = re.sub(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|authorization)\b\s*[:=]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s;,<>]+)", r"\1[redacted]", value)
    return re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", value)


def _text(value: object, label: str, limit: int, *, required=False) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{label} 应为{'非空' if required else ''}字符串，最多 {limit} 字符")
    return _redact(value)[:limit]


def _items(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError(f"{label} 应为最多 20 项的字符串数组")
    return [_text(item, label, 1000, required=True) for item in value]


def _new_data(*, session_id="", workspace="", started_at=0, coverage="off") -> dict:
    return {"session_id": session_id, "workspace": workspace, "started_at": started_at,
            "steps": [], "changes": [], "plan_revision": 0, "plan_history": [],
            "history_truncated": 0, "coverage": coverage, "error": "", "goal": None,
            "requests": [], "checkpoints": [], "timeline": [], "requests_truncated": 0,
            "checkpoints_truncated": 0, "timeline_truncated": 0, "baseline_files": 0,
            "discovered_files": 0, "observation_policy": OBSERVATION_POLICY,
            "active_goal": None, "latest_memory": None, "tool_events": [],
            "tool_events_truncated": 0, "usage_turns": [], "usage_turns_truncated": 0,
            "usage_cumulative": [], "usage_cumulative_truncated": 0,
            "usage_seen_ids": [], "usage_seen_ids_truncated": 0}


def _plan_signature(steps: list[dict]) -> list[tuple]:
    """Structural comparison excludes live statuses and timestamps."""
    return [(row["id"], row["title"], row["depends_on"], row.get("source", "")) for row in steps]


class ProjectTrace:
    """No thread, scan, endpoint or model request is started by construction."""

    def __init__(self, app_root: Path) -> None:
        self.directory = Path(app_root) / "data" / "project-trace"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.enabled = False
        self.workspace: Path | None = None
        self.version = 0
        self.data = _new_data()
        self._signatures = {}
        self._text = {}
        self._cache_bytes = 0
        self._diffs = {}
        self._event_ids = deque(maxlen=1000)

    def enable(self, workspace: Path) -> str:
        self.disable()
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("请选择存在的工作空间目录")
        with self.lock:
            self.workspace = root
            self.stop = threading.Event()
            self._signatures = {}
            self._text = {}
            self._cache_bytes = 0
            self._last_coverage = ""
            self._diffs = {}
            self._event_ids.clear()
            self.data = _new_data(session_id=uuid.uuid4().hex, workspace=str(root),
                                  started_at=time.time(), coverage="initializing")
            self._persist()
            self.enabled = True
            self.version += 1
            self.thread = threading.Thread(target=self._watch, name="remchat-project-trace", daemon=True)
            self.thread.start()
            return self.data["session_id"]

    def disable(self) -> None:
        with self.lock:
            self.enabled = False
            self.stop.set()
            thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError("项目观察尚未退出，请稍后再切换工作空间")
        with self.lock:
            self.thread = None
            self.version += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {**deepcopy(self.data), "enabled": self.enabled, "version": self.version}

    def focus_external(self, source: str) -> None:
        """Explicitly focus one host session without mixing current plans.

        Historical timelines/files/usage stay intact. Missing public metadata
        from the newly selected host must not inherit another host's goal or
        memory. Only the internal user-action path calls this method.
        """
        source = _text(source, "观察来源", 80, required=True)
        with self.lock:
            if not self.enabled:
                raise ValueError("项目观察未开启")
            if self.data.get("focused_source") == source:
                return
            previous, version = self.data, self.version
            self.data = deepcopy(previous)
            try:
                if self.data["steps"]:
                    self.data["plan_history"].append({"revision": self.data["plan_revision"],
                        "archived_at": time.time(), "steps": deepcopy(self.data["steps"])})
                    if len(self.data["plan_history"]) > MAX_PLAN_HISTORY:
                        self.data["plan_history"].pop(0)
                        self.data["history_truncated"] += 1
                    self.data["plan_revision"] += 1
                self.data.update(focused_source=source, steps=[], active_goal=None, latest_memory=None)
                self._persist()
                self.version += 1
            except Exception:
                self.data, self.version = previous, version
                raise

    def detail(self, change_id: str) -> str:
        with self.lock:
            return self._diffs.get(change_id, "")

    def _persist(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "latest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows readers/scanners can briefly deny atomic replacement even
        # after our own stream closes. Retry only that case, with a UI-safe bound.
        deadline = time.monotonic() + 0.2
        while True:
            try:
                temporary.replace(path)
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)

    def export(self, path: Path) -> None:
        value = self.snapshot()
        # Explicit metadata export only; no file bodies or diff previews. Free
        # text may still contain private content despite common-token masking.
        Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _identifier(value: object) -> str:
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise ValueError("步骤 ID 应为 1–80 位字母、数字、下划线或连字符")
        return value

    def accept(self, payload: dict) -> dict:
        """Apply an event atomically to metadata; failed saves can be retried."""
        try:
            if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_EVENT_BYTES:
                raise ValueError("项目事件不能超过 64 KiB")
        except (TypeError, RecursionError) as error:
            raise ValueError("项目事件必须是可序列化的小型 JSON 对象") from error
        with self.lock:
            previous, version = self.data, self.version
            differences = self._diffs.copy()
            self.data = deepcopy(previous)
            try:
                return self._accept_event(payload)
            except Exception:
                self.data, self.version, self._diffs = previous, version, differences
                raise

    def accept_many(self, events: list[dict]) -> list[dict]:
        """Validate an explicit import as one transaction and one disk replacement."""
        if not isinstance(events, list) or not 1 <= len(events) <= 100:
            raise ValueError("批量项目事件应为 1–100 个对象")
        try:
            if len(json.dumps(events, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_EVENT_BYTES:
                raise ValueError("批量项目事件合计不能超过 64 KiB")
        except (TypeError, RecursionError) as error:
            raise ValueError("批量项目事件必须是可序列化的小型 JSON") from error
        with self.lock:
            previous, version = self.data, self.version
            differences = self._diffs.copy()
            identifiers = deque(self._event_ids, maxlen=self._event_ids.maxlen)
            self.data = deepcopy(previous)
            try:
                results = [self._accept_event(event, persist=False) for event in events]
                if self.version != version:
                    self._persist()
                return results
            except Exception:
                self.data, self.version, self._diffs, self._event_ids = previous, version, differences, identifiers
                raise

    def _bounded_append(self, field: str, row: dict, limit: int) -> None:
        values = self.data.setdefault(field, [])
        values.append(row)
        excess = max(0, len(values) - limit)
        if excess:
            del values[:excess]
            counter = field + "_truncated"
            self.data[counter] = self.data.get(counter, 0) + excess

    def _timeline(self, kind: str, source: str, **fields) -> dict:
        row = {"id": uuid.uuid4().hex, "kind": kind, "timestamp": time.time(), "source": source,
               "plan_revision": self.data["plan_revision"], **fields}
        self._bounded_append("timeline", row, MAX_TIMELINE)
        return row

    def _accept_event(self, payload: dict, *, persist=True) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("项目事件必须是 JSON 对象")
        with self.lock:
            if not self.enabled:
                raise ValueError("项目过程视图未开启")
            if payload.get("session_id") != self.data["session_id"]:
                raise ValueError("项目事件 session_id 与当前观察会话不匹配")
            event_id = str(payload.get("event_id", ""))[:160]
            if event_id and event_id in self._event_ids:
                return {"session_id": self.data["session_id"], "duplicate": True}
            kind = payload.get("kind")
            source = _redact(str(payload.get("source", "CLI"))[:80])[:80]
            if kind == "goal":
                title = _text(payload.get("title"), "原始目标", 12000, required=True)
                if self.data.get("goal") is not None:
                    if self.data["goal"]["title"] == title:
                        return {"session_id": self.data["session_id"], "duplicate": True}
                    raise ValueError("原始目标已记录，补充或修改要求请上报 request，不覆盖原目标")
                self.data["goal"] = deepcopy(self._timeline("goal", source, title=title))
                if self.data.get("active_goal") is None:
                    self.data["active_goal"] = deepcopy(self.data["goal"])
            elif kind == "goal_update":
                title = _text(payload.get("title"), "当前目标", 12000, required=True)
                self.data["active_goal"] = deepcopy(self._timeline("goal_update", source, title=title))
            elif kind == "request":
                text = _text(payload.get("text"), "补充请求", 12000, required=True)
                reason = _text(payload.get("reason", ""), "请求原因", 4000)
                self._bounded_append("requests", deepcopy(self._timeline("request", source, text=text, reason=reason)), MAX_REQUESTS)
            elif kind == "checkpoint":
                summary = _text(payload.get("summary"), "检查点摘要", 12000, required=True)
                retained = _items(payload.get("retained", []), "保留内容")
                omitted = _items(payload.get("omitted", []), "未保留内容")
                self._bounded_append("checkpoints", deepcopy(self._timeline("checkpoint", source,
                    summary=summary, retained=retained, omitted=omitted)), MAX_CHECKPOINTS)
            elif kind == "memory":
                summary = _text(payload.get("summary"), "实际记忆摘要", 12000, required=True)
                retained = _items(payload.get("retained", []), "记忆保留内容")
                omitted = _items(payload.get("omitted", []), "记忆未保留内容")
                self.data["latest_memory"] = deepcopy(self._timeline("memory", source,
                    summary=summary, retained=retained, omitted=omitted))
            elif kind == "tool":
                name = _text(payload.get("name"), "工具名称", 200, required=True)
                category = _text(payload.get("category", "other"), "工具类别", 16, required=True).lower()
                state = payload.get("state")
                if category not in {"mcp", "skill", "command", "other"}:
                    raise ValueError("工具 category 应为 mcp/skill/command/other")
                if not isinstance(state, str) or state not in {"started", "completed", "failed"}:
                    raise ValueError("工具 state 应为 started/completed/failed")
                call_id = _text(payload.get("call_id", ""), "工具调用 ID", 160).strip()
                summary = _text(payload.get("summary", ""), "工具结果摘要", 4000)
                # Persist this allowlist only: raw args/results are neither
                # retained nor inspected/executed by the presentation backend.
                self._bounded_append("tool_events", deepcopy(self._timeline("tool", source,
                    name=name, category=category, state=state, call_id=call_id,
                    summary=summary)), MAX_TOOL_EVENTS)
            elif kind == "usage":
                model = _text(payload.get("model"), "计量模型名称", 200, required=True)
                scope = payload.get("scope", "turn")
                if not isinstance(scope, str) or scope not in {"turn", "cumulative"}:
                    raise ValueError("usage scope 应为 turn/cumulative")
                tokens = {name: payload.get(name) for name in ("input_tokens", "output_tokens", "total_tokens")}
                if any(value is not None and (type(value) is not int or value < 0) for value in tokens.values()):
                    raise ValueError("token 计量须为非负整数；未知值请省略或使用 null")
                usage_id = _text(payload.get("usage_id", ""), "计量 ID", 160).strip()
                key = {"source": source, "model": model, "scope": scope, "usage_id": usage_id}
                if usage_id and key in self.data.get("usage_seen_ids", []):
                    return {"session_id": self.data["session_id"], "duplicate": True}
                measurement = deepcopy(self._timeline("usage", source, model=model,
                    scope=scope, usage_id=usage_id, **tokens))
                if scope == "turn":
                    self._bounded_append("usage_turns", measurement, MAX_USAGE_TURNS)
                else:
                    snapshots = self.data.setdefault("usage_cumulative", [])
                    # Independent source+model snapshot, never added to turns
                    # or interpreted as an increment since the previous value.
                    snapshots[:] = [row for row in snapshots if (row["source"], row["model"]) != (source, model)]
                    self._bounded_append("usage_cumulative", measurement, MAX_USAGE_CUMULATIVE)
                if usage_id:
                    self._bounded_append("usage_seen_ids", key, MAX_USAGE_IDS)
            elif kind == "plan":
                steps = payload.get("steps")
                if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
                    raise ValueError("计划必须包含 1–100 个步骤")
                checked = []
                for row in steps:
                    if not isinstance(row, dict):
                        raise ValueError("计划步骤必须是对象")
                    identifier = self._identifier(row.get("id"))
                    title = row.get("title")
                    depends = row.get("depends_on", [])
                    if not isinstance(title, str) or not title.strip() or len(title) > 200:
                        raise ValueError("步骤标题需要 1–200 个字符")
                    if not isinstance(depends, list) or len(depends) > 100:
                        raise ValueError("depends_on 应为步骤 ID 数组")
                    checked.append({"id": identifier, "title": _redact(title.strip())[:200], "depends_on": [self._identifier(item) for item in depends],
                                    "state": "pending", "message": "", "source": source, "updated_at": time.time(),
                                    "outcome": _text(row.get("outcome", ""), "步骤结果", 4000),
                                    "remaining": _text(row.get("remaining", ""), "尚需处理", 4000)})
                ids = {row["id"] for row in checked}
                if len(ids) != len(checked):
                    raise ValueError("计划步骤 ID 不能重复")
                graph = {row["id"]: row["depends_on"] for row in checked}
                visited, visiting = set(), set()
                def visit(identifier):
                    if identifier not in ids or identifier in visiting:
                        raise ValueError("步骤依赖不存在或形成循环")
                    if identifier in visited:
                        return
                    visiting.add(identifier)
                    for dependency in graph[identifier]:
                        visit(dependency)
                    visiting.remove(identifier)
                    visited.add(identifier)
                for identifier in graph:
                    visit(identifier)
                # Keep the previous graph/statuses together, not a mixed pile of
                # removed steps. Validate the entire new graph before archiving.
                revised = _plan_signature(checked) != _plan_signature(self.data["steps"])
                if revised:
                    if self.data["steps"] or any(row.get("plan_revision", 0) == self.data["plan_revision"] for row in self.data["changes"]):
                        self.data["plan_history"].append({"revision": self.data["plan_revision"],
                            "archived_at": time.time(), "steps": deepcopy(self.data["steps"])})
                        if len(self.data["plan_history"]) > MAX_PLAN_HISTORY:
                            self.data["plan_history"].pop(0)
                            self.data["history_truncated"] += 1
                    self.data["plan_revision"] += 1
                # Revisions keep status for unchanged IDs; not a fake reset.
                old = {row["id"]: row for row in self.data["steps"]}
                for index, row in enumerate(checked):
                    if row["id"] in old:
                        for key in ("state", "message", "updated_at"):
                            row[key] = old[row["id"]][key]
                        for key in ("outcome", "remaining"):
                            if key not in steps[index]:
                                row[key] = old[row["id"]].get(key, "")
                self.data["steps"] = checked
                self._timeline("plan", source, title="", step_count=len(checked), revised=revised)
            elif kind == "step":
                identifier = self._identifier(payload.get("step_id"))
                state = payload.get("state")
                if not isinstance(state, str) or state not in STATES:
                    raise ValueError("步骤状态应为 pending/running/done/failed/cancelled")
                row = next((row for row in self.data["steps"] if row["id"] == identifier), None)
                if row is None:
                    if len(self.data["steps"]) >= 100:
                        raise ValueError("步骤数量已达 100 个上限")
                    row = {"id": identifier, "title": _redact(str(payload.get("title", identifier)))[:200], "depends_on": [],
                           "outcome": "", "remaining": ""}
                    self.data["steps"].append(row)
                for key in ("outcome", "remaining"):
                    if key in payload:
                        row[key] = _text(payload[key], "步骤结果" if key == "outcome" else "尚需处理", 4000)
                row.update(state=state, source=source, message=_redact(str(payload.get("message", "")))[:4000], updated_at=time.time())
                self._timeline("step", source, step_id=identifier, title=row["title"], state=state,
                               message=row["message"], outcome=row.get("outcome", ""), remaining=row.get("remaining", ""))
            elif kind == "file":
                relative = payload.get("path")
                if not isinstance(relative, str) or len(relative) > 1000:
                    raise ValueError("文件路径无效")
                allowed_path(self.workspace, relative)
                operation = payload.get("operation")
                if not isinstance(operation, str) or operation not in {"created", "modified", "deleted"}:
                    raise ValueError("operation 应为 created/modified/deleted")
                step = str(payload.get("step_id", ""))
                if step and not any(row["id"] == step for row in self.data["steps"]):
                    raise ValueError("文件事件引用了不存在的步骤")
                self._change(relative, operation, evidence="tool_reported", source=source, step_id=step)
            else:
                raise ValueError("kind 应为 goal/goal_update/request/checkpoint/memory/tool/usage/plan/step/file")
            self.version += 1
            if persist:
                self._persist()
            if event_id:
                self._event_ids.append(event_id)
            return {"session_id": self.data["session_id"], "kind": kind}

    def task_event(self, event: dict) -> None:
        """Existing monitoring supplies task status, not imagined tool steps."""
        if event.get("type") != "task":
            return
        with self.lock:
            if not self.enabled:
                return
            task = event.get("task", {})
            identifier = "task-" + str(task.get("id", ""))
            if not ID.fullmatch(identifier):
                return
            mapping = {"started": "running", "completed": "done", "failed": "failed", "cancelled": "cancelled"}
            state = mapping.get(event.get("state"))
            if not state:
                return
            try:
                self.accept({"session_id": self.data["session_id"], "kind": "step", "step_id": identifier,
                             "title": task.get("name", identifier), "state": state,
                             "message": event.get("message", ""), "source": "task_monitor"})
            except (OSError, ValueError) as error:
                self.data["error"] = str(error)

    def _cache(self, path: str, text: str | None) -> None:
        previous = self._text.pop(path, None)
        if previous is not None:
            self._cache_bytes -= len(previous.encode("utf-8"))
        if text is not None:
            size = len(text.encode("utf-8"))
            if self._cache_bytes + size <= MAX_CACHE_BYTES:
                self._text[path] = text
                self._cache_bytes += size

    def _change(self, path: str, operation: str, *, evidence="observed", source="local_observer", step_id="", before=None, after=None) -> None:
        running = [row["id"] for row in self.data["steps"] if row["state"] == "running"]
        identifier = uuid.uuid4().hex[:16]
        row = {"id": identifier, "path": path, "operation": operation, "evidence": evidence, "source": source,
               "step_id": step_id, "during_steps": running if evidence == "observed" else [], "timestamp": time.time(),
               "plan_revision": self.data["plan_revision"],
               "references": references(self.workspace, path, after)}
        self.data["changes"].append(row)
        diff = difference(before, after)
        if diff:
            self._diffs[identifier] = diff
        while len(self.data["changes"]) > 500:
            removed = self.data["changes"].pop(0)
            self._diffs.pop(removed["id"], None)

    def _poll(self, *, initial=False) -> None:
        signatures, complete = scan(self.workspace, self.stop)
        if self.stop.is_set():
            return
        with self.lock:
            if not self.enabled:
                return
            previous = (self.data, self.version, self._signatures.copy(), self._text.copy(),
                        self._cache_bytes, self._diffs.copy(), getattr(self, "_last_coverage", ""))
            self.data = deepcopy(self.data)
            try:
                self._apply_scan(signatures, complete, initial=initial)
            except Exception:
                (self.data, self.version, self._signatures, self._text,
                 self._cache_bytes, self._diffs, self._last_coverage) = previous
                raise

    def _apply_scan(self, signatures: dict, complete: bool, *, initial=False) -> None:
        """Runs under the trace lock, with rollback provided by _poll."""
        with self.lock:
            self.data["coverage"] = "complete" if complete else "partial"
            coverage_changed = self.data["coverage"] != getattr(self, "_last_coverage", "")
            previous_complete = getattr(self, "_last_coverage", "") == "complete"
            self._last_coverage = self.data["coverage"]
            changed = initial or coverage_changed
            reads = 0
            for path, signature in signatures.items():
                old = self._signatures.get(path)
                if signature != old:
                    # Metadata coverage is separate from bounded content cache.
                    # Never read every large source file just to fill a graph.
                    can_read = reads < 40 and (not initial or self._cache_bytes < MAX_CACHE_BYTES)
                    after = read_text(self.workspace, path) if can_read else None
                    reads += int(can_read)
                    if initial:
                        self.data["baseline_files"] = self.data.get("baseline_files", 0) + int(old is None)
                    elif old is None and not previous_complete:
                        # Newly covered old files are not changes. Counters keep
                        # evidence without crowding the 500 important-event slots.
                        self.data["discovered_files"] = self.data.get("discovered_files", 0) + 1
                    else:
                        operation = "created" if old is None else "modified"
                        self._change(path, operation, before="" if operation == "created" else self._text.get(path), after=after)
                    changed = True
                    self._cache(path, after)
            if complete:
                for path in self._signatures.keys() - signatures.keys():
                    if not initial:
                        self._change(path, "deleted", before=self._text.get(path), after="")
                    changed = True
                    self._cache(path, None)
                self._signatures = signatures
            else:
                self._signatures.update(signatures)
            if changed:
                self.version += 1
                self._persist()

    def _watch(self) -> None:
        initial = True
        while not self.stop.is_set():
            try:
                self._poll(initial=initial)
                initial = False
            except (OSError, ValueError) as error:
                with self.lock:
                    self.data["error"] = str(error)
                    self.version += 1
            if self.stop.wait(2):
                break
