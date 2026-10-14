"""Local service startup is a user choice, separate from desktop appearance.

Without an override, legacy TOML behavior is inherited. Saving this policy does
not start or stop any backend; callers publish the value only after persistence.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import os
import tempfile

from .config import AppConfig


POLICY_FILE = Path("data/local-model-settings.json")


def _policy_path(root: Path) -> Path:
    root = root.absolute()
    path = root / POLICY_FILE
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise OSError(f"本地模型设置路径不能是链接：{candidate}")
        if candidate == root:
            break
    return path


def apply_local_model_policy(config: AppConfig, *, log=print) -> AppConfig:
    """Unreadable overrides are kept for recovery; never rewrite them on load."""
    try:
        path = _policy_path(config.root)
        if not path.exists():
            return config
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(raw, dict) or type(raw.get("schema")) is not int or raw.get("schema") != 1
                or type(raw.get("auto_start")) is not bool):
            raise ValueError("本地模型设置格式无效")
    except (OSError, ValueError) as error:
        log(f"[本地模型] 设置未加载，沿用 TOML，原文件保留：{error}")
        return config
    return replace(config, llm=replace(config.llm, auto_start=raw["auto_start"]))


def save_local_model_policy(root: Path, *, auto_start: bool) -> Path:
    if type(auto_start) is not bool:
        raise ValueError("本地模型启动设置必须是布尔值")
    path = _policy_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"schema": 1, "auto_start": auto_start}, indent=2) + "\n"
    temporary = None
    try:
        # A unique file prevents two application instances sharing a .tmp path.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".local-model-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _policy_path(root)  # Check managed ancestry again before promotion.
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
