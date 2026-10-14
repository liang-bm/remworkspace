# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 规格。

选择 onedir 而不是 onefile：避免每次启动都把运行时解压到 C 盘临时目录，
也缩短桌面常驻程序的启动时间。模型、TTS 引擎和记忆数据库继续位于项目根。
"""

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules,collect_dynamic_libs,copy_metadata

if sys.platform != "win32":
    raise RuntimeError("Build the Windows specification on Windows")


project_root = Path(SPECPATH).resolve()
if project_root.name == "windows" and project_root.parent.name == "platforms":
    project_root = project_root.parents[1]
sys.path.insert(0, str(project_root / "app"))
asset_root = project_root / "app" / "remcompanion" / "assets"
icon_path = asset_root / "pet" / "rem-companion.ico"
adapter_root = project_root / "app" / "remcompanion" / "integrations" / "adapters"
from remcompanion.integrations.monitor_runtime import build_runtime_archive
monitor_archive = build_runtime_archive("windows", project_root / "build/windows/monitor-runtime.zip")
adapter_data = [
    (str(path), str(Path("remcompanion/integrations/adapters") / path.parent.relative_to(adapter_root)))
    for path in adapter_root.rglob("*")
    if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
]

a = Analysis(
    [str(project_root / "app/remcompanion/desktop_entry.py")],
    pathex=[str(project_root / "app")],
    binaries=collect_dynamic_libs('sqlite_vec',search_patterns=['*.dll','*.so','*.dylib']),
    datas=[(str(asset_root), "remcompanion/assets"), *adapter_data,
           *[(str(project_root / name), "remcompanion/documentation")
             for name in ("README.md", "README.en.md", "README.ja.md")],
           (str(monitor_archive), "remcompanion/integrations/resources"),
           (str(project_root/'app/remcompanion/tts_guard_entry.py'),'remcompanion'),
           *copy_metadata('Pygments'),*copy_metadata('cryptography'),*copy_metadata('sqlite-vec')],
    hiddenimports=collect_submodules("remcompanion.platforms.windows") + ["remcompanion.platforms.path_conversion"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["remcompanion.platforms.linux"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RemChat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RemChat",
)
