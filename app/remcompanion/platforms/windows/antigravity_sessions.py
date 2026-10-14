"""Read-only Antigravity archive discovery and bounded generation usage.

The official hook documentation identifies the desktop/IDE data roots and UUID
conversation IDs. SQLite generation usage was independently checked against
public ccusage source and numeric-only local samples. Only allowlisted protobuf
usage/model/identity/timestamp envelopes are projected; unknown byte strings,
prompts, thinking, tool output, PB archives, and credentials are never decoded.
A file's modification time is not evidence that an agent is currently running.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time

from .antigravity_catalog import read_summary_file, read_state_index, read_database_metadata

MAX_FILES = 65536
MAX_SESSIONS = 16384
CONVERSATION_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ARCHIVE_SUFFIXES = frozenset({".pb", ".db"})
KNOWN_ROOTS = ("antigravity-ide", "antigravity")
MAX_USAGE_ROWS = 128
MAX_USAGE_BLOB = 1024 * 1024
MAX_USAGE_BYTES = 8 * 1024 * 1024
MAX_WIRE_FIELDS = 4096
MAX_EXACT_INTEGER = (1 << 53) - 1
USAGE_SCHEMA = "antigravity-gen-metadata-cm1-usage4-v1"
LIMITATION = (
    "同步 Antigravity 会话列表的公开标题/项目摘要及本机存档索引；不是实时运行清单。"
    "列表摘要可能滞后，远程或已迁移记录可能只有摘要；当前状态、时长未知。SQLite 可提供近期生成调用的数字用量，不保证完整历史。"
    "不依据文件修改时间猜测，不解码凭据、消息、内部思维、工具参数/输出或 PB 存档正文。")


def _wire_varint(data: memoryview, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ValueError("overflowing protobuf varint")
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, position
    raise ValueError("overflowing protobuf varint")


def _wire_projection(data, wanted: dict[int, int]) -> dict:
    """Skip unknown byte ranges structurally; never decode unknown text.

    wanted maps protobuf field numbers to wire types. Views retain bounded
    buffers only for the duration of a call; no raw buffers enter snapshots.
    """
    view = memoryview(data)
    position = 0
    result = {}
    count = 0
    while position < len(view):
        count += 1
        if count > MAX_WIRE_FIELDS:
            raise ValueError("protobuf field budget")
        tag, position = _wire_varint(view, position)
        number, wire = tag >> 3, tag & 7
        if not number or number >= (1 << 29):
            raise ValueError("invalid protobuf field")
        value = None
        if wire == 0:
            value, position = _wire_varint(view, position)
        elif wire == 2:
            size, position = _wire_varint(view, position)
            end = position + size
            if end > len(view):
                raise ValueError("truncated protobuf bytes")
            if number in wanted:
                value = view[position:end]
            position = end
        elif wire in (1, 5):
            position += 8 if wire == 1 else 4
            if position > len(view):
                raise ValueError("truncated protobuf fixed field")
        else:
            raise ValueError("unsupported protobuf wire type")
        if number in wanted:
            if wire != wanted[number]:
                raise ValueError("protobuf schema changed")
            if number in result and result[number] != value:
                raise ValueError("conflicting protobuf scalar")
            result[number] = value
    return result


def _identifier(value, limit=128) -> str:
    if value is None or not 0 < len(value) <= limit:
        return ""
    # Only fields publicly documented as model/usage IDs are decoded here.
    try:
        text = bytes(value).decode("ascii")
    except UnicodeDecodeError:
        return ""
    return text if re.fullmatch(r"[A-Za-z0-9_.:/-]+", text) else ""


def _usage_timestamp(value) -> float | None:
    if value is None:
        return None
    fields = _wire_projection(value, {1: 0, 2: 0})
    seconds, nanos = fields.get(1), fields.get(2, 0)
    if (isinstance(seconds, int) and 946684800 <= seconds <= time.time() + 86400
            and 0 <= nanos < 1_000_000_000):
        return seconds + nanos / 1_000_000_000
    return None


def _generation_usage(blob: bytes, session_id: str, index: int) -> dict | None:
    if not isinstance(blob, bytes) or len(blob) > MAX_USAGE_BLOB:
        raise ValueError("generation metadata byte budget")
    root = _wire_projection(blob, {1: 2})
    if 1 not in root:
        raise ValueError("missing generation chat model")
    # chatModel unknown fields may contain private prompts: skip, never decode.
    chat = _wire_projection(root[1], {3: 0, 4: 2, 9: 2, 19: 2, 21: 2})
    if 4 not in chat:
        return None
    usage = _wire_projection(chat[4], {1: 0, 2: 0, 3: 0, 4: 0, 5: 0,
                                               7: 2, 9: 0, 10: 0, 11: 2, 12: 2})
    counts = {key: usage.get(key, 0) for key in (2, 3, 4, 5, 9, 10)}
    if any(number > MAX_EXACT_INTEGER for number in counts.values()):
        raise ValueError("inexact generation token counter")
    if not any(counts.values()):
        return None
    # Proto3 absent scalar counters default to zero. Output already includes
    # reasoning/visible buckets; never add those a second time.
    if counts[9] + counts[10] > counts[3]:
        raise ValueError("inconsistent generation output buckets")
    input_tokens = counts[2] + counts[4] + counts[5]
    total = input_tokens + counts[3]
    if total > MAX_EXACT_INTEGER:
        raise ValueError("inexact generation token total")
    native_id = next((_identifier(usage.get(key)) for key in (11, 12, 7)
                      if _identifier(usage.get(key))), "")
    identifier = "ag:" + native_id if native_id else f"{session_id}:gen:{index}"
    model = _identifier(chat.get(19)) or _identifier(chat.get(21))
    enum = usage.get(1, chat.get(3))
    if usage.get(1) and chat.get(3) and usage[1] != chat[3]:
        model, enum = "", None
    if not model and isinstance(enum, int) and 0 < enum <= MAX_EXACT_INTEGER:
        model = f"antigravity-model-{enum}"
    stamp = None
    if 9 in chat:
        generation_info = _wire_projection(chat[9], {4: 2})
        stamp = _usage_timestamp(generation_info.get(4))
    return {"usage_id": identifier, "usage_id_scope": "source", "usage_scope": "turn", "usage_kind": "generation",
            "usage_time": stamp, "model": model, "usage_model": model,
            "usage": {"input_tokens": input_tokens, "output_tokens": counts[3],
                      "cached_input_tokens": counts[5], "total_tokens": total}}


def _readonly_usage(path: Path, session_id: str) -> tuple[list[dict], str]:
    """Read recent canonical generation rows, not step copies or full history."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400
            or path.resolve(strict=True) != path):
        raise ValueError("generation database target changed")
    wal = path.with_name(path.name + "-wal")
    try:
        if wal.stat().st_size:
            return [], "WAL 有未合并记录；本次未读取用量，避免写入外部共享状态或发布过期值。"
    except FileNotFoundError:
        pass
    deadline = time.monotonic() + 1.0
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1.0)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        # No arbitrary table/view/virtual-table code or generated columns.
        schema = connection.execute("SELECT type,sql FROM sqlite_master WHERE name='gen_metadata' LIMIT 1").fetchone()
        if (not schema or schema[0] != "table" or not isinstance(schema[1], str)
                or "VIRTUAL" in schema[1].upper()):
            return [], "没有已验证的 gen_metadata 普通表；用量未知。"
        columns = {row[1]: row for row in connection.execute("PRAGMA table_xinfo(gen_metadata)")}
        if (not {"idx", "data"} <= columns.keys() or columns["idx"][2].upper() != "INTEGER"
                or columns["idx"][5] != 1 or columns["idx"][6] != 0
                or columns["data"][2].upper() != "BLOB" or columns["data"][6] != 0):
            return [], "gen_metadata 字段格式改变；用量未知。"
        connection.execute("BEGIN")
        rows = connection.execute("SELECT idx,length(data) FROM gen_metadata ORDER BY idx DESC LIMIT ?",
                                  (MAX_USAGE_ROWS + 1,)).fetchall()
        used = 0
        events = {}
        skipped = 0
        for index, size in rows[:MAX_USAGE_ROWS]:
            if time.monotonic() > deadline:
                raise TimeoutError("generation usage deadline")
            if (not isinstance(index, int) or not 0 <= index <= MAX_EXACT_INTEGER
                    or not isinstance(size, int) or not 0 < size <= MAX_USAGE_BLOB):
                skipped += 1
                continue
            if used + size > MAX_USAGE_BYTES:
                skipped += 1
                break
            row = connection.execute("SELECT data FROM gen_metadata WHERE idx=? LIMIT 1", (index,)).fetchone()
            if not row or not isinstance(row[0], bytes) or len(row[0]) != size:
                raise ValueError("generation record changed")
            used += size
            event = _generation_usage(row[0], session_id, index)
            if event is not None:
                # Descending rows: most recent copy of one native usage wins.
                events.setdefault(event["usage_id"], event)
        after = path.lstat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError("Antigravity database changed during read")
        if (not stat.S_ISREG(after.st_mode) or getattr(after, "st_file_attributes", 0) & 0x400
                or path.resolve(strict=True) != path):
            raise ValueError("generation database target changed")
        try:
            if wal.stat().st_size:
                raise ValueError("Antigravity WAL changed during read")
        except FileNotFoundError:
            pass
        coverage = f"仅近期至多 {MAX_USAGE_ROWS} 条生成记录（本次 {len(events)} 次调用）；不是完整历史用量。"
        if skipped or len(rows) > MAX_USAGE_ROWS:
            coverage += " 存在范围/字节上限或未读记录。"
        return list(reversed(list(events.values()))), coverage
    finally:
        connection.close()


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()).resolve()


