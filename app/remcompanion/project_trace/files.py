"""Bounded read-only observations and explicitly labelled static references."""

from __future__ import annotations

import ast
import difflib
import os
from pathlib import Path
import re
import time

from ..workspace_context import SECRET_FILENAMES, TEXT_EXTENSIONS

MAX_ENTRIES = 2000
MAX_FILE_BYTES = 64 * 1024
MAX_CACHE_BYTES = 2 * 1024 * 1024
IGNORED = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
           "runtime", "models", "engines", "pretrained_models", "cache", ".cache",
           "audio-cache", "backups", "tmp", "temp", "dist", "build", "project-trace",
           "release", "releases", "_internal", "site-packages", "env", "envs",
           ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
           ".hypothesis", ".coverage", "htmlcov", "logs", "log", "generated",
           ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".turbo", "target"}
GENERATED_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".dll", ".exe", ".so", ".dylib",
                        ".whl", ".egg", ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz",
                        ".msi", ".msix", ".dmg", ".pkg", ".log", ".tmp", ".bak", ".swp",
                        ".wav", ".mp3", ".flac", ".mp4", ".pt", ".pth", ".ckpt",
                        ".safetensors", ".gguf", ".onnx"}
OBSERVATION_POLICY = "默认跳过 releases、_internal、依赖/解释器、构建输出、缓存、日志及生成包；2000 项额度只统计可观察项。"


def is_generated_path(relative: str) -> bool:
    """Pure presentation/scanner policy; does not inspect or open files."""
    parts = [part.lower() for part in relative.replace("\\", "/").split("/") if part]
    return any(part in IGNORED or part.endswith((".dist-info", ".egg-info", ".app")) for part in parts) or (
        bool(parts) and (Path(parts[-1]).suffix in GENERATED_EXTENSIONS or parts[-1].endswith(".min.js")))


def allowed_path(root: Path, relative: str) -> Path:
    """Reject traversal and links; accept missing paths for deletion reports."""
    if not relative or "\\" in relative or Path(relative).is_absolute():
        raise ValueError("文件路径必须是工作空间内的相对路径（使用 /）")
    parts = Path(relative).parts
    if not parts or any(part in {"..", "."} or ":" in part for part in parts):
        raise ValueError("文件路径不能越出工作空间")
    if is_generated_path(relative):
        raise ValueError("依赖、模型、打包输出、日志或缓存不参与观察")
    name = parts[-1].lower()
    if name == ".env" or name.startswith(".env.") or name in SECRET_FILENAMES or Path(name).suffix in {".pem", ".key"}:
        raise ValueError("凭据文件不参与项目过程记录")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("链接目录或文件不参与观察")
    path.resolve().relative_to(root.resolve())
    return path


def scan(root: Path, stop=None) -> tuple[dict[str, tuple[int, int]], bool]:
    """0.5-second / 2000 observable-entry budget; partial scans prove no deletion."""
    result = {}
    stack = [root]
    count = 0
    deadline = time.monotonic() + 0.5
    complete = True
    while stack:
        if (stop is not None and stop.is_set()) or time.monotonic() > deadline:
            return result, False
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if time.monotonic() > deadline or (stop is not None and stop.is_set()):
                        return result, False
                    try:
                        relative = Path(entry.path).relative_to(root).as_posix()
                        # Prune before budgeting or descending. A release tree
                        # containing thousands of entries consumes no useful slot.
                        if is_generated_path(relative):
                            continue
                        path = allowed_path(root, relative)
                        count += 1
                        if count > MAX_ENTRIES:
                            return result, False
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            result[relative] = (info.st_mtime_ns, info.st_size)
                    except ValueError:
                        continue
                    except OSError:
                        complete = False
        except OSError:
            complete = False
    return result, complete


def read_text(root: Path, relative: str) -> str | None:
    path = allowed_path(root, relative)
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return None
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES or b"\0" in data:
            return None
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError):
        return None


def difference(before: str | None, after: str | None) -> str:
    if before is None or after is None:
        return ""
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="before", tofile="after", lineterm="")
    output = []
    size = 0
    for line in lines:
        size += len(line)
        if len(output) >= 300 or size > 16000:
            output.append("… [差异预览已达上限]")
            break
        output.append(line)
    return "\n".join(output)


def references(root: Path, relative: str, text: str | None) -> list[str]:
    """Resolve only local imports. No project execution or semantic causality."""
    if text is None:
        return []
    paths = set()
    source = Path(relative)
    candidates = []
    if source.suffix == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, RecursionError):
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates.extend(root / alias.name.replace(".", "/") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = root / source.parent
                if node.level:
                    for _ in range(node.level - 1):
                        base = base.parent
                else:
                    base = root
                module = base / (node.module or "").replace(".", "/")
                candidates.append(module)
                candidates.extend(module / alias.name for alias in node.names if alias.name != "*")
    elif source.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        # A literal relative specifier is evidence, not a full JS parser.
        values = re.findall(r'(?:from\s*|import\s*|require\(\s*)[\'\"](\.[^\'\"\n]+)[\'\"]', text)
        candidates.extend(root / source.parent / value for value in values[:100])
    for candidate in candidates[:100]:
        for suffix in ("", ".py", "/__init__.py", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
            target = Path(str(candidate) + suffix)
            try:
                name = target.resolve().relative_to(root.resolve()).as_posix()
                safe = allowed_path(root, name)
                if safe.is_file() and name != relative:
                    paths.add(name)
                    break
            except (OSError, ValueError):
                continue
    return sorted(paths)[:30]
