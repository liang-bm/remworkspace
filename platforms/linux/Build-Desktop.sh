#!/usr/bin/env bash
# Experimental Linux-only onedir package. Never reuse a Windows Python environment.
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
remchat_python="${REMCHAT_PYTHON:-$remchat_root/runtime/desktop-linux-xft/bin/python}"
if [[ "$(uname -s)" != Linux ]]; then
    printf '%s\n' 'This package must be built on Linux.' >&2
    exit 1
fi
if [[ ! -x "$remchat_python" ]]; then
    printf 'Linux Python is missing: %s. Run platforms/linux/scripts/Setup-LinuxDesktop.sh first.\n' "$remchat_python" >&2
    exit 1
fi
"$remchat_python" -c 'import sys, tkinter, PIL, PyInstaller; assert sys.version_info >= (3,11)'
REMCHAT_PYTHON="$remchat_python" bash "$remchat_root/platforms/linux/Build-Native.sh"
export PYINSTALLER_CONFIG_DIR="$remchat_root/cache/pyinstaller-linux"
export PYTHONUTF8=1
cd -- "$remchat_root"
printf '%s\n' 'Experimental packaging: validate the built application on its target Linux distribution before publishing.' >&2
exec "$remchat_python" -m PyInstaller --clean --noconfirm \
    --distpath "$remchat_root/dist/linux" --workpath "$remchat_root/build/linux" \
    "$remchat_root/platforms/linux/RemCompanion.spec" "$@"
