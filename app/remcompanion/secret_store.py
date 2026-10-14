"""API 凭据的安全存储协议与密文文件管理。

Windows 当前用户的数据保护 API 会把密文绑定到登录账户。项目目录里只保存
不可直接复用的密文，不把密钥写入 JSON、TOML、日志或命令行参数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
import re


PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SecretProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class FileSecretStore:
    """把保护器产生的密文按提供商分文件原子保存。"""

    def __init__(self, root: Path, protector: SecretProtector) -> None:
        self.directory = root / "data" / "credentials"
        self.protector = protector

    def _path(self, provider_id: str) -> Path:
        if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ValueError("provider ID 非法")
        return self.directory / f"{provider_id}.bin"

    def get(self, provider_id: str) -> str | None:
        path = self._path(provider_id)
        if not path.is_file():
            return None
        plaintext = self.protector.unprotect(path.read_bytes())
        return plaintext.decode("utf-8")

    def set(self, provider_id: str, secret: str) -> Path:
        normalized = secret.strip()
        if not normalized:
            raise ValueError("API Key 不能为空")
        path = self._path(provider_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(
            self.protector.protect(normalized.encode("utf-8"))
        )
        temporary.replace(path)
        return path

    def delete(self, provider_id: str) -> None:
        path = self._path(provider_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return


def create_secret_store(root: Path):
    from .platforms import native_module
    return native_module("secrets").create_secret_store(root)


def __getattr__(name: str):
    # Explicit old class imports remain available without loading them at startup.
    from .platforms import native_module
    if name == "WindowsDpapiProtector":
        return native_module("secrets", "windows").WindowsDpapiProtector
    if name == "UnavailableSecretStore":
        return native_module("secrets", "linux").UnavailableSecretStore
    raise AttributeError(name)
