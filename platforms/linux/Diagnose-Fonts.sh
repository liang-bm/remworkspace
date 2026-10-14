#!/usr/bin/env bash
# Explicit Linux font inspection. No network, models, sudo or Windows runtime.
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$remchat_root/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1
if [[ -n "${REMCHAT_PYTHON:-}" ]]; then
    remchat_python="$REMCHAT_PYTHON"
elif [[ -x "$remchat_root/runtime/desktop-linux-xft/bin/python" ]]; then
    remchat_python="$remchat_root/runtime/desktop-linux-xft/bin/python"
else
    remchat_python=python3
fi
exec "$remchat_python" -m remcompanion.platforms.linux.font_diagnostics --root "$remchat_root" "$@"
