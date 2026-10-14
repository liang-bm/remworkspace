"""Read-only Codex conversation catalog, excluding native child/review threads.

Only catalog metadata is projected. No credentials, configuration, instructions,
message bodies, or model reasoning are read from the database. A selected archive
subtree remains the scope even when its parent has a richer native catalog.
"""
from __future__ import annotations

import json
from datetime import datetime
import math
from pathlib import Path
import re
import shlex
import stat
import sqlite3
import time

from ..path_conversion import workspace_path

MAX_ROWS = 4096
MAX_CHILDREN = 128
HEADER_BYTES = 64 * 1024
DEADLINE_SECONDS = 2.0
HEADER_FIELDS = frozenset({"id", "session_id", "source", "thread_source", "parent_thread_id",
                           "forked_from_id", "agent_path", "agent_nickname", "timestamp", "cwd", "model"})


def _text(value, limit=4000):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _native_stamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, OSError, OverflowError):
            return None
    if type(value) not in {int, float}:
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 < number <= time.time() + 300 else None


def _source(value):
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return ""
    return value


def classify(meta: dict, edge_parent="") -> tuple[str, str]:
    """User forks stay main conversations; a fork alone is not a spawn edge."""
    source = _source(meta.get("session_source", meta.get("source")))
    parent = _text(meta.get("parent_thread_id"), 256) or _text(edge_parent, 256)
    kind = _text(meta.get("thread_source"), 64)
    subagent = source.get("subagent") if isinstance(source, dict) else None
    if isinstance(subagent, dict):
        spawn = subagent.get("thread_spawn")
        if isinstance(spawn, dict):
            parent = parent or _text(spawn.get("parent_thread_id"), 256)
        return ("review" if kind == "guardian_review" or subagent.get("other") == "guardian" else "child", parent)
    if kind == "guardian_review":
        return "review", parent
    if kind == "subagent" or parent:
        return "child", parent
    if kind == "user" or source in ("cli", "vscode", "exec"):
        return "main", ""
    return "unknown", ""


def classify_source(meta: dict) -> dict:
    kind, parent = classify(meta)
    return {"is_main": kind in {"main", "unknown"},
            "session_kind": {"main": "user", "child": "subagent", "review": "guardian_review"}.get(kind, "unknown"),
            "parent_thread_id": parent}


def _value_end(raw: bytes, pos: int) -> int:
    if pos >= len(raw):
        raise ValueError("Incomplete metadata")
    if raw[pos] == 34:
        pos += 1
        while pos < len(raw):
            if raw[pos] == 92:
                pos += 2
            elif raw[pos] == 34:
                return pos + 1
            else:
                pos += 1
        raise ValueError("Incomplete metadata string")
    if raw[pos] in (123, 91):
        stack = [125 if raw[pos] == 123 else 93]
        pos += 1
        while pos < len(raw):
            token = raw[pos]
            if token == 34:
                pos = _value_end(raw, pos)
                continue
            if token in (123, 91):
                if len(stack) >= 64:
                    raise ValueError("Metadata nesting limit")
                stack.append(125 if token == 123 else 93)
            elif token in (125, 93):
                if token != stack.pop():
                    raise ValueError("Invalid metadata")
                if not stack:
                    return pos + 1
            pos += 1
        raise ValueError("Incomplete metadata container")
    start = pos
    while pos < len(raw) and raw[pos] not in b",]} \t\r\n":
        pos += 1
    if pos == start:
        raise ValueError("Invalid metadata value")
    return pos


