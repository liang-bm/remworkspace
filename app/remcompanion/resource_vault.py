"""Account-bound storage for private source assets, not DRM or portable keys.

Public display sprites remain packaged assets. Private training sources are
encrypted at rest; a training operation decrypts only into a temporary directory
and removes that directory on completion. A process running as the same Windows
user can decrypt them. Preserve the Windows account / recovery backup.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import tempfile

MANIFEST = "asset-manifest.json"


def _linked(path: Path) -> bool:
    """Reject all Windows reparse points, including directory junctions."""
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _protector():
    from .platforms import native_module
    return native_module("secrets", "windows").WindowsDpapiProtector()


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or not isinstance(root, Path):
        raise ValueError("资源清单路径无效")
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {".", ".."} or ":" in part or "\\" in part for part in parts):
        raise ValueError("资源清单路径无效")
    if PurePosixPath(relative).is_absolute():
        raise ValueError("资源清单不能包含绝对路径")
    target = root.joinpath(*parts)
    if root.resolve() not in target.resolve().parents or any(_linked(p) for p in (target, *target.parents) if p != root.parent):
        raise ValueError("资源目录不能包含链接或外部路径")
    return target


def inventory(directory: Path) -> list[dict]:
    path = Path(directory) / MANIFEST
    if not path.is_file():
        return []
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("资源清单过大")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("protection") != "windows-user-dpapi":
        raise ValueError("不支持的资源保护格式")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise ValueError("资源清单为空")
    names = set()
    ciphers = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("资源清单条目无效")
        name = row.get("path", "")
        _safe_path(Path(directory), name)
        _safe_path(Path(directory), row.get("cipher", ""))
        if (name in names or row["cipher"] in ciphers or type(row.get("bytes")) is not int
                or not 0 <= row["bytes"] <= 1024 ** 3 or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))):
            raise ValueError("资源清单重复或大小无效")
        names.add(name)
        ciphers.add(row["cipher"])
    return rows


def read_asset(directory: Path, row: dict, protector=None) -> bytes:
    target = _safe_path(Path(directory), row["cipher"])
    if target.stat().st_size > row["bytes"] + 1024 ** 2:
        raise ValueError("加密资源大小无效")
    ciphertext = target.read_bytes()
    plaintext = (protector or _protector()).unprotect(ciphertext)
    if len(plaintext) != row["bytes"] or hashlib.sha256(plaintext).hexdigest() != row["sha256"]:
        raise ValueError("资源完整性验证失败：" + row["path"])
    return plaintext


def pack_directory(source: Path, destination: Path) -> dict:
    """Make a new verified vault. Never remove or overwrite the source."""
    source, destination = Path(source), Path(destination)
    if not source.is_dir() or _linked(source) or _linked(destination) or destination.exists():
        raise ValueError("源目录不存在/为链接，或目标目录已存在")
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("源目录和资源目标不能互相嵌套")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files or any(_linked(path) or any(_linked(p) for p in path.parents) for path in files):
        raise ValueError("素材为空或包含链接")
    protector = _protector()
    destination.mkdir(parents=True)
    rows = []
    for path in files:
        data = path.read_bytes()
        relative = path.relative_to(source).as_posix()
        cipher = relative + ".rasset"
        target = _safe_path(destination, cipher)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(protector.protect(data))
        row = {"path": relative, "cipher": cipher, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if read_asset(destination, row, protector) != data:
            raise ValueError("加密后逐文件回读验证失败")
        rows.append(row)
    manifest = {"schema": 1, "protection": "windows-user-dpapi", "files": rows}
    (destination / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"files": len(rows), "bytes": sum(row["bytes"] for row in rows), "verified": True}


def resolve_source_directory(root: Path, value) -> Path:
    from .platforms import native_module
    original = native_module("runtime").project_path(root, value)
    if original.parent == root and original.name in {"rem-voice", "rem-voice2"}:
        protected = root / "resources/private/voice/source" / original.name
        if (protected / MANIFEST).is_file():
            return protected
    return original


@contextmanager
def training_sources(config):
    """Materialize vaults only for an explicit training/dataset operation."""
    from .secure_resources import ResourceVault
    ResourceVault(config.root).require('training')
    vaults = [path for path in config.dataset.source_dirs if (path / MANIFEST).is_file()]
    if not vaults:
        yield config
        return
    temporary_parent = config.root / "tmp"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="private-voice-", dir=temporary_parent) as folder:
        staging = Path(folder)
        sources = []
        protector = _protector()
        for index, source in enumerate(config.dataset.source_dirs):
            if source not in vaults:
                sources.append(source)
                continue
            materialized = staging / f"source-{index}"
            materialized.mkdir()
            for row in inventory(source):
                target = _safe_path(materialized, row["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read_asset(source, row, protector))
            sources.append(materialized)
        yield replace(config, dataset=replace(config.dataset, source_dirs=tuple(sources)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="保护/回读验证私有资源；不会删除原文件")
    parser.add_argument("action", choices=("pack", "verify"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "pack":
        if args.destination is None:
            parser.error("pack 需要新的目标目录")
        result = pack_directory(args.source, args.destination)
    else:
        rows = inventory(args.source)
        if not rows:
            raise ValueError("未找到加密资源清单")
        protector = _protector()
        for row in rows:
            read_asset(args.source, row, protector)
        result = {"files": len(rows), "bytes": sum(row["bytes"] for row in rows), "verified": True}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
