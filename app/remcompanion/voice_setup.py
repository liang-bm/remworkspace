"""Explicit, local-only GPT-SoVITS voice installation and protected pack import.

The desktop works without this optional runtime. No cloud voice API, training
dataset, account key or private resource export is involved in installation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import wave
import zipfile

from .secure_resources import ResourceVault, atomic, checked_path, linked, private_dir
from .process import project_environment, run_command

ENGINE_REVISION = "d523079fc05d9a8028d6085bffe4a2757c32abb6"
ENGINE_SHA256 = "de125730d7f08db9ddf756384e54780a4d34a24f719d79d943021f8068b98d5f"
MODEL_REVISION = "8f8857563c040d542de108a2c18b0425abd6c9c5"
LANGUAGE_REVISION = "0c47645e02a7bc3688d7b263b0042c81e3cd82cd"
UV_VERSION = "0.13.1"
UV_ASSETS = {
    "windows": ("uv-x86_64-pc-windows-msvc.zip", 15308685,
                "3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672"),
    "linux": ("uv-x86_64-unknown-linux-gnu.tar.gz", 16738706,
              "eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220"),
}
# (relative path, bytes, SHA256), pinned official inference assets only.
MODEL_FILES = (
    ("chinese-hubert-base/config.json", 1449, "c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103"),
    ("chinese-hubert-base/preprocessor_config.json", 212, "dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b"),
    ("chinese-hubert-base/pytorch_model.bin", 188811417, "24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c"),
    ("chinese-roberta-wwm-ext-large/config.json", 963, "3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5"),
    ("chinese-roberta-wwm-ext-large/pytorch_model.bin", 651225145, "e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8"),
    ("chinese-roberta-wwm-ext-large/tokenizer.json", 268962, "173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b"),
    ("sv/pretrained_eres2netv2w24s4ep4.ckpt", 107528697, "4f5a0bf73c61eb41b174e1bb54e7ee3c83233892be8e0af1f187024e8e581a35"),
)
LANGUAGE_FILES = (
    ("G2PWModel.zip", 588856634, "46292be0374a49308069233cd5c147ae4c41806558e4781a2467a31a4d8099da"),
    ("nltk_data.zip", 9924448, "eb3ec26ace3f9ccbb08a6d333e26f0941c47e230ece0717dc992bdb7e99808dd"),
    ("open_jtalk_dic_utf_8-1.11.tar.gz", 23646843, "fe6ba0e43542cef98339abdffd903e062008ea170b04e7e2a35da805902f382a"),
)
PACK_FILES = {
    "models/tts/rem/gpt/rem-gpt.ckpt",
    "models/tts/rem/sovits/rem-sovits.pth",
    "voice-source/reference.wav",
    "voice-source/reference.txt",
}
_RUNTIME_MARKER = "runtime/.voice-runtime.json"
_PACK_MARKER = "data/voice-pack.json"


def _read_json(path: Path, limit: int = 65536) -> dict:
    if linked(path) or path.stat().st_size > limit:
        raise ValueError("配置文件过大或不是普通文件")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("配置必须是 JSON 对象")
    return value


def material_download_url() -> str:
    """Return the release's public Drive page; never a guessed download URL."""
    candidates = []
    if os.environ.get("REM_HOME"):
        candidates.append(Path(os.environ["REM_HOME"]))
    if getattr(sys, "frozen", False):
        candidates.extend((Path(sys.executable).resolve().parent,
                           Path(sys.executable).resolve().parent.parent))
    candidates.extend(Path(__file__).resolve().parents)
    for root in candidates:
        metadata = root / "config" / "release.json"
        if not metadata.is_file():
            continue
        value = _read_json(metadata).get("voice_material_url", "")
        if not isinstance(value, str):
            raise ValueError("声线素材下载链接格式无效")
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if not value:
            return ""
        if (parsed.scheme != "https" or parsed.hostname not in {"drive.google.com", "docs.google.com"}
                or parsed.username or parsed.password or parsed.port not in {None, 443}):
            raise ValueError("声线素材下载链接不是公开 Google Drive 页面")
        return value
    return ""