def _known_root(gemini: Path) -> Path | None:
    """Check two named children only; never enumerate a general .gemini tree."""
    boundary = gemini.resolve()
    for name in KNOWN_ROOTS:
        candidate = boundary / name
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(boundary)
            if resolved.is_dir():
                return resolved
        except (OSError, ValueError, RuntimeError):
            continue
    return None


def default_root() -> Path:
    """Default includes two named app stores, never backups or a full tree."""
    return _home() / ".gemini"


def _normalize_root(root: Path | str | None) -> tuple[Path, Path]:
    if root is not None and not isinstance(root, (str, Path)):
        raise ValueError("Antigravity 记录位置必须是目录或 .pb/.db 文件路径。")
    if isinstance(root, str):
        root = root.strip()
        if len(root) >= 2 and root[0] == root[-1] == '"':
            root = root[1:-1].strip()
        if not root:
            root = None
    selected = default_root() if root is None else Path(root).expanduser().resolve()
    if selected == Path(selected.anchor) or selected == _home():
        raise ValueError("请明确选择 Antigravity 数据根、conversations 目录或 UUID 命名的 .pb/.db 存档；不扫描磁盘或用户目录。")
    if selected.name.casefold() == ".gemini":
        selected = _known_root(selected)
        if selected is None:
            raise ValueError("所选 .gemini 下未找到 antigravity-ide 或 antigravity；未扫描其他目录。")
    try:
        selected = selected.resolve(strict=True)
        if selected.is_file():
            if (selected.suffix.casefold() not in ARCHIVE_SUFFIXES
                    or not CONVERSATION_ID.fullmatch(selected.stem)):
                raise ValueError("Antigravity 单文件索引仅接受 UUID 命名的 .pb 或 .db；不读取其二进制正文。")
            return selected, selected
        if not selected.is_dir():
            raise ValueError("Antigravity 记录位置不是可读取的普通文件或目录。")
        # An explicitly selected app root descends into exactly one child. A
        # selected conversations/relocated archive folder is never widened.
        child = selected / "conversations"
        if child.is_dir():
            directory = child.resolve(strict=True)
            directory.relative_to(selected)
        elif selected.name.casefold() in KNOWN_ROOTS:
            raise ValueError("Antigravity 数据根没有 conversations 目录；未读取其他状态、设置或凭据文件。")
        else:
            directory = selected
    except (OSError, RuntimeError) as error:
        raise ValueError("未找到可读的 Antigravity 存档目录；请明确选择已有数据根或 conversations 目录。") from error
    return selected, directory


