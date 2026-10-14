"""Read-only local tool session discovery on Windows.

The native transport only launches a *proxy* to an existing service. It cannot
start/resume a thread, answer approval requests, or change host configuration.
Codex defaults to clearly labelled local records. Public human prompts and the
structured update_plan and explicit assistant final reports are bounded allowlists;
never reasoning, analysis/unspecified-channel assistant prose,
images, shell/wrapper arguments, or tool output.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
from typing import Any

from ...project_trace.recorder import _redact
from .codex_catalog import metadata as codex_metadata, classify_source, public_history, public_tools, public_turn, header as codex_header, workspace_path

MAX_LINE = 4 * 1024 * 1024
HEADER_BYTES = 64 * 1024
TAIL_BYTES = 256 * 1024
MAX_SESSIONS = 4096
MAX_FILES = 4096
MAX_TOOL_ITEMS = 128
ANCHOR_BYTES = 128
RPC_TIMEOUT = 5.0
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
READ_METHODS = frozenset({"initialize", "thread/loaded/list", "thread/list",
                          "thread/read", "thread/turns/list", "thread/goal/get",
                          "thread/unsubscribe"})
# Only item variants verified in the local public event schema. Do not infer
# support for MCP/skills from unverified names, or expose command/path/output.
RECORD_TOOL_TYPES = {"CommandExecution": "command", "ImageView": "other",
                     "SubAgentActivity": "other"}


def catalog() -> list[dict]:
    entries = [
        ("codex", "Codex", True,
         "默认只读本机 .codex\\sessions；可选择 .codex 根、sessions 或日目录。记录不等同于实时服务，缺少开始证据时不估算时长。"),
        ("claude_code", "Claude Code", True, "只读 Claude Code 本机会话记录；未确认的当前状态与时长保持未知，已有 Hook 接入仍兼容。"),
        ("antigravity", "Antigravity", True, "只读本机存档与已验证 SQLite 生成计量（近期有界读取）；Token 不等同于额度。当前状态、工作区与时长未上报时保持未知。"),
        ("opencode", "OpenCode", False, "尚未实现现有服务会话发现；已有插件草稿仍需手动安装。"),
        ("chatgpt", "ChatGPT", False, "没有已验证的本机实时会话发现接口。"),
        ("zcode", "ZCode", False, "开始 Hook 不足以发现和确认多会话状态。"),
        ("deepseek_harness", "DeepSeek Harness", False, "尚未实现可信的现有会话发现接口。"),
        ("hermes", "Hermes Agent", False, "Gateway Hook 不等同于当前 CLI 会话发现。"),
    ]
    defaults = {"codex": str(_home() / ".codex/sessions"),
                "claude_code": str(_home() / ".claude/projects")}
    try:
        from .antigravity_sessions import default_root
        defaults["antigravity"] = str(default_root())
    except ImportError:
        # A partially updated source tree can still display the intended root;
        # no source is constructed and no archive is scanned by the catalog.
        gemini = _home() / ".gemini"
        defaults["antigravity"] = str(gemini / ("antigravity-ide" if (gemini / "antigravity-ide").is_dir()
                                               or not (gemini / "antigravity").is_dir() else "antigravity"))
    return [{"id": key, "name": name, "supported": supported, "description": description,
             "default_location": defaults.get(key, ""),
             "connection_hint": ("留空使用本机记录；输入 .codex 根会仅使用其中的 sessions。高级原生服务需显式选择 native。"
                                 if key == "codex" else "留空使用默认记录位置；输入路径时仅读取明确选择的记录目录。" if supported else "")}
            for key, name, supported, description in entries]


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()).resolve()


def _time(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            result = float(value)
        except (ValueError, OverflowError):
            return None
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    else:
        return None
    return result if math.isfinite(result) and result > 0 else None


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _workspace_key(value: str) -> str:
    """Equivalent normal and extended-length Windows directory spellings."""
    if value[:8].casefold() == "\\\\?\\unc\\":
        value = "\\\\" + value[8:]
    elif re.match(r"^\\\\\?\\[A-Za-z]:[\\/]", value):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _unix_ms(value: Any) -> float | None:
    """The verified item event's *_at_ms fields are numeric Unix milliseconds."""
    if type(value) not in {int, float}:
        return None
    stamp = _time(value)
    if stamp is None:
        return None
    stamp /= 1000
    try:
        datetime.fromtimestamp(stamp)
    except (ValueError, OverflowError, OSError):
        return None
    return stamp


def _state(status: Any) -> str:
    if isinstance(status, dict):
        status = status.get("type")
    if not isinstance(status, str):
        return "unknown"
    return {"active": "started", "inProgress": "started", "completed": "completed",
            "failed": "failed", "interrupted": "cancelled", "idle": "idle",
            "systemError": "failed"}.get(status, "unknown")


def _base(identifier: str, *, source: str) -> dict:
    return {"id": identifier, "name": "Codex " + identifier[:12], "workspace": "",
            "state": "unknown", "started_at": None, "finished_at": None,
            "run_id": "", "model": "", "usage": {}, "usage_time": None, "usage_model": "", "plan": [], "requests": [],
            "tools": [], "goal": None, "memory": None, "message": "", "source": source,
            "last_activity": None, "created_at": None, "timeline": [], "checkpoints": [],
            "session_kind": "unknown", "parent_thread_id": "", "subagents": [], "usage_events": []}


def _human_input(text: str) -> str:
    """Exclude exact host-injected envelopes in legacy role=user records.

    This is a fallback, not a keyword classifier: a real request quoting these
    terms stays visible. The indexed native userMessage catalog is preferred.
    """
    stripped = text.lstrip()
    generated = ("# AGENTS.md instructions\n", "# AGENTS.md instructions\r\n",
                 "<environment_context>", "<permissions instructions>",
                 "<collaboration_mode>", "<subagent_notification>",
                 "The following is the Codex agent history where request action you")
    return "" if stripped.startswith(generated) else text


def _usage(value: Any, *, scope: str, update_id: str) -> dict:
    if not isinstance(value, dict):
        return {}
    fields = {"inputTokens": "input_tokens", "cachedInputTokens": "cached_input_tokens",
              "outputTokens": "output_tokens", "reasoningOutputTokens": "reasoning_output_tokens",
              "totalTokens": "total_tokens"}
    result = {}
    for native, normalized in fields.items():
        number = value.get(native, value.get(normalized))
        if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            result[normalized] = number
    return {**result, "scope": scope, "update_id": update_id} if result else {}


def _json_value_end(raw: bytes, position: int) -> int:
    """Skip a JSON value structurally, without decoding private string bodies."""
    if position >= len(raw):
        raise ValueError("Incomplete record")
    if raw[position] == 34:
        position += 1
        while position < len(raw):
            if raw[position] == 92:
                position += 2
            elif raw[position] == 34:
                return position + 1
            else:
                position += 1
        raise ValueError("Incomplete string")
    if raw[position] in (123, 91):
        stack = [125 if raw[position] == 123 else 93]
        position += 1
        while position < len(raw):
            token = raw[position]
            if token == 34:
                position = _json_value_end(raw, position)
                continue
            if token in (123, 91):
                if len(stack) >= 64:
                    raise ValueError("Record nesting too deep")
                stack.append(125 if token == 123 else 93)
            elif token in (125, 93):
                if token != stack.pop():
                    raise ValueError("Invalid record structure")
                if not stack:
                    return position + 1
            position += 1
        raise ValueError("Incomplete container")
    start = position
    while position < len(raw) and raw[position] not in b",]} \t\r\n":
        position += 1
    if position == start:
        raise ValueError("Missing record value")
    return position