def selected_voice_device(config) -> str:
    marker = checked_path(config.root, _RUNTIME_MARKER)
    if not marker.is_file():
        return "cpu"
    data = _read_json(marker)
    if data.get("schema") != 1 or data.get("device") not in {"cpu", "cuda"}:
        raise ValueError("语音运行环境配置无效，请重新安装语音环境")
    return data["device"]


def inspect_voice_readiness(config) -> dict:
    """Cheap readiness inspection; installation/import perform full hash checks."""
    missing = []
    runtime = checked_path(config.root, _RUNTIME_MARKER)
    if not config.gpt_python.is_file() or not runtime.is_file():
        missing.append("语音引擎运行环境")
    else:
        try:
            data = _read_json(runtime)
            if (data.get("schema") != 1 or data.get("engine_revision") != ENGINE_REVISION
                    or data.get("model_revision") != MODEL_REVISION
                    or data.get("device") not in {"cpu", "cuda"}):
                missing.append("语音引擎版本")
        except (OSError, ValueError):
            missing.append("语音环境配置")
    if not (config.gpt_sovits_dir / "api_v2.py").is_file():
        missing.append("GPT-SoVITS 引擎")
    if not config.ffmpeg.is_file():
        missing.append("FFmpeg 音频工具")
    base = config.gpt_sovits_dir / "GPT_SoVITS" / "pretrained_models"
    for relative, size, _digest in MODEL_FILES:
        path = checked_path(base, relative)
        if not path.is_file() or path.stat().st_size != size:
            missing.append("基础语音模型")
            break
    frontends = (
        config.fast_langdetect_model,
        config.gpt_sovits_dir / "GPT_SoVITS/text/G2PWModel/g2pW.onnx",
        config.root / "runtime/voice-language/open_jtalk_dic_utf_8-1.11/sys.dic",
        config.root / "runtime/voice-language/nltk_data",
    )
    if not all(path.exists() and not linked(path) for path in frontends):
        missing.append("中文 / 日文语言资源")
    try:
        vault = ResourceVault(config.root)
        vault.require("inference")
        for relative, label in (
            ("models/tts/rem/gpt/rem-gpt.ckpt", "GPT 声线权重"),
            ("models/tts/rem/sovits/rem-sovits.pth", "SoVITS 声线权重"),
            ("voice-source/reference.wav", "声线参考音频"),
            ("voice-source/reference.txt", "参考音频文字"),
        ):
            path = checked_path(config.root, relative)
            if not vault.contains(path):
                missing.append(label)
        if not checked_path(config.root, _PACK_MARKER).is_file():
            missing.append("已验证的声线素材包")
    except (OSError, ValueError, PermissionError):
        missing.append("本机安全密钥或声线权限")
    return {"ready": not missing, "missing": list(dict.fromkeys(missing))}


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _download(url: str, target: Path, size: int, digest: str, log) -> Path:
    """Only independently hashed downloads can become usable components."""
    for path in (target, *target.parents):
        if linked(path):
            raise ValueError("下载目标不能包含链接")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size == size and _digest(target) == digest:
            log("[已验证] " + target.name)
            return target
        raise ValueError("已有组件校验不符，未覆盖：" + target.name)
    partial = target.with_name(target.name + ".part")
    if linked(partial):
        raise ValueError("下载缓存不能是链接")
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    if offset == size:
        if _digest(partial) == digest:
            partial.replace(target)
            return target
        partial.unlink()
        offset = 0
    headers = {"User-Agent": "RemWorkspace/0.1.0 voice setup"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    log("[下载] " + target.name)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        status = getattr(response, "status", 200)
        append = status == 206 and offset > 0
        if status not in {200, 206}:
            raise OSError("下载服务器返回非成功状态")
        if status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match or tuple(map(int, match.groups())) != (offset, size - 1, size):
                raise ValueError("下载续传范围不符")
        if not append:
            offset = 0
        with partial.open("ab" if append else "wb") as output:
            while block := response.read(1024 * 1024):
                offset += len(block)
                if offset > size:
                    raise ValueError("下载超过清单规定大小")
                output.write(block)
    if partial.stat().st_size != size or _digest(partial) != digest:
        raise ValueError("下载 SHA256 或大小校验失败：" + target.name)
    partial.replace(target)
    return target


def _extract(archive: Path, destination: Path, *, strip_prefix: str = "") -> None:
    """Extract official hashed archives without traversal, links or devices."""
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            for member in members:
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))):
                    raise ValueError("组件压缩包包含链接或特殊文件")
                name = member.filename.rstrip("/")
                if strip_prefix:
                    if name == strip_prefix:
                        continue
                    if not name.startswith(strip_prefix + "/"):
                        raise ValueError("组件压缩包根目录不符")
                    name = name[len(strip_prefix) + 1:]
                target = checked_path(destination, name)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if not (member.isfile() or member.isdir()):
                    raise ValueError("组件压缩包包含链接或特殊文件")
                target = checked_path(destination, member.name.rstrip("/"))
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)


