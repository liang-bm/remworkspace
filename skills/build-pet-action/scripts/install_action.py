"""Safely install validated action frames through a project hook."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import json
import re
import shutil
import uuid

from validate_action import validate_frames


ACTION_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _resolve_inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"hook 路径越过项目目录：{relative}")
    return target


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def install_action(
    *,
    project_root: Path,
    action_name: str,
    frames_dir: Path,
    hook_path: Path,
) -> Path:
    if not ACTION_NAME.fullmatch(action_name):
        raise ValueError("action name must be lowercase kebab/snake case")
    root = project_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    hook = json.loads(hook_path.read_text(encoding="utf-8"))
    if not isinstance(hook, dict) or not isinstance(hook.get("asset_root"), str):
        raise ValueError("hook 必须包含字符串 asset_root")
    asset_root = _resolve_inside(root, hook["asset_root"])
    registry = (
        _resolve_inside(root, hook["registry_json"])
        if isinstance(hook.get("registry_json"), str)
        else None
    )
    report = validate_frames(frames_dir)
    if not report.ok:
        messages = "; ".join(
            issue.message for issue in report.issues if issue.severity == "error"
        )
        raise ValueError(f"动作帧校验失败：{messages}")

    asset_root.mkdir(parents=True, exist_ok=True)
    target = asset_root / action_name
    staging = asset_root / f".{action_name}-staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    backup: Path | None = None
    try:
        for path in sorted(frames_dir.glob("*.png")):
            shutil.copy2(path, staging / path.name)
        for optional in ("action.json", "contact-sheet.png"):
            source = frames_dir / optional
            if source.is_file():
                shutil.copy2(source, staging / optional)
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = asset_root / f"{action_name}.backup-{stamp}"
            target.replace(backup)
        staging.replace(target)
        if registry is not None:
            if registry.is_file():
                raw = json.loads(registry.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("registry_json 根节点必须是对象")
            else:
                raw = {}
            relative = target.relative_to(root).as_posix()
            raw[action_name] = {
                "path": relative,
                "frame_count": report.frame_count,
                "canvas": list(report.canvas) if report.canvas else None,
            }
            _atomic_json(registry, raw)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if target.exists() and backup is not None:
            shutil.rmtree(target)
            backup.replace(target)
        raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--hook", required=True, type=Path)
    args = parser.parse_args()
    installed = install_action(
        project_root=args.project_root,
        action_name=args.name,
        frames_dir=args.frames,
        hook_path=args.hook,
    )
    print(installed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
