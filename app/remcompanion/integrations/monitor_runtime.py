"""Export a small, self-contained monitor runtime from reviewed application code.

The desktop bundle does not install an app/ source tree. Build-time archives
carry only standard-library helpers and the selected OS, never another OS's
native code. Exported hooks then work without importing the desktop application.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile


PACKAGE_INITIALIZER = b'"""RemChat exported monitor helpers; no desktop initialization."""\n'
COMMON_FILES = (
    "remcompanion/platforms/__init__.py",
    "remcompanion/integrations/notify.py",
    "remcompanion/integrations/processes.py",
    "remcompanion/integrations/adapters/runner.py",
    "remcompanion/integrations/adapters/codex/runner.py",
    "remcompanion/project_trace/send.py",
)
EMPTY_PACKAGES = (
    "remcompanion/__init__.py",
    "remcompanion/integrations/__init__.py",
    "remcompanion/integrations/adapters/__init__.py",
    "remcompanion/integrations/adapters/codex/__init__.py",
    "remcompanion/project_trace/__init__.py",
)
MAX_FILE_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024


def _platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise OSError("Monitor exports currently support Windows and Linux only")


def _file_names(platform: str) -> set[str]:
    if platform not in {"windows", "linux"}:
        raise ValueError("Unknown monitor runtime platform")
    native = f"remcompanion/platforms/{platform}"
    return set(COMMON_FILES) | set(EMPTY_PACKAGES) | {
        f"{native}/__init__.py", f"{native}/shell.py", f"{native}/processes.py",
    }


def _source_files(platform: str) -> dict[str, bytes]:
    source = Path(__file__).resolve().parents[2]
    return {name: PACKAGE_INITIALIZER if name in EMPTY_PACKAGES
            else (source / name).read_bytes()
            for name in sorted(_file_names(platform))}


def _manifest(platform: str, files: dict[str, bytes]) -> dict:
    return {"schema": 1, "platform": platform, "minimum_python": "3.8",
            "sha256": {name: hashlib.sha256(content).hexdigest()
                       for name, content in sorted(files.items())}}


def build_runtime_archive(platform: str, destination: Path) -> Path:
    """Called by the OS-specific PyInstaller specification, not by the UI."""
    files = _source_files(platform)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("runtime.json", json.dumps(_manifest(platform, files),
                                                  ensure_ascii=False, indent=2))
    return destination


def _bundled_files(platform: str) -> dict[str, bytes]:
    path = Path(__file__).resolve().parent / "resources/monitor-runtime.zip"
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        expected = _file_names(platform) | {"runtime.json"}
        if (len(entries) != len(expected) or {row.filename for row in entries} != expected
                or any(row.file_size > MAX_FILE_BYTES for row in entries)
                or sum(row.file_size for row in entries) > MAX_ARCHIVE_BYTES):
            raise ValueError("Invalid monitor runtime archive entries")
        manifest = json.loads(archive.read("runtime.json"))
        files = {name: archive.read(name) for name in sorted(expected - {"runtime.json"})}
    if manifest != _manifest(platform, files):
        raise ValueError("Monitor runtime manifest/hash validation failed")
    return files


def export_runtime(destination: Path) -> dict:
    """Write only into a new directory; no arbitrary zip extraction or imports."""
    platform = _platform()
    files = _bundled_files(platform) if getattr(sys, "frozen", False) else _source_files(platform)
    manifest = _manifest(platform, files)
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (destination / "runtime.json").write_text(json.dumps(manifest, ensure_ascii=False,
                                                        indent=2), encoding="utf-8")
    return manifest
