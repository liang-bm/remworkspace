"""接入程序的受控生命周期。

Windows 用 Job Object 在应用关闭时回收整个子进程树；POSIX 用独立进程组。
默认只继承运行程序所需的环境变量，密钥必须通过可见的 env_refs 明确选择。
"""

from __future__ import annotations

import os
from typing import Any


INHERITED_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME",
    "HOME", "LANG", "LC_ALL", "LC_CTYPE",
}


def program_environment(settings: dict[str, Any]) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() in INHERITED_ENV}
    environment.update({str(key): str(value) for key, value in settings.get("env", {}).items()})
    for key, reference in settings.get("env_refs", {}).items():
        if reference not in os.environ:
            raise ValueError(f"系统环境变量 {reference} 未设置。")
        environment[key] = os.environ[reference]
    return environment


from ..platforms import native_module


ProcessScope = native_module("processes").ProcessScope
