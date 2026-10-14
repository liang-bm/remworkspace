"""PyInstaller 与源码运行共用的独立桌面入口。"""

from pathlib import Path
import os
import sys
import traceback

from remcompanion.platforms import native_module


def _write_crash_log() -> Path:
    """即使配置加载失败，也尽量把完整堆栈写到 EXE 邻近的项目日志目录。"""

    explicit = os.environ.get("REM_HOME")
    if explicit:
        project_root = Path(explicit)
    elif getattr(sys, "frozen", False):
        project_root = Path(sys.executable).resolve().parent.parent
    else:
        project_root = next((parent for parent in Path(__file__).resolve().parents
                             if (parent / "config/rem_companion.toml").is_file()), Path.cwd())
    log_path = project_root / "logs" / "desktop-crash.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(traceback.format_exc(), encoding="utf-8")
    return log_path


if __name__ == "__main__":
    try:
        from remcompanion.desktop import main

        main()
    except Exception:
        path = _write_crash_log()
        native_module("windowing").show_startup_error(path)
        raise
