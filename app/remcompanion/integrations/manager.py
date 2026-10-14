"""RemChat 接入门面。

聊天窗口只依赖此模块的公开方法。配置导入、协议连接、工具执行分别是不同操作；
模型能整理草稿和读取已启用 Skill，但不能借草稿启动命令或启用工具。
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import uuid
from typing import Any

from .events import TaskMonitor
from .mcp import MCPClient, program_argv
from .processes import ProcessScope, program_environment
from .skills import SkillLibrary
from .storage import IntegrationStore, checked_id, parse_mcp_import, validate_mcp
from .session_monitor import ToolSessionMonitor, project_workspace_matches
from .usage_ledger import UsageLedger
from .adapters import adapter_catalog, export_adapter
from ..project_trace import ProjectTrace


HOOK_EVENTS = {"task_started", "task_completed", "task_failed", "task_cancelled", "manual_test"}
MAX_HOOK_OUTPUT_BYTES = 4 * 1024 * 1024


def validate_hook(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Hook 配置应为 JSON 对象。")
    row = deepcopy(item)
    row["id"] = checked_id(str(row.get("id", "")))
    events = row.get("events", [])
    if not isinstance(events, list) or not events or any(event not in HOOK_EVENTS for event in events):
        raise ValueError("Hook events 必须是 task_started/task_completed/task_failed/task_cancelled/manual_test 中的非空列表。")
    command = str(row.get("command", "")).strip()
    if not command:
        raise ValueError("Hook 需要可执行程序。")
    if not isinstance(row.get("enabled", False), bool):
        raise ValueError("Hook enabled 应为 JSON true/false。")
    args = row.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("Hook args 必须是字符串数组。")
    program_argv(command, args)
    row.update({"command": command, "args": args, "enabled": bool(row.get("enabled", False)), "timeout": max(1, min(120, float(row.get("timeout", 30))))})
    for field in ("env", "env_refs"):
        if not isinstance(row.get(field, {}), dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in row.get(field, {}).items()):
            raise ValueError(f"Hook {field} 应为字符串映射。")
    return row


class IntegrationManager:
    """公开接口均不依赖 Tk；订阅回调可能在工作线程运行，UI 用队列接收。"""

    def __init__(self, root_path: Path | str) -> None:
        self.store = IntegrationStore(root_path)
        self.root = self.store.root
        self.settings_path = self.store.path
        self.skills = SkillLibrary(self.store)
        self.clients: dict[str, MCPClient] = {}
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.lock = threading.RLock()
        self.closed = False
        self.running_hooks: set[ProcessScope] = set()
        # Only in-flight observation requests need generations. The final
        # request releases its entry, so deleted task IDs do not accumulate.
        self._task_observation_guards: dict[str, dict[str, int]] = {}
        self.usage_ledger = None
        self.usage_error = ""
        try:
            self.usage_ledger = UsageLedger(self.root)
        except (OSError, ValueError) as error:
            # A damaged optional ledger must not prevent chatting or observing.
            self.usage_error = str(error)[:600]
        self.project_trace = ProjectTrace(self.root)
        self.monitor = TaskMonitor(self.store, self._on_monitor_event)
        self.monitor.project_handler = self.project_trace.accept
        self.tool_sessions = ToolSessionMonitor(self)
        self.tool_sessions.start()

    def record_usage(self, tool_id, session_id, snapshot):
        """Persist existing metadata only; never fetch or initiate a model call."""
        if self.usage_ledger is None:
            return False
        try:
            changed = self.usage_ledger.record(tool_id, session_id, snapshot)
            self.usage_error = ""
            if changed:
                self._publish({"type": "usage_changed", "tool_id": tool_id})
            return changed
        except (OSError, ValueError, TypeError) as error:
            self.usage_error = str(error)[:600]
            return False

    def usage_summary(self, days=30, tool_id="", model=""):
        if self.usage_ledger is None:
            raise ValueError(self.usage_error or "本地用量账本不可用；其他功能仍可使用。")
        return self.usage_ledger.summary(days=days, tool_id=tool_id, model=model)

    def usage_calendar(self, year, tool_id="", model=""):
        if self.usage_ledger is None:
            raise ValueError(self.usage_error or "本地用量账本不可用；其他功能仍可使用。")
        return self.usage_ledger.calendar(year, tool_id=tool_id, model=model)

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self.lock:
            self.listeners.append(callback)
        def unsubscribe() -> None:
            with self.lock:
                if callback in self.listeners:
                    self.listeners.remove(callback)
        return unsubscribe

    def _publish(self, event: dict[str, Any]) -> None:
        with self.lock:
            listeners = list(self.listeners)
        for listener in listeners:
            try:
                listener(deepcopy(event))
            except Exception:
                # 某个窗口已销毁不能中断其他订阅者和真实任务状态更新。
                continue

    def _on_monitor_event(self, event: dict[str, Any]) -> None:
        # Task observation and project observation have different lifetimes.
        # A workspace switch must never pull an unrelated or formerly bound
        # external task into the newly enabled project trace.
        task = event.get("task", {})
        binding = task.get("trace_binding") if isinstance(task, dict) else None
        if (event.get("type") == "task" and isinstance(binding, dict)
                and binding.get("active", True) and not task.get("connection_id")
                and task.get("monitoring", True) and not self.closed):
            # Hold the trace lock through the check and accept: enable/disable
            # cannot replace the current session between these two operations.
            with self.project_trace.lock:
                if (self.project_trace.enabled
                        and binding.get("observation_session_id") == self.project_trace.data["session_id"]):
                    self.project_trace.task_event(event)
        self._publish(event)
        if event.get("type") == "task" and event.get("transition", True):
            mapping = {"started": "task_started", "completed": "task_completed", "failed": "task_failed", "cancelled": "task_cancelled"}
            kind = mapping.get(str(event.get("state")))
            if kind:
                self.emit_event(kind, event, publish=False)

    def list_mcp(self) -> list[dict[str, Any]]:
        return self.store.read()["mcp"]

    def mcp_connected(self, identifier: str) -> bool:
        """A completed handshake, not a saved enabled flag or a health probe."""
        with self.lock:
            client = self.clients.get(identifier)
            return bool(client and client.server and not client.closed
                        and (client.process is None or client.process.poll() is None))

    def upsert_mcp(self, item: dict[str, Any]) -> None:
        value = validate_mcp(item)
        self.disconnect_mcp(value["id"])
        self.store.update_item("mcp", value)

    def import_mcp(self, text: str) -> list[dict[str, Any]]:
        rows = parse_mcp_import(text)
        with self.store.lock:
            settings = self.store.read()
            merged = {row["id"]: row for row in settings["mcp"]}
            merged.update({row["id"]: row for row in rows})
            settings["mcp"] = list(merged.values())
            if rows and any(row["id"] in {old["id"] for old in self.list_mcp()} for row in rows):
                self.store.backup()
            self.store.write(settings)
        for row in rows:
            self.disconnect_mcp(row["id"])
        return rows

    def remove_mcp(self, identifier: str) -> None:
        with self.store.lock:
            self.store.get_item("mcp", identifier)
            self.store.backup()
            self.disconnect_mcp(identifier)
            self.store.remove_item("mcp", identifier)

    def connect_mcp(self, identifier: str) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError("接入服务已关闭。")
        self.disconnect_mcp(identifier)
        client = MCPClient(self.store.get_item("mcp", identifier))
        with self.lock:
            if self.closed:
                client.close()
                raise RuntimeError("接入服务已关闭。")
            self.clients[identifier] = client
        try:
            result = client.connect()
            if self.closed:
                client.close()
                raise RuntimeError("接入服务已关闭。")
            return result
        except Exception:
            with self.lock:
                if self.clients.get(identifier) is client:
                    self.clients.pop(identifier, None)
            client.close()
            raise

    def disconnect_mcp(self, identifier: str) -> None:
        with self.lock:
            client = self.clients.pop(identifier, None)
        if client:
            client.close()

    def _client(self, identifier: str) -> MCPClient:
        with self.lock:
            client = self.clients.get(identifier)
        if client is None:
            raise ValueError("请先连接此 MCP 服务。")
        if not self.store.get_item("mcp", identifier).get("enabled"):
            raise ValueError("此 MCP 服务已禁用。")
        return client

    def catalog_mcp(self, identifier: str) -> dict[str, Any]:
        client = self._client(identifier)
        capabilities = client.server.get("capabilities", {})
        result: dict[str, Any] = {"server": client.server, "tools": [], "resources": [], "prompts": []}
        for capability, method, field in (("tools", "tools/list", "tools"), ("resources", "resources/list", "resources"), ("prompts", "prompts/list", "prompts")):
            if capability in capabilities:
                result[field] = client.list_all(method, field)
        return result

    def call_tool(self, identifier: str, name: str, arguments: dict[str, Any], *, explicit_user_action: bool = False) -> dict[str, Any]:
        if not explicit_user_action:
            raise PermissionError("MCP 工具必须由用户在界面中明确执行。")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数应为 JSON 对象。")
        client = self._client(identifier)
        known = client.list_all("tools/list", "tools")
        if not any(tool.get("name") == name for tool in known):
            raise ValueError("此工具不在服务端当前工具清单中。")
        # Only an actual, explicitly authorized call produces tool evidence.
        # Capture this observation once; a late result cannot switch projects.
        session, call_id, tool_name = self._begin_mcp_call(identifier, name)
        try:
            result = client.request("tools/call", {"name": name, "arguments": arguments})
        except Exception:
            self._record_mcp_call(session, call_id, tool_name, "failed")
            raise
        state = "failed" if result.get("isError") is True else "completed"
        self._record_mcp_call(session, call_id, tool_name, state)
        return result

    def _begin_mcp_call(self, identifier: str, name: str) -> tuple[str, str, str]:
        try:
            with self.project_trace.lock:
                if self.project_trace.enabled and not self.closed:
                    session = self.project_trace.data["session_id"]
                    call_id = uuid.uuid4().hex
                    tool_name = f"{identifier}:{name}"
                    self._record_mcp_call(session, call_id, tool_name, "started")
                    return session, call_id, tool_name
        except Exception:
            # Optional telemetry must never affect the MCP operation itself.
            pass
        return "", "", ""

    def _record_mcp_call(self, session: str, call_id: str, name: str, state: str) -> None:
        if not session:
            return
        summaries = {
            "started": "用户明确授权的 MCP 工具调用已发起。",
            "completed": "MCP 调用已返回；结果仍需用户检查。",
            "failed": "MCP 调用未成功；未保存参数、结果或错误详情。",
        }
        try:
            with self.project_trace.lock:
                if (not self.closed and self.project_trace.enabled
                        and self.project_trace.data["session_id"] == session):
                    self.project_trace.accept({
                        "kind": "tool", "session_id": session, "source": "remchat_mcp",
                        "category": "mcp", "name": name, "state": state,
                        "call_id": call_id, "summary": summaries[state],
                    })
        except Exception:
            # The recorder already rolls back failed saves; preserve the
            # original tool result/error even if observation or storage fails.
            pass

    def read_resource(self, identifier: str, uri: str) -> dict[str, Any]:
        client = self._client(identifier)
        if "resources" not in client.server.get("capabilities", {}):
            raise ValueError("此 MCP 服务没有提供资源。")
        return client.request("resources/read", {"uri": uri})

    def get_prompt(self, identifier: str, name: str, arguments: dict[str, str]) -> dict[str, Any]:
        client = self._client(identifier)
        if "prompts" not in client.server.get("capabilities", {}):
            raise ValueError("此 MCP 服务没有提供提示词。")
        return client.request("prompts/get", {"name": name, "arguments": arguments})

    def list_hooks(self) -> list[dict[str, Any]]:
        return self.store.read()["hooks"]

    def upsert_hook(self, item: dict[str, Any]) -> None:
        self.store.update_item("hooks", validate_hook(item))

    def remove_hook(self, identifier: str) -> None:
        self.store.remove_item("hooks", identifier)

    def emit_event(self, name: str, payload: dict[str, Any], *, publish: bool = True) -> None:
        if name not in HOOK_EVENTS:
            raise ValueError("不支持的 Hook 事件名称。")
        event = {"event": name, "timestamp": time.time(), "payload": deepcopy(payload)}
        if publish:
            self._publish({"type": "hook_event", **event})
        if self.closed:
            return
        for hook in self.list_hooks():
            if hook.get("enabled") and name in hook.get("events", []):
                threading.Thread(target=self._run_hook, args=(hook, event), name="remchat-hook", daemon=True).start()

    def _run_hook(self, hook: dict[str, Any], event: dict[str, Any]) -> None:
        process: subprocess.Popen[str] | None = None
        scope: ProcessScope | None = None
        try:
            environment = program_environment(hook)
            environment["REMCHAT_EVENT_JSON"] = json.dumps(event, ensure_ascii=False)
            environment["REMCHAT_EVENT"] = event["event"]
            # 临时输出在项目数据盘，限制总量并只保留末尾，避免 communicate 累积无限输出。
            with tempfile.TemporaryFile(dir=self.store.directory) as output_file, tempfile.TemporaryFile(dir=self.store.directory) as error_file:
                with self.lock:
                    if self.closed:
                        return
                    scope = ProcessScope()
                    process = scope.launch(program_argv(hook["command"], hook["args"]), cwd=hook.get("cwd") or str(self.root), env=environment, stdout=output_file, stderr=error_file, text=True, encoding="utf-8", errors="replace")
                    self.running_hooks.add(scope)
                deadline = time.monotonic() + hook["timeout"]
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        scope.close(graceful=False)
                        raise RuntimeError("Hook 超时，已请求停止；若无法确认组归属请查看日志，可能留下后代。")
                    if os.fstat(output_file.fileno()).st_size + os.fstat(error_file.fileno()).st_size > MAX_HOOK_OUTPUT_BYTES:
                        scope.close(graceful=False)
                        raise RuntimeError("Hook 输出超过 4 MB，已请求停止；若无法确认组归属请查看日志，可能留下后代。")
                    try:
                        process.wait(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                def tail(file: Any) -> str:
                    file.seek(max(0, os.fstat(file.fileno()).st_size - 8000))
                    return file.read().decode("utf-8", errors="replace")[-4000:]
                self._publish({"type": "hook_result", "hook_id": hook["id"], "returncode": process.returncode, "message": tail(error_file) or tail(output_file) or "Hook 已运行。"})
        except Exception as error:
            self._publish({"type": "hook_error", "hook_id": hook["id"], "message": str(error)})
        finally:
            if scope:
                scope.close(graceful=False)
                with self.lock:
                    self.running_hooks.discard(scope)

    def register_task(self, name: str, **options: Any) -> dict[str, Any]:
        return self.monitor.register(name, **options)

    def tool_catalog(self) -> list[dict[str, Any]]:
        return self.tool_sessions.catalog()

    def connect_tool(self, tool_id: str, endpoint: str = "") -> dict[str, Any]:
        return self.tool_sessions.connect(tool_id, endpoint)

    def discover_tool_sessions(self, connection_id: str, workspace: str = "") -> list[dict[str, Any]]:
        return self.tool_sessions.discover(connection_id, workspace)

    def track_tool_sessions(self, connection_id: str, session_ids: list[str]) -> list[dict[str, Any]]:
        return self.tool_sessions.track(connection_id, session_ids)

    def disconnect_tool(self, connection_id: str) -> None:
        self.tool_sessions.disconnect(connection_id)

    def list_tool_connections(self) -> list[dict[str, Any]]:
        return self.tool_sessions.connections()

    def native_sync_status(self, tool_id=""):
        return self.tool_sessions.sync.status(tool_id)

    def cached_tool_sessions(self, connection_id, workspace=""):
        self.tool_sessions._source(connection_id)
        return self.tool_sessions.sync.catalog(connection_id, workspace)

    def tool_session_snapshot(self, connection_id, session_id):
        return self.tool_sessions.snapshot(connection_id, session_id)

    def watch_tool_session(self, connection_id, session_id, owner):
        self.tool_sessions._source(connection_id)
        self.tool_sessions.sync.watch(connection_id, session_id, owner)

    def unwatch_tool_sessions(self, owner):
        self.tool_sessions.sync.unwatch(owner)

    def bind_task_project(self, task_id: str, external_session_id: str = "", *,
                          adapter: str = "generic") -> dict[str, Any]:
        """Explicitly associate one registered monitor with the current project.

        Host session identity is metadata distinct from our observation ID;
        adapters remain responsible for filtering their native host events.
        Legacy/unbound task records still monitor normally, without producing
        project stages. No external tool is started or configured by binding.
        """
        if (not isinstance(external_session_id, str) or len(external_session_id) > 256
                or any(ord(char) < 32 for char in external_session_id)):
            raise ValueError("外部会话 ID 应为不含控制字符的字符串，最多 256 字符，可留空。")
        if not isinstance(adapter, str):
            raise ValueError("适配器名称应为字符串。")
        checked_id(adapter)
        # This lock order matches TaskMonitor.accept -> callback -> trace.
        # Persist a cloned row only; failed storage leaves no memory binding.
        with self.monitor.lock, self.project_trace.lock:
            if self.closed:
                raise RuntimeError("接入服务已关闭。")
            if not self.project_trace.enabled:
                raise ValueError("请先开启当前工作空间的项目观察，再关联监工任务。")
            row = self.store.get_item("tasks", task_id)
            if not row.get("monitoring", True):
                raise ValueError("此任务已取消监工，请登记新的监工任务后再关联。")
            if row.get("connection_id"):
                native_session = row.get("external_session_id", "")
                if external_session_id and native_session != external_session_id:
                    raise ValueError("选中任务与外部会话身份不匹配，不能关联其他会话。")
                if not project_workspace_matches(row, self.project_trace):
                    raise ValueError("此会话的工作目录与当前项目不同。请先选择该会话的项目工作空间，再聚焦；其他项目的文件改动不会混入。")
                external_session_id = native_session
            candidate = deepcopy(row)
            candidate["trace_binding"] = {
                "observation_session_id": self.project_trace.data["session_id"],
                "adapter": adapter,
                "external_session_id": external_session_id,
                "active": True,
            }
            if candidate.get("connection_id"):
                # The project recorder has one current plan. Keep every tool
                # ring running, but focus only this explicitly selected session.
                # Store the focus change atomically, retaining previous bindings
                # and project history instead of silently mixing two plans.
                with self.store.lock:
                    value = self.store.read()
                    for item in value["tasks"]:
                        previous = item.get("trace_binding", {})
                        if (item.get("connection_id") and item["id"] != task_id and
                                previous.get("observation_session_id") == self.project_trace.data["session_id"]):
                            previous["active"] = False
                    value["tasks"] = [candidate if item["id"] == task_id else item for item in value["tasks"]]
                    self.store.write(value)
                self.tool_sessions.focus_project(candidate)
            else:
                self.store.update_item("tasks", candidate)
            return deepcopy(candidate)

    def list_tasks(self) -> list[dict[str, Any]]:
        return sorted((self.tool_sessions.task_view(row) for row in self.store.read()["tasks"]),
                      key=lambda row: row.get("updated_at", 0), reverse=True)

    def list_adapters(self) -> list[dict[str, Any]]:
        return adapter_catalog()

    def export_adapter(self, identifier: str, task_id: str, *, session_id: str = "") -> dict[str, Any]:
        return export_adapter(self, identifier, task_id, session_id=session_id)

    def accept_task_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.monitor.accept(payload)

    def remove_task(self, identifier: str) -> None:
        self.remove_tasks([identifier])

    @staticmethod
    def _task_identifiers(identifiers: list[str]) -> list[str]:
        if not isinstance(identifiers, list) or any(not isinstance(item, str) for item in identifiers):
            raise ValueError("请选择有效的任务 ID 列表。")
        return list(dict.fromkeys(checked_id(item) for item in identifiers))

    def _guard_task_observation(self, identifiers: list[str]) -> dict[str, tuple[dict[str, int], int]]:
        """Capture generations before source IO; caller releases in finally."""
        with self.monitor.lock:
            result = {}
            for identifier in identifiers:
                entry = self._task_observation_guards.setdefault(identifier, {"generation": 0, "readers": 0})
                entry["readers"] += 1
                result[identifier] = (entry, entry["generation"])
            return result

    def _release_task_observation(self, guards: dict[str, tuple[dict[str, int], int]]) -> None:
        with self.monitor.lock:
            for identifier, (entry, _generation) in guards.items():
                entry["readers"] -= 1
                if not entry["readers"] and self._task_observation_guards.get(identifier) is entry:
                    self._task_observation_guards.pop(identifier, None)

    def _invalidate_task_observation(self, identifiers: list[str]) -> None:
        # Called after a successful settings write while holding monitor.lock.
        for identifier in identifiers:
            entry = self._task_observation_guards.get(identifier)
            if entry is not None:
                entry["generation"] += 1

    def remove_tasks(self, identifiers: list[str]) -> list[str]:
        """Remove only local monitor rows, atomically and with one backup.

        Native histories, external tasks, project evidence and the usage ledger
        are not removed. Invalid/missing IDs leave the complete batch intact.
        """
        identifiers = self._task_identifiers(identifiers)
        if not identifiers:
            return []
        with self.monitor.lock:
            with self.store.lock:
                value = self.store.read()
                existing = {row["id"] for row in value["tasks"]}
                if any(identifier not in existing for identifier in identifiers):
                    raise ValueError("部分任务已不存在，请刷新后重新选择；没有移除记录。")
                self.store.backup()
                selected = set(identifiers)
                value["tasks"] = [row for row in value["tasks"] if row["id"] not in selected]
                self.store.write(value)
                self._invalidate_task_observation(identifiers)
            # Keep publication ordered with source commits and native events.
            # Do not hold store.lock while a subscriber reads connection state.
            for identifier in identifiers:
                self._publish({"type": "task_removed", "task_id": identifier})
        return identifiers

    def cancel_monitoring(self, identifier: str) -> None:
        """用户主动停掉观察，不接管或取消外部宿主任务。"""
        self.cancel_monitoring_many([identifier])

    def cancel_monitoring_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        """Stop local observation, preserving the host state and recorded end."""
        identifiers = self._task_identifiers(identifiers)
        if not identifiers:
            return []
        with self.monitor.lock:
            with self.store.lock:
                value = self.store.read()
                by_id = {row["id"]: row for row in value["tasks"]}
                if any(identifier not in by_id for identifier in identifiers):
                    raise ValueError("部分任务已不存在，请刷新后重新选择；没有停止观察。")
                now = time.time()
                rows = [by_id[identifier] for identifier in identifiers]
                changed = [row for row in rows if row.get("monitoring", True)]
                if not changed:
                    self._invalidate_task_observation(identifiers)
                    return deepcopy(rows)
                self.store.backup()
                def stamp(item: Any) -> float | None:
                    if type(item) not in {int, float}:
                        return None
                    try:
                        result = float(item)
                    except (OverflowError, ValueError):
                        return None
                    return result if math.isfinite(result) and result > 0 else None
                for row in changed:
                    start = stamp(row.get("started_at"))
                    end = stamp(row.get("finished_at"))
                    if end is None or (start is not None and end < start):
                        end = stamp(row.get("observed_until"))
                    if end is None and row.get("source") in {"local_session_record", "claude_session_record", "antigravity_session_record"}:
                        detail = row.get("session_detail", {})
                        end = stamp(detail.get("last_activity")) if isinstance(detail, dict) else None
                        end = end or stamp(row.get("updated_at"))
                    row.update(monitoring=False, message="已停止观察；没有取消外部任务或删除外部会话。",
                               updated_at=now, observed_until=end or now)
                self.store.write(value)
                self._invalidate_task_observation(identifiers)
            for row in changed:
                self._publish({"type": "task", "task": row, "state": row.get("state", "unknown"),
                               "message": row["message"], "transition": False})
            return deepcopy(rows)

    def start_receiver(self, port: int = 0) -> dict[str, Any]:
        return self.monitor.start_receiver(port)

    def stop_receiver(self) -> None:
        self.monitor.stop_receiver()

    def resolve_hook_python(self, python_executable: str = "") -> str:
        """返回可执行入口，不通过已序列化的宿主命令字符串反解析。"""
        if not python_executable:
            from ..platforms import native_module
            executable = native_module("runtime").hook_python(self.root)
        else:
            executable = python_executable
        if not executable:
            raise ValueError("未找到 Python。请使用“复制任务接入代码”或填写可用 Python 路径。")
        return executable

    def claude_hook_config(self, task_id: str, python_executable: str = "", *,
                           notify_script: Path | None = None,
                           exec_form: bool = False) -> dict[str, Any]:
        """生成可检查的原生 Hook 配置，绝不写入外部 CLI 设置。"""

        self.store.get_item("tasks", task_id)
        executable = self.resolve_hook_python(python_executable)
        if notify_script is None:
            from .monitor_runtime import export_runtime
            folder = self.store.directory / "exports" / f"claude-hook-{checked_id(task_id)}-{time.time_ns()}"
            folder.mkdir(parents=True, exist_ok=False)
            try:
                export_runtime(folder / "runtime")
            except Exception:
                shutil.rmtree(folder)  # Only this newly created, checked-id export.
                raise
            notify_script = folder / "runtime/remcompanion/integrations/notify.py"
        script = notify_script
        args = [str(script), "--root", str(self.root), "--task", task_id, "--claude-hook"]
        if exec_form:
            handler = {"type": "command", "command": executable, "args": args, "timeout": 8}
            events = ("UserPromptSubmit", "Stop", "StopFailure")
        else:
            from ..platforms import native_module
            handler = {"type": "command", "command": native_module("shell").legacy_hook_command([executable, *args]), "timeout": 8}
            events = ("UserPromptSubmit", "Stop")
        return {"hooks": {name: [{"hooks": [deepcopy(handler)]}] for name in events}}

    def context(self, *, max_chars: int = 4000) -> str:
        """只把已启用 Skill 的说明交给聊天，绝不自动执行 Skill 文件。"""

        return self.skills.context(max_chars=max_chars)

    @staticmethod
    def draft_prompt(request: str) -> str:
        return f"""请为用户整理 RemChat 接入配置草稿，仅输出 JSON 对象，不执行命令、不猜测密钥。
