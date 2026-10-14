"""只读平台探测与 Windows/WSL 路径转换，不安装软件、不启动模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any


from .platforms import native_module
from .platforms.path_conversion import windows_path_to_wsl


def environment_python(root: Path, name: str) -> Path:
    return native_module("runtime").environment_python(root, name)


def decode_wsl_output(raw: bytes) -> str:
    # Explicit format decoder; no Windows process is created by this facade.
    return native_module("runtime", "windows").decode_wsl_output(raw)


@dataclass(frozen=True)
class PlatformCapabilities:
    system: str
    python: str
    python_version: str
    tkinter: bool
    pillow: bool
    secure_credentials: str
    audio_players: tuple[str, ...]
    display_available: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def platform_capabilities() -> PlatformCapabilities:
    """Read-only dependency and host facts; does not create a GUI or start models."""
    import importlib.util
    return PlatformCapabilities(
        system=sys.platform, python=sys.executable,
        python_version=".".join(str(piece) for piece in sys.version_info[:3]),
        tkinter=importlib.util.find_spec("tkinter") is not None,
        pillow=importlib.util.find_spec("PIL") is not None,
        **native_module("runtime").host_facts(),
    )


def wsl_inventory(timeout: float = 20) -> dict[str, Any]:
    return native_module("runtime").wsl_inventory(timeout)


def main() -> None:
    """python -m remcompanion.platform_support [--wsl]；只把事实输出至 stdout。"""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wsl", action="store_true", help="附带 Windows WSL 版本与发行版清单")
    args = parser.parse_args()
    result: dict[str, Any] = {"host": platform_capabilities().to_dict()}
    if args.wsl:
        result["wsl"] = wsl_inventory()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
