"""Strict, read-only projection of Antigravity's conversation summary indexes.

The IDE index wraps each CascadeTrajectorySummary in base64. The desktop
SummariesState map embeds that message directly. Field paths were checked
against public source descriptors; only identity/title/workspace/timestamps and
parent metadata are projected. Unknown strings, prompts and credentials are
never decoded. Cached status is deliberately not a live running signal.
"""
from __future__ import annotations

import base64
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import unquote, urlsplit

from ...project_trace.recorder import _redact

MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_INDEX_ENTRIES = 16384
MAX_FIELDS = 65536
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SUMMARY_KEYS = ("antigravityUnifiedStateSync.trajectorySummaries", "unifiedStateSync.trajectorySummaries")


def _varint(data, position):
    result = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ValueError("overflowing protobuf varint")
        result |= (byte & 127) << shift
        if byte < 128:
            return result, position
    raise ValueError("overflowing protobuf varint")


def project(data, wanted):
    """Retain only selected field slices, including repeated messages."""
    view, position, fields = memoryview(data), 0, {}
    for _ in range(MAX_FIELDS):
        if position == len(view):
            return fields
        tag, position = _varint(view, position)
        field, wire = tag >> 3, tag & 7
        if not field or field >= 1 << 29:
            raise ValueError("invalid protobuf tag")
        value = None
        if wire == 0:
            value, position = _varint(view, position)
        elif wire == 2:
            size, position = _varint(view, position)
            end = position + size
            if end > len(view):
                raise ValueError("truncated protobuf bytes")
            if field in wanted:
                value = view[position:end]
            position = end
        elif wire in (1, 5):
            position += 8 if wire == 1 else 4
            if position > len(view):
                raise ValueError("truncated protobuf fixed value")
        else:
            raise ValueError("unsupported protobuf wire type")
        if field in wanted:
            if wanted[field] != wire:
                raise ValueError("summary schema changed")
            fields.setdefault(field, []).append(value)
    raise ValueError("summary field budget reached")


def _one(fields, key):
    values = fields.get(key, ())
    if len(values) > 1 and any(value != values[0] for value in values[1:]):
        raise ValueError("conflicting summary scalar")
    return values[0] if values else None


def _text(value, limit):
    if value is None or len(value) > limit * 4:
        return ""
    text = bytes(value).decode("utf8", errors="strict")
    return _redact(text)[:limit]


def _timestamp(value):
    if value is None:
        return None
    fields = project(value, {1: 0, 2: 0})
    seconds, nanos = _one(fields, 1), _one(fields, 2) or 0
    return seconds + nanos / 1e9 if (isinstance(seconds, int) and 946684800 <= seconds <= time.time() + 86400 and 0 <= nanos < 1e9) else None


def workspace_display(uri):
    """Decode only an allowlisted workspace URI, not a filesystem target."""
    if not isinstance(uri, str) or not uri:
        return ""
    value = urlsplit(uri)
    if value.scheme == "file":
        path = unquote(value.path)
        if value.netloc:
            return "\\\\" + value.netloc + path.replace("/", "\\")
        if re.match(r"^/[A-Za-z]:/", path):
            return path[1:].replace("/", "\\")
        return path  # A remote Linux path stays a Linux path, never a local drive.
    return uri if value.scheme in {"vscode-remote", "vscode", "vscode-vfs"} else ""


def metadata(blob):
    fields = project(blob, {1: 2, 2: 2, 5: 2, 6: 2, 7: 2, 8: 2, 17: 0})
    parent = _text(_one(fields, 5), 128)
    if parent and not UUID.fullmatch(parent):
        raise ValueError("invalid parent conversation identity")
    workspaces = []
    for value in fields.get(1, ())[:16]:
        item = project(value, {1: 2})
        uri = _text(_one(item, 1), 4000)
        display = workspace_display(uri)
        if display and display not in workspaces:
            workspaces.append(display)
    for value in fields.get(7, ())[:16]:
        display = workspace_display(_text(value, 4000))
        if display and display not in workspaces:
            workspaces.append(display)
    return {"workspace": workspaces[0] if workspaces else "", "workspaces": workspaces,
            "created_at": _timestamp(_one(fields, 2)), "parent_thread_id": parent.lower(),
            "session_kind": "child" if parent or fields.get(8) or (_one(fields, 17) or 0) > 0 else "main"}