def _managed_uv(config, log) -> Path:
    system = platform.system().lower()
    if system not in UV_ASSETS or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise NotImplementedError("自动语音安装目前支持 Windows / Linux x86_64")
    filename, size, digest = UV_ASSETS[system]
    directory = checked_path(config.root, "runtime/voice-tools")
    archive = _download(f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{filename}",
                        directory / filename, size, digest, log)
    executable = directory / ("uv.exe" if system == "windows" else "uv-x86_64-unknown-linux-gnu/uv")
    if not executable.is_file():
        _extract(archive, directory)
        if system == "linux":
            executable.chmod(0o700)
    if linked(executable):
        raise ValueError("语音安装工具不能是链接")
    # Re-check executable against the authenticated archive, not only its name.
    if system == "windows":
        with zipfile.ZipFile(archive) as bundle:
            expected = hashlib.sha256(bundle.read("uv.exe")).hexdigest()
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            with bundle.extractfile("uv-x86_64-unknown-linux-gnu/uv") as source:
                expected = hashlib.sha256(source.read()).hexdigest()
    if _digest(executable) != expected:
        raise ValueError("语音安装工具内容校验失败")
    return executable


def _require_ffmpeg(config) -> None:
    """Verify the executable, not the unrelated ffmpeg-python wrapper package."""
    from .platforms import native_module
    if not config.ffmpeg.is_file():
        if platform.system() == "Windows":
            guide = ("请从 https://ffmpeg.org/download.html 下载 Windows 版，"
                     "把解压后的 bin 文件夹放到本软件的 engines/ffmpeg/bin，"
                     "保留同目录的 DLL 和许可证，然后重新安装语音环境。")
        else:
            guide = ("Ubuntu / Debian 请先在终端运行 sudo apt install ffmpeg；"
                     "其他发行版使用系统包管理器安装。完成后重新安装语音环境。")
        raise FileNotFoundError("尚未安装 FFmpeg。" + guide)
    try:
        result = subprocess.run([str(config.ffmpeg), "-version"], capture_output=True,
                                timeout=15, **native_module("processes").hidden_process_options())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("FFmpeg 无法运行；请检查完整的安装文件及执行权限") from exc
    if result.returncode or not result.stdout.startswith(b"ffmpeg version"):
        raise RuntimeError("FFmpeg 无法运行；请检查完整的安装文件及执行权限")