对象可含 mcp（数组）、hooks（数组）。每项 id 是英文字母数字连字符；enabled 必须 false。
MCP protocol_version 默认 2025-11-25；若用户明确给出服务只支持新版，可设置 2026-07-28。
MCP 支持 stdio: {{\"id\":\"example\",\"transport\":\"stdio\",\"command\":\"node\",\"args\":[\"明确的入口.js\"],\"enabled\":false}}，
或 http: {{\"id\":\"example\",\"transport\":\"http\",\"url\":\"https://用户给出的地址/mcp\",\"headers_env\":{{\"Authorization\":\"用户的环境变量名\"}},\"enabled\":false}}。
Hook 字段 id/command/args/events/enabled。events 只能 task_started/task_completed/task_failed/task_cancelled/manual_test。
如果信息不足，输出 {{\"questions\":[\"具体缺少的信息\"]}}，不要编造下载地址、程序路径或凭据。
用户需求：\n{request[:12000]}"""

    def import_draft(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1])
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("草稿必须是 JSON 对象。")
        if value.get("questions"):
            raise ValueError("需先补充：" + "；".join(str(question) for question in value["questions"]))
        # 验证所有项后再落盘，避免后半项错误造成部分导入。
        for category in ("mcp", "hooks"):
            if not isinstance(value.get(category, []), list) or not all(isinstance(row, dict) for row in value.get(category, [])):
                raise ValueError(f"草稿的 {category} 应为对象数组。")
        mcp_rows = [validate_mcp({**row, "enabled": False}) for row in value.get("mcp", [])]
        hook_rows = [validate_hook({**row, "enabled": False}) for row in value.get("hooks", [])]
        if not mcp_rows and not hook_rows:
            raise ValueError("草稿中没有可导入的 mcp 或 hooks。")
        with self.store.lock:
            settings = self.store.read()
            for category, imported in (("mcp", mcp_rows), ("hooks", hook_rows)):
                merged = {row["id"]: row for row in settings[category]}
                merged.update({row["id"]: row for row in imported})
                settings[category] = list(merged.values())
            self.store.write(settings)
        for row in mcp_rows:
            self.disconnect_mcp(row["id"])
        return {"mcp": len(mcp_rows), "hooks": len(hook_rows), "enabled": False}

    def close(self) -> None:
        with self.lock:
            self.closed = True
            clients = list(self.clients.values())
            self.clients.clear()
            hooks = list(self.running_hooks)
            self.listeners.clear()
        for client in clients:
            client.close()
        for scope in hooks:
            scope.close(graceful=False)
        self.tool_sessions.close()
        self.project_trace.disable()
        self.monitor.close()
