"""Stable Linux process handles, including Python builds without pidfd APIs.

The tiny bundled C bridge uses the native build headers, never architecture
syscall numbers guessed in Python. Missing/unsupported bridges fail closed.
"""

from functools import lru_cache
from pathlib import Path
import ctypes
import errno
import os
import signal
import sys


@lru_cache(maxsize=1)
def _bridge():
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "Linux pidfd bridge is unavailable on this OS")
    module_dir = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        path = module_dir / "resources/native/libremchat_pidfd.so"
    else:
        path = module_dir.parents[3] / "build/linux/native/libremchat_pidfd.so"
    if path.is_symlink() or not path.is_file():
        raise OSError(errno.ENOSYS, "Linux pidfd bridge missing; run platforms/linux/Build-Native.sh")
    try:
        library = ctypes.CDLL(str(path), use_errno=True)
        library.remchat_pidfd_abi.argtypes = []
        library.remchat_pidfd_abi.restype = ctypes.c_int
        if library.remchat_pidfd_abi() != 1:
            raise OSError(errno.ELIBBAD, "Unsupported RemChat pidfd ABI")
        library.remchat_pidfd_open.argtypes = [ctypes.c_int]
        library.remchat_pidfd_open.restype = ctypes.c_int
        library.remchat_pidfd_send_signal.argtypes = [ctypes.c_int, ctypes.c_int]
        library.remchat_pidfd_send_signal.restype = ctypes.c_int
    except AttributeError as error:
        raise OSError(errno.ELIBBAD, "Incomplete RemChat pidfd bridge") from error
    return library


def _native_integer(value, *, minimum):
    if type(value) is not int or value < minimum or ctypes.c_int(value).value != value:
        raise OSError(errno.EINVAL, "Invalid native pidfd argument")
    return value


def _result(value):
    if value < 0:
        number = ctypes.get_errno() or errno.EIO
        raise OSError(number, os.strerror(number))
    return value


def open_pidfd(pid: int) -> int:
    pid = _native_integer(pid, minimum=1)
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        return os.pidfd_open(pid, 0)
    return _result(_bridge().remchat_pidfd_open(pid))


def send_signal(descriptor: int, sig: int) -> None:
    descriptor = _native_integer(descriptor, minimum=0)
    # signal.Signals is an IntEnum; accept its explicit native integer value.
    sig = _native_integer(int(sig), minimum=0)
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(descriptor, sig)
    else:
        _result(_bridge().remchat_pidfd_send_signal(descriptor, sig))
