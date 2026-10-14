"""One routing boundary; operating-system implementations live in sibling folders.

Shared application code asks for a named capability, not for OS conditionals.
Import lazily so opening Windows never loads Linux native libraries (and vice versa).
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
import re
import sys


def native_module(name: str, platform: str | None = None) -> ModuleType:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Invalid platform module name")
    selected = platform
    if selected is None:
        if sys.platform == "win32":
            selected = "windows"
        elif sys.platform.startswith("linux"):
            selected = "linux"
        else:
            raise OSError(f"RemChat currently supports Windows and Linux, not {sys.platform}")
    if selected not in {"windows", "linux"}:
        raise ValueError("Unknown platform implementation")
    return import_module(f"{__name__}.{selected}.{name}")
