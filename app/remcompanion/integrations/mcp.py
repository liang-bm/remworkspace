"""MCP 客户端：JSON-RPC 2.0、stdio 和 Streamable HTTP。

兼容 2025-03-26 / 2025-06-18 / 2025-11-25，并可显式选用 2026-07-28。
服务端的工具和提示词只列出；工具调用由用户主动点击触发，不给模型自动执行权。
"""

from __future__ import annotations

from collections import deque
import base64
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .storage import validate_mcp
from .processes import ProcessScope, program_environment


SUPPORTED_VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"}
MODERN_VERSION = "2026-07-28"
MAX_MESSAGE = 4 * 1024 * 1024


class MCPError(RuntimeError):
    pass


def header_value(value: str | bool | int) -> str:
    """新协议要求非 ASCII、控制字符、首尾空白和 sentinel 字面量用 Base64。"""

    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    plain = all(0x20 <= ord(character) <= 0x7E or character == "\t" for character in text)
    if not plain or text != text.strip() or (text.startswith("=?base64?") and text.endswith("?=")):
        return "=?base64?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="
    return text


def header_bindings(schema: dict[str, Any]) -> list[tuple[str, tuple[str, ...], str]]:
    """验证 x-mcp-header，并返回只经 properties 可达的参数路径。"""

    bindings: list[tuple[str, tuple[str, ...], str]] = []
    if not isinstance(schema, dict):
        raise MCPError("工具 inputSchema 不是 JSON 对象。")
    seen: set[str] = set()
    token = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
    def walk(value: Any, path: tuple[str, ...], reachable: bool) -> None:
        if isinstance(value, dict):
            if "x-mcp-header" in value:
                name = value["x-mcp-header"]
                kind = value.get("type")
                if not reachable or not path or not isinstance(name, str) or not token.fullmatch(name) or name.lower() in seen or not isinstance(kind, str) or kind not in {"string", "integer", "boolean"}:
                    raise MCPError("工具包含无效或重复的 x-mcp-header 注解。")
                seen.add(name.lower())
                bindings.append((name, path, kind))
            for key, child in value.items():
                if key == "properties" and isinstance(child, dict) and reachable:
                    for name, property_schema in child.items():
                        walk(property_schema, path + (name,), True)
                elif isinstance(child, (dict, list)):
                    walk(child, path, False)
        elif isinstance(value, list):
            for child in value:
                walk(child, path, False)
    walk(schema, (), True)
    return bindings


def program_argv(command: str, args: list[str]) -> list[str]:
    """直接启动程序；已知 Windows npm CLI 包转为 node 入口，避免隐式 shell。"""

    from ..platforms import native_module
    return native_module("shell").program_argv(command, args)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, file: Any, code: int, message: str, headers: Any, new_url: str) -> None:
        raise MCPError("MCP 接口发生重定向，请直接填写最终地址。")