def _json_public_fields(raw: bytes, allowed: set[str]) -> dict[str, bytes]:
    """Raw top-level public fields only; nested args/output never become fields."""
    position, result = 0, {}
    while position < len(raw) and raw[position] in b" \t\r\n":
        position += 1
    if position >= len(raw) or raw[position] != 123:
        return result
    position += 1
    while position < len(raw):
        while position < len(raw) and raw[position] in b" \t\r\n":
            position += 1
        if position >= len(raw):
            raise ValueError("Incomplete object")
        if raw[position] == 125:
            return result
        if raw[position] != 34:
            raise ValueError("Invalid object key")
        key_end = _json_value_end(raw, position)
        key = json.loads(raw[position:key_end]) if key_end - position <= 64 else None
        position = key_end
        while position < len(raw) and raw[position] in b" \t\r\n":
            position += 1
        if position >= len(raw) or raw[position] != 58:
            raise ValueError("Missing object separator")
        position += 1
        while position < len(raw) and raw[position] in b" \t\r\n":
            position += 1
        value_end = _json_value_end(raw, position)
        if key in allowed:
            if key in result:
                raise ValueError("Ambiguous public field")
            result[key] = raw[position:value_end]
        position = value_end
        while position < len(raw) and raw[position] in b" \t\r\n":
            position += 1
        if position < len(raw) and raw[position] == 44:
            position += 1
            continue
        if position < len(raw) and raw[position] == 125:
            return result
        raise ValueError("Invalid object separator")
    raise ValueError("Incomplete object")


def _public_string(fields: dict[str, bytes], key: str, limit: int = 256) -> str:
    raw = fields.get(key, b"null").strip()
    if not raw.startswith(b'"') or len(raw) > limit * 6 + 2:
        return ""
    value = json.loads(raw)
    return value if isinstance(value, str) and len(value) <= limit else ""


def _public_plan(raw: bytes) -> list[dict] | None:
    """Only the public update_plan's step/status cells, not arbitrary tool args."""
    if len(raw) > HEADER_BYTES:
        return None
    if raw.lstrip().startswith(b'"'):
        decoded = json.loads(raw)
        if not isinstance(decoded, str):
            return None
        raw = decoded.encode("utf-8")
    fields = _json_public_fields(raw, {"plan"})
    cells = fields.get("plan", b"").strip()
    if not cells.startswith(b"["):
        return None
    position, result = 1, []
    while position < len(cells):
        while position < len(cells) and cells[position] in b" ,\t\r\n":
            position += 1
        if position >= len(cells) or cells[position] == 93:
            break
        end = _json_value_end(cells, position)
        item = _json_public_fields(cells[position:end], {"step", "status"})
        title, state = _public_string(item, "step", 1000), _public_string(item, "status", 32)
        if title.strip() and state in {"pending", "in_progress", "completed"}:
            result.append({"id": "plan-" + str(len(result)), "title": _redact(title)[:200], "state": state})
        position = end
        if len(result) >= 60:
            break
    return result


def _public_final_text(raw: bytes, text_types=frozenset({"output_text", "text"})) -> str:
    """Text cells of an explicitly public final message only, never analysis."""
    raw = raw.strip()
    if not raw.startswith(b"["):
        return ""
    position, texts = 1, []
    while position < len(raw) and len(texts) < 20:
        while position < len(raw) and raw[position] in b" ,\t\r\n":
            position += 1
        if position >= len(raw) or raw[position] == 93:
            break
        end = _json_value_end(raw, position)
        item = _json_public_fields(raw[position:end], {"type", "text"})
        if _public_string(item, "type", 40) in text_types:
            text = _public_string(item, "text", 16000)
            if text:
                texts.append(_redact(text))
        position = end
    return "\n".join(texts)[:12000]


def _public_event(row: dict, event: dict) -> None:
    events = row.setdefault("timeline", [])
    identifier = event.get("id") or hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if not any(old.get("id") == identifier for old in events):
        events.append(dict(event, id=identifier))
        del events[:-200]


def _record_names(candidates: dict[str, str]) -> dict[str, str]:
    """Public DB metadata only, indexed by IDs already discovered in records."""
    candidates = {key: value for key, value in list(candidates.items())[:MAX_SESSIONS]
                  if isinstance(key, str) and SESSION_ID.fullmatch(key) and workspace_path(value)}
    if not candidates:
        return {}
    metadata_home = _home() / ".codex"
    result = {}
    deadline = time.monotonic() + 1
    try:
        versions = []
        for path in metadata_home.glob("state_*.sqlite"):
            match = re.fullmatch(r"state_([0-9]+)\.sqlite", path.name)
            if match:
                versions.append((int(match[1]), path))
        for _version, path in sorted(versions, reverse=True)[:8]:
            if time.monotonic() >= deadline:
                break
            connection = None
            try:
                resolved = path.resolve()
                resolved.relative_to(metadata_home.resolve())
                connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True,
                                             timeout=max(0.001, deadline - time.monotonic()))
                connection.set_progress_handler(lambda: time.monotonic() >= deadline, 100)
                columns = connection.execute("PRAGMA table_info(threads)").fetchall()
                schema = {column[1]: column[2].upper() for column in columns}
                primary = [column[1] for column in columns if column[5]]
                if (primary != ["id"] or schema.get("id") != "TEXT" or schema.get("cwd") != "TEXT"
                        or not any(schema.get(key) == "TEXT" for key in ("name", "title"))):
                    continue  # Unknown schema must not trigger a history scan.
                pending = [key for key in candidates if key not in result]
                if not pending:
                    break
                # Column names are code constants, not values from the DB. All
                # values are parameters, and the unique ID key bounds the read.
                # A truncated directory could equal another project's valid
                # shorter path. Keep bounded original paths, otherwise unknown.
                fields = ["id", "CASE WHEN length(cwd)<=4000 AND instr(cwd,char(0))=0 THEN cwd ELSE NULL END"]
                fields += [f"substr({key}, 1, 160)" if schema.get(key) == "TEXT" else "NULL"
                           for key in ("name", "title")]
                query = "SELECT " + ", ".join(fields) + " FROM threads WHERE id IN (" + ",".join("?" for _ in pending) + ")"
                for identifier, cwd, name, title in connection.execute(query, pending).fetchmany(MAX_SESSIONS):
                    if identifier not in candidates or not workspace_path(cwd):
                        continue
                    if _workspace_key(cwd) != _workspace_key(candidates[identifier]):
                        continue
                    visible = _text(name) or _text(title)
                    if visible:
                        result[identifier] = visible
            except (OSError, ValueError, sqlite3.Error, OverflowError):
                continue  # Best-effort names must never block record discovery.
            finally:
                if connection is not None:
                    connection.close()
    except (OSError, ValueError, OverflowError):
        pass
    return result


