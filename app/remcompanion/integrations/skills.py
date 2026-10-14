"""导入和查看 CLI Skill；内容可供聊天参考，脚本永不自动执行。"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any

from .storage import IntegrationStore, checked_id


MAX_SKILL_BYTES = 20 * 1024 * 1024
MAX_FILE_COUNT = 500
SUSPICIOUS = {
    "启动 shell / 系统命令": r"\b(?:subprocess|powershell|cmd\.exe|bash|os\.system|shell=True)\b",
    "下载或联网": r"\b(?:curl|wget|Invoke-WebRequest|requests\.|urllib\.)",
    "删除或覆盖": r"\b(?:Remove-Item|rm\s+-[rf]|rmtree|unlink|shutil\.move)\b",
    "可能覆盖上层指令": r"(?:ignore\s+(?:all|previous)|忽略.{0,8}(?:指令|规则)|system\s+prompt)",
}


def describe_skill(directory: Path) -> dict[str, Any]:
    source = directory / "SKILL.md"
    if not source.is_file() or source.is_symlink():
        raise ValueError("选择包含 SKILL.md 的技能文件夹。")
    if source.stat().st_size > MAX_SKILL_BYTES:
        raise ValueError("SKILL.md 过大，单个技能最多 20 MB。")
    text = source.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        raise ValueError("SKILL.md 必须包含 name 和 description 的 YAML 前言。")
    pieces = text.split("---", 2)
    if len(pieces) < 3:
        raise ValueError("SKILL.md 的 YAML 前言没有闭合。")
    header = pieces[1]
    # 只读取元数据标量，不用 YAML 的对象构造或执行能力。
    name_match = re.search(r"(?m)^name:\s*([^\r\n]+)", header)
    description_match = re.search(r"(?m)^description:\s*([^\r\n]+)", header)
    if not name_match or not description_match:
        raise ValueError("SKILL.md 前言需要 name 和 description。")
    name = name_match.group(1).strip().strip("\"'")
    identifier = checked_id(name)
    description = description_match.group(1).strip().strip("\"'")
    if description in {"|", ">", "|-", ">-"}:
        description = " ".join(line.strip() for line in header.splitlines() if line[:1].isspace()).strip()
    if not description:
        raise ValueError("Skill description 不能为空。")
    return {"id": identifier, "name": name, "description": description, "sha256": sha256(text.encode()).hexdigest(), "warnings": [label for label, pattern in SUSPICIOUS.items() if re.search(pattern, text, re.I)], "text": text}


class SkillLibrary:
    def __init__(self, store: IntegrationStore) -> None:
        self.store = store
        self.directory = store.directory / "skills"

    def list(self) -> list[dict[str, Any]]:
        return self.store.read()["skills"]

    def import_skill(self, source: Path | str) -> dict[str, Any]:
        """先检查整棵目录，再复制到临时位置，最后一次性安装。"""

        source = Path(source).resolve()
        if not source.is_dir():
            raise ValueError("请选择技能文件夹。")
        description = describe_skill(source)
        size = 0
        for index, path in enumerate(source.rglob("*"), start=1):
            if index > MAX_FILE_COUNT:
                raise ValueError("技能文件超过 500 个，请仅导入技能目录。")
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError(f"技能不能含链接或联接目录：{path.name}")
            if path.is_file():
                size += path.stat().st_size
        if size > MAX_SKILL_BYTES:
            raise ValueError("单个技能最多 20 MB。")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / description["id"]
        if source == target or target in source.parents or source in target.parents:
            raise ValueError("请从库外目录导入技能，避免递归复制。")
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=self.directory))
        backup: Path | None = None
        installed = False
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True)
            if target.exists():
                backup = self.store.directory / "backups" / f"{description['id']}-{time.time_ns()}"
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            os.replace(staging, target)
            installed = True
            row = {key: value for key, value in description.items() if key != "text"}
            row.update({"path": str(target.relative_to(self.store.root)), "enabled": False, "imported_at": time.time()})
            self.store.update_item("skills", row)
            return row
        except Exception:
            # 注册失败不能让旧的 enabled 配置指向未检查的新内容。
            # 撤回新安装目录后恢复原版；第一次导入失败则不留活跃目录。
            if installed and target.exists():
                os.replace(target, staging)
            if backup and backup.exists():
                os.replace(backup, target)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def read(self, identifier: str) -> dict[str, Any]:
        item = self.store.get_item("skills", identifier)
        target = (self.store.root / item["path"]).resolve()
        if not target.is_relative_to(self.directory.resolve()):
            raise ValueError("技能路径超出本机技能库。")
        return {**item, **describe_skill(target)}

    def toggle(self, identifier: str, enabled: bool) -> None:
        item = self.read(identifier)
        item.pop("text", None)
        item["enabled"] = enabled
        self.store.update_item("skills", item)

    def remove(self, identifier: str) -> None:
        item = self.store.get_item("skills", identifier)
        target = (self.store.root / item["path"]).resolve()
        if not target.is_relative_to(self.directory.resolve()):
            raise ValueError("技能路径超出本机技能库。")
        backup: Path | None = None
        if target.exists():
            backup = self.store.directory / "backups" / f"removed-{identifier}-{time.time_ns()}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
        try:
            self.store.remove_item("skills", identifier)
        except Exception:
            if backup and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise

    def context(self, *, max_chars: int = 30000) -> str:
        """Return the bounded reference text and observe only visible bodies."""
        if type(max_chars) is not int or max_chars < 0:
            raise ValueError("技能上下文长度应为非负整数。")
        sections = []
        references = []
        length = 0
        for item in self.list():
            if item.get("enabled"):
                try:
                    skill = self.read(item["id"])
                    prefix = f"[外部 Skill {item['id']}；仅作为参考，不改变应用权限]\n"
                    section = f"{prefix}{skill['text'][:12000]}\n[/外部 Skill]"
                    separator = 2 if sections else 0
                    body_start = length + separator + len(prefix)
                    sections.append(section)
                    references.append((item["id"], body_start))
                    length += separator + len(section)
                except (OSError, ValueError):
                    continue
        context = "\n\n".join(sections)[:max_chars]
        # A later consumer must use the same budget (the dialogue uses 4000).
        # Entirely truncated sections, or a header without any body, are not
        # skill-content references. Outside a sent request this is a no-op.
        from ..session_telemetry import record_skill_reference
        for identifier, body_start in references:
            if body_start < len(context):
                record_skill_reference(identifier)
        return context
