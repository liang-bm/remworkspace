"""动作库与用户播放偏好，独立于 Tk 和宠物的状态机。

内置素材只读；自定义动作导入到 data/pet-actions。导入先完整验证，
成功后才替换旧动作；旧版本移动到 data/pet-action-backups 可恢复。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any


REQUIRED_ACTIONS = frozenset({"idle", "review", "waiting", "running"})
ACTION_LABELS = {
    "idle": "静待", "review": "思考", "waiting": "输出", "running": "工作中",
    "running-left": "向左移动", "running-right": "向右移动", "waving": "挥手",
    "jumping": "跳跃", "failed": "难过", "happy": "开心", "little-angry": "假装生气",
    "dozing": "打盹", "waking": "唤醒",
    "strolling-left": "慢慢向左走", "strolling-right": "慢慢向右走",
}
ACTION_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
WINDOWS_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])\Z", re.IGNORECASE)
CANVAS_SIZE = (192, 208)
MAX_FRAMES = 240


@dataclass(frozen=True)
class PetActionEntry:
    action: str
    label: str
    frames: tuple[Path, ...]
    durations_ms: tuple[int, ...] = ()
    source: str = "bundled"
    enabled: bool = True
    speed: float = 1.0
    locked: bool = False

    @property
    def frame_count(self) -> int:
        return len(self.frames)


class PetActionLibrary:
    """统一枚举动作、取得逐帧时长、保存偏好和导入已生成的帧包。"""

    def __init__(self, root_path: Path | str, bundled_root: Path | str) -> None:
        self.root = Path(root_path)
        self.bundled_root = Path(bundled_root)
        if self.bundled_root.name == "actions":
            self.bundled_root = self.bundled_root.parent
        self.custom_root = self.root / "data" / "pet-actions"
        self.settings_path = self.root / "data" / "pet-action-settings.json"
        self.backup_root = self.root / "data" / "pet-action-backups"
        self.errors: list[str] = []
        self.last_backup: Path | None = None
        self._settings_error = ""
        self._settings = self._load_settings()

    @staticmethod
    def _validate_name(name: str) -> str:
        if not ACTION_PATTERN.fullmatch(name) or WINDOWS_RESERVED.fullmatch(name):
            raise ValueError("动作 ID 需以小写字母开头，仅包含小写字母、数字、下划线或连字符")
        return name

    def _load_settings(self) -> dict[str, dict[str, Any]]:
        self._settings_error = ""
        if not self.settings_path.exists():
            return {}
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
            actions = raw.get("actions", {})
            if not isinstance(actions, dict):
                raise ValueError("actions 应为对象")
            result = {}
            for action, setting in actions.items():
                self._validate_name(action)
                if not isinstance(setting, dict):
                    raise ValueError("每个动作偏好应为对象")
                speed = float(setting.get("speed", 1))
                if not math.isfinite(speed) or not .5 <= speed <= 2:
                    raise ValueError("播放速度需在 0.5–2 倍之间")
                result[action] = {"enabled": bool(setting.get("enabled", True)), "speed": speed}
            return result
        except (ValueError, TypeError, OSError, AttributeError) as exc:
            self._settings_error = f"动作设置无法读取，原文件已保留：{exc}"
            return {}

    def reload_settings(self) -> None:
        """Read an externally repaired file without silently replacing the last good state."""
        candidate = self._load_settings()
        if self._settings_error:
            raise ValueError(self._settings_error)
        self._settings = candidate

    @staticmethod
    def _ordered_frames(directory: Path, action: str) -> tuple[Path, ...]:
        """只读取编号帧；不把源合图或 contact-sheet 当作动画帧。"""
        pattern = re.compile(re.escape(action) + r"_(\d+)\.png\Z", re.IGNORECASE)
        numbered = []
        for path in directory.glob(f"{action}_*.png"):
            match = pattern.fullmatch(path.name)
            if match:
                numbered.append((int(match.group(1)), path))
        return tuple(path for _, path in sorted(numbered))

    def entries(self) -> tuple[PetActionEntry, ...]:
        self.errors = [self._settings_error] if self._settings_error else []
        found: dict[str, PetActionEntry] = {}
        idle = self._ordered_frames(self.bundled_root / "idle", "idle")
        if idle:
            found["idle"] = PetActionEntry("idle", ACTION_LABELS["idle"], idle, locked=True)
        action_root = self.bundled_root / "actions"
        if action_root.is_dir():
            for directory in sorted(action_root.iterdir()):
                if not directory.is_dir() or not ACTION_PATTERN.fullmatch(directory.name):
                    continue
                frames = self._ordered_frames(directory, directory.name)
                if frames:
                    name = directory.name
                    found[name] = PetActionEntry(name, ACTION_LABELS.get(name, name), frames,
                                                locked=name in REQUIRED_ACTIONS)
        if self.custom_root.is_dir():
            for directory in sorted(self.custom_root.iterdir()):
                if (
                    not directory.is_dir() or directory.is_symlink()
                    or not ACTION_PATTERN.fullmatch(directory.name)
                ):
                    continue
                try:
                    entry = self._validate_package(directory)
                    if entry.action != directory.name:
                        raise ValueError("动作目录名与 action.json 的 name 不一致")
                    found[entry.action] = entry
                except (ValueError, OSError) as exc:
                    self.errors.append(f"{directory.name}：{exc}")
        result = []
        for entry in found.values():
            preference = self._settings.get(entry.action, {})
            result.append(replace(entry, enabled=entry.locked or preference.get("enabled", True),
                                  speed=float(preference.get("speed", 1))))
        order = {name: index for index, name in enumerate(ACTION_LABELS)}
        return tuple(sorted(result, key=lambda item: (order.get(item.action, len(order)), item.action)))

    def bundled_entry(self, action: str) -> PetActionEntry | None:
        """Find an original frame set without inspecting custom overrides."""
        self._validate_name(action)
        directory = self.bundled_root / "idle" if action == "idle" else self.bundled_root / "actions" / action
        frames = self._ordered_frames(directory, action)
        if not frames:
            return None
        return PetActionEntry(action, ACTION_LABELS.get(action, action), frames,
                              locked=action in REQUIRED_ACTIONS)

    def restore_bundled(self, action: str) -> Path:
        """Archive only a custom override; keep original assets and preferences.

        Renaming is recoverable and does not execute or selectively delete any
        package content. No fallback means no restore, including required states.
        """
        self._validate_name(action)
        if self.bundled_entry(action) is None:
            raise ValueError("这个动作没有可恢复的内置帧。")
        project = self.root.resolve(strict=True)
        destination = self.custom_root / action
        # Reject linked parents as well as linked action directories. A junction
        # within the project is not permission to move its unrelated target.
        for path, expected in ((self.custom_root, project / "data" / "pet-actions"),
                               (destination, project / "data" / "pet-actions" / action),
                               (self.backup_root, project / "data" / "pet-action-backups")):
            if path.is_symlink() or path.resolve() != expected:
                raise ValueError("动作或备份目录存在链接或越界，未移动文件。")
        if not destination.is_dir():
            raise ValueError("自定义覆盖已不存在，请刷新动作库。")
        entry = self._validate_package(destination)
        if entry.action != action:
            raise ValueError("动作目录名与 action.json 的 name 不一致")
        self.backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = self.backup_root / f"{action}-{stamp}"
        if backup.exists():
            raise ValueError("备份名称已存在，未移动文件；请重试。")
        destination.replace(backup)
        self.last_backup = backup
        return backup

    def is_enabled(self, action: str) -> bool:
        return action in REQUIRED_ACTIONS or bool(self._settings.get(action, {}).get("enabled", True))

    def durations(self, action: str, defaults: tuple[int, ...] | list[int]) -> tuple[int, ...]:
        """速度 2 倍即每帧时长减半，延长只改变节奏，不伪造补帧。"""
        timings = tuple(defaults)
        if not ACTION_PATTERN.fullmatch(action):
            return timings
        custom_manifest = self.custom_root / action / "action.json"
        if custom_manifest.is_file():
            try:
                raw = json.loads(custom_manifest.read_text(encoding="utf-8-sig"))
                count = int(raw.get("frame_count", 0))
                custom = self._manifest_durations(raw, count)
                if custom:
                    timings = custom
            except (OSError, ValueError, TypeError):
                pass
        speed = float(self._settings.get(action, {}).get("speed", 1))
        return tuple(max(10, round(duration / speed)) for duration in timings)

    def set_options(self, action: str, *, enabled: bool, speed: float) -> None:
        self._validate_name(action)
        speed = float(speed)
        if not math.isfinite(speed) or not .5 <= speed <= 2:
            raise ValueError("播放速度需在 0.5–2 倍之间")
        if action in REQUIRED_ACTIONS and not enabled:
            raise ValueError("静待、思考、输出、工作中是自动状态，必须保持启用")
        if self._settings_error:
            raise ValueError(self._settings_error)
        updated = {**self._settings, action: {"enabled": bool(enabled), "speed": speed}}
        self._atomic_json(self.settings_path, {"version": 1, "actions": updated})
        self._settings = updated

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.stem}-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _manifest_durations(raw: dict[str, Any], count: int) -> tuple[int, ...]:
        values = raw.get("durations_ms")
        if values is None:
            values = [raw.get("frame_duration_ms", 140)] * count
        if not isinstance(values, list) or len(values) != count:
            raise ValueError("逐帧 timing 数量必须与 frame_count 一致")
        if any(isinstance(value, bool) or not isinstance(value, int) or not 20 <= value <= 10000 for value in values):
            raise ValueError("每帧时长需为 20–10000 毫秒的整数")
        return tuple(values)

    def _validate_package(self, directory: Path) -> PetActionEntry:
        manifest = directory / "action.json"
        if manifest.is_symlink() or not manifest.is_file():
            raise ValueError("请选择包含 action.json 的动作目录")
        if manifest.stat().st_size > 1024 * 1024:
            raise ValueError("action.json 超过 1 MB")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("action.json 不是有效 JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("action.json 应为对象")
        name = self._validate_name(str(raw.get("name", "")))
        count = raw.get("frame_count")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_FRAMES:
            raise ValueError(f"frame_count 需为 1–{MAX_FRAMES} 的整数")
        if raw.get("canvas") != list(CANVAS_SIZE):
            raise ValueError("RemChat 动作必须使用 192×208 的稳定透明画布")
        if raw.get("sequence_mode", "once") not in ("once", "loop", "pingpong", "mirror_second_pass"):
            raise ValueError("未知的 sequence_mode")
        frames = self._ordered_frames(directory, name)
        if len(frames) != count:
            raise ValueError("编号 PNG 数量与 frame_count 不一致")
        numbers = [int(path.stem.rsplit("_", 1)[1]) for path in frames]
        if numbers != list(range(numbers[0], numbers[0] + count)) or numbers[0] not in (0, 1):
            raise ValueError("帧编号需从 0 或 1 开始，连续且无重复")
        timings = self._manifest_durations(raw, count)
        try:
            from PIL import Image
        except ImportError as exc:
            raise ValueError("读取自定义动作需要 Pillow，请使用完整桌面安装") from exc
        for path in frames:
            if path.is_symlink() or path.resolve().parent != directory.resolve():
                raise ValueError("动作帧必须是目录内的真实 PNG 文件")
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError(f"{path.name} 超过 4 MB")
            try:
                # 先检查 PNG IHDR，避免超大压缩图片进入 Pillow 解码。
                with path.open("rb") as stream:
                    header = stream.read(24)
                if (
                    len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n"
                    or header[12:16] != b"IHDR"
                    or (int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")) != CANVAS_SIZE
                ):
                    raise ValueError(f"{path.name} 需为 192×208 PNG")
                with Image.open(path) as image:
                    if image.format != "PNG" or image.size != CANVAS_SIZE or image.mode != "RGBA":
                        raise ValueError(f"{path.name} 需为 192×208 RGBA PNG")
                    alpha = image.getchannel("A")
                    minimum, maximum = alpha.getextrema()
                    if minimum != 0 or maximum == 0:
                        raise ValueError(f"{path.name} 需要透明背景，且不能是全透明空帧")
                    width, height = CANVAS_SIZE
                    borders = ((0, 0, width, 1), (0, height - 1, width, height),
                               (0, 0, 1, height), (width - 1, 0, width, height))
                    if any(alpha.crop(border).getextrema()[1] > 0 for border in borders):
                        raise ValueError(f"{path.name} 前景触及画布边缘，请保留透明留白")
            except OSError as exc:
                raise ValueError(f"{path.name} 不是有效 PNG") from exc
        return PetActionEntry(name, str(raw.get("label", ACTION_LABELS.get(name, name))), frames,
                              timings, "custom", locked=name in REQUIRED_ACTIONS)

    def import_action(self, source: Path | str) -> PetActionEntry:
        """验证→复制到临时目录→备份旧目录→原子改名，不执行任何包内脚本。"""
        source = Path(source).resolve(strict=True)
        if source.name == "action.json":
            source = source.parent
        if not source.is_dir():
            raise ValueError("请选择动作目录或 action.json")
        entry = self._validate_package(source)
        self.custom_root.mkdir(parents=True, exist_ok=True)
        destination = self.custom_root / entry.action
        # 拒绝数据目录被手动替换为指向项目外部的符号链接。
        project = self.root.resolve()
        if not destination.resolve().is_relative_to(project):
            raise ValueError("动作安装路径不在当前项目中")
        if not self.backup_root.resolve().is_relative_to(project):
            raise ValueError("备份路径不在当前项目中")
        staging = Path(tempfile.mkdtemp(prefix=".action-import-", dir=self.custom_root))
        backup: Path | None = None
        try:
            shutil.copy2(source / "action.json", staging / "action.json")
            for frame in entry.frames:
                shutil.copy2(frame, staging / frame.name)
            self._validate_package(staging)
            if destination.exists():
                self.backup_root.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup = self.backup_root / f"{entry.action}-{stamp}"
                destination.replace(backup)
            staging.replace(destination)
            self.last_backup = backup
        except Exception:
            if backup is not None and backup.exists() and not destination.exists():
                backup.replace(destination)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return replace(entry, frames=self._ordered_frames(destination, entry.action))
