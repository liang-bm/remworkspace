"""Bounded local project instruction discovery, never host prompt attribution.

Only the selected native session's existing, absolute working directory is
used. No archive-to-workspace guessing, global instructions, credentials,
host settings, Git contents, project execution or automatic writes occur here.
Call this synchronous I/O boundary on a worker, not on the Tk event thread.

``text`` is the original UTF-8 file text and must not be replaced with a
redacted preview before an explicit edit. ``fingerprint`` hashes original
bytes; an editor must recheck it before writing. Candidate discovery does not
prove a host injected the file, or reveal the host's full system prompt.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import time


MAX_LAYERS = 24
MAX_FILES = 64
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_RULE_ENTRIES = 128
SCAN_SECONDS = 2.0
TOOLS = {"codex", "claude_code", "antigravity"}
COVERAGE = (
    "仅查看所选会话实际工作目录内存在的本地项目指令候选；不读取全局配置、"
    "凭据或 .git 内容，不代表宿主当前已注入这些文件，也不是完整系统提示词。"
)


def _linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    # Python versions before Path.is_junction still expose Windows reparse
    # attributes via lstat; do not silently follow a junction on those builds.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _safe_components(path: Path) -> None:
    """Do not follow links even when they resolve back inside the project."""
    for component in (*reversed(path.parents), path):
        if _linked(component):
            raise ValueError("linked_path")


def _local_directory(value: str) -> Path:
    if not value or len(value) > 4096 or "\0" in value:
        raise ValueError("unknown_working_directory")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        raise ValueError("remote_working_directory")
    # Extended local drive spelling is reported by native Codex sessions.
    # UNC and WSL/network locations are not silently mapped to this machine.
    if value.lower().startswith("\\\\?\\unc\\"):
        raise ValueError("remote_working_directory")
    if value.startswith("\\\\?\\"):
        if not re.match(r"^\\\\\?\\[A-Za-z]:[\\/]", value):
            raise ValueError("remote_working_directory")
        value = value[4:]
    if value.startswith(("\\\\", "//")):
        raise ValueError("remote_working_directory")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("non_local_working_directory")
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", value):
        raise ValueError("non_local_working_directory")
    _safe_components(path)
    if not path.is_dir():
        raise ValueError("working_directory_unavailable")
    return path.resolve(strict=True)


def _broad_directory(path: Path) -> bool:
    if path == path.parent:
        return True
    try:
        return path == Path.home().resolve()
    except (OSError, RuntimeError):
        return False


def _bounded_layers(cwd: Path, limit: int, deadline: float):
    """Find nearest Git marker without reading it; otherwise use cwd alone."""
    inspected = []
    current = cwd
    for _ in range(limit):
        if time.monotonic() > deadline:
            return [cwd], "deadline", False
        if _broad_directory(current):
            break
        inspected.append(current)
        marker = current / ".git"
        # Worktrees have a .git file, not a directory. Existence is enough for
        # a scope boundary; its text is deliberately never read or resolved.
        try:
            info = marker.lstat()
            if _linked(marker):
                return [cwd], "linked_git_marker", False
            if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
                return list(reversed(inspected)), "nearest_git_marker", True
        except FileNotFoundError:
            pass
        except OSError:
            return [cwd], "root_unavailable", False
        current = current.parent
    return [cwd], "working_directory_only", len(inspected) < limit


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _read_candidate(path: Path, root: Path, source_kind: str, limit: int) -> dict:
    row = {"path": str(path), "name": path.name,
           "relative_path": path.relative_to(root).as_posix(),
           "source_kind": source_kind, "injection_confirmed": False,
           "status": "unavailable", "text": "", "fingerprint": "",
           "size_bytes": None, "mtime_ns": None, "encoding": "",
           "newline": "", "editable": False, "bytes_read": 0}
    try:
        _safe_components(path)
        path.resolve(strict=True).relative_to(root)
        before = path.stat()
        row.update(size_bytes=before.st_size, mtime_ns=before.st_mtime_ns)
        if not stat.S_ISREG(before.st_mode):
            row["status"] = "not_regular_file"
            return row
        if before.st_size > limit:
            row["status"] = "file_too_large"
            return row
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            _safe_components(path)
            if identity(before) != identity(opened):
                row["status"] = "changed_during_read"
                return row
            data = stream.read(limit + 1)
            row["bytes_read"] = len(data)
        after = path.stat()
        if identity(before) != identity(opened) or identity(opened) != identity(after):
            row["status"] = "changed_during_read"
            return row
        _safe_components(path)
        if len(data) > limit:
            row["status"] = "file_too_large"
            return row
        if b"\0" in data:
            row["status"] = "not_utf8_text"
            return row
        text = data.decode("utf-8-sig")
        row.update(status="ok", text=text,
                   fingerprint=hashlib.sha256(data).hexdigest(),
                   encoding="utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8",
                   newline="\r\n" if b"\r\n" in data else "\n", editable=True)
    except UnicodeError:
        row["status"] = "not_utf8_text"
    except PermissionError:
        row["status"] = "permission_denied"
    except FileNotFoundError:
        row["status"] = "disappeared"
    except ValueError as error:
        row["status"] = "linked_path" if str(error) == "linked_path" else "outside_project"
    except OSError:
        row["status"] = "read_error"
    return row


def discover_project_instructions(
    working_directory: str,
    tool_id: str,
    *,
    max_layers: int = MAX_LAYERS,
    max_files: int = MAX_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict:
    """Return actual local candidate files in root-to-cwd order, read-only.

    Codex uses one precedence-selected AGENTS file per level. Claude and
    Antigravity are expressly local candidates, not confirmed injected rules.
    Limits may be lowered by callers, but never raised beyond safety budgets.
    """
    result = {"working_directory": working_directory if isinstance(working_directory, str) else "",
              "project_root": "", "tool_id": tool_id if isinstance(tool_id, str) else "",
              "status": "unavailable", "files": [], "coverage": COVERAGE,
              "complete": True, "scanned_directories": [], "scope_basis": ""}
    tool_id = result["tool_id"]
    if tool_id not in TOOLS:
        result["status"] = "unsupported_tool"
        return result
    limits = (max_layers, max_files, max_file_bytes, max_total_bytes)
    if any(type(number) is not int or number <= 0 for number in limits):
        raise ValueError("Instruction discovery limits must be positive integers")
    max_layers = min(max_layers, MAX_LAYERS)
    max_files = min(max_files, MAX_FILES)
    max_file_bytes = min(max_file_bytes, MAX_FILE_BYTES)
    max_total_bytes = min(max_total_bytes, MAX_TOTAL_BYTES)
    try:
        cwd = _local_directory(result["working_directory"])
        if _broad_directory(cwd):
            result["status"] = "broad_working_directory"
            return result
        deadline = time.monotonic() + SCAN_SECONDS
        layers, basis, complete = _bounded_layers(cwd, max_layers, deadline)
        root = layers[0]
        result.update(working_directory=str(cwd), project_root=str(root),
                      scope_basis=basis, complete=complete)
        if basis != "nearest_git_marker":
            result["coverage"] += " 未确认仓库根；仅查看工作目录本层，不推断祖先或远程映射。"
        total_bytes = 0
        for directory in layers:
            if time.monotonic() > deadline:
                result["complete"] = False
                break
            result["scanned_directories"].append(str(directory))
            candidates = []
            if tool_id == "codex":
                override = directory / "AGENTS.override.md"
                candidates.append((override if _exists(override) else directory / "AGENTS.md", "codex_precedence_candidate"))
            elif tool_id == "claude_code":
                candidates.extend((directory / name, "claude_local_candidate")
                                  for name in ("CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md"))
            else:
                candidates.append((directory / "AGENTS.md", "antigravity_local_candidate"))
                rules = directory / ".agents/rules"
                if _exists(rules):
                    _safe_components(rules)
                    if rules.is_dir():
                        names = []
                        with os.scandir(rules) as entries:
                            for index, entry in enumerate(entries):
                                if index >= MAX_RULE_ENTRIES or time.monotonic() > deadline:
                                    result["complete"] = False
                                    break
                                if entry.name.lower().endswith(".md"):
                                    names.append(entry.name)
                        candidates.extend((rules / name, "antigravity_local_rule_candidate") for name in sorted(names))
            for path, source_kind in candidates:
                if time.monotonic() > deadline or len(result["files"]) >= max_files:
                    result["complete"] = False
                    break
                if not _exists(path):
                    continue
                remaining = max_total_bytes - total_bytes
                if remaining <= 0:
                    result["complete"] = False
                    break
                row = _read_candidate(path, root, source_kind, min(max_file_bytes, remaining))
                result["files"].append(row)
                total_bytes += row["bytes_read"]
                if row["status"] != "ok":
                    result["complete"] = False
            if not result["complete"] and (len(result["files"]) >= max_files or total_bytes >= max_total_bytes):
                break
        result["status"] = "ok" if result["files"] and result["complete"] else "partial" if not result["complete"] else "no_files"
        if not result["complete"]:
            result["coverage"] += " 受读取错误、文件变化或安全额度限制；列表不保证完整，错误项不会提供可编辑正文。"
    except ValueError as error:
        result["status"] = str(error) if str(error) in {
            "unknown_working_directory", "remote_working_directory", "non_local_working_directory",
            "working_directory_unavailable", "linked_path"} else "invalid_working_directory"
        result["complete"] = False
    except PermissionError:
        result.update(status="permission_denied", complete=False)
    except (OSError, RuntimeError):
        result.update(status="read_error", complete=False)
    return result
