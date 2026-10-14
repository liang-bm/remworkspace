"""Pure path-format conversion; does not load native APIs on either platform."""
from pathlib import Path, PureWindowsPath, PurePosixPath
import re
import unicodedata


def workspace_path(value) -> str:
    """Validate a native directory string without changing its identity.

    Invalid or oversized values become unknown, never a truncated or guessed
    directory. Preserve internal/edge spaces, Unicode and native spellings;
    callers redact only the rendered preview, not filesystem input. No mapping,
    normalization, native API or filesystem access is performed here.
    """
    if not isinstance(value, str) or len(value) > 4000:
        return ""
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        return ""
    return value

def windows_path_to_wsl(path: str | Path) -> str:
    """F:\\目录 映射为 /mnt/f/目录；拒绝相对盘符、UNC 与控制字符。"""
    text = str(path)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("路径包含控制字符")
    if text.startswith("//"):
        raise ValueError("UNC 网络路径不能按默认 WSL 盘符规则转换")
    if text.startswith("/"):
        value_posix = PurePosixPath(text)
        if ".." in value_posix.parts:
            raise ValueError("请先将路径解析成无 .. 的绝对路径")
        return str(value_posix)
    value = PureWindowsPath(text)
    if not value.is_absolute() or not re.fullmatch(r"[A-Za-z]:", value.drive):
        raise ValueError("请提供 Windows 盘符绝对路径，或已有的 Linux 绝对路径")
    pieces = value.parts[1:]
    if ".." in pieces:
        raise ValueError("请先将路径解析成无 .. 的绝对路径")
    return str(PurePosixPath("/mnt", value.drive[0].lower(), *pieces))
