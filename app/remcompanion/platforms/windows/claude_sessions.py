"""Read-only, bounded Claude Code session records; never start a model.

JSON projection skips unselected values without decoding their strings. Thinking,
tool output, Bash arguments, attachments and assistant prose are not inspected
or retained. Only public human prompts and the structured TodoWrite plan are
content allowlists. A recorded response stop is not proof a task has finished.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import re
import stat
import threading
import time

from ...project_trace.recorder import _redact
from ...project_trace.files import allowed_path
from .codex_catalog import workspace_path

HEADER_BYTES = 64 * 1024
READ_BYTES = 256 * 1024
DISCOVERY_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 65536
MAX_PROJECTS = 4096
MAX_SESSIONS = 16384
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_CACHED = 256
MAX_ITEMS = 64
ANCHOR_BYTES = 256
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_JSON = json.JSONDecoder()


def _public(value, limit=200):
    return _redact(value)[:limit] if isinstance(value, str) else ""


def _title(value):
    return " ".join(_public(value, 2000).split())[:160]


def _workspace_key(value):
    if value[:8].casefold() == "\\\\?\\unc\\":
        value = "\\\\" + value[8:]
    elif re.match(r"^\\\\\?\\[A-Za-z]:[\\/]", value):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def _file_relative(workspace, value):
    """Only the reported path, never file contents or creation-time inference."""
    if not isinstance(value, str) or not value or len(value) > 4000 or not ntpath.isabs(workspace):
        return ""
    root = _workspace_key(workspace)
    candidate = _workspace_key(value)
    if not ntpath.isabs(candidate):
        if ntpath.splitdrive(candidate)[0]:
            return ""
        candidate = ntpath.join(root, candidate)
    try:
        if ntpath.commonpath((root, candidate)) != root:
            return ""
        relative = ntpath.relpath(candidate, root).replace("\\", "/")
        if len(relative) > 1000:
            return ""
        allowed_path(Path(workspace), relative)
        return relative
    except (ValueError, OSError):
        return ""


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    return stamp if math.isfinite(stamp) and 0 < stamp <= time.time() + 300 else None


def _plain(path):
    info = path.lstat()
    return not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT


def _skip(raw, position):
    """Lexically skip one JSON value, never decode ignored string contents."""
    while position < len(raw) and raw[position].isspace():
        position += 1
    start = position
    if position >= len(raw):
        raise ValueError("incomplete JSON")
    if raw[position] == '"':
        position += 1
        while position < len(raw):
            char = raw[position]
            if char == '"':
                return position + 1
            if char == "\\":
                position += 1
                if position >= len(raw) or raw[position] not in '"\\/bfnrtu':
                    raise ValueError("invalid JSON escape")
                if raw[position] == "u":
                    if not re.fullmatch(r"[0-9A-Fa-f]{4}", raw[position + 1:position + 5]):
                        raise ValueError("invalid Unicode escape")
                    position += 4
            elif ord(char) < 32:
                raise ValueError("invalid JSON string")
            position += 1
        raise ValueError("incomplete JSON string")
    if raw[position] in "[{":
        stack = []
        while position < len(raw):
            char = raw[position]
            if char == '"':
                position = _skip(raw, position)
                continue
            if char in "[{":
                stack.append(char)
                if len(stack) > 64:
                    raise ValueError("JSON nesting limit")
            elif char in "]}":
                if not stack or (stack.pop(), char) not in {( "[", "]"), ("{", "}")}:
                    raise ValueError("invalid JSON nesting")
                if not stack:
                    return position + 1
            position += 1
        raise ValueError("incomplete JSON container")
    while position < len(raw) and raw[position] not in ",]} \t\r\n":
        position += 1
    if not re.fullmatch(r"(?:null|true|false|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)", raw[start:position]):
        raise ValueError("invalid JSON primitive")
    return position


def _project(raw, position, specification):
    while position < len(raw) and raw[position].isspace():
        position += 1
    if specification is None:
        return None, _skip(raw, position)
    if callable(specification):
        return specification(raw, position)
    if specification is True:
        end = _skip(raw, position)
        if raw[position] in "[{" or end - position > 16384:
            return None, end
        return _JSON.raw_decode(raw, position)
    opener, closer = ("{", "}") if isinstance(specification, dict) else ("[", "]")
    if position >= len(raw) or raw[position] != opener:
        return None, _skip(raw, position)
    position += 1
    result = {} if opener == "{" else []
    while True:
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position < len(raw) and raw[position] == closer:
            return result, position + 1
        if opener == "{":
            end = _skip(raw, position)
            if raw[position] != '"' or end - position > 256:
                raise ValueError("invalid JSON key")
            key, position = _JSON.raw_decode(raw, position)
            while position < len(raw) and raw[position].isspace():
                position += 1
            if position >= len(raw) or raw[position] != ":":
                raise ValueError("invalid JSON object")
            value, position = _project(raw, position + 1, specification.get(key))
            if key in specification:
                result[key] = value
        else:
            value, position = _project(raw, position, specification[0] if len(result) < 128 else None)
            if len(result) < 128:
                result.append(value)
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position < len(raw) and raw[position] == closer:
            return result, position + 1
        if position >= len(raw) or raw[position] != ",":
            raise ValueError("invalid JSON container")
        position += 1
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position < len(raw) and raw[position] == closer:
            raise ValueError("invalid trailing JSON comma")


_USAGE = {key: True for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
_META = {key: True for key in ("type", "sessionId", "cwd", "timestamp", "uuid", "promptId", "isMeta", "isSidechain", "customTitle", "aiTitle")}
_META["message"] = {"id": True, "role": True, "model": True, "stop_reason": True, "usage": _USAGE}
_BLOCK = {key: True for key in ("type", "id", "name", "tool_use_id", "is_error")}


def _index_entries(raw, position):
    """Project all bounded index entries, without the message-block 128 cap."""
    if raw[position] != "[":
        return _project(raw, position, None)
    position += 1
    result = []
    fields = {key: True for key in ("sessionId", "summary", "firstPrompt", "customTitle", "projectPath", "isSidechain", "created", "modified")}
    while True:
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position < len(raw) and raw[position] == "]":
            return result, position + 1
        if len(result) >= MAX_SESSIONS:
            raise ValueError("Claude index entry budget reached")
        value, position = _project(raw, position, fields)
        if isinstance(value, dict):
            result.append(value)
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position < len(raw) and raw[position] == "]":
            return result, position + 1
        if position >= len(raw) or raw[position] != ",":
            raise ValueError("invalid Claude index array")
        position += 1


def _record(line, *, metadata=False):
    try:
        raw = line.decode("utf-8")
        value, end = _project(raw, 0, _META)
        if raw[end:].strip() or not isinstance(value, dict):
            return None
        if metadata or value.get("type") not in {"user", "assistant"}:
            return value
        public_prompt = value.get("type") == "user" and value.get("isMeta") is not True
        def block(text, position):
            result, end = _project(text, position, _BLOCK)
            if not isinstance(result, dict):
                return {}, end
            if public_prompt and result.get("type") == "text":
                result, end = _project(text, position, {"type": True, "text": True})
            elif value.get("type") == "assistant" and result.get("type") == "tool_use" and result.get("name") == "TodoWrite":
                plan, end = _project(text, position, {"input": {"todos": [{"id": True, "content": True, "status": True}]}})
                result.update(plan)
            elif value.get("type") == "assistant" and result.get("type") == "tool_use" and result.get("name") in {"Edit", "Write"}:
                paths, end = _project(text, position, {"input": {"file_path": True}})
                result.update(paths)
            return result, end
        def content(text, position):
            if text[position] == "[":
                return _project(text, position, [block])
            return _project(text, position, True if public_prompt else None)
        extra, _ = _project(raw, 0, {"message": {"content": content}})
        if isinstance(extra.get("message"), dict):
            message = value.get("message")
            if isinstance(message, dict):
                message.update(extra["message"])
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError, IndexError):
        return None


class ClaudeSessionSource:
    tool_id = "claude_code"
    source = "claude_session_record"
    name = "Claude Code 本机会话记录"
    description = "只读 Claude 本机会话记录；不保证实时运行状态，不读取思考、命令参数或工具输出。"

    def __init__(self, root=None):
        selected = Path(root).expanduser() if root is not None else Path(os.environ.get("USERPROFILE") or Path.home()) / ".claude/projects"
        if selected.name.casefold() == ".claude" and (selected / "projects").is_dir():
            selected /= "projects"
        if not selected.exists():
            raise ValueError("没有可读的 Claude 本机会话记录目录。")
        if not _plain(selected):
            raise ValueError("不跟随 Claude 会话记录的符号链接或重解析点。")
        self.directory = selected.resolve(strict=True)
        if not self.directory.is_dir() and not (self.directory.is_file() and self.directory.suffix.casefold() == ".jsonl"):
            raise ValueError("请选择 Claude projects 记录目录或明确的 JSONL 会话文件。")
        self.location = self.data_directory = str(self.directory)
        self.paths, self.rows, self.cursors, self.calls = {}, {}, {}, {}
        self.catalog_metadata = {}
        self.discovery_summary = {}
        self.lock = threading.RLock()
        self.closed = False

    def _files(self):
        if self.directory.is_file():
            return [self.directory]
        candidates, directories, visited = [], [self.directory], 0
        self._index_paths = []
        while directories and visited < MAX_ENTRIES:
            directory = directories.pop(0)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > MAX_ENTRIES:
                            break
                        path = Path(entry.path)
                        try:
                            if not _plain(path):
                                continue
                            if entry.is_file(follow_symlinks=False) and path.suffix.casefold() == ".jsonl" and not path.name.startswith("agent-"):
                                candidates.append(path)
                            elif entry.is_file(follow_symlinks=False) and path.name == "sessions-index.json":
                                self._index_paths.append(path)
                            elif directory == self.directory and entry.is_dir(follow_symlinks=False) and len(directories) < MAX_PROJECTS:
                                directories.append(path)
                        except OSError:
                            continue
            except OSError:
                continue
        self._scan_limited = bool(directories or visited >= MAX_ENTRIES)
        return candidates

    def _index_metadata(self, path):
        self._safe(path)
        info = path.stat()
        if info.st_size > MAX_INDEX_BYTES:
            raise ValueError("Claude index byte budget reached")
        with path.open("r", encoding="utf8") as file:
            raw = file.read(MAX_INDEX_BYTES + 1)
        if len(raw.encode("utf8")) > MAX_INDEX_BYTES:
            raise ValueError("Claude index byte budget reached")
        data, end = _project(raw, 0, {"version": True, "originalPath": True, "entries": _index_entries})
        if raw[end:].strip() or not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Claude index schema changed")
        after = path.stat()
        if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("Claude index changed during read")
        result = {}
        for item in data.get("entries", ()):
            identifier = item.get("sessionId")
            if item.get("isSidechain") is True or not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
                continue
            title = item.get("customTitle") or item.get("summary") or item.get("firstPrompt")
            project = item.get("projectPath")
            if project is None or project == "":
                project = data.get("originalPath")
            result[identifier] = {"id": identifier, "name": _title(title),
                                  "workspace": workspace_path(project),
                                  "created_at": _stamp(item.get("created")), "last_activity": _stamp(item.get("modified")),
                                  "session_kind": "main", "parent_thread_id": "",
                                  "metadata_location": str(path)}
        return result

    def _safe(self, path):
        if not _plain(path) or path.resolve(strict=True) != path:
            raise ValueError("Claude 会话记录路径已被替换；请重新发现。")
        if self.directory.is_dir() and not path.is_relative_to(self.directory):
            raise ValueError("Claude 会话记录位于所选目录之外。")

    @staticmethod
    def _segment(file, start, count):
        file.seek(max(0, start - 1))
        boundary = start == 0 or file.read(1) == b"\n"
        file.seek(start)
        raw = file.read(count)
        if not boundary:
            first = raw.find(b"\n")
            raw = raw[first + 1:] if first >= 0 else b""
        return [line for line in raw.split(b"\n")[:-1] if line and len(line) <= READ_BYTES]

    def _metadata(self, path, budget):
        self._safe(path)
        result, used = {}, 0
        with path.open("rb") as file:
            size = os.fstat(file.fileno()).st_size
            for start, count in ((0, min(size, HEADER_BYTES)), (max(0, size - READ_BYTES), min(size, READ_BYTES))):
                if count > budget - used:
                    break
                for line in self._segment(file, start, count):
                    value = _record(line, metadata=True)
                    if not value or value.get("isSidechain") is True:
                        continue
                    identifier = value.get("sessionId")
                    if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier):
                        if result.get("id") and result["id"] != identifier:
                            return {}, used + count
                        result["id"] = identifier
                    if "cwd" in value:
                        result["workspace"] = workspace_path(value["cwd"])
                    title = value.get("customTitle") or value.get("aiTitle")
                    stamp = _stamp(value.get("timestamp"))
                    if stamp:
                        result["last_activity"] = max(stamp, result.get("last_activity") or 0)
                        result["created_at"] = min(stamp, result.get("created_at") or stamp)
                    if title:
                        result["name"] = _title(title)
                    elif not result.get("name") and value.get("type") == "user" and value.get("isMeta") is not True:
                        public = _record(line)
                        content = public.get("message", {}).get("content") if public else None
                        texts = [content] if isinstance(content, str) else [v.get("text", "") for v in content if isinstance(v, dict) and v.get("type") == "text"] if isinstance(content, list) else []
                        result["name"] = _title(" ".join(v for v in texts if isinstance(v, str)))
                    message = value.get("message")
                    if isinstance(message, dict) and message.get("model"):
                        result["model"] = _public(message["model"])
                used += count
        return result, used

    def _base(self, identifier, meta):
        return {"id": identifier, "name": meta.get("name") or "Claude " + identifier[:12], "workspace": workspace_path(meta.get("workspace")),
                "state": "unknown", "started_at": None, "finished_at": None, "run_id": "", "model": meta.get("model", ""),
                "usage": {}, "usage_scope": "turn", "usage_id": "", "usage_time": None,
                "usage_model": "", "usage_id_scope": "session", "usage_events": [], "plan": [], "requests": [], "tools": [], "files": [],
                "goal": None, "memory": None, "request_summary": "", "last_activity": None, "source": self.source,
                "session_kind": "main", "parent_thread_id": "", "created_at": meta.get("created_at"),
                "metadata_only": False, "metadata_location": meta.get("metadata_location", ""),
                "message": "Claude 本机记录（有界读取，非实时状态）；任务开始/结束未知。只显示实际上报的公开字段。"}

    def discover(self, workspace=""):
        if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
            raise ValueError("工作空间路径无效。")
        with self.lock:
            if self.closed:
                raise RuntimeError("Claude 会话记录观察已关闭。")
            ranked = []
            warnings = []
            for path in self._files():
                try:
                    self._safe(path)
                    ranked.append((path.stat().st_mtime_ns, str(path), path))
                except (OSError, ValueError):
                    continue
            indexed = {}
            for path in getattr(self, "_index_paths", ()):
                try:
                    for identifier, meta in self._index_metadata(path).items():
                        meta["path"] = path.parent / (identifier + ".jsonl")
                        indexed.setdefault(identifier, meta)
                except (OSError, ValueError, UnicodeError, RecursionError, TypeError, IndexError):
                    warnings.append("一个会话摘要索引不可读或格式改变；仍扫描普通主会话文件。")
            self.catalog_metadata = indexed
            result, indexed_ids = [], set()
            for _, _, path in sorted(ranked, reverse=True):
                if len(result) >= MAX_SESSIONS:
                    warnings.append("已达到安全扫描上限，请按项目目录筛选其余会话。")
                    break
                try:
                    meta, _ = self._metadata(path, HEADER_BYTES + READ_BYTES)
                except (OSError, ValueError):
                    continue
                identifier = meta.get("id")
                if not identifier:
                    continue
                index = indexed.get(identifier, {})
                # The public index title is what a human sees in Claude's list.
                # Never trust fullPath to open a file outside the chosen root.
                # A JSONL cwd is the actual reported directory; an index's
                # project root must not retarget project-file reads to an
                # ancestor (or hide an explicitly invalid reported cwd).
                meta = {**meta, **{key: value for key, value in index.items()
                                  if key != "path" and not (key == "workspace" and "workspace" in meta)
                                  and value is not None and value != ""}}
                # A record excluded by its actual cwd is not a missing record.
                # Do not re-add it through an index's stale project directory.
                indexed_ids.add(identifier)
                if workspace and _workspace_key(meta.get("workspace", "")) != _workspace_key(workspace):
                    continue
                if identifier in self.paths and self.paths[identifier] is not None and self.paths[identifier] != path:
                    continue
                if identifier in self.paths and self.paths[identifier] is None:
                    self.rows[identifier] = self._base(identifier, meta)
                self.paths[identifier] = path
                row = self.rows.setdefault(identifier, self._base(identifier, meta))
                row.update({key: meta[key] for key in ("name", "workspace", "model") if meta.get(key)})
                if "workspace" in meta:
                    row["workspace"] = workspace_path(meta["workspace"])
                row.update(created_at=meta.get("created_at"), last_activity=max(meta.get("last_activity") or 0, row.get("last_activity") or 0) or None,
                           metadata_only=False, metadata_location=meta.get("metadata_location", ""))
                result.append({key: row[key] for key in ("id", "name", "workspace", "state", "model", "source", "session_kind", "parent_thread_id", "created_at", "last_activity", "metadata_only", "metadata_location")})
            for identifier, meta in sorted(indexed.items(), key=lambda pair: (pair[1].get("last_activity") or 0, pair[0]), reverse=True):
                if identifier in indexed_ids or (workspace and _workspace_key(meta.get("workspace", "")) != _workspace_key(workspace)):
                    continue
                if len(result) >= MAX_SESSIONS:
                    warnings.append("已达到安全扫描上限，请按项目目录筛选其余会话。")
                    break
                self.paths[identifier] = None
                # Missing local records must not expose a previous parsed
                # transcript/usage as if it were a fresh metadata-only row.
                row = self.rows[identifier] = self._base(identifier, meta)
                row.update({key: meta[key] for key in ("name", "workspace", "created_at", "last_activity") if meta.get(key)})
                row["metadata_only"] = True
                row["message"] = "仅有 Claude 会话列表摘要；对应 JSONL 记录不在本机所选目录。可查看名称与项目，公开输入、工具、用量及当前状态未上报。"
                result.append({key: row[key] for key in ("id", "name", "workspace", "state", "model", "source", "session_kind", "parent_thread_id", "created_at", "last_activity", "metadata_only", "metadata_location")})
            if getattr(self, "_scan_limited", False):
                warnings.append("目录扫描达到安全边界；请按具体 projects 子目录发现其余会话。")
            self.discovery_summary = {"found": len(result), "metadata_only": sum(self.paths[row["id"]] is None for row in result),
                                      "limit_reached": bool(warnings), "message": " ".join(dict.fromkeys(warnings))}
            if warnings:
                for row in result:
                    row["discovery_message"] = self.discovery_summary["message"]
            return result

    @staticmethod
    def _upsert(values, item, fields):
        index = next((index for index, old in enumerate(values) if all(old.get(key) == item.get(key) for key in fields)), None)
        if index is None:
            values.append(item)
        else:
            values[index] = item
        del values[:-MAX_ITEMS]

    def _consume(self, row, value):
        if value.get("sessionId") != row["id"] or value.get("isSidechain") is True:
            return
        if "cwd" in value:
            row["workspace"] = workspace_path(value["cwd"])
        stamp = _stamp(value.get("timestamp"))
        if stamp:
            row["last_activity"] = max(stamp, row.get("last_activity") or 0)
        title = value.get("customTitle") or value.get("aiTitle")
        if title:
            row["name"] = _title(title)
        message = value.get("message")
        if not isinstance(message, dict):
            return
        if message.get("model"):
            row["model"] = _public(message["model"])
        content = message.get("content", [])
        if value.get("type") == "user" and value.get("isMeta") is not True:
            texts = [content] if isinstance(content, str) else [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"] if isinstance(content, list) else []
            text = _public("\n".join(item for item in texts if isinstance(item, str)), 2000)
            if text.strip():
                identifier = value.get("promptId") or value.get("uuid") or hashlib.sha256((row["id"] + str(value.get("timestamp")) + text).encode()).hexdigest()
                self._upsert(row["requests"], {"id": _public(identifier, 160), "text": text, "timestamp": stamp}, ("id",))
                row["request_summary"] = text
        calls = self.calls.setdefault(row["id"], {})
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and value.get("type") == "assistant":
                identifier, name = _public(item.get("id"), 160), _public(item.get("name"), 200)
                if not identifier or not name:
                    continue
                category = "mcp" if name.startswith("mcp__") else "skill" if name == "Skill" else "command" if name in {"Bash", "PowerShell"} else "other"
                if identifier in calls and calls[identifier]["name"] != name:
                    continue  # A native call ID cannot acquire another tool name.
                call = calls.setdefault(identifier, {"name": name, "category": category})
                if name in {"Edit", "Write"} and isinstance(item.get("input"), dict):
                    relative = _file_relative(row["workspace"], item["input"].get("file_path"))
                    if relative:
                        call["file_path"] = relative
                self._upsert(row["tools"], {"id": identifier, "name": name, "state": "started", "category": category, "timestamp": stamp, "summary": "Claude 记录了工具调用；未保留参数。"}, ("id", "state"))
                if name == "TodoWrite" and isinstance(item.get("input"), dict):
                    todos = item["input"].get("todos")
                    if isinstance(todos, list):
                        row["plan"] = [{"id": _public(todo.get("id"), 160) or "todo-" + str(index), "title": _public(todo.get("content"), 200), "state": todo["status"], "timestamp": stamp}
                                       for index, todo in enumerate(todos[:60]) if isinstance(todo, dict) and todo.get("status") in {"pending", "in_progress", "completed"} and isinstance(todo.get("content"), str) and todo["content"].strip()]
            elif item.get("type") == "tool_result" and value.get("type") == "user":
                identifier = _public(item.get("tool_use_id"), 160)
                if identifier in calls and ("is_error" not in item or item["is_error"] is None or type(item["is_error"]) is bool):
                    call = calls[identifier]
                    failed = item.get("is_error") is True
                    self._upsert(row["tools"], {"id": identifier, "name": call["name"], "category": call["category"], "state": "failed" if failed else "completed", "timestamp": stamp, "summary": "Claude 记录中工具已返回；未保留工具输出。"}, ("id", "state"))
                    # Claude's public tool_result contract makes is_error optional;
                    # a matched non-error return proves success, not task completion.
                    if not failed and call.get("file_path"):
                        self._upsert(row["files"], {"id": identifier, "path": call["file_path"], "operation": "modified"}, ("id",))
        while len(calls) > MAX_CACHED:
            calls.pop(next(iter(calls)))
        usage = message.get("usage")
        if value.get("type") == "assistant" and isinstance(usage, dict) and message.get("id") and message.get("stop_reason"):
            values = {key: number for key, number in usage.items() if type(number) is int and number >= 0}
            tokens = {}
            if "input_tokens" in values:
                tokens["input_tokens"] = values["input_tokens"] + values.get("cache_creation_input_tokens", 0) + values.get("cache_read_input_tokens", 0)
            if "output_tokens" in values:
                tokens["output_tokens"] = values["output_tokens"]
            if "cache_read_input_tokens" in values:
                tokens["cached_input_tokens"] = values["cache_read_input_tokens"]
            if "input_tokens" in tokens and "output_tokens" in tokens:
                tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
            if tokens:
                row.update(usage=tokens, usage_id=_public(message["id"], 160), usage_time=stamp,
                           usage_model=_public(message.get("model")),
                           usage_id_scope="source" if str(message["id"]).startswith(("msg_", "chatcmpl-", "resp_")) else "session")
                self._upsert(row["usage_events"],
                    {"usage": tokens, "usage_id": row["usage_id"], "usage_scope": "turn",
                     "usage_time": stamp, "usage_model": row["usage_model"],
                     "usage_id_scope": row["usage_id_scope"]}, ("usage_id",))

    def snapshot(self, session_id):
        with self.lock:
            if self.closed:
                raise RuntimeError("Claude 会话记录观察已关闭。")
            if session_id not in self.paths:
                raise ValueError("请先发现并选择该 Claude 本机会话。")
            path = self.paths[session_id]
            if path is None:
                return deepcopy(self.rows[session_id])
            self._safe(path)
            with path.open("rb") as file:
                info = os.fstat(file.fileno())
                identity = (info.st_dev, info.st_ino)
                previous = self.cursors.get(session_id)
                reset = not previous or previous[:2] != identity or previous[2] > info.st_size
                if previous and not reset:
                    file.seek(max(0, previous[2] - ANCHOR_BYTES))
                    reset = hashlib.sha256(file.read(min(ANCHOR_BYTES, previous[2]))).digest() != previous[3]
                if reset:
                    meta, _ = self._metadata(path, HEADER_BYTES + READ_BYTES)
                    if meta.get("id") != session_id:
                        raise ValueError("Claude 会话记录的身份已改变；请重新发现。")
                    meta.update({key: value for key, value in self.catalog_metadata.get(session_id, {}).items()
                                 if key != "path" and not (key == "workspace" and "workspace" in meta)
                                 and value is not None and value != ""})
                    self.rows[session_id] = self._base(session_id, meta)
                    self.calls.pop(session_id, None)
                    for line in self._segment(file, 0, min(info.st_size, HEADER_BYTES)):
                        value = _record(line)
                        if value:
                            self._consume(self.rows[session_id], value)
                    if info.st_size > HEADER_BYTES + READ_BYTES:
                        self.rows[session_id]["plan"] = []  # A skipped interval may contain a later revision.
                row = self.rows[session_id]
                offset = max(0, info.st_size - READ_BYTES) if reset else previous[2]
                if info.st_size - offset > READ_BYTES:
                    offset = max(0, info.st_size - READ_BYTES)
                    row["plan"] = []
                file.seek(max(0, offset - 1))
                boundary = offset == 0 or file.read(1) == b"\n"
                file.seek(offset)
                raw = file.read(READ_BYTES)
                last = raw.rfind(b"\n")
                complete = raw[:last + 1] if last >= 0 else b""
                if not boundary:
                    first = complete.find(b"\n")
                    complete = complete[first + 1:] if first >= 0 else b""
                for line in complete.splitlines():
                    value = _record(line)
                    if value:
                        self._consume(row, value)
                end = offset + last + 1 if last >= 0 else offset + len(raw) if len(raw) == READ_BYTES else offset
                file.seek(max(0, end - ANCHOR_BYTES))
                anchor = hashlib.sha256(file.read(min(ANCHOR_BYTES, end))).digest()
                self.cursors[session_id] = (*identity, end, anchor)
            return deepcopy(row)

    def sync_marker(self, session_id):
        with self.lock:
            row = self.rows.get(session_id, {})
            metadata = (row.get("name"), row.get("last_activity"), row.get("metadata_only"))
            path = self.paths.get(session_id)
            if path is None:
                return metadata
            info = path.stat()
            return (*metadata, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def close(self):
        with self.lock:
            self.closed = True
            self.paths.clear()
            self.rows.clear()
            self.cursors.clear()
            self.calls.clear()
            self.catalog_metadata.clear()
