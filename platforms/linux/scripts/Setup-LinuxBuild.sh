#!/usr/bin/env bash
# Project-local build tools only. No sudo, system Python changes or model downloads.
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
bash "$remchat_root/platforms/linux/scripts/Setup-LinuxDesktop.sh"
export PIP_CACHE_DIR="$remchat_root/cache/pip-linux"
export TMPDIR="$remchat_root/tmp/linux-setup"
remchat_python="$remchat_root/runtime/desktop-linux-xft/bin/python"
"$remchat_python" -m pip install --disable-pip-version-check \
    'PyInstaller==6.21.0' 'pyinstaller-hooks-contrib==2026.8' 'altgraph==0.17.5'
"$remchat_python" -c 'import PyInstaller,tkinter,PIL; print("Build environment ready:", PyInstaller.__version__, tkinter.TkVersion, PIL.__version__)'
printf '%s\n' 'Next: bash platforms/linux/Build-Desktop.sh'