class MCPClient:
    """一个客户端对应一个明确启用的服务，所有请求在锁内串行化。"""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = validate_mcp(settings)
        self.timeout = self.settings["timeout"]
        self.version = self.settings["protocol_version"]
        self.server: dict[str, Any] = {}
        self.session_id = ""
        self.sequence = 0
        self.process: subprocess.Popen[str] | None = None
        self.scope: ProcessScope | None = None
        self.inbox: queue.Queue[dict[str, Any] | Exception] = queue.Queue(maxsize=16)
        self.logs: deque[str] = deque(maxlen=80)
        self.lock = threading.RLock()
        self.closed = False
        self.tool_schemas: dict[str, dict[str, Any]] = {}
        self.writing = threading.Event()

    def connect(self) -> dict[str, Any]:
        if self.closed:
            raise MCPError("MCP 连接已关闭。")
        if not self.settings["enabled"]:
            raise MCPError("此接入尚未启用。请先检查配置并启用。")
        if self.settings["transport"] == "stdio":
            environment = program_environment(self.settings)
            working_dir = self.settings.get("cwd") or None
            self.scope = ProcessScope()
            self.process = self.scope.launch(
                program_argv(str(self.settings["command"]), self.settings["args"]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=environment, cwd=working_dir,
            )
            threading.Thread(target=self._read_stdout, name="remchat-mcp-stdout", daemon=True).start()
            threading.Thread(target=self._read_stderr, name="remchat-mcp-stderr", daemon=True).start()
        try:
            if self.version == MODERN_VERSION:
                result = self.request("server/discover", {})
                if MODERN_VERSION not in result.get("supportedVersions", []):
                    raise MCPError("此服务不支持 2026-07-28；请根据 supportedVersions 选择兼容版本。")
                self.server = {**result, "protocolVersion": self.version, "serverInfo": result.get("_meta", {}).get("io.modelcontextprotocol/serverInfo", {})}
                return self.server
            result = self.request("initialize", {
                "protocolVersion": self.version, "capabilities": {},
                "clientInfo": {"name": "RemChat", "version": "1.0.0"},
            })
            self.version = str(result.get("protocolVersion", ""))
            if self.version not in SUPPORTED_VERSIONS:
                raise MCPError(f"服务端协商到 {self.version}；此客户端支持 {', '.join(sorted(SUPPORTED_VERSIONS))}。")
            self.server = result
            self.notify("notifications/initialized")
            return result
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while not self.closed:
                line = self.process.stdout.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE or len(line.encode("utf-8")) > MAX_MESSAGE:
                    raise MCPError("MCP 返回消息超过 4 MB。")
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise MCPError("MCP 标准输出包含无效 JSON-RPC。")
                if "method" in message and "id" not in message:
                    continue
                self.inbox.put(message, timeout=0.5)
        except Exception as error:
            try:
                self.inbox.put(error, timeout=0.1)
            except queue.Full:
                pass
        finally:
            try:
                self.inbox.put(MCPError("MCP 进程已退出或关闭了输出。"), timeout=0.1)
            except queue.Full:
                pass

    def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            while not self.closed:
                # 即使服务端一直不写换行，也按有界块排空，避免一行吃满内存。
                chunk = self.process.stderr.readline(4096)
                if not chunk:
                    break
                self.logs.append(chunk.strip()[:2000])
        except (OSError, ValueError):
            pass

    def _write(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin or self.process.poll() is not None:
            raise MCPError("MCP 进程没有运行。")
        text = json.dumps(message, ensure_ascii=False) + "\n"
        if len(text.encode("utf-8")) > MAX_MESSAGE:
            raise MCPError("MCP 请求超过 4 MB。")
        outcome: queue.Queue[Exception | None] = queue.Queue(maxsize=1)
        def write() -> None:
            self.writing.set()
            try:
                assert self.process and self.process.stdin
                self.process.stdin.write(text)
                self.process.stdin.flush()
                outcome.put(None)
            except Exception as error:
                outcome.put(error)
            finally:
                self.writing.clear()
        threading.Thread(target=write, name="remchat-mcp-write", daemon=True).start()
        try:
            result = outcome.get(timeout=self.timeout)
        except queue.Empty as error:
            if self.scope:
                self.scope.close(graceful=False)
            raise MCPError("MCP 进程没有读取请求，写入超时并已请求停止；若无法确认组归属请查看日志，可能留下后代。") from error
        if result:
            raise MCPError(f"MCP 请求写入失败：{result}") from result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        with self.lock:
            message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            if self.settings["transport"] == "stdio":
                self._write(message)
            else:
                self._post(message)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            if self.closed:
                raise MCPError("MCP 连接已关闭。")
            self.sequence += 1
            identifier = self.sequence
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": identifier, "method": method}
            if params is not None:
                message["params"] = dict(params)
            if self.version == MODERN_VERSION:
                message.setdefault("params", {})["_meta"] = {"io.modelcontextprotocol/protocolVersion": self.version, "io.modelcontextprotocol/clientCapabilities": {}, "io.modelcontextprotocol/clientInfo": {"name": "RemChat", "version": "1.0.0"}}
            if self.settings["transport"] == "http":
                response = self._post(message)
            else:
                self._write(message)
                deadline = time.monotonic() + self.timeout
                while True:
                    try:
                        response = self.inbox.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty as error:
                        self.notify("notifications/cancelled", {"requestId": identifier, "reason": "request timed out"})
                        raise MCPError(f"MCP 请求 {method} 超时。") from error
                    if isinstance(response, Exception):
                        raise MCPError(str(response)) from response
                    # 未协商 sampling/elicitation；拒绝服务端反向请求，不允许绕过用户。
                    if "method" in response and "id" in response:
                        if self.version == MODERN_VERSION:
                            raise MCPError("新版 MCP 服务违规返回了反向 JSON-RPC 请求。")
                        self._write({"jsonrpc": "2.0", "id": response["id"], "error": {"code": -32601, "message": "Client capability not enabled"}})
                    elif response.get("id") == identifier:
                        break
                    if time.monotonic() >= deadline:
                        raise MCPError(f"MCP 请求 {method} 超时。")
            if not response or response.get("id") != identifier:
                raise MCPError("MCP 没有返回与请求 ID 匹配的结果。")
            if "error" in response:
                detail = response["error"]
                raise MCPError(f"MCP {method}：{detail.get('message', detail) if isinstance(detail, dict) else detail}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise MCPError(f"MCP {method} 的结果不是对象。")
            return result

    def _post(self, message: dict[str, Any]) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if message.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = self.version
        if self.session_id and self.version != MODERN_VERSION:
            headers["Mcp-Session-Id"] = self.session_id
        if self.version == MODERN_VERSION:
            headers["Mcp-Method"] = message["method"]
            params = message.get("params", {})
            if message["method"] in {"tools/call", "prompts/get", "resources/read"}:
                headers["Mcp-Name"] = header_value(params.get("name", params.get("uri", "")))
            if message["method"] == "tools/call":
                schema = self.tool_schemas.get(str(params.get("name")), {})
                for name, path, kind in header_bindings(schema):
                    value: Any = params.get("arguments", {})
                    for segment in path:
                        value = value.get(segment) if isinstance(value, dict) else None
                    if value is None:
                        continue
                    valid = (kind == "string" and isinstance(value, str)) or (kind == "boolean" and isinstance(value, bool)) or (kind == "integer" and type(value) is int and abs(value) <= 2**53 - 1)
                    if not valid:
                        raise MCPError(f"参数 {'.'.join(path)} 与协议请求头注解的类型不符。")
                    headers["Mcp-Param-" + name] = header_value(value)
        for header, reference in self.settings.get("headers_env", {}).items():
            if reference not in os.environ:
                raise MCPError(f"系统环境变量 {reference} 未设置。")
            if header.lower() in {"host", "content-length", "content-type", "accept", "mcp-protocol-version", "mcp-session-id", "mcp-method", "mcp-name"} or header.lower().startswith("mcp-param-"):
                raise MCPError(f"不能覆盖协议请求头 {header}。")
            headers[header] = os.environ[reference]
        request = urllib.request.Request(self.settings["url"], data=json.dumps(message).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session and self.version != MODERN_VERSION:
                    self.session_id = session
                if response.status == 202:
                    return None
                content_type = response.headers.get("Content-Type", "").split(";")[0]
                if content_type == "text/event-stream":
                    return self._read_sse(response, message.get("id"))
                raw = response.read(MAX_MESSAGE + 1)
                if len(raw) > MAX_MESSAGE:
                    raise MCPError("MCP 响应超过 4 MB。")
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                    raise MCPError("HTTP 返回值不是 JSON-RPC 2.0 对象。")
                return value
        except urllib.error.HTTPError as error:
            versions = ""
            try:
                body = json.loads(error.read(65536))
                supported = body.get("error", {}).get("data", {}).get("supported")
                if supported:
                    versions = "；服务支持版本：" + ", ".join(str(version) for version in supported)
            except (ValueError, AttributeError):
                pass
            raise MCPError(f"MCP 接口返回 HTTP {error.code}，请检查地址、授权和 protocol_version{versions}。") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise MCPError(f"MCP 连接失败：{error}") from error

    def _read_sse(self, response: Any, identifier: Any) -> dict[str, Any]:
        data: list[str] = []
        received = 0
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            raw = response.readline(MAX_MESSAGE + 1)
            if not raw:
                break
            received += len(raw)
            if received > MAX_MESSAGE:
                raise MCPError("MCP SSE 响应超过 4 MB。")
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                value = json.loads("\n".join(data))
                data = []
                if isinstance(value, dict) and value.get("id") == identifier and "method" not in value:
                    return value
                if isinstance(value, dict) and "method" in value and "id" in value:
                    if self.version == MODERN_VERSION:
                        raise MCPError("新版 MCP SSE 流违规返回了反向请求。")
                    self._post({"jsonrpc": "2.0", "id": value["id"], "error": {"code": -32601, "message": "Client capability not enabled"}})
        raise MCPError("MCP SSE 流结束前没有返回请求结果；请重试连接。")

    def list_all(self, method: str, field: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(100):
            result = self.request(method, {"cursor": cursor} if cursor else {})
            batch = result.get(field, [])
            if not isinstance(batch, list):
                raise MCPError(f"MCP {field} 不是列表。")
            for row in batch:
                if not isinstance(row, dict):
                    continue
                if field == "tools":
                    if not isinstance(row.get("name"), str) or not row.get("name") or not isinstance(row.get("inputSchema"), dict):
                        self.logs.append("跳过缺少 name / inputSchema 的无效工具。")
                        continue
                    if self.version == MODERN_VERSION and self.settings["transport"] == "http":
                        try:
                            header_bindings(row.get("inputSchema", {}))
                        except MCPError as error:
                            self.logs.append(f"跳过工具 {row.get('name')}：{error}")
                            continue
                    self.tool_schemas[str(row.get("name"))] = row.get("inputSchema", {})
                rows.append(row)
            following = result.get("nextCursor")
            if not following:
                return rows
            if following == cursor:
                raise MCPError("MCP 服务端返回了重复分页游标。")
            cursor = str(following)
        raise MCPError("MCP 分页超过 100 页，请缩小服务端列表。")

    def close(self) -> None:
        self.closed = True
        if self.scope:
            self.scope.close(graceful=not self.writing.is_set())
        if self.settings["transport"] == "http" and self.session_id and self.version != MODERN_VERSION:
            session, self.session_id = self.session_id, ""
            headers = {"Accept": "application/json, text/event-stream", "Mcp-Session-Id": session, "MCP-Protocol-Version": self.version}
            for name, reference in self.settings.get("headers_env", {}).items():
                if reference in os.environ:
                    headers[name] = os.environ[reference]
            request = urllib.request.Request(self.settings["url"], headers=headers, method="DELETE")
            try:
                with urllib.request.build_opener(_NoRedirect()).open(request, timeout=min(2, self.timeout)):
                    pass
            except urllib.error.HTTPError as error:
                if error.code not in {404, 405}:
                    self.logs.append(f"MCP 会话关闭返回 HTTP {error.code}。")
            except (MCPError, urllib.error.URLError, TimeoutError, OSError):
                self.logs.append("MCP 会话关闭请求未完成；本机连接已释放。")
