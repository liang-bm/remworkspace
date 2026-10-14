"""Windows account-bound DPAPI credentials; ciphertext format is unchanged."""
from __future__ import annotations
from ctypes import POINTER, Structure, byref, c_char, c_void_p, cast, create_string_buffer
from ctypes import wintypes
import ctypes
import os
from pathlib import Path

class _DataBlob(Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", POINTER(c_char)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = create_string_buffer(data)
    return (
        _DataBlob(len(data), cast(buffer, POINTER(c_char))),
        buffer,
    )


class WindowsDpapiProtector:
    """使用 CryptProtectData/CryptUnprotectData 绑定当前 Windows 用户。"""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows DPAPI 仅在 Windows 上可用")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, plaintext: bytes) -> bytes:
        source, _buffer = _blob(plaintext)
        output = _DataBlob()
        success = self._crypt32.CryptProtectData(
            byref(source),
            "Rem Companion API credential",
            None,
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            byref(output),
        )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(cast(output.pbData, c_void_p))

    def unprotect(self, ciphertext: bytes) -> bytes:
        source, _buffer = _blob(ciphertext)
        output = _DataBlob()
        success = self._crypt32.CryptUnprotectData(
            byref(source),
            None,
            None,
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            byref(output),
        )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(cast(output.pbData, c_void_p))



def create_secret_store(root: Path):
    from ...secret_store import FileSecretStore
    return FileSecretStore(root, WindowsDpapiProtector())


def storage_description() -> str:
    return "密钥使用 Windows DPAPI 加密。"