def summary(blob, identifier):
    fields = project(blob, {1: 2, 3: 2, 7: 2, 9: 2, 17: 2})
    result = metadata(_one(fields, 17)) if fields.get(17) else {"session_kind": "unknown", "parent_thread_id": ""}
    result.update(id=identifier.lower(), name=" ".join(_text(_one(fields, 1), 200).split()),
                  last_activity=_timestamp(_one(fields, 3)), created_at=_timestamp(_one(fields, 7)))
    workspaces = []
    for value in fields.get(9, ())[:16]:
        item = project(value, {1: 2})
        display = workspace_display(_text(_one(item, 1), 4000))
        if display and display not in workspaces:
            workspaces.append(display)
    if workspaces:
        result.update(workspace=workspaces[0], workspaces=workspaces)
    return result


def summaries(blob, *, wrapped=False):
    if not isinstance(blob, bytes) or len(blob) > MAX_INDEX_BYTES:
        raise ValueError("summary index byte budget")
    entries = project(blob, {1: 2}).get(1, ())
    if len(entries) > MAX_INDEX_ENTRIES:
        raise ValueError("summary index entry budget")
    result = {}
    for raw in entries:
        entry = project(raw, {1: 2, 2: 2})
        identifier = _text(_one(entry, 1), 128)
        payload = _one(entry, 2)
        if not UUID.fullmatch(identifier) or payload is None:
            continue
        if wrapped:
            payload = _one(project(payload, {1: 2}), 1)
            if payload is None:
                continue
            payload = base64.b64decode(payload, validate=True)
        item = summary(payload, identifier)
        result[item["id"]] = item
    return result


def plain(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or path.resolve(strict=True) != path):
        raise ValueError("summary target is not a plain file")
    return info


def read_summary_file(path):
    before = plain(path)
    if before.st_size > MAX_INDEX_BYTES:
        raise ValueError("summary file too large")
    with path.open("rb") as file:
        blob = file.read(MAX_INDEX_BYTES + 1)
    after = plain(path)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("summary index changed during read")
    return summaries(blob)


def read_state_index(path):
    """Exact key reads only; immutable SQLite never creates external sidecars."""
    before = plain(path)
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("state index has unmerged WAL; current snapshot unavailable")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
    deadline = time.monotonic() + 1
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        table = connection.execute("SELECT type,sql FROM sqlite_master WHERE name='ItemTable'").fetchone()
        if not table or table[0] != "table" or "VIRTUAL" in (table[1] or "").upper():
            raise ValueError("state index schema changed")
        columns = {r[1]: r for r in connection.execute("PRAGMA table_xinfo(ItemTable)")}
        if not {"key", "value"} <= columns.keys() or any(columns[k][6] != 0 for k in ("key", "value")):
            raise ValueError("state index schema changed")
        result = {}
        for key in SUMMARY_KEYS:
            size = connection.execute("SELECT length(value) FROM ItemTable WHERE key=?", (key,)).fetchone()
            if not size or not size[0]:
                continue
            if not isinstance(size[0], int) or size[0] > MAX_INDEX_BYTES:
                raise ValueError("encoded state index byte budget")
            row = connection.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()
            if not row or not isinstance(row[0], (bytes, str)) or len(row[0]) != size[0]:
                raise ValueError("state index changed during read")
            for identifier, item in summaries(base64.b64decode(row[0], validate=True), wrapped=True).items():
                result.setdefault(identifier, item)
        after = plain(path)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("state index changed during read")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("state index WAL changed during read")
        return result
    finally:
        connection.close()


def read_database_metadata(path, identifier):
    """Read identity/workspace/parent blob only, never a step payload."""
    before = plain(path)
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("conversation metadata has unmerged WAL")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
    deadline = time.monotonic() + 1
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        for table_name, required in (("trajectory_meta", ("cascade_id",)), ("trajectory_metadata_blob", ("id", "data"))):
            table = connection.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (table_name,)).fetchone()
            if not table or table[0] != "table" or "VIRTUAL" in (table[1] or "").upper():
                raise ValueError("conversation metadata schema changed")
            columns = {r[1]: r for r in connection.execute("PRAGMA table_xinfo(" + table_name + ")")}
            if any(key not in columns or columns[key][6] != 0 for key in required):
                raise ValueError("conversation metadata schema changed")
        row = connection.execute("SELECT cascade_id FROM trajectory_meta LIMIT 2").fetchall()
        if len(row) != 1 or row[0][0] != identifier:
            raise ValueError("conversation metadata identity changed")
        size = connection.execute("SELECT length(data) FROM trajectory_metadata_blob WHERE id='main'").fetchone()
        if not size or not isinstance(size[0], int) or not 0 < size[0] <= 1024 * 1024:
            return {}
        row = connection.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchone()
        if not row or not isinstance(row[0], bytes) or len(row[0]) != size[0]:
            raise ValueError("conversation metadata changed during read")
        result = metadata(row[0])
        after = plain(path)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("conversation metadata changed during read")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("conversation metadata WAL changed during read")
        return result
    finally:
        connection.close()
