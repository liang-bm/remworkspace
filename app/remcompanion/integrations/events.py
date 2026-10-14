"""接收真实任务事件的本机入口和文件观察器。

事件只更新已注册任务并通知 UI。这里不推测 LLM 的完成情况，不启动被监工软件。
"""

from __future__ import annotations

from collections import deque
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable
import uuid

from .storage import IntegrationStore


EVENT_STATES = {"started", "completed", "failed", "cancelled"}
ALIASES = {"start": "started", "done": "completed", "complete": "completed", "success": "completed", "error": "failed", "failure": "failed"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
RUN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")


def _recorded_time(value: Any) -> float | None:
    """Old records may not have timing evidence; never infer it from creation."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        recorded = float(value)
    except (OverflowError, ValueError):
        return None
    return recorded if math.isfinite(recorded) and recorded > 0 else None


def file_signature(path: Path) -> list[int] | None:
    try:
        info = path.stat()
        return [info.st_mtime_ns, info.st_size]
    except FileNotFoundError:
        return None


class TaskMonitor:
    def __init__(self, store: IntegrationStore, on_event: Callable[[dict[str, Any]], None]) -> None:
        self.store = store
        self.on_event = on_event
        self.stop_event = threading.Event()
        self.event_ids: deque[tuple[str, str]] = deque(maxlen=1000)
        self.error_keys: deque[str] = deque(maxlen=100)
        self.lock = threading.RLock()
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.watch_thread = threading.Thread(target=self._watch_files, name="remchat-task-files", daemon=True)
        self.watch_thread.start()
        self.token = ""
        self.project_handler = None

    def register(self, name: str, *, watch_path: str = "", file_mode: str = "marker", task_id: str = "") -> dict[str, Any]:
        if not name.strip():
            raise ValueError("请填写任务名称。")
        if file_mode not in {"marker", "json"}:
            raise ValueError("文件观察模式应为 marker 或 json。")
        path = Path(watch_path).expanduser().resolve() if watch_path.strip() else None
        if path and path.is_dir():
            raise ValueError("请选择一个完成标记文件或事件 JSON 文件，不是文件夹。")
        with self.lock:
            identifier = task_id or uuid.uuid4().hex[:16]
            if any(item.get("id") == identifier for item in self.store.read()["tasks"]):
                raise ValueError("该任务 ID 已登记，请使用其他 ID 或向已有任务发送事件。")
            now = time.time()
            row = {"id": identifier, "name": name.strip()[:160], "state": "started", "monitoring": True,
                   "message": "已登记，等待真实开始或完成事件。", "created_at": now, "updated_at": now,
                   "started_at": None, "finished_at": None, "run_id": "", "retired_run_ids": [],
                   "watch_path": str(path) if path else "", "file_mode": file_mode,
                   "signature": file_signature(path) if path else None}
            self.store.update_item("tasks", row)
            # Preserve the legacy row state/file observer contract, but registration
            # is not proof that the external task has started.
            self.on_event({"type": "task", "task": row, "state": "registered", "message": row["message"]})
            return row

    def accept(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("事件必须是 JSON 对象。")
        identifier = str(payload.get("task_id", payload.get("id", "")))
        state = str(payload.get("state", "")).lower()
        state = ALIASES.get(state, state)
        if state not in EVENT_STATES:
            raise ValueError("state 应为 started / completed / failed / cancelled。")
        event_id = str(payload.get("event_id", ""))[:200]
        requested_run = payload.get("run_id", "")
        if not isinstance(requested_run, str) or (requested_run and not RUN_ID.fullmatch(requested_run)):
            raise ValueError("run_id 应为 1–128 个字母、数字或 _.:-，也可省略。")
        with self.lock:
            row = self.store.get_item("tasks", identifier)
            if not row.get("monitoring", True):
                return row
            if event_id and (identifier, event_id) in self.event_ids:
                return row
            current_value = row.get("run_id", "")
            current_run = current_value if isinstance(current_value, str) and RUN_ID.fullmatch(current_value) else ""
            retired_value = row.get("retired_run_ids", [])
            retired_runs = (
                [value for value in retired_value if isinstance(value, str) and RUN_ID.fullmatch(value)][-32:]
                if isinstance(retired_value, list) else []
            )
            if requested_run in retired_runs:
                return row
            terminal = row.get("state") in TERMINAL_STATES
            # Explicit run identities prevent late results from ending a new run.
            # Older adapters without run_id retain their terminal -> started restart.
            if requested_run and current_run and requested_run != current_run and state != "started":
                return row
            if terminal and (state != "started" or (requested_run and requested_run == current_run)):
                return row
            now = time.time()
            previous_started = _recorded_time(row.get("started_at"))
            new_run = state == "started" and (terminal or bool(requested_run and current_run and requested_run != current_run))
            transition = row.get("state") != state or (state == "started" and (new_run or previous_started is None))
            row["started_at"] = previous_started
            if state == "started":
                if new_run:
                    if current_run:
                        retired_runs.append(current_run)
                    row["run_id"] = requested_run or uuid.uuid4().hex
                    row["started_at"] = now
                else:
                    row["run_id"] = current_run or requested_run or uuid.uuid4().hex
                    if previous_started is None:
                        row["started_at"] = now
                row["finished_at"] = None
                row.pop("observed_until", None)
            else:
                row["run_id"] = current_run or requested_run or uuid.uuid4().hex
                row["finished_at"] = now
            row["retired_run_ids"] = list(dict.fromkeys(retired_runs))[-32:]
            row.update({"state": state, "message": str(payload.get("message", ""))[:4000], "updated_at": now})
            self.store.update_item("tasks", row)
            if event_id:
                self.event_ids.append((identifier, event_id))
            self.on_event({"type": "task", "task": row, "state": state, "message": row["message"], "transition": transition})
            return row

    def start_receiver(self, port: int = 0) -> dict[str, Any]:
        """只绑定 loopback，随机 token，拒绝浏览器跨来源请求。"""

        if self.server:
            return self.receiver_info()
        if not 0 <= int(port) <= 65535:
            raise ValueError("监听端口需要位于 0–65535，0 表示自动选择空闲端口。")
        self.store.directory.mkdir(parents=True, exist_ok=True)
        token_path = self.store.directory / "receiver-token.txt"
        if token_path.exists():
            self.token = token_path.read_text(encoding="utf-8").strip()
        if len(self.token) < 32:
            self.token = secrets.token_urlsafe(32)
            token_path.write_text(self.token, encoding="utf-8")
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path not in {"/events", "/project-events"}:
                    self.reply(404, {"error": "unknown endpoint"})
                    return
                origin = self.headers.get("Origin", "")
                if origin:
                    self.reply(403, {"error": "browser origins are not accepted"})
                    return
                expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                if self.headers.get("Host", "") not in expected_hosts:
                    self.reply(403, {"error": "invalid host"})
                    return
                authorization = self.headers.get("Authorization", "")
                if not hmac.compare_digest(authorization, "Bearer " + monitor.token):
                    self.reply(401, {"error": "invalid token"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 65536:
                        self.reply(413, {"error": "event body must be 1–65536 bytes"})
                        return
                    payload = json.loads(self.rfile.read(size))
                    if self.path == "/project-events":
                        if monitor.project_handler is None:
                            raise ValueError("项目过程接收器未配置")
                        self.reply(200, {"ok": True, **monitor.project_handler(payload)})
                    else:
                        row = monitor.accept(payload)
                        self.reply(200, {"ok": True, "task_id": row["id"], "state": row["state"]})
                except (ValueError, OSError) as error:
                    self.reply(400, {"error": str(error)})

            def do_GET(self) -> None:
                self.reply(405, {"error": "POST /events is required"})

            def reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

        self.server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="remchat-task-http", daemon=True)
        self.server_thread.start()
        self._write_receiver_state()
        return self.receiver_info()

    def receiver_info(self) -> dict[str, Any]:
        return {"url": f"http://127.0.0.1:{self.server.server_port}/events" if self.server else "", "running": bool(self.server), "token_path": str(self.store.directory / "receiver-token.txt")}

    def stop_receiver(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.server_thread:
            self.server_thread.join(timeout=2)
            self.server_thread = None
        if (self.store.directory / "receiver.json").exists():
            self._write_receiver_state()

    def _write_receiver_state(self) -> None:
        # 不含 token 的运行态描述，便于 CLI helper 找到本次自动选择的端口。
        value = {key: item for key, item in self.receiver_info().items() if key != "token"}
        path = self.store.directory / "receiver.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _watch_files(self) -> None:
        while not self.stop_event.wait(1):
            try:
                tasks = self.store.read()["tasks"]
            except (OSError, ValueError) as error:
                self._report_error(error)
                continue
            for row in tasks:
                if self.stop_event.is_set():
                    return
                try:
                    self._process_file(row)
                except (OSError, ValueError) as error:
                    self._report_error(error)

    def _report_error(self, error: Exception) -> None:
        key = hashlib.sha256(str(error).encode()).hexdigest()
        if key not in self.error_keys:
            self.error_keys.append(key)
            self.on_event({"type": "monitor_error", "message": str(error)})

    def _process_file(self, snapshot: dict[str, Any]) -> None:
        if not snapshot.get("monitoring", True) or snapshot.get("state") != "started" or not snapshot.get("watch_path"):
            return
        with self.lock:
            row = self.store.get_item("tasks", snapshot["id"])
            if not row.get("monitoring", True) or row.get("state") != "started":
                return
            path = Path(row["watch_path"])
            signature = file_signature(path)
            if signature is None or signature == row.get("signature"):
                return
            payload: dict[str, Any] = {"task_id": row["id"], "state": "completed", "message": f"完成标记已更新：{path.name}"}
            if row.get("file_mode") == "json":
                if path.stat().st_size > 65536:
                    raise ValueError(f"任务事件文件超过 64 KB：{path.name}")
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(value, dict):
                    raise ValueError("任务事件文件不是 JSON 对象。")
                payload = {**value, "task_id": row["id"]}
            updated = self.accept(payload)
            # 验证和处理成功后才消费此次文件更新；无效或写到一半的 JSON 会下轮重试。
            updated["signature"] = signature
            self.store.update_item("tasks", updated)

    def close(self) -> None:
        self.stop_event.set()
        self.stop_receiver()
        self.watch_thread.join(timeout=2)
