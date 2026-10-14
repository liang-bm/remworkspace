"""桌面伴侣设置的持久化、缓存统计与 VS Code 桥接。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import shutil
import subprocess
import stat

from .i18n import normalize_language
from .global_instruction import INSTRUCTION_FILE, read_instruction, save_instruction


PREFERENCES_FILE = Path("data") / "desktop-preferences.json"
MEMORY_EDITOR_FILE = Path("data") / "memory-editor.json"
PET_SCALES = (0.75, 1.0, 1.25, 1.5)
DEFAULT_BACKGROUND_FADE = 62
DEFAULT_CHAT_FONT_SIZE = 11
MIN_CHAT_FONT_SIZE = 9
MAX_CHAT_FONT_SIZE = 18


@dataclass(frozen=True)
class DesktopPreferences:
    """不适合写回训练配置 TOML 的轻量桌面偏好。"""

    pet_scale: float = 1.0
    background_fade: int = DEFAULT_BACKGROUND_FADE
    chat_font_size: int = DEFAULT_CHAT_FONT_SIZE
    overlay_visible: bool = True
    overlay_collapsed: bool = False
    theme_mode: str = "light"
    ui_language: str = "zh-Hans"
    task_rings_visible: bool = True
    hidden_task_ring_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class CacheStats:
    files: int
    bytes: int


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def load_desktop_preferences(root: Path) -> DesktopPreferences:
    path = root / PREFERENCES_FILE
    if not path.is_file():
        return DesktopPreferences()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return DesktopPreferences()
        scale = float(raw.get("pet_scale", 1.0))
        background_fade = int(
            raw.get("background_fade", DEFAULT_BACKGROUND_FADE)
        )
        chat_font_size = int(
            raw.get("chat_font_size", DEFAULT_CHAT_FONT_SIZE)
        )
        overlay_visible = raw.get("overlay_visible", True)
        overlay_collapsed = raw.get("overlay_collapsed", False)
        theme_mode = raw.get("theme_mode", "light")
        ui_language = normalize_language(raw.get("ui_language"))
        task_rings_visible = raw.get("task_rings_visible", True)
        hidden_groups = raw.get("hidden_task_ring_groups", [])
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DesktopPreferences()
    # 只接受界面能明确展示的档位，避免手改 JSON 后创建超大透明窗口。
    if scale not in PET_SCALES:
        scale = 1.0
    background_fade = min(100, max(0, background_fade))
    chat_font_size = min(
        MAX_CHAT_FONT_SIZE,
        max(MIN_CHAT_FONT_SIZE, chat_font_size),
    )
    return DesktopPreferences(
        pet_scale=scale,
        background_fade=background_fade,
        chat_font_size=chat_font_size,
        overlay_visible=overlay_visible if isinstance(overlay_visible, bool) else True,
        overlay_collapsed=overlay_collapsed if isinstance(overlay_collapsed, bool) else False,
        theme_mode=theme_mode if isinstance(theme_mode, str) and theme_mode in {"light", "dark"} else "light",
        ui_language=ui_language,
        task_rings_visible=task_rings_visible if isinstance(task_rings_visible, bool) else True,
        hidden_task_ring_groups=tuple(dict.fromkeys(
            value for value in hidden_groups
            if isinstance(value, str) and 0 < len(value) <= 512
        )) if isinstance(hidden_groups, list) else (),
    )


def save_desktop_preferences(
    root: Path,
    preferences: DesktopPreferences,
    *,
    changed_fields: tuple[str, ...] | None = None,
) -> None:
    # Preserve unrelated/future preferences. An unreadable existing file must
    # never be replaced with defaults merely because one display option changed.
    path = root / PREFERENCES_FILE
    existing = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("桌面偏好文件格式无效，未覆盖原文件")
    updates = asdict(preferences)
    if changed_fields is not None:
        updates = {key: updates[key] for key in changed_fields}
    existing.update(updates)
    _atomic_write_text(
        path,
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
    )


def load_global_instruction(root: Path, fallback: str) -> str:
    """读取用户可编辑的雷姆人格指令，首次使用时落盘默认值。"""

    try:
        snapshot = read_instruction(root)
        if snapshot.revision is None:
            save_instruction(root, fallback, snapshot)
            return fallback.strip()
        return snapshot.content or fallback.strip()
    except (OSError, ValueError, UnicodeError):
        # Do not replace unreadable or invalid user files with the fallback.
        return fallback.strip()


def save_global_instruction(root: Path, content: str) -> Path:
    save_instruction(root, content, read_instruction(root))
    return root / INSTRUCTION_FILE


def safe_cache_directories(root: Path) -> tuple[Path, ...]:
    """只列出可重建缓存；模型、训练数据、记忆和试听文件不在其中。"""

    return (
        root / "cache" / "pip",
        root / "cache" / "uv",
        root / "cache" / "pyinstaller",
        root / "tmp" / "dialogue-audio",
        root / "data" / "audio-cache" / "dialogue",
    )


def _is_linked_path(path: Path) -> bool:
    """Windows junction 不算 symlink；所有 reparse point 都保守跳过。"""
    try:
        info = path.lstat()
        return path.is_symlink() or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except OSError:
        return True


def _safe_cache_tree(root: Path, directory: Path):
    """先剪枝再遍历，只返回精确缓存根里的普通文件/目录。"""
    if not directory.is_dir() or _is_linked_path(directory):
        return
    base = directory.resolve()
    if not base.is_relative_to(root.resolve()):
        raise RuntimeError(f"拒绝访问项目目录外缓存: {base}")
    for current, child_dirs, child_files in os.walk(directory, topdown=True, followlinks=False):
        current_path = Path(current)
        if _is_linked_path(current_path) or not current_path.resolve().is_relative_to(base):
            child_dirs[:] = []
            continue
        child_dirs[:] = [name for name in child_dirs
                         if not _is_linked_path(current_path / name)
                         and (current_path / name).resolve().is_relative_to(base)]
        files = tuple(current_path / name for name in child_files
                      if not _is_linked_path(current_path / name)
                      and (current_path / name).resolve().is_relative_to(base))
        yield current_path, files


def scan_safe_cache(root: Path) -> CacheStats:
    files = 0
    total_bytes = 0
    for directory in safe_cache_directories(root):
        for _current, paths in _safe_cache_tree(root, directory):
            for path in paths:
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue
                files += 1
    return CacheStats(files=files, bytes=total_bytes)


def clear_safe_cache(root: Path) -> CacheStats:
    """清空明确可重建的缓存目录，并返回实际删除量。"""

    removed_files = 0
    removed_bytes = 0
    for directory in safe_cache_directories(root):
        visited = []
        base = directory.resolve()
        for current_path, paths in _safe_cache_tree(root, directory):
            visited.append(current_path)
            for path in paths:
                try:
                    if _is_linked_path(path) or not path.resolve().is_relative_to(base):
                        continue
                    size = path.stat().st_size
                    path.unlink()
                except OSError:
                    continue
                removed_files += 1
                removed_bytes += size
        # 仅删除刚才安全遍历到的空子目录；缓存根和所有链接都保留。
        for path in reversed(visited):
            if path == directory or _is_linked_path(path):
                continue
            try:
                if path.resolve().is_relative_to(base):
                    path.rmdir()
            except OSError:
                pass
    return CacheStats(files=removed_files, bytes=removed_bytes)


def _vscode_executable() -> Path | None:
    from .platforms import native_module
    return native_module("shell").vscode_executable()


def open_in_vscode(*paths: Path) -> None:
    executable = _vscode_executable()
    if executable is None:
        raise FileNotFoundError("未找到 Visual Studio Code")
    command = [str(executable), "--reuse-window", *(str(path) for path in paths)]
    from .platforms import native_module
    options = native_module("processes").hidden_process_options()
    subprocess.Popen(  # noqa: S603 - 路径均由本地固定目录解析
        command,
        close_fds=True,
        **options,
    )
