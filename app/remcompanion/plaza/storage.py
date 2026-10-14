"""广场存档，每种活动一份易读 JSON；不接触聊天与长期记忆。"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any


class PlazaStore:
    """只在 data/plaza 内读写，损坏文件保留供用户检查。"""

    FILES = {"garden", "farm-world", "words", "companion", "gomoku", "cooking"}

    def __init__(self, root: Path | str) -> None:
        self.path = Path(root) / "data" / "plaza"
        self.path.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        if name not in self.FILES:
            raise ValueError("未知的广场存档名称")
        return self.path / f"{name}.json"

    def load(self, name: str) -> dict[str, Any]:
        path = self._file(name)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取 {path.name}，原文件已保留：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} 应为 JSON 对象，原文件已保留")
        return value

    def save(self, name: str, value: dict[str, Any]) -> bytes:
        """同目录临时文件 + replace，写入中断不会留下半份存档。"""
        destination = self._file(name)
        temporary: Path | None = None
        serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", suffix=".tmp", dir=self.path,
                prefix=f".{name}-", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
            return serialized.encode("utf-8")
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def backup_original(self, name: str, version: int, original: bytes) -> Path:
        """Retain exact pre-migration bytes, never overwrite an existing backup."""
        source = self._file(name)
        if source.is_symlink() or not source.exists() or source.read_bytes() != original:
            raise OSError("存档已被其他程序修改，停止迁移；请重新读取存档")
        directory = self.path / "backups"
        if directory.is_symlink():
            raise OSError("备份目录是符号链接，停止迁移")
        directory.mkdir(exist_ok=True)
        digest = hashlib.sha256(original).hexdigest()[:16]
        backup = directory / f"{name}-v{version}-{digest}.json"
        if backup.is_symlink():
            raise OSError("备份文件是符号链接，停止迁移")
        if backup.exists():
            if backup.read_bytes() != original:
                raise OSError("原存档备份冲突，停止迁移")
        else:
            with backup.open("xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
        return backup
