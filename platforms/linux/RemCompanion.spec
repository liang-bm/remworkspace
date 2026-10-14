# -*- mode: python ; coding: utf-8 -*-
"""Independent experimental Linux onedir specification; not a Windows build."""
from pathlib import Path
import hashlib
import os
import sys
from PyInstaller.utils.hooks import collect_submodules,copy_metadata,collect_dynamic_libs

if not sys.platform.startswith("linux"):
    raise RuntimeError("Build this specification on Linux, never Windows")
project_root = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(project_root / "app"))
asset_root = project_root / "app" / "remcompanion" / "assets"
font_root = Path(os.environ.get("REMCHAT_FONT_DIR", project_root / "runtime/linux-fonts")).resolve()
font_hashes = {
    "NotoSansCJKsc-Regular.otf": "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b",
    "LICENSE-Noto-CJK.txt": "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2",
}
font_data = []
for filename, expected in font_hashes.items():
    path = font_root / filename
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Missing or unverified Linux font resource: {path}. Run Setup-LinuxDesktop.sh.")
    font_data.append((str(path), "remcompanion/platforms/linux/resources/fonts"))
# Conda's SQLite/ICU needs the matching C++ ABI. PyInstaller otherwise selected
# Ubuntu's older system library: compilation succeeded, frozen startup failed.
runtime_binaries = []
native_root = project_root / "build/linux/native"
native_library = native_root / "libremchat_pidfd.so"
native_manifest = native_root / "pidfd-build.json"
import json
native_review = json.loads(native_manifest.read_text(encoding="utf-8"))
if (native_library.is_symlink() or native_manifest.is_symlink()
        or native_review.get("schema") != 1 or native_review.get("abi") != 1
        or native_review.get("library_sha256") != hashlib.sha256(native_library.read_bytes()).hexdigest()
        or native_review.get("source_sha256") != hashlib.sha256((project_root / "platforms/linux/native/pidfd.c").read_bytes()).hexdigest()):
    raise RuntimeError("Unverified Linux pidfd bridge; run Build-Native.sh")
runtime_binaries.append((str(native_library), "remcompanion/platforms/linux/resources/native"))
for filename in ("libstdc++.so.6", "libgcc_s.so.1"):
    path = Path(sys.prefix) / "lib" / filename
    if not path.is_file():
        raise RuntimeError(f"Required Linux build runtime library is missing: {path}")
    runtime_binaries.append((str(path), "."))
adapter_root = project_root / "app" / "remcompanion" / "integrations" / "adapters"
from remcompanion.integrations.monitor_runtime import build_runtime_archive
monitor_archive = build_runtime_archive("linux", project_root / "build/linux/monitor-runtime.zip")
adapter_data = [
    (str(path), str(Path("remcompanion/integrations/adapters") / path.parent.relative_to(adapter_root)))
    for path in adapter_root.rglob("*")
    if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
]
a = Analysis(
    [str(project_root / "app/remcompanion/platforms/linux/desktop_entry.py")],
    pathex=[str(project_root / "app"), str(project_root)],
    binaries=runtime_binaries+collect_dynamic_libs('sqlite_vec',search_patterns=['*.so','*.dll','*.dylib']),
    datas=[(str(asset_root), "remcompanion/assets"), *adapter_data, *font_data,
           *[(str(project_root / name), "remcompanion/documentation")
             for name in ("README.md", "README.en.md", "README.ja.md")],
           (str(monitor_archive), "remcompanion/integrations/resources"),
           (str(project_root/'app/remcompanion/tts_guard_entry.py'),'remcompanion'),
           *copy_metadata('Pygments'),*copy_metadata('cryptography'),
           *copy_metadata('SecretStorage',recursive=True),*copy_metadata('sqlite-vec')],
    # Pillow 12.3 imports its Tk finder dynamically only on the first image paste.
    hiddenimports=collect_submodules("remcompanion.platforms.linux") + [
        "remcompanion.platforms.path_conversion", "PIL._tkinter_finder",
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["remcompanion.platforms.windows"], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="RemChat",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RemChat")
