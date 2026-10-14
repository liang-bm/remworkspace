"""User-owned personality file: read snapshots, reject stale saves, keep backups.

File version checks are optimistic, not an OS-wide compare-and-swap. An
uncooperative external writer can still race the last check and atomic replace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import uuid


INSTRUCTION_FILE = Path('data/global-instruction.md')
BACKUP_DIRECTORY = Path('data/instruction-backups')


@dataclass(frozen=True)
class InstructionSnapshot:
    content: str
    revision: str | None


@dataclass(frozen=True)
class InstructionSave:
    snapshot: InstructionSnapshot
    backup: Path | None


class InstructionError(ValueError):
    """A reviewed, user-facing instruction-file constraint."""


class InstructionConflict(InstructionError):
    """The on-disk bytes are no longer the version the editor loaded."""


def _reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise InstructionError('人格文件或备份目录包含链接，未写入。请检查真实路径。')


def instruction_path(root: Path) -> Path:
    project = root.resolve(strict=True)
    _reject_link(project / 'data')
    path = project / INSTRUCTION_FILE
    _reject_link(path)
    if path.exists() and not path.is_file():
        raise InstructionError('人格文件路径不是普通文件，未写入。')
    return path


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _revision(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def read_instruction(root: Path) -> InstructionSnapshot:
    raw = _read_bytes(instruction_path(root))
    content = raw.decode('utf-8-sig').strip() if raw is not None else ''
    return InstructionSnapshot(content, _revision(raw))


def save_instruction(root: Path, content: str, expected: InstructionSnapshot) -> InstructionSave:
    normalized = content.strip()
    if not normalized:
        raise InstructionError('全局指令不能为空')
    path = instruction_path(root)
    raw = _read_bytes(path)
    if _revision(raw) != expected.revision:
        raise InstructionConflict('人格文件已被外部修改或删除，未覆盖。请先重新读取；界面草稿已保留。')
    if raw is not None and (not path.stat().st_mode & 0o222 or not os.access(path, os.W_OK)):
        raise InstructionError('人格文件为只读，未写入。请检查权限；界面草稿已保留。')
    data = (normalized + '\n').encode('utf-8')
    if raw == data:
        return InstructionSave(InstructionSnapshot(normalized, _revision(data)), None)
    path.parent.mkdir(parents=True, exist_ok=True)
    instruction_path(root)  # Recheck newly created parents before writing.
    backup = None
    if raw is not None:
        directory = path.parent / BACKUP_DIRECTORY.name
        _reject_link(directory)
        directory.mkdir(exist_ok=True)
        _reject_link(directory)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = directory / f'global-instruction-{stamp}-{uuid.uuid4().hex}.md'
        with backup.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    descriptor, temporary_name = tempfile.mkstemp(prefix='.instruction-', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        instruction_path(root)
        if _revision(_read_bytes(path)) != expected.revision:
            raise InstructionConflict('人格文件已被外部修改或删除，未覆盖。请先重新读取；界面草稿已保留。')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return InstructionSave(InstructionSnapshot(normalized, _revision(data)), backup)