def _fields(raw: bytes, allow: frozenset) -> dict:
    """Skip disallowed JSON string bodies without materializing them."""
    pos, result = 0, {}
    while pos < len(raw) and raw[pos] in b" \t\r\n":
        pos += 1
    if pos >= len(raw) or raw[pos] != 123:
        return result
    pos += 1
    while pos < len(raw):
        while pos < len(raw) and raw[pos] in b" ,\t\r\n":
            pos += 1
        if pos >= len(raw) or raw[pos] == 125:
            return result
        key_end = _value_end(raw, pos)
        key = json.loads(raw[pos:key_end]) if key_end - pos <= 128 else None
        pos = key_end
        while pos < len(raw) and raw[pos] in b" \t\r\n":
            pos += 1
        if pos >= len(raw) or raw[pos] != 58:
            raise ValueError("Missing metadata separator")
        pos += 1
        while pos < len(raw) and raw[pos] in b" \t\r\n":
            pos += 1
        end = _value_end(raw, pos)
        if key in allow:
            if key in result:
                raise ValueError("Duplicate metadata field")
            result[key] = raw[pos:end]
        pos = end
    return result


def header(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.readline(HEADER_BYTES + 1)
    if len(raw) > HEADER_BYTES or not raw.endswith(b"\n"):
        return {}
    try:
        top = _fields(raw, frozenset({"type", "payload"}))
        if json.loads(top.get("type", b"null")) != "session_meta":
            return {}
        return {key: json.loads(value) for key, value in _fields(top.get("payload", b"{}"), HEADER_FIELDS).items()}
    except (ValueError, TypeError, UnicodeError):
        return {}


def _within(path: Path, directory: Path) -> bool:
    try:
        resolved = _local_path(path).resolve()
        directory = _local_path(directory).resolve()
        if directory.is_file():
            return resolved == directory
        resolved.relative_to(directory)
        return True
    except (OSError, ValueError):
        return False


def _local_path(value) -> Path:
    text = str(value)
    if text.casefold().startswith("\\\\?\\unc\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return Path(text)


def _workspace(value: str) -> str:
    value = value.replace("/", "\\")
    if value.casefold().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return value.rstrip("\\").casefold()


def _catalog_root(directory: Path) -> Path | None:
    start = directory.parent if directory.is_file() else directory
    for candidate in (start, *list(start.parents)[:8]):
        if (candidate / "state_5.sqlite").is_file() or (candidate / "session_index.jsonl").is_file():
            return candidate
    return None


def _names(root: Path | None, deadline: float) -> dict:
    if root is None:
        return {}
    names = {}
    try:
        with (root / "session_index.jsonl").open("rb") as stream:
            for _ in range(MAX_ROWS):
                if time.monotonic() > deadline or stream.tell() >= 2 * 1024 * 1024:
                    break
                raw = stream.readline(HEADER_BYTES + 1)
                if not raw:
                    break
                if len(raw) > HEADER_BYTES:
                    break
                fields = _fields(raw, frozenset({"id", "thread_name"}))
                identifier = json.loads(fields.get("id", b"null"))
                name = _text(json.loads(fields.get("thread_name", b"null")), 200)
                if isinstance(identifier, str) and name:
                    names[identifier] = name
    except (OSError, ValueError, TypeError):
        pass
    return names


def _database(root: Path | None, directory: Path, deadline: float) -> tuple[list[dict], list[dict], dict] | None:
    if root is None or not (root / "state_5.sqlite").is_file():
        return None
    connection = None
    try:
        connection = sqlite3.connect((root / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0.25)
        connection.execute("PRAGMA query_only=1")
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
        if not {"id", "rollout_path", "source"}.issubset(columns):
            return None
        allowed = ("id", "rollout_path", "created_at", "updated_at", "recency_at", "source", "thread_source", "name", "title", "cwd", "archived", "agent_path", "agent_nickname", "model")
        selected = [column for column in allowed if column in columns]
        order = "recency_at" if "recency_at" in columns else "updated_at" if "updated_at" in columns else "id"
        archived = "archived=0 AND " if "archived" in columns else ""
        main_where = ("(thread_source IS NULL OR thread_source NOT IN ('subagent','guardian_review')) AND "
                      if "thread_source" in columns else "")
        main_where = "(" + main_where + "CASE WHEN json_valid(source) THEN json_type(source,'$.subagent') ELSE NULL END IS NULL)"
        normal = _local_path(directory).resolve().as_posix().rstrip("/")
        extended = "//?/UNC/" + normal[2:] if normal.startswith("//") else "//?/" + normal
        if directory.is_file():
            scoped = "(replace(rollout_path,char(92),'/')=? OR replace(rollout_path,char(92),'/')=?) AND "
            scope_values = (normal, extended)
        else:
            scoped = "(replace(rollout_path,char(92),'/') LIKE ? ESCAPE '!' OR replace(rollout_path,char(92),'/') LIKE ? ESCAPE '!') AND "
            scope_values = tuple(value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%" for value in (normal, extended))
        # Apply native main classification before either safety cap. A growing
        # stream of child/review rows cannot displace the user's older chats.
        prefix = "SELECT " + ",".join(selected) + " FROM threads WHERE " + archived + scoped
        suffix = " ORDER BY " + order + " DESC LIMIT ?"
        rows = [dict(zip(selected, row)) for row in connection.execute(prefix + main_where + suffix, (*scope_values, MAX_ROWS + 1))]
        child_rows = [dict(zip(selected, row)) for row in connection.execute(prefix + "NOT " + main_where + suffix, (*scope_values, MAX_ROWS + 1))]
        edges = {}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='thread_spawn_edges'")}
        if tables:
            for parent, child in connection.execute("SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges LIMIT ?", (MAX_ROWS,)):
                edges[child] = parent
        return rows, child_rows, edges
    except (OSError, sqlite3.Error, ValueError):
        return None
    finally:
        if connection is not None:
            connection.close()


def metadata(directory: Path, *, limit=4096, workspace="") -> dict:
    """Return main conversations and bounded, optional child metadata.

    ``database_available`` means native metadata was usable, not a live service.
    ``complete`` is false when the safety cap/deadline truncated the catalog.
    Child counts are verified in this bounded catalog, never inferred from names.
    """
    if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
        raise ValueError("工作空间路径无效。")
    directory = Path(directory).resolve()
    limit = max(1, min(int(limit), MAX_ROWS))
    deadline = time.monotonic() + DEADLINE_SECONDS
    root = _catalog_root(directory)
    names = _names(root, deadline)
    native = _database(root, directory, deadline)
    main_rows, child_rows, edges = native if native is not None else ([], [], {})
    complete = len(main_rows) <= MAX_ROWS and len(child_rows) <= MAX_ROWS
    rows = main_rows[:MAX_ROWS] + child_rows[:MAX_ROWS]
    # A rollout is appended before the asynchronous native catalog transaction
    # in some Codex versions. A healthy database must not hide these new files.
    # Inspect only bounded public headers of files not already catalogued.
    if directory.is_dir() or directory.is_file():
        candidates = []
        known_paths = {str(_local_path(row.get("rollout_path") or "").resolve()).casefold() for row in rows}
        def recent_files(folder):
            # Native YYYY/MM/DD trees: visit the recent named directories first
            # so a safety bound does not favor old subagent files over new chats.
            pending = [folder]
            visited = 0
            while pending:
                if time.monotonic() > deadline or visited >= MAX_ROWS * 4:
                    return
                current = pending.pop()
                try:
                    entries = []
                    for item in current.iterdir():
                        visited += 1
                        if visited >= MAX_ROWS * 4 or time.monotonic() > deadline:
                            break
                        entries.append(item)
                    entries.sort(key=lambda item: item.name)
                except OSError:
                    continue
                children = []
                for item in entries:
                    try:
                        info = item.lstat()
                    except OSError:
                        continue
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        continue
                    if not _within(item, directory):
                        continue
                    if stat.S_ISREG(info.st_mode) and item.suffix == ".jsonl":
                        yield item
                    elif stat.S_ISDIR(info.st_mode):
                        children.append(item)
                pending.extend(children)
        paths = [directory] if directory.is_file() else recent_files(directory)
        try:
            for index, path in enumerate(paths):
                if index >= MAX_ROWS or time.monotonic() > deadline:
                    complete = False
                    break
                if str(path.resolve()).casefold() not in known_paths and _within(path, directory):
                    candidates.append((path.stat().st_mtime_ns, path))
            for modified, path in sorted(candidates, reverse=True):
                if time.monotonic() > deadline:
                    complete = False
                    break
                row = header(path)
                # File mtime discovers changed/new files, but is not evidence of
                # conversation activity: copying an old rollout must not make
                # it displace the user's newest native conversation.
                row.update(rollout_path=str(path), updated_at=_native_stamp(row.get("timestamp")))
                rows.append(row)
        except OSError:
            complete = False
    mains, children = [], []
    for value in rows:
        if time.monotonic() > deadline:
            complete = False
            break
        identifier = _text(value.get("id") or value.get("session_id"), 256)
        path = _local_path(value.get("rollout_path") or "")
        if not identifier or not _within(path, directory):
            continue
        readable = path.is_file()
        cwd = workspace_path(value.get("cwd"))
        if workspace and _workspace(cwd) != _workspace(workspace):
            continue
        kind, parent = classify(value, edges.get(identifier, ""))
        if readable and kind != "main" and not parent:
            # Guardian reviews have a parent in native header metadata but no
            # thread_spawn_edges row in the observed native schema.
            try:
                kind, parent = classify({**value, **header(path)}, edges.get(identifier, ""))
            except OSError:
                pass
        row = {"id": identifier, "name": _text(value.get("name"), 200) or names.get(identifier) or _text(value.get("title"), 200) or "Codex " + identifier[:12],
               "cwd": cwd, "rollout_path": str(path.resolve()), "created_at": value.get("created_at") or value.get("timestamp"),
               "updated_at": value.get("recency_at") or value.get("updated_at"), "thread_source": _text(value.get("thread_source"), 64),
               "metadata_only": not readable,
               "session_source": _source(value.get("source")), "parent_thread_id": parent, "is_main": kind in {"main", "unknown"},
               "model": _text(value.get("model"), 200)}
        if kind in {"main", "unknown"}:
            mains.append(row)
        elif kind in ("child", "review"):
            row["name"] = _text(value.get("agent_nickname"), 200) or row["name"]
            row["kind"] = kind
            children.append(row)
    # Native timestamps only; files touched/copied later do not gain recency.
    mains.sort(key=lambda row: max(_native_stamp(row.get("updated_at")) or 0,
                                   _native_stamp(row.get("created_at")) or 0), reverse=True)
    complete = complete and len(mains) <= limit
    selected = mains[:limit]
    ancestry = {child["id"]: child["parent_thread_id"] for child in children}
    for main in selected:
        owned = []
        for child in children:
            parent, seen = child["parent_thread_id"], set()
            while parent in ancestry and parent not in seen:
                seen.add(parent)
                parent = ancestry[parent]
            if parent == main["id"]:
                owned.append(child)
        projected = [{"id": child["id"], "name": child["name"], "model": child["model"],
                      "created_at": child["created_at"], "state": "unknown", "parent_thread_id": child["parent_thread_id"],
                      "session_kind": "guardian_review" if child["kind"] == "review" else "subagent"} for child in owned[:MAX_CHILDREN]]
        main.update(children=projected, child_count=len(owned), children_truncated=len(owned) > MAX_CHILDREN)
    message = ("原生主会话目录与新增存档合并；子 Agent/自动审查不单独列出。" if native is not None else "原生目录不可用；按存档元数据筛选主会话。")
    if not complete:
        message += " 本次目录读取达到安全数量/时间边界；可按项目或明确目录缩小范围，不代表完整历史。"
    return {"rows": selected, "database_available": native is not None, "complete": complete,
            "catalog_message": message}


def public_history(directory: Path, session_id: str, *, limit=100) -> dict:
    """Indexed native human inputs, without loading whole item_json in Python.

    Native ``userMessage`` items were verified to contain human inputs, unlike
    arbitrary rollout response-item ``role=user`` instruction injections.
    Text elements only; image URLs, attached data and other items are ignored.
    """
    result = {"requests": [], "available": False, "coverage": "原生用户输入索引不可用。"}
    directory = Path(directory).resolve()
    root = _catalog_root(directory)
    identifier = _text(session_id, 256)
    if root is None or identifier != session_id or not identifier:
        return result
    catalog = None
    history = None
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        # Verify the requested thread's native rollout path is inside the user's
        # selected archive scope; selecting a day must not read a different day.
        catalog = sqlite3.connect((root / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0.25)
        catalog.execute("PRAGMA query_only=1")
        catalog.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        row = catalog.execute("SELECT rollout_path FROM threads WHERE id=? LIMIT 1", (identifier,)).fetchone()
        if row is None or not _within(Path(row[0]), directory):
            return result
        history = sqlite3.connect((root / "thread_history_1.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0.25)
        history.execute("PRAGMA query_only=1")
        history.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        columns = {row[1] for row in history.execute("PRAGMA table_info(thread_items)")}
        if not {"thread_id", "item_id", "rollout_ordinal", "created_at_ms", "item_json", "item_type"}.issubset(columns):
            return result
        # Require a native thread-prefixed index, not a full shared-history scan.
        indexed = False
        for index in history.execute("PRAGMA index_list(thread_items)"):
            name = index[1]
            if not isinstance(name, str) or not name.replace("_", "").isalnum():
                continue
            info = list(history.execute('PRAGMA index_info("' + name + '")'))
            eligible = not index[4]
            if index[4]:
                sql = history.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
                eligible = bool(sql and "whereitem_type='usermessage'" in "".join((sql[0] or "").lower().split()))
            if eligible and len(info) >= 2 and [cell[2] for cell in info[:2]] == ["thread_id", "rollout_ordinal"]:
                indexed = True
                break
        if not indexed:
            return result
        query = """
            WITH recent AS MATERIALIZED (
                SELECT item_id,rollout_ordinal,created_at_ms,item_json
                FROM thread_items WHERE thread_id=? AND item_type='userMessage'
                ORDER BY rollout_ordinal DESC LIMIT ?
            )
            SELECT recent.item_id,recent.rollout_ordinal,recent.created_at_ms,
                   substr(json_extract(element.value,'$.text'),1,12000),element.key
            FROM recent,json_each(recent.item_json,'$.content') AS element
            WHERE json_extract(element.value,'$.type')='text'
              AND json_type(element.value,'$.text')='text'
            ORDER BY recent.rollout_ordinal,CAST(element.key AS INTEGER)
        """
        grouped = {}
        for item_id, ordinal, stamp, text, _position in history.execute(query, (identifier, max(1, min(int(limit), 100)))):
            if not isinstance(text, str) or not text.strip():
                continue
            entry = grouped.setdefault(item_id, {"id": item_id, "text": "", "timestamp": stamp / 1000 if isinstance(stamp, (int, float)) and stamp > 0 else None,
                                                  "source": "native_user_message", "ordinal": ordinal})
            entry["text"] = (entry["text"] + ("\n" if entry["text"] else "") + text)[:12000]
        result.update(requests=list(grouped.values()), available=True,
                      coverage="最近 " + str(min(int(limit), 100)) + " 条原生用户输入（有界索引；不含系统注入与子 Agent 消息）。")
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    finally:
        if history is not None:
            history.close()
        if catalog is not None:
            catalog.close()
    return result


_POWERSHELL_READ = re.compile(
    r'^\s*"[^"\r\n]*[\\/]powershell\.exe"\s+(?:-NoProfile\s+)?-Command\s+"(?P<body>[^"\r\n]*)"\s*$', re.I)


def public_turn(directory: Path, session_id: str) -> dict:
    """Latest selected thread's indexed lifecycle metadata, never item bodies.

    A long rollout's tail often omits its current task_started event. The host's
    read-only projection retains that explicit start; its inProgress status is
    a recorded turn state, not proof that an OS process is currently alive.
    """
    result = {"available": False}
    directory = Path(directory).resolve()
    root = _catalog_root(directory)
    if root is None or not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", session_id):
        return result
    catalog = history = None
    deadline = time.monotonic() + 0.25
    try:
        catalog = sqlite3.connect((root / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0.1)
        catalog.execute("PRAGMA query_only=1")
        catalog.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        entry = catalog.execute("SELECT rollout_path FROM threads WHERE id=? LIMIT 1", (session_id,)).fetchone()
        if entry is None or not _within(_local_path(entry[0]), directory):
            return result
        history = sqlite3.connect((root / "thread_history_1.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0.1)
        history.execute("PRAGMA query_only=1")
        history.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        fields = ("turn_id", "status", "started_at", "completed_at", "rollout_ordinal")
        columns = {row[1] for row in history.execute("PRAGMA table_info(thread_turns)")}
        if not {"thread_id", *fields}.issubset(columns):
            return result
        indexed = False
        for index in history.execute("PRAGMA index_list(thread_turns)"):
            name = index[1]
            if index[4] or not isinstance(name, str) or not name.replace("_", "").isalnum():
                continue
            info = list(history.execute('PRAGMA index_info("' + name + '")'))
            if len(info) >= 2 and [cell[2] for cell in info[:2]] == ["thread_id", "rollout_ordinal"]:
                indexed = True
                break
        if not indexed:
            return result  # Never scan the shared history when its index is absent.
        entry = history.execute("SELECT " + ",".join(fields) +
                                " FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1",
                                (session_id,)).fetchone()
        if entry is not None:
            result.update(dict(zip(fields, entry)), available=True)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    finally:
        if history is not None:
            history.close()
        if catalog is not None:
            catalog.close()
    return result


def literal_skill_read(command, status, exit_code, *, allowed_roots):
    """A verified literal file-read request, NOT Skill execution or compliance.

    Never evaluate shell/JS, expand variables, follow branches, or read the file.
    Reject composite commands, globs, traversal and symlink escapes. A completed
    native command with exitCode 0 supports the narrow 'file read' claim only.
    """
    if not isinstance(command, str) or len(command) >= 16384:
        return None
    wrapped = _POWERSHELL_READ.fullmatch(command)
    body = wrapped.group('body') if wrapped else command
    if any(char in body for char in ';\n\r|$`&(){}'):
        return None
    try:
        tokens = shlex.split(body.replace('\\', '/'), posix=True)
    except ValueError:
        return None
    if not tokens or tokens[0].casefold() != 'get-content':
        return None
    path, seen, index = None, set(), 1
    while index < len(tokens):
        flag = tokens[index].casefold()
        if flag in {'-path', '-literalpath', '-raw', '-encoding'}:
            group = 'path' if flag in {'-path', '-literalpath'} else flag
            if group in seen:
                return None
            seen.add(group)
            if flag != '-raw':
                index += 1
                if index >= len(tokens):
                    return None
                if group == 'path':
                    if path is not None:
                        return None
                    path = tokens[index]
                elif tokens[index].casefold() not in {'utf8', 'utf-8'}:
                    return None
        elif not flag.startswith('-') and path is None:
            path = tokens[index]
        else:
            return None
        index += 1
    if not path or not path.casefold().endswith('/skill.md') or any(char in path for char in '*?[]'):
        return None
    if '..' in Path(path).parts:
        return None
    try:
        resolved = Path(path).resolve()
        if not any(resolved.is_relative_to(Path(root).resolve()) for root in allowed_roots):
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    name = resolved.parent.name
    if not name:
        return None
    success = status == 'completed' and type(exit_code) is int and exit_code == 0
    return {'name': name, 'evidence_kind': 'skill_file_read',
            'state': 'completed' if success else 'failed' if type(exit_code) is int and exit_code != 0 else 'unknown',
            'summary': '原生文件读取成功；不代表执行 Skill 脚本或证明遵循，单 Skill Token 未上报。' if success else
                       '公开 Skill 文件读取请求；未确认成功，单 Skill Token 未上报。'}


def public_tools(directory: Path, session_id: str, *, limit=200) -> dict:
    """Indexed, selected-thread native commands and literal Skill references.

    Only the verified commandExecution schema is supported. No command output,
    cwd, reasoning, arbitrary tool arguments, or Skill body leaves this function.
    Unsupported tool schemas remain unexpanded, never guessed from substrings.
    """
    result = {'tools': [], 'available': False, 'coverage': '原生公开工具索引不可用。'}
    directory = Path(directory).resolve()
    root = _catalog_root(directory)
    if root is None or not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        return result
    catalog = history = None
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        catalog = sqlite3.connect((root / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True, timeout=.25)
        catalog.execute('PRAGMA query_only=1')
        catalog.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        row = catalog.execute('SELECT rollout_path FROM threads WHERE id=? LIMIT 1', (session_id,)).fetchone()
        if not row or not _within(Path(row[0]), directory):
            return result
        history = sqlite3.connect((root / 'thread_history_1.sqlite').as_uri() + '?mode=ro', uri=True, timeout=.25)
        history.execute('PRAGMA query_only=1')
        history.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        indexed = False
        for index in history.execute('PRAGMA index_list(thread_items)'):
            name = index[1]
            if not index[4] and isinstance(name, str) and name.replace('_', '').isalnum():
                info = list(history.execute('PRAGMA index_info("' + name + '")'))
                indexed |= len(info) >= 2 and [cell[2] for cell in info[:2]] == ['thread_id', 'rollout_ordinal']
        if not indexed:
            return result
        query = """SELECT item_id,created_at_ms,
            substr(json_extract(item_json,'$.command'),1,16384),
            substr(json_extract(item_json,'$.status'),1,40),json_extract(item_json,'$.exitCode')
            FROM thread_items WHERE thread_id=? AND item_type='commandExecution'
            ORDER BY rollout_ordinal DESC LIMIT ?"""
        home = Path.home()
        roots = (home / '.codex/skills', home / '.agents/skills', home / '.codex/plugins')
        tools = []
        for identifier, stamp, command, status, exit_code in history.execute(query, (session_id, max(1, min(int(limit), 200)))):
            if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
                continue
            timestamp = stamp / 1000 if type(stamp) in {int, float} and stamp > 0 else None
            state = 'completed' if status == 'completed' else 'started' if status in {'inProgress', 'in_progress'} else 'unknown'
            if type(exit_code) is int and exit_code != 0:
                state = 'failed'
            tools.append({'id': identifier, 'name': 'CommandExecution', 'category': 'command', 'state': state,
                          'timestamp': timestamp, 'started_at': None, 'finished_at': timestamp if state in {'completed', 'failed'} else None,
                          'evidence_kind': 'native_command'})
            reference = literal_skill_read(command, status, exit_code, allowed_roots=roots)
            if reference:
                tools.append({**reference, 'id': identifier + ':skill-read', 'name': reference['name'] + ' · 参考读取',
                              'category': 'skill', 'timestamp': timestamp, 'started_at': None,
                              'finished_at': timestamp if reference['state'] in {'completed', 'failed'} else None})
        result.update(tools=list(reversed(tools)), available=True,
                      coverage='最近200条原生命令索引；仅严格单命令的 Skill.md 读取单列为参考，不代表 Skill 脚本执行；wrapper 内部 MCP 未展开。')
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    finally:
        if history is not None:
            history.close()
        if catalog is not None:
            catalog.close()
    return result
