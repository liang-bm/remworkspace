#!/usr/bin/env bash
# Linux 源码入口；不安装包，不复用 Windows 虚拟环境。
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export REM_HOME="$remchat_root"
export PYTHONPATH="$remchat_root/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
if [[ -n "${REMCHAT_PYTHON:-}" ]]; then
    remchat_python="$REMCHAT_PYTHON"
elif [[ -x "$remchat_root/runtime/desktop-linux-xft/bin/python" ]]; then
    remchat_python="$remchat_root/runtime/desktop-linux-xft/bin/python"
else
    remchat_python=python3
fi
if [[ -z "${DISPLAY:-}" ]]; then
    printf '%s\n' 'RemChat needs an X11 display (including WSLg/XWayland). DISPLAY is not set.' >&2
    exit 1
fi
"$remchat_python" -c 'import sys; assert sys.version_info >= (3, 11), "RemChat requires Python 3.11+"; import tkinter, PIL'
if [[ ! -f "$remchat_root/build/linux/native/libremchat_pidfd.so" ]]; then
    printf '%s\n' 'If this Python lacks pidfd APIs, backend cleanup requires platforms/linux/Build-Native.sh. No compiler or backend will be installed automatically.' >&2
fi
printf '%s\n' 'For setup, controls and Linux desktop requirements, see README.md (README.en.md / README.ja.md). Model and voice runtimes are configured separately.' >&2
cd -- "$remchat_root"
exec "$remchat_python" -m remcompanion.desktop