class AntigravitySessionSource:
    """Bounded archive/usage metadata, not a native runtime connection."""

    tool_id = "antigravity"
    name = "Antigravity 本机会话存档索引"
    source = "antigravity_session_record"

    def __init__(self, root: Path | str | None = None):
        self.root, self.directory = _normalize_root(root)
        default_scope = root is None or self.root in {
            _home() / ".gemini", *(_home() / ".gemini" / name for name in KNOWN_ROOTS)}
        pairs = [(self.root, self.directory)]
        if default_scope:
            for name in KNOWN_ROOTS:
                candidate = _home() / ".gemini" / name
                try:
                    pair = _normalize_root(candidate)
                    if pair not in pairs:
                        pairs.append(pair)
                except ValueError:
                    continue
        self.roots = tuple(pair[0] for pair in pairs)
        self.directories = tuple(pair[1] for pair in pairs)
        self.default_scope = default_scope
        self.location = str(self.root)
        self.data_directory = self.location
        self.description = (LIMITATION + " 记录位置：" + self.location
                            + ("；默认包含 antigravity 与 antigravity-ide 两个标准存档目录及对应会话列表摘要。" if default_scope else "；仅所选记录目录。")
                            + f"安全上限 {MAX_FILES} 个目录条目 / {MAX_SESSIONS} 个候选，达到时明确提示。")
        self.paths: dict[str, tuple[Path, ...]] = {}
        self.rows = {}
        self.discovery_summary = {}
        self.lock = threading.RLock()
        self.closed = False

    def _check_open(self):
        if self.closed:
            raise RuntimeError("Antigravity 会话索引观察已关闭。")
        for directory in self.directories:
            if directory.resolve(strict=True) != directory:
                raise ValueError("Antigravity 索引目录目标已改变；请重新发现会话。")

    def _archive_info(self, path: Path) -> tuple[int, Path] | None:
        """Stat only; do not open/decode an archive, DB, or WAL."""
        if (path.suffix.casefold() not in ARCHIVE_SUFFIXES
                or not CONVERSATION_ID.fullmatch(path.stem)):
            return None
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            return None
        resolved = path.resolve(strict=True)
        if resolved != path:
            return None
        if not any((directory.is_dir() and resolved.is_relative_to(directory)) or resolved == directory for directory in self.directories):
            return None
        return info.st_mtime_ns, resolved

    def _metadata_indexes(self):
        indexes, warnings = {}, []
        readers = [(root / "agyhub_summaries_proto.pb", read_summary_file) for root in self.roots if root.is_dir()]
        if self.default_scope:
            appdata = Path(os.environ.get("APPDATA") or _home() / "AppData/Roaming")
            readers.extend((appdata / name / "User/globalStorage/state.vscdb", read_state_index)
                           for name in ("Antigravity", "Antigravity IDE"))
        for path, reader in readers:
            if not path.exists():
                continue
            try:
                values = reader(path)
                for identifier, item in values.items():
                    previous = indexes.get(identifier)
                    if not previous or (item.get("last_activity") or 0) > (previous.get("last_activity") or 0):
                        indexes[identifier] = {**item, "metadata_location": str(path)}
            except (OSError, ValueError, sqlite3.Error, UnicodeError):
                warnings.append("一个会话列表摘要不可读、正在写入或格式改变；该来源本次未同步，仍保留可发现的存档候选。")
        return indexes, warnings

    def discover(self, workspace: str = "") -> list[dict]:
        with self.lock:
            self._check_open()
            if not isinstance(workspace, str):
                raise ValueError("工作区筛选必须是路径字符串。")
            if len(workspace) > 4000:
                raise ValueError("工作区筛选路径过长。")
            candidates: dict[str, list[tuple[int, Path]]] = {}
            indexed, warnings = self._metadata_indexes()
            for directory in self.directories:
                scan = None if directory.is_file() else os.scandir(directory)
                paths = [directory] if scan is None else (directory / entry.name for entry in scan)
                try:
                    for index, path in enumerate(paths):
                        if index >= MAX_FILES:
                            warnings.append("目录条目达到安全扫描上限；请选择更具体的存档目录。")
                            break
                        try:
                            archive = self._archive_info(path)
                        except (OSError, ValueError, RuntimeError):
                            continue
                        if archive is not None:
                            identifier = path.stem.lower()
                            candidates.setdefault(identifier, []).append(archive)
                finally:
                    if scan is not None:
                        scan.close()
            for identifier in indexed:
                candidates.setdefault(identifier, [])
            # Modification timestamps order the *archive index* only. They are
            # never returned as agent activity, duration, or a running state.
            ordered = sorted(candidates, key=lambda key: (
                indexed.get(key, {}).get("last_activity") or 0,
                max((item[0] for item in candidates[key]), default=0), key), reverse=True)
            result = []
            children = 0
            for identifier in ordered:
                meta = dict(indexed.get(identifier, {}))
                for _, path in candidates[identifier]:
                    if path.suffix.casefold() != ".db":
                        continue
                    try:
                        db_meta = read_database_metadata(path, identifier)
                        if db_meta:
                            meta.update({key: value for key, value in db_meta.items() if value is not None and value != ""})
                    except (OSError, ValueError, sqlite3.Error, UnicodeError):
                        continue
                if meta.get("session_kind") == "child":
                    children += 1
                    continue
                if workspace.strip():
                    from .claude_sessions import _workspace_key
                    locations = meta.get("workspaces") or [meta.get("workspace", "")]
                    if not any(_workspace_key(value) == _workspace_key(workspace.strip()) for value in locations):
                        continue
                if len(result) >= MAX_SESSIONS:
                    warnings.append("会话候选达到安全扫描上限；请按项目或明确存档目录筛选。")
                    break
                self.paths[identifier] = tuple(item[1] for item in candidates[identifier])
                row = {"id": identifier, "name": meta.get("name") or "Antigravity " + identifier[:12],
                       "workspace": meta.get("workspace", ""), "state": "unknown", "model": "", "source": self.source,
                       "session_kind": meta.get("session_kind", "unknown"), "parent_thread_id": "",
                       "created_at": meta.get("created_at"), "last_activity": meta.get("last_activity"),
                       "metadata_location": meta.get("metadata_location", ""),
                       "archive_locations": [str(path) for path in self.paths[identifier]],
                       "metadata_only": not bool(self.paths[identifier])}
                self.rows[identifier] = row
                result.append(dict(row))
            self.discovery_summary = {"found": len(result), "metadata_only": sum(row["metadata_only"] for row in result),
                                      "excluded_children": children, "limit_reached": bool(warnings),
                                      "message": " ".join(dict.fromkeys(warnings)), "roots": [str(root) for root in self.roots]}
            if warnings:
                for row in result:
                    row["discovery_message"] = self.discovery_summary["message"]
            return result

    def snapshot(self, session_id: str) -> dict:
        with self.lock:
            self._check_open()
            if not isinstance(session_id, str) or session_id not in self.paths:
                raise ValueError("请先发现并明确选择此 Antigravity 会话索引。")
            meta = self.rows.get(session_id, {})
            formats = set()
            databases = []
            for path in self.paths[session_id]:
                try:
                    archive = self._archive_info(path)
                except FileNotFoundError:
                    continue  # The same UUID may have migrated .pb -> .db.
                if archive is None:
                    raise ValueError("所选 Antigravity 存档已改变或不再是普通文件；请重新发现。")
                formats.add(path.suffix.casefold())
                if path.suffix.casefold() == ".db":
                    databases.append(path)
            if not formats and not meta.get("metadata_location"):
                raise ValueError("所选 Antigravity 会话存档已不可用；不猜测会话结束或仍在运行。")
            events = {}
            coverage = ("仅有会话列表摘要，本机没有对应存档；公开输入、工具及用量未上报。" if not formats else
                        "PB 存档未解码；无已验证的 SQLite 生成用量。")
            for path in databases:
                try:
                    samples, coverage = _readonly_usage(path, session_id)
                    for sample in samples:
                        events.setdefault(sample["usage_id"], sample)
                except (OSError, sqlite3.Error, ValueError, TimeoutError):
                    coverage = "用量读取失败、超时或格式改变；本次用量未知，不使用旧计数推测。"
                    events.clear()
                    break
            samples = list(events.values())[-MAX_USAGE_ROWS:]
            latest = samples[-1] if samples else {}
            return {**meta, "id": session_id, "name": meta.get("name") or "Antigravity " + session_id[:12],
                    "workspace": meta.get("workspace", ""), "state": "unknown", "started_at": None, "finished_at": None,
                    "run_id": "", "model": latest.get("model", ""), "usage": latest.get("usage", {}),
                    "usage_scope": "turn", "usage_kind": "generation", "usage_id": latest.get("usage_id", ""),
                    "usage_id_scope": latest.get("usage_id_scope", "session"),
                    "usage_time": latest.get("usage_time"), "usage_model": latest.get("model", ""),
                    "usage_events": samples, "usage_coverage": coverage, "usage_schema": USAGE_SCHEMA,
                    "plan": [], "requests": [], "tools": [],
                    "files": [], "goal": None, "memory": None, "last_activity": meta.get("last_activity"),
                    "source": self.source,
                    "message": LIMITATION + (" 已发现存档格式：" + " / ".join(sorted(formats)) + "。 " if formats else " ")
                    + coverage + (" 摘要来源：" + meta["metadata_location"] if meta.get("metadata_location") else "")}

    def sync_marker(self, session_id):
        with self.lock:
            row = self.rows.get(session_id, {})
            result = [row.get("name"), row.get("last_activity"), row.get("metadata_only")]
            for path in self.paths.get(session_id, ()):
                try:
                    info = path.stat()
                    result.extend((str(path), info.st_ino, info.st_size, info.st_mtime_ns))
                    if path.suffix.casefold() == ".db":
                        wal = path.with_name(path.name + "-wal")
                        if wal.is_file():
                            info = wal.stat()
                            result.extend((info.st_size, info.st_mtime_ns))
                except OSError:
                    result.append("unavailable")
            return tuple(result)

    def close(self):
        with self.lock:
            self.closed = True
            self.paths.clear()
            self.rows.clear()
