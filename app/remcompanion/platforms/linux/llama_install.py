"""Pinned Linux-only LLM installation, separate from Windows and TTS runtimes.

conda-forge supplies a compatible userspace without replacing system glibc.
Package metadata/ELF probes are checked before publishing the active runtime.
Unknown prefixes and failed downloads are preserved, not cleared or overwritten.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import fcntl
import hashlib
import json
import os
import platform
import urllib.error
import urllib.request

from .download_processes import ACTIVE_DOWNLOADS, DownloadProcesses
from .network import configure_tls

MAMBA_VERSION = "2.9.0"
MAMBA_SHA256 = "366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3"
MAMBA_URL = ("https://github.com/mamba-org/micromamba-releases/releases/download/"
             "2.9.0-0/micromamba-linux-64")
READY_FILE = ".remchat-ready.json"
ACTIVE_FILE = "llama-cpp-active.json"
MODEL_FILENAME = "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
MODEL_SIZE = 3_013_027_808
MODEL_SHA256 = "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983"


@dataclass(frozen=True)
class Profile:
    name: str
    build: str
    sha256: str
    gpu: bool

    @property
    def directory(self) -> str:
        return f"llama-cpp-11351-{self.name}"

    @property
    def spec(self) -> str:
        return f"llama.cpp=11351={self.build}"

    @property
    def owner(self) -> dict:
        return {"application": "RemChat", "schema": 1, "platform": "linux-x86_64",
                "profile": self.name, "package": self.spec,
                "package_sha256": self.sha256}

    @property
    def owner_filename(self) -> str:
        # micromamba refuses non-conda folders. Keep provenance beside the
        # prefix, so a new prefix can remain absent until create() owns it.
        return f".{self.directory}.owner.json"


PROFILES = {
    "cuda129": Profile("cuda129", "cuda129_h0b4778f_0",
                       "2b91589248aba54eebd464e952bd984a9d24fd057cb6854bf0a43fcdea25db6c", True),
    "cpu": Profile("cpu", "cpu_mkl_hc2f5b01_0",
                   "605e011285c66cec2c5690df120cca216708bc807e200d04910a3524d5d207de", False),
}


def safe_path(root: Path, relative: str) -> Path:
    """Reject linked managed ancestors instead of following them outside root."""
    root = root.absolute()
    fragment = Path(relative)
    if fragment.is_absolute() or ".." in fragment.parts:
        raise ValueError("受管路径必须是无 .. 的相对路径。")
    path = root / relative
    if path != root and root not in path.parents:
        raise ValueError("受管路径越界。")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise RuntimeError(f"受管安装路径不能是符号链接：{candidate}")
        if candidate == root:
            break
    return path


def digest(path: Path, processes: DownloadProcesses = ACTIVE_DOWNLOADS) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            processes.check()
            result.update(chunk)
    return result.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".new")
    if temporary.is_symlink():
        raise RuntimeError(f"拒绝覆盖符号链接：{temporary}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def installation_lock(root: Path):
    directory = safe_path(root, "runtime/linux")
    directory.mkdir(parents=True, exist_ok=True)
    lock = safe_path(root, "runtime/linux/.llama-install.lock")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("另一个 Linux 本地模型安装任务正在运行。") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def download_verified(url: str, path: Path, *, sha256: str = "", size: int | None = None,
                      log=print, progress=None, processes=ACTIVE_DOWNLOADS) -> Path:
    """Resume into .part; promote only after verification. Existing bad data stays."""
    processes.check()
    if not url.startswith("https://"):
        raise ValueError("Linux 模型下载只接受 HTTPS URL。")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    if path.is_symlink() or partial.is_symlink():
        raise RuntimeError("下载目标不能是符号链接。")

    def verify(candidate: Path) -> None:
        processes.check()
        if size is not None and candidate.stat().st_size != size:
            raise RuntimeError(f"文件大小不匹配，原文件保留：{candidate}")
        if sha256 and digest(candidate, processes) != sha256:
            raise RuntimeError(f"SHA256 不匹配，原文件保留：{candidate}")
        if not sha256:
            with candidate.open("rb") as stream:
                if stream.read(4) != b"GGUF":
                    raise RuntimeError(f"文件不是 GGUF，原文件保留：{candidate}")

    if path.exists():
        verify(path)
        log(f"[校验/复用] {path.name}")
        if progress:
            progress(path.name, path.stat().st_size, size or path.stat().st_size)
        return path
    configure_tls()
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "RemChat/Linux installer"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        if error.code != 416 or not partial.exists():
            raise
        # Never trust HTTP 416 as evidence that a partial download is complete.
        verify(partial)
    else:
        with response:
            if offset and response.status != 206:
                offset = 0
            if offset and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                raise RuntimeError("服务器的续传位置不匹配；保留部分文件。")
            total = size or (offset + int(response.headers.get("Content-Length", "0")) or None)
            log(f"[下载] {path.name}")
            with partial.open("ab" if offset else "wb") as stream:
                while True:
                    processes.check()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    offset += len(chunk)
                    if progress:
                        progress(path.name, offset, total)
        verify(partial)
    processes.check()
    partial.replace(path)
    return path


def executable_for(root: Path) -> Path:
    """Locate only a ready managed Linux prefix; no Windows/PATH fallback."""
    missing = root / "runtime/linux/llama-cpp-not-installed/bin/llama-server"
    try:
        active = json.loads(safe_path(root, f"runtime/linux/{ACTIVE_FILE}").read_text(encoding="utf-8"))
        profile = PROFILES[active["profile"]]
        prefix = safe_path(root, f"runtime/linux/{profile.directory}")
        owner = json.loads(safe_path(root, f"runtime/linux/{profile.owner_filename}").read_text(encoding="utf-8"))
        ready = json.loads(safe_path(root, f"runtime/linux/{profile.directory}/{READY_FILE}").read_text(encoding="utf-8"))
        executable = safe_path(root, f"runtime/linux/{profile.directory}/bin/llama-server")
        if owner != profile.owner or ready != active or ready.get("package_sha256") != profile.sha256:
            return missing
        with executable.open("rb") as stream:
            if stream.read(5) != b"\x7fELF\x02":
                return missing
        if ready.get("executable_sha256") != hashlib.sha256(executable.read_bytes()).hexdigest():
            return missing
        return executable if os.access(executable, os.X_OK) else missing
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return missing


def verify_prefix(root: Path, profile: Profile, env: dict, *, log=print,
                  processes=ACTIVE_DOWNLOADS) -> dict:
    prefix = safe_path(root, f"runtime/linux/{profile.directory}")
    metadata = safe_path(root, f"runtime/linux/{profile.directory}/conda-meta/llama.cpp-11351-{profile.build}.json")
    package = json.loads(metadata.read_text(encoding="utf-8"))
    if package.get("sha256") != profile.sha256 or package.get("build") != profile.build:
        raise RuntimeError("Linux llama.cpp 包元数据不匹配；环境保留，不会激活。")
    executable = safe_path(root, f"runtime/linux/{profile.directory}/bin/llama-server")
    with executable.open("rb") as stream:
        if stream.read(5) != b"\x7fELF\x02":
            raise RuntimeError("Linux 引擎不是 64 位 ELF。")
    version = processes.run([str(executable), "--version"], env=env, log=log)
    devices = processes.run([str(executable), "--list-devices"], env=env, log=log)
    if profile.gpu and "CUDA0:" not in devices:
        raise RuntimeError("CUDA 环境未检测到可用 GPU；不会静默切换到 CPU。")
    return {**profile.owner, "executable_sha256": digest(executable, processes),
            "version": version.strip(), "devices": devices.strip()}


def install_runtime(root: Path, *, backend: str = "cuda129", log=print, progress=None,
                    processes=ACTIVE_DOWNLOADS) -> Path:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise RuntimeError("此安装入口仅支持 Linux x86_64；不会安装 Windows 依赖。")
    profile = PROFILES[backend]
    root = root.absolute()
    with installation_lock(root):
        processes.check()
        prefix = safe_path(root, f"runtime/linux/{profile.directory}")
        owner = safe_path(root, f"runtime/linux/{profile.owner_filename}")
        if prefix.exists() or owner.exists():
            try:
                owned = json.loads(owner.read_text(encoding="utf-8")) == profile.owner
            except (OSError, ValueError):
                owned = False
            if not owned:
                raise RuntimeError(f"存在未受管/被修改的环境，保留且不覆盖：{prefix}")
        else:
            write_json(owner, profile.owner)
        cache = safe_path(root, "cache/micromamba-linux-llm")
        cache.mkdir(parents=True, exist_ok=True)
        packages = safe_path(root, "cache/micromamba-linux-llm/pkgs")
        temporary = safe_path(root, "tmp/downloads/linux")
        temporary.mkdir(parents=True, exist_ok=True)
        # Configure before taking the environment snapshot for micromamba.
        configure_tls()
        env = dict(os.environ, MAMBA_ROOT_PREFIX=str(cache), CONDA_PKGS_DIRS=str(packages),
                   TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
        # Do not inherit a user's unrelated loader/conda environment into probes.
        for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
                    "CONDA_OVERRIDE_CUDA", "CONDA_OVERRIDE_GLIBC"):
            env.pop(key, None)
        ready_path = safe_path(root, f"runtime/linux/{profile.directory}/{READY_FILE}")
        if ready_path.exists():
            previous = json.loads(ready_path.read_text(encoding="utf-8"))
            executable = safe_path(root, f"runtime/linux/{profile.directory}/bin/llama-server")
            if (any(previous.get(key) != value for key, value in profile.owner.items())
                    or previous.get("executable_sha256") != digest(executable, processes)):
                raise RuntimeError("已安装的 Linux 引擎记录/文件被修改，保留且不覆盖。")
        else:
            mamba = safe_path(root, f"runtime/linux-tools/micromamba-{MAMBA_VERSION}")
            download_verified(MAMBA_URL, mamba, sha256=MAMBA_SHA256,
                              log=log, progress=progress, processes=processes)
            mamba.chmod(0o755)
            command = [str(mamba), "create", "--no-rc", "--yes", "--prefix", str(prefix),
                       "--override-channels", "--channel", "conda-forge", profile.spec]
            log(f"[Linux 独立环境] {profile.spec}")
            processes.run(command, env=env, log=log)
        ready = verify_prefix(root, profile, env, log=log, processes=processes)
        processes.check()
        write_json(ready_path, ready)
        write_json(safe_path(root, f"runtime/linux/{ACTIVE_FILE}"), ready)
        log(f"[完成] Linux {backend} 引擎已就绪，未更改 Windows 环境。")
        return prefix / "bin/llama-server"


def download_local_llm(config, *, log=print, progress=None) -> None:
    filename = str(config.downloads["qwen_filename"])
    if Path(filename).name != filename or "\\" in filename or not filename.endswith(".gguf"):
        raise ValueError("模型文件名必须是不含目录的 .gguf 名称。")
    model = safe_path(config.root, f"models/llm/{filename}")
    known = filename == MODEL_FILENAME
    download_verified(str(config.downloads["qwen_url"]), model,
                      sha256=MODEL_SHA256 if known else "", size=MODEL_SIZE if known else None,
                      log=log, progress=progress)
    install_runtime(config.root, backend=str(config.downloads.get("llama_linux_backend", "cuda129")),
                    log=log, progress=progress)
    log("[完成] 本地聊天模型已就绪；未下载 TTS 或训练环境。")


def main() -> None:
    import argparse
    from ...config import load_config
    parser = argparse.ArgumentParser(description="RemChat Linux-only local LLM setup")
    parser.add_argument("--backend", choices=tuple(PROFILES), default="cuda129")
    options = parser.parse_args()
    config = load_config()
    config.downloads["llama_linux_backend"] = options.backend
    try:
        download_local_llm(config)
    finally:
        ACTIVE_DOWNLOADS.close()


if __name__ == "__main__":
    main()