def _require_voice_build_tools(config) -> None:
    """Source-only Japanese frontend/jieba bindings need a native compiler."""
    from .platforms import native_module
    options = native_module("processes").hidden_process_options()
    # Existing exact versions do not need to be rebuilt on another installation.
    if config.gpt_python.is_file():
        probe = ("import importlib.metadata as m; "
                 "assert m.version('pyopenjtalk')=='0.4.1'; "
                 "assert m.version('jieba-fast')=='0.53'")
        result = subprocess.run([str(config.gpt_python), "-I", "-c", probe],
                                capture_output=True, timeout=30, **options)
        if result.returncode == 0:
            return
    if platform.system() == "Windows":
        if shutil.which("cl"):
            return
        program_files = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
        locator = Path(program_files) / "Microsoft Visual Studio/Installer/vswhere.exe"
        if locator.is_file():
            result = subprocess.run([str(locator), "-latest", "-products", "*", "-requires",
                                     "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                                     "-property", "installationPath"], capture_output=True,
                                    timeout=15, **options)
            if result.returncode == 0 and result.stdout.strip():
                return
        raise RuntimeError("日文语音依赖需要 C++ 编译器。请从 "
                           "https://visualstudio.microsoft.com/visual-cpp-build-tools/ "
                           "安装 Build Tools，勾选“使用 C++ 的桌面开发”（含 MSVC 和 Windows SDK），"
                           "重启本软件后重新安装语音环境。CMake / Cython 由隔离构建环境安装。")
    if shutil.which("c++") or shutil.which("g++"):
        return
    raise RuntimeError("日文语音依赖需要 C++ 编译器。Ubuntu / Debian 请先运行 "
                       "sudo apt install build-essential；其他发行版安装对应 C/C++ 工具链，"
                       "然后重新安装语音环境。CMake / Cython 由隔离构建环境安装。")


def install_voice_environment(config, device: str = "cpu", log=print) -> None:
    """Install only inference components, in this app's managed environment."""
    if device not in {"cpu", "cuda"}:
        raise ValueError("语音设备请选择 cpu 或 cuda")
    _require_ffmpeg(config)
    _require_voice_build_tools(config)
    uv = _managed_uv(config, log)
    downloads = checked_path(config.root, "tmp/voice-downloads")
    archive = _download(f"https://codeload.github.com/RVC-Boss/GPT-SoVITS/zip/{ENGINE_REVISION}",
                        downloads / "gpt-sovits-source.zip", 6621083, ENGINE_SHA256, log)
    source_marker = config.gpt_sovits_dir / ".remworkspace-engine-revision"
    if not source_marker.is_file():
        if config.gpt_sovits_dir.exists() and any(config.gpt_sovits_dir.iterdir()):
            raise ValueError("已有 GPT-SoVITS 目录未由此安装器管理，未覆盖原文件")
        _extract(archive, config.gpt_sovits_dir, strip_prefix="GPT-SoVITS-" + ENGINE_REVISION)
        atomic(source_marker, (ENGINE_REVISION + "\n").encode("ascii"))
    if source_marker.read_text(encoding="ascii").strip() != ENGINE_REVISION:
        raise ValueError("已有语音引擎版本不符，未覆盖")
    environment = checked_path(config.root, "runtime/gpt-sovits")
    if not config.gpt_python.is_file():
        run_command([uv, "venv", environment, "--python", "3.10", "--managed-python"], cwd=config.root, log=log)
    index = "https://download.pytorch.org/whl/" + ("cpu" if device == "cpu" else "cu128")
    run_command([uv, "pip", "install", "--python", config.gpt_python, "--index-url", index,
                 "torch==2.8.0", "torchaudio==2.8.0"], cwd=config.root, log=log)
    # No Gradio, FunASR, training dataset, base generator, discriminator or LLM.
    packages = [
        "numpy==1.26.4", "scipy==1.15.3", "librosa==0.10.2", "ffmpeg-python==0.2.0",
        "pytorch-lightning==2.6.5", "torchmetrics==1.5.0", "transformers==4.50.0",
        "peft==0.17.1", "sentencepiece==0.2.2", "PyYAML==6.0.3", "psutil==7.2.2",
        "cn2an==0.5.24", "pypinyin==0.55.0", "jieba==0.42.1", "jieba-fast==0.53",
        "pyopenjtalk==0.4.1", "g2p-en==2.1.0", "wordsegment==1.3.1",
        "fast-langdetect==1.0.1", "fasttext-predict==0.9.2.4", "split-lang==2.1.1", "rotary-embedding-torch==0.9.1",
        "x-transformers==2.24.0", "fastapi==0.139.2", "pydantic==2.10.6", "uvicorn==0.50.1",
        "soundfile==0.14.0", "onnxruntime==1.23.2", "tqdm==4.69.0", "opencc==1.4.1",
        "chardet==7.4.3", "ToJyutping==3.2.0", "g2pk2==0.0.3", "ko-pron==1.3",
    ]
    run_command([uv, "pip", "install", "--python", config.gpt_python, "--index-url", "https://pypi.org/simple",
                 *packages], cwd=config.root, log=log)
    base = config.gpt_sovits_dir / "GPT_SoVITS/pretrained_models"
    for relative, size, digest in MODEL_FILES:
        _download(f"https://huggingface.co/lj1995/GPT-SoVITS/resolve/{MODEL_REVISION}/{relative}",
                  checked_path(base, relative), size, digest, log)
    _download("https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin",
              config.fast_langdetect_model, 131266198,
              "7e69ec5451bc261cc7844e49e4792a85d7f09c06789ec800fc4a44aec362764e", log)
    language = checked_path(config.root, "runtime/voice-language")
    for filename, size, digest in LANGUAGE_FILES:
        resource = _download(f"https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/{LANGUAGE_REVISION}/{filename}",
                             downloads / filename, size, digest, log)
        target = config.gpt_sovits_dir / "GPT_SoVITS/text" if filename == "G2PWModel.zip" else language
        _extract(resource, target)
    env = project_environment(config.root)
    env["PYTHONPATH"] = os.pathsep.join((str(config.gpt_sovits_dir / "GPT_SoVITS"), str(config.gpt_sovits_dir)))
    env["NLTK_DATA"] = str(language / "nltk_data")
    env["OPEN_JTALK_DICT_DIR"] = str(language / "open_jtalk_dic_utf_8-1.11")
    smoke = ("import torch,torchaudio; from TTS_infer_pack.TTS import TTS; "
             "from text.cleaner import clean_text; clean_text('こんにちは。','ja','v2Pro'); "
             "assert torch.__version__.startswith('2.8.0'); "
             "assert torchaudio.__version__.startswith('2.8.0'); ")
    if device == "cuda":
        smoke += "assert torch.cuda.is_available(), 'CUDA unavailable'; "
    smoke += "print('Voice runtime imports and Japanese frontend verified')"
    run_command([config.gpt_python, "-s", "-c", smoke], cwd=config.gpt_sovits_dir, log=log, env_updates=env)
    marker = checked_path(config.root, _RUNTIME_MARKER)
    atomic(marker, json.dumps({"schema": 1, "engine_revision": ENGINE_REVISION,
                             "model_revision": MODEL_REVISION, "device": device}).encode("utf-8"))
    # Expanded language models are the runtime copy. Discard only this installer's
    # authenticated transport archives after the smoke check succeeds.
    for completed in (archive, *(downloads / row[0] for row in LANGUAGE_FILES)):
        if completed.is_file() and not linked(completed):
            completed.unlink()
    log("[完成] 语音运行环境已验证；下一步导入声线素材包")


_SAFE_CHECKPOINT = r'''
import io, json, math, sys, torch
if not torch.__version__.startswith('2.8.0'):
    raise ValueError('Install the supported PyTorch 2.8 voice runtime first')
def plain(value, depth=0):
    if depth > 30: raise ValueError('Metadata nesting too deep')
    if value is None or type(value) in (bool,int): return
    if type(value) is float and math.isfinite(value): return
    if type(value) is str:
        if len(value)>1024 or '/' in value or '\\' in value or ':' in value: raise ValueError('Unexpected path metadata')
        return
    if type(value) in (list,tuple):
        for child in value: plain(child,depth+1)
        return
    if type(value) is dict:
        for key,child in value.items():
            if type(key) is not str: raise ValueError('Invalid metadata key')
            plain(key,depth+1); plain(child,depth+1)
        return
    raise ValueError('Non-primitive checkpoint metadata')
for filename in sys.argv[1:]:
    with open(filename,'rb') as source: content=source.read()
    is_sovits=filename.endswith('.pth')
    if is_sovits:
        if content[:2]!=b'05': raise ValueError('Expected full v2Pro weight')
        content=b'PK'+content[2:]
    elif content[:2]!=b'PK': raise ValueError('Expected tensor ZIP')
    value=torch.load(io.BytesIO(content),map_location='cpu',weights_only=True)
    if type(value) is not dict or set(value)!={'weight','config','info'}: raise ValueError('Unexpected checkpoint fields')
    plain(value['config']); plain(value['info'])
    if type(value['weight']) is not dict or not value['weight']: raise ValueError('Missing tensor weights')
    for name,tensor in value['weight'].items():
        if type(name) is not str or type(tensor) is not torch.Tensor or tensor.layout!=torch.strided:
            raise ValueError('Non-tensor checkpoint payload')
        if tensor.numel()*tensor.element_size()>len(content):
            raise ValueError('Tensor view exceeds checkpoint size')
        if not torch.isfinite(tensor).all(): raise ValueError('Non-finite tensor')
    if is_sovits:
        if set(value['config'])!={'model','data','train'}: raise ValueError('Unexpected SoVITS config')
        if set(value['config']['train'])!={'segment_size'}: raise ValueError('Unexpected training metadata')
    elif set(value['config'])!={'model','data'} or set(value['config']['data'])!={'max_sec'}:
        raise ValueError('Unexpected GPT config')
print('Checkpoint tensors and inference-only metadata verified')
'''


@contextmanager
def _voice_archive(config, selected_path, log):
    """Join local, sequential, checksum-verified parts; never fetch manifest URLs."""
    selected = Path(selected_path)
    if linked(selected) or not selected.is_file():
        raise ValueError("请选择普通 ZIP 或 .voice-parts.json 声线清单")
    if not selected.name.endswith(".voice-parts.json"):
        yield selected
        return
    manifest = _read_json(selected)
    if (set(manifest) != {"schema", "format", "archive", "bytes", "sha256", "parts"}
            or manifest.get("schema") != 1 or manifest.get("format") != "remworkspace-voice-parts"
            or not isinstance(manifest.get("archive"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.zip", manifest["archive"])
            or type(manifest.get("bytes")) is not int or not 0 < manifest["bytes"] <= 1024 ** 3
            or not isinstance(manifest.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"])
            or not isinstance(manifest.get("parts"), list) or not 1 <= len(manifest["parts"]) <= 16):
        raise ValueError("声线分卷清单无效")
    parts = []
    total = 0
    for index, row in enumerate(manifest["parts"], start=1):
        expected = manifest["archive"] + f".{index:03d}"
        if (type(row) is not dict or set(row) != {"path", "bytes", "sha256"}
                or row.get("path") != expected or type(row.get("bytes")) is not int
                or not 0 < row["bytes"] <= 128 * 1024 ** 2
                or not isinstance(row.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
            raise ValueError("声线分卷必须是同目录内依次编号的普通文件")
        path = checked_path(selected.parent, expected)
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise ValueError("声线分卷缺失或大小不符：" + expected)
        total += row["bytes"]
        parts.append((path, row))
    if total != manifest["bytes"]:
        raise ValueError("声线分卷总大小与清单不符")
    temporary_root = checked_path(config.root, "tmp/voice-import")
    private_dir(temporary_root)
    with tempfile.TemporaryDirectory(prefix="joined-", dir=temporary_root) as temporary:
        archive = Path(temporary) / manifest["archive"]
        whole_digest = hashlib.sha256()
        log("[校验] 正在校验并合并本地声线分卷")
        with archive.open("xb") as output:
            for path, row in parts:
                part_digest = hashlib.sha256()
                count = 0
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        count += len(block)
                        if count > row["bytes"]:
                            raise ValueError("声线分卷在读取时发生变化")
                        part_digest.update(block)
                        whole_digest.update(block)
                        output.write(block)
                if count != row["bytes"] or part_digest.hexdigest() != row["sha256"]:
                    raise ValueError("声线分卷 SHA256 不符：" + path.name)
        if whole_digest.hexdigest() != manifest["sha256"]:
            raise ValueError("合并后声线 ZIP 的 SHA256 不符")
        yield archive


def import_voice_pack(config, archive_path, log=print) -> None:
    """Accept the original ZIP or a local parts manifest, then protected-import it."""
    with _voice_archive(config, archive_path, log) as archive:
        _import_voice_zip(config, archive, log)


def _import_voice_zip(config, archive_path, log=print) -> None:
    """Validate a four-file pack, then seal it to the user's account-bound vault."""
    archive = Path(archive_path)
    if linked(archive) or not archive.is_file() or archive.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError("请选择普通 ZIP 声线素材包（最大 1 GB）")
    if not config.gpt_python.is_file():
        raise FileNotFoundError("请先安装语音引擎运行环境")
    vault = ResourceVault(config.root)
    vault.require("inference")
    for relative in PACK_FILES:
        path = checked_path(config.root, relative)
        if path.exists() or vault.contains(path):
            raise FileExistsError("已有声线素材，未覆盖；请使用新的安装目录导入")
    temporary_root = checked_path(config.root, "tmp/voice-import")
    private_dir(temporary_root)
    with tempfile.TemporaryDirectory(prefix="verified-", dir=temporary_root) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            names = [entry.filename for entry in members]
            if set(names) != PACK_FILES | {"voice-pack.json"} or len(names) != 5:
                raise ValueError("声线包只能包含清单及四个规定的推理文件")
            for entry in members:
                mode = entry.external_attr >> 16
                if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
                    raise ValueError("声线包包含链接或特殊文件")
                checked_path(staging, entry.filename)
            if bundle.getinfo("voice-pack.json").file_size > 65536:
                raise ValueError("声线清单过大")
            manifest = json.loads(bundle.read("voice-pack.json").decode("utf-8"))
            if (not isinstance(manifest, dict)
                    or set(manifest) != {"schema", "engine", "files", "reference_language"}
                    or manifest.get("schema") != 1
                    or manifest.get("engine") != "gpt-sovits-v2Pro"
                    or manifest.get("reference_language") != "ja"
                    or not isinstance(manifest.get("files"), list)):
                raise ValueError("声线清单版本、引擎或参考语言不符")
            rows = manifest["files"]
            if len(rows) != 4 or {row.get("path") for row in rows if isinstance(row, dict)} != PACK_FILES:
                raise ValueError("声线清单文件集合无效")
            for row in rows:
                if (set(row) != {"path", "bytes", "sha256"} or type(row["bytes"]) is not int
                        or not 0 < row["bytes"] <= 512 * 1024 * 1024
                        or not isinstance(row["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", row["sha256"])):
                    raise ValueError("声线清单大小或摘要无效")
                entry = bundle.getinfo(row["path"])
                if entry.file_size != row["bytes"]:
                    raise ValueError("声线包文件大小不符")
                path = checked_path(staging, row["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                if _digest(path) != row["sha256"]:
                    raise ValueError("声线包文件 SHA256 不符")
        reference = staging / "voice-source/reference.wav"
        with wave.open(str(reference), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            if audio.getcomptype() != "NONE" or audio.getnchannels() not in {1, 2} or not 3 <= duration <= 10:
                raise ValueError("参考音频必须是 3–10 秒 PCM WAV")
        transcript = staging / "voice-source/reference.txt"
        text = transcript.read_text(encoding="utf-8").strip() if transcript.stat().st_size <= 16384 else ""
        if not text or len(text) > 4096:
            raise ValueError("参考音频文字为空或过大")
        command = [str(config.gpt_python), "-I", "-c", _SAFE_CHECKPOINT,
                   str(staging / "models/tts/rem/gpt/rem-gpt.ckpt"),
                   str(staging / "models/tts/rem/sovits/rem-sovits.pth")]
        from .platforms import native_module
        result = subprocess.run(command, capture_output=True, timeout=180,
                                **native_module("processes").hidden_process_options())
        if result.returncode:
            raise ValueError("声线权重不是安全、纯推理的 v2Pro 张量文件，未导入")
        imported = []
        try:
            for row in rows:
                target = checked_path(config.root, row["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output, checked_path(staging, row["path"]).open("rb") as source:
                    shutil.copyfileobj(source, output)
                imported.append(target)
            vault.seal_many([(path, "voice", True) for path in imported])
            marker = checked_path(config.root, _PACK_MARKER)
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic(marker, json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        finally:
            # These paths were exclusively created above; originals are never removed.
            for path in imported:
                if path.exists() and not linked(path):
                    path.unlink()
    log("[完成] 声线已导入并加密保存；原始下载包未删除")