class RpcRequestError(RuntimeError):
    """One rejected RPC request, not evidence that the transport is disconnected."""
    request_error = True


class _Proxy:
    """Bounded WebSocket RPC over the existing raw stdio-to-socket proxy.

    Control sockets use HTTP Upgrade and RFC6455 frames, not the JSONL format
    used by an app-server launched with stdio://. Never start such a new server
    as a substitute for attaching to the user's existing one.
    """
    def __init__(self, executable: Path, endpoint: Path, home: Path):
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.incoming: queue.Queue = queue.Queue(maxsize=64)
        self.ready = threading.Event()
        self.closed = False
        self.error = ""
        self.sequence = 0
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(home)
        self.process = subprocess.Popen(
            [str(executable), "app-server", "proxy", "--sock", str(endpoint)],
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.expected_accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        self.reader = threading.Thread(target=self._read, name="remchat-codex-proxy", daemon=True)
        self.reader.start()
        try:
            request = ("GET / HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\n"
                       "Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
                       f"Sec-WebSocket-Key: {key}\r\n\r\n")
            self.process.stdin.write(request.encode("ascii"))
            self.process.stdin.flush()
            if not self.ready.wait(RPC_TIMEOUT):
                raise TimeoutError("已有 Codex 服务没有回应 WebSocket 握手；未启动新服务。")
            if self.error:
                raise RuntimeError(self.error)
            self.call("initialize", {"clientInfo": {"name": "remchat_session_observer", "version": "1"},
                                     "capabilities": {"experimentalApi": True}})
            self._write({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def _exact(self, size: int) -> bytes:
        value = self.process.stdout.read(size)
        if len(value) != size:
            raise OSError("Codex 会话连接已关闭。")
        return value

    def _send_frame(self, body: bytes, opcode: int = 1):
        if len(body) > HEADER_BYTES:
            raise ValueError("会话读取请求过大。")
        mask = os.urandom(4)
        length = len(body)
        if length < 126:
            head = bytes([0x80 | opcode, 0x80 | length])
        elif length <= 65535:
            head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", length)
        else:
            head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", length)
        framed = head + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(body))
        with self.send_lock:
            if self.closed or self.process.poll() is not None:
                raise OSError("Codex 会话代理已关闭。")
            self.process.stdin.write(framed)
            self.process.stdin.flush()

    def _read(self):
        try:
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                if len(header) >= 8192:
                    raise ValueError("Codex WebSocket 握手响应过大。")
                header.extend(self._exact(1))
            lines = header.decode("ascii").split("\r\n")
            if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
                raise ValueError("Codex 已有服务拒绝 WebSocket 握手。")
            fields = {key.strip().lower(): value.strip() for line in lines[1:] if ":" in line
                      for key, value in [line.split(":", 1)]}
            if (fields.get("upgrade", "").lower() != "websocket"
                    or "upgrade" not in [part.strip() for part in fields.get("connection", "").lower().split(",")]
                    or not hmac.compare_digest(fields.get("sec-websocket-accept", ""), self.expected_accept)):
                raise ValueError("Codex WebSocket 握手校验失败。")
            self.ready.set()
            message = bytearray()
            fragmented = False
            while not self.closed:
                first, second = self._exact(2)
                opcode, final = first & 15, bool(first & 128)
                length = second & 127
                if first & 0x70 or second & 128:
                    raise ValueError("Codex WebSocket 帧无效。")
                if length == 126:
                    length = struct.unpack("!H", self._exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._exact(8))[0]
                if length > MAX_LINE or (opcode < 8 and len(message) + length > MAX_LINE):
                    raise ValueError("Codex 会话响应超过 4 MiB；已停止观察，不影响宿主。")
                if opcode >= 8 and (not final or length > 125):
                    raise ValueError("Codex WebSocket 控制帧无效。")
                body = self._exact(length)
                if opcode == 8:
                    raise OSError("Codex 会话服务已关闭连接。")
                if opcode == 9:
                    self._send_frame(body, opcode=10)
                    continue
                if opcode == 10:
                    continue
                if opcode == 1:
                    if fragmented:
                        raise ValueError("Codex WebSocket 分片顺序无效。")
                    message = bytearray(body)
                    fragmented = not final
                elif opcode == 0 and fragmented:
                    message.extend(body)
                    fragmented = not final
                else:
                    raise ValueError("Codex WebSocket 仅支持文本 JSON 响应。")
                if not final:
                    continue
                try:
                    value = json.loads(message)
                except (ValueError, UnicodeError):
                    message.clear()
                    continue
                message.clear()
                # Drop unsolicited events/approval requests and all their contents.
                # An observer must never answer a host permission request.
                if isinstance(value, dict) and "id" in value and ("result" in value or "error" in value):
                    try:
                        self.incoming.put_nowait(value)
                    except queue.Full:
                        self.error = "Codex 会话响应队列已满；请重新连接。"
                        break
        except (OSError, ValueError, UnicodeError) as error:
            self.error = str(error) or "Codex 会话连接读取失败。"
        finally:
            self.ready.set()

    def _write(self, value: dict):
        if self.closed or self.process.poll() is not None or self.error:
            raise RuntimeError(self.error or "Codex 会话连接不可用。")
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_frame(body)

    def call(self, method: str, params: dict) -> dict:
        if method not in READ_METHODS:
            raise ValueError("会话观察只允许白名单中的只读方法。")
        if method == "thread/read" and params.get("includeTurns") is not False:
            raise ValueError("会话观察不允许完整历史读取。")
        if method == "thread/list" and params.get("useStateDbOnly") is not True:
            raise ValueError("会话发现不允许扫描或修复完整历史。")
        if method == "thread/turns/list" and params.get("itemsView") != "notLoaded":
            raise ValueError("回合观察只读取元数据，不读取消息或工具正文。")
        with self.lock:
            self.sequence += 1
            identifier = self.sequence
            self._write({"id": identifier, "method": method, "params": params})
            deadline = time.monotonic() + RPC_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    value = self.incoming.get(timeout=max(0.001, min(0.2, deadline - time.monotonic())))
                except queue.Empty:
                    if self.error or self.process.poll() is not None:
                        raise RuntimeError(self.error or "Codex 会话连接已退出。")
                    continue
                if value.get("id") != identifier:
                    continue
                if "error" in value:
                    raise RpcRequestError("Codex 拒绝此次只读会话请求（" + method + "）。")
                result = value.get("result")
                if not isinstance(result, dict):
                    raise ValueError("Codex 会话接口返回了无效对象。")
                return result
            raise TimeoutError("Codex 已有服务没有回应只读请求；未启动新服务。")

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                # Only our proxy is owned here; never terminate the host daemon.
                self.process.kill()
                self.process.wait(timeout=1)
            self.process.stdout.close()
        if self.reader is not threading.current_thread():
            self.reader.join(timeout=1)


class CodexNativeSource:
    name = "Codex 原生服务"
    source = "codex_app_server"
    description = "只读连接已有 Codex 服务；仅列出该服务实际加载的会话。"
    def __init__(self, executable: Path, endpoint: Path, home: Path):
        self.proxy = _Proxy(executable, endpoint, home)
        self.location = str(endpoint.resolve())
        self.known: set[str] = set()
        self.lock = threading.RLock()

    def discover(self, workspace: str = "") -> list[dict]:
        if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
            raise ValueError("工作空间路径无效。")
        with self.lock:
            result, cursor = [], None
            for _page in range(4):
                response = self.proxy.call("thread/loaded/list", {"limit": 32, "cursor": cursor})
                for identifier in response.get("data", [])[:32]:
                    if not isinstance(identifier, str) or not SESSION_ID.fullmatch(identifier):
                        continue
                    native = self.proxy.call("thread/read", {"threadId": identifier, "includeTurns": False}).get("thread", {})
                    if not isinstance(native, dict) or native.get("id") != identifier:
                        continue
                    lineage = classify_source(native)
                    if not lineage["is_main"]:
                        continue
                    cwd = workspace_path(native.get("cwd"))
                    if workspace and _workspace_key(cwd) != _workspace_key(workspace):
                        continue
                    self.known.add(identifier)
                    result.append({"id": identifier, "name": _text(native.get("name")) or "Codex " + identifier[:12],
                                   "workspace": cwd, "state": _state(native.get("status")),
                                   "model": _text(native.get("model")), "source": "codex_app_server", **lineage})
                cursor = response.get("nextCursor")
                if not cursor:
                    break
            return result

    def snapshot(self, session_id: str) -> dict:
        with self.lock:
            if session_id not in self.known:
                raise ValueError("请先发现并选择该服务实际加载的会话。")
            native = self.proxy.call("thread/read", {"threadId": session_id, "includeTurns": False}).get("thread", {})
            if not isinstance(native, dict) or native.get("id") != session_id:
                raise ValueError("Codex 返回的会话身份与所选会话不同。")
            row = _base(session_id, source="codex_app_server")
            row.update(name=_text(native.get("name")) or row["name"], workspace=workspace_path(native.get("cwd")),
                       model=_text(native.get("model")), state=_state(native.get("status")))
            status = native.get("status")
            if isinstance(status, dict) and status.get("type") == "notLoaded":
                row["message"] = "该服务未加载此会话；历史回合不代表其他私有实例中的当前状态。"
                return row
            try:
                turns = self.proxy.call("thread/turns/list", {"threadId": session_id, "limit": 1,
                                                            "sortDirection": "desc", "itemsView": "notLoaded"}).get("data", [])
                turn = turns[0] if turns and isinstance(turns[0], dict) else {}
                row.update(run_id=_text(turn.get("id"), 256), started_at=_time(turn.get("startedAt")),
                           finished_at=_time(turn.get("completedAt")))
                # A currently active runtime always wins over an older terminal turn.
                if row["state"] == "started" and turn.get("status") != "inProgress":
                    row.update(run_id="", started_at=None, finished_at=None)
                elif row["state"] == "idle" and turn:
                    row["state"] = _state(turn.get("status"))
                elif row["state"] == "unknown" and turn:
                    row["state"] = _state(turn.get("status"))
            except RuntimeError:
                row["message"] = "宿主不支持回合元数据；状态来自实时服务，时长未知。"
            try:
                goal = self.proxy.call("thread/goal/get", {"threadId": session_id}).get("goal")
                if isinstance(goal, dict):
                    row["goal"] = _text(goal.get("objective"), 12000) or None
            except RuntimeError:
                pass
            if not row["message"]:
                row["message"] = "已有 Codex 服务的真实会话状态；未启动或接管模型。"
            return row

    def close(self):
        self.proxy.close()


class CodexRecordSource:
    """Bounded, incremental metadata reader; missing turn starts remain unknown."""
    name = "Codex 本机会话记录"
    source = "local_session_record"
    description = "有界公开指令、计划、明确 final 报告与工具元数据；不读取模型思考、analysis 或工具输出。"
    def __init__(self, directory: Path, *, message: str = ""):
        self.directory = directory.resolve()
        self.location = str(self.directory)
        self.data_directory = self.location
        self.description = "只读 Codex 本机记录：" + self.location + "。不是实时服务；缺少回合开始证据时不估算时长。"
        self.message = message
        self.paths: dict[str, Path] = {}
        self.rows: dict[str, dict] = {}
        self.offsets: dict[str, tuple[int, int, int]] = {}
        self.call_names: dict[str, dict[str, str]] = {}
        self.item_types: dict[str, dict[str, str]] = {}
        self.lock = threading.RLock()
        self.closed = False
        self.catalog_message = ""

    def _header(self, path: Path) -> dict:
        return codex_header(path)

    def discover(self, workspace: str = "") -> list[dict]:
        if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
            raise ValueError("工作空间路径无效。")
        with self.lock:
            if self.closed:
                raise RuntimeError("会话记录观察已关闭。")
            catalog = codex_metadata(self.directory, limit=MAX_SESSIONS, workspace=workspace)
            self.catalog_message = catalog.get("catalog_message", "")
            if catalog.get("database_available") or catalog.get("rows"):
                result = []
                for entry in catalog["rows"]:
                    identifier = entry["id"]
                    metadata_only = entry.get("metadata_only") is True
                    try:
                        path = Path(entry["rollout_path"]).resolve()
                        meta = self._header(path) if not metadata_only else {}
                    except (OSError, ValueError):
                        continue
                    if not metadata_only and ((meta.get("id") or meta.get("session_id")) != identifier or not classify_source(meta)["is_main"]):
                        continue
                    self.paths[identifier] = None if metadata_only else path
                    row = self.rows.setdefault(identifier, _base(identifier, source=self.source))
                    row.update(name=_text(entry.get("name")) or row["name"], workspace=workspace_path(entry.get("cwd")),
                               created_at=_time(entry.get("created_at")), model=_text(entry.get("model")) or row["model"],
                               updated_at=_time(entry.get("updated_at")), metadata_only=metadata_only,
                               session_kind="user", parent_thread_id="", subagents=entry.get("children", []),
                               child_count=entry.get("child_count", 0), message=self.catalog_message)
                    result.append({key: row.get(key) for key in ("id", "name", "workspace", "state", "model", "source", "created_at", "updated_at", "last_activity", "metadata_only", "session_kind", "parent_thread_id", "child_count")})
                return result
            candidates = []
            paths = [self.directory] if self.directory.is_file() else self.directory.rglob("*.jsonl")
            for index, path in enumerate(paths):
                if index >= MAX_FILES:
                    break
                try:
                    resolved = path.resolve()
                    if self.directory.is_dir():
                        resolved.relative_to(self.directory)
                    candidates.append((resolved.stat().st_mtime_ns, resolved))
                except (OSError, ValueError):
                    continue
            result = []
            needs_name = {}
            children = []
            for _modified, path in sorted(candidates, reverse=True):
                try:
                    meta = self._header(path)
                except OSError:
                    continue
                if not isinstance(meta, dict):
                    continue
                identifier = meta.get("id") or meta.get("session_id")
                if not isinstance(identifier, str) or not SESSION_ID.fullmatch(identifier):
                    continue
                lineage = classify_source(meta)
                if not lineage["is_main"]:
                    children.append({"id": identifier, "name": _text(meta.get("agent_nickname")) or identifier[:12],
                                     "model": _text(meta.get("model")), "created_at": _time(meta.get("timestamp")),
                                     "state": "unknown", **lineage})
                    continue
                if len(result) >= MAX_SESSIONS:
                    continue  # Classify before capping, so children cannot crowd out parents.
                cwd = workspace_path(meta.get("cwd"))
                if workspace and _workspace_key(cwd) != _workspace_key(workspace):
                    continue
                self.paths[identifier] = path
                row = self.rows.setdefault(identifier, _base(identifier, source="local_session_record"))
                row.update(workspace=cwd, **lineage)
                row["created_at"] = _time(meta.get("timestamp"))
                if not _text(meta.get("agent_nickname")):
                    needs_name[identifier] = cwd
                row["message"] = self.message or "本机记录：不是实时服务；只有明确的回合开始/结束事件才确认状态与时长。"
                result.append({key: row[key] for key in ("id", "name", "workspace", "state", "model", "source", "created_at")})
            for identifier, name in _record_names(needs_name).items():
                self.rows[identifier]["name"] = name
            for summary in result:
                row = self.rows[summary["id"]]
                row["subagents"] = [child for child in children if child["parent_thread_id"] == row["id"]][:128]
                row["child_count"] = len(row["subagents"])
                summary.update(name=row["name"], session_kind=row["session_kind"], parent_thread_id=row["parent_thread_id"], child_count=row["child_count"])
            return result

    def _consume_response_item(self, row: dict, raw: bytes):
        """Tool metadata; only exact update_plan admits public plan arguments."""
        top = _json_public_fields(raw, {"type", "timestamp", "payload"})
        if _public_string(top, "type") != "response_item":
            return
        payload = _json_public_fields(top.get("payload", b"{}"), {"type", "name", "namespace", "call_id", "thread_id", "arguments", "role", "channel", "content", "id"})
        kind = _public_string(payload, "type")
        thread = _public_string(payload, "thread_id")
        if thread and thread != row["id"]:
            return
        if kind == "message":
            if _public_string(payload, "role", 40) == "user":
                text = _public_final_text(payload.get("content", b"[]"), frozenset({"input_text", "text"}))
                if text:
                    self._consume(row, {"type": "event_msg", "timestamp": _public_string(top, "timestamp", 80),
                                        "payload": {"type": "user_message", "message": text,
                                                    "id": _public_string(payload, "id", 160), "thread_id": row["id"]}})
                return
            if _public_string(payload, "role", 40) == "assistant" and _public_string(payload, "channel", 40) == "final":
                text = _public_final_text(payload.get("content", b"[]"))
                if text:
                    _public_event(row, {"id": _public_string(payload, "id", 160), "kind": "report",
                                        "title": "阶段报告 · 公开 final", "body": text,
                                        "timestamp": _time(_public_string(top, "timestamp", 80)),
                                        "source": "模型公开报告，不代表独立验收通过"})
            return
        if kind not in {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"}:
            return  # Reasoning, agent messages and all other item content excluded.
        thread = _public_string(payload, "thread_id")
        if thread and thread != row["id"]:
            return
        call_id = _public_string(payload, "call_id")
        if not SESSION_ID.fullmatch(call_id):
            return
        stamp = _time(_public_string(top, "timestamp", 80))
        tools = row["tools"]
        previous = next((tool for tool in tools if tool["id"] == call_id), None)
        if kind in {"function_call_output", "custom_tool_call_output"}:
            if previous is None:
                return  # A bounded tail may not contain the call's name; do not invent it.
            if previous["state"] not in {"failed", "cancelled"}:
                previous["state"] = "completed"  # Completion is not proof of success.
            if stamp and (not previous["started_at"] or stamp >= previous["started_at"]):
                previous["finished_at"] = stamp
        else:
            name = _text(_public_string(payload, "name", 160))
            namespace = _text(_public_string(payload, "namespace", 160))
            if not name:
                return
            qualified = _text(namespace + "." + name if namespace else name)
            if name in {"update_plan", "functions.update_plan"} and namespace in {"", "functions"}:
                plan = _public_plan(payload.get("arguments", b"{}"))
                if plan is not None:
                    row["plan"] = [dict(item, timestamp=stamp) for item in plan]
                    _public_event(row, {"id": call_id + ":plan", "kind": "plan", "timestamp": stamp,
                                        "title": "公开计划更新", "step_count": len(plan), "time_basis": "plan_reported"})
                    for item in row["plan"]:
                        _public_event(row, {"id": call_id + ":" + item["id"], "kind": "step", "timestamp": stamp,
                                            "title": item["title"], "step_id": item["id"], "state": item["state"],
                                            "time_basis": "plan_reported"})
            mcp_name = name if name.startswith("mcp__") else namespace + "." + name if namespace.startswith("mcp__") else ""
            mcp_parts = mcp_name[5:].split("__", 1) if "__" in mcp_name[5:] else mcp_name[5:].rsplit(".", 1)
            category = ("mcp" if mcp_name and len(mcp_parts) == 2 and all(mcp_parts)
                        else "skill" if name.casefold() == "skill" else "other")
            names = self.call_names.setdefault(row["id"], {})
            if call_id in names and names[call_id] != qualified:
                return  # Reused call IDs cannot change a verified public tool name.
            names[call_id] = qualified
            while len(names) > MAX_TOOL_ITEMS:
                names.pop(next(iter(names)))
            if previous is None:
                previous = {"id": call_id, "name": qualified, "state": "started", "category": category,
                            "started_at": stamp, "finished_at": None}
                tools.append(previous)
                del tools[:-MAX_TOOL_ITEMS]
            elif previous["name"] in RECORD_TOOL_TYPES:
                previous["name"] = qualified  # Enrich the same native item, never double count.
            if stamp and not previous["started_at"]:
                previous["started_at"] = stamp
        if stamp:
            row["last_activity"] = max(stamp, row.get("last_activity") or 0)
        # Tool call timestamps never become a whole turn's started/finished_at.

    def _consume(self, row: dict, value: dict):
        payload = value.get("payload")
        if not isinstance(payload, dict):
            return
        stamp = _time(value.get("timestamp"))
        if value.get("type") == "turn_context":
            row["model"] = _text(payload.get("model")) or row["model"]
            return
        if value.get("type") == "token_usage_record":
            identifier = payload.get("thread_id")
            if identifier and identifier != row["id"]:
                return
            if stamp:
                row["last_activity"] = max(stamp, row.get("last_activity") or 0)
            turn_id = _text(payload.get("turn_id"), 256)
            # Both thread and turn totals are cumulative counters, not the
            # individual response charge. Only payload.usage below is atomic.
            thread_usage, turn_usage = payload.get("thread_token_usage"), payload.get("turn_token_usage")
            usage = thread_usage or turn_usage
            row["usage"] = _usage(usage, scope="session", update_id=_text(payload.get("response_id"), 256) or str(stamp))
            row["usage_time"] = stamp if row["usage"] else None
            # A thread counter spans turns/models. A turn ID is not its reset
            # epoch, and a turn-only counter must not share the thread stream.
            row["usage_counter_scope"] = "session" if thread_usage else "turn"
            row["usage_counter_epoch"] = row["id"] if thread_usage else turn_id
            row["usage_source"] = "codex_thread_counter" if thread_usage else "codex_turn_counter"
            row["usage_model"] = ""  # Counter deltas cannot be billed to the last model.
            response_id = _text(payload.get("response_id"), 160)
            atomic = _usage(payload.get("usage"), scope="turn", update_id=response_id)
            if response_id and SESSION_ID.fullmatch(response_id) and atomic:
                sample = {"usage": atomic, "usage_scope": "turn", "usage_id": response_id,
                          "usage_id_scope": "source", "usage_time": stamp, "usage_model": row.get("model", ""),
                          "usage_source": "codex_token_usage_record", "run_id": turn_id}
                samples = row.setdefault("usage_events", [])
                if not any(item["usage_id"] == response_id for item in samples):
                    samples.append(sample)
                    del samples[:-256]
            if not row["run_id"] and turn_id:
                row["run_id"] = turn_id
            return
        if value.get("type") != "event_msg":
            return  # Never decode response_item contents, reasoning or tool output.
        kind = payload.get("type")
        if not isinstance(kind, str):
            return
        identifier = payload.get("thread_id")
        if identifier and identifier != row["id"]:
            return
        if kind in {"item_started", "item_completed"}:
            item = payload.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if not isinstance(item_type, str) or item_type not in RECORD_TOOL_TYPES:
                return  # Reasoning, AgentMessage and unverified item types excluded.
            call_id = item.get("id")
            if not isinstance(call_id, str) or not SESSION_ID.fullmatch(call_id):
                return
            item_types = self.item_types.setdefault(row["id"], {})
            if call_id in item_types and item_types[call_id] != item_type:
                return
            item_types[call_id] = item_type
            while len(item_types) > MAX_TOOL_ITEMS:
                item_types.pop(next(iter(item_types)))
            state = "started" if kind == "item_started" else "completed"
            status = item.get("status")
            if isinstance(status, dict):
                status = status.get("type")
            native_state = {"inprogress": "started", "in_progress": "started",
                            "completed": "completed", "failed": "failed",
                            "interrupted": "cancelled", "cancelled": "cancelled"}.get(
                                status.casefold() if isinstance(status, str) else "", "unknown")
            if kind == "item_completed" and native_state in {"failed", "cancelled"}:
                state = native_state
            elif kind == "item_completed" and native_state == "started":
                state = "unknown"  # Contradictory metadata is not a success.
            if (kind == "item_completed" and item_type == "CommandExecution"
                    and type(item.get("exit_code")) is int and item["exit_code"] != 0):
                state = "failed"  # Actual exit metadata, never command/output inference.
            tools = row["tools"]
            previous = next((tool for tool in tools if tool["id"] == call_id), None)
            if previous is None:
                previous = {"id": call_id, "name": item_type, "state": state,
                            "category": RECORD_TOOL_TYPES[item_type],
                            "started_at": None, "finished_at": None}
                tools.append(previous)
                del tools[:-MAX_TOOL_ITEMS]
            elif previous["name"] != item_type and self.call_names.get(row["id"], {}).get(call_id) != previous["name"]:
                return  # A reused ID cannot change an already verified tool type.
            elif state == "started" and previous["state"] != "started":
                return  # An out-of-order start must not overwrite a terminal item.
            previous["state"] = state
            previous["category"] = RECORD_TOOL_TYPES[item_type]
            started = _unix_ms(payload.get("started_at_ms"))
            finished = _unix_ms(payload.get("completed_at_ms")) if kind == "item_completed" else None
            if started:
                previous["started_at"] = started
            if finished and (not previous["started_at"] or finished >= previous["started_at"]):
                previous["finished_at"] = finished
            for activity in (previous["started_at"], previous["finished_at"]):
                if activity:
                    row["last_activity"] = max(activity, row.get("last_activity") or 0)
            return  # Tool activity is not evidence of a whole turn's start/end.
        if kind == "user_message":
            text = _human_input(_redact(payload.get("message", "")))[:12000] if isinstance(payload.get("message"), str) else ""
            if text.strip():
                identifier = _text(payload.get("id"), 160) or hashlib.sha256((row["id"] + str(stamp) + text).encode()).hexdigest()
                request = {"id": identifier, "text": text, "timestamp": stamp}
                if not any(item.get("id") == identifier for item in row["requests"]):
                    row["requests"].append(request)
                    del row["requests"][:-100]
                _public_event(row, dict(request, kind="request"))
            return
        public_events = {"task_started", "turn_started", "task_complete", "task_completed", "turn_completed",
                         "turn_aborted", "task_failed", "turn_failed", "token_count", "thread_settings_applied"}
        if kind in public_events and stamp:
            row["last_activity"] = max(stamp, row.get("last_activity") or 0)
        if kind in {"task_started", "turn_started"}:
            turn = _text(payload.get("turn_id"), 256)
            if not turn or not stamp:
                return
            row.update(state="started", run_id=turn, started_at=stamp, finished_at=None,
                       state_evidence="public_turn_event")
        elif kind in {"task_complete", "task_completed", "turn_completed", "turn_aborted", "task_failed", "turn_failed"}:
            turn = _text(payload.get("turn_id"), 256)
            if turn and row["run_id"] and turn != row["run_id"]:
                return
            terminal = "cancelled" if kind == "turn_aborted" else "failed" if kind.endswith("failed") else "completed"
            row.update(state=terminal, run_id=turn or row["run_id"], finished_at=stamp,
                       state_evidence="public_turn_event")
            _public_event(row, {"kind": "turn_end", "timestamp": stamp, "state": terminal,
                                "title": "回合结束上报（不等于项目独立验收通过）", "id": (turn or str(stamp)) + ":" + kind})
        elif kind == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                row["usage"] = _usage(info.get("total_token_usage"), scope="session", update_id=str(stamp))
                row["usage_time"] = stamp if row["usage"] else None
                row.update(usage_model="", usage_counter_scope="session", usage_counter_epoch=row["id"], usage_source="codex_thread_counter")
        elif kind == "thread_settings_applied":
            settings = payload.get("thread_settings")
            if isinstance(settings, dict):
                row["model"] = _text(settings.get("model")) or row["model"]

    def _consume_public_record(self, row: dict, raw: bytes):
        """Skip private event bodies before JSON decoding, including Reasoning."""
        top = _json_public_fields(raw, {"type", "timestamp", "payload", "message"})
        record_type = _public_string(top, "type", 40)
        timestamp = _public_string(top, "timestamp", 80)
        if record_type == "compacted":
            summary = _redact(_public_string(top, "message", 16000))[:4000]
            event = {"kind": "checkpoint", "timestamp": _time(timestamp),
                     "summary": ("公开压缩摘要（最多4000字符；不读取 replacement_history）\n" + summary) if summary else
                                "宿主记录了上下文压缩；未提供可投影摘要，不读取隐藏推理或 replacement_history。"}
            _public_event(row, event)
            row["checkpoints"] = [event]
            row["memory"] = summary or None
            return
        if record_type == "response_item":
            self._consume_response_item(row, raw)
            return
        if record_type not in {"event_msg", "turn_context", "token_usage_record"}:
            return
        payload = _json_public_fields(top.get("payload", b"{}"), {"type"})
        kind = _public_string(payload, "type", 40)
        allowed = ({"model"} if record_type == "turn_context" else
                   {"thread_id", "turn_id", "thread_token_usage", "turn_token_usage", "usage", "response_id"} if record_type == "token_usage_record" else
                   {"type", "thread_id", "id", "message"} if kind == "user_message" else
                   {"type", "thread_id", "item", "started_at_ms", "completed_at_ms"} if kind in {"item_started", "item_completed"} else
                   {"type", "thread_id", "turn_id", "info", "thread_settings"} if kind in {
                       "task_started", "turn_started", "task_complete", "task_completed", "turn_completed",
                       "turn_aborted", "task_failed", "turn_failed", "token_count", "thread_settings_applied"} else set())
        if not allowed:
            return
        projected = _json_public_fields(top.get("payload", b"{}"), allowed)
        # Select nested metadata before decoding. A familiar outer event name
        # does not grant access to arbitrary private fields inside its objects.
        def object_bytes(fields):
            return b"{" + b",".join(json.dumps(key).encode() + b":" + value for key, value in fields.items()) + b"}"
        def usage_bytes(value):
            fields = _json_public_fields(value, {"inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens",
                                                "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"})
            return object_bytes({key: value for key, value in fields.items() if len(value) <= 32 and value.strip().isdigit()})
        for key in {"thread_token_usage", "turn_token_usage", "usage"} & projected.keys():
            projected[key] = usage_bytes(projected[key])
        if "info" in projected:
            info = _json_public_fields(projected["info"], {"total_token_usage"})
            projected["info"] = object_bytes({key: usage_bytes(value) for key, value in info.items()})
        if "thread_settings" in projected:
            settings = _json_public_fields(projected["thread_settings"], {"model"})
            projected["thread_settings"] = object_bytes({"model": json.dumps(_public_string(settings, "model")).encode()})
        for key in {"model", "thread_id", "turn_id", "response_id", "id", "type", "message"} & projected.keys():
            projected[key] = json.dumps(_public_string(projected, key, 16000 if key == "message" else 256)).encode()
        for key in {"started_at_ms", "completed_at_ms"} & projected.keys():
            if len(projected[key]) > 16 or not projected[key].strip().isdigit():
                projected.pop(key)
        if "item" in projected:
            item = _json_public_fields(projected["item"], {"type", "id", "status", "exit_code"})
            if _public_string(item, "type") not in RECORD_TOOL_TYPES:
                return
            if item.get("status", b"").lstrip().startswith(b"{"):
                item["status"] = object_bytes({"type": json.dumps(_public_string(_json_public_fields(item["status"], {"type"}), "type", 40)).encode()})
            elif "status" in item:
                item["status"] = json.dumps(_public_string(item, "status", 40)).encode()
            for key in {"id", "type"} & item.keys():
                item[key] = json.dumps(_public_string(item, key)).encode()
            if "exit_code" in item and (len(item["exit_code"]) > 16 or not item["exit_code"].strip().lstrip(b"-").isdigit()):
                item.pop("exit_code")
            projected["item"] = object_bytes(item)
        self._consume(row, {"type": record_type, "timestamp": timestamp,
                            "payload": {key: json.loads(value) for key, value in projected.items()}})

    def snapshot(self, session_id: str) -> dict:
        with self.lock:
            if self.closed:
                raise RuntimeError("会话记录观察已关闭。")
            if session_id not in self.paths:
                raise ValueError("请先发现并选择该本机会话记录。")
            path = self.paths[session_id]
            if path is None:
                return {**_base(session_id, source=self.source), **{key: self.rows[session_id].get(key) for key in
                        ("name", "workspace", "created_at", "updated_at", "session_kind", "parent_thread_id", "subagents", "child_count")},
                        "metadata_only": True, "message": "仅有 Codex 原生会话列表摘要；对应 JSONL 尚未可读。正文、状态和用量未知，后台将继续重试。"}
            if path.resolve() != path:
                raise ValueError("会话记录目标已被替换为其他路径；请重新发现。")
            info = path.stat()
            previous = self.offsets.get(session_id)
            identity = (info.st_dev, info.st_ino)
            offset = previous[2] if previous and previous[:2] == identity and previous[2] <= info.st_size else None
            if offset is not None and len(previous) > 3:
                with path.open("rb") as file:
                    file.seek(max(0, offset - ANCHOR_BYTES))
                    anchor = hashlib.sha256(file.read(min(ANCHOR_BYTES, offset))).digest()
                if anchor != previous[3]:
                    offset = None  # Native replacement/rewrite, not an append.
            row = self.rows[session_id]
            if offset is None:
                row.update(state="unknown", started_at=None, finished_at=None, run_id="", state_evidence="", usage={},
                           usage_time=None, usage_model="", usage_counter_scope="", usage_counter_epoch="", usage_source="",
                           usage_events=[], tools=[], requests=[], plan=[], timeline=[], checkpoints=[], memory=None)
                self.call_names.pop(session_id, None)
                self.item_types.pop(session_id, None)
                offset = max(0, info.st_size - TAIL_BYTES)
                if offset > HEADER_BYTES:
                    # Initial public inputs may not be in a long session's
                    # tail. Only this selected file's small prefix is allowed;
                    # old counters, tools, starts and plans cannot become live.
                    with path.open("rb") as file:
                        prefix = file.read(HEADER_BYTES)
                    for line in prefix[:prefix.rfind(b"\n") + 1].splitlines():
                        try:
                            top = _json_public_fields(line, {"type", "payload"})
                            kind = _public_string(top, "type", 40)
                            meta = _json_public_fields(top.get("payload", b"{}"), {"type", "role", "channel"})
                            if ((kind == "event_msg" and _public_string(meta, "type", 40) == "user_message") or
                                (kind == "response_item" and _public_string(meta, "type", 40) == "message" and
                                 (_public_string(meta, "role", 40) == "user" or
                                  (_public_string(meta, "role", 40) == "assistant" and _public_string(meta, "channel", 40) == "final")))):
                                self._consume_public_record(row, line)
                        except (ValueError, UnicodeError):
                            continue
                    row["message"] = "选中记录：前64KiB只取公开用户输入/明确final报告，末256KiB公开事件；中段未读取，不代表完整历史或实时状态。"
            if info.st_size - offset > TAIL_BYTES:
                # A skipped interval may contain a start/finish. Invalidate old proof.
                offset = max(0, info.st_size - TAIL_BYTES)
                row.update(state="unknown", started_at=None, finished_at=None, run_id="", state_evidence="")
                row["plan"] = []  # A skipped interval can contain a newer plan.
            with path.open("rb") as file:
                file.seek(offset)
                raw = file.read(TAIL_BYTES)
            if offset and (not previous or offset != previous[2]):
                first = raw.find(b"\n")
                if first < 0:
                    self.offsets[session_id] = (*identity, offset + len(raw))
                    raw = b""  # Still consult the indexed native user history.
                else:
                    offset += first + 1
                    raw = raw[first + 1:]
            last = raw.rfind(b"\n")
            if last >= 0:
                for line in raw[:last].splitlines():
                    try:
                        self._consume_public_record(row, line)
                    except (ValueError, UnicodeError):
                        continue
                self.offsets[session_id] = (*identity, offset + last + 1)
            else:
                self.offsets[session_id] = (*identity, offset)
            history = public_history(self.directory, session_id, limit=100)
            if history.get("available"):
                row["requests"] = [{**request, "text": _redact(request["text"])[:12000]} for request in history["requests"]]
                row["timeline"] = [event for event in row["timeline"] if event.get("kind") != "request"]
                for request in row["requests"]:
                    _public_event(row, dict(request, kind="request"))
                row["message"] = self.catalog_message + " " + history.get("coverage", "") + " 计划/公开报告/计量仅取日志末256KiB，不代表完整历史或实时状态。"
            native_tools = public_tools(self.directory, session_id)
            if native_tools.get("available"):
                # Native item IDs enrich/deduplicate the same command in the
                # rollout tail. References are a separate evidence kind, not a
                # second invocation of a Skill executor.
                by_id = {tool["id"]: tool for tool in row["tools"]}
                for tool in native_tools["tools"]:
                    existing = by_id.get(tool["id"], {})
                    by_id[tool["id"]] = {**existing, **tool}
                row["tools"] = sorted(by_id.values(), key=lambda tool: tool.get("timestamp") or tool.get("finished_at") or tool.get("started_at") or 0)[-256:]
                row["message"] += " " + native_tools["coverage"]
            newest_request = max((request.get("timestamp") or 0 for request in row["requests"]), default=0)
            if row["state"] in {"completed", "failed", "cancelled"} and row.get("finished_at") and newest_request > row["finished_at"]:
                # New user input invalidates an older terminal turn. Without a
                # new start event, 'running' and elapsed time are still unknown.
                row.update(state="unknown", started_at=None, finished_at=None, run_id="", state_evidence="")
            native_turn = public_turn(self.directory, session_id)
            if native_turn.get("available"):
                self._consume_turn_metadata(row, native_turn)
            end = self.offsets[session_id][2]
            with path.open("rb") as file:
                file.seek(max(0, end - ANCHOR_BYTES))
                anchor = hashlib.sha256(file.read(min(ANCHOR_BYTES, end))).digest()
            self.offsets[session_id] = (*identity, end, anchor)
            return deepcopy(row)

    def sync_marker(self, session_id):
        """File/index invalidation only; these values never imply task state."""
        with self.lock:
            row = self.rows.get(session_id, {})
            path = self.paths.get(session_id)
            metadata = (row.get("name"), row.get("updated_at"), row.get("metadata_only"))
            if path is None:
                return metadata
            info = path.stat()
            return (*metadata, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def _consume_turn_metadata(self, row: dict, turn: dict):
        """Recover explicit lifecycle evidence outside the bounded rollout tail."""
        identifier = _text(turn.get("turn_id"), 256)
        start, end = _time(turn.get("started_at")), _time(turn.get("completed_at"))
        if not SESSION_ID.fullmatch(identifier) or not start or start > time.time() + 300:
            return
        if end and (end < start or end > time.time() + 300):
            return
        status = turn.get("status")
        state = {"inprogress": "started", "in_progress": "started", "completed": "completed",
                 "failed": "failed", "interrupted": "cancelled", "cancelled": "cancelled"}.get(
                     status.casefold() if isinstance(status, str) else "", "unknown")
        if state == "unknown" or state != "started" and not end:
            return
        # The index may lag an appended terminal event. Never resurrect that
        # same turn, or overwrite a newer selected thread's lifecycle evidence.
        latest = row.get("finished_at") or row.get("started_at") or 0
        if start < latest and row.get("run_id") != identifier:
            return
        if row.get("run_id") == identifier and row.get("state") in {"completed", "failed", "cancelled"}:
            return
        newest_request = max((item.get("timestamp") or 0 for item in row["requests"]), default=0)
        if state != "started" and newest_request > end:
            return
        row.update(state=state, run_id=identifier, started_at=start, finished_at=end,
                   state_evidence="native_turn_metadata")
        row["last_activity"] = max(row.get("last_activity") or 0, end or start)

    def close(self):
        with self.lock:
            self.closed = True
            self.paths.clear()
            self.rows.clear()
            self.offsets.clear()
            self.call_names.clear()
            self.item_types.clear()


def _codex_records_path(selected: Path) -> Path:
    """Descend only from .codex to sessions; never widen a selected subtree."""
    selected = selected.resolve()
    if selected.name.casefold() == ".codex":
        selected = (selected / "sessions").resolve()
    if selected == Path(selected.anchor) or selected == _home():
        raise ValueError("请选择 .codex、sessions、具体记录子目录或 .jsonl 文件，不读取整个磁盘或用户目录。")
    if selected.is_file():
        if selected.suffix.casefold() != ".jsonl":
            raise ValueError("Codex 记录文件必须是 .jsonl；原生服务请显式选择 native。")
    elif not selected.is_dir():
        raise ValueError("没有可读的 Codex 会话记录位置；请选择 .codex 根中的 sessions 或已有记录目录。")
    return selected


def create_source(tool_id: str, endpoint: str = ""):
    if not isinstance(endpoint, str):
        raise ValueError("接入地址必须是字符串。")
    selected_endpoint = endpoint.strip()
    if len(selected_endpoint) >= 2 and selected_endpoint[0] == selected_endpoint[-1] == '"':
        selected_endpoint = selected_endpoint[1:-1].strip()
    if tool_id in {"claude_code", "claude"}:
        from .claude_sessions import ClaudeSessionSource
        return ClaudeSessionSource(root=None if selected_endpoint in {"", "local-records"} else
                                   Path(selected_endpoint).expanduser().resolve())
    if tool_id == "antigravity":
        from .antigravity_sessions import AntigravitySessionSource
        return AntigravitySessionSource(root=None if selected_endpoint in {"", "local-records"} else
                                        Path(selected_endpoint).expanduser().resolve())
    if tool_id != "codex":
        raise ValueError("此工具尚不支持现有会话发现；不伪装成等待接入。")
    home = _home() / ".codex"
    if selected_endpoint in {"", "local-records"}:
        return CodexRecordSource(_codex_records_path(home / "sessions"))
    if selected_endpoint == "native":
        selected = home / "app-server-control/app-server-control.sock"
    elif selected_endpoint.startswith("native:"):
        address = selected_endpoint[len("native:"):].strip()
        if not address:
            raise ValueError("native: 后需提供已运行服务的控制 socket，或使用 native 选择默认既有服务。")
        selected = Path(address).expanduser().resolve()
    else:
        selected = Path(selected_endpoint).expanduser().resolve()
        if selected.suffix.casefold() != ".sock":
            return CodexRecordSource(_codex_records_path(selected))
    executable = home / "packages/app-server-daemon/current/bin/codex.exe"
    if not executable.is_file():
        found = shutil.which("codex.exe")
        executable = Path(found) if found else executable
    if selected.exists() and executable.is_file():
        return CodexNativeSource(executable, selected, home)
    raise ValueError("既有 Codex 控制 socket 或原生 proxy 不可用；未启动新服务。可选择默认本机记录。")
