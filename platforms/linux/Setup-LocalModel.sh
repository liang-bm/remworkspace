#!/usr/bin/env bash
# Linux-only local chat model installation. No sudo, shell init, TTS or training.
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'This setup supports Linux x86_64 only.' >&2
    exit 1
fi
remchat_python="${REMCHAT_PYTHON:-$remchat_root/runtime/desktop-linux-xft/bin/python}"
if [[ ! -x "$remchat_python" ]]; then
    printf '%s\n' 'Run platforms/linux/scripts/Setup-LinuxDesktop.sh first.' >&2
    exit 1
fi
export REM_HOME="$remchat_root"
export PYTHONPATH="$remchat_root/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$remchat_python" -m remcompanion.platforms.linux.llama_install "$@"
