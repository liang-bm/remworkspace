"""Compatibility entry; font implementations are isolated by operating system."""
from pathlib import Path
from .platforms import native_module

def configure_fonts(root: Path) -> bool:
    return native_module("fonts").configure_fonts(root)
