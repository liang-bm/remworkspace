"""User-selected backgrounds, without an interrupting slideshow.

Only imported application copies can be removed. Legacy/preinstalled images
remain read-only, and settings are separate from desktop preferences.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import stat
from typing import Callable

from .chat_background import BACKGROUND_EXTENSIONS, pillow_available


MODES = {"startup": "每次启动更换", "daily": "每天更换", "fixed": "固定一张"}
ALBUM_FILE = Path("data/background-album.json")
IMPORT_DIR = Path("resources/backgrounds/imported")


def prepare_import(source: Path) -> tuple[str, bytes]:
    """Heavy decode can run off the Tk thread; manifest changes stay on it."""
    if not pillow_available():
        raise RuntimeError("当前运行时没有安装 Pillow，无法导入背景")
    from PIL import Image, ImageOps
    source = Path(source)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    return source.stem, buffer.getvalue()


@dataclass(frozen=True)
class BackgroundEntry:
    key: str
    path: Path
    name: str
    imported: bool
    enabled: bool


def _ordinary(path: Path) -> bool:
    """Reject junctions/symlinks as well as files beneath linked parents."""
    try:
        info = path.lstat()
        return not path.is_symlink() and not bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except OSError:
        return False


def _managed_path(root: Path, relative: Path) -> Path:
    root = root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("背景路径不在应用资源目录内")
    current = path
    while current != root:
        if current.exists() and not _ordinary(current):
            raise ValueError("背景资源目录不能是符号链接或目录联接")
        current = current.parent
    return path


class BackgroundAlbum:
    """A small, atomic JSON manifest around application-owned image copies."""

    def __init__(self, root: Path, *, today: Callable[[], date] = date.today):
        self.root = Path(root).resolve()
        self.today = today
        self.path = self.root / ALBUM_FILE
        self.state = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "mode": "startup", "current": "", "day": "",
                    "disabled": [], "removed": [], "names": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("背景相册配置无法读取；原配置未覆盖") from error
        if (not isinstance(raw, dict) or raw.get("version", 1) != 1
                or not isinstance(raw.get("names", {}), dict)
                or not all(isinstance(raw.get(key, []), list) for key in ("disabled", "removed"))):
            raise ValueError("背景相册配置格式无效；原配置未覆盖")
        raw.setdefault("disabled", [])
        raw.setdefault("removed", [])
        raw.setdefault("names", {})
        if raw.get("mode") not in MODES:
            raw["mode"] = "startup"
        return raw

    @property
    def mode(self) -> str:
        return self.state.get("mode", "startup")

    @property
    def current_key(self) -> str:
        return self.state.get("current", "")

    def _save(self, updated: dict) -> None:
        target = _managed_path(self.root, ALBUM_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = _managed_path(self.root, ALBUM_FILE.with_suffix(".json.tmp"))
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        self.state = updated

    def entries(self) -> tuple[BackgroundEntry, ...]:
        found: dict[str, tuple[Path, bool]] = {}
        # Resource root wins duplicate names during a recoverable migration.
        for relative, imported in ((IMPORT_DIR, True), (Path("resources/backgrounds"), False),
                                   (Path("background"), False)):
            directory = self.root / relative
            if not directory.is_dir() or not _ordinary(directory):
                continue
            if directory.resolve() != directory.absolute():
                continue
            for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
                if not path.is_file() or not _ordinary(path) or path.suffix.lower() not in BACKGROUND_EXTENSIONS:
                    continue
                key = ("imported:" if imported else "library:") + path.name.casefold()
                found.setdefault(key, (path, imported))
        names = self.state.get("names", {})
        disabled = set(str(key) for key in self.state["disabled"])
        removed = set(str(key) for key in self.state["removed"])
        return tuple(BackgroundEntry(key, path, str(names.get(key, path.stem)), imported, key not in disabled)
                     for key, (path, imported) in found.items() if key not in removed)

    def eligible(self) -> tuple[BackgroundEntry, ...]:
        return tuple(entry for entry in self.entries() if entry.enabled)

    def _select(self, updated: dict, *, advance: bool = False) -> Path | None:
        entries = self.eligible()
        if not entries:
            updated["current"] = ""
            return None
        keys = [entry.key for entry in entries]
        current = updated.get("current", "")
        index = keys.index(current) if current in keys else 0
        if advance and current in keys:
            index = (index + 1) % len(keys)
        updated["current"] = keys[index]
        return entries[index].path

    def activate(self, *, startup: bool = False) -> Path | None:
        updated = dict(self.state)
        day = self.today().isoformat()
        advance = ((startup and self.mode == "startup")
                   or (self.mode == "daily" and bool(updated.get("day")) and updated.get("day") != day))
        path = self._select(updated, advance=advance)
        updated["day"] = day
        if updated != self.state:
            self._save(updated)
        return path

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("请选择固定、每天或每次启动更换")
        updated = dict(self.state, mode=mode, day=self.today().isoformat())
        self._save(updated)

    def set_current(self, key: str) -> Path:
        entry = next((item for item in self.entries() if item.key == key), None)
        if entry is None:
            raise ValueError("这张背景已不存在，请刷新相册")
        updated = dict(self.state, current=key, day=self.today().isoformat())
        updated["disabled"] = [value for value in updated["disabled"] if value != key]
        self._save(updated)
        return entry.path

    def set_enabled(self, key: str, enabled: bool) -> Path | None:
        if not any(item.key == key for item in self.entries()):
            raise ValueError("这张背景已不存在，请刷新相册")
        disabled = [value for value in self.state["disabled"] if value != key]
        if not enabled:
            disabled.append(key)
        updated = dict(self.state, disabled=disabled)
        self._save(updated)
        return self.activate()

    def import_image(self, source: Path) -> BackgroundEntry:
        # Decode and re-encode only the first frame: no continuous animation,
        # no user EXIF metadata in the application copy.
        return self.import_prepared(*prepare_import(source))

    def import_prepared(self, name: str, payload: bytes) -> BackgroundEntry:
        filename = sha256(payload).hexdigest()[:32] + ".png"
        target = _managed_path(self.root, IMPORT_DIR / filename)
        created = not target.exists()
        if created:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = _managed_path(self.root, IMPORT_DIR / (filename + ".tmp"))
            temporary.write_bytes(payload)
            temporary.replace(target)
        key = "imported:" + filename
        names = dict(self.state["names"])
        names[key] = name
        updated = dict(self.state, names=names)
        updated["removed"] = [value for value in updated["removed"] if value != key]
        updated["disabled"] = [value for value in updated["disabled"] if value != key]
        try:
            self._save(updated)
        except (OSError, ValueError):
            if created:
                target.unlink(missing_ok=True)
            raise
        return next(item for item in self.entries() if item.key == key)

    def remove(self, key: str) -> Path | None:
        entry = next((item for item in self.entries() if item.key == key), None)
        if entry is None:
            raise ValueError("这张背景已不存在，请刷新相册")
        old_state = self.state
        updated = dict(old_state)
        updated["removed"] = list(dict.fromkeys([*old_state["removed"], key]))
        self._save(updated)
        if entry.imported:
            try:
                original = _managed_path(self.root, IMPORT_DIR / entry.path.name)
                suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                trash = _managed_path(self.root, Path("data/background-trash") / f"{suffix}-{entry.path.name}")
                trash.parent.mkdir(parents=True, exist_ok=True)
                original.replace(trash)
            except (OSError, ValueError):
                self._save(old_state)
                raise
        return self.activate()
