"""Compile only RemChat's Linux pidfd bridge; no downloads or system changes."""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_library(path):
    # Opens a handle to ourselves and checks signal 0 only; sends no signal.
    library = ctypes.CDLL(str(path), use_errno=True)
    library.remchat_pidfd_abi.restype = ctypes.c_int
    library.remchat_pidfd_abi.argtypes = []
    if library.remchat_pidfd_abi() != 1:
        raise ValueError("Unsupported native bridge ABI")
    library.remchat_pidfd_open.argtypes = [ctypes.c_int]
    library.remchat_pidfd_open.restype = ctypes.c_int
    library.remchat_pidfd_send_signal.argtypes = [ctypes.c_int, ctypes.c_int]
    library.remchat_pidfd_send_signal.restype = ctypes.c_int
    descriptor = library.remchat_pidfd_open(os.getpid())
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "Kernel pidfd_open probe failed")
    try:
        if library.remchat_pidfd_send_signal(descriptor, 0) != 0:
            raise OSError(ctypes.get_errno(), "Kernel pidfd_send_signal probe failed")
    finally:
        os.close(descriptor)


def build():
    if sys.platform != "linux":
        raise ValueError("Build the native bridge on Linux, never Windows")
    root = Path(__file__).resolve().parents[3]
    directory = root / "build/linux/native"
    for path in (directory, *directory.parents):
        if path.is_symlink():
            raise ValueError("Native build output must not have linked ancestors")
    source = root / "platforms/linux/native/pidfd.c"
    target = directory / "libremchat_pidfd.so"
    manifest = directory / "pidfd-build.json"
    if target.is_symlink() or manifest.is_symlink():
        raise ValueError("Native build files must not be links")
    source_hash = digest(source)
    if target.exists() or manifest.exists():
        if not target.is_file() or not manifest.is_file():
            raise ValueError("Existing unmanaged/incomplete native output preserved")
        prior = json.loads(manifest.read_text(encoding="utf-8"))
        if prior.get("schema") != 1 or prior.get("library_sha256") != digest(target):
            raise ValueError("Modified native output preserved; review before rebuilding")
        if prior.get("source_sha256") == source_hash:
            verify_library(target)
            print(json.dumps(prior))
            return
    compiler = shutil.which(os.environ.get("REMCHAT_CC", "cc"))
    if not compiler:
        raise ValueError("A native Linux C compiler and Linux syscall headers are required; no automatic system install")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pidfd-", dir=directory) as temporary:
        candidate = Path(temporary) / target.name
        command = [compiler, "-std=c11", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra", "-Werror",
                   "-Wl,-z,relro", "-Wl,-z,now", str(source), "-o", str(candidate)]
        subprocess.run(command, check=True)
        if candidate.read_bytes()[:4] != b"\x7fELF":
            raise ValueError("Compiler did not produce a Linux ELF bridge")
        verify_library(candidate)
        metadata = {"schema": 1, "source_sha256": source_hash, "library_sha256": digest(candidate),
                    "abi": 1, "compiler": compiler, "machine": os.uname().machine}
        staged_manifest = Path(temporary) / manifest.name
        staged_manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        candidate.replace(target)
        staged_manifest.replace(manifest)
    print(json.dumps(metadata))


if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError, AttributeError, subprocess.CalledProcessError) as error:
        print("Linux native build failed: " + str(error), file=sys.stderr)
        sys.exit(1)
