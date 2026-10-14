"""接入配置的唯一读写入口。

只保存可见配置；凭据引用环境变量，Skill 和 MCP 的工具内容均视为不可信输入。
同进程内由锁串行化，写入采用同目录临时文件 + replace，避免中断留下半个 JSON。
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any


ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
DEFAULTS: dict[str, Any] = {
    "version": 1,
    "mcp": [],
    "hooks": [],
    "skills": [],
    "tasks": [],
    "tool_connections": [],
    "apps": [],
    "receiver": {"port": 0},
}


def checked_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise ValueError("名称只允许字母、数字、下划线、点和连字符，最长 80 字符。")
    return value


class IntegrationStore:
    """轻量 JSON 仓库，不给界面暴露可变的内部对象。"""

    def __init__(self, root_path: Path | str) -> None:
        self.root = Path(root_path).resolve()
        self.directory = self.root / "data" / "integrations"
        self.path = self.directory / "settings.json"
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                return deepcopy(DEFAULTS)
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(f"接入配置无法读取：{self.path}\n{error}") from error
            if not isinstance(value, dict):
                raise ValueError("接入配置必须是 JSON 对象。")
            result = deepcopy(DEFAULTS)
            result.update(value)
            for category in ("mcp", "hooks", "skills", "tasks", "tool_connections", "apps"):
                if not isinstance(result[category], list):
                    raise ValueError(f"接入配置中的 {category} 必须是列表。")
                identifiers: set[str] = set()
                for row in result[category]:
                    if not isinstance(row, dict):
                        raise ValueError(f"接入配置中的 {category} 包含非对象条目。")
                    identifier = checked_id(str(row.get("id", "")))
                    if identifier in identifiers:
                        raise ValueError(f"接入配置中的 {category} 存在重复 ID：{identifier}。")
                    identifiers.add(identifier)
                    if category not in {"tasks", "tool_connections"} and not isinstance(row.get("enabled", False), bool):
                        raise ValueError(f"{identifier} 的 enabled 应为 true/false，而不是字符串。")
            return result

    def write(self, value: dict[str, Any]) -> None:
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(value, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def update_item(self, category: str, item: dict[str, Any]) -> None:
        checked_id(str(item.get("id", "")))
        with self.lock:
            value = self.read()
            rows = value[category]
            index = next((i for i, row in enumerate(rows) if row.get("id") == item["id"]), None)
            if index is None:
                rows.append(deepcopy(item))
            else:
                rows[index] = deepcopy(item)
            self.write(value)

    def backup(self) -> Path:
        """Keep a recoverable snapshot before explicit replacement/removal."""
        with self.lock:
            value = self.read()
            directory = self.directory / "backups"
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(value, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                os.unlink(name)
                raise
            return Path(name)

    def remove_item(self, category: str, identifier: str) -> None:
        with self.lock:
            value = self.read()
            value[category] = [row for row in value[category] if row.get("id") != identifier]
            self.write(value)

    def get_item(self, category: str, identifier: str) -> dict[str, Any]:
        item = next((row for row in self.read()[category] if row.get("id") == identifier), None)
        if item is None:
            raise ValueError(f"找不到 {identifier}。")
        return deepcopy(item)


def validate_mcp(item: dict[str, Any]) -> dict[str, Any]:
    """只接受本项目支持的显式传输；导入配置不会触发启动。"""

    result = deepcopy(item)
    result["id"] = checked_id(str(result.get("id", "")))
    result["name"] = str(result.get("name") or result["id"])[:160]
    transport = str(result.get("transport", "stdio"))
    if transport not in {"stdio", "http"}:
        raise ValueError("当前支持 stdio 和 Streamable HTTP，旧版独立 SSE 接口请升级。")
    result["transport"] = transport
    if not isinstance(result.get("enabled", False), bool):
        raise ValueError("enabled 应为 JSON true/false，而不是字符串。")
    result["enabled"] = result.get("enabled", False)
    result["protocol_version"] = str(result.get("protocol_version", "2025-11-25"))
    if result["protocol_version"] not in {"2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"}:
        raise ValueError("protocol_version 支持 2025-03-26 / 2025-06-18 / 2025-11-25 / 2026-07-28。")
    result["timeout"] = max(1, min(120, float(result.get("timeout", 20))))
    if transport == "stdio":
        if not str(result.get("command", "")).strip():
            raise ValueError("stdio 接入需要填写可执行程序。")
        if not isinstance(result.get("args", []), list) or not all(isinstance(arg, str) for arg in result.get("args", [])):
            raise ValueError("args 必须是字符串数组。")
        if not isinstance(result.get("env", {}), dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in result.get("env", {}).items()):
            raise ValueError("env 必须是对象。建议使用 env_refs 引用系统环境变量。")
        result["args"] = result.get("args", [])
    else:
        from urllib.parse import urlparse
        address = urlparse(str(result.get("url", "")))
        if address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password:
            raise ValueError("HTTP 接口需要合法的 http(s) URL，凭据不要放在 URL 中。")
        if address.scheme == "http" and address.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("非本机接口必须使用 HTTPS。")
    for field in ("env_refs", "headers_env"):
        mapping = result.get(field, {})
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValueError(f"{field} 必须是字符串映射。")
    return result


def parse_mcp_import(text: str, *, force_disabled: bool = True) -> list[dict[str, Any]]:
    """兼容常见 CLI 的 mcpServers 形状，也支持单条 RemChat 配置。"""

    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("MCP 配置必须是 JSON 对象。")
    if "mcpServers" in value:
        servers = value["mcpServers"]
        if not isinstance(servers, dict):
            raise ValueError("mcpServers 必须是对象。")
        rows = []
        for identifier, server in servers.items():
            if not isinstance(server, dict):
                raise ValueError(f"{identifier} 的配置不是对象。")
            row = {**server, "id": identifier, "name": identifier}
            row["transport"] = "http" if row.get("url") else "stdio"
            rows.append(row)
    else:
        rows = [value]
    result = []
    for row in rows:
        if force_disabled:
            row["enabled"] = False
        result.append(validate_mcp(row))
    return result
